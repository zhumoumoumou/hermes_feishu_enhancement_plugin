# Changelog

## 4.3.1

- Preflight document Block resource counts before sending create requests,
  including Feishu's published Sheet, Bitable, ISV, and mention limits.
- Cap dynamically resolved link-preview and Wiki sub-page-list blocks at five
  per request, and return actionable split-or-rich-link guidance for error
  `1770035` instead of encouraging an unchanged retry.
- Accept Feishu's documented `Undefined` link-preview URL type in addition to
  `MessageLink`.

## 4.3.0

- Apply a configurable finite timeout to the shared Feishu SDK client so a
  stalled HTTP connection cannot hold an agent session forever.
- Prepare and upload source-based document media with configurable bounded
  concurrency while preserving update/result order.
- Add optional bounded polling for Wiki document-migration tasks, including
  explicit success, failure, processing, and timeout states.

## 4.2.1

- Add existing Drive-document migration into Wiki spaces, including optional
  owner approval requests and asynchronous task-result lookup.

## 4.2.0

- Add `feishu_wiki` for Wiki URL/token resolution, docx-backed page reading,
  space and node browsing, application-visible search, and node create/rename/
  move/copy operations.
- Add the `search:docs:read` scope to the bulk permission template and document
  the tenant-token resource-access and Wiki node-deletion boundaries.

## 4.1.3

- Add optional provider-aware Feishu menu model keys while retaining legacy
  model-only keys and session-scoped switching behavior.

## 4.1.2

- Add a ready-to-import Feishu permission JSON covering the plugin's complete
  feature set, with bulk-import instructions and least-privilege guidance.

## 4.1.1

- Add the enabled-by-default `inherit_parent_chat_context` Feishu setting to
  disable frozen parent-chat snapshots for both group and direct-message topics.

## 4.1.0

- Seed each new Feishu topic once with the parent Hermes group conversation
  from strictly before the topic root message.
- Freeze the inherited snapshot so later parent-chat messages never leak into
  an existing topic, with exact root-time lookup through Feishu when needed.
- Apply the same frozen parent-chat snapshot semantics to direct-message topics.

## 4.0.0

- Add structured Block inspection with bounded automatic pagination.
- Add document comment/reply management and document rename, copy, move, and
  recycle-bin deletion.
- Preserve existing inline attachments and rich-text comment references during
  content updates, and reuse existing media tokens for display-only changes.
- Add Sheet value/style/dimension/worksheet editing; Bitable app/table/field/
  view/record CRUD; Board inspection/node creation/deletion/theme updates; and
  Task v2 task/member/reminder/dependency/task-list operations.
- Register fifteen document-domain tools and expand request-shape regression
  coverage across every newly supported API family.

## 3.1.0

- Add one-call updates for up to 200 distinct document blocks, covering rich
  text and block style, tables, grids, tasks, images, and attachments.
- Allow image and attachment replacement from either an existing media token or
  a safely resolved source that the tool uploads and binds automatically.
- Add parent/index-range block deletion with validation for Feishu's exclusive
  end index and structural deletion constraints.
- Register eight document tools and add end-to-end request-shape, media-upload,
  operation-matrix, and deletion regression coverage.

## 3.0.0

- Add KaTeX equations, user/document mentions, and reminders as rich-text elements.
- Add list, code, quote, and todo text block creation with block-level styles.
- Add one-call insertion of up to 50 mixed text and structured document blocks.
- Add end-to-end image and attachment insertion with safe source resolution,
  Feishu media upload, and token binding.
- Cover the stable public Block types that the bot's tenant token can create,
  including Board, Wiki sub-page lists, and message-link previews, while
  rejecting generated/read-only and user-token-only types.

## 2.3.0

- Add heading levels 1-9 to document block creation.
- Add whole-block and per-segment rich-text styles to append and update tools.
- URL-encode rich-text links before sending them to Feishu.

## 2.2.0

- Add tools to create, append to, update, and share Feishu docx documents.
- Resolve the current Feishu requester for one-step sharing of app-owned docs.
- Document the least-privilege Feishu scopes and document-level prerequisites.

## 2.1.0

- Document and pin the isolated test environment, including the Feishu SDK.
- Cover canonical-database precedence and invalid-database JSON fallback.
- Enable the built-in Feishu document reader in normal bot DMs and group chats.

## 2.0.2

- Prevent menu-generated commands from being treated as replies to a fake Feishu message ID.
- Send command results as normal messages to the resolved DM chat.

## 2.0.1

- Resolve menu-operator DM routing from the canonical `state.db` gateway index.
- Keep `sessions.json` only as a compatibility fallback.
- Add regression coverage for installations with no legacy JSON mirror.

## 2.0.0

- Rename the plugin from `feishu-menu-events` to `feishu-bot-enhancements`.
- Restructure the project as a central suite for shared Feishu bot enhancements.
- Move custom-menu handling into a dedicated enhancement module.
- Preserve safe model and reasoning menu command routing.

## 1.0.0

- Add support for Feishu `application.bot.menu_v6` events.
