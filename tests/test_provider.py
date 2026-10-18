"""Tests for aegis.provider OllamaProvider implementation."""
from __future__ import annotations

import io
import json
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch
import unittest

from aegis.errors import ProviderError, StopRequested
from aegis.interfaces import ModelProvider
from aegis.provider.ollama import OllamaProvider


class _MockHTTPResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, *args: Any) -> bytes:
        return self._body

    def readline(self, *args: Any) -> bytes:
        return b""

    def getcode(self) -> int:
        return self.status

    def __enter__(self) -> _MockHTTPResponse:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class TestOllamaProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.mock_opener = MagicMock()
        self.provider = OllamaProvider(
            host="http://127.0.0.1:11434",
            default_model="qwen2.5-coder:1.5b",
            timeout=10.0,
            opener=self.mock_opener,
        )

    def test_implements_protocol(self) -> None:
        self.assertIsInstance(self.provider, ModelProvider)
        self.assertEqual(self.provider.name, "ollama")

    def test_generate_success(self) -> None:
        response_payload = {
            "model": "qwen2.5-coder:1.5b",
            "created_at": "2026-10-18T12:00:00Z",
            "response": "Hello, AEGIS!",
            "done": True,
        }
        self.mock_opener.open.return_value = _MockHTTPResponse(
            json.dumps(response_payload).encode("utf-8")
        )

        res = self.provider.generate("Say hello")
        self.assertEqual(res, "Hello, AEGIS!")
        self.assertIsNone(self.provider.last_error())

        # Verify request arguments
        self.assertEqual(self.mock_opener.open.call_count, 1)
        req = self.mock_opener.open.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/generate")
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen2.5-coder:1.5b")
        self.assertEqual(body["prompt"], "Say hello")
        self.assertFalse(body["stream"])

    def test_generate_with_options_and_overrides(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(
            json.dumps({"response": "Custom answer"}).encode("utf-8")
        )

        res = self.provider.generate(
            "Write code",
            system="Be precise",
            model="custom-model:7b",
            stop=["STOP", "END"],
        )
        self.assertEqual(res, "Custom answer")

        req = self.mock_opener.open.call_args[0][0]
        body = json.loads(req.data.decode("utf-8"))
        self.assertEqual(body["model"], "custom-model:7b")
        self.assertEqual(body["prompt"], "Write code")
        self.assertEqual(body["system"], "Be precise")
        self.assertEqual(body["options"]["stop"], ["STOP", "END"])

    def test_generate_http_error(self) -> None:
        http_err = urllib.error.HTTPError(
            url="http://127.0.0.1:11434/api/generate",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=io.BytesIO(b"server crashed"),
        )
        self.addCleanup(http_err.close)
        self.mock_opener.open.side_effect = http_err
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("500", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    def test_generate_url_error(self) -> None:
        self.mock_opener.open.side_effect = urllib.error.URLError("Connection refused")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("Connection refused", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    def test_generate_malformed_json(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(b"invalid-json-string")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("Malformed JSON", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    def test_generate_empty_response_body(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(b"")
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("Empty response", str(ctx.exception))
        self.assertIsNotNone(self.provider.last_error())

    def test_generate_empty_response_text(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(
            json.dumps({"response": ""}).encode("utf-8")
        )
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("empty response", str(ctx.exception).lower())
        self.assertIsNotNone(self.provider.last_error())

    def test_generate_missing_response_field(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(
            json.dumps({"done": True}).encode("utf-8")
        )
        with self.assertRaises(ProviderError) as ctx:
            self.provider.generate("hi")
        self.assertIn("missing 'response' field", str(ctx.exception))

    def test_list_models_success(self) -> None:
        tags_payload = {
            "models": [
                {"name": "qwen2.5-coder:1.5b", "size": 12345},
                {"name": "llama3:latest", "size": 67890},
            ]
        }
        self.mock_opener.open.return_value = _MockHTTPResponse(
            json.dumps(tags_payload).encode("utf-8")
        )

        models = self.provider.list_models()
        self.assertEqual(models, ["qwen2.5-coder:1.5b", "llama3:latest"])
        self.assertIsNone(self.provider.last_error())

        req = self.mock_opener.open.call_args[0][0]
        self.assertEqual(req.get_method(), "GET")
        self.assertEqual(req.full_url, "http://127.0.0.1:11434/api/tags")

    def test_list_models_failure(self) -> None:
        self.mock_opener.open.side_effect = urllib.error.URLError("Network unreachable")
        with self.assertRaises(ProviderError):
            self.provider.list_models()
        self.assertIsNotNone(self.provider.last_error())

    def test_health_success(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(
            json.dumps({"models": []}).encode("utf-8")
        )
        self.assertTrue(self.provider.health())
        self.assertIsNone(self.provider.last_error())

    def test_health_connection_error(self) -> None:
        self.mock_opener.open.side_effect = urllib.error.URLError("Connection refused")
        self.assertFalse(self.provider.health())
        self.assertIsNotNone(self.provider.last_error())

    def test_health_non_200(self) -> None:
        self.mock_opener.open.return_value = _MockHTTPResponse(b"{}", status=503)
        self.assertFalse(self.provider.health())

    def test_non_loopback_host_rejected_by_default(self) -> None:
        """Verify non-loopback Ollama host is rejected when network is disabled (M2)."""
        with self.assertRaises(ProviderError) as ctx:
            OllamaProvider(host="http://remote-ollama.example.com:11434", default_model="m", allow_network=False)
        self.assertIn("rejected: network access is disabled", str(ctx.exception))

    def test_non_loopback_host_permitted_when_allow_network(self) -> None:
        """Verify non-loopback host is permitted when allow_network=True."""
        p = OllamaProvider(host="http://remote-ollama.example.com:11434", default_model="m", allow_network=True)
        self.assertEqual(p.host, "http://remote-ollama.example.com:11434")

    def test_invalid_scheme_rejected(self) -> None:
        """Verify non-http/https host is rejected with ProviderError."""
        with self.assertRaises(ProviderError) as ctx:
            OllamaProvider(host="file://localhost/etc/hosts", default_model="m")
        self.assertIn("scheme must be http or https", str(ctx.exception))

        with self.assertRaises(ProviderError) as ctx:
            OllamaProvider(host="ftp://127.0.0.1:11434", default_model="m")
        self.assertIn("scheme must be http or https", str(ctx.exception))

    def test_provider_blocks_redirect(self) -> None:
        """Verify provider HTTP request blocks 3xx redirects using production redirect handler (M2)."""
        import http.server
        import socketserver
        import threading

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:11434/api/generate2")
                self.end_headers()

            def log_message(self, format, *args):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), RedirectHandler) as httpd:
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.handle_request)
            t.daemon = True
            t.start()

            # No injected opener -> tests production _NoRedirectHandler
            p = OllamaProvider(host=f"http://127.0.0.1:{port}", default_model="m")
            with self.assertRaises(ProviderError) as ctx:
                p.generate("test")
            self.assertIn("redirect", str(ctx.exception).lower())
            t.join(timeout=2.0)

    def test_stop_during_stalled_stream(self) -> None:
        """Verify StopRequested interrupts within ~0.2s when the server stalls without sending a line (M1)."""
        import http.server
        import socketserver
        import threading
        import time

        class StallStreamHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.flush()
                # Stall indefinitely without sending body
                time.sleep(5)

            def log_message(self, format, *args):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), StallStreamHandler) as httpd:
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.handle_request)
            t.daemon = True
            t.start()

            stopped = False
            def stop_check():
                if stopped:
                    raise StopRequested("Interrupted stalled stream")

            def trigger_stop():
                time.sleep(0.2)
                nonlocal stopped
                stopped = True

            threading.Thread(target=trigger_stop, daemon=True).start()

            provider = OllamaProvider(host=f"http://127.0.0.1:{port}", default_model="m", timeout=10.0)
            t0 = time.monotonic()
            with self.assertRaises(StopRequested):
                provider.generate("test prompt", stop_check=stop_check)
            elapsed = time.monotonic() - t0
            self.assertLess(elapsed, 2.0, "Stop should interrupt stalled stream quickly, not wait for timeout")
            t.join(timeout=1.0)

    def test_stop_during_stalled_headers(self) -> None:
        """Verify StopRequested interrupts within ~0.2s when the server stalls on response headers (safety blocker)."""
        import http.server
        import socketserver
        import threading
        import time

        class StallHeadersHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                # Accept connection and POST, but stall without sending any response headers
                time.sleep(5)

            def log_message(self, format, *args):
                pass

        with socketserver.TCPServer(("127.0.0.1", 0), StallHeadersHandler) as httpd:
            port = httpd.server_address[1]
            t = threading.Thread(target=httpd.handle_request)
            t.daemon = True
            t.start()

            stopped = False

            def stop_check():
                if stopped:
                    raise StopRequested("Interrupted stalled headers wait")

            def trigger_stop():
                time.sleep(0.2)
                nonlocal stopped
                stopped = True

            threading.Thread(target=trigger_stop, daemon=True).start()

            # Set timeout to 120s (default timeout); stop must interrupt well before that (assert elapsed < 2.0s)
            provider = OllamaProvider(host=f"http://127.0.0.1:{port}", default_model="m", timeout=120.0)
            t0 = time.monotonic()
            with self.assertRaises(StopRequested):
                provider.generate("test prompt", stop_check=stop_check)
            elapsed = time.monotonic() - t0
            self.assertLess(
                elapsed,
                2.0,
                f"Stop should interrupt header stall quickly (took {elapsed:.2f}s, expected < 2.0s)",
            )
            t.join(timeout=1.0)


if __name__ == "__main__":
    unittest.main()
