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
| This enhancement plugin | `feishu-bot-enhancements 4.3.1` | `plugin.yaml` |

The plugin composes the bundled Feishu adapter and imports its public runtime
entry points instead of patching Hermes source. When upgrading Hermes or the
bundled `feishu-platform`, run this repository's full test suite before rollout.

## Current enhancements

### Frozen parent-chat context for topics

When a new Feishu topic first reaches Hermes, the plugin seeds that topic with
the active Hermes conversation from its parent chat that predates the topic's
root message. The root timestamp is resolved from the persisted parent message
when possible and otherwise from Feishu's message API. The snapshot is added
only once: later parent-chat messages never flow into the topic, while later
messages inside the topic continue using the topic's own session.

The same mechanism applies to group-chat and direct-message topics. It inherits
Hermes' persisted parent-chat conversation, not arbitrary messages that the bot
was never allowed to receive or persist. It follows the configured group and
thread session-isolation settings; for one shared context per group and per
topic, set `group_sessions_per_user: false` and
`thread_sessions_per_user: false`.

Parent-context inheritance is enabled by default. Disable it for both group and
direct-message topics with:

```yaml
platforms:
  feishu:
    extra:
      inherit_parent_chat_context: false
```

The setting is read when the Feishu adapter starts, so restart the gateway after
changing it. Disabling it prevents future snapshot injection; it does not remove
snapshots already persisted in existing topic transcripts.

### Custom menu events

Listens for `application.bot.menu_v6` and safely translates namespaced menu keys into existing, session-scoped Hermes commands:

- `hermes.model.status` → `/model`
- `hermes.model.<model-id>` → `/model <model-id> --session`
- `hermes.model.<provider>.<model-id>` →
  `/model <model-id> --provider <provider> --session`
- `hermes.reasoning.status` → `/reasoning`
- `hermes.reasoning.<effort>` → `/reasoning <effort>`

The provider is optional for backward compatibility. Prefer the provider-aware
form when the same model ID exists on multiple backends; for example,
`hermes.model.openai-codex.gpt-5.6-sol` avoids routing GPT to the API-key-only
`openai-api` provider. A dotted prefix is recognized as a provider only when it
matches a registered Hermes provider, so existing dotted model IDs continue to
work. Unsupported or unsafe event keys are rejected. Menu actions are routed
only to a previously established root DM for the app-scoped operator ID.
Routing is read from Hermes' canonical `state.db` gateway index, with
`sessions.json` retained only as a legacy fallback.

### Document reading in normal chats

Makes Hermes' built-in `feishu_doc_read` tool work when a user sends a Feishu
document link or token in an ordinary bot DM or group chat. The plugin binds the
connected adapter client and injects it only for the duration of the document
tool call; it does not copy or override Hermes' built-in reader.

The `feishu_doc` toolset must be enabled for the Feishu platform. The bot app
must have document-read API permission, and the requested document must be
accessible to that app. Feishu remains the authorization boundary.

### Document creation, rich editing, objects, and sharing

Adds sixteen tools to the existing `feishu_doc` toolset:

- `feishu_doc_create` creates an app-owned docx document.
- `feishu_doc_append_text` appends a text-like block or level 1-9 heading.
- `feishu_doc_update_text` replaces an existing text-like block's rich content.
- `feishu_doc_share` adds a viewer, editor, or manager. If no member is given,
  it shares with the current Feishu requester using the persisted session origin.
- `feishu_doc_insert_blocks` inserts up to 50 mixed text and structured blocks
  in one request. It enforces Feishu's published per-request resource limits
  and conservatively caps dynamically resolved `link_preview` plus
  `sub_page_list` blocks at five per call. Split larger sets into multiple
  calls, or use rich-text links instead of document previews.
- `feishu_doc_insert_media` creates, uploads, and binds an image or attachment.
- `feishu_doc_update_blocks` updates up to 200 distinct blocks in one request,
  including rich text, structural objects, and uploaded media replacement.
- `feishu_doc_delete_blocks` deletes a contiguous child-block range from a
  document or container.
- `feishu_doc_get_blocks` gets one structured Block or traverses the Block tree.
- `feishu_doc_comments` lists and manages full-document comments and replies.
- `feishu_doc_manage` renames, copies, moves, or deletes a document.
- `feishu_sheet_edit` edits Sheet cells, styles, dimensions, and worksheets.
- `feishu_bitable_edit` manages Bitable metadata, tables, fields, views, and records.
- `feishu_board_edit` inspects/creates/deletes Board nodes and changes its theme.
- `feishu_task_edit` creates and manages Task v2 tasks and their relationships.
- `feishu_wiki` resolves Wiki links, reads docx-backed pages, lists and searches
  knowledge spaces, and creates, renames, moves, or copies Wiki nodes.

### Wiki knowledge-base access

