import unittest
from pathlib import Path
from unittest.mock import patch

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
    def test_start_passes_sizing_and_mock_mode_to_live_trader(self) -> None:
        manager = TraderProcessManager(Path.cwd())
        process = FakeProcess()
        with patch("trader_control.subprocess.Popen", return_value=process) as popen:
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
        self.assertEqual(status["pid"], 4321)
        self.assertTrue(status["running"])
        self.assertEqual(manager.stop()["return_code"], 0)

    def test_invalid_sizing_is_rejected(self) -> None:
        manager = TraderProcessManager(Path.cwd())
        with self.assertRaisesRegex(ValueError, "multiple"):
            manager.update_settings(101, 10)


if __name__ == "__main__":
    unittest.main()
