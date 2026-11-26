"""Regression coverage for initial-call chunks being mistaken for target updates."""

import json
from pathlib import Path
from live_trader import telegram_call_to_signal
from trading import parse_trade_call

import unittest
from types import SimpleNamespace

from telegram_trade_listener import TradeCallAssembler, TradeCallMessage, _process_message
from trade_lifecycle import TradeCommand, parse_trade_command


INITIAL = "NIFTY 25000 CE\nTRIGGER RANGE - 160 - 167"
# Exact targets/SL text from server message 55091; instrument/entry above are synthetic.
TARGET_CHUNK = "TARGET - 174, 186, 202\n\nSL - 152"


def message(message_id, text, reply_to=None):
    return SimpleNamespace(id=message_id, raw_text=text, reply_to_msg_id=reply_to,
                           chat_id=-100123, date=None)


class TelegramRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assembler = TradeCallAssembler()
        self.seen = set()
        self.received = []

    async def process(self, message_id, text, reply_to=None):
        await _process_message(message(message_id, text, reply_to), self.assembler,
                               self.seen, self.received.append)

    async def test_server_targets_sl_chunk_completes_pending_call(self):
        await self.process(55090, INITIAL)
        self.assertTrue(self.assembler.has_pending)
        await self.process(55091, TARGET_CHUNK, reply_to=55090)
        self.assertEqual(len(self.received), 1)
        call = self.received[0]
        self.assertIsInstance(call, TradeCallMessage)
        self.assertEqual(call.targets, (174, 186, 202))
        self.assertEqual(call.stoploss, 152)
        self.assertEqual(call.trigger_range, (160, 167))
        self.assertEqual(call.source_message_ids, (55090, 55091))
        self.assertFalse(self.assembler.has_pending)

    async def test_targets_and_sl_in_separate_chunks_complete_call(self):
        await self.process(1, INITIAL)
        await self.process(2, "TARGET - 174, 186, 202")
        self.assertEqual(self.received, [])
        await self.process(3, "SL - 152")
        self.assertIsInstance(self.received[0], TradeCallMessage)
        self.assertEqual(self.received[0].stoploss, 152)

    async def test_complete_single_message_is_a_call_not_a_control(self):
        await self.process(1, INITIAL + "\n" + TARGET_CHUNK)
        self.assertEqual(len(self.received), 1)
        self.assertIsInstance(self.received[0], TradeCallMessage)

    async def test_explicit_modify_during_assembly_is_still_a_control(self):
        await self.process(1, INITIAL)
        await self.process(2, "MODIFY TARGET - 180, 190, 210")
        self.assertIsInstance(self.received[0], TradeCommand)
        self.assertEqual(self.received[0].targets, (180, 190, 210))
        self.assertTrue(self.assembler.has_pending)

    async def test_standalone_targets_after_completed_call_remain_a_control(self):
        await self.process(1, INITIAL + "\n" + TARGET_CHUNK)
        await self.process(2, TARGET_CHUNK)
        self.assertIsInstance(self.received[0], TradeCallMessage)
        self.assertIsInstance(self.received[1], TradeCommand)
        self.assertEqual(self.received[1].action, "MODIFY_TARGETS")
        self.assertEqual(self.received[1].targets, (174, 186, 202))

    async def test_duplicate_final_chunk_does_not_emit_another_call_or_control(self):
        await self.process(1, INITIAL)
        await self.process(2, TARGET_CHUNK)
        await self.process(2, TARGET_CHUNK)
        self.assertEqual(len(self.received), 1)
        self.assertIsInstance(self.received[0], TradeCallMessage)


class TargetCommandTests(unittest.TestCase):
    def test_plain_target_field_is_not_an_explicit_modification(self):
        self.assertIsNone(parse_trade_command(TARGET_CHUNK))

    def test_explicit_target_update_does_not_include_stoploss(self):
        command = parse_trade_command("MODIFY " + TARGET_CHUNK)
        self.assertEqual(command.targets, (174, 186, 202))


class MessageAssemblyRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assembler, self.events, self.seen = TradeCallAssembler(), [], set()
        self.sequence = 0

    async def send(self, body):
        self.sequence += 1
        msg = SimpleNamespace(id=self.sequence, raw_text=body, date=None, chat_id=-100123, reply_to_msg_id=None)
        await _process_message(msg, self.assembler, self.seen, self.events.append)

    async def complete_pe(self):
        for body in ("Trade Upcoming Be Ready\n\nSENSEX 82400 PE", "BUYING RANGE - 350 - 360",
                     "Wait for this level", "TARGET - 380, 400, 435\n\nSL - 330"):
            await self.send(body)

    async def test_recovery_modify_entry_before_first_targets_replaces_old_entry(self):
        for body in ("RECOVERY TRADE ♻️\n\nTrade Upcoming Be Ready\n\nSENSEX 82100 CE",
                     "BUY ABOVE - 340", "MODIFY ENTRY\nBUYING RANGE - 270 - 280",
                     "TARGET - 300, 320, 355\n\nSL - 250"):
            await self.send(body)
        calls = [event for event in self.events if isinstance(event, TradeCallMessage)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].trigger_range, (270, 280))
        self.assertIsNone(calls[0].buy_above)
        self.assertEqual(calls[0].targets, (300, 320, 355))
        self.assertEqual(calls[0].stoploss, 250)
        self.assertEqual(parse_trade_call(telegram_call_to_signal(calls[0])).buy_above, 280)
        self.assertFalse(any(isinstance(event, TradeCommand) for event in self.events))

    async def test_split_reentry_waits_for_fresh_targets_and_sl_not_old_levels(self):
        await self.complete_pe()
        await self.send("350 TO 370")
        await self.send("SL hit - 330")
        await self.send("RE ENTRY - ABOVE - 355")
        self.assertEqual(len([event for event in self.events if isinstance(event, TradeCallMessage)]), 1)
        reentry = [event for event in self.events if isinstance(event, TradeCommand)][0]
        self.assertEqual(reentry.action, "REENTRY")
        self.assertTrue(reentry.awaiting_levels)
        await self.send("Activated at 355")
        await self.send("TARGET - 375, 400, 435\n\nSL - 335")
        calls = [event for event in self.events if isinstance(event, TradeCallMessage)]
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1].instrument_name, "SENSEX 82400 PE")
        self.assertEqual(calls[-1].buy_above, 355)
        self.assertEqual(calls[-1].targets, (375, 400, 435))
        self.assertEqual(calls[-1].stoploss, 335)
        parse_trade_call(telegram_call_to_signal(calls[-1]))

    async def test_new_instrument_does_not_inherit_incomplete_old_call(self):
        await self.send("Trade Upcoming Be Ready\n\nSENSEX 82100 CE\nBUY ABOVE - 340")
        await self.send("Trade Upcoming Be Ready\n\nSENSEX 82000 CE")
        await self.send("BUYING RANGE - 390 - 400")
        await self.send("TARGET - 421, 440, 475\nSL - 370")
        calls = [event for event in self.events if isinstance(event, TradeCallMessage)]
        self.assertEqual(calls[0].instrument_name, "SENSEX 82000 CE")
        self.assertEqual(calls[0].trigger_range, (390, 400))

    async def test_invalid_setup_does_not_kill_listener_and_next_setup_still_completes(self):
        await self.send("SENSEX 82100 CE\nBUY ABOVE - 340\nTARGET - 300, 320, 355\nSL - 250")
        self.assertEqual(self.events, [])
        await self.complete_pe()
        self.assertEqual(self.events[0].instrument_name, "SENSEX 82400 PE")

    async def test_reentry_without_known_instrument_does_not_invent_a_call(self):
        await self.send("RE ENTRY - ABOVE - 355")
        await self.send("TARGET - 375, 400, 435\nSL - 335")
        self.assertFalse(any(isinstance(event, TradeCallMessage) for event in self.events))

    async def test_primary_buy_above_and_range_with_progress(self):
        for body in ("GOOD MORNING TRADERS", "Trade Upcoming Be Ready\n\nSENSEX 82100 CE",
                     "BUY ABOVE - 340", "TARGET - 360, 380, 422\n\nSL - 310",
                     "340 TO 345", "340 TO 350", "340 TO 355",
                     "340 TO 360\nFirst Target Achieved 🎯\nBOOK QUANTITIES AS PER OUR TRADING RULES",
                     "340 TO 365", "340 TO 370", "Trade Upcoming Be Ready\n\nSENSEX 82000 CE",
                     "BUYING RANGE - 390 - 400", "TARGET - 421, 440, 475\n\nSL - 370",
                     "391 TO 400", "391 TO 410", "391 TO 415", "391 TO 426", "391 TO 430",
                     "391 TO 445\nSecond Target Achieved 🎯\nBOOK MAJORITY QUANTITIES NOW",
                     "391 TO 455", "391 TO 465"):
            await self.send(body)
        await self.complete_pe()
        calls = [event for event in self.events if isinstance(event, TradeCallMessage)]
        self.assertEqual([call.instrument_name for call in calls], ["SENSEX 82100 CE", "SENSEX 82000 CE", "SENSEX 82400 PE"])
        for call in calls:
            parse_trade_call(telegram_call_to_signal(call))


class ChannelMessageReplayTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
