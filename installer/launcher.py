#!/usr/bin/env python3
"""Claude Code Local Panel — user-friendly launcher (Linux, macOS, Windows).

Designed to be run by a non-technical user after extracting the OS-specific
archive:
  - Linux/macOS: ``./claude-panel``
  - Windows:     ``claude-panel.exe``

Responsibilities on first run:
  1. Detect OS and architecture; pick the right `clickhouse-local` binary
     (or, on Windows, fall back to WSL with a friendly dialog if missing).
  2. Download `clickhouse-local` (~150-180 MB) into the per-user app dir.
  3. Resolve bundled assets (works both from source and from PyInstaller bundle).
  4. Start the local HTTP server on a free port.
  5. Open the user's default browser pointing at the panel.

Errors and missing prerequisites surface through a small Tk dialog so the user
gets a readable message instead of a Python traceback.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Platform profiles — single source of truth for ClickHouse downloads.
# ---------------------------------------------------------------------------

PROFILES = {
    "linux-x86_64":   {"url": "https://builds.clickhouse.com/master/amd64/clickhouse",          "ext": ""},
    "linux-aarch64":  {"url": "https://builds.clickhouse.com/master/aarch64/clickhouse",        "ext": ""},
    "macos-x86_64":   {"url": "https://builds.clickhouse.com/master/macos/clickhouse",          "ext": ""},
    "macos-arm64":    {"url": "https://builds.clickhouse.com/master/macos-aarch64/clickhouse",  "ext": ""},
    # Windows has no native ClickHouse binary. We always go through WSL,
    # which itself downloads the Linux x86_64 build.
    "windows-x86_64": {"url": "https://builds.clickhouse.com/master/amd64/clickhouse",          "ext": "", "needs_wsl": True},
}

DEFAULT_PORT = 8765

if sys.platform == "win32":
    APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "ClaudeCodePanel"
elif sys.platform == "darwin":
    APP_DIR = Path.home() / "Library" / "Application Support" / "ClaudeCodePanel"
else:
    APP_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "claude-panel"

VENDOR_DIR = APP_DIR / "vendor"
LOG_FILE = APP_DIR / "launcher.log"


def current_profile() -> str:
    sys_name = platform.system().lower()
    arch = (platform.machine() or "").lower()
    if sys_name == "linux":
        return "linux-aarch64" if arch in ("aarch64", "arm64") else "linux-x86_64"
    if sys_name == "darwin":
        return "macos-arm64" if arch in ("arm64", "aarch64") else "macos-x86_64"
    if sys_name == "windows":
        return "windows-x86_64"
    raise RuntimeError(f"Sistema operativo no soportado: {sys_name} {arch}")


def clickhouse_filename() -> str:
    # On Windows we still run the Linux binary inside WSL, so no .exe extension.
    return "clickhouse"


def clickhouse_path() -> Path:
    return VENDOR_DIR / clickhouse_filename()


# ---------------------------------------------------------------------------
# Logging and UI helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass
    print(line, end="", file=sys.stderr)


def _tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    return root


def show_error(title: str, message: str) -> None:
    try:
        from tkinter import messagebox
        root = _tk_root()
        messagebox.showerror(title, message)
        root.destroy()
    except Exception:
        print(f"ERROR · {title}\n{message}", file=sys.stderr)


def show_info(title: str, message: str) -> None:
    try:
        from tkinter import messagebox
        root = _tk_root()
        messagebox.showinfo(title, message)
        root.destroy()
    except Exception:
        print(f"INFO · {title}\n{message}", file=sys.stderr)


def ask_yes_no(title: str, message: str) -> bool:
    try:
        from tkinter import messagebox
        root = _tk_root()
        ok = messagebox.askyesno(title, message)
        root.destroy()
        return bool(ok)
    except Exception:
        ans = input(f"{title}\n{message} [y/N] ").strip().lower()
        return ans in ("y", "yes", "s", "si", "sí")


class ProgressDialog:
    """Lightweight Tk progress bar. Falls back to stderr counter."""

    def __init__(self, title: str, message: str) -> None:
        self.tk_ok = False
        try:
            import tkinter as tk
            from tkinter import ttk
            self.root = tk.Tk()
            self.root.title(title)
            self.root.geometry("440x140")
            self.root.resizable(False, False)
            tk.Label(self.root, text=message, padx=16, pady=14, justify="left", wraplength=400).pack()
            self.bar = ttk.Progressbar(self.root, mode="determinate", length=380)
            self.bar.pack(pady=4)
            self.status = tk.StringVar(value="Preparando…")
            tk.Label(self.root, textvariable=self.status, fg="#475569").pack()
            self.tk_ok = True
            self.root.update()
        except Exception:
            self.root = None

    def update(self, downloaded: int, total: int) -> None:
        if not self.tk_ok:
            mb = downloaded / 1_000_000
            print(f"\r  descargado {mb:6.1f} MB", end="", file=sys.stderr)
            return
        if total > 0:
            self.bar["maximum"] = total
            self.bar["value"] = downloaded
            self.status.set(f"{downloaded/1_000_000:.1f} MB / {total/1_000_000:.1f} MB")
        else:
            self.bar.step(1)
            self.status.set(f"{downloaded/1_000_000:.1f} MB")
        try:
            self.root.update()
        except Exception:
            self.tk_ok = False

    def close(self) -> None:
        if self.tk_ok:
            try:
                self.root.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# WSL helpers (Windows only)
# ---------------------------------------------------------------------------

def wsl_available() -> bool:
    if sys.platform != "win32":
        return False
    if not shutil.which("wsl"):
        return False
    try:
        r = subprocess.run(["wsl", "--status"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def wsl_path_to_windows(path: str) -> str:
    """Convert a Windows path to a /mnt/c-style WSL path."""
    p = path.replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        drive = p[0].lower()
        return f"/mnt/{drive}{p[2:]}"
    return p


def ensure_clickhouse_windows() -> list[str]:
    """On Windows we run ClickHouse inside WSL. Returns a command prefix list,
    e.g. ['wsl', '/mnt/c/Users/<u>/AppData/.../clickhouse']."""
    if not wsl_available():
        msg = (
            "El panel necesita WSL (Windows Subsystem for Linux) para ejecutar "
            "ClickHouse en Windows.\n\n"
            "Cómo activarlo (una sola vez):\n"
            "  1. Abre PowerShell como Administrador.\n"
            "  2. Ejecuta:  wsl --install\n"
            "  3. Reinicia el equipo cuando te lo pida.\n"
            "  4. Vuelve a abrir Claude Code Panel.\n\n"
            "¿Quieres que abra la página oficial de instalación de WSL?"
        )
        if ask_yes_no("Falta WSL", msg):
            webbrowser.open("https://learn.microsoft.com/windows/wsl/install")
        raise RuntimeError("WSL no está instalado o no responde.")

    target = clickhouse_path()
    if not target.exists():
        download_clickhouse(PROFILES["windows-x86_64"]["url"], target)
    wsl_target = wsl_path_to_windows(str(target.resolve()))
    # Make sure the binary is executable from inside WSL.
    subprocess.run(["wsl", "chmod", "+x", wsl_target], check=False, capture_output=True)
    return ["wsl", wsl_target]


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_with_progress(url: str, dest: Path, on_progress: Callable[[int, int], None]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        downloaded = 0
        chunk = 1 << 16
        with tmp.open("wb") as f:
            while True:
                buf = resp.read(chunk)
                if not buf:
                    break
                f.write(buf)
                downloaded += len(buf)
                on_progress(downloaded, total)
    tmp.replace(dest)
    try:
        dest.chmod(0o755)
    except OSError:
        pass  # Windows doesn't need chmod


def download_clickhouse(url: str, dest: Path) -> None:
    log(f"Descargando ClickHouse desde {url} -> {dest}")
    dlg = ProgressDialog(
        "Preparando Claude Code Panel",
        "Descargando ClickHouse local.\nSólo ocurre la primera vez (≈150 MB).",
    )
    try:
        download_with_progress(url, dest, dlg.update)
    except urllib.error.URLError as e:
        dlg.close()
        raise RuntimeError(
            "No se pudo descargar ClickHouse.\n\n"
            f"Detalle: {e}\n\n"
            "Comprueba tu conexión a internet y vuelve a abrir el panel."
        ) from e
    finally:
        dlg.close()
    log(f"ClickHouse listo: {dest}")


# ---------------------------------------------------------------------------
# Asset resolution and ClickHouse command prefix
# ---------------------------------------------------------------------------

def resolve_assets_dir() -> Path:
    """Return the directory served as the panel root."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        bundled = Path(sys._MEIPASS)
        if (bundled / "index.html").exists():
            return bundled
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent / "local-claude-panel",
    ]
    for c in candidates:
        if (c / "index.html").exists():
            return c
    raise RuntimeError("No se han encontrado los assets del panel (index.html).")


