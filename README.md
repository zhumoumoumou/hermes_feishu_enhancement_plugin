# Feishu Bot Enhancements

A profile-shareable Hermes plugin that centralizes enhancements for all of our Feishu bots without modifying Hermes Agent's bundled Feishu adapter.

## Current enhancements

### Custom menu events

Listens for `application.bot.menu_v6` and safely translates namespaced menu keys into existing, session-scoped Hermes commands:

- `hermes.model.status` → `/model`
- `hermes.model.<model-id>` → `/model <model-id> --session`
- `hermes.reasoning.status` → `/reasoning`
- `hermes.reasoning.<effort>` → `/reasoning <effort>`

Unsupported or unsafe event keys are rejected. Menu actions are routed only to a previously established root DM for the app-scoped operator ID. Routing is read from Hermes' canonical `state.db` gateway index, with `sessions.json` retained only as a legacy fallback.

### Document reading in normal chats

Makes Hermes' built-in `feishu_doc_read` tool work when a user sends a Feishu
document link or token in an ordinary bot DM or group chat. The plugin binds the
connected adapter client and injects it only for the duration of the document
tool call; it does not copy or override Hermes' built-in reader.

The `feishu_doc` toolset must be enabled for the Feishu platform. The bot app
must have document-read API permission, and the requested document must be
accessible to that app. Feishu remains the authorization boundary.

## Layout

```text
feishu-bot-enhancements/
├── plugin.yaml                         # Hermes plugin manifest
├── __init__.py                         # Stable Hermes entry point
├── feishu_bot_enhancements/
│   ├── __init__.py
│   ├── document_access.py              # Normal-chat document access
│   ├── plugin.py                       # Adapter composition and registration
│   └── menu_events.py                  # Custom-menu enhancement
├── tests/
├── requirements-dev.txt                # Isolated test dependencies
├── CHANGELOG.md
└── README.md
```

New Feishu enhancements belong in their own module under `feishu_bot_enhancements/`. Compose them into `EnhancedFeishuAdapter` in `plugin.py`; do not patch bundled Hermes source files.

## Test

```bash
python3.11 -m venv .venv  # Any Hermes-supported Python 3.11–3.13 works.
.venv/bin/python -m pip install -r requirements-dev.txt
PYTHONPATH="${HERMES_AGENT_ROOT:-$HOME/.hermes/hermes-agent}" \
  .venv/bin/python -m pytest -q
```

`HERMES_AGENT_ROOT` may point to another Hermes Agent checkout. The development
requirements include the Feishu SDK because one integration test verifies the
real SDK dispatcher registration.

## Profiles

The canonical repository lives under the default profile. Other profiles may load the same repository through profile-local symlinks, so one Git history governs all Feishu bots.
