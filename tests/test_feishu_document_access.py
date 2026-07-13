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
