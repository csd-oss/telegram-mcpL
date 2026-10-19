"""Poll results (get_poll_results) and vote events, for user sessions and bots."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telethon import types
from telethon.tl.functions.messages import GetPollVotesRequest

from telegram_mcp import polls, runtime
from telegram_mcp.mcpl.events import PollVoteBatcher, build_poll_vote_messages
from telegram_mcp.tools import messages
from tests.test_mcpl_events import make_user

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
A, B, C = b"\x00", b"\x01", b"\x02"


def make_poll(*, public=True, multiple=True):
    return types.Poll(
        id=77,
        hash=0,
        question=types.TextWithEntities("Lunch?", []),
        answers=[
            types.PollAnswer(types.TextWithEntities(text, []), option)
            for text, option in [("Pizza", A), ("Sushi", B), ("Salad", C)]
        ],
        closed=False,
        public_voters=public,
        multiple_choice=multiple,
    )


def counts(**voters):
    """PollResults with the given voter count per option (a, b, c)."""
    options = {"a": A, "b": B, "c": C}
    return types.PollResults(
        results=[types.PollAnswerVoters(options[k], voters=n) for k, n in voters.items()],
        total_voters=3,
    )


@pytest.fixture(autouse=True)
def clean_polls():
    polls.reset()
    yield
    polls.reset()


@pytest.fixture
def poll_chat(monkeypatch):
    poll = make_poll()
    message = types.Message(
        id=42,
        peer_id=types.PeerChat(123),
        date=NOW,
        message="",
        media=types.MessageMediaPoll(poll, counts(a=2, b=1, c=2)),
    )
    users = {7: make_user(7, first="Mykhailo", username="mk"), 8: make_user(8, first="Ann")}
    client = AsyncMock()
    client.get_messages.return_value = message
    client.is_bot.return_value = False
    entity = types.Chat(
        id=123,
        title="Team",
        photo=types.ChatPhotoEmpty(),
        participants_count=3,
        date=NOW,
        version=1,
    )
    client.get_entity.side_effect = lambda peer_id: {**users, -123: entity}[peer_id]
    monkeypatch.setattr(messages, "get_client", Mock(return_value=client))
    monkeypatch.setattr(messages, "resolve_entity", AsyncMock(return_value=entity))
    monkeypatch.setattr(runtime, "is_multi_mode", lambda: False)
    monkeypatch.setattr(runtime, "ALLOWED_CHAT_IDS", None)
    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    return SimpleNamespace(poll=poll, message=message, client=client, entity=entity, users=users)


async def get_results(**kwargs):
    result = await messages.get_poll_results(**{"chat_id": -123, "message_id": 42, **kwargs})
    return json.loads(result)


def vote(voter_id, *options):
    return types.UpdateMessagePollVote(77, types.PeerUser(voter_id), list(options), [], qts=1)


# ---------------------------------------------------------------------------
# get_poll_results
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_session_reads_counts_and_votes(poll_chat):
    pages = [
        types.messages.VotesList(
            count=3,
            votes=[types.MessagePeerVoteMultiple(types.PeerUser(7), [C, A], NOW)],
            chats=[],
            users=[poll_chat.users[7]],
            next_offset="next",
        ),
        types.messages.VotesList(
            count=3,
            votes=[types.MessagePeerVote(types.PeerUser(8), B, NOW)],
            chats=[],
            users=[poll_chat.users[8]],
        ),
    ]
    poll_chat.client.side_effect = pages
    payload = await get_results()

    requests = [call.args[0] for call in poll_chat.client.call_args_list]
    assert all(isinstance(r, GetPollVotesRequest) and r.id == 42 for r in requests)
    assert [r.offset for r in requests] == [None, "next"]
    [record] = payload["results"]
    assert record["chat_id"] == -123
    assert record["question"] == "Lunch?"
    assert record["total_voters"] == 3
    assert record["closed"] is False and record["public_votes"] is True
    assert [(o["text"], o["voters"], o["percent"]) for o in record["options"]] == [
        ("Pizza", 2, 66.7),
        ("Sushi", 1, 33.3),
        ("Salad", 2, 66.7),
    ]
    assert record["votes"] == [
        {
            "voter": {"id": 7, "name": "Mykhailo", "username": "mk"},
            "options": ["Pizza", "Salad"],
            "option_indices": [0, 2],
        },
        {"voter": {"id": 8, "name": "Ann"}, "options": ["Sushi"], "option_indices": [1]},
    ]


@pytest.mark.asyncio
async def test_bot_lists_the_votes_it_was_sent(poll_chat):
    poll_chat.client.is_bot.return_value = True
    polls.note_vote(vote(7, A, C))
    polls.note_vote(vote(8, B))
    polls.note_vote(vote(8))  # Ann retracted
    payload = await get_results()

    poll_chat.client.assert_not_awaited()  # no getPollVotes: bots can't call it
    [record] = payload["results"]
    assert record["votes"] == [
        {
            "voter": {"id": 7, "name": "Mykhailo", "username": "mk"},
            "options": ["Pizza", "Salad"],
            "option_indices": [0, 2],
        }
    ]
    assert payload["votes_note"] == "Votes received since the server started."


@pytest.mark.asyncio
async def test_bot_falls_back_to_counts_from_updates(poll_chat):
    poll_chat.client.is_bot.return_value = True
    poll_chat.message.media.results = types.PollResults(min=True)
    polls.note_update(types.UpdateMessagePoll(77, counts(a=1, b=0, c=0)))
    [record] = (await get_results())["results"]
    assert [o["voters"] for o in record["options"]] == [1, 0, 0]


@pytest.mark.asyncio
async def test_anonymous_poll_has_counts_but_no_votes(poll_chat):
    poll_chat.poll.public_voters = False
    [record] = (await get_results())["results"]
    assert "votes" not in record
    assert record["options"][0]["voters"] == 2
    poll_chat.client.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_a_poll(poll_chat):
    poll_chat.message.media = None
    result = await messages.get_poll_results(chat_id=-123, message_id=42)
    assert "does not contain a poll" in result


@pytest.mark.asyncio
async def test_chat_allowlist_blocks_reading(poll_chat, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ALLOWED_CHAT_IDS", "-456")
    result = await messages.get_poll_results(chat_id=-123, message_id=42)
    assert "restricted by privacy policy" in result
    poll_chat.client.get_messages.assert_not_awaited()


def test_tool_is_registered_read_only():
    tool = next(t for t in messages.mcp._tool_manager.list_tools() if t.name == "get_poll_results")
    assert tool.annotations.readOnlyHint


def test_created_poll_is_placed_by_its_server_id():
    sent = types.Message(
        id=42,
        peer_id=types.PeerChat(123),
        date=NOW,
        message="",
        media=types.MessageMediaPoll(make_poll(), types.PollResults()),
    )
    polls.note_sent(types.Updates([types.UpdateNewMessage(sent, 1, 1)], [], [], NOW, 1))
    assert polls.where(77) == (-123, 42)


# ---------------------------------------------------------------------------
# Vote events
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vote_event_text_and_metadata(poll_chat):
    polls.note_poll(77, -123, 42)
    polls.note_vote(vote(7, C, A))
    polls.note_vote(vote(8))
    out = await build_poll_vote_messages(
        poll_chat.client, 77, [7, 8], account_label="default", self_id=99
    )
    texts = [m["content"][0]["text"] for m in out]
    assert texts == [
        "[poll vote] Mykhailo (@mk) chose: Pizza, Salad (poll: Lunch?, message_id 42)",
        "[poll vote] Ann retracted their vote (poll: Lunch?, message_id 42)",
    ]
    first = out[0]
    assert first["channelId"] == "telegram:default:group:123"
    assert first["author"] == {"id": "7", "name": "Mykhailo"}
    assert first["metadata"]["isPollVote"] is True
    assert first["metadata"]["isReplyToBot"] is True
    assert first["metadata"]["replyToMessageId"] == "42"


@pytest.mark.asyncio
async def test_vote_on_unknown_poll_is_not_pushed(poll_chat):
    polls.note_vote(vote(7, A))
    assert (
        await build_poll_vote_messages(
            poll_chat.client, 77, [7], account_label="default", self_id=99
        )
        == []
    )


@pytest.mark.asyncio
async def test_batcher_flushes_once_per_poll_after_quiet_period():
    flushed = []

    async def flush(poll_id, voters):
        flushed.append((poll_id, voters))

    batcher = PollVoteBatcher(flush, delay=0.1)
    batcher.add(77, 8)
    batcher.add(77, 7)
    await asyncio.sleep(0.05)
    batcher.add(77, 7)  # changed their mind: the timer restarts
    batcher.add(78, 7)
    await asyncio.sleep(0.08)  # 0.13 s after the first vote, 0.08 s after the last
    assert flushed == []
    await asyncio.sleep(0.1)
    assert sorted(flushed) == [(77, [7, 8]), (78, [7])]
