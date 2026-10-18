"""Tests for AegisConfig loading, parsing, and directory management."""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from aegis.config import AegisConfig, ensure_dirs
from aegis.errors import ConfigError


class TestAegisConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        # Clear any AEGIS_* env vars for test isolation
        self.clean_env = {
            k: v for k, v in os.environ.items() if not k.startswith("AEGIS_")
        }

    def test_default_values(self) -> None:
        with mock.patch.dict(os.environ, self.clean_env, clear=True):
            config = AegisConfig.load()
            self.assertEqual(config.model, "qwen2.5-coder:1.5b")
            self.assertEqual(config.ollama_host, "http://127.0.0.1:11434")
            self.assertEqual(config.request_timeout, 120.0)
            self.assertFalse(config.allow_network)
            self.assertFalse(config.allow_consequential)
            self.assertEqual(config.max_memory_results, 50)
            self.assertEqual(config.max_chat_turns, 20)
            self.assertTrue(config.memory_db.endswith("memory.db"))
            self.assertTrue(config.audit_log.endswith("audit.jsonl"))
            self.assertTrue(config.stop_file.endswith("STOP"))

    def test_data_dir_default_and_override(self) -> None:
        with mock.patch.dict(os.environ, self.clean_env, clear=True):
            default_dir = AegisConfig.data_dir()
            expected = os.path.abspath(os.path.expanduser("~/.local/share/aegis"))
            self.assertEqual(default_dir, expected)

        custom_dir = os.path.join(self.temp_dir.name, "custom_data")
        env_with_data_dir = dict(self.clean_env)
        env_with_data_dir["AEGIS_DATA_DIR"] = custom_dir
        with mock.patch.dict(os.environ, env_with_data_dir, clear=True):
            self.assertEqual(AegisConfig.data_dir(), os.path.abspath(custom_dir))

    def test_load_toml_top_level_and_table(self) -> None:
        toml_content = """
model = "deepseek-coder:6.7b"
ollama_host = "http://127.0.0.1:11435"
request_timeout = 60.0
allow_network = true
allow_consequential = true
max_memory_results = 25
max_chat_turns = 10
"""
        cfg_file = os.path.join(self.temp_dir.name, "test_config.toml")
        with open(cfg_file, "w", encoding="utf-8") as f:
            f.write(toml_content)

        with mock.patch.dict(os.environ, self.clean_env, clear=True):
            config = AegisConfig.load(cfg_file)
            self.assertEqual(config.model, "deepseek-coder:6.7b")
            self.assertEqual(config.ollama_host, "http://127.0.0.1:11435")
            self.assertEqual(config.request_timeout, 60.0)
            self.assertTrue(config.allow_network)
            self.assertTrue(config.allow_consequential)
            self.assertEqual(config.max_memory_results, 25)
            self.assertEqual(config.max_chat_turns, 10)

        # Nested under [aegis] table
        table_toml = """
[aegis]
model = "llama3:8b"
max_chat_turns = 15
"""
        table_file = os.path.join(self.temp_dir.name, "table_config.toml")
        with open(table_file, "w", encoding="utf-8") as f:
            f.write(table_toml)

        with mock.patch.dict(os.environ, self.clean_env, clear=True):
            cfg_table = AegisConfig.load(table_file)
            self.assertEqual(cfg_table.model, "llama3:8b")
            self.assertEqual(cfg_table.max_chat_turns, 15)

    def test_explicit_path_missing_raises_config_error(self) -> None:
        nonexistent = os.path.join(self.temp_dir.name, "missing.toml")
        with mock.patch.dict(os.environ, self.clean_env, clear=True):
            with self.assertRaises(ConfigError):
                AegisConfig.load(nonexistent)

    def test_aegis_config_env_var_missing_raises_config_error(self) -> None:
        env_with_cfg = dict(self.clean_env)
        env_with_cfg["AEGIS_CONFIG"] = os.path.join(self.temp_dir.name, "missing_env.toml")
        with mock.patch.dict(os.environ, env_with_cfg, clear=True):
            with self.assertRaises(ConfigError):
                AegisConfig.load()

    def test_invalid_toml_syntax_raises_config_error(self) -> None:
        bad_toml = "invalid [ toml syntax"
        cfg_file = os.path.join(self.temp_dir.name, "bad.toml")
        with open(cfg_file, "w", encoding="utf-8") as f:
            f.write(bad_toml)

        with mock.patch.dict(os.environ, self.clean_env, clear=True):
            with self.assertRaises(ConfigError):
                AegisConfig.load(cfg_file)

    def test_environment_variable_overrides(self) -> None:
        override_env = dict(self.clean_env)
        override_env["AEGIS_MODEL"] = "mistral:7b"
        override_env["AEGIS_OLLAMA_HOST"] = "http://127.0.0.1:9999"
        override_env["AEGIS_MEMORY_DB"] = "/tmp/test_mem.db"
        override_env["AEGIS_AUDIT_LOG"] = "/tmp/test_audit.jsonl"
        override_env["AEGIS_STOP_FILE"] = "/tmp/test_STOP"
        override_env["AEGIS_ALLOW_NETWORK"] = "true"
        override_env["AEGIS_ALLOW_CONSEQUENTIAL"] = "yes"
        override_env["AEGIS_REQUEST_TIMEOUT"] = "45.5"
        override_env["AEGIS_MAX_MEMORY_RESULTS"] = "100"
        override_env["AEGIS_MAX_CHAT_TURNS"] = "40"

        with mock.patch.dict(os.environ, override_env, clear=True):
            config = AegisConfig.load()
            self.assertEqual(config.model, "mistral:7b")
            self.assertEqual(config.ollama_host, "http://127.0.0.1:9999")
            self.assertEqual(config.memory_db, "/tmp/test_mem.db")
            self.assertEqual(config.audit_log, "/tmp/test_audit.jsonl")
            self.assertEqual(config.stop_file, "/tmp/test_STOP")
            self.assertTrue(config.allow_network)
            self.assertTrue(config.allow_consequential)
            self.assertEqual(config.request_timeout, 45.5)
            self.assertEqual(config.max_memory_results, 100)
            self.assertEqual(config.max_chat_turns, 40)

    def test_invalid_numeric_env_var_raises_config_error(self) -> None:
        bad_env = dict(self.clean_env)
        bad_env["AEGIS_REQUEST_TIMEOUT"] = "not-a-number"
        with mock.patch.dict(os.environ, bad_env, clear=True):
            with self.assertRaises(ConfigError):
                AegisConfig.load()

    def test_ensure_dirs(self) -> None:
        base = os.path.join(self.temp_dir.name, "sub_data")
        db = os.path.join(base, "db_dir", "mem.db")
        audit = os.path.join(base, "audit_dir", "audit.jsonl")
        stop = os.path.join(base, "stop_dir", "STOP")

        cfg = AegisConfig(memory_db=db, audit_log=audit, stop_file=stop)
        ensure_dirs(cfg)

        self.assertTrue(os.path.isdir(os.path.dirname(db)))
        self.assertTrue(os.path.isdir(os.path.dirname(audit)))
        self.assertTrue(os.path.isdir(os.path.dirname(stop)))


if __name__ == "__main__":
    unittest.main()
