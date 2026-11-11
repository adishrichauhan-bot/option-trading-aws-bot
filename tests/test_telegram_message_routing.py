"""Regression coverage for initial-call chunks being mistaken for target updates."""

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


if __name__ == "__main__":
    unittest.main()