def detect_system_clickhouse() -> Optional[str]:
    for name in ("clickhouse-local", "clickhouse"):
        p = shutil.which(name)
        if p:
            return p
    return None


def resolve_clickhouse_command() -> tuple[list[str], str]:
    """Return (argv_prefix, identifier_for_logs).

    On Linux/macOS the result is ['/path/to/clickhouse-local'].
    On Windows it is ['wsl', '/mnt/c/.../clickhouse'] (Linux binary in WSL).
    """
    profile_id = current_profile()
    profile = PROFILES[profile_id]

    if profile.get("needs_wsl"):
        return ensure_clickhouse_windows(), f"WSL+{profile_id}"

    # First, see if the user already has ClickHouse on PATH — skip the download.
    system = detect_system_clickhouse()
    if system:
        return [system], f"sistema:{system}"

    target = clickhouse_path()
    if not target.exists() or not os.access(target, os.X_OK):
        download_clickhouse(profile["url"], target)
    return [str(target)], f"cache:{target}"


# ---------------------------------------------------------------------------
# Server orchestration
# ---------------------------------------------------------------------------

def detect_claude_projects() -> Optional[Path]:
    p = Path.home() / ".claude" / "projects"
    return p if p.exists() else None


def pick_free_port(preferred: int) -> int:
    for port in [preferred, *range(preferred + 1, preferred + 20)]:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No hay puertos libres entre 8765 y 8784.")


