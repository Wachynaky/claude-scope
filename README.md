# Claude Scope

![Claude Scope dashboard](img/claude-scope.png)

Local observability dashboard for Claude Code sessions. It reads your JSONL
files in `~/.claude/projects/` with embedded ClickHouse (chdb) and explores them
in a browser. **Nothing leaves your machine.**

Features:

- **Traces and observations** by session, turn and "trace" (grouped turns).
- **Real billable cost** per model: input, output, cache R/W 5m + 1h and server
  tools, adjusted by service tier. Cost deduplicated by `requestId`.
- **Per-turn and per-session detail**: prompt, response, tool calls
  (input/output) and tokens for each step.
- **Filters**: date, project, model, tool.

> Want to see it first? Jump to [The panel views](#the-panel-views) at the end.

## How to run it

You only need **Python 3.9 or newer**. The data engine is
[**chdb**](https://github.com/chdb-io/chdb) (embedded ClickHouse), installed with
`pip`: no binary to download, nothing to configure. The panel opens the browser
on its own.

### Linux and macOS

Copy and paste this block into a terminal:

```bash
git clone https://github.com/Wachynaky/claude-scope.git
cd claude-scope
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 claude-scope/local_server.py
```

That's it: `http://127.0.0.1:8765` opens in your browser with the panel.

> The first time, `pip install` downloads chdb (it bundles the ClickHouse
> engine, takes a moment). After that it starts instantly and works offline.

If you don't have Python: on Debian/Ubuntu `sudo apt install python3 python3-venv`;
on macOS install it from [python.org](https://www.python.org/downloads/) or with
Homebrew (`brew install python`).

### Windows

chdb (ClickHouse) has no native Windows build, so the panel runs **inside WSL**
(Windows Subsystem for Linux), where everything works the same as on Linux. You
only need to enable WSL once:

```powershell
# PowerShell as Administrator, first time only
wsl --install
# Reboot when prompted.
```

Then open an **Ubuntu/WSL** terminal and, inside WSL, copy and paste the exact
same block as on Linux:

```bash
git clone https://github.com/Wachynaky/claude-scope.git
cd claude-scope
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 claude-scope/local_server.py
```

If Python is missing inside WSL: `sudo apt install python3 python3-venv`.

---

- Next time (everything already installed), just enter the folder and run:
  ```bash
  source .venv/bin/activate
  python3 claude-scope/local_server.py
  ```
- To **stop** the panel: press `Ctrl + C` in the terminal (or use the power-off
  button inside the panel itself).

## The initial screen: where to read your sessions from

The first time you open the panel it asks **where to read the session files
from** (Claude Code's `.jsonl` files):

<table>
  <tr>
    <td><img src="img/pantalla-inicial.png" alt="Initial screen: Where are your Claude Code sessions?" width="520"></td>
  </tr>
</table>

You have three options:

- **Use Claude Code's default folder**: reads `~/.claude/projects/` in read-only.
  The usual choice if you use Claude Code on this same machine.
- **My history is in another folder**: opens a dialog to pick the folder where
  your `.jsonl` files are.
- **Drag / upload my .jsonl files**: they are copied into the panel and used as
  the local source; handy if someone sent you the sessions from another machine.

You can change the option anytime from the header. The panel **only reads** these
files, it never modifies them.

## What's in the folder

These are the only files needed to run the panel:

```
requirements.txt         # Dependency: chdb (embedded ClickHouse)

claude-scope/            # The panel itself
├─ index.html            # Single-page app
├─ local_server.py       # HTTP server + chdb queries (opens the browser)
├─ pricing.json          # Anthropic pricing
└─ assets/vendor/        # marked + ansi_up vendored (offline)
```

## Startup options

`local_server.py` takes a few parameters:

```bash
python3 claude-scope/local_server.py --port 9000   # another port (default 8765)
python3 claude-scope/local_server.py --no-open      # don't auto-open the browser
```

## The panel views

### The dashboard

![Claude Scope dashboard](img/claude-scope.png)

The main screen. At the very top, in the header:

- **Search** to filter by message content.
- **Token mode** (toggle on the right), which changes how tokens are counted
  across every metric:
  - **All & Real data**: input + output + cache, deduplicated per request. This
    is what you actually consume.
  - **Same as Claude Code**: raw input + output only, like the «Total tokens» in
    `/config` → Stats (usually higher because it doesn't deduplicate or count cache).
- **Data source** (the folder you're reading) and panel **zoom**.

Right below you choose **how to group** the data:

- **Group by trace** (default): each session is a trace row with all its turns
  nested below. The best view to walk through a full session.
- **Group by turn**: one row per prompt (turn), to compare individual turns.
- **Group by session**: one row per session, summary view.

And you filter by **project, model, tool and time range** (with «Clear filters»
to reset them). The stats bar sums up traces, tokens, total cost, API time and
permissions, and below there are charts for **daily tokens, tokens per tool,
token mix (input/output/cache), cost mix, cost per model, tool calls, MCP servers
and projects**.

### The conversation

Click a trace to open the session as a **chat**: System / You / Assistant bubbles
with the tokens and cost of each turn, and the rendered content (markdown, code
blocks, ASCII...).

Selecting a turn opens the **«Details of invocation»** panel on the right, with
cards for Cost, **Total** tokens, Input and Output (same colors as the session
cards), a **spans** diagram of the invocation and all its metadata. The divider
between the chat and the panel is **draggable**: pull it to widen whichever side
you need at any moment.

![Conversation with the details panel](img/conversacion-detalle.png)

Each **tool call** (WebSearch, WebFetch, Read, Bash...) expands with its **Input /
Output / Metadata** tabs to inspect exactly what went in and what came out:

## License

Apache 2.0, see `LICENSE`.
