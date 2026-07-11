# Feishu Bot Enhancements

A profile-shareable Hermes plugin that centralizes enhancements for all of our Feishu bots without modifying Hermes Agent's bundled Feishu adapter.

## Current enhancement

### Custom menu events

Listens for `application.bot.menu_v6` and safely translates namespaced menu keys into existing, session-scoped Hermes commands:

- `hermes.model.status` → `/model`
- `hermes.model.<model-id>` → `/model <model-id> --session`
- `hermes.reasoning.status` → `/reasoning`
- `hermes.reasoning.<effort>` → `/reasoning <effort>`

Unsupported or unsafe event keys are rejected. Menu actions are routed only to a previously established root DM for the app-scoped operator ID. Routing is read from Hermes' canonical `state.db` gateway index, with `sessions.json` retained only as a legacy fallback.

## Layout

```text
feishu-bot-enhancements/
├── plugin.yaml                         # Hermes plugin manifest
├── __init__.py                         # Stable Hermes entry point
├── feishu_bot_enhancements/
│   ├── __init__.py
│   ├── plugin.py                       # Adapter composition and registration
│   └── menu_events.py                  # Custom-menu enhancement
├── tests/
├── CHANGELOG.md
└── README.md
```

New Feishu enhancements belong in their own module under `feishu_bot_enhancements/`. Compose them into `EnhancedFeishuAdapter` in `plugin.py`; do not patch bundled Hermes source files.

## Test

```bash
PYTHONPATH=~/.hermes/hermes-agent .venv/bin/python -m pytest -q
```

## Profiles

The canonical repository lives under the default profile. Other profiles may load the same repository through profile-local symlinks, so one Git history governs all Feishu bots.
