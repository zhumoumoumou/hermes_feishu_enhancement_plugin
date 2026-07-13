"""Seed a Feishu topic with a frozen snapshot of its parent chat context."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import OrderedDict
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


logger = logging.getLogger("hermes.plugins.feishu_bot_enhancements.topic_context")

_ROOT_TIME_CACHE_SIZE = 256
_INHERIT_PARENT_CONTEXT_KEY = "inherit_parent_chat_context"
_CONTEXT_TAG = "<feishu_parent_chat_context>"
_LEGACY_CONTEXT_TAG = "<feishu_parent_group_context>"
_CONTEXT_HEADER = (
    "[Historical context snapshot: the following user/assistant messages are "
    "from the parent Feishu chat before this topic began. Treat them as prior "
    "conversation context, not as new messages in this topic.]"
)


def coerce_epoch_seconds(value: Any) -> float | None:
    """Normalize a datetime or seconds/milliseconds epoch value."""
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OSError, OverflowError, ValueError):
            return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    # Feishu Message.create_time is a millisecond Unix timestamp.
    if timestamp >= 10_000_000_000:
        timestamp /= 1000.0
    return timestamp


def _render_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif item not in (None, ""):
                parts.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
        return "\n".join(parts).strip()
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(content).strip()


def render_parent_context(
    history: Iterable[Mapping[str, Any]],
    *,
    cutoff_timestamp: float,
) -> str | None:
    """Render only parent user/assistant messages strictly before the topic root."""
    rendered: list[str] = []
    for message in history:
        role = str(message.get("role") or "").lower()
        if role not in {"user", "assistant"}:
            continue
        timestamp = coerce_epoch_seconds(message.get("timestamp"))
        # Unknown timestamps cannot be proven to predate the topic. Excluding
        # them preserves the snapshot boundary instead of leaking later chat.
        if timestamp is None or timestamp >= cutoff_timestamp:
            continue
        content = _render_content(message.get("content"))
        if not content:
            continue
        stamp = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(
            timespec="seconds"
        )
        label = "User" if role == "user" else "Assistant"
        rendered.append(f"[{stamp}] {label}:\n{content}")
    if not rendered:
        return None
    return (
        f"{_CONTEXT_HEADER}\n"
        f"{_CONTEXT_TAG}\n"
        + "\n\n".join(rendered)
        + "\n</feishu_parent_chat_context>"
    )


def _raw_message(event: Any) -> Any:
    raw = getattr(event, "raw_message", None)
    payload = getattr(raw, "event", None)
    return getattr(payload, "message", None) or raw


def _platform_value(source: Any) -> str:
    platform = getattr(source, "platform", None)
    return str(getattr(platform, "value", platform) or "")


class TopicContextEnhancementMixin:
    """Add an immutable parent-chat history snapshot to each Feishu topic."""

    def _parent_context_inheritance_enabled(self) -> bool:
        extra = getattr(getattr(self, "config", None), "extra", None) or {}
        value = extra.get(_INHERIT_PARENT_CONTEXT_KEY, True)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"false", "0", "no", "off", "disabled"}:
                return False
            if normalized in {"true", "1", "yes", "on", "enabled"}:
                return True
        # Preserve the enabled-by-default behavior for missing or malformed
        # values; disabling inheritance must be explicit and unambiguous.
        return True

    async def handle_message(self, event) -> None:
        # Feishu's bundled adapter calls handle_message while holding its
        # per-chat lock. Seed here so two first messages cannot both inherit,
        # and so the base method installs its active-session guard immediately
        # after this method returns.
        await self._seed_topic_context(event)
        await super().handle_message(event)

    async def _seed_topic_context(self, event: Any) -> None:
        if not self._parent_context_inheritance_enabled():
            return
        source = getattr(event, "source", None)
        if (
            source is None
            or not getattr(source, "thread_id", None)
            or getattr(event, "is_command", lambda: False)()
        ):
            return

        store = getattr(self, "_session_store", None)
        if store is None:
            return

        # Secondary multiplexed-profile adapters are constructed inside that
        # profile's runtime scope, before the runner's handler stamps events.
        if (
            getattr(getattr(store, "config", None), "multiplex_profiles", False)
            and not getattr(source, "profile", None)
            and getattr(self, "_enhancement_profile_name", None)
        ):
            source.profile = self._enhancement_profile_name

        try:
            topic_key = store._generate_session_key(source)
        except Exception:
            logger.debug("Unable to derive Feishu topic session key", exc_info=True)
            return

        seeded = getattr(self, "_topic_context_seeded", None)
        if seeded is None:
            seeded = set()
            self._topic_context_seeded = seeded
        if topic_key in seeded:
            return

        topic_session_id = await asyncio.to_thread(
            self._find_session_id, store, source, topic_key
        )
        if topic_session_id:
            topic_history = await asyncio.to_thread(
                store.load_transcript, topic_session_id
            )
            if self._history_has_snapshot(topic_history):
                seeded.add(topic_key)
                return

        parent_source = replace(source, thread_id=None)
        try:
            parent_key = store._generate_session_key(parent_source)
        except Exception:
            logger.debug("Unable to derive parent Feishu chat session key", exc_info=True)
            return
        parent_session_id = await asyncio.to_thread(
            self._find_session_id, store, parent_source, parent_key
        )
        if not parent_session_id:
            seeded.add(topic_key)
            return

        history = await asyncio.to_thread(store.load_transcript, parent_session_id)
        if not history:
            seeded.add(topic_key)
            return

        cutoff = self._cutoff_from_parent_history(event, history)
        if cutoff is None:
            cutoff = await self._fetch_topic_root_timestamp(event)
        if cutoff is None:
            logger.warning(
                "Unable to establish the root time for new Feishu topic %s; "
                "parent context was not inherited to avoid including later chat messages",
                getattr(source, "thread_id", ""),
            )
            seeded.add(topic_key)
            return

        context = render_parent_context(history, cutoff_timestamp=cutoff)
        if context:
            existing = str(getattr(event, "channel_context", "") or "").strip()
            event.channel_context = f"{context}\n\n{existing}" if existing else context
            logger.info(
                "Seeded Feishu topic %s from parent chat session %s at cutoff %.3f",
                getattr(source, "thread_id", ""),
                parent_session_id,
                cutoff,
            )
        seeded.add(topic_key)

    @staticmethod
    def _history_has_snapshot(history: Iterable[Mapping[str, Any]]) -> bool:
        return any(
            any(
                tag in _render_content(message.get("content"))
                for tag in (_CONTEXT_TAG, _LEGACY_CONTEXT_TAG)
            )
            for message in history
        )

    @staticmethod
    def _find_session_id(store: Any, source: Any, session_key: str) -> str | None:
        session_id = store.peek_session_id(session_key)
        if session_id:
            return str(session_id)
        db = getattr(store, "_db", None)
        finder = getattr(db, "find_latest_gateway_session_for_peer", None)
        if not callable(finder):
            return None
        row = finder(
            source=_platform_value(source),
            user_id=getattr(source, "user_id", None),
            session_key=session_key,
            chat_id=getattr(source, "chat_id", None),
            chat_type=getattr(source, "chat_type", None),
            thread_id=getattr(source, "thread_id", None),
        )
        return str(row.get("id")) if isinstance(row, Mapping) and row.get("id") else None

    @staticmethod
    def _cutoff_from_parent_history(
        event: Any, history: Iterable[Mapping[str, Any]]
    ) -> float | None:
        message = _raw_message(event)
        root_id = str(getattr(message, "root_id", "") or "").strip()
        if not root_id:
            return None
        for item in history:
            persisted_id = str(
                item.get("message_id") or item.get("platform_message_id") or ""
            ).strip()
            if persisted_id == root_id:
                return coerce_epoch_seconds(item.get("timestamp"))
        return None

    async def _fetch_topic_root_timestamp(self, event: Any) -> float | None:
        message = _raw_message(event)
        if message is None:
            return None
        root_id = str(getattr(message, "root_id", "") or "").strip()
        current_id = str(
            getattr(message, "message_id", "") or getattr(event, "message_id", "") or ""
        ).strip()

        # A topic-root event has no root_id yet; its own create_time is the
        # boundary. Replies carry root_id and require the root message time.
        if not root_id or root_id == current_id:
            return coerce_epoch_seconds(getattr(message, "create_time", None))
        return await self._fetch_message_timestamp(root_id)

    async def _fetch_message_timestamp(self, message_id: str) -> float | None:
        cache = getattr(self, "_topic_root_time_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._topic_root_time_cache = cache
        if message_id in cache:
            cache.move_to_end(message_id)
            return cache[message_id]

        client = getattr(self, "_client", None)
        if client is None:
            return None
        try:
            request = self._build_get_message_request(message_id)
            response = await self._run_blocking(client.im.v1.message.get, request)
            if not response or getattr(response, "success", lambda: False)() is False:
                return None
            items = getattr(getattr(response, "data", None), "items", None) or []
            root = items[0] if items else None
            timestamp = coerce_epoch_seconds(getattr(root, "create_time", None))
        except Exception:
            logger.warning(
                "Failed to fetch Feishu topic root timestamp for %s",
                message_id,
                exc_info=True,
            )
            return None

        cache[message_id] = timestamp
        while len(cache) > _ROOT_TIME_CACHE_SIZE:
            cache.popitem(last=False)
        return timestamp


__all__ = [
    "TopicContextEnhancementMixin",
    "coerce_epoch_seconds",
    "render_parent_context",
]
