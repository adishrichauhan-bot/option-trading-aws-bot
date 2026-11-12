import unittest
from types import SimpleNamespace

from live_trader import telegram_call_to_signal
from telegram_trade_listener import TradeCallAssembler, TradeCallMessage, _process_message
from trade_lifecycle import TradeCommand
from trading import parse_trade_call


class Nov12SignalTests(unittest.IsolatedAsyncioTestCase):
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