def wait_until_up(url: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> int:
    try:
        assets = resolve_assets_dir()
        ch_cmd, ch_desc = resolve_clickhouse_command()
        log(f"ClickHouse: {ch_desc} (argv={ch_cmd})")

        # Single-binary path → server uses CLAUDE_PANEL_CLICKHOUSE_BIN; for
        # multi-arg invocations (e.g. ['wsl', '/mnt/c/.../clickhouse']) we use
        # CLAUDE_PANEL_CLICKHOUSE_ARGV with NUL separators.
        if len(ch_cmd) == 1:
            os.environ["CLAUDE_PANEL_CLICKHOUSE_BIN"] = ch_cmd[0]
        else:
            os.environ["CLAUDE_PANEL_CLICKHOUSE_ARGV"] = "\x00".join(ch_cmd)

        os.environ["CLAUDE_PANEL_BASE_DIR"] = str(assets)
        os.environ["CLAUDE_PANEL_LOCAL_GLOB"] = str(assets / "local-data" / "projects" / "*" / "*.jsonl")
        os.environ["CLAUDE_PANEL_HOME_GLOB"] = str(Path.home() / ".claude" / "projects" / "*" / "*.jsonl")

        port = pick_free_port(DEFAULT_PORT)
        url = f"http://127.0.0.1:{port}"

        sys.path.insert(0, str(assets))
        if (assets / "local_server.py").exists():
            import local_server  # type: ignore
        else:
            repo_local = Path(__file__).resolve().parent.parent / "local-claude-panel"
            sys.path.insert(0, str(repo_local))
            import local_server  # type: ignore

        from http.server import ThreadingHTTPServer
        server = ThreadingHTTPServer(("127.0.0.1", port), local_server.Handler)
        server.query_timeout = 120  # type: ignore[attr-defined]

        def serve() -> None:
            try:
                server.serve_forever()
            except Exception as e:
                log(f"servidor terminado: {e}")

        threading.Thread(target=serve, daemon=True).start()

        if not wait_until_up(url + "/health", timeout=5):
            log("servidor no respondió al /health en 5 s, abriendo browser igualmente")

        log(f"Abriendo navegador en {url}")
        webbrowser.open(url)

        if not detect_claude_projects():
            log("Aviso: ~/.claude/projects no existe — se mostrará pantalla de bienvenida")

        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            log("KeyboardInterrupt — cerrando")
            return 0
    except Exception as exc:
        log(f"ERROR: {exc!r}")
        show_error("Claude Code Panel", str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
