# Changelog

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
