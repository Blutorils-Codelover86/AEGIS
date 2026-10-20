"""Theme and visual identity for Project AEGIS."""
from __future__ import annotations

import os
from typing import Any

from rich.console import Console
from rich.style import Style
from rich.theme import Theme

ACCENT = "#3B82F6"
ACCENT_DIM = "#1D4ED8"
MUTED = "#6B7280"


def build_theme() -> Theme:
    """Build and return the AEGIS rich theme."""
    return Theme(
        {
            "aegis.accent": Style(color=ACCENT, bold=True),
            "aegis.user": Style(color="#60A5FA", bold=True),
            "aegis.assistant": Style(color=ACCENT, bold=True),
            "aegis.system": Style(color="#9CA3AF", italic=True),
            "aegis.muted": Style(color=MUTED),
            "aegis.success": Style(color="#10B981", bold=True),
            "aegis.warning": Style(color="#F59E0B", bold=True),
            "aegis.error": Style(color="#EF4444", bold=True),
            "aegis.code": Style(color="#38BDF8"),
            "aegis.table.header": Style(color=ACCENT, bold=True),
            "aegis.link": Style(color="#60A5FA", underline=True),
        }
    )


def make_console(
    *,
    plain: bool = False,
    force_terminal: bool | None = None,
    file: Any = None,
) -> Console:
    """Create a rich Console instance honoring plain and NO_COLOR options."""
    theme = build_theme()
    is_no_color = plain or ("NO_COLOR" in os.environ)

    if plain:
        return Console(
            theme=theme,
            no_color=True,
            force_terminal=False if force_terminal is None else force_terminal,
            color_system=None,
            highlight=False,
            soft_wrap=False,
            file=file,
        )

    return Console(
        theme=theme,
        no_color=is_no_color,
        force_terminal=force_terminal,
        highlight=False,
        soft_wrap=False,
        file=file,
    )


def header_info(config: Any, model: str | None = None) -> str:
    """Return compact header string: AEGIS · model · local."""
    effective_model = model or getattr(config, "model", "default")
    host = getattr(config, "ollama_host", "")
    locality = "local"
    if host and not any(loc in host for loc in ("127.0.0.1", "localhost", "::1")):
        locality = host
    return f"AEGIS · {effective_model} · {locality}"


def print_header(console: Console, config: Any, model: str | None = None) -> None:
    """Print the AEGIS session header."""
    info = header_info(config, model)
    if console.no_color:
        console.print(info)
    else:
        console.print(f"[aegis.accent]{info}[/aegis.accent]")


def print_user(console: Console, text: str) -> None:
    """Print user prompt text with styling."""
    if console.no_color:
        console.print(f"you > {text}")
    else:
        console.print(f"[aegis.user]you >[/aegis.user] {text}")


def print_assistant_prefix(console: Console) -> None:
    """Print the assistant prefix label."""
    if console.no_color:
        console.print("aegis >")
    else:
        console.print("[aegis.assistant]aegis >[/aegis.assistant]")
