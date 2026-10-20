"""Unit tests for streaming provider and CLI streaming behavior."""
from __future__ import annotations

import http.server
import io
import json
import socketserver
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from typing import Callable, Iterator, Sequence

from aegis.cli.main import main
from aegis.config import AegisConfig
from aegis.errors import ProviderError, StopRequested
from aegis.provider.ollama import OllamaProvider


class _StreamingHandler(http.server.BaseHTTPRequestHandler):
    scenario = "normal"

    def do_POST(self) -> None:
        if self.path != "/api/generate":
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        if self.scenario == "normal":
            chunks = ["Hello", ", ", "Project ", "AEGIS", "!"]
            for c in chunks:
                line = json.dumps({"response": c, "done": False}) + "\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            done_line = json.dumps({"response": "", "done": True}) + "\n"
            self.wfile.write(done_line.encode("utf-8"))
            self.wfile.flush()

        elif self.scenario == "malformed_json":
            self.wfile.write(b"not-json-data\n")
            self.wfile.flush()

        elif self.scenario == "error_payload":
            err_line = json.dumps({"error": "model 'not-found' not found"}) + "\n"
            self.wfile.write(err_line.encode("utf-8"))
            self.wfile.flush()

        elif self.scenario == "stalled_after_chunk":
            line = json.dumps({"response": "chunk1", "done": False}) + "\n"
            self.wfile.write(line.encode("utf-8"))
            self.wfile.flush()
            time.sleep(2.0)

    def log_message(self, format: str, *args: object) -> None:
        pass


class TestStreamingProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.server = socketserver.TCPServer(("127.0.0.1", 0), _StreamingHandler)
        self.port = self.server.server_address[1]
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.provider = OllamaProvider(
            host=f"http://127.0.0.1:{self.port}",
            default_model="qwen2.5-coder:1.5b",
            timeout=5.0,
        )

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_stream_order_and_done(self) -> None:
        _StreamingHandler.scenario = "normal"
        chunks = list(self.provider.generate_stream("test prompt"))
        self.assertEqual(chunks, ["Hello", ", ", "Project ", "AEGIS", "!"])

    def test_stream_malformed_json_raises_provider_error(self) -> None:
        _StreamingHandler.scenario = "malformed_json"
        with self.assertRaises(ProviderError) as ctx:
            list(self.provider.generate_stream("test prompt"))
        self.assertIn("Malformed JSON", str(ctx.exception))

    def test_stream_error_payload_raises_provider_error(self) -> None:
        _StreamingHandler.scenario = "error_payload"
        with self.assertRaises(ProviderError) as ctx:
            list(self.provider.generate_stream("test prompt"))
        self.assertIn("model 'not-found' not found", str(ctx.exception))

    def test_stream_stop_mid_stream(self) -> None:
        _StreamingHandler.scenario = "stalled_after_chunk"
        stopped = False

        def stop_check() -> None:
            if stopped:
                raise StopRequested("Interrupted mid-stream")

        gen = self.provider.generate_stream("test prompt", stop_check=stop_check)
        first = next(gen)
        self.assertEqual(first, "chunk1")

        stopped = True
        with self.assertRaises(StopRequested):
            next(gen)

    def test_no_artificial_chunk_delays(self) -> None:
        _StreamingHandler.scenario = "normal"
        t0 = time.monotonic()
        chunks = list(self.provider.generate_stream("test prompt"))
        elapsed = time.monotonic() - t0
        self.assertEqual(len(chunks), 5)
        # All 5 chunks from loopback server should arrive within 0.15s (no fake sleep)
        self.assertLess(elapsed, 0.15, f"Chunks took {elapsed:.3f}s; artificial delay detected")


class FakeStreamingProvider:
    name: str = "fake-streaming"

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.calls: list[dict] = []

    def generate_stream(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        stop: Sequence[str] | None = None,
        stop_check: Callable[[], None] | None = None,
    ) -> Iterator[str]:
        self.calls.append({"prompt": prompt, "system": system, "model": model})
        for c in self.chunks:
            if stop_check is not None:
                stop_check()
            yield c

    def health(self) -> bool:
        return True

    def list_models(self) -> list[str]:
        return ["fake-model:latest"]


class TestCLIStreaming(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config = AegisConfig(
            model="test-model:1b",
            ollama_host="http://127.0.0.1:11434",
            memory_db=f"{self.temp_dir.name}/memory.db",
            audit_log=f"{self.temp_dir.name}/audit.jsonl",
            stop_file=f"{self.temp_dir.name}/STOP",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_single_shot_streaming(self) -> None:
        fake = FakeStreamingProvider(["Streaming ", "chunk ", "by ", "chunk.\n"])
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "Stream this", "--plain"],
                provider=fake,
                config=self.config,
            )
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("Streaming chunk by chunk.\n", output)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0]["prompt"], "Stream this")

    def test_cli_streaming_no_fake_delays(self) -> None:
        chunks = [f"token_{i} " for i in range(20)]
        fake = FakeStreamingProvider(chunks)
        out = io.StringIO()
        t0 = time.monotonic()
        with redirect_stdout(out):
            rc = main(
                ["chat", "--prompt", "speed test", "--plain"],
                provider=fake,
                config=self.config,
            )
        elapsed = time.monotonic() - t0
        self.assertEqual(rc, 0)
        self.assertLess(elapsed, 0.1, f"CLI streaming took {elapsed:.3f}s; artificial delay detected")


if __name__ == "__main__":
    unittest.main()
