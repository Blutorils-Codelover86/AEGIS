"""Ollama model provider implementation for Project AEGIS."""
from __future__ import annotations

import io
import json
import select
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Sequence

from aegis.errors import ProviderError, StopRequested


def _is_loopback_host(target: str | None) -> bool:
    """Check deterministically whether target resolves to a loopback address under http/https."""
    if not target or not isinstance(target, str):
        return False
    raw = target.strip()
    if not raw:
        return False
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    if "://" not in raw and not raw.startswith("//"):
        cleaned = raw.split("/")[0].split(":")[0].removeprefix("[").removesuffix("]").strip().lower()
        return cleaned in loopback_hosts

    try:
        parsed = urllib.parse.urlsplit(raw)
    except Exception:
        return False

    if parsed.scheme.lower() not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    return hostname.strip().lower() in loopback_hosts


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Custom redirect handler that blocks all HTTP redirects."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

    def http_error_301(self, req, fp, code, msg, headers):
        raise ProviderError(f"HTTP redirect ({code}) blocked by provider policy")

    def http_error_302(self, req, fp, code, msg, headers):
        raise ProviderError(f"HTTP redirect ({code}) blocked by provider policy")

    def http_error_303(self, req, fp, code, msg, headers):
        raise ProviderError(f"HTTP redirect ({code}) blocked by provider policy")

    def http_error_307(self, req, fp, code, msg, headers):
        raise ProviderError(f"HTTP redirect ({code}) blocked by provider policy")

    def http_error_308(self, req, fp, code, msg, headers):
        raise ProviderError(f"HTTP redirect ({code}) blocked by provider policy")


