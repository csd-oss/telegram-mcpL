"""Who voted for what in a poll, for bots and user sessions alike.

A user session asks Telegram (messages.getPollVotes). A bot can't: it is told
instead, through updates for the polls it sent. UpdateMessagePollVote carries a
voter and their options but only the poll id; UpdateMessagePoll and the poll
message itself carry the chat and message id. This module keeps both in memory,
so a bot knows the votes cast since the server started.
"""

from __future__ import annotations

from typing import Any, Optional

from telethon import events, functions, types, utils

from sanitize import sanitize_name, sanitize_user_content

VOTES_PAGE_SIZE = 50
VOTES_MAX_PAGES = 20

_where: dict[int, tuple[int, int]] = {}  # poll id -> (marked chat id, message id)
_results: dict[int, Any] = {}  # poll id -> latest PollResults from UpdateMessagePoll
_votes: dict[int, dict[int, list[bytes]]] = {}  # poll id -> voter id -> options ([] = retracted)
_attached: set[int] = set()


def reset() -> None:
    """Test helper: forget every poll."""
    _where.clear()
    _results.clear()
    _votes.clear()
    _attached.clear()


def attach(client) -> None:
    """Record poll updates for this client (idempotent). Runs for every client at
    startup, MCPL or not, so get_poll_results sees a bot's votes either way."""
    if id(client) in _attached:
        return

    async def on_poll_update(update):  # pragma: no cover — real-Telegram path
        if isinstance(update, types.UpdateMessagePollVote):
            note_vote(update)
        else:
            note_update(update)

    client.add_event_handler(
        on_poll_update, events.Raw((types.UpdateMessagePoll, types.UpdateMessagePollVote))
    )
    _attached.add(id(client))


def note_poll(poll_id: int, chat_id: int, message_id: int) -> None:
    _where[poll_id] = (chat_id, message_id)


def note_sent(result: Any) -> None:
    """Remember where the poll(s) in a send result went (create_poll)."""
    for update in getattr(result, "updates", None) or []:
        message = getattr(update, "message", None)
        media = getattr(message, "media", None)
        if isinstance(media, types.MessageMediaPoll):
            note_poll(media.poll.id, utils.get_peer_id(message.peer_id), message.id)


def note_update(update: types.UpdateMessagePoll) -> None:
    if update.peer is not None and update.msg_id is not None:
        note_poll(update.poll_id, utils.get_peer_id(update.peer), update.msg_id)
    _results[update.poll_id] = update.results


def note_vote(update: types.UpdateMessagePollVote) -> int:
    """Record a voter's current choice; returns the voter's id."""
    voter_id = utils.get_peer_id(update.peer)
    _votes.setdefault(update.poll_id, {})[voter_id] = list(update.options)
    return voter_id


def where(poll_id: int) -> Optional[tuple[int, int]]:
    return _where.get(poll_id)


def results_for(poll_id: int) -> Any:
    return _results.get(poll_id)


def choice(poll_id: int, voter_id: int) -> list[bytes]:
    return _votes.get(poll_id, {}).get(voter_id, [])


def votes_for(poll_id: int) -> dict[int, list[bytes]]:
    return {voter: options for voter, options in _votes.get(poll_id, {}).items() if options}


def option_texts(poll: Any, options: list[bytes]) -> list[str]:
    """Answer texts for option bytes, in the poll's order."""
    return [
        sanitize_user_content(answer.text.text)
        for answer in poll.answers
        if answer.option in options
    ]


def voter_record(entity: Any, voter_id: int) -> dict[str, Any]:
    record: dict[str, Any] = {"id": voter_id}
    if entity is None:
        return record
    name = getattr(entity, "title", None) or " ".join(
        filter(None, (getattr(entity, "first_name", None), getattr(entity, "last_name", None)))
    )
    if name:
        record["name"] = sanitize_name(name)
    if getattr(entity, "username", None):
        record["username"] = entity.username
    return record


async def fetch_votes(client, peer, message_id: int) -> list[tuple[dict[str, Any], list[bytes]]]:
    """(voter, options) for every vote, from messages.getPollVotes (user sessions)."""
    out: list[tuple[dict[str, Any], list[bytes]]] = []
    offset = None
    for _ in range(VOTES_MAX_PAGES):
        page = await client(
            functions.messages.GetPollVotesRequest(
                peer=peer, id=message_id, limit=VOTES_PAGE_SIZE, offset=offset
            )
        )
        entities = {utils.get_peer_id(e): e for e in [*page.users, *page.chats]}
        for vote in page.votes:
            options = getattr(vote, "options", None)
            if options is None:
                option = getattr(vote, "option", None)
                options = [option] if option is not None else []
            voter_id = utils.get_peer_id(vote.peer)
            out.append((voter_record(entities.get(voter_id), voter_id), list(options)))
        offset = page.next_offset
        if not offset:
            break
    return out


def poll_record(
    poll: Any,
    results: Any,
    votes: Optional[list[tuple[dict[str, Any], list[bytes]]]],
) -> dict[str, Any]:
    """The tool's view of a poll: options with counts and, if known, each voter's choice."""
    counts = {r.option: r.voters or 0 for r in (getattr(results, "results", None) or [])}
    total = getattr(results, "total_voters", None) or 0
    options = []
    for index, answer in enumerate(poll.answers):
        voters = counts.get(answer.option, 0)
        options.append(
            {
                "index": index,
                "text": sanitize_user_content(answer.text.text),
                "voters": voters,
                "percent": round(100 * voters / total, 1) if total else 0.0,
            }
        )
    record: dict[str, Any] = {
        "question": sanitize_user_content(poll.question.text),
        "options": options,
        "total_voters": total,
        "closed": bool(poll.closed),
        "multiple_choice": bool(poll.multiple_choice),
        "quiz": bool(poll.quiz),
        "public_votes": bool(poll.public_voters),
    }
    if votes is not None:
        indices = {answer.option: i for i, answer in enumerate(poll.answers)}
        record["votes"] = [
            {
                "voter": voter,
                "options": option_texts(poll, chosen),
                "option_indices": sorted(indices[o] for o in chosen if o in indices),
            }
            for voter, chosen in votes
        ]
    return record
