"""Behavior tests for document access from normal Feishu bot chats."""

from __future__ import annotations

import asyncio
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
