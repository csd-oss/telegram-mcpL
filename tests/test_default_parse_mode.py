"""TELEGRAM_DEFAULT_PARSE_MODE: default formatting for send_message / reply_to_message."""

from unittest.mock import MagicMock

import pytest

from telegram_mcp.tools import messages


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
