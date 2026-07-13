"""Structured inspection, comments, and file lifecycle for Feishu documents."""

from __future__ import annotations

from typing import Any

from tools.registry import tool_error, tool_result

from .document_tools import _check_feishu, _client_or_error, _request


_LIST_BLOCKS_URI = "/open-apis/docx/v1/documents/:document_id/blocks"
_GET_BLOCK_URI = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
_COMMENT_URI = "/open-apis/drive/v1/files/:file_token/comments"
_COMMENT_ITEM_URI = (
    "/open-apis/drive/v1/files/:file_token/comments/:comment_id"
)
_COMMENT_REPLIES_URI = (
    "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies"
)
_COMMENT_REPLY_URI = (
    "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies/:reply_id"
)
_DRIVE_FILE_URI = "/open-apis/drive/v1/files/:file_token"
_DRIVE_COPY_URI = "/open-apis/drive/v1/files/:file_token/copy"
_DRIVE_MOVE_URI = "/open-apis/drive/v1/files/:file_token/move"


FEISHU_DOC_GET_BLOCKS_SCHEMA = {
    "name": "feishu_doc_get_blocks",
    "description": (
        "Get one structured Feishu docx Block or list the document Block tree. "
        "Returns block IDs, parent/child relationships, indexes, media tokens, "
        "table cells, and embedded-object tokens needed for safe editing."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "block_id": {
                "type": "string",
                "description": "When set, fetch only this Block.",
            },
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "default": 500,
            },
            "page_token": {"type": "string"},
            "fetch_all": {
                "type": "boolean",
                "default": False,
                "description": "Follow up to 20 pages automatically.",
            },
        },
        "required": ["document_id"],
    },
}


_COMMENT_ELEMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["text", "docs_link", "person"],
            "default": "text",
        },
        "text": {"type": "string"},
        "url": {"type": "string"},
        "user_id": {"type": "string"},
    },
}

FEISHU_DOC_COMMENTS_SCHEMA = {
    "name": "feishu_doc_comments",
    "description": (
        "List, create, reply to, edit, delete, solve, or reopen Feishu document "
        "comments. Content supports text, document links, and person mentions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "action": {
                "type": "string",
                "enum": [
                    "list",
                    "create",
                    "reply",
                    "update_reply",
                    "delete_reply",
                    "set_solved",
                ],
            },
            "comment_id": {"type": "string"},
            "reply_id": {"type": "string"},
            "content": {"type": "string"},
            "elements": {
                "type": "array",
                "minItems": 1,
                "maxItems": 100,
                "items": _COMMENT_ELEMENT_SCHEMA,
            },
            "is_solved": {"type": "boolean"},
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 50,
            },
            "page_token": {"type": "string"},
        },
        "required": ["document_id", "action"],
    },
}


FEISHU_DOC_MANAGE_SCHEMA = {
    "name": "feishu_doc_manage",
    "description": (
        "Rename, copy, move, or delete an entire Feishu docx document. Copy and "
        "move require a destination folder token; delete moves the document to "
        "the Feishu recycle bin."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "action": {
                "type": "string",
                "enum": ["rename", "copy", "move", "delete"],
            },
            "title": {
                "type": "string",
                "description": "New title for rename or copied document.",
            },
            "folder_token": {"type": "string"},
        },
        "required": ["document_id", "action"],
    },
}


def _positive_integer(value: Any, default: int, maximum: int) -> tuple[int, str | None]:
    if value is None:
        return default, None
    if isinstance(value, bool) or not isinstance(value, int):
        return 0, "must be an integer"
    if not 1 <= value <= maximum:
        return 0, f"must be between 1 and {maximum}"
    return value, None


