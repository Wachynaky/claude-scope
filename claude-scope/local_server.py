#!/usr/bin/env python3
"""Local HTTP bridge for the Claude Code ClickHouse panel.

The server reads Claude Code JSONL files (read-only) and exposes a small set
of HTTP endpoints to the front-end:

  GET  /health             — server status + has_data + first_run flag.
  GET  /config             — current data source configuration.
  POST /config             — persist configuration {mode, projects_dir}.
  POST /config/pick-folder — open a native folder picker (Tk), return path.
  POST /upload-sessions    — receive uploaded .jsonl files, store under
                             local-data/projects/<bucket>/.
  POST /reset              — clear config, return to first-run wizard.
  POST /query              — run a SELECT against the JSONL via clickhouse-local.
  POST /shutdown           — terminate the server (used by the panel's
                             Shutdown button).
"""

from __future__ import annotations

import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


BASE_DIR = Path(os.environ.get("CLAUDE_PANEL_BASE_DIR") or Path(__file__).resolve().parent)
LOCAL_DATA_ROOT = Path(os.environ.get("CLAUDE_PANEL_LOCAL_DATA_DIR") or (BASE_DIR / "local-data" / "projects"))
LOCAL_DATA_GLOB = os.environ.get("CLAUDE_PANEL_LOCAL_GLOB") or str(LOCAL_DATA_ROOT / "*" / "*.jsonl")
HOME_DATA_GLOB = os.environ.get("CLAUDE_PANEL_HOME_GLOB") or str(Path.home() / ".claude" / "projects" / "*" / "*.jsonl")

# Persistent panel configuration. Lives in the same per-user dir the launcher
# uses for clickhouse-local cache.
if sys.platform == "win32":
    _APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ClaudeCodePanel"
elif sys.platform == "darwin":
    _APP_DIR = Path.home() / "Library" / "Application Support" / "ClaudeCodePanel"
else:
    _APP_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "claude-panel"
CONFIG_FILE = Path(os.environ.get("CLAUDE_PANEL_CONFIG_FILE") or (_APP_DIR / "config.json"))

VALID_MODES = {"default", "custom_path", "local_copy"}

FORMAT_RE = re.compile(r"\s+FORMAT\s+JSON\s*;?\s*$", re.IGNORECASE)
RAW_TABLE_RE = re.compile(r"\bFROM\s+claude_code\.raw\b", re.IGNORECASE)


def sql_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config() -> dict:
    try:
        with CONFIG_FILE.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return {}
        if cfg.get("mode") not in VALID_MODES:
            cfg.pop("mode", None)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(CONFIG_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_FILE)


def reset_config() -> None:
    try:
        CONFIG_FILE.unlink()
    except FileNotFoundError:
        pass


def resolve_custom_glob(path: str) -> str:
    """Given a folder the user picked, return the best matching glob for JSONL.

    Users may pick any of these and we should "just work":

      - ``.../.claude``               (root of Claude Code; data lives in ./projects)
      - ``.../.claude/projects``      (already the right level)
      - ``.../some-folder``           (recursive fallback)
      - ``.../-encoded-project-name`` (a single project's folder)

    Order:
      1. If ``<path>/projects/*/*.jsonl`` matches anything → use it.
      2. If ``<path>/*/*.jsonl`` matches → use it.
      3. If ``<path>/*.jsonl`` matches (single project picked) → use it.
      4. Fallback to recursive ``<path>/**/*.jsonl`` (clickhouse-local + glob
         both support ** but we still guard the result via glob.glob).
    """
    p = Path(path)
    candidates = [
        p / "projects" / "*" / "*.jsonl",
        p / "*" / "*.jsonl",
        p / "*.jsonl",
        p / "**" / "*.jsonl",
    ]
    for cand in candidates:
        s = str(cand)
        # recursive=True needed for the ** form
        if glob.glob(s, recursive=True):
            return s
    # Nothing matched yet — keep the most permissive pattern so /health can
    # report has_data=false and the panel suggests changing the folder.
    return str(p / "**" / "*.jsonl")


def select_data_glob() -> str:
    """Return the glob the panel should read from, based on persistent config.

    Order:
      1. Explicit config file (mode = default / custom_path / local_copy).
      2. If config absent, but local-data/projects has files → use that
         (mostly for the dev workflow where someone ran prepare_claude_data.sh).
      3. Otherwise fall back to ~/.claude/projects so the welcome screen of
         a fresh user can see "no sessions" instead of erroring out.
    """
    cfg = load_config()
    mode = cfg.get("mode")
    if mode == "default":
        return HOME_DATA_GLOB
    if mode == "custom_path":
        path = cfg.get("projects_dir") or ""
        if path:
            return resolve_custom_glob(path)
    if mode == "local_copy":
        return str(LOCAL_DATA_ROOT / "**" / "*.jsonl")
    # No config yet — keep the legacy auto behaviour so existing dev setups work.
    if glob.glob(LOCAL_DATA_GLOB, recursive=True):
        return LOCAL_DATA_GLOB
    return HOME_DATA_GLOB


