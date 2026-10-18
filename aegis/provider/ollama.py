"""Ollama model provider implementation for Project AEGIS."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Sequence

from aegis.errors import ProviderError


class OllamaProvider:
    """Model provider communicating with a local Ollama instance via HTTP."""

    name: str = "ollama"

    def __init__(self, host: str, default_model: str, timeout: float = 120.0) -> None:
        self.host = host.rstrip("/")
        self.default_model = default_model
        self.timeout = float(timeout)
        self._last_error: str | None = None

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
    ) -> str:
        """Generate completion text for a prompt using Ollama."""
        target_model = model or self.default_model
        payload: dict[str, Any] = {
            "model": target_model,
            "prompt": prompt,
            "system": system,
            "stream": False,
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
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
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
