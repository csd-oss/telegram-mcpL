"""TELEGRAM_DEFAULT_PARSE_MODE: default formatting for send_message, reply_to_message,
edit_message and the captions of send_file / send_album."""

from unittest.mock import MagicMock

import pytest
import telethon

from telegram_mcp import runtime
from telegram_mcp.tools import media, messages


class _DummyClient:
    def __init__(self, fail_first=False):
        self.calls = []
        self.fail_first = fail_first

    async def send_message(self, entity, message, **kwargs):
        self.calls.append({"entity": entity, "message": message, **kwargs})
        if self.fail_first and len(self.calls) == 1:
            raise ValueError("ENTITY_BOUNDS_INVALID")
        result = MagicMock()
        result.id = 7
        return result


def _patch(monkeypatch, cl):
    async def fake_resolve(chat_id, cl=None):
        return "entity"

    monkeypatch.setattr(messages, "get_client", lambda account=None: cl)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "is_chat_allowlist_enabled", lambda: False)


@pytest.mark.asyncio
async def test_unset_env_keeps_plain(monkeypatch):
    monkeypatch.delenv("TELEGRAM_DEFAULT_PARSE_MODE", raising=False)
    cl = _DummyClient()
    _patch(monkeypatch, cl)
    await messages.send_message(1, "**hi**")
    assert cl.calls[0]["parse_mode"] is None