def handle_doc_get_blocks(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    block_id = str(args.get("block_id") or "").strip()
    if not document_id:
        return tool_error("document_id is required")
    page_size, error = _positive_integer(args.get("page_size"), 500, 500)
    if error:
        return tool_error(f"page_size {error}")
    fetch_all = args.get("fetch_all", False)
    if not isinstance(fetch_all, bool):
        return tool_error("fetch_all must be a boolean")

    client, error = _client_or_error()
    if error:
        return error
    if block_id:
        code, msg, data = _request(
            client,
            "GET",
            _GET_BLOCK_URI,
            paths={"document_id": document_id, "block_id": block_id},
            queries=[("document_revision_id", "-1")],
        )
        if code != 0:
            return tool_error(f"Get document Block failed: code={code} msg={msg}")
        return tool_result(success=True, **data)

    page_token = str(args.get("page_token") or "").strip()
    items: list[Any] = []
    page_count = 0
    last_data: dict[str, Any] = {}
    while True:
        queries = [
            ("document_revision_id", "-1"),
            ("page_size", str(page_size)),
        ]
        if page_token:
            queries.append(("page_token", page_token))
        code, msg, data = _request(
            client,
            "GET",
            _LIST_BLOCKS_URI,
            paths={"document_id": document_id},
            queries=queries,
        )
        if code != 0:
            return tool_error(f"List document Blocks failed: code={code} msg={msg}")
        page_count += 1
        last_data = data
        page_items = data.get("items")
        if isinstance(page_items, list):
            items.extend(page_items)
        next_token = str(data.get("page_token") or "")
        has_more = data.get("has_more") is True
        if not fetch_all or not has_more or not next_token or page_count >= 20:
            break
        page_token = next_token
    result = dict(last_data)
    result["items"] = items
    result["page_count"] = page_count
    result["truncated"] = bool(
        fetch_all and last_data.get("has_more") is True and page_count >= 20
    )
    return tool_result(success=True, **result)


def _comment_content(args: dict) -> tuple[dict[str, Any] | None, str | None]:
    raw_elements = args.get("elements")
    if raw_elements is None:
        content = args.get("content")
        if not isinstance(content, str) or not content.strip():
            return None, "non-empty content or elements is required"
        return {
            "elements": [
                {"type": "text_run", "text_run": {"text": content.strip()}}
            ]
        }, None
    if not isinstance(raw_elements, list) or not 1 <= len(raw_elements) <= 100:
        return None, "elements must contain 1-100 items"
    elements: list[dict[str, Any]] = []
    for index, item in enumerate(raw_elements):
        if not isinstance(item, dict):
            return None, f"elements[{index}] must be an object"
        element_type = str(item.get("type") or "text").strip()
        if element_type == "text":
            text = str(item.get("text") or "").strip()
            if not text:
                return None, f"elements[{index}].text is required"
            elements.append({"type": "text_run", "text_run": {"text": text}})
        elif element_type == "docs_link":
            url = str(item.get("url") or "").strip()
            if not url:
                return None, f"elements[{index}].url is required"
            elements.append({"type": "docs_link", "docs_link": {"url": url}})
        elif element_type == "person":
            user_id = str(item.get("user_id") or "").strip()
            if not user_id:
                return None, f"elements[{index}].user_id is required"
            elements.append({"type": "person", "person": {"user_id": user_id}})
        else:
            return None, f"elements[{index}].type is unsupported"
    return {"elements": elements}, None


def handle_doc_comments(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    action = str(args.get("action") or "").strip()
    comment_id = str(args.get("comment_id") or "").strip()
    reply_id = str(args.get("reply_id") or "").strip()
    if not document_id or action not in {
        "list",
        "create",
        "reply",
        "update_reply",
        "delete_reply",
        "set_solved",
    }:
        return tool_error("document_id and a supported action are required")
    if action in {"reply", "update_reply", "delete_reply", "set_solved"} and not comment_id:
        return tool_error("comment_id is required for this action")
    if action in {"update_reply", "delete_reply"} and not reply_id:
        return tool_error("reply_id is required for this action")

    client, error = _client_or_error()
    if error:
        return error
    queries = [("file_type", "docx"), ("user_id_type", "open_id")]
    body: dict[str, Any] | None = None
    paths = {"file_token": document_id}
    if action == "list":
        page_size, error = _positive_integer(args.get("page_size"), 50, 50)
        if error:
            return tool_error(f"page_size {error}")
        queries.append(("page_size", str(page_size)))
        page_token = str(args.get("page_token") or "").strip()
        if page_token:
            queries.append(("page_token", page_token))
        method, uri = "GET", _COMMENT_URI
    elif action == "create":
        content, error = _comment_content(args)
        if error:
            return tool_error(error)
        method, uri = "POST", _COMMENT_URI
        body = {"reply_list": {"replies": [{"content": content}]}}
    elif action in {"reply", "update_reply"}:
        content, error = _comment_content(args)
        if error:
            return tool_error(error)
        paths.update({"comment_id": comment_id})
        method = "POST" if action == "reply" else "PUT"
        uri = _COMMENT_REPLIES_URI if action == "reply" else _COMMENT_REPLY_URI
        if action == "update_reply":
            paths["reply_id"] = reply_id
        body = {"content": content}
    elif action == "delete_reply":
        paths.update({"comment_id": comment_id, "reply_id": reply_id})
        method, uri = "DELETE", _COMMENT_REPLY_URI
    else:
        is_solved = args.get("is_solved")
        if not isinstance(is_solved, bool):
            return tool_error("is_solved must be a boolean")
        paths["comment_id"] = comment_id
        method, uri = "PATCH", _COMMENT_ITEM_URI
        body = {"is_solved": is_solved}
    code, msg, data = _request(
        client, method, uri, paths=paths, queries=queries, body=body
    )
    if code != 0:
        return tool_error(f"Document comment action failed: code={code} msg={msg}")
    return tool_result(success=True, action=action, **data)


def handle_doc_manage(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    action = str(args.get("action") or "").strip()
    if not document_id or action not in {"rename", "copy", "move", "delete"}:
        return tool_error("document_id and a supported action are required")
    client, error = _client_or_error()
    if error:
        return error
    paths = {"file_token": document_id}
    if action == "rename":
        title = str(args.get("title") or "").strip()
        if not title or len(title) > 800:
            return tool_error("title must contain 1-800 characters")
        code, msg, data = _request(
            client,
            "PATCH",
            _GET_BLOCK_URI,
            paths={"document_id": document_id, "block_id": document_id},
            queries=[("document_revision_id", "-1")],
            body={
                "update_text_elements": {
                    "elements": [
                        {
                            "text_run": {
                                "content": title,
                                "text_element_style": {},
                            }
                        }
                    ]
                }
            },
        )
    elif action == "copy":
        title = str(args.get("title") or "").strip()
        folder_token = str(args.get("folder_token") or "").strip()
        if not title or len(title.encode("utf-8")) > 256 or not folder_token:
            return tool_error("copy requires title (up to 256 bytes) and folder_token")
        code, msg, data = _request(
            client,
            "POST",
            _DRIVE_COPY_URI,
            paths=paths,
            queries=[("user_id_type", "open_id")],
            body={"name": title, "type": "docx", "folder_token": folder_token},
        )
    elif action == "move":
        folder_token = str(args.get("folder_token") or "").strip()
        if not folder_token:
            return tool_error("move requires folder_token")
        code, msg, data = _request(
            client,
            "POST",
            _DRIVE_MOVE_URI,
            paths=paths,
            body={"type": "docx", "folder_token": folder_token},
        )
    else:
        code, msg, data = _request(
            client,
            "DELETE",
            _DRIVE_FILE_URI,
            paths=paths,
            queries=[("type", "docx")],
        )
    if code != 0:
        return tool_error(f"Document {action} failed: code={code} msg={msg}")
    return tool_result(success=True, action=action, **data)


def register_document_management_tools(ctx: Any) -> None:
    for name, schema, handler, description, emoji in (
        (
            "feishu_doc_get_blocks",
            FEISHU_DOC_GET_BLOCKS_SCHEMA,
            handle_doc_get_blocks,
            "Inspect the structured Block tree of a Feishu document",
            "🔎",
        ),
        (
            "feishu_doc_comments",
            FEISHU_DOC_COMMENTS_SCHEMA,
            handle_doc_comments,
            "Read and manage Feishu document comments",
            "💬",
        ),
        (
            "feishu_doc_manage",
            FEISHU_DOC_MANAGE_SCHEMA,
            handle_doc_manage,
            "Rename, copy, move, or delete a Feishu document",
            "🗂️",
        ),
    ):
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
    "handle_doc_comments",
    "handle_doc_get_blocks",
    "handle_doc_manage",
    "register_document_management_tools",
]
