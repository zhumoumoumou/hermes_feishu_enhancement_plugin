"""Behavior tests for document access from normal Feishu bot chats."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import threading
import time

from lark_oapi.core.enum import HttpMethod


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


def test_enhanced_adapter_applies_bounded_sdk_request_timeout(monkeypatch):
    plugin = _load_plugin_module()
    client = SimpleNamespace(_config=SimpleNamespace(timeout=None))
    monkeypatch.setattr(
        plugin.BundledFeishuAdapter,
        "_build_lark_client",
        lambda self, domain: client,
    )
    adapter = object.__new__(plugin.EnhancedFeishuAdapter)
    adapter.config = SimpleNamespace(extra={"request_timeout_seconds": 12})

    assert adapter._build_lark_client("https://open.feishu.cn") is client
    assert client._config.timeout == 12

    adapter.config.extra["request_timeout_seconds"] = 0
    adapter._build_lark_client("https://open.feishu.cn")
    assert client._config.timeout == 60


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


def test_document_append_supports_heading_and_whole_run_style():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_value = None

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 2}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_tools.handle_doc_append_text(
            {
                "document_id": "doxcn_created",
                "content": "Summary",
                "heading_level": 2,
                "bold": True,
                "text_color": 5,
            }
        )
    )

    assert result["success"] is True
    assert client.request_value.body == {
        "index": -1,
        "children": [
            {
                "block_type": 4,
                "heading2": {
                    "elements": [
                        {
                            "text_run": {
                                "content": "Summary",
                                "text_element_style": {
                                    "bold": True,
                                    "text_color": 5,
                                },
                            }
                        }
                    ]
                },
            }
        ],
    }


def test_document_update_supports_mixed_rich_text_segments():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_value = None

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 3}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_tools.handle_doc_update_text(
            {
                "document_id": "doxcn_created",
                "block_id": "doxcn_block",
                "elements": [
                    {"content": "Important", "bold": True},
                    {
                        "content": " details",
                        "italic": True,
                        "background_color": 3,
                        "link_url": "https://example.com/docs?a=1&b=2",
                    },
                ],
            }
        )
    )

    assert result["success"] is True
    assert client.request_value.body == {
        "update_text_elements": {
            "elements": [
                {
                    "text_run": {
                        "content": "Important",
                        "text_element_style": {"bold": True},
                    }
                },
                {
                    "text_run": {
                        "content": " details",
                        "text_element_style": {
                            "italic": True,
                            "background_color": 3,
                            "link": {
                                "url": (
                                    "https%3A%2F%2Fexample.com%2Fdocs%3F"
                                    "a%3D1%26b%3D2"
                                )
                            },
                        },
                    }
                },
            ]
        }
    }


def test_document_text_supports_equations_mentions_and_reminders():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_value = None

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 4}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    result = json.loads(
        plugin._document_tools.handle_doc_append_text(
            {
                "document_id": "doxcn_created",
                "elements": [
                    {"content": "Energy: ", "bold": True},
                    {"type": "equation", "content": r"E=mc^2"},
                    {"type": "mention_user", "user_id": "ou_alice"},
                    {
                        "type": "mention_doc",
                        "token": "doxcn_other",
                        "obj_type": 22,
                        "url": "https://example.feishu.cn/docx/doxcn_other?a=1",
                    },
                    {
                        "type": "reminder",
                        "create_user_id": "ou_alice",
                        "expire_time": 1_800_000_000_000,
                        "notify_time": 1_799_999_000_000,
                        "is_notify": True,
                    },
                ],
            }
        )
    )

    assert result["success"] is True
    elements = client.request_value.body["children"][0]["text"]["elements"]
    assert elements[1] == {
        "equation": {"content": r"E=mc^2", "text_element_style": {}}
    }
    assert elements[2]["mention_user"]["user_id"] == "ou_alice"
    assert elements[3]["mention_doc"]["url"] == (
        "https%3A%2F%2Fexample.feishu.cn%2Fdocx%2Fdoxcn_other%3Fa%3D1"
    )
    assert elements[4]["reminder"]["is_notify"] is True
    assert elements[4]["reminder"]["expire_time"] == "1800000000000"


def test_document_append_supports_list_code_quote_and_todo_blocks():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.requests = []

        def request(self, request):
            self.requests.append(request)
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 5}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    cases = [
        {"block_type": "bullet", "content": "Bullet"},
        {"block_type": "ordered", "content": "Ordered"},
        {
            "block_type": "code",
            "content": "print('ok')",
            "language": 49,
            "wrap": True,
        },
        {"block_type": "quote", "content": "Quote"},
        {"block_type": "todo", "content": "Done", "done": True},
    ]
    for case in cases:
        result = json.loads(
            plugin._document_tools.handle_doc_append_text(
                {"document_id": "doxcn_created", **case}
            )
        )
        assert result["success"] is True

    assert [request.body["children"][0]["block_type"] for request in client.requests] == [
        12,
        13,
        14,
        15,
        17,
    ]
    assert client.requests[2].body["children"][0]["code"]["style"] == {
        "language": 49,
        "wrap": True,
    }
    assert client.requests[4].body["children"][0]["todo"]["style"] == {
        "done": True
    }


def test_document_insert_blocks_batches_text_and_structured_objects():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_value = None

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                raw=SimpleNamespace(
                    content=json.dumps(
                        {"data": {"document_revision_id": 6, "children": []}}
                    )
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    result = json.loads(
        plugin._document_objects.handle_doc_insert_blocks(
            {
                "document_id": "doxcn_created",
                "blocks": [
                    {"type": "heading2", "content": "Objects"},
                    {
                        "type": "text",
                        "elements": [
                            {"content": "Area: "},
                            {"type": "equation", "content": r"A=\pi r^2"},
                        ],
                    },
                    {"type": "divider"},
                    {
                        "type": "table",
                        "data": {"property": {"row_size": 2, "column_size": 3}},
                    },
                    {"type": "callout", "data": {"background_color": 4}},
                    {
                        "type": "iframe",
                        "data": {
                            "component": {
                                "type": 1,
                                "url": "https://example.com/embed?a=1&b=2",
                            }
                        },
                    },
                    {"type": "board", "data": {"align": 2, "width": 600}},
                ],
            }
        )
    )

    assert result["success"] is True
    request = client.request_value
    assert request.uri.endswith("/blocks/:block_id/children")
    children = request.body["children"]
    assert [child["block_type"] for child in children] == [4, 2, 22, 31, 19, 26, 43]
    assert children[1]["text"]["elements"][1]["equation"]["content"] == r"A=\pi r^2"
    assert children[3]["table"]["property"] == {"row_size": 2, "column_size": 3}
    assert children[5]["iframe"]["component"]["url"] == (
        "https%3A%2F%2Fexample.com%2Fembed%3Fa%3D1%26b%3D2"
    )


def test_document_insert_blocks_builds_every_declared_object_type():
    plugin = _load_plugin_module()
    cases = {
        "bitable": {},
        "callout": {},
        "chat_card": {"chat_id": "oc_chat"},
        "divider": {},
        "grid": {"column_size": 2},
        "iframe": {"component": {"type": 1, "url": "https://example.com"}},
        "isv": {"component_id": "component", "component_type_id": "type"},
        "sheet": {"row_size": 1, "column_size": 1},
        "table": {"property": {"row_size": 1, "column_size": 1}},
        "quote_container": {},
        "add_ons": {
            "component_id": "component",
            "component_type_id": "blk_type",
        },
        "wiki_catalog": {},
        "board": {},
        "link_preview": {
            "url": "https://applink.feishu.cn/client/message/link/open?token=x",
            "url_type": "MessageLink",
        },
        "sub_page_list": {"wiki_token": "w" * 27},
    }

    for position, (block_type, data) in enumerate(cases.items()):
        block, error = plugin._document_objects._build_block(
            {"type": block_type, "data": data}, position
        )
        assert error is None
        assert block["block_type"] == plugin._document_objects._OBJECT_BLOCK_TYPES[
            block_type
        ]
        assert block_type in block

    unsupported, error = plugin._document_objects._build_block(
        {"type": "okr", "data": {"okr_id": "123"}}, 99
    )
    assert unsupported is None
    assert "unsupported" in error


def test_document_insert_blocks_rejects_large_dynamic_resource_batch():
    plugin = _load_plugin_module()
    result = json.loads(
        plugin._document_objects.handle_doc_insert_blocks(
            {
                "document_id": "doxcn_created",
                "blocks": [
                    {
                        "type": "link_preview",
                        "data": {
                            "url": f"https://example.com/document/{index}",
                            "url_type": "Undefined",
                        },
                    }
                    for index in range(6)
                ],
            }
        )
    )

    assert "use at most 5 per request" in result["error"]
    assert "rich-text links" in result["error"]


def test_document_insert_blocks_enforces_published_resource_limits():
    plugin = _load_plugin_module()
    result = json.loads(
        plugin._document_objects.handle_doc_insert_blocks(
            {
                "document_id": "doxcn_created",
                "blocks": [
                    {"type": "sheet", "data": {"row_size": 1, "column_size": 1}}
                    for _ in range(6)
                ],
            }
        )
    )

    assert "6 sheet resources" in result["error"]
    assert "at most 5 per request" in result["error"]


def test_document_insert_blocks_explains_server_resource_limit():
    plugin = _load_plugin_module()

    class FakeClient:
        def request(self, request):
            return SimpleNamespace(
                code=1770035,
                msg="resource count exceed limit",
                raw=SimpleNamespace(content=json.dumps({"data": {}})),
            )

    plugin._document_access.bind_adapter(SimpleNamespace(_client=FakeClient()))
    result = json.loads(
        plugin._document_objects.handle_doc_insert_blocks(
            {
                "document_id": "doxcn_created",
                "blocks": [
                    {
                        "type": "link_preview",
                        "data": {
                            "url": "https://example.com/document/1",
                            "url_type": "Undefined",
                        },
                    }
                ],
            }
        )
    )

    assert "1770035" in result["error"]
    assert "do not retry the unchanged batch" in result["error"]


def test_document_image_data_url_uses_a_safe_inferred_filename(monkeypatch):
    plugin = _load_plugin_module()

    async def fake_image_resolver(source, context):
        assert source.startswith("data:image/png;base64,")
        return SimpleNamespace(data=b"png", mime="image/png", origin="data")

    from tools import image_source

    monkeypatch.setattr(image_source, "resolve_image_source", fake_image_resolver)
    data, mime, file_name = asyncio.run(
        plugin._document_objects._resolve_media_source(
            "image",
            "data:image/png;base64,cG5n",
            task_id="task-1",
            cwd="/workspace",
        )
    )

    assert data == b"png"
    assert mime == "image/png"
    assert file_name == "image.png"


def test_document_insert_media_creates_uploads_and_binds_image_and_file(monkeypatch):
    plugin = _load_plugin_module()

    async def fake_resolve(kind, source, *, task_id, cwd):
        assert source == "/workspace/input.bin"
        return b"media-bytes", "application/octet-stream", f"asset.{kind}"

    monkeypatch.setattr(plugin._document_objects, "_resolve_media_source", fake_resolve)

    class FakeMedia:
        def __init__(self, uploads):
            self.uploads = uploads

        def upload_all(self, request):
            self.uploads.append(request)
            return SimpleNamespace(
                code=0,
                msg="success",
                data=SimpleNamespace(file_token="file_token"),
            )

    class FakeClient:
        def __init__(self, kind):
            self.kind = kind
            self.requests = []
            self.uploads = []
            self.drive = SimpleNamespace(
                v1=SimpleNamespace(media=FakeMedia(self.uploads))
            )

        def request(self, request):
            self.requests.append(request)
            if request.uri.endswith("/children"):
                child = (
                    {"block_id": "image_block"}
                    if self.kind == "image"
                    else {"block_id": "view_block", "children": ["file_block"]}
                )
                data = {"children": [child]}
            else:
                data = {"document_revision_id": 7}
            return SimpleNamespace(
                code=0,
                msg="success",
                raw=SimpleNamespace(content=json.dumps({"data": data})),
            )

    for kind, expected_block_id in (("image", "image_block"), ("file", "file_block")):
        client = FakeClient(kind)
        plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
        display = (
            {"image_align": 2, "caption": "Diagram"}
            if kind == "image"
            else {"file_view_type": 2}
        )
        result = json.loads(
            asyncio.run(
                plugin._document_objects.handle_doc_insert_media(
                    {
                        "document_id": "doxcn_created",
                        "kind": kind,
                        "source": "/workspace/input.bin",
                        **display,
                    },
                    task_id="task-1",
                    cwd="/workspace",
                )
            )
        )

        assert result["success"] is True
        assert result["block_id"] == expected_block_id
        created = client.requests[0].body["children"][0]
        if kind == "image":
            assert created == {
                "block_type": 27,
                "image": {"align": 2, "caption": {"content": "Diagram"}},
            }
        else:
            assert created == {
                "block_type": 23,
                "file": {"token": "", "view_type": 2},
            }
        upload_body = client.uploads[0].request_body
        assert upload_body.parent_type == f"docx_{kind}"
        assert upload_body.parent_node == expected_block_id
        assert upload_body.size == len(b"media-bytes")
        assert json.loads(upload_body.extra) == {"drive_route_token": "doxcn_created"}
        operation = "replace_image" if kind == "image" else "replace_file"
        assert client.requests[1].body == {operation: {"token": "file_token"}}


def test_document_update_blocks_batches_rich_text_and_structural_edits():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_value = None

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                msg="success",
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 8}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    result = json.loads(
        asyncio.run(
            plugin._document_objects.handle_doc_update_blocks(
                {
                    "document_id": "doxcn_created",
                    "updates": [
                        {
                            "block_id": "text_block",
                            "operation": "update_text",
                            "data": {
                                "elements": [
                                    {"content": "Area: ", "bold": True},
                                    {"type": "equation", "content": r"A=\pi r^2"},
                                ],
                                "style": {"align": 2, "folded": False},
                            },
                        },
                        {
                            "block_id": "table_block",
                            "operation": "merge_table_cells",
                            "data": {
                                "row_start_index": 0,
                                "row_end_index": 1,
                                "column_start_index": 0,
                                "column_end_index": 2,
                            },
                        },
                    ],
                }
            )
        )
    )

    assert result["success"] is True
    assert result["updated_block_count"] == 2
    assert client.request_value.uri.endswith("/blocks/batch_update")
    requests = client.request_value.body["requests"]
    assert requests[0]["update_text"]["style"] == {"align": 2, "folded": False}
    assert requests[0]["update_text"]["fields"] == [1, 3]
    assert requests[0]["update_text"]["elements"][1] == {
        "equation": {"content": r"A=\pi r^2", "text_element_style": {}}
    }
    assert requests[1]["merge_table_cells"] == {
        "row_start_index": 0,
        "row_end_index": 1,
        "column_start_index": 0,
        "column_end_index": 2,
    }


def test_document_update_builder_covers_every_declared_operation():
    plugin = _load_plugin_module()
    cases = {
        "update_text_elements": {"content": "new"},
        "update_text_style": {"style": {"align": 1}},
        "update_text": {"content": "done", "style": {"done": True}},
        "update_table_property": {"header_row": True},
        "insert_table_row": {"row_index": -1},
        "insert_table_column": {"column_index": -1},
        "delete_table_rows": {"row_start_index": 0, "row_end_index": 1},
        "delete_table_columns": {
            "column_start_index": 0,
            "column_end_index": 1,
        },
        "merge_table_cells": {
            "row_start_index": 0,
            "row_end_index": 1,
            "column_start_index": 0,
            "column_end_index": 1,
        },
        "unmerge_table_cells": {"row_index": 0, "column_index": 0},
        "insert_grid_column": {"column_index": 1},
        "delete_grid_column": {"column_index": 0},
        "update_grid_column_width_ratio": {"width_ratios": [40, 60]},
        "replace_image": {"token": "image_token", "caption": "Updated"},
        "replace_file": {"token": "file_token"},
        "update_task": {"folded": True},
    }

    assert set(cases) == set(plugin._document_objects._UPDATE_OPERATIONS)
    for index, (operation, data) in enumerate(cases.items()):
        request, error = plugin._document_objects._build_update_request(
            {"block_id": f"block_{index}", "operation": operation, "data": data},
            index,
        )
        assert error is None
        assert request["block_id"] == f"block_{index}"
        assert operation in request
    assert cases["replace_image"]["caption"] == "Updated"


def test_document_update_blocks_uploads_and_replaces_existing_media(monkeypatch):
    plugin = _load_plugin_module()

    async def fake_resolve(kind, source, *, task_id, cwd):
        assert kind == "image"
        assert source == "https://example.com/new.png"
        return b"new-image", "image/png", "new.png"

    monkeypatch.setattr(plugin._document_objects, "_resolve_media_source", fake_resolve)

    class FakeMedia:
        def upload_all(self, request):
            return SimpleNamespace(
                code=0,
                msg="success",
                data=SimpleNamespace(file_token="new_image_token"),
            )

    class FakeClient:
        def __init__(self):
            self.request_value = None
            self.drive = SimpleNamespace(v1=SimpleNamespace(media=FakeMedia()))

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                msg="success",
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 9}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    result = json.loads(
        asyncio.run(
            plugin._document_objects.handle_doc_update_blocks(
                {
                    "document_id": "doxcn_created",
                    "updates": [
                        {
                            "block_id": "image_block",
                            "operation": "replace_image",
                            "source": "https://example.com/new.png",
                            "data": {"align": 3, "caption": "New image"},
                        }
                    ],
                },
                task_id="task-1",
                cwd="/workspace",
            )
        )
    )

    assert result["success"] is True
    assert result["uploaded_media"][0]["file_token"] == "new_image_token"
    request = client.request_value.body["requests"][0]
    assert request == {
        "block_id": "image_block",
        "replace_image": {
            "align": 3,
            "caption": {"content": "New image"},
            "token": "new_image_token",
        },
    }


def test_document_update_blocks_uploads_media_with_bounded_concurrency(monkeypatch):
    plugin = _load_plugin_module()
    active = 0
    max_active = 0
    lock = threading.Lock()

    async def fake_resolve(kind, source, *, task_id, cwd):
        return source.encode(), "image/png", f"{source}.png"

    def fake_upload(client, *, data, file_name, parent_type, block_id, document_id):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.04)
        with lock:
            active -= 1
        return 0, "success", {"file_token": f"token_{block_id}"}

    monkeypatch.setattr(plugin._document_objects, "_resolve_media_source", fake_resolve)
    monkeypatch.setattr(plugin._document_objects, "_upload_media", fake_upload)
    monkeypatch.setattr(plugin._document_objects, "media_upload_concurrency", lambda: 2)
    client = _RecordingClient([{"document_revision_id": 9}])
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        asyncio.run(
            plugin._document_objects.handle_doc_update_blocks(
                {
                    "document_id": "doxcn_created",
                    "updates": [
                        {
                            "block_id": f"image_{index}",
                            "operation": "replace_image",
                            "source": f"image-{index}",
                            "data": {},
                        }
                        for index in range(4)
                    ],
                }
            )
        )
    )

    assert result["success"] is True
    assert max_active == 2
    assert [item["block_id"] for item in result["uploaded_media"]] == [
        "image_0",
        "image_1",
        "image_2",
        "image_3",
    ]


def test_document_delete_blocks_uses_parent_and_exclusive_index_range():
    plugin = _load_plugin_module()

    class FakeClient:
        def __init__(self):
            self.request_value = None

        def request(self, request):
            self.request_value = request
            return SimpleNamespace(
                code=0,
                msg="success",
                raw=SimpleNamespace(
                    content=json.dumps({"data": {"document_revision_id": 10}})
                ),
            )

    client = FakeClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    result = json.loads(
        plugin._document_objects.handle_doc_delete_blocks(
            {
                "document_id": "doxcn_created",
                "parent_block_id": "container_block",
                "start_index": 2,
                "end_index": 5,
            }
        )
    )

    assert result["success"] is True
    assert result["deleted_count"] == 3
    assert client.request_value.uri.endswith("/children/batch_delete")
    assert client.request_value.paths == {
        "document_id": "doxcn_created",
        "block_id": "container_block",
    }
    assert client.request_value.body == {"start_index": 2, "end_index": 5}


def test_document_update_and_delete_reject_ambiguous_or_invalid_ranges():
    plugin = _load_plugin_module()
    duplicate = json.loads(
        asyncio.run(
            plugin._document_objects.handle_doc_update_blocks(
                {
                    "document_id": "doxcn_created",
                    "updates": [
                        {
                            "block_id": "same",
                            "operation": "insert_table_row",
                            "data": {"row_index": -1},
                        },
                        {
                            "block_id": "same",
                            "operation": "insert_table_column",
                            "data": {"column_index": -1},
                        },
                    ],
                }
            )
        )
    )
    invalid_delete = json.loads(
        plugin._document_objects.handle_doc_delete_blocks(
            {"document_id": "doxcn_created", "start_index": 2, "end_index": 2}
        )
    )

    assert "only one update per block" in duplicate["error"]
    assert "less than" in invalid_delete["error"]


def test_document_rich_text_validation_rejects_invalid_input():
    plugin = _load_plugin_module()

    bad_heading = json.loads(
        plugin._document_tools.handle_doc_append_text(
            {
                "document_id": "doxcn_created",
                "content": "Bad heading",
                "heading_level": 10,
            }
        )
    )
    empty_elements = json.loads(
        plugin._document_tools.handle_doc_update_text(
            {
                "document_id": "doxcn_created",
                "block_id": "doxcn_block",
                "elements": [],
            }
        )
    )
    bad_style = json.loads(
        plugin._document_tools.handle_doc_update_text(
            {
                "document_id": "doxcn_created",
                "block_id": "doxcn_block",
                "content": "Bad style",
                "bold": "yes",
            }
        )
    )

    assert "heading_level" in bad_heading["error"]
    assert "elements" in empty_elements["error"]
    assert "bold" in bad_style["error"]


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


class _RecordingClient:
    def __init__(self, responses=None):
        self.requests = []
        self.responses = list(responses or [])

    def request(self, request):
        self.requests.append(request)
        data = self.responses.pop(0) if self.responses else {"ok": True}
        return SimpleNamespace(
            code=0,
            msg="success",
            raw=SimpleNamespace(content=json.dumps({"data": data})),
        )


def test_rich_text_update_preserves_comments_and_inline_files():
    plugin = _load_plugin_module()
    client = _RecordingClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = asyncio.run(
        plugin._document_objects.handle_doc_update_blocks(
            {
                "document_id": "doxcn_doc",
                "updates": [
                    {
                        "block_id": "doxcn_text",
                        "operation": "update_text_elements",
                        "data": {
                            "elements": [
                                {
                                    "content": "reviewed",
                                    "bold": True,
                                    "comment_ids": ["comment_1"],
                                },
                                {
                                    "type": "inline_file",
                                    "file_token": "boxcn_file",
                                    "source_block_id": "doxcn_source",
                                },
                            ]
                        },
                    }
                ],
            }
        )
    )

    assert json.loads(result)["success"] is True
    elements = client.requests[0].body["requests"][0]["update_text_elements"][
        "elements"
    ]
    assert elements[0]["text_run"]["text_element_style"] == {
        "bold": True,
        "comment_ids": ["comment_1"],
    }
    assert elements[1] == {
        "file": {
            "file_token": "boxcn_file",
            "source_block_id": "doxcn_source",
            "text_element_style": {},
        }
    }

    rejected = json.loads(
        plugin._document_tools.handle_doc_append_text(
            {
                "document_id": "doxcn_doc",
                "elements": [{"type": "inline_file", "file_token": "boxcn_file"}],
            }
        )
    )
    assert "only preserve or move" in rejected["error"]


def test_media_property_update_reuses_the_existing_token():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [{"block": {"image": {"token": "img_existing"}}}, {"revision_id": 3}]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = asyncio.run(
        plugin._document_objects.handle_doc_update_blocks(
            {
                "document_id": "doxcn_doc",
                "updates": [
                    {
                        "block_id": "doxcn_image",
                        "operation": "replace_image",
                        "data": {"width": 640, "align": 2},
                    }
                ],
            }
        )
    )

    assert json.loads(result)["success"] is True
    assert client.requests[0].http_method == HttpMethod.GET
    assert client.requests[0].uri.endswith("/blocks/:block_id")
    assert client.requests[1].body["requests"] == [
        {
            "block_id": "doxcn_image",
            "replace_image": {"token": "img_existing", "width": 640, "align": 2},
        }
    ]


def test_structured_block_inspection_follows_pagination():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {"items": [{"block_id": "a"}], "has_more": True, "page_token": "p2"},
            {"items": [{"block_id": "b"}], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_management.handle_doc_get_blocks(
            {"document_id": "doxcn_doc", "fetch_all": True, "page_size": 100}
        )
    )

    assert result["items"] == [{"block_id": "a"}, {"block_id": "b"}]
    assert result["page_count"] == 2
    assert ("page_token", "p2") in client.requests[1].queries


def test_document_comments_and_lifecycle_request_shapes():
    plugin = _load_plugin_module()
    client = _RecordingClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    comment_calls = [
        {"document_id": "doc", "action": "list"},
        {"document_id": "doc", "action": "create", "content": "first"},
        {
            "document_id": "doc",
            "action": "reply",
            "comment_id": "c1",
            "elements": [{"type": "person", "user_id": "ou_1"}],
        },
        {
            "document_id": "doc",
            "action": "update_reply",
            "comment_id": "c1",
            "reply_id": "r1",
            "content": "fixed",
        },
        {
            "document_id": "doc",
            "action": "delete_reply",
            "comment_id": "c1",
            "reply_id": "r1",
        },
        {
            "document_id": "doc",
            "action": "set_solved",
            "comment_id": "c1",
            "is_solved": True,
        },
    ]
    for args in comment_calls:
        assert json.loads(plugin._document_management.handle_doc_comments(args))[
            "success"
        ]

    assert [request.http_method for request in client.requests[:6]] == [
        HttpMethod.GET,
        HttpMethod.POST,
        HttpMethod.POST,
        HttpMethod.PUT,
        HttpMethod.DELETE,
        HttpMethod.PATCH,
    ]
    assert client.requests[1].body == {
        "reply_list": {
            "replies": [
                {
                    "content": {
                        "elements": [
                            {"type": "text_run", "text_run": {"text": "first"}}
                        ]
                    }
                }
            ]
        }
    }
    assert client.requests[2].body == {
        "content": {
            "elements": [{"type": "person", "person": {"user_id": "ou_1"}}]
        }
    }

    lifecycle_calls = [
        {"document_id": "doc", "action": "rename", "title": "Renamed"},
        {
            "document_id": "doc",
            "action": "copy",
            "title": "Copied",
            "folder_token": "fld_copy",
        },
        {"document_id": "doc", "action": "move", "folder_token": "fld_move"},
        {"document_id": "doc", "action": "delete"},
    ]
    for args in lifecycle_calls:
        assert json.loads(plugin._document_management.handle_doc_manage(args))["success"]

    rename, copy, move, delete = client.requests[6:]
    assert rename.http_method == HttpMethod.PATCH
    assert rename.paths == {"document_id": "doc", "block_id": "doc"}
    assert copy.uri.endswith("/:file_token/copy")
    assert copy.body == {"name": "Copied", "type": "docx", "folder_token": "fld_copy"}
    assert move.body == {"type": "docx", "folder_token": "fld_move"}
    assert delete.http_method == HttpMethod.DELETE
    assert ("type", "docx") in delete.queries


def test_sheet_edit_operation_matrix():
    plugin = _load_plugin_module()
    client = _RecordingClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    operations = [
        ("write_values", {"range": "Sheet1!A1:B2", "values": [[1, 2], [3, 4]]}),
        ("append_values", {"range": "Sheet1!A:B", "values": [[5, 6]]}),
        ("set_style", {"range": "Sheet1!A1", "style": {"font": {"bold": True}}}),
        ("add_dimensions", {"sheet_id": "s1", "major_dimension": "ROWS", "length": 2}),
        (
            "insert_dimensions",
            {"sheet_id": "s1", "major_dimension": "ROWS", "start_index": 1, "end_index": 3},
        ),
        (
            "delete_dimensions",
            {"sheet_id": "s1", "major_dimension": "COLUMNS", "start_index": 2, "end_index": 4},
        ),
        (
            "move_dimensions",
            {
                "sheet_id": "s1",
                "major_dimension": "ROWS",
                "start_index": 0,
                "end_index": 2,
                "destination_index": 4,
            },
        ),
        ("batch_sheets", {"requests": [{"addSheet": {"properties": {"title": "New"}}}]}),
    ]
    for operation, data in operations:
        result = json.loads(
            plugin._document_domains.handle_sheet_edit(
                {"spreadsheet_token": "sht", "operation": operation, "data": data}
            )
        )
        assert result["success"] is True

    assert [request.http_method for request in client.requests] == [
        HttpMethod.PUT,
        HttpMethod.POST,
        HttpMethod.PUT,
        HttpMethod.POST,
        HttpMethod.POST,
        HttpMethod.DELETE,
        HttpMethod.POST,
        HttpMethod.POST,
    ]
    assert client.requests[0].body["valueRange"]["range"] == "Sheet1!A1:B2"
    assert client.requests[6].uri.endswith("/:sheet_id/move_dimension")
    assert client.requests[6].body == {
        "source": {"major_dimension": "ROWS", "start_index": 0, "end_index": 2},
        "destination_index": 4,
    }


def test_embed_bitable_defers_gantt_until_fields_and_records_exist():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {
                "document_revision_id": 12,
                "children": [
                    {
                        "block_id": "blk_bitable",
                        "block_type": 18,
                        "bitable": {
                            "token": "app_embedded_tbl_default",
                            "view_type": 1,
                        },
                    }
                ],
            },
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_doc_embed_bitable(
            {
                "document_id": "doxcn_doc",
                "view_type": "gantt",
                "view_name": "Project timeline",
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "needs_setup"
    assert result["block_id"] == "blk_bitable"
    assert result["app_token"] == "app_embedded"
    assert result["table_id"] == "tbl_default"
    assert result["embedded_view_type"] == "grid"
    assert result["requires_manual_view_switch"] is True
    assert "intentionally not created against an empty table" in result["setup_error"]
    assert [request.http_method for request in client.requests] == [HttpMethod.POST]
    assert client.requests[0].body["children"] == [
        {"block_type": 18, "bitable": {"view_type": 1}}
    ]


def test_embed_bitable_creates_native_kanban_without_extra_view():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {
                "children": [
                    {
                        "block_id": "blk_kanban",
                        "bitable": {"token": "app_kanban", "view_type": 2},
                    }
                ]
            },
            {"items": [{"table_id": "tbl_kanban", "name": "Table 1"}]},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_doc_embed_bitable(
            {"document_id": "doxcn_doc", "view_type": "kanban"}
        )
    )

    assert result["status"] == "needs_manual_configuration"
    assert result["embedded_view_type"] == "kanban"
    assert result["requires_manual_view_switch"] is False
    assert "created_view" not in result
    assert len(client.requests) == 2
    assert client.requests[0].body["children"][0]["bitable"]["view_type"] == 2


def test_embed_bitable_reports_partial_declarative_setup_without_duplicate_retry():
    plugin = _load_plugin_module()

    class PartialClient:
        def __init__(self):
            self.requests = []

        def request(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                code, msg, data = 0, "success", {
                    "children": [
                        {
                            "block_id": "blk_partial",
                            "bitable": {
                                "token": "app_partial_tbl_partial",
                                "view_type": 1,
                            },
                        }
                    ]
                }
            elif request.uri.endswith("/fields"):
                code, msg, data = 0, "success", {
                    "items": [
                        {
                            "field_id": "fld_primary",
                            "field_name": "Text",
                            "type": 1,
                            "is_primary": True,
                        }
                    ]
                }
            else:
                code, msg, data = 0, "success", {"items": []}
            return SimpleNamespace(
                code=code,
                msg=msg,
                raw=SimpleNamespace(content=json.dumps({"data": data})),
            )

    client = PartialClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    result = json.loads(
        plugin._document_domains.handle_doc_embed_bitable(
            {"document_id": "doxcn_doc", "view_type": "gantt", "setup": {}}
        )
    )

    assert result["success"] is True
    assert result["status"] == "partial"
    assert result["block_id"] == "blk_partial"
    assert result["app_token"] == "app_partial"
    assert "Do not create another block" in result["setup_error"]
    assert sum(request.uri.endswith("/children") for request in client.requests) == 1


def test_embed_bitable_can_create_and_populate_grid_in_one_call():
    plugin = _load_plugin_module()
    task_field = {
        "field_id": "fld_task",
        "field_name": "Task",
        "type": 1,
        "is_primary": True,
    }
    client = _RecordingClient(
        [
            {
                "children": [
                    {
                        "block_id": "blk_grid",
                        "bitable": {"token": "app_grid_tbl_grid", "view_type": 1},
                    }
                ]
            },
            {
                "items": [
                    {
                        "field_id": "fld_primary",
                        "field_name": "Text",
                        "type": 1,
                        "is_primary": True,
                    }
                ],
                "has_more": False,
            },
            {"field": task_field},
            {"items": [task_field], "has_more": False},
            {"items": [{"record_id": "blank", "fields": {}}], "has_more": False},
            {},
            {},
            {"items": [], "has_more": False},
            {
                "items": [
                    {"record_id": "rec1", "fields": {"Task": "First task"}}
                ],
                "has_more": False,
            },
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_doc_embed_bitable(
            {
                "document_id": "doc",
                "view_type": "grid",
                "setup": {
                    "fields": [{"field_name": "Task", "type": 1, "primary": True}],
                    "records": [{"fields": {"Task": "First task"}}],
                    "key_field": "Task",
                },
            }
        )
    )

    assert result["status"] == "complete"
    assert result["app_token"] == "app_grid"
    assert result["table_id"] == "tbl_grid"
    assert result["sync"]["created_records"] == 1
    assert result["sync"]["deleted_blank_records"] == 1
    assert sum(request.uri.endswith("/children") for request in client.requests) == 1


def test_bitable_read_operations_resolve_metadata_ids():
    plugin = _load_plugin_module()
    client = _RecordingClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))
    calls = [
        ("get_app", {}, {}),
        ("list_tables", {}, {"page_size": 50}),
        ("list_fields", {"table_id": "tbl"}, {"page_size": 50}),
        ("list_views", {"table_id": "tbl"}, {"page_size": 50}),
        (
            "list_records",
            {"table_id": "tbl", "view_id": "vew"},
            {"page_size": 50, "page_token": "next"},
        ),
    ]
    for operation, ids, data in calls:
        result = json.loads(
            plugin._document_domains.handle_bitable_edit(
                {"app_token": "app", "operation": operation, "data": data, **ids}
            )
        )
        assert result["success"] is True

    assert all(request.http_method == HttpMethod.GET for request in client.requests)
    assert [request.uri.rsplit("/", 1)[-1] for request in client.requests] == [
        ":app_token",
        "tables",
        "fields",
        "views",
        "records",
    ]
    assert ("page_size", "50") in client.requests[1].queries
    assert ("page_token", "next") in client.requests[4].queries
    assert ("view_id", "vew") in client.requests[4].queries


def test_bitable_read_fetch_all_follows_pages():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {
                "items": [{"record_id": "rec1"}],
                "has_more": True,
                "page_token": "next",
            },
            {"items": [{"record_id": "rec2"}], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_bitable_edit(
            {
                "app_token": "app",
                "table_id": "tbl",
                "operation": "list_records",
                "data": {"fetch_all": True, "max_items": 10},
            }
        )
    )

    assert result["success"] is True
    assert result["page_count"] == 2
    assert [item["record_id"] for item in result["items"]] == ["rec1", "rec2"]
    assert ("page_token", "next") in client.requests[1].queries


def test_resolve_document_bitable_requires_unique_match_and_splits_token():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {
                "items": [
                    {
                        "block_id": "blk_base",
                        "parent_id": "doc",
                        "bitable": {
                            "token": "app_canonical_tbl_tasks",
                            "view_type": 1,
                        },
                    }
                ],
                "has_more": True,
                "page_token": "page2",
            },
            {"items": [{"block_id": "blk_text", "text": {}}], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_doc_resolve_bitable(
            {"document_id": "doc", "require_unique": True}
        )
    )

    assert result["count"] == 1
    assert result["page_count"] == 2
    assert result["resolved"] == {
        "block_id": "blk_base",
        "parent_id": "doc",
        "raw_token": "app_canonical_tbl_tasks",
        "app_token": "app_canonical",
        "table_id": "tbl_tasks",
        "embedded_view_type": 1,
    }


def test_bitable_sync_populates_dates_before_gantt_and_kanban_views():
    plugin = _load_plugin_module()
    initial_fields = [
        {
            "field_id": "fld_primary",
            "field_name": "Text",
            "type": 1,
            "is_primary": True,
        }
    ]
    final_fields = [
        {"field_id": "fld_task", "field_name": "Task", "type": 1, "is_primary": True},
        {"field_id": "fld_start", "field_name": "Start", "type": 5},
        {"field_id": "fld_end", "field_name": "End", "type": 5},
        {"field_id": "fld_status", "field_name": "Status", "type": 3},
    ]
    record_fields = {
        "Task": "Investigate import",
        "Start": 1000,
        "End": 2000,
        "Status": "Done",
    }
    client = _RecordingClient(
        [
            {"items": initial_fields, "has_more": False},
            {"field": final_fields[0]},
            {"field": final_fields[1]},
            {"field": final_fields[2]},
            {"field": final_fields[3]},
            {"items": final_fields, "has_more": False},
            {"items": [{"record_id": "blank", "fields": {}}], "has_more": False},
            {},
            {},
            {"items": [], "has_more": False},
            {"view": {"view_id": "vew_gantt", "view_name": "Timeline", "view_type": "gantt"}},
            {"view": {"view_id": "vew_kanban", "view_name": "Board", "view_type": "kanban"}},
            {"items": [{"record_id": "rec1", "fields": record_fields}], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_bitable_sync(
            {
                "app_token": "app",
                "table_id": "tbl",
                "fields": [
                    {"field_name": "Task", "type": 1, "primary": True},
                    {"field_name": "Start", "type": 5},
                    {"field_name": "End", "type": 5},
                    {"field_name": "Status", "type": 3},
                ],
                "records": [{"fields": record_fields}],
                "key_field": "Task",
                "delete_blank_records": True,
                "views": [
                    {
                        "view_name": "Timeline",
                        "view_type": "gantt",
                        "start_field": "Start",
                        "end_field": "End",
                    },
                    {
                        "view_name": "Board",
                        "view_type": "kanban",
                        "group_field": "Status",
                    },
                ],
            }
        )
    )

    assert result["success"] is True
    assert result["status"] == "needs_manual_configuration"
    assert result["renamed_primary_fields"] == 1
    assert result["created_fields"] == 3
    assert result["created_records"] == 1
    assert result["deleted_blank_records"] == 1
    assert result["created_views"] == 2
    assert result["verified_records"] == 1
    record_write_index = next(
        index
        for index, request in enumerate(client.requests)
        if request.uri.endswith("/records/batch_create")
    )
    view_write_indexes = [
        index
        for index, request in enumerate(client.requests)
        if request.uri.endswith("/views") and request.http_method == HttpMethod.POST
    ]
    assert view_write_indexes and all(record_write_index < index for index in view_write_indexes)
    assert result["views"][0]["binding_candidate_unambiguous"] is True
    assert result["views"][1]["binding_candidate_unambiguous"] is True


def test_bitable_sync_updates_existing_record_without_recreating_view():
    plugin = _load_plugin_module()
    fields = [
        {"field_id": "fld_task", "field_name": "Task", "type": 1, "is_primary": True},
        {"field_id": "fld_status", "field_name": "Status", "type": 3},
    ]
    expected = {"Task": "Importer", "Status": "Done"}
    client = _RecordingClient(
        [
            {"items": fields, "has_more": False},
            {"items": fields, "has_more": False},
            {
                "items": [
                    {
                        "record_id": "rec1",
                        "fields": {"Task": "Importer", "Status": "Doing"},
                    }
                ],
                "has_more": False,
            },
            {},
            {
                "items": [
                    {
                        "view_id": "vew_board",
                        "view_name": "Board",
                        "view_type": "kanban",
                    }
                ],
                "has_more": False,
            },
            {"items": [{"record_id": "rec1", "fields": expected}], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_bitable_sync(
            {
                "app_token": "app",
                "table_id": "tbl",
                "fields": [
                    {"field_name": "Task", "type": 1, "primary": True},
                    {"field_name": "Status", "type": 3},
                ],
                "records": [{"fields": expected}],
                "key_field": "Task",
                "views": [
                    {
                        "view_name": "Board",
                        "view_type": "kanban",
                        "group_field": "Status",
                    }
                ],
            }
        )
    )

    assert result["created_records"] == 0
    assert result["updated_records"] == 1
    assert result["created_views"] == 0
    assert result["views"][0]["created"] is False
    updates = [request for request in client.requests if request.uri.endswith("batch_update")]
    assert len(updates) == 1
    assert updates[0].body == {
        "records": [{"record_id": "rec1", "fields": expected}]
    }


def test_bitable_sync_refuses_to_initialize_gantt_without_dated_records():
    plugin = _load_plugin_module()
    fields = [
        {"field_id": "fld_start", "field_name": "Start", "type": 5},
        {"field_id": "fld_end", "field_name": "End", "type": 5},
    ]
    client = _RecordingClient(
        [
            {"items": fields, "has_more": False},
            {"items": fields, "has_more": False},
            {"items": [], "has_more": False},
            {"items": [], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._document_domains.handle_bitable_sync(
            {
                "app_token": "app",
                "table_id": "tbl",
                "views": [
                    {
                        "view_name": "Timeline",
                        "view_type": "gantt",
                        "start_field": "Start",
                        "end_field": "End",
                    }
                ],
            }
        )
    )

    assert "requires at least one supplied dated record" in result["error"]
    assert not any(
        request.uri.endswith("/views") and request.http_method == HttpMethod.POST
        for request in client.requests
    )


def test_bitable_board_and_task_operation_matrices():
    plugin = _load_plugin_module()
    client = _RecordingClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    bitable_calls = [
        ("update_app", {}, {}),
        ("batch_create_tables", {}, {"tables": [{"name": "T"}]}),
        ("batch_delete_tables", {}, {"table_ids": ["tbl"]}),
        ("create_field", {"table_id": "tbl"}, {"field_name": "F", "type": 1}),
        ("update_field", {"table_id": "tbl", "field_id": "fld"}, {"field_name": "F2"}),
        ("delete_field", {"table_id": "tbl", "field_id": "fld"}, {}),
        ("create_view", {"table_id": "tbl"}, {"view_name": "V", "view_type": "grid"}),
        ("update_view", {"table_id": "tbl", "view_id": "vew"}, {"view_name": "V2"}),
        ("delete_view", {"table_id": "tbl", "view_id": "vew"}, {}),
        ("batch_create_records", {"table_id": "tbl"}, {"records": [{"fields": {"F": "a"}}]}),
        ("batch_update_records", {"table_id": "tbl"}, {"records": [{"record_id": "rec", "fields": {"F": "b"}}]}),
        ("batch_delete_records", {"table_id": "tbl"}, {"records": ["rec"]}),
    ]
    for operation, ids, data in bitable_calls:
        result = json.loads(
            plugin._document_domains.handle_bitable_edit(
                {"app_token": "app", "operation": operation, "data": data, **ids}
            )
        )
        assert result["success"] is True

    assert [request.uri.rsplit("/", 1)[-1] for request in client.requests[9:12]] == [
        "batch_create",
        "batch_update",
        "batch_delete",
    ]
    assert client.requests[4].http_method == HttpMethod.PUT
    assert client.requests[7].http_method == HttpMethod.PATCH
    assert client.requests[8].http_method == HttpMethod.DELETE

    board_start = len(client.requests)
    for operation, data in [
        ("get_nodes", {}),
        ("get_theme", {}),
        ("create_nodes", {"nodes": [{"type": "text", "x": 0, "y": 0}]}),
        ("delete_nodes", {"ids": ["node"]}),
        ("update_theme", {"theme": "classic"}),
    ]:
        result = json.loads(
            plugin._document_domains.handle_board_edit(
                {"whiteboard_id": "board", "operation": operation, "data": data}
            )
        )
        assert result["success"] is True
    board_requests = client.requests[board_start:]
    assert [request.http_method for request in board_requests] == [
        HttpMethod.GET,
        HttpMethod.GET,
        HttpMethod.POST,
        HttpMethod.DELETE,
        HttpMethod.POST,
    ]

    task_start = len(client.requests)
    task_operations = [
        "create",
        "update",
        "delete",
        "add_members",
        "remove_members",
        "add_reminders",
        "remove_reminders",
        "add_dependencies",
        "remove_dependencies",
        "add_tasklist",
        "remove_tasklist",
    ]
    for operation in task_operations:
        args = {"operation": operation, "data": {}}
        if operation != "create":
            args["task_guid"] = "task"
        result = json.loads(plugin._document_domains.handle_task_edit(args))
        assert result["success"] is True
    task_requests = client.requests[task_start:]
    assert [request.http_method for request in task_requests[:3]] == [
        HttpMethod.POST,
        HttpMethod.PATCH,
        HttpMethod.DELETE,
    ]
    assert [request.uri.rsplit("/", 1)[-1] for request in task_requests[3:]] == task_operations[3:]


def test_wiki_link_resolution_and_docx_read():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {
                "node": {
                    "space_id": "7001",
                    "node_token": "wikcn1234567890",
                    "obj_token": "doxcn1234567890",
                    "obj_type": "docx",
                    "title": "Wiki page",
                }
            },
            {"content": "Knowledge-base body"},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "read_node",
                "token": "https://tenant.feishu.cn/wiki/wikcn1234567890?from=chat",
            }
        )
    )

    assert result["success"] is True
    assert result["content"] == "Knowledge-base body"
    assert result["node"]["obj_token"] == "doxcn1234567890"
    assert client.requests[0].uri.endswith("/spaces/get_node")
    assert client.requests[0].queries == [("token", "wikcn1234567890")]
    assert client.requests[1].paths == {"document_id": "doxcn1234567890"}


def test_wiki_space_and_node_pagination():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {"items": [{"space_id": "1"}], "has_more": True, "page_token": "p2"},
            {"items": [{"space_id": "2"}], "has_more": False},
            {"items": [{"node_token": "n1"}], "has_more": False},
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    spaces = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {"action": "list_spaces", "fetch_all": True, "page_size": 50}
        )
    )
    nodes = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "list_nodes",
                "space_id": "7001",
                "parent_node_token": "https://tenant.feishu.cn/wiki/wikcnparent1234",
            }
        )
    )

    assert [item["space_id"] for item in spaces["items"]] == ["1", "2"]
    assert spaces["page_count"] == 2
    assert ("page_token", "p2") in client.requests[1].queries
    assert nodes["items"] == [{"node_token": "n1"}]
    assert client.requests[2].paths == {"space_id": "7001"}
    assert ("parent_node_token", "wikcnparent1234") in client.requests[2].queries


def test_wiki_search_and_write_operation_shapes():
    plugin = _load_plugin_module()
    client = _RecordingClient()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    calls = [
        {
            "action": "search",
            "query": "deployment",
            "space_id": "7001",
            "page_size": 25,
        },
        {
            "action": "create_node",
            "space_id": "7001",
            "obj_type": "docx",
            "title": "Runbook",
            "parent_node_token": "wikcnparent1234",
        },
        {
            "action": "update_title",
            "space_id": "7001",
            "node_token": "wikcnnode123456",
            "title": "New title",
        },
        {
            "action": "move_node",
            "space_id": "7001",
            "node_token": "wikcnnode123456",
            "target_space_id": "7002",
            "target_parent_token": "wikcntarget1234",
        },
        {
            "action": "copy_node",
            "space_id": "7001",
            "node_token": "wikcnnode123456",
            "target_space_id": "7002",
            "title": "Copied title",
        },
    ]
    for args in calls:
        assert json.loads(plugin._wiki_tools.handle_feishu_wiki(args))["success"]

    search, create, rename, move, copy = client.requests
    assert search.uri == "/open-apis/search/v2/doc_wiki/search"
    assert search.body == {
        "query": "deployment",
        "wiki_filter": {"space_ids": ["7001"]},
        "page_size": 25,
    }
    assert create.uri.endswith("/spaces/:space_id/nodes")
    assert create.body == {
        "obj_type": "docx",
        "node_type": "origin",
        "title": "Runbook",
        "parent_node_token": "wikcnparent1234",
    }
    assert rename.uri.endswith("/:node_token/update_title")
    assert rename.body == {"title": "New title"}
    assert move.uri.endswith("/:node_token/move")
    assert move.body == {
        "target_space_id": "7002",
        "target_parent_token": "wikcntarget1234",
    }
    assert copy.uri.endswith("/:node_token/copy")
    assert copy.body == {"target_space_id": "7002", "title": "Copied title"}


def test_wiki_rejects_unsupported_or_incomplete_operations():
    plugin = _load_plugin_module()
    plugin._document_access.bind_adapter(SimpleNamespace(_client=_RecordingClient()))

    bad_search = json.loads(
        plugin._wiki_tools.handle_feishu_wiki({"action": "search", "query": ""})
    )
    bad_shortcut = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "create_node",
                "space_id": "7001",
                "node_type": "shortcut",
            }
        )
    )
    bad_move = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "move_node",
                "space_id": "7001",
                "node_token": "wikcnnode123456",
            }
        )
    )

    assert "query" in bad_search["error"]
    assert "origin_node_token" in bad_shortcut["error"]
    assert "target_space_id" in bad_move["error"]


def test_wiki_moves_drive_document_and_queries_async_task():
    plugin = _load_plugin_module()
    client = _RecordingClient(
        [
            {"task_id": "task-123"},
            {
                "task": {
                    "task_id": "task-123",
                    "move_result": [{"status": 0, "status_msg": "success"}],
                }
            },
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    moved = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "move_document",
                "space_id": "7001",
                "obj_token": "https://tenant.feishu.cn/docx/doxcn1234567890",
                "parent_node_token": "https://tenant.feishu.cn/wiki/wikcnparent1234",
                "apply": True,
            }
        )
    )
    task = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {"action": "get_task", "task_id": moved["task_id"]}
        )
    )

    assert moved["success"] is True
    assert task["task"]["move_result"][0]["status"] == 0
    move_request, task_request = client.requests
    assert move_request.uri.endswith("/nodes/move_docs_to_wiki")
    assert move_request.paths == {"space_id": "7001"}
    assert move_request.body == {
        "obj_type": "docx",
        "obj_token": "doxcn1234567890",
        "parent_wiki_token": "wikcnparent1234",
        "apply": True,
    }
    assert task_request.uri == "/open-apis/wiki/v2/tasks/:task_id"
    assert task_request.paths == {"task_id": "task-123"}
    assert task_request.queries == [("task_type", "move")]


def test_wiki_move_document_can_wait_for_bounded_async_completion(monkeypatch):
    plugin = _load_plugin_module()
    monkeypatch.setattr(plugin._wiki_tools.time, "sleep", lambda _: None)
    client = _RecordingClient(
        [
            {"task_id": "task-123"},
            {
                "task": {
                    "task_id": "task-123",
                    "move_result": [{"status": 1, "status_msg": "processing"}],
                }
            },
            {
                "task": {
                    "task_id": "task-123",
                    "move_result": [{"status": 0, "status_msg": "success"}],
                }
            },
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "move_document",
                "space_id": "7001",
                "obj_token": "doxcn1234567890",
                "obj_type": "docx",
                "wait_for_completion": True,
                "poll_interval_seconds": 0.5,
                "task_timeout_seconds": 10,
            }
        )
    )

    assert result["success"] is True
    assert result["task_state"] == "succeeded"
    assert result["completed"] is True
    assert result["timed_out"] is False
    assert result["poll_count"] == 2
    assert len(client.requests) == 3


def test_wiki_get_task_wait_returns_processing_on_timeout(monkeypatch):
    plugin = _load_plugin_module()
    moments = iter([0.0, 2.0])
    monkeypatch.setattr(plugin._wiki_tools.time, "monotonic", lambda: next(moments))
    client = _RecordingClient(
        [
            {
                "task": {
                    "task_id": "task-123",
                    "move_result": [{"status": 1, "status_msg": "processing"}],
                }
            }
        ]
    )
    plugin._document_access.bind_adapter(SimpleNamespace(_client=client))

    result = json.loads(
        plugin._wiki_tools.handle_feishu_wiki(
            {
                "action": "get_task",
                "task_id": "task-123",
                "wait_for_completion": True,
                "task_timeout_seconds": 1,
            }
        )
    )

    assert result["success"] is True
    assert result["task_state"] == "processing"
    assert result["completed"] is False
    assert result["timed_out"] is True
    assert result["poll_count"] == 1


def test_wiki_move_task_state_distinguishes_terminal_failure():
    plugin = _load_plugin_module()

    assert plugin._wiki_tools._move_task_state(
        {"task": {"move_result": [{"status": 1}]}}
    ) == "processing"
    assert plugin._wiki_tools._move_task_state(
        {"task": {"move_result": [{"status": 0}]}}
    ) == "succeeded"
    assert plugin._wiki_tools._move_task_state(
        {"task": {"move_result": [{"status": -1}]}}
    ) == "failed"
