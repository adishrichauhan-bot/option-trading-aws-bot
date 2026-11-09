import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

from desktop_control import normalize_session_value, read_env_file, write_env_file
from telegram_trade_listener import _prepare_session_path
from trader_control import TraderProcessManager


class FakeProcess:
    pid = 4321

    def __init__(self) -> None:
        self.return_code = None
        self.stdout = []

    def poll(self):
        return self.return_code

    def terminate(self):
        self.return_code = 0

    def wait(self, timeout=None):
        return self.return_code


class TraderControlTests(unittest.TestCase):
    def test_env_updates_preserve_comments_and_existing_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("# keep this\nTELEGRAM_CHAT=old\nOTHER=value\n", encoding="utf-8")
            write_env_file(path, {"TELEGRAM_CHAT": "new", "FYERS_QUANTITY": "65"})
            values = read_env_file(path)
            self.assertEqual(values["TELEGRAM_CHAT"], "new")
            self.assertEqual(values["FYERS_QUANTITY"], "65")
            self.assertIn("# keep this", path.read_text(encoding="utf-8"))

    def test_windows_normalizes_shell_home_session_paths(self) -> None:
        normalized = normalize_session_value('"$HOME\\.telegram\\trade_listener"')
        self.assertNotIn("$HOME", normalized)
        self.assertNotIn('"', normalized)
        self.assertTrue(normalized.endswith(".telegram\\trade_listener"))
        prepared = _prepare_session_path('"$HOME\\.telegram\\trade_listener"')
        self.assertEqual(prepared, normalized)

    def test_start_passes_sizing_and_mock_mode_to_live_trader(self) -> None:
        manager = TraderProcessManager(Path.cwd())
        process = FakeProcess()
        with patch.dict(os.environ, {"TELEGRAM_API_ID": "1", "TELEGRAM_API_HASH": "hash"}), patch(
            "trader_control.subprocess.Popen", return_value=process
        ) as popen:
            status = manager.start(
                quantity=150,
                lot_size=25,
                telegram_chat="-100123",
                telegram_session="/tmp/session",
                mock_market=True,
            )

        command = popen.call_args.args[0]
        self.assertEqual(command[command.index("--quantity") + 1], "150")
        self.assertEqual(command[command.index("--lot-size") + 1], "25")
        self.assertIn("--mock-market", command)
        self.assertIn("-u", command)
        self.assertEqual(status["pid"], 4321)
        self.assertTrue(status["running"])
        self.assertEqual(manager.stop()["return_code"], 0)

    def test_invalid_sizing_is_rejected(self) -> None:
        manager = TraderProcessManager(Path.cwd())
        with self.assertRaisesRegex(ValueError, "multiple"):
            manager.update_settings(101, 10)


if __name__ == "__main__":
    unittest.main()