def first_run() -> bool:
    """True until the user has explicitly chosen a data source mode."""
    cfg = load_config()
    return cfg.get("mode") not in VALID_MODES


# ---------------------------------------------------------------------------
# ClickHouse query handling
# ---------------------------------------------------------------------------

def normalize_query(sql: str) -> str:
    query = FORMAT_RE.sub("", sql.strip()).strip()
    if query.endswith(";"):
        query = query[:-1].strip()
    if ";" in query:
        raise ValueError("Only one SELECT query is allowed")
    if not (query.lower().startswith("select") or query.lower().startswith("with")):
        raise ValueError("Only SELECT/WITH queries are allowed")
    return RAW_TABLE_RE.sub("FROM raw", query)


def build_clickhouse_query(sql: str) -> str:
    data_glob = select_data_glob()
    raw_cte = f"""raw AS (
    SELECT
        _path AS path,
        CAST(json, 'JSON') AS data
    FROM file({sql_quote(data_glob)}, 'JSONAsString')
    WHERE isValidJSON(json)
)
"""
    query = normalize_query(sql)
    if query.lower().startswith("with"):
        return "WITH " + raw_cte + ",\n" + query[4:].lstrip() + "\nFORMAT JSON"
    return "WITH " + raw_cte + query + "\nFORMAT JSON"


def clickhouse_invocation() -> list[str]:
    argv_env = os.environ.get("CLAUDE_PANEL_CLICKHOUSE_ARGV")
    if argv_env:
        return argv_env.split("\x00")
    binary = shutil_which("clickhouse-local") or shutil_which("clickhouse") or "clickhouse-local"
    return [binary]


def run_clickhouse(sql: str, timeout: int) -> tuple[int, str, str]:
    query = build_clickhouse_query(sql)
    cmd = clickhouse_invocation() + ["--query", query]
    proc = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ---------------------------------------------------------------------------
# Native folder picker (used by /config/pick-folder)
# ---------------------------------------------------------------------------

