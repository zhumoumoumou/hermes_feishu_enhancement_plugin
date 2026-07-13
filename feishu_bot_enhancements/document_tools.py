"""Create, edit, and share Feishu documents through the connected bot app."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from tools.registry import tool_error, tool_result

from .document_access import get_bound_client


_CREATE_DOCUMENT_URI = "/open-apis/docx/v1/documents"
_CREATE_CHILDREN_URI = (
    "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
)
_UPDATE_BLOCK_URI = (
    "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
)
_ADD_MEMBER_URI = "/open-apis/drive/v1/permissions/:token/members"


def _check_feishu() -> bool:
    try:
        return importlib.util.find_spec("lark_oapi") is not None
    except (ImportError, ValueError):
        return False


def _parse_response_data(response: Any) -> dict[str, Any]:
    raw = getattr(response, "raw", None)
    if raw is not None and hasattr(raw, "content"):
        try:
            payload = json.loads(raw.content)
            data = payload.get("data", {})
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return data
    if data is not None and hasattr(data, "__dict__"):
        return vars(data)
    return {}


def _request(
    client: Any,
    method: str,
    uri: str,
    *,
    paths: dict[str, str] | None = None,
    queries: list[tuple[str, str]] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    from lark_oapi import AccessTokenType
    from lark_oapi.core.enum import HttpMethod
    from lark_oapi.core.model.base_request import BaseRequest

    methods = {
        "GET": HttpMethod.GET,
        "POST": HttpMethod.POST,
        "PATCH": HttpMethod.PATCH,
    }
    builder = (
        BaseRequest.builder()
        .http_method(methods[method])
        .uri(uri)
        .token_types({AccessTokenType.TENANT})
    )
    if paths:
        builder = builder.paths(paths)
    if queries:
        builder = builder.queries(queries)
    if body is not None:
        builder = builder.body(body)

    response = client.request(builder.build())
    return (
        getattr(response, "code", None),
        str(getattr(response, "msg", "") or ""),
        _parse_response_data(response),
    )


def _client_or_error() -> tuple[Any, str | None]:
    client = get_bound_client()
    if client is None:
        return None, tool_error("Feishu client is not connected")
    return client, None


def _text_elements(content: str) -> list[dict[str, Any]]:
    return [
        {
            "text_run": {
                "content": content,
                "text_element_style": {},
            }
        }
    ]


def _decode_entry(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return None
    return value if isinstance(value, dict) else None


def _session_open_id(entries: Any, session_id: str) -> str | None:
    values = entries.values() if isinstance(entries, dict) else ()
    for raw in values:
        entry = _decode_entry(raw)
        if not entry or str(entry.get("session_id") or "") != session_id:
            continue
        origin = entry.get("origin")
        if not isinstance(origin, dict) or origin.get("platform") != "feishu":
            continue
        open_id = str(origin.get("user_id") or "").strip()
        if open_id:
            return open_id
    return None


def resolve_session_open_id(
    session_id: str,
    sessions_path: Path | None = None,
    *,
    state_db_path: Path | None = None,
) -> str | None:
    """Resolve the Feishu requester's open_id from the gateway routing index."""
    if not session_id:
        return None

    path = sessions_path or (get_hermes_home() / "sessions" / "sessions.json")
    db_path = state_db_path or (get_hermes_home() / "state.db")
    try:
        from hermes_state import SessionDB

        db = SessionDB(db_path=db_path, read_only=True)
        try:
            entries = db.load_gateway_routing_entries(scope=str(path.parent.resolve()))
        finally:
            db.close()
        open_id = _session_open_id(entries, session_id)
        if open_id:
            return open_id
    except Exception:
        pass

    try:
        entries = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return _session_open_id(entries, session_id)


FEISHU_DOC_CREATE_SCHEMA = {
    "name": "feishu_doc_create",
    "description": "Create an empty Feishu docx document owned by the bot app.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Plain-text document title (1-800 characters).",
            },
            "folder_token": {
                "type": "string",
                "description": (
                    "Optional destination folder token. With an app token, the "
                    "folder must have been created by the app."
                ),
            },
        },
        "required": ["title"],
    },
}


def handle_doc_create(args: dict, **_: Any) -> str:
    title = str(args.get("title") or "").strip()
    if not title or len(title) > 800:
        return tool_error("title must contain 1-800 characters")
    folder_token = str(args.get("folder_token") or "").strip()

    client, error = _client_or_error()
    if error:
        return error
    body = {"title": title}
    if folder_token:
        body["folder_token"] = folder_token
    code, msg, data = _request(client, "POST", _CREATE_DOCUMENT_URI, body=body)
    if code != 0:
        return tool_error(f"Create document failed: code={code} msg={msg}")

    document = data.get("document", data)
    if not isinstance(document, dict) or not document.get("document_id"):
        return tool_error("Create document succeeded but returned no document_id")
    return tool_result(success=True, document=document)


FEISHU_DOC_APPEND_TEXT_SCHEMA = {
    "name": "feishu_doc_append_text",
    "description": "Append a plain-text block to a Feishu docx document.",
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The docx document_id from the URL or create result.",
            },
            "content": {"type": "string", "description": "Text to append."},
            "parent_block_id": {
                "type": "string",
                "description": (
                    "Optional parent block. Defaults to the document root block."
                ),
            },
            "index": {
                "type": "integer",
                "description": "Insertion index; -1 appends at the end.",
                "default": -1,
            },
        },
        "required": ["document_id", "content"],
    },
}


