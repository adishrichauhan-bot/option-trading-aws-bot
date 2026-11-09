"""Standalone Telegram trade-call listener and parser.

This module only reads Telegram messages and prints parsed trade calls as JSON.
It does not call Fyers, place orders, or import the trading engine.

Examples:
    python telegram_trade_listener.py --parse-file trade_call.txt
    python telegram_trade_listener.py --chat @my_channel --once --limit 20
    python telegram_trade_listener.py --chat @my_channel

The Telegram client is imported lazily, so the parser can be used without
installing Telethon. A Telegram user session is used rather than a bot token.
The first network run may ask for the phone number, login code, and 2FA password.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional

from trade_lifecycle import TradeCommand, parse_trade_command

try:
    from dotenv import load_dotenv
except ImportError:  # Optional for parser-only use.
    load_dotenv = None  # type: ignore[assignment]


DASHES = r"[-\u2013\u2014]"
NUMBER = r"\d+(?:\.\d+)?"
INSTRUMENT_RE = re.compile(
    rf"^(?P<instrument>(?:[A-Z][A-Z0-9_&.-]*)(?::[A-Z0-9_&.-]+)?\s+"
    rf"{NUMBER}\s+(?:CE|PE))\s*$",
    re.IGNORECASE,
)
SEPARATOR = rf"(?:[:]|{DASHES})"
RANGE_RE = re.compile(
    rf"^\s*(?:BUYING\s+RANGE|TRIGGER\s+RANGE)\s*{SEPARATOR}\s*"
    rf"(?P<low>{NUMBER})\s*{DASHES}\s*(?P<high>{NUMBER})\s*$",
    re.IGNORECASE,
)
BUY_ABOVE_RE = re.compile(
    rf"^\s*BUY\s+ABOVE\s*{SEPARATOR}\s*(?P<price>{NUMBER})\s*$",
    re.IGNORECASE,
)
TARGET_RE = re.compile(
    rf"^\s*TARGETS?\s*{SEPARATOR}\s*(?P<values>{NUMBER}(?:\s*,\s*{NUMBER}){{2}})\s*$",
    re.IGNORECASE,
)
STOPLOSS_RE = re.compile(
    rf"^\s*(?:SL|STOP\s*-?\s*LOSS|STOPLOSS)\s*{SEPARATOR}\s*(?P<value>{NUMBER})\s*$",
    re.IGNORECASE,
)
ANNOUNCEMENT_RE = re.compile(r"^\s*Trade\s+Upcoming\s+Be\s+Ready\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class TradeCallMessage:
    """A validated trade call extracted from a Telegram message."""

    instrument_name: str
    trigger_range: tuple[float, float] | None
    targets: tuple[float, float, float]
    stoploss: float
    raw_text: str
    buy_above: float | None = None
    message_id: Optional[int] = None
    chat_id: Optional[int] = None
    message_date: Optional[str] = None
    source_message_ids: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        if self.trigger_range is not None:
            value["trigger_range"] = list(self.trigger_range)
        value["targets"] = list(self.targets)
        return value


class TradeCallAssembler:
    """Reconstruct a trade call sent as multiple Telegram messages."""

    def __init__(self, max_messages: int = 30) -> None:
        self._chunks: list[tuple[int, str]] = []
        self._max_messages = max_messages

    def append(self, message_id: int, text: str) -> Optional[TradeCallMessage]:
        self._chunks.append((message_id, text))
        self._chunks.sort(key=lambda item: item[0])
        self._chunks = self._chunks[-self._max_messages :]

        # Try both newline joining (one field per Telegram message) and
        # direct joining (a message split in the middle of a word/number).
        for separator in ("\n", " ", ""):
            combined = separator.join(chunk for _, chunk in self._chunks)
            candidates = [combined]
            if separator == "":
                # Add boundaries when a provider split directly between
                # fields, e.g. "BUYING" + " RANGE - 160 - 167".
                with_announcement_boundary = re.sub(
                    r"(TRADE\s*UPCOMING\s*BE\s*READY)",
                    r"\n\1\n",
                    combined,
                    flags=re.IGNORECASE,
                )
                candidates.append(
                    re.sub(
                        r"(BUYING\s*RANGE|TRIGGER\s*RANGE|BUY\s*ABOVE|TARGETS?|STOP\s*-?\s*LOSS|STOPLOSS|SL)",
                        r"\n\1",
                        with_announcement_boundary,
                        flags=re.IGNORECASE,
                    )
                )
            for candidate in candidates:
                call = parse_trade_call(candidate)
                if call is not None:
                    call = replace(call, source_message_ids=tuple(item[0] for item in self._chunks))
                    self._chunks.clear()
                    return call
        return None

    @property
    def has_pending(self) -> bool:
        return bool(self._chunks)

    def clear(self) -> None:
        self._chunks.clear()


def _clean_lines(text: str) -> list[str]:
    cleaned_lines = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        # Remove emoji, zero-width characters, and other decorative symbols,
        # while preserving letters, numbers, spaces, commas, and separators.
        line = "".join(
            " " if unicodedata.category(char).startswith("C") else char
            for char in unicodedata.normalize("NFKC", raw_line)
            if not unicodedata.category(char).startswith(("So", "Sk"))
        )
        line = re.sub(r"\s+", " ", line).strip(" \t*_~|>")
        if line:
            cleaned_lines.append(line)
    return cleaned_lines


def _find_call_lines(lines: list[str]) -> Optional[tuple[str, str, str, str]]:
    """Find the four call fields even when Telegram adds quoted prefixes.

    Telegram messages from the signal channel can contain a channel header or
    a quoted copy of an earlier message before the actual field, for example:
    ``Finobulls PREMIUM BUYING RANGE - 132 - 138``.  Extract the field from
    inside the line instead of requiring the whole line to match exactly.
    """

    instrument_search = re.compile(
        rf"(?P<instrument>(?:[A-Z][A-Z0-9_&.-]*)(?::[A-Z0-9_&.-]+)?\s+"
        rf"{NUMBER}\s+(?:CE|PE))\b",
        re.IGNORECASE,
    )
    range_search = re.compile(
        rf"(?:BUYING\s+RANGE|TRIGGER\s+RANGE)\s*{SEPARATOR}\s*"
        rf"(?P<low>{NUMBER})\s*(?:{DASHES}|TO)\s*(?P<high>{NUMBER})",
        re.IGNORECASE,
    )
    buy_above_search = re.compile(
        rf"BUY\s+ABOVE\s*{SEPARATOR}\s*(?P<price>{NUMBER})",
        re.IGNORECASE,
    )
    target_search = re.compile(
        rf"TARGETS?\s*{SEPARATOR}\s*"
        rf"(?P<values>{NUMBER}\s*,\s*{NUMBER}\s*,\s*{NUMBER})",
        re.IGNORECASE,
    )
    stoploss_search = re.compile(
        rf"(?:SL|STOP\s*-?\s*LOSS|STOPLOSS)\s*{SEPARATOR}\s*"
        rf"(?P<value>{NUMBER})",
        re.IGNORECASE,
    )

    for instrument_index, line in enumerate(lines):
        instrument_match = instrument_search.search(line)
        if instrument_match is None:
            continue

        range_index = next(
            (
                index
                for index in range(instrument_index, len(lines))
                if (
                    range_search.search(lines[index]) is not None
                    or buy_above_search.search(lines[index]) is not None
                )
            ),
            None,
        )
        if range_index is None:
            continue
        target_index = next(
            (
                index
                for index in range(range_index, len(lines))
                if target_search.search(lines[index]) is not None
            ),
            None,
        )
        if target_index is None:
            continue
        stoploss_index = next(
            (
                index
                for index in range(target_index, len(lines))
                if stoploss_search.search(lines[index]) is not None
            ),
            None,
        )
        if stoploss_index is not None:
            range_match = range_search.search(lines[range_index])
            target_match = target_search.search(lines[target_index])
            stoploss_match = stoploss_search.search(lines[stoploss_index])
            assert target_match is not None
            assert stoploss_match is not None
            buy_above_match = buy_above_search.search(lines[range_index])
            if buy_above_match is not None:
                entry_line = f"BUY ABOVE - {buy_above_match.group('price')}"
            else:
                assert range_match is not None
                entry_line = (
                    f"TRIGGER RANGE - {range_match.group('low')} - "
                    f"{range_match.group('high')}"
                )
            return (
                instrument_match.group("instrument"),
                entry_line,
                f"TARGET - {target_match.group('values')}",
                f"SL - {stoploss_match.group('value')}",
            )
    return None


def parse_trade_call(text: str) -> Optional[TradeCallMessage]:
    """Parse one trade call, returning ``None`` for unrelated messages.

    The parser accepts ``BUYING RANGE`` and ``TRIGGER RANGE`` as equivalent,
    and accepts ``SL``, ``STOP LOSS``, or ``STOPLOSS`` for the final line.
    """

    raw_text = text.strip()
    if not raw_text:
        return None

    lines = _clean_lines(raw_text)
    call_lines = _find_call_lines(lines)
    if call_lines is None:
        return None

    instrument_match = INSTRUMENT_RE.fullmatch(call_lines[0])
    range_match = RANGE_RE.fullmatch(call_lines[1])
    buy_above_match = BUY_ABOVE_RE.fullmatch(call_lines[1])
    target_match = TARGET_RE.fullmatch(call_lines[2])
    stoploss_match = STOPLOSS_RE.fullmatch(call_lines[3])
    if not all((instrument_match, target_match, stoploss_match)) or not (
        range_match or buy_above_match
    ):
        return None

    instrument = instrument_match.group("instrument").upper()
    targets = tuple(float(value) for value in target_match.group("values").split(","))
    stoploss = float(stoploss_match.group("value"))

    if not all(first < second for first, second in zip(targets, targets[1:])):
        raise ValueError("targets must be strictly increasing")

    trigger_range: tuple[float, float] | None
    buy_above: float | None
    if range_match:
        trigger_low = float(range_match.group("low"))
        trigger_high = float(range_match.group("high"))
        if trigger_low > trigger_high:
            raise ValueError("trigger range lower bound must not exceed upper bound")
        if targets[0] <= trigger_high:
            raise ValueError("the first target must be above the trigger range")
        if stoploss >= trigger_low:
            raise ValueError("stop-loss must be below the trigger range")
        trigger_range = (trigger_low, trigger_high)
        buy_above = None
    else:
        assert buy_above_match is not None
        entry = float(buy_above_match.group("price"))
        if targets[0] <= entry:
            raise ValueError("the first target must be above BUY ABOVE price")
        if stoploss >= entry:
            raise ValueError("stop-loss must be below BUY ABOVE price")
        trigger_range = None
        buy_above = entry

    return TradeCallMessage(
        instrument_name=instrument,
        trigger_range=trigger_range,
        buy_above=buy_above,
        targets=targets,  # type: ignore[arg-type]
        stoploss=stoploss,
        raw_text=raw_text,
    )


def _json_line(call: TradeCallMessage) -> str:
    return json.dumps(call.to_dict(), separators=(",", ":"), sort_keys=True)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _prepare_session_path(session: str) -> str:
    """Create the directory needed by Telethon's SQLite session file."""

    session_path = Path(session).expanduser()
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return str(session_path)


