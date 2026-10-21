"""AEGIS startup check engine and cinematic presentation.

Runs health and readiness checks across all subsystems before chat/TUI interaction.
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from rich.console import Console

from aegis.config import AegisConfig
from aegis.interfaces import ModelProvider
from aegis.stop import StopController


class CheckState(str, Enum):
    """Subsystem check state."""

    READY = "ready"
    DEGRADED = "degraded"      # usable, but with a real limitation
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class StartupCheck:
    """Result of a single subsystem startup check."""

    key: str          # identity|memory|permissions|stop|ollama|model
    label: str        # human label, e.g. "Memory"
    state: CheckState
    detail: str       # real detail from the check (no invented data)
    ok: bool


@dataclass(frozen=True)
class StartupReport:
    """Aggregated startup diagnostic report."""

    checks: tuple[StartupCheck, ...]
    memory_available: bool
    ollama_available: bool
    model_available: bool
    stop_requested: bool
    elapsed: float


SHIELD_EMBLEM = [
    "[bold #3B82F6]    .---.[/bold #3B82F6]",
    "[bold #3B82F6]   /     \\   [/bold #3B82F6][bold #E2E8F0]AEGIS[/bold #E2E8F0] [aegis.muted]· local-first assistant[/aegis.muted]",
    "[bold #60A5FA]  |   ▲   |  [/bold #60A5FA][#94A3B8]interchangeable model runtime[/#94A3B8]",
    "[bold #3B82F6]   \\     /   [/bold #3B82F6][#64748B]─────────────────────────────[/#64748B]",
    "[bold #1D4ED8]    '---'[/bold #1D4ED8]",
]


def _should_animate(
    console: Console,
    plain: bool,
    animation: bool,
    env: Mapping[str, str] | None,
) -> bool:
    """Determine whether full animated emblem presentation is permitted."""
    if plain or not animation:
        return False
    effective_env = os.environ if env is None else env
    if "AEGIS_NO_ANIMATION" in effective_env:
        return False
    if "NO_COLOR" in effective_env:
        return False
    if console.no_color or not console.is_terminal:
        return False
    return True


def run_startup(
    console: Console,
    config: AegisConfig,
    provider: ModelProvider,
    *,
    model: str,
    plain: bool = False,
    animation: bool = True,
    env: Mapping[str, str] | None = None,
    stop_ctrl: StopController | None = None,
) -> StartupReport:
    """Execute startup readiness checks and render status to console.

    Runs exactly once per process. Propagates KeyboardInterrupt cleanly.
    """
    t0 = time.monotonic()
    checks: list[StartupCheck] = []
    animate = _should_animate(console, plain, animation, env)
    deadline = t0 + 2.0  # Max delay budget for animation

    def _animate_delay(delay: float) -> None:
        if stop_ctrl is not None and stop_ctrl.is_stopped():
            return
        now = time.monotonic()
        remaining = deadline - now
        if remaining <= 0.02:
            return
        time.sleep(min(delay, remaining))

    # 1. Identity Check
    try:
        from aegis.identity import AEGIS_NAME, IDENTITY_VERSION, identity_system_prompt

        sys_prompt = identity_system_prompt(model=model)
        if sys_prompt and AEGIS_NAME in sys_prompt and model in sys_prompt:
            id_check = StartupCheck(
                key="identity",
                label="Identity",
                state=CheckState.READY,
                detail=f"AEGIS v{IDENTITY_VERSION} ({model})",
                ok=True,
            )
        else:
            id_check = StartupCheck(
                key="identity",
                label="Identity",
                state=CheckState.UNAVAILABLE,
                detail="identity prompt invalid or empty",
                ok=False,
            )
    except Exception as e:
        id_check = StartupCheck(
            key="identity",
            label="Identity",
            state=CheckState.UNAVAILABLE,
            detail=str(e),
            ok=False,
        )
    checks.append(id_check)

    # 2. Memory Store Check
    try:
        from aegis.memory import SQLiteMemoryStore

        store = SQLiteMemoryStore(config.memory_db)
        try:
            count = store.count()
        finally:
            store.close()
        mem_check = StartupCheck(
            key="memory",
            label="Memory",
            state=CheckState.READY,
            detail=f"{count} records",
            ok=True,
        )
    except Exception as e:
        mem_check = StartupCheck(
            key="memory",
            label="Memory",
            state=CheckState.UNAVAILABLE,
            detail=str(e),
            ok=False,
        )
    checks.append(mem_check)

    # 3. Permissions Policy Check
    try:
        from aegis.interfaces import Decision, PermissionRequest, PermissionResult
        from aegis.permissions import DeterministicPolicy

        policy = DeterministicPolicy(config)
        req = PermissionRequest(action="sys.status")
        res = policy.evaluate(req)
        if isinstance(res, PermissionResult) and res.decision == Decision.ALLOW:
            perm_check = StartupCheck(
                key="permissions",
                label="Permissions",
                state=CheckState.READY,
                detail=f"deterministic policy ({res.rule})",
                ok=True,
            )
        elif isinstance(res, PermissionResult):
            perm_check = StartupCheck(
                key="permissions",
                label="Permissions",
                state=CheckState.READY,
                detail=f"{res.decision.value} ({res.rule})",
                ok=True,
            )
        else:
            perm_check = StartupCheck(
                key="permissions",
                label="Permissions",
                state=CheckState.UNAVAILABLE,
                detail="unexpected permission result",
                ok=False,
            )
    except Exception as e:
        perm_check = StartupCheck(
            key="permissions",
            label="Permissions",
            state=CheckState.UNAVAILABLE,
            detail=str(e),
            ok=False,
        )
    checks.append(perm_check)

    # 4. Stop Controller Check
    try:
        ctrl = stop_ctrl if stop_ctrl is not None else StopController(config.stop_file)
        if ctrl.is_stopped():
            stop_check_item = StartupCheck(
                key="stop",
                label="Stop",
                state=CheckState.STOPPED,
                detail=f"stop file present ({config.stop_file})",
                ok=False,
            )
        else:
            stop_check_item = StartupCheck(
                key="stop",
                label="Stop",
                state=CheckState.READY,
                detail="clear",
                ok=True,
            )
    except Exception as e:
        stop_check_item = StartupCheck(
            key="stop",
            label="Stop",
            state=CheckState.UNAVAILABLE,
            detail=str(e),
            ok=False,
        )
    checks.append(stop_check_item)

    # 5. Ollama Connectivity Check
    try:
        is_healthy = provider.health()
        if is_healthy:
            ollama_check = StartupCheck(
                key="ollama",
                label="Ollama",
                state=CheckState.READY,
                detail=f"online ({getattr(provider, 'host', config.ollama_host)})",
                ok=True,
            )
        else:
            err = getattr(provider, "last_error", lambda: None)()
            ollama_check = StartupCheck(
                key="ollama",
                label="Ollama",
                state=CheckState.UNAVAILABLE,
                detail=str(err) if err else "service unreachable",
                ok=False,
            )
    except Exception as e:
        ollama_check = StartupCheck(
            key="ollama",
            label="Ollama",
            state=CheckState.UNAVAILABLE,
            detail=str(e),
            ok=False,
        )
    checks.append(ollama_check)

    # 6. Model Availability Check
    if not ollama_check.ok:
        model_check = StartupCheck(
            key="model",
            label="Model",
            state=CheckState.SKIPPED,
            detail="Ollama offline",
            ok=False,
        )
    else:
        try:
            models_list = provider.list_models()
            found = False
            if model in models_list:
                found = True
            else:
                for m in models_list:
                    if m == model or (":" not in model and m.startswith(f"{model}:")):
                        found = True
                        break
            if found:
                model_check = StartupCheck(
                    key="model",
                    label="Model",
                    state=CheckState.READY,
                    detail=f"available ({model})",
                    ok=True,
                )
            else:
                model_check = StartupCheck(
                    key="model",
                    label="Model",
                    state=CheckState.DEGRADED,
                    detail=f"model '{model}' not in {models_list}",
                    ok=False,
                )
        except Exception as e:
            model_check = StartupCheck(
                key="model",
                label="Model",
                state=CheckState.UNAVAILABLE,
                detail=str(e),
                ok=False,
            )
    checks.append(model_check)

    # Presentation Rendering
    if animate:
        # 1. Subtle scan/reveal sequence of shield emblem lines
        for line in SHIELD_EMBLEM:
            console.print(line)
            _animate_delay(0.08)
        console.print()
        _animate_delay(0.12)

        # 2. Render each check line with scanning reveal
        state_markers = {
            CheckState.READY: ("[aegis.success]●[/aegis.success]", "[bold #E2E8F0]ready[/bold #E2E8F0]"),
            CheckState.DEGRADED: ("[aegis.warning]▲[/aegis.warning]", "[bold #F59E0B]degraded[/bold #F59E0B]"),
            CheckState.UNAVAILABLE: ("[aegis.error]■[/aegis.error]", "[bold #EF4444]unavailable[/bold #EF4444]"),
            CheckState.STOPPED: ("[aegis.error]■[/aegis.error]", "[bold #EF4444]stopped[/bold #EF4444]"),
            CheckState.SKIPPED: ("[aegis.muted]○[/aegis.muted]", "[aegis.muted]skipped[/aegis.muted]"),
        }

        for chk in checks:
            icon, state_txt = state_markers[chk.state]
            console.print(f" {icon}  [bold #94A3B8]{chk.label:<16}[/] {state_txt:<24} [aegis.muted]{chk.detail}[/]")
            _animate_delay(0.15)
        console.print()
        _animate_delay(0.05)

    elif plain:
        # Plain mode: concise text, zero ANSI
        for chk in checks:
            console.print(f"[{chk.state.value}] {chk.label.lower()}: {chk.detail}", markup=False)

    elif not console.is_terminal:
        # Non-TTY: silent on success; real failures emitted to stderr
        for chk in checks:
            if not chk.ok:
                sys.stderr.write(f"aegis warning: {chk.label.lower()} is {chk.state.value} ({chk.detail})\n")
        sys.stderr.flush()

    else:
        # TTY without animation (NO_COLOR / AEGIS_NO_ANIMATION / animation=False)
        for chk in checks:
            console.print(f"[{chk.state.value}] {chk.label}: {chk.detail}", markup=False)

    elapsed = time.monotonic() - t0
    return StartupReport(
        checks=tuple(checks),
        memory_available=mem_check.ok,
        ollama_available=ollama_check.ok,
        model_available=model_check.ok,
        stop_requested=(stop_check_item.state == CheckState.STOPPED),
        elapsed=elapsed,
    )
