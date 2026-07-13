"""Create, edit, and share Feishu documents through the connected bot app."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

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


_BOOLEAN_STYLE_FIELDS = (
    "bold",
    "italic",
    "strikethrough",
    "underline",
    "inline_code",
)

_STYLE_SCHEMA_PROPERTIES = {
    "bold": {"type": "boolean", "description": "Render the text in bold."},
    "italic": {"type": "boolean", "description": "Render the text in italics."},
    "strikethrough": {
        "type": "boolean",
        "description": "Render the text with strikethrough.",
    },
    "underline": {
        "type": "boolean",
        "description": "Render the text with an underline.",
    },
    "inline_code": {
        "type": "boolean",
        "description": "Render the text as inline code.",
    },
    "text_color": {
        "type": "integer",
        "enum": list(range(1, 8)),
        "description": (
            "Font color: 1 pink, 2 orange, 3 yellow, 4 green, 5 blue, "
            "6 purple, 7 gray."
        ),
    },
    "background_color": {
        "type": "integer",
        "enum": list(range(1, 16)),
        "description": (
            "Background color: 1-7 light pink/orange/yellow/green/blue/"
            "purple/gray; 8-14 the corresponding dark colors; 15 light gray."
        ),
    },
    "link_url": {
        "type": "string",
        "description": "Optional raw URL; the tool URL-encodes it for Feishu.",
    },
}

_RICH_TEXT_ELEMENTS_SCHEMA = {
    "type": "array",
    "minItems": 1,
    "description": (
        "Rich inline elements. When supplied, this takes precedence over content "
        "and the block-level style fields."
    ),
    "items": {
        "type": "object",
        "properties": {
            "type": {
                "type": "string",
                "enum": [
                    "text",
                    "equation",
                    "mention_user",
                    "mention_doc",
                    "reminder",
                ],
                "default": "text",
                "description": "Inline element type.",
            },
            "content": {
                "type": "string",
                "description": "Text or KaTeX equation content.",
            },
            "user_id": {
                "type": "string",
                "description": "Open ID for a mention_user element.",
            },
            "token": {
                "type": "string",
                "description": "Document token for a mention_doc element.",
            },
            "obj_type": {
                "type": "integer",
                "enum": [1, 3, 8, 11, 12, 15, 16, 22],
                "description": "Mentioned document type; 22 means docx.",
            },
            "url": {
                "type": "string",
                "description": "Optional raw document URL for mention_doc.",
            },
            "fallback_type": {
                "type": "string",
                "enum": ["FallbackToLink", "FallbackToText"],
                "description": "Fallback when the mentioned document is unavailable.",
            },
            "create_user_id": {
                "type": "string",
                "description": "Creator Open ID for a reminder.",
            },
            "is_notify": {"type": "boolean"},
            "is_whole_day": {"type": "boolean"},
            "expire_time": {
                "type": "integer",
                "description": "Reminder event time in milliseconds.",
            },
            "notify_time": {
                "type": "integer",
                "description": "Reminder notification time in milliseconds.",
            },
            **_STYLE_SCHEMA_PROPERTIES,
        },
    },
}

_TEXT_BLOCK_TYPES = {
    "text": 2,
    "bullet": 12,
    "ordered": 13,
    "code": 14,
    "quote": 15,
    "todo": 17,
}

_TEXT_BLOCK_STYLE_SCHEMA_PROPERTIES = {
    "align": {
        "type": "integer",
        "enum": [1, 2, 3],
        "description": "Block alignment: 1 left, 2 center, 3 right.",
    },
    "folded": {"type": "boolean", "description": "Fold the block."},
    "done": {
        "type": "boolean",
        "description": "Completion state for a todo block.",
    },
    "language": {
        "type": "integer",
        "minimum": 1,
        "maximum": 75,
        "description": "Feishu CodeLanguage enum for a code block.",
    },
    "wrap": {
        "type": "boolean",
        "description": "Enable line wrapping for a code block.",
    },
}


def _text_elements(
    content: str, style: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    return [
        {
            "text_run": {
                "content": content,
                "text_element_style": style or {},
            }
        }
    ]


def _text_style(values: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    style: dict[str, Any] = {}
    for field in _BOOLEAN_STYLE_FIELDS:
        if field not in values:
            continue
        value = values[field]
        if not isinstance(value, bool):
            return {}, f"{field} must be a boolean"
        style[field] = value

    for field, maximum in (("text_color", 7), ("background_color", 15)):
        if field not in values or values[field] is None:
            continue
        value = values[field]
        if isinstance(value, bool) or not isinstance(value, int):
            return {}, f"{field} must be an integer"
        if not 1 <= value <= maximum:
            return {}, f"{field} must be between 1 and {maximum}"
        style[field] = value

    if "link_url" in values and values["link_url"] is not None:
        link_url = values["link_url"]
        if not isinstance(link_url, str) or not link_url.strip():
            return {}, "link_url must be a non-empty string"
        style["link"] = {"url": quote(link_url.strip(), safe="")}

    return style, None


def _rich_text_element(
    item: dict[str, Any], index: int
) -> tuple[dict[str, Any] | None, str | None]:
    element_type = str(item.get("type") or "text").strip()
    style, error = _text_style(item)
    if error:
        return None, f"elements[{index}].{error}"

    if element_type in {"text", "equation"}:
        content = item.get("content")
        if not isinstance(content, str) or not content:
            return None, f"elements[{index}].content must be non-empty text"
        key = "text_run" if element_type == "text" else "equation"
        return {key: {"content": content, "text_element_style": style}}, None

    if element_type == "mention_user":
        user_id = str(item.get("user_id") or "").strip()
        if not user_id:
            return None, f"elements[{index}].user_id is required"
        return {
            "mention_user": {
                "user_id": user_id,
                "text_element_style": style,
            }
        }, None

    if element_type == "mention_doc":
        token = str(item.get("token") or "").strip()
        url = str(item.get("url") or "").strip()
        obj_type = item.get("obj_type")
        if not token or obj_type not in {1, 3, 8, 11, 12, 15, 16, 22}:
            return None, (
                f"elements[{index}] mention_doc requires token and a supported "
                "obj_type"
            )
        mention_doc: dict[str, Any] = {
            "token": token,
            "obj_type": obj_type,
            "text_element_style": style,
        }
        if url:
            mention_doc["url"] = quote(url, safe="")
        if "fallback_type" in item:
            fallback_type = item["fallback_type"]
            if fallback_type not in {"FallbackToLink", "FallbackToText"}:
                return None, f"elements[{index}].fallback_type is unsupported"
            mention_doc["fallback_type"] = fallback_type
        return {"mention_doc": mention_doc}, None

    if element_type == "reminder":
        create_user_id = str(item.get("create_user_id") or "").strip()
        expire_time = item.get("expire_time")
        notify_time = item.get("notify_time")
        if not create_user_id:
            return None, f"elements[{index}].create_user_id is required"
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (expire_time, notify_time)
        ):
            return None, (
                f"elements[{index}].expire_time and notify_time must be integers"
            )
        reminder = {
            "create_user_id": create_user_id,
            "expire_time": str(expire_time),
            "notify_time": str(notify_time),
            "text_element_style": style,
        }
        for field in ("is_notify", "is_whole_day"):
            if field in item:
                if not isinstance(item[field], bool):
                    return None, f"elements[{index}].{field} must be a boolean"
                reminder[field] = item[field]
        return {"reminder": reminder}, None

    return None, f"elements[{index}].type is unsupported"


def _build_text_elements(
    args: dict[str, Any],
) -> tuple[list[dict[str, Any]] | None, str | None]:
    raw_elements = args.get("elements")
    if raw_elements is not None:
        if not isinstance(raw_elements, list) or not raw_elements:
            return None, "elements must be a non-empty array"
        elements: list[dict[str, Any]] = []
        for index, item in enumerate(raw_elements):
            if not isinstance(item, dict):
                return None, f"elements[{index}] must be an object"
            element, error = _rich_text_element(item, index)
            if error:
                return None, error
            elements.append(element)
        return elements, None

    content = args.get("content")
    if not isinstance(content, str) or not content:
        return None, "non-empty content or elements is required"
    style, error = _text_style(args)
    if error:
        return None, error
    return _text_elements(content, style), None


def _text_block_style(
    args: dict[str, Any], block_type: str
) -> tuple[dict[str, Any], str | None]:
    style: dict[str, Any] = {}
    for field in ("folded", "done", "wrap"):
        if field not in args:
            continue
        if not isinstance(args[field], bool):
            return {}, f"{field} must be a boolean"
        style[field] = args[field]

    if "align" in args:
        align = args["align"]
        if isinstance(align, bool) or align not in {1, 2, 3}:
            return {}, "align must be 1, 2, or 3"
        style["align"] = align
    if "language" in args:
        language = args["language"]
        if isinstance(language, bool) or not isinstance(language, int):
            return {}, "language must be an integer"
        if not 1 <= language <= 75:
            return {}, "language must be between 1 and 75"
        style["language"] = language

    if "done" in style and block_type != "todo":
        return {}, "done is only valid for todo blocks"
    if ("language" in style or "wrap" in style) and block_type != "code":
        return {}, "language and wrap are only valid for code blocks"
    return style, None


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
    "description": (
        "Append a plain, styled, or heading text block to a Feishu docx document."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The docx document_id from the URL or create result.",
            },
            "content": {
                "type": "string",
                "description": "Text to append in simple single-style mode.",
            },
            "elements": _RICH_TEXT_ELEMENTS_SCHEMA,
            "heading_level": {
                "type": "integer",
                "minimum": 1,
                "maximum": 9,
                "description": (
                    "Optional heading level from 1 to 9. Omit for normal text."
                ),
            },
            "block_type": {
                "type": "string",
                "enum": list(_TEXT_BLOCK_TYPES),
                "default": "text",
                "description": (
                    "Text-like block type. heading_level is used for headings."
                ),
            },
            **_STYLE_SCHEMA_PROPERTIES,
            **_TEXT_BLOCK_STYLE_SCHEMA_PROPERTIES,
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
        "required": ["document_id"],
    },
}


def handle_doc_append_text(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    if not document_id or not parent_block_id:
        return tool_error("document_id is required")
    elements, error = _build_text_elements(args)
    if error:
        return tool_error(error)

    heading_level = args.get("heading_level")
    requested_block_type = str(args.get("block_type") or "text").strip()
    if requested_block_type not in _TEXT_BLOCK_TYPES:
        return tool_error("block_type is unsupported")
    if heading_level is not None:
        if requested_block_type != "text":
            return tool_error("heading_level cannot be combined with block_type")
        if isinstance(heading_level, bool) or not isinstance(heading_level, int):
            return tool_error("heading_level must be an integer from 1 to 9")
        if not 1 <= heading_level <= 9:
            return tool_error("heading_level must be between 1 and 9")
    style, error = _text_block_style(args, requested_block_type)
    if error:
        return tool_error(error)
    try:
        index = int(args.get("index", -1))
    except (TypeError, ValueError):
        return tool_error("index must be an integer")
    if index < -1:
        return tool_error("index must be -1 or greater")

    client, error = _client_or_error()
    if error:
        return error
    block_type = (
        _TEXT_BLOCK_TYPES[requested_block_type]
        if heading_level is None
        else heading_level + 2
    )
    block_field = (
        requested_block_type if heading_level is None else f"heading{heading_level}"
    )
    block_data: dict[str, Any] = {"elements": elements}
    if style:
        block_data["style"] = style
    body = {
        "index": index,
        "children": [
            {
                "block_type": block_type,
                block_field: block_data,
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
    "description": (
        "Replace all rich-text elements in an existing text-like or heading block."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string", "description": "The docx document_id."},
            "block_id": {"type": "string", "description": "The text-like block ID."},
            "content": {
                "type": "string",
                "description": "Replacement text in simple single-style mode.",
            },
            "elements": _RICH_TEXT_ELEMENTS_SCHEMA,
            **_STYLE_SCHEMA_PROPERTIES,
        },
        "required": ["document_id", "block_id"],
    },
}


def handle_doc_update_text(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    block_id = str(args.get("block_id") or "").strip()
    if not document_id or not block_id:
        return tool_error("document_id and block_id are required")
    elements, error = _build_text_elements(args)
    if error:
        return tool_error(error)

    client, error = _client_or_error()
    if error:
        return error
    code, msg, data = _request(
        client,
        "PATCH",
        _UPDATE_BLOCK_URI,
        paths={"document_id": document_id, "block_id": block_id},
        queries=[("document_revision_id", "-1")],
        body={"update_text_elements": {"elements": elements}},
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
