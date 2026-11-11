"""Offline replay of user-supplied message bodies, never a Telegram/broker connection."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from live_trader import telegram_call_to_signal
from telegram_trade_listener import TradeCallAssembler, TradeCallMessage, _process_message
from trade_lifecycle import TradeCommand
from trading import parse_trade_call


class FinobullsDayReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_four_calls_emit_once_with_exact_levels_despite_progress_messages(self):
        bodies = json.loads((Path(__file__).parent / "fixtures" / "finobulls_2026_11_11.json").read_text(encoding="utf-8"))
        assembler, seen, emitted = TradeCallAssembler(), set(), []
        entry_id = None
        for message_id, body in enumerate(bodies, 1):
            if body.startswith(("BUY ABOVE", "BUYING RANGE")):
                entry_id = message_id
            message = SimpleNamespace(id=message_id, raw_text=body, reply_to_msg_id=entry_id,
                                      chat_id=-100123, date=None)
            # Event delivery plus polling may return the same message twice.
            await _process_message(message, assembler, seen, emitted.append)
            await _process_message(message, assembler, seen, emitted.append)
        calls = [event for event in emitted if isinstance(event, TradeCallMessage)]
        self.assertEqual(len(calls), 4)
        expected = [
            ("NIFTY 25600 CE", 164, None, (174, 186, 202), 152),
            ("NIFTY 25600 CE", None, (140, 147), (157, 169, 188), 130),
            ("SENSEX 82000 PE", 330, None, (350, 370, 425), 300),
            ("SENSEX 82100 PE", 380, None, (400, 422, 460), 350),
        ]
        for call, values in zip(calls, expected):
            self.assertEqual((call.instrument_name, call.buy_above, call.trigger_range, call.targets, call.stoploss), values)
            # Also prove each emitted call survives the engine's stricter parser.
            engine_call = parse_trade_call(telegram_call_to_signal(call))
            self.assertEqual(engine_call.instrument_name, call.instrument_name)
            self.assertEqual(engine_call.targets, call.targets)
        controls = [event for event in emitted if isinstance(event, TradeCommand)]
        self.assertEqual([event.action for event in controls], ["EXIT", "EXIT"])
