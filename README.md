# Feishu Bot Enhancements

A profile-shareable Hermes plugin that centralizes enhancements for all of our Feishu bots without modifying Hermes Agent's bundled Feishu adapter.

## Version baseline

This release was developed and regression-tested against the following exact
versions. They are the known-good baseline, not a claim that every later Hermes
revision is automatically compatible.

| Component | Version | Baseline detail |
| --- | --- | --- |
| Hermes Agent | `0.18.2` | Source revision `111544d` (2026-07-10) |
| Bundled Feishu bot platform | `feishu-platform 1.0.0` | `plugins/platforms/feishu/plugin.yaml` |
| Feishu Python SDK | `lark-oapi 1.5.3` | Development and integration-test dependency |
| This enhancement plugin | `feishu-bot-enhancements 3.0.0` | `plugin.yaml` |

The plugin composes the bundled Feishu adapter and imports its public runtime
entry points instead of patching Hermes source. When upgrading Hermes or the
bundled `feishu-platform`, run this repository's full test suite before rollout.

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

### Document creation, rich editing, objects, and sharing

Adds six tools to the existing `feishu_doc` toolset:

- `feishu_doc_create` creates an app-owned docx document.
- `feishu_doc_append_text` appends a text-like block or level 1-9 heading.
- `feishu_doc_update_text` replaces an existing text-like block's rich content.
- `feishu_doc_share` adds a viewer, editor, or manager. If no member is given,
  it shares with the current Feishu requester using the persisted session origin.
- `feishu_doc_insert_blocks` inserts up to 50 mixed text and structured blocks
  in one request.
- `feishu_doc_insert_media` creates, uploads, and binds an image or attachment.

Append and update accept either `content` for a single-style run or `elements`
for mixed formatting within one block. Supported inline styles are bold, italic,
underline, strikethrough, inline code, font/background colors, and links. For
example, a level-2 heading can use `content: "Summary", heading_level: 2,
bold: true`; mixed content can use `elements: [{"content": "Important",
"bold": true}, {"content": " details"}]`. Updating content preserves only the
styles supplied in that update.

Inline `elements` also support formulas (`type: "equation"`, with KaTeX in
`content`), user mentions, document mentions, and reminders. Text-like blocks
cover normal text, headings 1-9, unordered and ordered lists, code, quote, and
todo. The batch block tool additionally covers the public block types that
Feishu currently allows callers to create directly: Bitable, Callout, ChatCard,
Divider, Grid, Iframe, ISV, Sheet, Table, QuoteContainer, AddOns, Board, old/new
Wiki sub-page lists, and message-link previews.
Container contents can be inserted later by passing the returned cell, column,
callout, or quote-container block ID as `parent_block_id`.

Formula and mixed-object example:

```json
{
  "document_id": "doxcn...",
  "blocks": [
    {"type": "heading2", "content": "Result"},
    {
      "type": "text",
      "elements": [
        {"content": "Area: ", "bold": true},
        {"type": "equation", "content": "A=\\pi r^2"}
      ]
    },
    {"type": "divider"},
    {"type": "table", "data": {"property": {"row_size": 2, "column_size": 3}}}
  ]
}
```

Images accept a workspace/cache path, safe HTTP(S) URL, or base64 data URL.
They can also set alignment and a plain-text caption. Attachments accept a
workspace/cache path and card or preview display. Feishu's single-part media
API has a 20 MB limit; larger files require a separate multipart workflow and
are rejected before a document block is created. Types Feishu exposes only as
generated/read-only blocks (such as TableCell, GridColumn, View, OKR children),
or marks non-creatable (such as Mindnote, Diagram, Task, Agenda, and synced
blocks), are intentionally not presented as direct-create types. OKR creation is
also excluded because Feishu requires a `user_access_token`, while these bot
tools deliberately operate with the connected app's `tenant_access_token`.
Wiki sub-page lists work only inside eligible knowledge-base documents, and
message-link previews remain subject to Feishu's rule that the caller created
the referenced message link. Creating an empty Board needs no extra scope;
editing its internal nodes later requires the separate Board node-write scope.

Enable these least-privilege scopes in **Feishu Developer Console → Permissions
& Scopes → Docs**, then publish a new app version:

- `docx:document` — **Create and edit new documents**. This covers document
  creation, block creation, block updates, and reading documents the app can
  access.
- `docs:permission.member:create` — **Add cloud document collaborators**.
- `docs:document.media:upload` — **Upload images and attachments to cloud
  documents**. Required only for `feishu_doc_insert_media`.

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
│   ├── document_objects.py             # Blocks, formulas, images, attachments
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
