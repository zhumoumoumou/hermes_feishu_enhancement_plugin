# Changelog

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
