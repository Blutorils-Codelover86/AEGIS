"""Configuration loading and validation for Project AEGIS."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from typing import Any

from aegis.errors import ConfigError


def _to_bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "on")
    return bool(val)


@dataclass(frozen=True)
class AegisConfig:
    model: str = "qwen2.5-coder:1.5b"
    ollama_host: str = "http://127.0.0.1:11434"
    memory_db: str = ""        # default: <data_dir>/memory.db
    audit_log: str = ""        # default: <data_dir>/audit.jsonl
    stop_file: str = ""        # default: <data_dir>/STOP
    request_timeout: float = 120.0
    allow_network: bool = False        # non-loopback network (denied by default)
    allow_consequential: bool = False  # auto-allow consequential actions (default: confirm)
    max_memory_results: int = 50
    max_chat_turns: int = 20

    @staticmethod
    def data_dir() -> str:
        env_dir = os.environ.get("AEGIS_DATA_DIR")
        if env_dir:
            return os.path.abspath(os.path.expanduser(env_dir))
        return os.path.abspath(os.path.expanduser("~/.local/share/aegis"))

    @classmethod
    def load(cls, path: str | None = None) -> AegisConfig:
        data_directory = cls.data_dir()
        target_path: str | None = None
        explicit = False

        if path is not None:
            target_path = os.path.abspath(os.path.expanduser(path))
            explicit = True
        elif os.environ.get("AEGIS_CONFIG"):
            target_path = os.path.abspath(os.path.expanduser(os.environ["AEGIS_CONFIG"]))
            explicit = True
        else:
            default_candidate = os.path.join(data_directory, "aegis.toml")
            if os.path.exists(default_candidate):
                target_path = default_candidate

        parsed: dict[str, Any] = {}
        if target_path is not None:
            if not os.path.isfile(target_path):
                raise ConfigError(f"Config file not found or not a regular file: {target_path}")
            try:
                with open(target_path, "rb") as f:
                    parsed = tomllib.load(f)
            except Exception as e:
                raise ConfigError(f"Failed to read or parse TOML config at '{target_path}': {e}") from e

        cfg: dict[str, Any] = dict(parsed)
        if isinstance(parsed.get("aegis"), dict):
            cfg.update(parsed["aegis"])

        # Base fields with defaults
        model = str(cfg.get("model", "qwen2.5-coder:1.5b"))
        ollama_host = str(cfg.get("ollama_host", "http://127.0.0.1:11434"))
        memory_db = str(cfg.get("memory_db") or os.path.join(data_directory, "memory.db"))
        audit_log = str(cfg.get("audit_log") or os.path.join(data_directory, "audit.jsonl"))
        stop_file = str(cfg.get("stop_file") or os.path.join(data_directory, "STOP"))

        try:
            request_timeout = float(cfg.get("request_timeout", 120.0))
        except (ValueError, TypeError) as e:
            raise ConfigError(f"Invalid request_timeout value in config: {e}") from e

        allow_network = _to_bool(cfg.get("allow_network", False))
        allow_consequential = _to_bool(cfg.get("allow_consequential", False))

        try:
            max_memory_results = int(cfg.get("max_memory_results", 50))
        except (ValueError, TypeError) as e:
            raise ConfigError(f"Invalid max_memory_results value in config: {e}") from e

        try:
            max_chat_turns = int(cfg.get("max_chat_turns", 20))
        except (ValueError, TypeError) as e:
            raise ConfigError(f"Invalid max_chat_turns value in config: {e}") from e

        # Environment variable overrides
        if "AEGIS_MODEL" in os.environ:
            model = os.environ["AEGIS_MODEL"]
        if "AEGIS_OLLAMA_HOST" in os.environ:
            ollama_host = os.environ["AEGIS_OLLAMA_HOST"]
        if "AEGIS_MEMORY_DB" in os.environ:
            memory_db = os.environ["AEGIS_MEMORY_DB"]
        if "AEGIS_AUDIT_LOG" in os.environ:
            audit_log = os.environ["AEGIS_AUDIT_LOG"]
        if "AEGIS_STOP_FILE" in os.environ:
            stop_file = os.environ["AEGIS_STOP_FILE"]
        if "AEGIS_ALLOW_NETWORK" in os.environ:
            allow_network = _to_bool(os.environ["AEGIS_ALLOW_NETWORK"])
        if "AEGIS_ALLOW_CONSEQUENTIAL" in os.environ:
            allow_consequential = _to_bool(os.environ["AEGIS_ALLOW_CONSEQUENTIAL"])
        if "AEGIS_REQUEST_TIMEOUT" in os.environ:
            try:
                request_timeout = float(os.environ["AEGIS_REQUEST_TIMEOUT"])
            except ValueError as e:
                raise ConfigError(f"Invalid AEGIS_REQUEST_TIMEOUT: {e}") from e
        if "AEGIS_MAX_MEMORY_RESULTS" in os.environ:
            try:
                max_memory_results = int(os.environ["AEGIS_MAX_MEMORY_RESULTS"])
            except ValueError as e:
                raise ConfigError(f"Invalid AEGIS_MAX_MEMORY_RESULTS: {e}") from e
        if "AEGIS_MAX_CHAT_TURNS" in os.environ:
            try:
                max_chat_turns = int(os.environ["AEGIS_MAX_CHAT_TURNS"])
            except ValueError as e:
                raise ConfigError(f"Invalid AEGIS_MAX_CHAT_TURNS: {e}") from e

        return cls(
            model=model,
            ollama_host=ollama_host,
            memory_db=memory_db,
            audit_log=audit_log,
            stop_file=stop_file,
            request_timeout=request_timeout,
            allow_network=allow_network,
            allow_consequential=allow_consequential,
            max_memory_results=max_memory_results,
            max_chat_turns=max_chat_turns,
        )


def ensure_dirs(config: AegisConfig) -> None:
    """Create parent directories for db, audit, and stop paths."""
    d = AegisConfig.data_dir()
    os.makedirs(d, exist_ok=True)
    targets = [
        config.memory_db or os.path.join(d, "memory.db"),
        config.audit_log or os.path.join(d, "audit.jsonl"),
        config.stop_file or os.path.join(d, "STOP"),
    ]
    for p in targets:
        parent = os.path.dirname(os.path.abspath(p))
        if parent:
            os.makedirs(parent, exist_ok=True)
