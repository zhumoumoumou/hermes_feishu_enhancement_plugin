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

### Document creation, editing, and sharing

Adds four tools to the existing `feishu_doc` toolset:

- `feishu_doc_create` creates an app-owned docx document.
- `feishu_doc_append_text` appends normal text or a level 1-9 heading.
- `feishu_doc_update_text` replaces an existing text-like block's rich content.
- `feishu_doc_share` adds a viewer, editor, or manager. If no member is given,
  it shares with the current Feishu requester using the persisted session origin.

Append and update accept either `content` for a single-style run or `elements`
for mixed formatting within one block. Supported inline styles are bold, italic,
underline, strikethrough, inline code, font/background colors, and links. For
example, a level-2 heading can use `content: "Summary", heading_level: 2,
bold: true`; mixed content can use `elements: [{"content": "Important",
"bold": true}, {"content": " details"}]`. Updating content preserves only the
styles supplied in that update.

Enable these least-privilege scopes in **Feishu Developer Console → Permissions
& Scopes → Docs**, then publish a new app version:

- `docx:document` — **Create and edit new documents**. This covers document
  creation, block creation, block updates, and reading documents the app can
  access.
- `docs:permission.member:create` — **Add cloud document collaborators**.

Documents created with `tenant_access_token` belong to the app and live in the
app's cloud space. Users cannot open them until the app adds them as a
collaborator. For documents created by users, the app must first be added as a
document app/collaborator with sufficient edit or share permission. Feishu's
document sharing settings and tenant visibility policies still apply.

## Layout

```text
feishu-bot-enhancements/
├── plugin.yaml                         # Hermes plugin manifest
├── __init__.py                         # Stable Hermes entry point
├── feishu_bot_enhancements/
│   ├── __init__.py
│   ├── document_access.py              # Normal-chat document access
│   ├── document_tools.py               # Create, edit, and share documents
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
