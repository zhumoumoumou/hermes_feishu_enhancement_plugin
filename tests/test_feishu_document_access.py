"""Behavior tests for document access from normal Feishu bot chats."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_module():
    path = PLUGIN_ROOT / "__init__.py"
    spec = importlib.util.spec_from_file_location("feishu_document_access_plugin", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_adapter_factory_binds_the_live_feishu_adapter(monkeypatch):
    plugin = _load_plugin_module()
    adapter = SimpleNamespace(_client=object())
    monkeypatch.setitem(
        plugin._build_adapter.__globals__,
        "EnhancedFeishuAdapter",
        lambda config: adapter,
    )

    assert plugin._build_adapter(object()) is adapter
    assert plugin._document_access.get_bound_client() is adapter._client


def test_document_hooks_inject_and_clear_client_on_tool_thread(monkeypatch):
    plugin = _load_plugin_module()
    client = object()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    injected = []
    fake_tool = ModuleType("tools.feishu_doc_tool")
    fake_tool.set_client = injected.append
    monkeypatch.setitem(sys.modules, "tools.feishu_doc_tool", fake_tool)

    plugin._document_access.inject_document_client(
        tool_name="feishu_doc_read", args={"doc_token": "doccn_test"}
    )
    plugin._document_access.clear_document_client(tool_name="feishu_doc_read")

    assert injected == [client, None]


def test_injected_client_drives_the_builtin_document_reader():
    plugin = _load_plugin_module()
    from tools import feishu_doc_tool

    class FakeClient:
        def request(self, request):
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps(
                        {"data": {"content": "Document body from Feishu"}}
                    )
                ),
            )

    plugin._document_access.bind_adapter(SimpleNamespace(_client=FakeClient()))
    try:
        plugin._document_access.inject_document_client(tool_name="feishu_doc_read")
        result = feishu_doc_tool._handle_feishu_doc_read(
            {"doc_token": "doccn_test"}
        )
    finally:
        plugin._document_access.clear_document_client(tool_name="feishu_doc_read")

    assert json.loads(result) == {
        "success": True,
        "content": "Document body from Feishu",
    }
    assert feishu_doc_tool.get_client() is None


def test_document_hooks_ignore_unrelated_tools(monkeypatch):
    plugin = _load_plugin_module()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=object()))

    fake_tool = ModuleType("tools.feishu_doc_tool")
    fake_tool.set_client = lambda client: (_ for _ in ()).throw(
        AssertionError("unrelated tool must not touch Feishu document state")
    )
    monkeypatch.setitem(sys.modules, "tools.feishu_doc_tool", fake_tool)

    plugin._document_access.inject_document_client(tool_name="web_search", args={})
    plugin._document_access.clear_document_client(tool_name="web_search")


def test_disconnected_adapter_does_not_expose_stale_client():
    plugin = _load_plugin_module()
    plugin._document_access.bind_adapter(
        SimpleNamespace(_client=object(), _running=False)
    )

    assert plugin._document_access.get_bound_client() is None


def test_document_create_edit_and_share_call_official_apis():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.requests = []

        def request(self, request):
            self.requests.append(request)
            if request.uri.endswith("/documents"):
                data = {
                    "document": {
                        "document_id": "doxcn_created",
                        "revision_id": 1,
                        "title": "Created by bot",
                    }
                }
            elif request.uri.endswith("/members"):
                data = {
                    "member": {
                        "member_id": "ou_recipient",
                        "member_type": "openid",
                        "perm": "edit",
                    }
                }
            else:
                data = {"document_revision_id": 2}
            return SimpleNamespace(
                code=0,
                msg="success",
                raw=SimpleNamespace(content=json.dumps({"data": data})),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    created = json.loads(
        plugin._document_tools.handle_doc_create({"title": "Created by bot"})
    )
    appended = json.loads(
        plugin._document_tools.handle_doc_append_text(
            {"document_id": "doxcn_created", "content": "First paragraph"}
        )
    )
    updated = json.loads(
        plugin._document_tools.handle_doc_update_text(
            {
                "document_id": "doxcn_created",
                "block_id": "doxcn_block",
                "content": "Revised paragraph",
            }
        )
    )
    shared = json.loads(
        plugin._document_tools.handle_doc_share(
            {
                "document_id": "doxcn_created",
                "member_id": "ou_recipient",
                "permission": "edit",
            }
        )
    )

    assert created["document"]["document_id"] == "doxcn_created"
    assert appended["success"] is True
    assert updated["success"] is True
    assert shared["member"]["member_id"] == "ou_recipient"

    create, append, update, share = client.requests
    assert create.uri == "/open-apis/docx/v1/documents"
    assert create.body == {"title": "Created by bot"}
    assert append.paths == {
        "document_id": "doxcn_created",
        "block_id": "doxcn_created",
    }
    assert append.body == {
        "index": -1,
        "children": [
            {
                "block_type": 2,
                "text": {
                    "elements": [
                        {
                            "text_run": {
                                "content": "First paragraph",
                                "text_element_style": {},
                            }
                        }
                    ]
                },
            }
        ],
    }
    assert update.paths["block_id"] == "doxcn_block"
    assert update.body["update_text_elements"]["elements"][0]["text_run"][
        "content"
    ] == "Revised paragraph"
    assert share.uri == "/open-apis/drive/v1/permissions/:token/members"
    assert share.queries == [("type", "docx"), ("need_notification", "false")]
    assert share.body == {
        "member_type": "openid",
        "member_id": "ou_recipient",
        "perm": "edit",
        "type": "user",
    }


def test_document_share_defaults_to_current_feishu_requester(monkeypatch):
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_body = None

        def request(self, request):
            self.request_body = request.body
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"member": request.body}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    monkeypatch.setattr(
        plugin._document_tools,
        "resolve_session_open_id",
        lambda session_id: "ou_current" if session_id == "session-1" else None,
    )

    result = json.loads(
        plugin._document_tools.handle_doc_share(
            {"document_id": "doxcn_created"}, session_id="session-1"
        )
    )

    assert result["success"] is True
    assert client.request_body["member_id"] == "ou_current"


def test_resolve_session_open_id_uses_canonical_gateway_index(tmp_path):
    plugin = _load_plugin_module()
    from hermes_state import SessionDB

    sessions_dir = tmp_path / "sessions"
    state_db_path = tmp_path / "state.db"
    entries = {
        "feishu:dm": json.dumps(
            {
                "session_key": "feishu:dm",
                "session_id": "session-1",
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_chat",
                    "chat_type": "dm",
                    "user_id": "ou_current",
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
        plugin._document_tools.resolve_session_open_id(
            "session-1",
            sessions_dir / "sessions.json",
            state_db_path=state_db_path,
        )
        == "ou_current"
    )