class OllamaProvider:
    """Model provider communicating with a local Ollama instance via HTTP."""

    name: str = "ollama"

    def __init__(
        self,
        host: str,
        default_model: str,
        timeout: float = 120.0,
        allow_network: bool = False,
        *,
        opener: Any = None,
    ) -> None:
        raw_host = host.rstrip("/")
        try:
            parsed = urllib.parse.urlsplit(raw_host)
        except Exception as e:
            raise ProviderError(f"Malformed Ollama host '{host}': {e}") from e

        if parsed.scheme.lower() not in ("http", "https"):
            raise ProviderError(
                f"Invalid Ollama host '{host}': scheme must be http or https"
            )
        if not parsed.hostname:
            raise ProviderError(
                f"Invalid Ollama host '{host}': concrete hostname is required"
            )

        if not _is_loopback_host(raw_host) and not allow_network:
            raise ProviderError(
                f"Non-loopback Ollama host '{host}' rejected: network access is disabled"
            )

        self.host = raw_host
        self.default_model = default_model
        self.timeout = float(timeout)
        self.allow_network = allow_network
        self._last_error: str | None = None
        self._opener = opener if opener is not None else urllib.request.build_opener(_NoRedirectHandler())

    def _open(self, req: urllib.request.Request, timeout: float):
        return self._opener.open(req, timeout=timeout)

    def _cancellable_open(
        self,
        req: urllib.request.Request,
        *,
        timeout: float,
        stop_check: Callable[[], None] | None = None,
    ) -> Any:
        """Open an HTTP request with active stop-check cancellation during the header-wait phase."""
        if stop_check is None:
            return self._open(req, timeout=timeout)

        stop_check()
        open_result: list[Any] = []
        open_exc: list[BaseException] = []
        cancelled = threading.Event()

        def open_worker() -> None:
            try:
                resp = self._open(req, timeout=timeout)
                if cancelled.is_set():
                    try:
                        resp.close()
                    except Exception:
                        pass
                else:
                    open_result.append(resp)
            except BaseException as e:
                if not cancelled.is_set():
                    open_exc.append(e)

        t = threading.Thread(target=open_worker, daemon=True)
        t.start()

        deadline = time.monotonic() + timeout
        try:
            while t.is_alive():
                stop_check()
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Ollama request timed out after {timeout}s waiting for response headers")
                t.join(timeout=0.1)

            if stop_check is not None:
                stop_check()

            if open_exc:
                raise open_exc[0]

            if not open_result:
                raise TimeoutError(f"Ollama request timed out after {timeout}s waiting for response headers")

            return open_result[0]
        except BaseException:
            cancelled.set()
            if open_result:
                try:
                    open_result[0].close()
                except Exception:
                    pass
            raise

    def last_error(self) -> str | None:
        """Return the last recorded error message for diagnostics."""
        return self._last_error

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> str:
        """Generate completion text for a prompt using Ollama."""
        if stop_check is not None:
            stop_check()

        target_model = model or self.default_model
        use_stream = bool(stop_check)
        payload: dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
            "system": system,
            "stream": use_stream,
        }
        if stop is not None:
            payload["options"] = {"stop": list(stop)}

        url = f"{self.host}/api/generate"
        encoded_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=encoded_data,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )

        try:
            with self._cancellable_open(req, timeout=self.timeout, stop_check=stop_check) as resp:
                if stop_check is not None and (hasattr(resp, "readline") or hasattr(resp, "__iter__")):
                    chunks: list[str] = []
                    deadline = time.monotonic() + self.timeout

                    # Obtain fileno if available for non-blocking select polling
                    fn: int | None = None
                    if hasattr(resp, "fileno"):
                        try:
                            fn = resp.fileno()
                        except Exception:
                            fn = None

                    while True:
                        stop_check()
                        if time.monotonic() > deadline:
                            raise TimeoutError(f"Ollama stream timed out after {self.timeout}s")

                        if fn is not None and fn >= 0:
                            try:
                                r, _, _ = select.select([fn], [], [], 0.1)
                                if not r:
                                    # Connection stalled or waiting; continue polling stop_check
                                    continue
                            except (io.UnsupportedOperation, OSError):
                                fn = None

                        line = resp.readline()
                        if not line:
                            break

                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            data = json.loads(stripped.decode("utf-8"))
                            if isinstance(data, dict):
                                if "response" in data and data["response"] is not None:
                                    chunks.append(str(data["response"]))
                                if data.get("done", False):
                                    break
                        except Exception:
                            pass

                    result = "".join(chunks)
                    if not result:
                        msg = "Ollama returned an empty response"
                        self._last_error = msg
                        raise ProviderError(msg)
                    self._last_error = None
                    return result
                else:
                    if stop_check is not None:
                        stop_check()
                    body = resp.read()
                    if stop_check is not None:
                        stop_check()
        except StopRequested:
            raise
        except urllib.error.HTTPError as e:
            try:
                e.close()
            except Exception:
                pass
            msg = f"Ollama HTTP error {e.code}: {e.reason}"
            self._last_error = msg
            raise ProviderError(msg) from e
        except urllib.error.URLError as e:
            msg = f"Ollama connection error: {e.reason}"
            self._last_error = msg
            raise ProviderError(msg) from e
        except TimeoutError as e:
            msg = f"Ollama request timed out after {self.timeout}s"
            self._last_error = msg
            raise ProviderError(msg) from e
        except Exception as e:
            if isinstance(e, ProviderError):
                raise
            msg = f"Ollama request failed: {e}"
            self._last_error = msg
            raise ProviderError(msg) from e

        if not body or not body.strip():
            msg = "Empty response received from Ollama server"
            self._last_error = msg
            raise ProviderError(msg)

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            msg = f"Malformed JSON response from Ollama: {e}"
            self._last_error = msg
            raise ProviderError(msg) from e

        if not isinstance(data, dict):
            msg = "Malformed response from Ollama: expected JSON object"
            self._last_error = msg
            raise ProviderError(msg)

        if "response" not in data or data["response"] is None:
            msg = "Ollama response missing 'response' field"
            self._last_error = msg
            raise ProviderError(msg)

        result = str(data["response"])
        if not result:
            msg = "Ollama returned an empty response"
            self._last_error = msg
            raise ProviderError(msg)

        self._last_error = None
        return result

    def list_models(self) -> list[str]:
        """List locally available models from Ollama."""
        url = f"{self.host}/api/tags"
        req = urllib.request.Request(
            url=url,
            headers={"Accept": "application/json"},
            method="GET",
        )

        try:
            with self._open(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            try:
                e.close()
            except Exception:
                pass
            msg = f"Ollama HTTP error {e.code}: {e.reason}"
            self._last_error = msg
            raise ProviderError(msg) from e
        except urllib.error.URLError as e:
            msg = f"Ollama connection error: {e.reason}"
            self._last_error = msg
            raise ProviderError(msg) from e
        except TimeoutError as e:
            msg = f"Ollama request timed out after {self.timeout}s"
            self._last_error = msg
            raise ProviderError(msg) from e
        except Exception as e:
            if isinstance(e, ProviderError):
                raise
            msg = f"Ollama request failed: {e}"
            self._last_error = msg
            raise ProviderError(msg) from e

        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            msg = f"Malformed JSON response from Ollama: {e}"
            self._last_error = msg
            raise ProviderError(msg) from e

        if not isinstance(data, dict) or "models" not in data:
            msg = "Ollama /api/tags response missing 'models' list"
            self._last_error = msg
            raise ProviderError(msg)

        models_list = data["models"]
        if not isinstance(models_list, list):
            msg = "Ollama /api/tags 'models' field is not a list"
            self._last_error = msg
            raise ProviderError(msg)

        names: list[str] = []
        for item in models_list:
            if isinstance(item, dict) and "name" in item:
                names.append(str(item["name"]))
            elif isinstance(item, str):
                names.append(item)

        self._last_error = None
        return names

    def health(self) -> bool:
        """Check Ollama connectivity and health with a short timeout. Never raises."""
        url = f"{self.host}/api/tags"
        req = urllib.request.Request(
            url=url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        timeout = min(self.timeout, 5.0)

        try:
            with self._open(req, timeout=timeout) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if status != 200:
                    self._last_error = f"Ollama health check returned HTTP status {status}"
                    return False
                body = resp.read()
                data = json.loads(body.decode("utf-8"))
                if not isinstance(data, dict):
                    self._last_error = "Ollama health check response was not a JSON object"
                    return False
                self._last_error = None
                return True
        except Exception as e:
            self._last_error = str(e)
            return False
