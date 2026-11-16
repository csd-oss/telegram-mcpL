# Telegram incoming chat labels

Branch: `glassy/incoming-chat-label`, targeting `csd-oss/telegram-mcpL:homelab`.
Base: `1fe19cb58933623001d815a4c5367c4395ec0569`.

Homelab's `ansible/roles/mcp_telegram/defaults/main.yml` vendors this fork;
`ansible/group_vars/all.yml` pins `image_versions['telegram-mcpl']` to `1fe19cb`.
Its README identifies `homelab` as the integration branch. No homelab files or
image pins were changed. Nothing was deployed or merged.

Groups, supergroups and channels now show `[<Telegram chat title>]` before the
author, falling back to the existing chat ID when the title is absent. A DM
from its own peer keeps the quiet format. Reply context and media headers use
the same label. Titles are sanitized to one line and internal brackets become
parentheses, as with other header fields.

Delayed voice transcript success/failure notifications now show the title when
available and retain the numeric chat ID. Event IDs, routing, resource URIs,
tags and metadata are unchanged.

The pinned branch subscribes to NewMessage, poll votes and ChatAction, but not
MessageEdited. This change preserves those subscriptions. A real Telethon
MessageEdited event passed through the incoming formatter is covered by a test
and receives the same prefix; adding edit delivery is outside this formatting
change. Existing synthetic poll-vote text is unchanged.

## Before / after

Group text:

```text
Before: Ruby (@rbcnn) [message_id 815]: ок
After:  [UNITE Frontend Deployment] Ruby (@rbcnn) [message_id 815]: ок
```

Private DM (unchanged):

```text
Before: Misha (@magnumlink) [message_id 7431]: ок
After:  Misha (@magnumlink) [message_id 7431]: ок
```

Reply:

```text
Before:
↳ in reply to Glassy [message_id 814]: "deploy?"
Ruby [message_id 815]: ок

After:
↳ in reply to [UNITE Frontend Deployment] Glassy [message_id 814]: "deploy?"
[UNITE Frontend Deployment] Ruby [message_id 815]: ок
```

Edited message rendered by the shared formatter:

```text
Before: Ruby [message_id 815]: edited
After:  [UNITE Frontend Deployment] Ruby [message_id 815]: edited
```

Voice / media placeholder (also applies to captions and inline photos):

```text
Before: Ruby [message_id 815]: [voice, message_id 815]
After:  [UNITE Frontend Deployment] Ruby [message_id 815]: [voice, message_id 815]
```

Delayed voice transcript:

```text
Before: Transcript of voice message_id 815 in chat -100123 (uk, openai; machine transcript, not a verbatim quote):
After:  Transcript of voice message_id 815 in chat [UNITE Frontend Deployment] (-100123) (uk, openai; machine transcript, not a verbatim quote):
```

Missing title (existing peer ID `100`):

```text
Before: Ruby [message_id 815]: ок
After:  [100] Ruby [message_id 815]: ок
```

## Validation

Dependencies installed with `uv sync --frozen`; Python 3.13.5.
All tests use fake Telegram clients; no live messages were sent.

```text
$ .venv/bin/python -m pytest -q tests/test_incoming_chat_labels.py tests/test_mcpl_events.py tests/test_voice_transcripts.py
79 passed, 1 warning in 1.24s

$ .venv/bin/python -m pytest --cov --cov-report=term-missing --cov-report=xml -q
Required test coverage of 80.0% reached. Total coverage: 92.63%
1698 passed, 9 skipped, 2 warnings in 14.98s

$ .venv/bin/python -m black --check telegram_mcp/mcpl/events.py telegram_mcp/transcription.py tests/test_incoming_chat_labels.py tests/test_voice_transcripts.py
All done! ✨ 🍰 ✨
4 files would be left unchanged.

$ .venv/bin/python -m flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
0

$ git diff --check
(no output; exit 0)
```

Warnings are deprecations from python-json-logger and Starlette's httpx test client.
Full test output is saved locally at `/home/glassy/jobs/tgprefix/pytest-full.log`.
