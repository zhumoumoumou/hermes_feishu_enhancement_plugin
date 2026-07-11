"""Custom-menu enhancement for Hermes Feishu bots."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from hermes_constants import get_hermes_home
from plugins.platforms.feishu import adapter as _bundled


logger = logging.getLogger("hermes.plugins.feishu_bot_enhancements.menu_events")
_SAFE_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_SAFE_REASONING = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}\Z")


def parse_menu_command(event_key: str) -> str | None:
    """Translate a safe, namespaced menu key into a session-scoped command."""
    key = str(event_key or "").strip()
    if key.startswith("hermes.model."):
        model = key.removeprefix("hermes.model.")
        if model == "status":
            return "/model"
        if _SAFE_MODEL.fullmatch(model):
            return f"/model {model} --session"
        return None
    if key.startswith("hermes.reasoning."):
        effort = key.removeprefix("hermes.reasoning.")
        if effort == "status":
            return "/reasoning"
        if _SAFE_REASONING.fullmatch(effort):
            return f"/reasoning {effort}"
    return None


def _latest_root_dm(entries, open_id: str) -> str | None:
    matches: list[tuple[str, str]] = []
    values = entries.values() if isinstance(entries, dict) else ()
    for value in values:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                continue
        if not isinstance(value, dict):
            continue
        origin = value.get("origin")
        if not isinstance(origin, dict):
            continue
        if (
            origin.get("platform") == "feishu"
            and origin.get("chat_type") == "dm"
            and origin.get("user_id") == open_id
            and not origin.get("thread_id")
            and origin.get("chat_id")
        ):
            matches.append((str(value.get("updated_at") or ""), str(origin["chat_id"])))
    return max(matches)[1] if matches else None


def resolve_dm_chat_id(
    open_id: str,
    sessions_path: Path | None = None,
    *,
    state_db_path: Path | None = None,
) -> str | None:
    """Resolve the latest root DM from state.db, then the legacy JSON mirror."""
    path = sessions_path or (get_hermes_home() / "sessions" / "sessions.json")
    db_path = state_db_path or (get_hermes_home() / "state.db")

    try:
        from hermes_state import SessionDB

        db = SessionDB(db_path=db_path, read_only=True)
        try:
            entries = db.load_gateway_routing_entries(scope=str(path.parent.resolve()))
        finally:
            db.close()
        chat_id = _latest_root_dm(entries, open_id)
        if chat_id:
            return chat_id
    except Exception:
        logger.debug("Unable to read canonical gateway routing from state.db", exc_info=True)

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return _latest_root_dm(payload, open_id)


class MenuEventEnhancementMixin:
    """Add Feishu custom-menu event handling to a compatible adapter."""

    def _build_event_handler(self):
        handler = super()._build_event_handler()
        if handler is None or _bundled.EventDispatcherHandler is None:
            return handler

        extension = (
            _bundled.EventDispatcherHandler.builder(
                self._encrypt_key,
                self._verification_token,
            )
            .register_p2_application_bot_menu_v6(self._on_bot_menu_event)
            .build()
        )
        handler._processorMap.update(extension._processorMap)
        return handler

    def _on_bot_menu_event(self, data) -> None:
        """Record and schedule one custom-menu event on the adapter loop."""
        event = getattr(data, "event", None)
        event_key = str(getattr(event, "event_key", "") or "").strip()
        operator = getattr(event, "operator", None)
        operator_id = getattr(operator, "operator_id", None)
        open_id = str(getattr(operator_id, "open_id", "") or "").strip()
        logger.info(
            "[Feishu menu] application.bot.menu_v6 received: "
            "event_key=%r operator_open_id=%s",
            event_key,
            open_id or "<unknown>",
        )
        loop = getattr(self, "_loop", None)
        if not self._loop_accepts_callbacks(loop):
            logger.warning("[Feishu menu] adapter loop unavailable; event dropped")
            return
        self._submit_on_loop(loop, self._route_bot_menu_event(data))

    async def _route_bot_menu_event(self, data) -> None:
        event = getattr(data, "event", None)
        event_key = str(getattr(event, "event_key", "") or "").strip()
        command = parse_menu_command(event_key)
        if command is None:
            logger.warning("[Feishu menu] unsupported or unsafe event_key=%r", event_key)
            return

        operator = getattr(event, "operator", None)
        operator_id = getattr(operator, "operator_id", None)
        open_id = str(getattr(operator_id, "open_id", "") or "").strip()
        if not open_id:
            logger.warning("[Feishu menu] event missing operator open_id; dropped")
            return
        chat_id = resolve_dm_chat_id(open_id)
        if not chat_id:
            logger.warning(
                "[Feishu menu] no persisted root DM found for operator_open_id=%s; dropped",
                open_id,
            )
            return

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_id,
            chat_type="dm",
            user_id=open_id,
            user_name=str(getattr(operator, "operator_name", "") or "") or None,
        )
        synthetic_event = _bundled.MessageEvent(
            text=command,
            message_type=_bundled.MessageType.COMMAND,
            source=source,
            raw_message=data,
            message_id=f"menu:{getattr(event, 'timestamp', '')}:{_bundled.uuid.uuid4()}",
            channel_prompt=self._resolve_channel_prompt(chat_id),
            timestamp=_bundled.datetime.now(),
        )
        logger.info(
            "[Feishu menu] routing event_key=%r as command=%r chat_id=%s",
            event_key,
            command,
            chat_id,
        )
        await self._handle_message_with_guards(synthetic_event)