def _phone_session_name(phone: str | None) -> str:
    """Return a stable, filesystem-safe default session name for a phone."""

    digits = re.sub(r"\D", "", phone or "")
    return f"trade_listener_{digits}" if digits else "trade_listener"


def _default_session() -> str:
    """Use a different Telethon session for each configured Telegram account."""

    configured = os.getenv("TELEGRAM_SESSION")
    if configured:
        return configured
    return str(Path("~/.telegram") / _phone_session_name(os.getenv("TELEGRAM_PHONE")))


def _phone_digits(phone: str | None) -> str:
    return re.sub(r"\D", "", phone or "")


def _build_client(session: str):
    try:
        from telethon import TelegramClient
    except ImportError as exc:
        raise RuntimeError(
            "Telethon is required for Telegram access. Install it with: "
            "python -m pip install telethon"
        ) from exc

    try:
        api_id = int(_required_env("TELEGRAM_API_ID"))
    except ValueError as exc:
        raise RuntimeError("TELEGRAM_API_ID must be an integer") from exc
    api_hash = _required_env("TELEGRAM_API_HASH")
    phone = os.getenv("TELEGRAM_PHONE") or None
    return TelegramClient(_prepare_session_path(session), api_id, api_hash), phone


async def _resolve_chat(client: Any, chat_ref: str) -> Any:
    """Resolve a username/ID, or a visible Telegram dialog title."""

    reference = chat_ref.strip()
    if re.fullmatch(r"-?\d+", reference):
        numeric_id = int(reference)
        # Dialogs contain the access hash needed for private users/chats.
        async for dialog in client.iter_dialogs():
            if dialog.id == numeric_id:
                return dialog.entity
        try:
            return await client.get_entity(numeric_id)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Telegram numeric chat ID {numeric_id} is not available in this account's dialogs. "
                "Run --list-dialogs using the same session and use the exact ID shown there."
            ) from exc

    try:
        return await client.get_entity(reference)
    except (ValueError, TypeError):
        title = reference.lstrip("@").strip()
        async for dialog in client.iter_dialogs():
            if dialog.name and dialog.name.casefold() == title.casefold():
                return dialog.entity
        raise ValueError(
            f"Telegram chat {chat_ref!r} was not found. Use its exact @username, "
            "numeric chat ID, or visible dialog name."
        )


