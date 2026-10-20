# Project AEGIS — Terminal Experience & Rich Core (Sprint 002)

Project AEGIS features a rich, responsive terminal user interface powered by [Rich](https://github.com/Textualize/rich). It combines live streaming token generation with deterministic markdown rendering, real-time performance indicators, and graceful degradation across accessible, scriptable, and automated environments.

---

## 1. Visual Identity & Styling

AEGIS defines a dark-friendly, high-contrast visual theme anchored by an electric blue accent color.

### Palette & Style Tokens

| Token | Style | Hex / Value | Description |
|---|---|---|---|
| `aegis.accent` | `bold #3B82F6` | `#3B82F6` | Primary brand accent; headers and highlights |
| `aegis.user` | `bold #60A5FA` | `#60A5FA` | User input prefix (`you >`) |
| `aegis.assistant` | `bold #3B82F6` | `#3B82F6` | Assistant response prefix (`aegis >`) |
| `aegis.system` | `italic #9CA3AF` | `#9CA3AF` | System announcements and prompt metadata |
| `aegis.muted` | `#6B7280` | `#6B7280` | Timing indicators, chunk rates, secondary IDs |
| `aegis.success` | `bold #10B981` | `#10B981` | Health checks, successful saves, completions |
| `aegis.warning` | `bold #F59E0B` | `#F59E0B` | Confirmation requests, stop notifications |
| `aegis.error` | `bold #EF4444` | `#EF4444` | Provider failures, policy rejections, errors |
| `aegis.code` | `#38BDF8` | `#38BDF8` | Inline code spans and syntax accent |
| `aegis.table.header`| `bold #3B82F6` | `#3B82F6` | Table column headers |
| `aegis.link` | `underline #60A5FA` | `#60A5FA` | Clickable hyperlinks |

### Header

Every interactive or styled session begins with a compact session identity line:

```text
AEGIS · <model> · local
```

For example:
```text
AEGIS · qwen2.5-coder:1.5b · local
```

---

## 2. CLI Flags & Accessibility

AEGIS provides fine-grained controls for automation, pipelines, and accessibility:

### `--plain`
Emits raw, unformatted text without any ANSI escape sequences, theme styling, or animation indicators.
- Ideal for UNIX pipes (`aegis chat --plain | jq ...`), shell scripts, or clean file redirection.
- Never outputs ANSI color codes (verified zero `\033` bytes).
- Suppresses decorative banners, spinners, and completion lines.

### `--no-color` (and `NO_COLOR` environment variable)
Disables terminal color codes while preserving rich layout (such as ASCII table borders and list spacing).
- Adheres strictly to the [no-color.org](https://no-color.org) specification.
- Activated by either `--no-color` on the command line or setting `NO_COLOR=1` in the environment.

### `--tui`
Launches the full-screen interactive Textual terminal UI (Phase B).
- Requires the optional `tui` extra: `pip install "aegis-ai[tui]"`.
- Requires an interactive TTY stdout.
- If Textual is not installed or stdout is redirected/non-TTY, AEGIS prints a clear warning to stderr and seamlessly falls back to standard Rich terminal chat without failing.

### `--model <name>`
Overrides the model for the command or chat session (e.g. `--model gemma3:4b` or `--model qwen2.5-coder:1.5b`). Accepts any local Ollama model without validation restrictions.

---

## 3. Full-Screen Interactive TUI (Textual)

Project AEGIS provides an optional, full-screen terminal interface built with [Textual](https://github.com/Textualize/textual).

### Installation

Install the `tui` optional dependency into your virtual environment:

```bash
pip install -e ".[tui]"
# or for an existing release:
pip install "aegis-ai[tui]"
```

### Launching

Launch the full-screen TUI:

```bash
# Start an interactive chat session
aegis chat --tui

# Start with a specific model
aegis chat --tui --model gemma3:1b

# Start with an initial prompt
aegis chat --tui --prompt "What are the core safety tenets of AEGIS?"
```

### Keybindings & Controls

| Keybinding | Action | Description |
|---|---|---|
| `Ctrl+C` | Quit | Gracefully stops generation workers and exits the application |
| `Ctrl+Q` | Quit | Immediate clean exit |
| `Ctrl+L` | Clear | Clears chat message history |
| `Enter` | Submit | Submits current prompt in the input box |

### Architecture & Design

- **Dark-Friendly Aesthetics**: Designed with high-contrast slate-950 (`#0F172A`) background and `#3B82F6` electric blue branding.
- **Model Identity Header**: Persistent top header displaying session identity (`AEGIS · <model> · local`).
- **Scrollable Message History**: Distinct visual styling for user queries (light blue accent `#60A5FA`) versus assistant responses (`#3B82F6` accent).
- **Non-blocking Worker Streaming**: Generation tasks run in isolated background worker threads using `provider.generate_stream()`, continuously posting markdown chunk updates to the UI thread via thread-safe callbacks without dropping UI frames.
- **Full Markdown Rendering**: Native Textual `Markdown` widget renders formatted text, headers, lists, code fences, and tables.
- **Graceful Fallback**: The core CLI lazily imports Textual only when `--tui` is requested. If the dependency is missing or stdout is non-interactive, AEGIS logs a clear fallback notice and routes through the standard Rich streaming engine.

---

## 4. Streaming & Animations Architecture

### Safe Incremental Rendering (`StreamBuffer`)

Rendering streaming Markdown incrementally in a terminal presents subtle segmentation hazards:
1. **Trailing partial lines**: Splitting an incoming word or escape sequence mid-character.
2. **Open code fences**: Emitting an opening ` ``` ` before its corresponding closing fence arrives causes terminal formatters to mangle syntax highlighting.
3. **Incomplete tables**: Markdown tables require header, delimiter, and row alignments; emitting rows piecemeal degrades rendering into raw text.
4. **Partial bold**: Spanning `**bold text**` across chunk boundaries must not leak unclosed formatting.

AEGIS solves this with `aegis.cli.render.StreamBuffer`:
- Buffers incoming stream chunks deterministically.
- `feed(chunk: str) -> str`: Emits only stable prefixes safe to render as complete Markdown blocks.
- `flush() -> str`: Emits any remaining buffered content at the conclusion of the generation stream.
- **Invariant**: Never emits a half-open fence or a partial table row.

### No Fake Typewriter Delays

AEGIS renders Ollama tokens **as they are received**:
- No artificial `sleep()` or simulated typing delays.
- Immediate UI responsiveness reflects actual inference throughput.

### Indicators & Clean Signal Handling

1. **ThinkingIndicator**: Displays a non-intrusive dot spinner while waiting for Ollama to return response headers and the first token. Stops the instant the first chunk arrives.
2. **StreamIndicator**: Subtly tracks token emission rate (e.g. `▍ streaming · 12.4 tok/s`) during active streaming.
3. **Completion**: Prints a single summary line when generation completes:
   ```text
   ▍ done · 63 chunks in 6.87s (9.2 tok/s)
   ```
4. **Clean Ctrl+C**: When the operator sends `SIGINT` (Ctrl+C) mid-stream:
   - Indicators stop cleanly.
   - Text rendered up to that point is preserved on the terminal screen.
   - Prints `[Stop requested by user]`.
   - Exits immediately with standard status code `130`.

### Graceful Degradation Matrix

| Environment | Colors | Animations | Markdown | Completion Line |
|---|---|---|---|---|
| Interactive TTY | Yes (`#3B82F6`) | Yes (spinner + rate) | Yes (Rich formatted) | Yes |
| `--plain` flag | No | No | No (raw text) | No |
| `NO_COLOR=1` | No | No | Yes (plain borders) | No |
| `AEGIS_NO_ANIMATION=1` | Yes | No | Yes | No |
| Pipe / Redirection | No | No | Stripped | No |