`feishu_wiki` accepts either a `/wiki/<node-token>` URL or a raw Wiki/document
token. `get_node` resolves a Wiki node to its underlying `obj_type` and
`obj_token`; `read_node` performs that resolution and returns plain text in one
call when the page is backed by docx. The returned object token can be passed to
the existing Docx, Sheet, Bitable, or other domain tools for type-specific
content operations.

Read actions are `list_spaces`, `get_space`, `get_node`, `read_node`,
`list_nodes`, and `search`. Write actions are `create_node`, `update_title`,
`move_node`, and `copy_node`. Lists can follow at most 20 pages automatically;
search uses Feishu's application-capable document/Wiki search API and can be
limited to one `space_id`.

`move_document` migrates an existing Drive document into a knowledge space,
optionally below `parent_node_token`. It accepts doc/docx, Sheet, Bitable, and
Mindnote URLs or tokens. The operation is asynchronous when Feishu returns a
`task_id`. By default the tool returns that ID immediately. Set
`wait_for_completion: true` on `move_document` or `get_task` to poll within the
same bounded tool call; `poll_interval_seconds` defaults to 2 seconds and
`task_timeout_seconds` defaults to 60 seconds (maximum 300). A timed-out wait
returns the latest processing state instead of waiting indefinitely. Setting
`apply: true` asks the document owner for migration approval when the bot cannot
move it directly; it does not bypass document permissions.

### Feishu request safety and media concurrency

The plugin applies a finite timeout to the shared Feishu SDK client and uses
bounded concurrency when a single `feishu_doc_update_blocks` call uploads
multiple images or files. Both values can be adjusted in `config.yaml` without
adding environment variables:

```yaml
platforms:
  feishu:
    extra:
      request_timeout_seconds: 60
      document_media_upload_concurrency: 4
```

`request_timeout_seconds` accepts 1–300 seconds. The media concurrency accepts
1–8 workers. Invalid values fall back to the defaults above. The timeout applies
to all calls made through the shared Lark client, including normal bot sends;
choose a value large enough for the tenant's biggest permitted upload.

All calls use the bot application's `tenant_access_token`. The application must
be granted access to the concrete knowledge space or added as a knowledge-space
member; API scopes alone do not grant resource access. Feishu's published Wiki
v2 API has no node-delete endpoint, so the plugin does not disguise deletion of
an underlying Drive document as Wiki-node deletion. Creating an entirely new
knowledge space and the older `/wiki/v2/nodes/search` endpoint require a user
token and are intentionally not exposed by this tenant-token tool.

Append and update accept either `content` for a single-style run or `elements`
for mixed formatting within one block. Supported inline styles are bold, italic,
underline, strikethrough, inline code, font/background colors, and links. For
example, a level-2 heading can use `content: "Summary", heading_level: 2,
bold: true`; mixed content can use `elements: [{"content": "Important",
"bold": true}, {"content": " details"}]`. Updating content preserves only the
styles supplied in that update.

Inline `elements` also support formulas (`type: "equation"`, with KaTeX in
`content`), user mentions, document mentions, reminders, and existing inline
attachments during update. Existing `comment_ids` can be retained in the
element style when replacing rich content. Text-like blocks
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
inspecting or editing its internal nodes uses the separate Board scopes below.

The batch update tool exposes every Block update operation available to this
tenant-token workflow: replace rich-text elements (including equations), update
text-block style, update both content and style, change table properties, insert
or delete table rows/columns, merge or unmerge cells, insert/delete/resize grid
columns, update an existing Task block, and replace an image or attachment.
Each update item uses `block_id`, an `operation`, and its operation-specific
`data`. Feishu does not allow the same Block ID to appear twice in one batch;
the tool rejects that case before sending the request. For `replace_image` and
`replace_file`, pass an existing token in `data.token`, pass `source` and let
the tool resolve/upload/bind new media, or omit both when changing only image
display properties so the current block token is reused automatically.

Block deletion follows Feishu's parent-child model rather than accepting a
standalone block ID. Supply `parent_block_id` (the document root is the default)
and zero-based `start_index`/`end_index`; the interval is left-closed and
right-open, so `0` through `1` deletes only the first child. Table rows and
columns and Grid columns are not deletable through this endpoint—use the
corresponding `delete_table_rows`, `delete_table_columns`, or
`delete_grid_column` update operation. Feishu also prevents deleting every
child of TableCell, GridColumn, and Callout containers.

Batch update example:

```json
{
  "document_id": "doxcn...",
  "updates": [
    {
      "block_id": "doxcn_text...",
      "operation": "update_text",
      "data": {
        "elements": [
          {"content": "Area: ", "bold": true},
          {"type": "equation", "content": "A=\\pi r^2"}
        ],
        "style": {"align": 2}
      }
    },
    {
      "block_id": "doxcn_table...",
      "operation": "merge_table_cells",
      "data": {
        "row_start_index": 0,
        "row_end_index": 1,
        "column_start_index": 0,
        "column_end_index": 2
      }
    }
  ]
}
```