async def _read_recent(
    client: Any,
    entity: Any,
    limit: int,
    on_trade_call: Callable[[TradeCallMessage], None] | None = None,
    show_all: bool = False,
    message_id_range: tuple[int, int] | None = None,
) -> None:
    if message_id_range is None:
        messages = [message async for message in client.iter_messages(entity, limit=limit)]
    else:
        first_id, last_id = message_id_range
        messages = [
            message
            async for message in client.iter_messages(
                entity,
                min_id=first_id - 1,
                max_id=last_id + 1,
                reverse=True,
            )
            if first_id <= message.id <= last_id
        ]
    assembler = TradeCallAssembler()
    for message in reversed(messages):
        if show_all:
            print(
                json.dumps(
                    {
                        "message_id": message.id,
                        "chat_id": getattr(message, "chat_id", None),
                        "message_date": message.date.isoformat() if message.date else None,
                        "raw_text": message.raw_text or "",
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                flush=True,
            )
        await _process_message(message, assembler, set(), on_trade_call)


async def _process_message(
    message: Any,
    assembler: TradeCallAssembler,
    seen_ids: set[int],
    on_trade_call: Callable[[TradeCallMessage | TradeCommand], None] | None = None,
) -> None:
    """Append one message chunk and print only completed trade calls."""

    message_id = message.id
    if message_id in seen_ids:
        return
    seen_ids.add(message_id)

    raw_text = message.raw_text or ""
    reply_to = getattr(message, "reply_to_msg_id", None)
    if reply_to is None:
        reply = getattr(message, "reply_to", None)
        reply_to = getattr(reply, "reply_to_msg_id", None)

    # Control messages are emitted immediately. Standalone TARGET/SL lines
    # are controls only after an initial call has already been completed;
    # while an initial call is being assembled they remain call chunks.
    command = parse_trade_command(
        raw_text, message_id=message_id, reply_to_message_id=reply_to
    )
    if command is None and not assembler.has_pending:
        standalone = re.search(r"\b(?:TARGETS?|SL|STOP\s*-?\s*LOSS)\b", raw_text, re.IGNORECASE)
        if standalone:
            command = parse_trade_command(
                "MODIFY " + raw_text,
                message_id=message_id,
                reply_to_message_id=reply_to,
            )
    if command is not None:
        print("Trade control command received.", flush=True)
        print(json.dumps(asdict(command), separators=(",", ":"), default=str), flush=True)
        if on_trade_call is not None:
            on_trade_call(command)
        return

    print(f"Received message id={message_id}; appending chunk...", flush=True)
    call = assembler.append(message_id, raw_text)
    if call is None:
        print("Trade call incomplete; waiting for the next chunk.", flush=True)
        return

    call = TradeCallMessage(
        **{
            **call.__dict__,
            "message_id": message_id,
            "chat_id": getattr(message, "chat_id", None),
            "message_date": message.date.isoformat() if message.date else None,
        }
    )
    print("Trade call completed.", flush=True)
    print(_json_line(call), flush=True)
    if on_trade_call is not None:
        on_trade_call(call)


async def _listen(
    chat_ref: str,
    session: str,
    once: bool,
    limit: int,
    on_trade_call: Callable[[TradeCallMessage | TradeCommand], None] | None = None,
    stop_event: Any | None = None,
    show_all: bool = False,
    message_id_range: tuple[int, int] | None = None,
) -> None:
    from telethon import events
    from telethon.utils import get_peer_id

    client, phone = _build_client(session)
    async with client:
        if not await client.is_user_authorized():
            phone = phone or input("Telegram phone number with country code: ").strip()
            if not phone:
                raise RuntimeError(
                    "A phone number is required for the first Telegram login. "
                    "Set TELEGRAM_PHONE or enter it when prompted."
                )
        await client.start(phone=phone)
        account = await client.get_me()
        configured_phone = _phone_digits(phone)
        logged_in_phone = _phone_digits(getattr(account, "phone", None))
        if configured_phone and logged_in_phone and configured_phone != logged_in_phone:
            session_file = f"{_prepare_session_path(session)}.session"
            raise RuntimeError(
                f"Telegram session belongs to +{logged_in_phone}, not +{configured_phone}. "
                "Set TELEGRAM_SESSION to a new path (or remove the stale session file) and log in again. "
                f"Current session file: {session_file}"
            )
        entity = await _resolve_chat(client, chat_ref)
        if once:
            await _read_recent(
                client,
                entity,
                limit,
                on_trade_call,
                show_all,
                message_id_range,
            )
            return

        seen_ids: set[int] = set()
        assembler = TradeCallAssembler()
        latest_messages = await client.get_messages(entity, limit=1)
        latest_id = latest_messages[0].id if latest_messages else 0
        account_name = " ".join(
            part for part in (getattr(account, "first_name", None), getattr(account, "last_name", None)) if part
        ) or getattr(account, "username", None) or str(getattr(account, "id", "unknown"))

        target_chat_id = get_peer_id(entity)

        @client.on(events.NewMessage())
        async def handle_message(event: Any) -> None:
            if event.chat_id != target_chat_id:
                return
            await _process_message(event.message, assembler, seen_ids, on_trade_call)

        print(
            f"Logged in as {account_name}; target chat={chat_ref}; chat_id={target_chat_id}. "
            f"Polling every 2 seconds; last message id={latest_id}. Press Ctrl+C to stop.",
            flush=True,
        )

        while True:
            if stop_event is not None and stop_event.is_set():
                return
            # Fetch the newest messages and compare locally. This is more
            # reliable than combining min_id with reverse pagination across
            # different Telegram chat types.
            recent_messages = await client.get_messages(entity, limit=20)
            for message in reversed(recent_messages):
                if message.id <= latest_id:
                    continue
                latest_id = max(latest_id, message.id)
                await _process_message(message, assembler, seen_ids, on_trade_call)
            await asyncio.sleep(2)


async def listen_for_trade_calls(
    chat_ref: str,
    session: str,
    on_trade_call: Callable[[TradeCallMessage | TradeCommand], None],
    stop_event: Any | None = None,
) -> None:
    """Continuously deliver completed Telegram trade calls to a callback."""

    await _listen(
        chat_ref,
        session,
        once=False,
        limit=50,
        on_trade_call=on_trade_call,
        stop_event=stop_event,
    )


async def _list_dialogs(session: str) -> None:
    """Print dialog titles and IDs to identify the correct Telegram chat."""

    client, phone = _build_client(session)
    async with client:
        if not await client.is_user_authorized():
            phone = phone or input("Telegram phone number with country code: ").strip()
            if not phone:
                raise RuntimeError("A phone number is required for the first Telegram login")
        await client.start(phone=phone)
        account = await client.get_me()
        name = " ".join(
            part for part in (getattr(account, "first_name", None), getattr(account, "last_name", None)) if part
        ) or getattr(account, "username", None) or str(getattr(account, "id", "unknown"))
        print(f"Logged in as: {name}")
        async for dialog in client.iter_dialogs(limit=200):
            print(f"{dialog.id}\t{dialog.name}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chat", default=os.getenv("TELEGRAM_CHAT"), help="Telegram username, ID, or 'me'")
    parser.add_argument(
        "--session",
        default=_default_session(),
        help="Telethon session path; keep the generated .session file private",
    )
    parser.add_argument("--once", action="store_true", help="Parse recent messages and exit")
    parser.add_argument("--limit", type=int, default=50, help="Number of recent messages for --once")
    parser.add_argument(
        "--show-all",
        action="store_true",
        help="Print every message when used with --once, including unrelated messages",
    )
    parser.add_argument(
        "--message-id-range",
        metavar="START-END",
        help="Read an inclusive message-ID range, for example 100-150",
    )
    parser.add_argument("--list-dialogs", action="store_true", help="List available chats and exit")
    parser.add_argument("--parse-file", type=Path, help="Parse a local message file without Telegram")
    return parser.parse_args()


def main() -> None:
    if load_dotenv is not None:
        load_dotenv()
    args = _parse_args()

    if args.list_dialogs:
        session = os.path.expanduser(args.session)
        try:
            asyncio.run(_list_dialogs(session))
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
        return

    if args.parse_file:
        call = parse_trade_call(args.parse_file.read_text(encoding="utf-8"))
        if call is None:
            raise SystemExit("file does not contain a recognized trade call")
        print(_json_line(call))
        return

    if not args.chat:
        raise SystemExit("provide --chat or set TELEGRAM_CHAT")
    if args.limit < 1:
        raise SystemExit("--limit must be at least 1")

    message_id_range = None
    if args.message_id_range:
        match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", args.message_id_range)
        if not match:
            raise SystemExit("--message-id-range must use START-END, for example 100-150")
        first_id, last_id = (int(value) for value in match.groups())
        if first_id <= 0 or last_id < first_id:
            raise SystemExit("message ID range must be positive and ordered as START-END")
        if not args.once:
            raise SystemExit("--message-id-range requires --once")
        message_id_range = (first_id, last_id)

    session = os.path.expanduser(args.session)
    try:
        asyncio.run(
            _listen(
                args.chat,
                session,
                args.once,
                args.limit,
                show_all=args.show_all,
                message_id_range=message_id_range,
            )
        )
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
