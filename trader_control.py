"""Small local process supervisor for the Telegram live-trading engine."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class TraderSettings:
    quantity: int = 100
    lot_size: int = 10


class TraderProcessManager:
    """Start/stop one live_trader process and retain a small output buffer."""

    def __init__(self, project_dir: str | Path | None = None) -> None:
        self.project_dir = Path(project_dir or Path(__file__).resolve().parent)
        self.settings = TraderSettings(
            quantity=int(os.getenv("FYERS_QUANTITY", "100")),
            lot_size=int(os.getenv("FYERS_LOT_SIZE", "10")),
        )
        self._process: subprocess.Popen[str] | None = None
        self._lock = threading.RLock()
        self._output: deque[str] = deque(maxlen=200)
        self._reader: threading.Thread | None = None

    @staticmethod
    def _validate(quantity: int, lot_size: int) -> None:
        if quantity <= 0 or lot_size <= 0:
            raise ValueError("quantity and lot size must be greater than zero")
        if quantity % lot_size:
            raise ValueError("quantity must be a multiple of lot size")

    def _is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _read_output(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            with self._lock:
                self._output.append(line.rstrip())

    def update_settings(self, quantity: int, lot_size: int) -> dict[str, Any]:
        self._validate(quantity, lot_size)
        with self._lock:
            self.settings = TraderSettings(quantity, lot_size)
            return self.status()

    def start(
        self,
        *,
        quantity: int | None = None,
        lot_size: int | None = None,
        telegram_chat: str | None = None,
        telegram_session: str | None = None,
        mock_market: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            selected_quantity = self.settings.quantity if quantity is None else quantity
            selected_lot_size = self.settings.lot_size if lot_size is None else lot_size
            self._validate(selected_quantity, selected_lot_size)
            if self._is_running():
                raise RuntimeError("live trader is already running")
            selected_chat = telegram_chat or os.getenv("TELEGRAM_CHAT")
            if not selected_chat:
                raise ValueError("Telegram chat is required; set TELEGRAM_CHAT or enter it in the UI")
            self.settings = TraderSettings(selected_quantity, selected_lot_size)
            command = [
                sys.executable,
                str(self.project_dir / "live_trader.py"),
                "--quantity",
                str(selected_quantity),
                "--lot-size",
                str(selected_lot_size),
            ]
            command.extend(["--telegram-chat", selected_chat])
            if telegram_session:
                command.extend(["--telegram-session", telegram_session])
            if mock_market:
                command.append("--mock-market")

            environment = os.environ.copy()
            environment["FYERS_QUANTITY"] = str(selected_quantity)
            environment["FYERS_LOT_SIZE"] = str(selected_lot_size)
            self._output.clear()
            self._process = subprocess.Popen(
                command,
                cwd=self.project_dir,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            self._reader = threading.Thread(
                target=self._read_output,
                args=(self._process,),
                name="live-trader-output",
                daemon=True,
            )
            self._reader.start()
            return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if not self._is_running():
                return self.status()
            assert self._process is not None
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=5)
            return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            running = self._is_running()
            return {
                "running": running,
                "pid": self._process.pid if self._process is not None and running else None,
                "return_code": self._process.poll() if self._process is not None else None,
                "settings": asdict(self.settings),
                "output": list(self._output),
            }
