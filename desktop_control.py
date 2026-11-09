"""Simple Windows-friendly desktop control panel for the trading bot.

Run with the Python interpreter that has the project dependencies installed:
    python desktop_control.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from trader_control import TraderProcessManager


ENV_FIELDS = (
    ("client_id", "Fyers client ID", False),
    ("secret_key", "Fyers secret key", True),
    ("redirect_uri", "Fyers redirect URI", False),
    ("grant_type", "Fyers grant type", False),
    ("response_type", "Fyers response type", False),
    ("state", "Fyers state", False),
    ("pin", "Fyers PIN (optional)", True),
    ("TELEGRAM_API_ID", "Telegram API ID", False),
    ("TELEGRAM_API_HASH", "Telegram API hash", True),
    ("TELEGRAM_PHONE", "Telegram phone", False),
    ("TELEGRAM_CHAT", "Telegram chat ID", False),
    ("TELEGRAM_SESSION", "Telegram session path", False),
    ("FYERS_QUANTITY", "Trade quantity", False),
    ("FYERS_LOT_SIZE", "Lot size", False),
)


def read_env_file(path: str | Path) -> dict[str, str]:
    """Read simple KEY=value dotenv entries without exposing secrets."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize_session_value(value: str) -> str:
    """Convert shell-style home placeholders to the current OS home path."""
    normalized = value.strip().strip('"').strip("'")
    home = str(Path.home())
    if normalized.upper().startswith("$HOME\\") or normalized.upper().startswith("$HOME/"):
        normalized = home + normalized[5:]
    elif normalized.upper().startswith("${HOME}\\") or normalized.upper().startswith("${HOME}/"):
        normalized = home + normalized[7:]
    return str(Path(os.path.expandvars(normalized)).expanduser())


def write_env_file(path: str | Path, updates: dict[str, str]) -> None:
    """Update selected keys while preserving comments and unrelated settings."""
    env_path = Path(path)
    existing = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    output: list[str] = []
    for line in existing:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in updates:
            output.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            output.append(line)
    if output and output[-1].strip():
        output.append("")
    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")
    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def run_interactive_command(project_dir: Path, script: str, args: list[str]) -> None:
    """Run auth/dialog commands in a console so OTP and paste prompts work."""
    command = [sys.executable, str(project_dir / script), *args]
    kwargs: dict[str, object] = {"cwd": project_dir, "env": os.environ.copy()}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(command, **kwargs)


class DesktopControlApp:
    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir or Path(__file__).resolve().parent)
        self.env_path = self.project_dir / ".env"
        self.manager = TraderProcessManager(self.project_dir)
        self._widgets: dict[str, object] = {}
        self._root = None
        self._status = None
        self._log = None

    def _save_config(self) -> None:
        values = {
            key: str(widget.get()).strip()
            for key, widget in self._widgets.items()
            if hasattr(widget, "get")
        }
        write_env_file(self.env_path, values)
        os.environ.update({key: value for key, value in values.items() if value})
        self._set_status(".env saved")

    def _load_config(self) -> None:
        values = read_env_file(self.env_path)
        for key, widget in self._widgets.items():
            widget.delete(0, "end")
            value = values.get(key, "")
            if key == "TELEGRAM_SESSION" and value:
                value = normalize_session_value(value)
            widget.insert(0, value)

    def _set_status(self, text: str) -> None:
        if self._status is not None:
            self._status.config(text=text)

    def _refresh(self) -> None:
        status = self.manager.status()
        state = f"RUNNING (PID {status['pid']})" if status["running"] else "STOPPED"
        self._set_status(state)
        if self._log is not None:
            self._log.delete("1.0", "end")
            self._log.insert("end", "\n".join(status["output"]))
        if self._root is not None:
            self._root.after(1000, self._refresh)

    def _start_engine(self) -> None:
        self._save_config()
        try:
            quantity = int(self._widgets["FYERS_QUANTITY"].get())
            lot_size = int(self._widgets["FYERS_LOT_SIZE"].get())
            status = self.manager.start(quantity=quantity, lot_size=lot_size)
            self._set_status(f"RUNNING (PID {status['pid']})")
        except Exception as exc:
            self._set_status(f"Start failed: {exc}")

    def _stop_engine(self) -> None:
        try:
            self.manager.stop()
            self._set_status("STOPPED - verify Fyers positions")
        except Exception as exc:
            self._set_status(f"Stop failed: {exc}")

    def build(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        root = tk.Tk()
        self._root = root
        root.title("Option Trading Bot Control")
        root.geometry("760x720")
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Option Trading Bot", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(
            frame,
            text="Save configuration, authenticate, select Telegram chat, then start the engine.",
        ).pack(anchor="w", pady=(2, 10))

        form = ttk.Frame(frame)
        form.pack(fill="x")
        for row, (key, label, secret) in enumerate(ENV_FIELDS):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            entry = ttk.Entry(form, width=68, show="*" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", pady=2)
            self._widgets[key] = entry
        form.columnconfigure(1, weight=1)
        self._load_config()

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=10)
        ttk.Button(buttons, text="Save .env", command=self._save_config).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Fyers login/auth", command=lambda: self._save_then_command("auth.py", [])).pack(side="left", padx=6)
        ttk.Button(buttons, text="List Telegram chats", command=lambda: self._save_then_command("telegram_trade_listener.py", ["--list-dialogs"])).pack(side="left", padx=6)
        ttk.Button(buttons, text="Start engine", command=self._start_engine).pack(side="left", padx=6)
        ttk.Button(buttons, text="Stop engine", command=self._stop_engine).pack(side="left", padx=6)

        self._status = ttk.Label(frame, text="STOPPED", font=("Segoe UI", 11, "bold"))
        self._status.pack(anchor="w", pady=(4, 6))
        self._log = tk.Text(frame, height=12, wrap="word", state="normal", background="#101827", foreground="#dce7f7")
        self._log.pack(fill="both", expand=True)
        self._refresh()
        root.mainloop()

    def _save_then_command(self, script: str, args: list[str]) -> None:
        self._save_config()
        try:
            run_interactive_command(self.project_dir, script, args)
            self._set_status(f"Started {script} in a separate console")
        except Exception as exc:
            self._set_status(f"Could not start {script}: {exc}")


def main() -> None:
    DesktopControlApp().build()


if __name__ == "__main__":
    main()