def pick_folder_dialog(initialdir: str | None = None) -> str:
    """Open a native folder selection dialog using Tk; return the chosen path
    or an empty string if the user cancels. Tk runs in this process; the
    GUI thread is acquired briefly and released right after.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError(f"No se pudo abrir un selector de carpeta: {exc}") from exc

    root = tk.Tk()
    root.withdraw()
    # On Linux, force the dialog to appear above other windows.
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    chosen = filedialog.askdirectory(
        parent=root,
        title="Selecciona la carpeta de sesiones de Claude Code",
        initialdir=initialdir or str(Path.home()),
        mustexist=True,
    )
    root.destroy()
    return chosen or ""


# ---------------------------------------------------------------------------
# Upload handler (drag & drop of .jsonl files)
# ---------------------------------------------------------------------------

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str, fallback: str = "session") -> str:
    base = os.path.basename(name).strip() or fallback
    cleaned = _SAFE_NAME_RE.sub("-", base).strip("-.")
    return cleaned or fallback


def store_uploaded_jsonl(filename: str, data: bytes, relpath: str = "") -> str:
    """Persist an uploaded JSONL under LOCAL_DATA_ROOT/<bucket>/<file>.jsonl."""
    LOCAL_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    rel = (relpath or "").replace("\\", "/")
    parts = [p for p in rel.split("/") if p and p not in (".", "..")]
    parent_parts = parts[:-1] if parts else []
    bucket = "/".join(safe_name(p, "project") for p in parent_parts) or "uploaded"
    dest_dir = LOCAL_DATA_ROOT / bucket
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = safe_name(filename, f"session-{int(time.time())}")
    if not fname.lower().endswith(".jsonl"):
        fname += ".jsonl"
    dest = dest_dir / fname
    # Avoid overwriting an existing distinct upload by appending a counter.
    i = 1
    while dest.exists() and dest.read_bytes() != data:
        dest = dest_dir / f"{fname[:-6]}-{i}.jsonl"
        i += 1
    dest.write_bytes(data)
    return str(dest)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(SimpleHTTPRequestHandler):
    server_version = "ClaudeCodeLocalPanel/1.0"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    # ---- GET --------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path == "/health":
            data_glob = select_data_glob()
            matches = glob.glob(data_glob, recursive=True)
            cfg = load_config()
            self.send_json({
                "ok": True,
                "data_glob": data_glob,
                "has_data": bool(matches),
                "session_files": len(matches),
                "first_run": first_run(),
                "config": cfg,
            })
            return
        if self.path == "/config":
            self.send_json({"config": load_config(), "first_run": first_run()})
            return
        super().do_GET()

    # ---- POST -------------------------------------------------------------

    def do_POST(self) -> None:
        if self.path == "/shutdown":
            self.send_text(200, "stopping")
            import threading as _t
            _t.Thread(target=lambda: (__import__("os")._exit(0)), daemon=True).start()
            return
        if self.path == "/reset":
            reset_config()
            self.send_json({"ok": True})
            return
        if self.path == "/config":
            self._handle_config_save()
            return
        if self.path == "/config/pick-folder":
            self._handle_pick_folder()
            return
        if self.path == "/upload-sessions":
            self._handle_upload()
            return
        if self.path == "/query":
            self._handle_query()
            return
        self.send_error(404, "Unknown endpoint")

    # ---- helpers ----------------------------------------------------------

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8") if length else ""
        if not body.strip():
            return {}
        return json.loads(body)

    def _handle_query(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            payload = json.loads(body) if body.strip().startswith("{") else {"query": body}
            sql = str(payload.get("query", ""))
            if not sql.strip():
                raise ValueError("Missing query")
            code, stdout, stderr = run_clickhouse(sql, int(getattr(self.server, "query_timeout", 120)))
            if code != 0:
                self.send_text(500, stderr.strip() or stdout.strip() or "clickhouse-local failed")
                return
            try:
                self.send_raw_json(stdout)
            except BrokenPipeError:
                return
        except subprocess.TimeoutExpired:
            self.send_text(504, "clickhouse-local query timed out")
        except Exception as exc:
            self.send_text(400, str(exc))

    def _handle_config_save(self) -> None:
        try:
            payload = self._read_json_body()
            mode = payload.get("mode")
            if mode not in VALID_MODES:
                raise ValueError(f"mode debe ser uno de {sorted(VALID_MODES)}")
            cfg: dict[str, Any] = {"mode": mode, "saved_at": int(time.time())}
            if mode == "custom_path":
                projects_dir = (payload.get("projects_dir") or "").strip()
                if not projects_dir:
                    raise ValueError("projects_dir requerido para mode=custom_path")
                p = Path(projects_dir).expanduser()
                if not p.exists() or not p.is_dir():
                    raise ValueError(f"La carpeta no existe: {p}")
                cfg["projects_dir"] = str(p)
            save_config(cfg)
            self.send_json({"ok": True, "config": cfg})
        except Exception as exc:
            self.send_text(400, str(exc))

    def _handle_pick_folder(self) -> None:
        try:
            payload = {}
            try:
                payload = self._read_json_body()
            except Exception:
                pass
            initial = payload.get("initial_dir") if isinstance(payload, dict) else None
            chosen = pick_folder_dialog(initial)
            self.send_json({"path": chosen})
        except Exception as exc:
            self.send_text(500, str(exc))

    def _handle_upload(self) -> None:
        """Accept a JSON body of the form:
            {"files": [{"name": "x.jsonl", "relpath": "a/b/x.jsonl",
                        "content_b64": "..."}, ...]}
        We use JSON+base64 (no multipart) so we don't depend on the `cgi`
        stdlib module, removed in Python 3.13.
        """
        try:
            payload = self._read_json_body()
            files = payload.get("files") or []
            if not isinstance(files, list):
                raise ValueError("'files' debe ser una lista")
            saved: list[dict] = []
            errors: list[str] = []
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "").strip()
                relpath = str(entry.get("relpath") or name)
                b64 = entry.get("content_b64")
                if not name or not b64:
                    errors.append(f"{name or '(sin nombre)'}: faltan campos")
                    continue
                if not name.lower().endswith(".jsonl"):
                    continue
                try:
                    raw = base64.b64decode(b64, validate=False)
                except Exception as e:
                    errors.append(f"{name}: base64 inválido ({e})")
                    continue
                if not raw:
                    errors.append(f"{name}: vacío")
                    continue
                try:
                    dest = store_uploaded_jsonl(name, raw, relpath)
                    saved.append({"name": name, "path": dest, "size": len(raw)})
                except Exception as e:
                    errors.append(f"{name}: {e}")
            self.send_json({"ok": True, "saved": saved, "errors": errors})
        except Exception as exc:
            self.send_text(400, str(exc))

    # ---- output helpers ---------------------------------------------------

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_raw_json(json.dumps(payload))

    def send_raw_json(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            return


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve the local Claude Code ClickHouse panel.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=int, default=120, help="clickhouse-local query timeout in seconds")
    return parser.parse_args()


def main() -> int:
    if not shutil_which("clickhouse-local"):
        print("clickhouse-local was not found in PATH", file=sys.stderr)
        return 1
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.query_timeout = args.timeout
    print(f"Serving {BASE_DIR}")
    print(f"Reading JSONL from: {select_data_glob()}")
    print(f"Config file: {CONFIG_FILE}")
    print(f"Open http://{args.host}:{args.port}")
    server.serve_forever()
    return 0


def shutil_which(cmd: str) -> str | None:
    if cmd == "clickhouse-local":
        env_override = os.environ.get("CLAUDE_PANEL_CLICKHOUSE_BIN")
        if env_override and os.access(env_override, os.X_OK):
            return env_override
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(directory) / cmd
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


if __name__ == "__main__":
    raise SystemExit(main())