def handle_doc_append_text(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    content = str(args.get("content") or "")
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    if not document_id or not parent_block_id or not content:
        return tool_error("document_id and non-empty content are required")
    try:
        index = int(args.get("index", -1))
    except (TypeError, ValueError):
        return tool_error("index must be an integer")
    if index < -1:
        return tool_error("index must be -1 or greater")

    client, error = _client_or_error()
    if error:
        return error
    body = {
        "index": index,
        "children": [
            {
                "block_type": 2,
                "text": {"elements": _text_elements(content)},
            }
        ],
    }
    code, msg, data = _request(
        client,
        "POST",
        _CREATE_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": parent_block_id},
        queries=[("document_revision_id", "-1")],
        body=body,
    )
    if code != 0:
        return tool_error(f"Append document text failed: code={code} msg={msg}")
    return tool_result(success=True, **data)


FEISHU_DOC_UPDATE_TEXT_SCHEMA = {
    "name": "feishu_doc_update_text",
    "description": "Replace all rich-text elements in an existing text-like block.",
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "The docx document_id."},
            "block_id": {"type": "string", "description": "The text-like block ID."},
            "content": {"type": "string", "description": "Replacement plain text."},
        },
        "required": ["document_id", "block_id", "content"],
    },
}


def handle_doc_update_text(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    block_id = str(args.get("block_id") or "").strip()
    content = str(args.get("content") or "")
    if not document_id or not block_id or not content:
        return tool_error("document_id, block_id, and non-empty content are required")

    client, error = _client_or_error()
    if error:
        return error
    code, msg, data = _request(
        client,
        "PATCH",
        _UPDATE_BLOCK_URI,
        paths={"document_id": document_id, "block_id": block_id},
        queries=[("document_revision_id", "-1")],
        body={"update_text_elements": {"elements": _text_elements(content)}},
    )
    if code != 0:
        return tool_error(f"Update document text failed: code={code} msg={msg}")
    return tool_result(success=True, **data)


FEISHU_DOC_SHARE_SCHEMA = {
    "name": "feishu_doc_share",
    "description": (
        "Add a viewer, editor, or manager to a Feishu docx document. "
        "Omit member_id to share with the current Feishu requester."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "The docx document_id."},
            "member_id": {
                "type": "string",
                "description": "Collaborator ID; defaults to the requester's open_id.",
            },
            "member_type": {
                "type": "string",
                "enum": ["openid", "email", "openchat"],
                "description": "ID type. Defaults to openid.",
                "default": "openid",
            },
            "permission": {
                "type": "string",
                "enum": ["view", "edit", "full_access"],
                "description": "Permission granted to the collaborator.",
                "default": "view",
            },
        },
        "required": ["document_id"],
    },
}


def handle_doc_share(args: dict, **kwargs: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    member_type = str(args.get("member_type") or "openid").strip()
    permission = str(args.get("permission") or "view").strip()
    if not document_id:
        return tool_error("document_id is required")
    if member_type not in {"openid", "email", "openchat"}:
        return tool_error("member_type must be openid, email, or openchat")
    if permission not in {"view", "edit", "full_access"}:
        return tool_error("permission must be view, edit, or full_access")

    member_id = str(args.get("member_id") or "").strip()
    if not member_id:
        if member_type != "openid":
            return tool_error("member_id is required unless member_type is openid")
        member_id = resolve_session_open_id(str(kwargs.get("session_id") or "")) or ""
    if not member_id:
        return tool_error(
            "Unable to resolve the current Feishu requester; provide member_id explicitly"
        )

    client, error = _client_or_error()
    if error:
        return error
    member_kind = "chat" if member_type == "openchat" else "user"
    code, msg, data = _request(
        client,
        "POST",
        _ADD_MEMBER_URI,
        paths={"token": document_id},
        queries=[("type", "docx"), ("need_notification", "false")],
        body={
            "member_type": member_type,
            "member_id": member_id,
            "perm": permission,
            "type": member_kind,
        },
    )
    if code != 0:
        return tool_error(f"Share document failed: code={code} msg={msg}")
    return tool_result(success=True, **data)


def register_document_tools(ctx: Any) -> None:
    tools = [
        (
            "feishu_doc_create",
            FEISHU_DOC_CREATE_SCHEMA,
            handle_doc_create,
            "Create a Feishu document",
            "➕",
        ),
        (
            "feishu_doc_append_text",
            FEISHU_DOC_APPEND_TEXT_SCHEMA,
            handle_doc_append_text,
            "Append text to a Feishu document",
            "✍️",
        ),
        (
            "feishu_doc_update_text",
            FEISHU_DOC_UPDATE_TEXT_SCHEMA,
            handle_doc_update_text,
            "Update a Feishu document block",
            "✏️",
        ),
        (
            "feishu_doc_share",
            FEISHU_DOC_SHARE_SCHEMA,
            handle_doc_share,
            "Share a Feishu document",
            "🔗",
        ),
    ]
    for name, schema, handler, description, emoji in tools:
        ctx.register_tool(
            name=name,
            toolset="feishu_doc",
            schema=schema,
            handler=handler,
            check_fn=_check_feishu,
            requires_env=[],
            is_async=False,
            description=description,
            emoji=emoji,
        )


__all__ = [
    "handle_doc_append_text",
    "handle_doc_create",
    "handle_doc_share",
    "handle_doc_update_text",
    "register_document_tools",
    "resolve_session_open_id",
]
