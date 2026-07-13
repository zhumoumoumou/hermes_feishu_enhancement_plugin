"""Behavior tests for the user-level Feishu menu event extension."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def test_plugin_is_named_and_structured_as_feishu_bot_enhancements():
    manifest = yaml.safe_load((PLUGIN_ROOT / "plugin.yaml").read_text(encoding="utf-8"))

    assert manifest["name"] == "feishu-bot-enhancements"
    assert manifest["kind"] == "standalone"
    assert (PLUGIN_ROOT / "feishu_bot_enhancements" / "menu_events.py").is_file()
    assert (PLUGIN_ROOT / "README.md").is_file()


def _load_plugin_module():
    path = PLUGIN_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location("feishu_menu_events_plugin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_registers_application_bot_menu_v6_without_rebuilding_base_dispatcher(monkeypatch):
    plugin = _load_plugin_module()
    from lark_oapi.event.dispatcher_handler import EventDispatcherHandler

    base_handler = EventDispatcherHandler.builder("", "").build()
    monkeypatch.setattr(
        plugin.BundledFeishuAdapter,
        "_build_event_handler",
        lambda self: base_handler,
    )

    adapter = plugin.MenuEnabledFeishuAdapter.__new__(plugin.MenuEnabledFeishuAdapter)
    adapter._encrypt_key = ""
    adapter._verification_token = ""

    handler = adapter._build_event_handler()

    assert handler is base_handler
    assert "p2.application.bot.menu_v6" in handler._processorMap


def test_menu_event_records_event_key_and_operator(caplog):
    from types import SimpleNamespace

    plugin = _load_plugin_module()
    adapter = plugin.MenuEnabledFeishuAdapter.__new__(plugin.MenuEnabledFeishuAdapter)
    data = SimpleNamespace(
        event=SimpleNamespace(
            event_key="hermes.reasoning.high",
            operator=SimpleNamespace(
                operator_id=SimpleNamespace(open_id="ou_test_operator")
            ),
        )
    )

    with caplog.at_level(
        "INFO", logger="hermes.plugins.feishu_bot_enhancements.menu_events"
    ):
        adapter._on_bot_menu_event(data)

    assert "application.bot.menu_v6 received" in caplog.text
    assert "hermes.reasoning.high" in caplog.text
    assert "ou_test_operator" in caplog.text


def test_register_wraps_existing_feishu_entry_without_copying_metadata(monkeypatch):
    from gateway.platform_registry import PlatformEntry

    plugin = _load_plugin_module()
    sentinel_sender = object()
    existing = PlatformEntry(
        name="feishu",
        label="Future Feishu Label",
        adapter_factory=lambda config: ("bundled", config),
        check_fn=lambda: True,
        validate_config=lambda config: True,
        source="builtin",
        plugin_name="feishu-platform",
        max_message_length=12345,
        platform_hint="future bundled hint",
        standalone_sender_fn=sentinel_sender,
    )
    registered = []
    monkeypatch.setattr(plugin.platform_registry, "get", lambda name: existing)
    monkeypatch.setattr(plugin.platform_registry, "register", registered.append)

    class FakeManifest:
        name = "feishu-bot-enhancements"

    class FakeContext:
        manifest = FakeManifest()

        def __init__(self):
            self.hooks = []

        def register_hook(self, name, callback):
            self.hooks.append((name, callback))

    context = FakeContext()
    plugin.register(context)

    assert len(registered) == 1
    replacement = registered[0]
    assert replacement.label == "Future Feishu Label"
    assert replacement.max_message_length == 12345
    assert replacement.platform_hint == "future bundled hint"
    assert replacement.standalone_sender_fn is sentinel_sender
    assert replacement.source == "plugin"
    assert replacement.plugin_name == "feishu-bot-enhancements"
    assert replacement.adapter_factory is plugin._build_adapter
    assert context.hooks == [
        ("pre_tool_call", plugin._document_access.inject_document_client),
        ("post_tool_call", plugin._document_access.clear_document_client),
    ]


@pytest.mark.parametrize(
    ("event_key", "expected"),
    [
        ("hermes.model.gpt-5.6-sol", "/model gpt-5.6-sol --session"),
        ("hermes.model.deepseek-v4-pro", "/model deepseek-v4-pro --session"),
        ("hermes.model.status", "/model"),
        ("hermes.reasoning.medium", "/reasoning medium"),
        ("hermes.reasoning.xhigh", "/reasoning xhigh"),
        ("hermes.reasoning.none", "/reasoning none"),
        ("hermes.reasoning.status", "/reasoning"),
    ],
)
def test_dynamic_menu_event_keys_map_to_session_commands(event_key, expected):
    plugin = _load_plugin_module()
    assert plugin.parse_menu_command(event_key) == expected


def test_menu_event_key_rejects_command_injection():
    plugin = _load_plugin_module()
    assert plugin.parse_menu_command("hermes.model.good --global") is None
    assert plugin.parse_menu_command("hermes.reasoning.high\n/restart") is None
    assert plugin.parse_menu_command("other.model.gpt-5.6-sol") is None


def test_resolve_dm_chat_id_uses_latest_matching_root_dm(tmp_path):
    import json

    plugin = _load_plugin_module()
    sessions = {
        "older": {
            "updated_at": "2026-07-09T10:00:00",
            "origin": {
                "platform": "feishu",
                "chat_id": "oc_old",
                "chat_type": "dm",
                "user_id": "ou_target",
                "thread_id": None,
            },
        },
        "thread": {
            "updated_at": "2026-07-11T10:00:00",
            "origin": {
                "platform": "feishu",
                "chat_id": "oc_thread_parent",
                "chat_type": "dm",
                "user_id": "ou_target",
                "thread_id": "om_thread",
            },
        },
        "latest": {
            "updated_at": "2026-07-10T10:00:00",
            "origin": {
                "platform": "feishu",
                "chat_id": "oc_latest",
                "chat_type": "dm",
                "user_id": "ou_target",
                "thread_id": None,
            },
        },
    }
    path = tmp_path / "sessions.json"
    path.write_text(json.dumps(sessions), encoding="utf-8")

    assert plugin.resolve_dm_chat_id("ou_target", path) == "oc_latest"
    assert plugin.resolve_dm_chat_id("ou_unknown", path) is None


def test_resolve_dm_chat_id_prefers_canonical_state_db_without_legacy_json(tmp_path):
    import json

    from hermes_state import SessionDB

    plugin = _load_plugin_module()
    sessions_dir = tmp_path / "sessions"
    state_db_path = tmp_path / "state.db"
    entries = {
        "older": json.dumps(
            {
                "updated_at": "2026-07-09T10:00:00",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_state_old",
                    "chat_type": "dm",
                    "user_id": "ou_target",
                    "thread_id": None,
                },
            }
        ),
        "thread": json.dumps(
            {
                "updated_at": "2026-07-11T10:00:00",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_state_thread",
                    "chat_type": "dm",
                    "user_id": "ou_target",
                    "thread_id": "om_thread",
                },
            }
        ),
        "latest": json.dumps(
            {
                "updated_at": "2026-07-10T10:00:00",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_state_latest",
                    "chat_type": "dm",
                    "user_id": "ou_target",
                    "thread_id": None,
                },
            }
        ),
    }
    db = SessionDB(db_path=state_db_path)
    try:
        db.replace_gateway_routing_entries(entries, scope=str(sessions_dir))
    finally:
        db.close()

    assert not (sessions_dir / "sessions.json").exists()
    assert (
        plugin.resolve_dm_chat_id(
            "ou_target",
            sessions_dir / "sessions.json",
            state_db_path=state_db_path,
        )
        == "oc_state_latest"
    )


def test_resolve_dm_chat_id_prefers_canonical_state_db_over_legacy_json(tmp_path):
    import json

    from hermes_state import SessionDB

    plugin = _load_plugin_module()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    sessions_path = sessions_dir / "sessions.json"
    state_db_path = tmp_path / "state.db"
    sessions_path.write_text(
        json.dumps(
            {
                "legacy": {
                    "updated_at": "2026-07-13T12:00:00",
                    "origin": {
                        "platform": "feishu",
                        "chat_id": "oc_legacy_newer",
                        "chat_type": "dm",
                        "user_id": "ou_target",
                        "thread_id": None,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    entries = {
        "canonical": json.dumps(
            {
                "updated_at": "2026-07-10T10:00:00",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_canonical",
                    "chat_type": "dm",
                    "user_id": "ou_target",
                    "thread_id": None,
                },
            }
        )
    }
    db = SessionDB(db_path=state_db_path)
    try:
        db.replace_gateway_routing_entries(entries, scope=str(sessions_dir.resolve()))
    finally:
        db.close()

    assert (
        plugin.resolve_dm_chat_id(
            "ou_target", sessions_path, state_db_path=state_db_path
        )
        == "oc_canonical"
    )


def test_resolve_dm_chat_id_falls_back_to_legacy_json_when_state_db_is_invalid(
    tmp_path,
):
    import json

    plugin = _load_plugin_module()
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    sessions_path = sessions_dir / "sessions.json"
    state_db_path = tmp_path / "state.db"
    state_db_path.write_bytes(b"not a sqlite database")
    sessions_path.write_text(
        json.dumps(
            {
                "legacy": {
                    "updated_at": "2026-07-10T10:00:00",
                    "origin": {
                        "platform": "feishu",
                        "chat_id": "oc_legacy_fallback",
                        "chat_type": "dm",
                        "user_id": "ou_target",
                        "thread_id": None,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    assert (
        plugin.resolve_dm_chat_id(
            "ou_target", sessions_path, state_db_path=state_db_path
        )
        == "oc_legacy_fallback"
    )


def test_menu_event_routes_as_existing_gateway_command(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    plugin = _load_plugin_module()
    from gateway.platforms.base import MessageType

    adapter = plugin.MenuEnabledFeishuAdapter.__new__(plugin.MenuEnabledFeishuAdapter)
    adapter.platform = plugin._bundled.Platform.FEISHU
    adapter._handle_message_with_guards = AsyncMock()
    adapter._resolve_channel_prompt = lambda chat_id: None
    monkeypatch.setattr(
        plugin._menu_events, "resolve_dm_chat_id", lambda open_id: "oc_target_dm"
    )
    data = SimpleNamespace(
        event=SimpleNamespace(
            event_key="hermes.model.gpt-5.6-luna",
            timestamp=123456,
            operator=SimpleNamespace(
                operator_name="Tester",
                operator_id=SimpleNamespace(open_id="ou_target")
            ),
        )
    )

    asyncio.run(adapter._route_bot_menu_event(data))

    routed = adapter._handle_message_with_guards.await_args.args[0]
    assert routed.text == "/model gpt-5.6-luna --session"
    assert routed.message_type is MessageType.COMMAND
    assert routed.source.chat_id == "oc_target_dm"
    assert routed.source.user_id == "ou_target"
    assert routed.source.chat_type == "dm"
    assert routed.message_id is None
