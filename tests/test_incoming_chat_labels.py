"""Chat context stays visible in the text the agent actually reads."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from telethon import events, types

from telegram_mcp.mcpl.events import build_incoming_message
from tests.test_mcpl_event_tags import make_chat
from tests.test_mcpl_events import FakeEvent, FakeMessage, make_user


async def incoming(chat, sender, message):
    return await build_incoming_message(
        FakeEvent(message, chat, sender),
        account_label="default",
        self_id=99,
        self_username=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["group", "supergroup", "channel"])
@pytest.mark.parametrize("title", ["UNITE Frontend Deployment", "", "Команда 🚀\n[dev]"])
async def test_chat_title_or_id_precedes_author_without_changing_event(kind, title):
    chat = make_chat(kind)
    chat.title = title
    sender = make_user(42, first="Ruby", username="rbcnn")
    message = FakeMessage(id=815, text="ок")
    # Compare the entire event to its old rendering: only content may change.
    with patch("telegram_mcp.mcpl.events._build_speaker_header", return_value="old: "):
        before = await incoming(chat, sender, message)
    after = await incoming(chat, sender, message)
    label = {
        "UNITE Frontend Deployment": "UNITE Frontend Deployment",
        "": "100",
        "Команда 🚀\n[dev]": "Команда 🚀 (dev)",
    }[title]
    assert after["content"] == [
        {"type": "text", "text": f"[{label}] Ruby (@rbcnn) [message_id 815]: ок"}
    ]
    before.pop("content")
    after.pop("content")
    assert after == before


@pytest.mark.asyncio
async def test_only_dm_from_the_same_user_omits_chat_label():
    chat = make_user(42, first="Misha", username="magnumlink")
    message = FakeMessage(id=7431, text="ок")
    result = await incoming(chat, chat, message)
    assert result["content"][0]["text"] == "Misha (@magnumlink) [message_id 7431]: ок"
    other = await incoming(chat, make_user(7, first="Other"), message)
    assert other["content"][0]["text"] == "[42] Other [message_id 7431]: ок"


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved", [True, False])
async def test_reply_line_and_speaker_both_name_the_group(resolved):
    replied = FakeMessage(id=814, text="deploy?")
    replied.sender_id = 99
    replied.sender = make_user(99, first="Glassy")
    message = FakeMessage(
        id=815,
        text="ок",
        is_reply=True,
        reply_to=SimpleNamespace(reply_to_msg_id=814),
        replied=replied if resolved else None,
    )
    result = await incoming(make_chat("group"), make_user(42, first="Ruby"), message)
    target = 'Glassy [message_id 814]: "deploy?"' if resolved else "[message_id 814]"
    assert result["content"][0]["text"] == (
        f"↳ in reply to [Group] {target}\n[Group] Ruby [message_id 815]: ок"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["voice", "photo", "inline_photo"])
@pytest.mark.parametrize("caption", ["", "caption"])
async def test_media_keeps_chat_header_and_resource_ids(kind, caption):
    if kind == "voice":
        media = types.MessageMediaDocument(
            document=types.Document(
                id=1,
                access_hash=0,
                file_reference=b"",
                date=None,
                mime_type="audio/ogg",
                size=3,
                dc_id=1,
                attributes=[types.DocumentAttributeAudio(duration=1, voice=True)],
            )
        )
    else:
        media = types.MessageMediaPhoto()
    message = FakeMessage(id=815, text=caption, media=media)
    if kind == "inline_photo":

        async def download_media(**kwargs):
            return b"jpeg"

        message.download_media = download_media
    result = await incoming(make_chat("supergroup"), make_user(42, first="Ruby"), message)
    assert result["content"][0]["text"].startswith("[Channel] Ruby [message_id 815]")
    if kind != "inline_photo":
        assert any(f"[{kind}, message_id 815]" in b.get("text", "") for b in result["content"])
        assert result["content"][-1] == {
            "type": "resource",
            "uri": "telegram://message/default/100/815",
        }


@pytest.mark.asyncio
async def test_edited_event_uses_the_same_chat_header():
    message = types.Message(id=815, peer_id=types.PeerChat(100), message="edited")
    event = events.MessageEdited.Event(message)
    chat, sender = make_chat("group"), make_user(42, first="Ruby")
    event.get_chat = AsyncMock(return_value=chat)
    event.get_sender = AsyncMock(return_value=sender)
    result = await build_incoming_message(
        event, account_label="default", self_id=99, self_username=None
    )
    assert result["content"][0]["text"] == "[Group] Ruby [message_id 815]: edited"
