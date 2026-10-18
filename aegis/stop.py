"""Stop mechanism for Project AEGIS."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from aegis.errors import StopRequested


class StopController:
    """Controls execution halting via a local stop file."""

    def __init__(self, stop_file: str) -> None:
        self.stop_file = os.path.abspath(os.path.expanduser(stop_file))

    def request_stop(self, reason: str = "user") -> None:
        """Signal an immediate stop by writing reason and timestamp to the stop file."""
        parent = os.path.dirname(self.stop_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        payload = {
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        temp_path = f"{self.stop_file}.tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(temp_path, self.stop_file)

    def is_stopped(self) -> bool:
        """Check whether a stop has been requested."""
        return os.path.isfile(self.stop_file)

    def clear(self) -> None:
        """Clear any existing stop signal."""
        try:
            os.remove(self.stop_file)
        except FileNotFoundError:
            pass

    def reason(self) -> str | None:
        """Return the reason recorded in the stop file, or None if not stopped."""
        if not self.is_stopped():
            return None
        try:
            with open(self.stop_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if not content:
                    return "user"
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        return str(data.get("reason", "user"))
                    return str(data)
                except json.JSONDecodeError:
                    return content
        except OSError:
            return "user"

    def check(self) -> None:
        """Raise StopRequested if a stop condition is active."""
        if self.is_stopped():
            r = self.reason() or "stop requested"
            raise StopRequested(r)
