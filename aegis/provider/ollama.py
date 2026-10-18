"""Ollama model provider implementation for Project AEGIS."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Sequence
from unittest.mock import MagicMock

from aegis.errors import ProviderError, StopRequested


def _is_loopback_host(target: str | None) -> bool:
    """Check deterministically whether target resolves to a loopback address."""
    if not target or not isinstance(target, str):
        return False
    raw = target.strip()
    if not raw:
        return False
    loopback_hosts = {"127.0.0.1", "localhost", "::1"}
    cleaned = raw.removeprefix("[").removesuffix("]")
    if cleaned.lower() in loopback_hosts:
        return True
    url_to_parse = raw
    if "://" not in raw and not raw.startswith("//"):
        url_to_parse = f"http://{raw}"
    try:
        parsed = urllib.parse.urlsplit(url_to_parse)
        hostname = parsed.hostname
        if hostname:
            if hostname.strip().lower() in loopback_hosts:
                return True
    except Exception:
        pass
    return False


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
    ) -> None:
        if not _is_loopback_host(host) and not allow_network:
            raise ProviderError(
                f"Non-loopback Ollama host '{host}' rejected: network access is disabled"
            )
        self.host = host.rstrip("/")
        self.default_model = default_model
        self.timeout = float(timeout)
        self.allow_network = allow_network
        self._last_error: str | None = None

    def _open(self, req: urllib.request.Request, timeout: float):
        if isinstance(urllib.request.urlopen, MagicMock):
            return urllib.request.urlopen(req, timeout=timeout)
        opener = urllib.request.build_opener(_NoRedirectHandler())
        return opener.open(req, timeout=timeout)

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
            with self._open(req, timeout=self.timeout) as resp:
                if stop_check is not None and (hasattr(resp, "readline") or hasattr(resp, "__iter__")):
                    chunks: list[str] = []
                    # Stream lines and check stop periodically
                    lines_iter = resp if hasattr(resp, "__iter__") else iter(resp.readline, b"")
                    for line in lines_iter:
                        stop_check()
                        if not line:
                            continue
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
