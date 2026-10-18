"""Unit tests for aegis.provider.ollama."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from aegis.errors import ProviderError
from aegis.interfaces import ModelProvider
from aegis.provider.ollama import OllamaProvider


class _MockHTTPResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _MockHTTPResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class TestOllamaProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = OllamaProvider(
            host="http://127.0.0.1:11434",
            default_model="qwen2.5-coder:1.5b",
            timeout=10.0,
        )

    def test_implements_protocol(self) -> None:
        self.assertIsInstance(self.provider, ModelProvider)
        self.assertEqual(self.provider.name, "ollama")

    @patch("urllib.request.urlopen")
    def test_generate_success(self, mock_urlopen: MagicMock) -> None:
        response_payload = {
            "model": "qwen2.5-coder:1.5b",
            "created_at": "2026-10-18T12:00:00Z",
            "response": "Hello, AEGIS!",
            "done": True,
        }
        mock_urlopen.return_value = _MockHTTPResponse(
            json.dumps(response_payload).encode("utf-8")
        )

        res = self.provider.generate("Say hello")
        self.assertEqual(res, "Hello, AEGIS!")
        self.assertIsNone(self.provider.last_error())

        # Verify request arguments
        self.assertEqual(mock_urlopen.call_count, 1)
        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/generate")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen2.5-coder:1.5b")
        self.assertEqual(body["prompt"], "Say hello")
        self.assertFalse(body["stream"])

    @patch("urllib.request.urlopen")
    def test_generate_with_options_and_overrides(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(
            json.dumps({"response": "Custom answer"}).encode("utf-8")
        )

        res = self.provider.generate(
            "Write code",
            system="Be precise",
            model="custom-model:7b",
            stop=["STOP", "END"],
        )
        self.assertEqual(res, "Custom answer")

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "custom-model:7b")
        self.assertEqual(body["prompt"], "Write code")
        self.assertEqual(body["system"], "Be precise")
        self.assertEqual(body["options"]["stop"], ["STOP", "END"])

    @patch("urllib.request.urlopen")
    def test_generate_http_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.HTTPError(
            url="http://127.0.0.1:11434/api/generate",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=io.BytesIO(b"server crashed"),
        )
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("500", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_generate_url_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("Connection refused", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_generate_malformed_json(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(b"invalid-json-string")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("Malformed JSON", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_generate_empty_response_body(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(b"")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("Empty response", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_generate_empty_response_text(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(
            json.dumps({"response": ""}).encode("utf-8")
        )
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("empty response", str(ctx.exception).lower())
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_generate_missing_response_field(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(
            json.dumps({"done": True}).encode("utf-8")
        )
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("missing 'response' field", str(ctx.exception))

    @patch("urllib.request.urlopen")
    def test_list_models_success(self, mock_urlopen: MagicMock) -> None:
        tags_payload = {
            "models": [
                {"name": "qwen2.5-coder:1.5b", "size": 12345},
                {"name": "llama3:latest", "size": 67890},
            ]
        }
        mock_urlopen.return_value = _MockHTTPResponse(
            json.dumps(tags_payload).encode("utf-8")
        )

        models = self.provider.list_models()
        self.assertEqual(models, ["qwen2.5-coder:1.5b", "llama3:latest"])
        self.assertIsNone(self.provider.last_error())

        req = mock_urlopen.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/tags")

    @patch("urllib.request.urlopen")
    def test_list_models_failure(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("Network unreachable")
        with self.assertRaises(ProviderError):
            self.provider.list_models()
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_health_success(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(
            json.dumps({"models": []}).encode("utf-8")
        )
        self.assertTrue(self.provider.health())
        self.assertIsNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_health_connection_error(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        self.assertFalse(self.provider.health())
        self.assertIsNotNone(self.provider.last_error())

    @patch("urllib.request.urlopen")
    def test_health_non_200(self, mock_urlopen: MagicMock) -> None:
        mock_urlopen.return_value = _MockHTTPResponse(b"{}", status=503)
        self.assertFalse(self.provider.health())


if __name__ == "__main__":
    unittest.main()