Structured inspection returns the IDs, child order, media tokens, table cells,
and embedded-object tokens needed to build safe updates. Comment actions cover
listing, full-document comment creation, replies, reply edits/deletion, and
solve/reopen. Document lifecycle actions cover title changes, asynchronous
copy, folder moves, and recycle-bin deletion.

Embedded-object editing uses one operation-oriented tool per API family:

- Sheet writes or appends values/formulas, applies styles, adds/inserts/deletes/
  moves rows or columns, and batches worksheet add/copy/delete requests.
- Bitable updates app metadata and provides table, field, view, and batch record
  create/update/delete operations.
- Board lists nodes or the theme, creates nodes, recursively deletes nodes, and
  switches theme.
- Task v2 creates/updates/deletes tasks and adds/removes members, reminders,
  dependencies, and task-list membership.

These tools send documented Feishu request objects in `data`, so new field
types do not require a plugin release. They still validate routing IDs, batch
limits, and the operation-specific envelope before making a request. Feishu
does not currently publish APIs for moving an existing docx Block, changing a
Block's type in place, or updating an existing Board node's properties. Those
operations are intentionally not simulated by destructive delete/recreate.

Enable these least-privilege scopes in **Feishu Developer Console → Permissions
& Scopes**, then publish a new app version. Only enable the rows for tools you
intend to use:

- `docx:document` — **Create and edit new documents**. This covers document
  creation, block creation, block updates and deletion, and reading documents
  the app can access.
- `docs:permission.member:create` — **Add cloud document collaborators**.
- `docs:document.media:upload` — **Upload images and attachments to cloud
  documents**. Required for `feishu_doc_insert_media` and for source-based
  image/file replacement in `feishu_doc_update_blocks`.
- `docs:document.comment:read` and
  `docs:document.comment:write_only` — list comments and add/update/delete
  comments or replies, including solve/reopen.
- `docs:document:copy`, `space:document:move`, and `space:document:delete` —
  copy, move, and delete whole documents respectively. Rename remains covered
  by `docx:document`.
- `sheets:spreadsheet` — edit Sheet content and structure. Use
  `sheets:spreadsheet:write_only` instead if no Sheet read API is needed.
- `bitable:app` — edit Bitable apps, tables, fields, views, and records.
- `board:whiteboard:node:read`, `board:whiteboard:node:create`, and
  `board:whiteboard:node:delete` — inspect nodes/theme, create nodes/change the
  theme, and delete nodes respectively.
- `task:task:write` — create and manage Task v2 tasks. With a tenant token, the
  app can operate only on tasks for which it is a member or otherwise authorized.
- `wiki:space:retrieve`, `wiki:space:read`, `wiki:node:retrieve`, and
  `wiki:node:read` — list knowledge spaces/nodes and resolve Wiki links.
- `wiki:node:create`, `wiki:node:update`, `wiki:node:move`, and
  `wiki:node:copy` — create, rename, move, and copy Wiki nodes. The broader
  `wiki:wiki` scope may be used instead when all Wiki management is intended.
- `search:docs:read` — search Wiki/document metadata with the application-capable
  document search endpoint.

### 一键导入飞书权限

仓库根目录的 [`feishu-permissions.json`](./feishu-permissions.json) 是覆盖本
插件全部功能的完整权限模板。它方便快速配置，并非最小权限集合；正式环境
建议先检查其中的读写、删除及用户身份权限，只保留实际需要的权限。

1. 打开飞书开发者平台并进入对应应用。
2. 进入 **权限管理**，选择 **批量处理 → 批量导入/导出权限**。
3. 复制 `feishu-permissions.json` 的全部内容，粘贴到批量导入文本框中。
4. 检查变更并确认新增权限。

模板同时包含 `tenant` 和 `user` scopes；用户身份权限仍需由相应用户完成
授权后才能生效。若只需要部分能力，也可以按上方的最小权限说明手动配置。

Documents created with `tenant_access_token` belong to the app and live in the
app's cloud space. Users cannot open them until the app adds them as a
collaborator. For documents created by users, the app must first be added as a
document app/collaborator with sufficient edit or share permission. Feishu's
document sharing settings and tenant visibility policies still apply.

## Layout

```text
feishu-bot-enhancements/
├── plugin.yaml                         # Hermes plugin manifest
├── feishu-permissions.json             # Feishu bulk permission import template
├── __init__.py                         # Stable Hermes entry point
├── feishu_bot_enhancements/
│   ├── __init__.py
│   ├── document_access.py              # Normal-chat document access
│   ├── document_tools.py               # Create, edit, and share documents
│   ├── document_objects.py             # Blocks, formulas, images, attachments
│   ├── document_management.py          # Block inspection, comments, lifecycle
│   ├── document_domains.py             # Sheet, Bitable, Board, and Task APIs
│   ├── wiki_tools.py                    # Wiki resolution, browsing, and nodes
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