@pytest.mark.asyncio
async def test_env_default_applies(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _DummyClient()
    _patch(monkeypatch, cl)
    await messages.send_message(1, "**hi**")
    assert cl.calls[0]["parse_mode"] == "md"


@pytest.mark.asyncio
async def test_explicit_parse_mode_wins(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _DummyClient()
    _patch(monkeypatch, cl)
    await messages.send_message(1, "<b>hi</b>", parse_mode="html")
    assert cl.calls[0]["parse_mode"] == "html"


@pytest.mark.asyncio
async def test_defaulted_mode_falls_back_to_plain(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _DummyClient(fail_first=True)
    _patch(monkeypatch, cl)
    result = await messages.send_message(1, "odd * text")
    assert result.startswith("Message sent")
    assert [c["parse_mode"] for c in cl.calls] == ["md", None]


@pytest.mark.asyncio
async def test_reply_uses_default(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _DummyClient()
    _patch(monkeypatch, cl)
    await messages.reply_to_message(1, 5, "**ok**")
    assert cl.calls[0]["parse_mode"] == "md"
    assert cl.calls[0]["reply_to"] == 5


# --- edit_message ------------------------------------------------------------


class _EditClient:
    def __init__(self, reject_first=None):
        self.calls = []
        self.reject_first = reject_first

    async def edit_message(self, entity, message_id, text, **kwargs):
        self.calls.append({"message_id": message_id, "text": text, **kwargs})
        if self.reject_first is not None and len(self.calls) == 1:
            raise self.reject_first


def _bad_request(message="ENTITY_BOUNDS_INVALID"):
    return telethon.errors.BadRequestError(request=None, message=message)


@pytest.mark.asyncio
async def test_edit_unset_env_omits_parse_mode(monkeypatch):
    monkeypatch.delenv("TELEGRAM_DEFAULT_PARSE_MODE", raising=False)
    cl = _EditClient()
    _patch(monkeypatch, cl)
    assert await messages.edit_message(1, 5, "**hi**") == "Message 5 edited."
    assert "parse_mode" not in cl.calls[0]  # Telethon's own default, as before


@pytest.mark.asyncio
async def test_edit_env_default_applies(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "html")
    cl = _EditClient()
    _patch(monkeypatch, cl)
    await messages.edit_message(1, 5, "<b>hi</b>")
    assert cl.calls[0]["parse_mode"] == "html"


@pytest.mark.asyncio
async def test_edit_explicit_parse_mode_wins(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "html")
    cl = _EditClient()
    _patch(monkeypatch, cl)
    await messages.edit_message(1, 5, "**hi**", parse_mode="md")
    assert cl.calls[0]["parse_mode"] == "md"


@pytest.mark.asyncio
async def test_edit_defaulted_mode_falls_back_to_plain(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _EditClient(reject_first=_bad_request())
    _patch(monkeypatch, cl)
    assert await messages.edit_message(1, 5, "odd * text") == "Message 5 edited."
    assert [c.get("parse_mode", "omitted") for c in cl.calls] == ["md", None]


@pytest.mark.asyncio
async def test_edit_explicit_mode_is_not_retried(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _EditClient(reject_first=_bad_request())
    _patch(monkeypatch, cl)
    result = await messages.edit_message(1, 5, "odd * text", parse_mode="md")
    assert len(cl.calls) == 1
    assert "edited" not in result


@pytest.mark.asyncio
async def test_edit_not_modified_is_not_retried(monkeypatch):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = _EditClient(reject_first=telethon.errors.MessageNotModifiedError(request=None))
    _patch(monkeypatch, cl)
    result = await messages.edit_message(1, 5, "same")
    assert len(cl.calls) == 1
    assert result == messages.MESSAGE_NOT_MODIFIED.format(message_id=5)


# --- send_file / send_album captions -----------------------------------------


class _FileClient:
    def __init__(self, reject_first=False):
        self.calls = []
        self.reject_first = reject_first

    async def send_file(self, entity, file, **kwargs):
        self.calls.append({"file": file, **kwargs})
        if self.reject_first and len(self.calls) == 1:
            raise _bad_request()
        result = MagicMock()
        result.id = 9
        return result


@pytest.fixture
def file_client(tmp_path, monkeypatch):
    root = (tmp_path / "root").resolve()
    root.mkdir()
    for name in ("doc.txt", "one.txt", "two.txt"):
        (root / name).write_bytes(b"text")
    monkeypatch.setattr(runtime, "SERVER_ALLOWED_ROOTS", [root])

    def make(reject_first=False):
        cl = _FileClient(reject_first)
        monkeypatch.setattr(media, "get_client", lambda account=None: cl)

        async def fake_resolve(chat_id, cl=None):
            return "entity"

        monkeypatch.setattr(media, "resolve_entity", fake_resolve)
        return cl

    return make


@pytest.mark.asyncio
async def test_caption_unset_env_omits_parse_mode(monkeypatch, file_client):
    monkeypatch.delenv("TELEGRAM_DEFAULT_PARSE_MODE", raising=False)
    cl = file_client()
    result = await media.send_file(1, "doc.txt", caption="**hi**")
    assert result.startswith("File sent")
    assert "parse_mode" not in cl.calls[0]


@pytest.mark.asyncio
async def test_caption_env_default_applies(monkeypatch, file_client):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = file_client()
    await media.send_file(1, "doc.txt", caption="**hi**")
    assert cl.calls[0]["parse_mode"] == "md"


@pytest.mark.asyncio
async def test_no_caption_no_parse_mode(monkeypatch, file_client):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = file_client()
    await media.send_file(1, "doc.txt")
    assert "parse_mode" not in cl.calls[0]


@pytest.mark.asyncio
async def test_caption_explicit_parse_mode_wins(monkeypatch, file_client):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = file_client()
    await media.send_file(1, "doc.txt", caption="<b>hi</b>", parse_mode="html")
    assert cl.calls[0]["parse_mode"] == "html"


@pytest.mark.asyncio
async def test_caption_defaulted_mode_falls_back_to_plain(monkeypatch, file_client):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = file_client(reject_first=True)
    result = await media.send_file(1, "doc.txt", caption="odd * text")
    assert result.startswith("File sent")
    assert [c["parse_mode"] for c in cl.calls] == ["md", None]


@pytest.mark.asyncio
async def test_album_caption_uses_default(monkeypatch, file_client):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = file_client()
    result = await media.send_album(1, ["one.txt", "two.txt"], caption="**pair**")
    assert result.startswith("Album of 2 files sent")
    assert cl.calls[0]["parse_mode"] == "md"


@pytest.mark.asyncio
async def test_album_through_send_file_falls_back_to_plain(monkeypatch, file_client):
    monkeypatch.setenv("TELEGRAM_DEFAULT_PARSE_MODE", "md")
    cl = file_client(reject_first=True)
    result = await media.send_file(1, ["one.txt", "two.txt"], caption="odd * text")
    assert result.startswith("Album of 2 files sent")
    assert [c["parse_mode"] for c in cl.calls] == ["md", None]
