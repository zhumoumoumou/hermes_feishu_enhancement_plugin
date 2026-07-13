"""Regression tests for frozen parent-group context in Feishu topics."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from gateway.platforms.base import MessageEvent, MessageType, Platform
from gateway.session import SessionSource, build_session_key

from feishu_bot_enhancements.topic_context import (
    TopicContextEnhancementMixin,
    coerce_epoch_seconds,
    render_parent_context,
)


class _Store:
    def __init__(self, history):
        self.config = SimpleNamespace(
            multiplex_profiles=False,
            group_sessions_per_user=False,
            thread_sessions_per_user=False,
        )
        self.history = history
        self.entries = {}
        self._db = None

    def _generate_session_key(self, source):
        return build_session_key(
            source,
            group_sessions_per_user=False,
            thread_sessions_per_user=False,
        )

    def peek_session_id(self, key):
        return self.entries.get(key)

    def load_transcript(self, session_id):
        assert session_id == "parent-session"
        return list(self.history)


def _source(thread_id="omt_topic", *, chat_type="group"):
    return SessionSource(
        platform=Platform.FEISHU,
        chat_id="oc_group" if chat_type == "group" else "oc_dm",
        chat_name="研发群" if chat_type == "group" else "Alice",
        chat_type=chat_type,
        user_id="ou_alice",
        user_name="Alice",
        thread_id=thread_id,
    )


def _event(*, root_id="om_root", create_time=2_000_000):
    message = SimpleNamespace(
        message_id="om_reply",
        root_id=root_id,
        thread_id="omt_topic",
        create_time=create_time,
    )
    return MessageEvent(
        text="话题内的新消息",
        message_type=MessageType.TEXT,
        source=_source(),
        raw_message=SimpleNamespace(event=SimpleNamespace(message=message)),
        message_id="om_reply",
    )


def _adapter(history):
    adapter = object.__new__(TopicContextEnhancementMixin)
    adapter._session_store = _Store(history)
    adapter._client = None
    parent_key = adapter._session_store._generate_session_key(_source(thread_id=None))
    adapter._session_store.entries[parent_key] = "parent-session"
    return adapter


def test_epoch_parser_accepts_feishu_milliseconds_and_datetimes():
    assert coerce_epoch_seconds(1_700_000_000_123) == 1_700_000_000.123
    dt = datetime(2026, 7, 13, tzinfo=timezone.utc)
    assert coerce_epoch_seconds(dt) == dt.timestamp()


def test_render_parent_context_keeps_only_provably_earlier_user_assistant_messages():
    context = render_parent_context(
        [
            {"role": "system", "content": "internal", "timestamp": 10},
            {"role": "user", "content": "before", "timestamp": 100},
            {"role": "assistant", "content": "answer", "timestamp": 110},
            {"role": "tool", "content": "secret tool output", "timestamp": 120},
            {"role": "user", "content": "at root", "timestamp": 200},
            {"role": "assistant", "content": "after root", "timestamp": 210},
            {"role": "user", "content": "unknown time"},
        ],
        cutoff_timestamp=200,
    )

    assert context is not None
    assert "before" in context
    assert "answer" in context
    assert "internal" not in context
    assert "secret tool output" not in context
    assert "at root" not in context
    assert "after root" not in context
    assert "unknown time" not in context


def test_new_topic_inherits_parent_snapshot_strictly_before_persisted_root():
    history = [
        {"role": "user", "content": "群聊旧问题", "timestamp": 100, "message_id": "om_old"},
        {"role": "assistant", "content": "群聊旧回答", "timestamp": 110},
        {"role": "user", "content": "话题根消息", "timestamp": 200, "message_id": "om_root"},
        {"role": "assistant", "content": "根消息之后的群聊回答", "timestamp": 210},
        {"role": "user", "content": "群聊后续新增", "timestamp": 220, "message_id": "om_later"},
    ]
    adapter = _adapter(history)
    event = _event()

    asyncio.run(adapter._seed_topic_context(event))

    assert "群聊旧问题" in event.channel_context
    assert "群聊旧回答" in event.channel_context
    assert "话题根消息" not in event.channel_context
    assert "根消息之后的群聊回答" not in event.channel_context
    assert "群聊后续新增" not in event.channel_context


def test_dm_topic_uses_the_same_frozen_parent_chat_snapshot():
    history = [
        {"role": "user", "content": "单聊旧问题", "timestamp": 100},
        {"role": "assistant", "content": "单聊旧回答", "timestamp": 110},
        {"role": "user", "content": "单聊后续新增", "timestamp": 210},
    ]
    adapter = object.__new__(TopicContextEnhancementMixin)
    adapter._session_store = _Store(history)
    adapter._client = None
    source = _source(chat_type="dm")
    parent_key = adapter._session_store._generate_session_key(
        _source(thread_id=None, chat_type="dm")
    )
    adapter._session_store.entries[parent_key] = "parent-session"
    message = SimpleNamespace(
        message_id="om_dm_reply",
        root_id="",
        thread_id="omt_topic",
        create_time=200,
    )
    event = MessageEvent(
        text="单聊话题消息",
        message_type=MessageType.TEXT,
        source=source,
        raw_message=SimpleNamespace(event=SimpleNamespace(message=message)),
        message_id="om_dm_reply",
    )

    asyncio.run(adapter._seed_topic_context(event))

    assert "单聊旧问题" in event.channel_context
    assert "单聊旧回答" in event.channel_context
    assert "单聊后续新增" not in event.channel_context


def test_existing_topic_with_snapshot_never_receives_new_parent_group_messages():
    adapter = _adapter([
        {"role": "user", "content": "old", "timestamp": 100, "message_id": "om_old"}
    ])
    topic_key = adapter._session_store._generate_session_key(_source())
    adapter._session_store.entries[topic_key] = "existing-topic-session"
    original_loader = adapter._session_store.load_transcript

    def _load(session_id):
        if session_id == "existing-topic-session":
            return [{
                "role": "user",
                "content": "<feishu_parent_group_context>\nalready frozen",
                "timestamp": 200,
            }]
        return original_loader(session_id)

    adapter._session_store.load_transcript = _load
    event = _event()

    asyncio.run(adapter._seed_topic_context(event))

    assert event.channel_context is None


def test_preexisting_topic_without_snapshot_is_backfilled_once():
    adapter = _adapter([
        {"role": "user", "content": "parent before root", "timestamp": 100},
    ])
    topic_key = adapter._session_store._generate_session_key(_source())
    adapter._session_store.entries[topic_key] = "existing-topic-session"
    original_loader = adapter._session_store.load_transcript

    def _load(session_id):
        if session_id == "existing-topic-session":
            return [{"role": "user", "content": "old topic turn", "timestamp": 210}]
        return original_loader(session_id)

    adapter._session_store.load_transcript = _load
    event = _event(root_id="", create_time=200_000)

    asyncio.run(adapter._seed_topic_context(event))

    assert "parent before root" in event.channel_context


def test_root_timestamp_falls_back_to_feishu_message_api_and_is_cached():
    adapter = _adapter([])
    root = SimpleNamespace(create_time=1_700_000_000_123)
    response = SimpleNamespace(
        success=lambda: True,
        data=SimpleNamespace(items=[root]),
    )
    get_message = Mock(return_value=response)
    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(get=get_message)))
    )
    adapter._build_get_message_request = Mock(return_value="request")

    async def _run_blocking(fn, request):
        return fn(request)

    adapter._run_blocking = _run_blocking

    first = asyncio.run(adapter._fetch_message_timestamp("om_root"))
    second = asyncio.run(adapter._fetch_message_timestamp("om_root"))

    assert first == 1_700_000_000.123
    assert second == first
    get_message.assert_called_once_with("request")


def test_mixin_is_composed_before_bundled_handle_message():
    from feishu_bot_enhancements.plugin import EnhancedFeishuAdapter

    assert EnhancedFeishuAdapter.__mro__.index(TopicContextEnhancementMixin) < 2
