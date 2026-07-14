"""Browse and manage Feishu Wiki spaces through the connected bot app."""

from __future__ import annotations

import re
import time
from typing import Any

from tools.registry import tool_error, tool_result

from .document_tools import _check_feishu, _client_or_error, _request


_WIKI_SPACES_URI = "/open-apis/wiki/v2/spaces"
_WIKI_NODE_URI = "/open-apis/wiki/v2/spaces/get_node"
_WIKI_NODES_URI = "/open-apis/wiki/v2/spaces/:space_id/nodes"
_DOCX_RAW_CONTENT_URI = "/open-apis/docx/v1/documents/:document_id/raw_content"
_DOC_SEARCH_URI = "/open-apis/search/v2/doc_wiki/search"
_WIKI_TASK_URI = "/open-apis/wiki/v2/tasks/:task_id"
_RESOURCE_URL = re.compile(
    r"/(wiki|doc|docx|sheet|sheets|mindnote|bitable|base)/"
    r"([A-Za-z0-9_-]{10,999})"
)
_GET_OBJECT_TYPES = {"wiki", "doc", "docx", "sheet", "mindnote", "bitable"}
_CREATE_OBJECT_TYPES = {"docx", "sheet", "mindnote", "bitable"}
_ACTIONS = [
    "list_spaces",
    "get_space",
    "get_node",
    "read_node",
    "list_nodes",
    "search",
    "create_node",
    "update_title",
    "move_node",
    "copy_node",
    "move_document",
    "get_task",
]


FEISHU_WIKI_SCHEMA = {
    "name": "feishu_wiki",
    "description": (
        "Resolve, read, browse, search, and manage Feishu Wiki knowledge-base "
        "spaces and nodes as the bot app. token accepts either a /wiki/ URL, "
        "a Wiki node token, or an underlying document token. read_node resolves "
        "the Wiki node and returns plain text for docx nodes. list operations "
        "support bounded pagination. create/update/move/copy require Wiki "
        "container permissions. Feishu publishes no Wiki-node delete API."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": _ACTIONS},
            "space_id": {"type": "string"},
            "token": {
                "type": "string",
                "description": "Wiki/document URL or token for get_node/read_node.",
            },
            "obj_type": {
                "type": "string",
                "enum": sorted(_GET_OBJECT_TYPES),
                "description": "Underlying type when token is not a Wiki token.",
            },
            "node_token": {"type": "string"},
            "obj_token": {
                "type": "string",
                "description": "Drive document token or URL for move_document.",
            },
            "parent_node_token": {"type": "string"},
            "target_space_id": {"type": "string"},
            "target_parent_token": {"type": "string"},
            "node_type": {
                "type": "string",
                "enum": ["origin", "shortcut"],
                "default": "origin",
            },
            "origin_node_token": {"type": "string"},
            "apply": {
                "type": "boolean",
                "description": "Request migration approval when direct access is missing.",
            },
            "task_id": {"type": "string"},
            "wait_for_completion": {
                "type": "boolean",
                "default": False,
                "description": (
                    "For move_document or get_task, poll the Feishu async task "
                    "until it succeeds, fails, or reaches task_timeout_seconds."
                ),
            },
            "poll_interval_seconds": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 10,
                "default": 2,
            },
            "task_timeout_seconds": {
                "type": "number",
                "minimum": 1,
                "maximum": 300,
                "default": 60,
            },
            "title": {"type": "string"},
            "query": {"type": "string", "maxLength": 50},
            "page_size": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "default": 20,
            },
            "page_token": {"type": "string"},
            "fetch_all": {
                "type": "boolean",
                "default": False,
                "description": "Follow at most 20 pages for list actions.",
            },
        },
        "required": ["action"],
    },
}


def _text(args: dict[str, Any], field: str) -> str:
    return str(args.get(field) or "").strip()


def _page_size(args: dict[str, Any]) -> tuple[int, str | None]:
    value = args.get("page_size", 20)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 50:
        return 0, "page_size must be an integer between 1 and 50"
    return value, None


def _resource(value: str, explicit_type: str = "") -> tuple[str, str]:
    """Return ``(token, obj_type)`` from a Feishu URL or a raw token."""
    match = _RESOURCE_URL.search(value)
    if not match:
        return value.strip(), explicit_type or "wiki"
    obj_type, token = match.groups()
    aliases = {"sheets": "sheet", "base": "bitable"}
    return token, aliases.get(obj_type, obj_type)


def _node_token(value: str) -> str:
    token, _ = _resource(value, "wiki")
    return token


def _request_node(client: Any, token: str, obj_type: str) -> tuple[Any, str, dict[str, Any]]:
    queries = [("token", token)]
    if obj_type != "wiki":
        queries.append(("obj_type", obj_type))
    return _request(client, "GET", _WIKI_NODE_URI, queries=queries)


def _bounded_number(
    value: Any, *, default: float, minimum: float, maximum: float, field: str
) -> tuple[float, str | None]:
    if value is None:
        return default, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0, f"{field} must be a number"
    if not minimum <= value <= maximum:
        return 0, f"{field} must be between {minimum:g} and {maximum:g}"
    return float(value), None


def _move_task_state(data: dict[str, Any]) -> str:
    task = data.get("task")
    results = task.get("move_result") if isinstance(task, dict) else None
    if not isinstance(results, list) or not results:
        return "unknown"
    statuses = [item.get("status") for item in results if isinstance(item, dict)]
    if not statuses:
        return "unknown"
    if any(status == 1 for status in statuses):
        return "processing"
    if any(status == -1 for status in statuses):
        return "failed"
    if all(status == 0 for status in statuses):
        return "succeeded"
    return "unknown"


def _get_task(
    client: Any, task_id: str
) -> tuple[Any, str, dict[str, Any]]:
    return _request(
        client,
        "GET",
        _WIKI_TASK_URI,
        paths={"task_id": task_id},
        queries=[("task_type", "move")],
    )


def _wait_for_task(
    client: Any,
    task_id: str,
    *,
    poll_interval_seconds: float,
    task_timeout_seconds: float,
) -> tuple[Any, str, dict[str, Any], str, int, bool]:
    deadline = time.monotonic() + task_timeout_seconds
    poll_count = 0
    while True:
        code, msg, data = _get_task(client, task_id)
        poll_count += 1
        if code != 0:
            return code, msg, data, "request_failed", poll_count, False
        state = _move_task_state(data)
        if state != "processing":
            return code, msg, data, state, poll_count, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return code, msg, data, state, poll_count, True
        time.sleep(min(poll_interval_seconds, remaining))


def _list_pages(
    client: Any,
    uri: str,
    *,
    paths: dict[str, str] | None,
    queries: list[tuple[str, str]],
    page_token: str,
    fetch_all: bool,
) -> tuple[Any, str, dict[str, Any]]:
    items: list[Any] = []
    page_count = 0
    last_data: dict[str, Any] = {}
    while True:
        page_queries = list(queries)
        if page_token:
            page_queries.append(("page_token", page_token))
        code, msg, data = _request(
            client, "GET", uri, paths=paths, queries=page_queries
        )
        if code != 0:
            return code, msg, data
        page_count += 1
        page_items = data.get("items", [])
        if isinstance(page_items, list):
            items.extend(page_items)
        last_data = data
        if not fetch_all or not data.get("has_more") or page_count >= 20:
            break
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            break
    result = dict(last_data)
    result["items"] = items
    result["page_count"] = page_count
    if fetch_all and page_count >= 20 and result.get("has_more"):
        result["truncated"] = True
    return 0, "success", result


def handle_feishu_wiki(args: dict, **_: Any) -> str:
    action = _text(args, "action")
    if action not in _ACTIONS:
        return tool_error("a supported action is required")
    page_size, error = _page_size(args)
    if error:
        return tool_error(error)
    fetch_all = args.get("fetch_all", False)
    if not isinstance(fetch_all, bool):
        return tool_error("fetch_all must be a boolean")
    wait_for_completion = args.get("wait_for_completion", False)
    if not isinstance(wait_for_completion, bool):
        return tool_error("wait_for_completion must be a boolean")
    poll_interval, error = _bounded_number(
        args.get("poll_interval_seconds"),
        default=2,
        minimum=0.5,
        maximum=10,
        field="poll_interval_seconds",
    )
    if error:
        return tool_error(error)
    task_timeout, error = _bounded_number(
        args.get("task_timeout_seconds"),
        default=60,
        minimum=1,
        maximum=300,
        field="task_timeout_seconds",
    )
    if error:
        return tool_error(error)

    client, error = _client_or_error()
    if error:
        return error

    space_id = _text(args, "space_id")
    node_token = _node_token(_text(args, "node_token"))
    paths: dict[str, str] | None = None
    queries: list[tuple[str, str]] | None = None
    body: dict[str, Any] | None = None
    task_state = "unknown"
    poll_count = 0
    timed_out = False

    if action == "list_spaces":
        code, msg, data = _list_pages(
            client,
            _WIKI_SPACES_URI,
            paths=None,
            queries=[("page_size", str(page_size))],
            page_token=_text(args, "page_token"),
            fetch_all=fetch_all,
        )
    elif action == "get_space":
        if not space_id:
            return tool_error("space_id is required for get_space")
        code, msg, data = _request(
            client,
            "GET",
            f"{_WIKI_SPACES_URI}/:space_id",
            paths={"space_id": space_id},
        )
    elif action in {"get_node", "read_node"}:
        raw_token = _text(args, "token") or _text(args, "node_token")
        if not raw_token:
            return tool_error("token is required for get_node/read_node")
        explicit_type = _text(args, "obj_type")
        if explicit_type and explicit_type not in _GET_OBJECT_TYPES:
            return tool_error("obj_type is unsupported")
        token, obj_type = _resource(raw_token, explicit_type)
        code, msg, data = _request_node(client, token, obj_type)
        if code == 0 and action == "read_node":
            node = data.get("node", {})
            resolved_type = str(node.get("obj_type") or "").strip()
            document_id = str(node.get("obj_token") or "").strip()
            if resolved_type != "docx" or not document_id:
                return tool_error(
                    "read_node currently returns content only for docx Wiki nodes; "
                    "use get_node to obtain the underlying object token"
                )
            read_code, read_msg, read_data = _request(
                client,
                "GET",
                _DOCX_RAW_CONTENT_URI,
                paths={"document_id": document_id},
            )
            if read_code != 0:
                return tool_error(
                    f"Read Wiki docx content failed: code={read_code} msg={read_msg}"
                )
            return tool_result(
                success=True, action=action, node=node, content=read_data.get("content", "")
            )
    elif action == "list_nodes":
        if not space_id:
            return tool_error("space_id is required for list_nodes")
        list_queries = [("page_size", str(page_size))]
        parent = _node_token(_text(args, "parent_node_token"))
        if parent:
            list_queries.append(("parent_node_token", parent))
        code, msg, data = _list_pages(
            client,
            _WIKI_NODES_URI,
            paths={"space_id": space_id},
            queries=list_queries,
            page_token=_text(args, "page_token"),
            fetch_all=fetch_all,
        )
    elif action == "search":
        query = _text(args, "query")
        if not query or len(query) > 50:
            return tool_error("query must contain 1-50 characters")
        body = {
            "query": query,
            "wiki_filter": {"space_ids": [space_id]} if space_id else {},
            "page_size": page_size,
        }
        page_token = _text(args, "page_token")
        if page_token:
            body["page_token"] = page_token
        code, msg, data = _request(client, "POST", _DOC_SEARCH_URI, body=body)
    elif action == "move_document":
        if not space_id:
            return tool_error("space_id is required for move_document")
        raw_token = _text(args, "obj_token") or _text(args, "token")
        if not raw_token:
            return tool_error("obj_token is required for move_document")
        explicit_type = _text(args, "obj_type")
        obj_token, obj_type = _resource(raw_token, explicit_type)
        if obj_type not in {"doc", "docx", "sheet", "bitable", "mindnote"}:
            return tool_error(
                "move_document obj_type must be doc, docx, sheet, bitable, or mindnote"
            )
        body = {"obj_type": obj_type, "obj_token": obj_token}
        parent = _node_token(
            _text(args, "parent_node_token") or _text(args, "target_parent_token")
        )
        if parent:
            body["parent_wiki_token"] = parent
        if "apply" in args:
            if not isinstance(args["apply"], bool):
                return tool_error("apply must be a boolean")
            body["apply"] = args["apply"]
        code, msg, data = _request(
            client,
            "POST",
            f"{_WIKI_NODES_URI}/move_docs_to_wiki",
            paths={"space_id": space_id},
            body=body,
        )
    elif action == "get_task":
        task_id = _text(args, "task_id")
        if not task_id:
            return tool_error("task_id is required for get_task")
        if wait_for_completion:
            code, msg, data, task_state, poll_count, timed_out = _wait_for_task(
                client,
                task_id,
                poll_interval_seconds=poll_interval,
                task_timeout_seconds=task_timeout,
            )
        else:
            code, msg, data = _get_task(client, task_id)
    elif action == "create_node":
        if not space_id:
            return tool_error("space_id is required for create_node")
        obj_type = _text(args, "obj_type") or "docx"
        if obj_type not in _CREATE_OBJECT_TYPES:
            return tool_error("create_node obj_type must be docx, sheet, mindnote, or bitable")
        node_type = _text(args, "node_type") or "origin"
        if node_type not in {"origin", "shortcut"}:
            return tool_error("node_type must be origin or shortcut")
        body = {"obj_type": obj_type, "node_type": node_type}
        title = _text(args, "title")
        if title:
            body["title"] = title
        parent = _node_token(_text(args, "parent_node_token"))
        if parent:
            body["parent_node_token"] = parent
        origin = _node_token(_text(args, "origin_node_token"))
        if node_type == "shortcut":
            if not origin:
                return tool_error("origin_node_token is required for a shortcut")
            body["origin_node_token"] = origin
        code, msg, data = _request(
            client,
            "POST",
            _WIKI_NODES_URI,
            paths={"space_id": space_id},
            body=body,
        )
    else:
        if not space_id or not node_token:
            return tool_error(f"space_id and node_token are required for {action}")
        paths = {"space_id": space_id, "node_token": node_token}
        base = f"{_WIKI_NODES_URI}/:node_token"
        if action == "update_title":
            title = _text(args, "title")
            if not title:
                return tool_error("title is required for update_title")
            uri, body = f"{base}/update_title", {"title": title}
        else:
            target_space = _text(args, "target_space_id")
            target_parent = _node_token(_text(args, "target_parent_token"))
            if not target_space and not target_parent:
                return tool_error(
                    f"target_space_id or target_parent_token is required for {action}"
                )
            body = {}
            if target_space:
                body["target_space_id"] = target_space
            if target_parent:
                body["target_parent_token"] = target_parent
            if action == "copy_node" and "title" in args:
                body["title"] = str(args.get("title") or "")
            uri = f"{base}/{'move' if action == 'move_node' else 'copy'}"
        code, msg, data = _request(
            client, "POST", uri, paths=paths, body=body
        )

    if code != 0:
        return tool_error(f"Wiki {action} failed: code={code} msg={msg}")
    if action == "move_document" and wait_for_completion:
        task_id = str(data.get("task_id") or "").strip()
        if task_id:
            initial_data = dict(data)
            code, msg, task_data, task_state, poll_count, timed_out = _wait_for_task(
                client,
                task_id,
                poll_interval_seconds=poll_interval,
                task_timeout_seconds=task_timeout,
            )
            if code != 0:
                return tool_error(
                    f"Wiki get_task failed after move_document: code={code} msg={msg}",
                    task_id=task_id,
                )
            data = {**initial_data, **task_data}
    if wait_for_completion and action in {"move_document", "get_task"}:
        return tool_result(
            success=task_state != "failed",
            action=action,
            task_state=task_state,
            completed=task_state in {"succeeded", "failed"},
            timed_out=timed_out,
            poll_count=poll_count,
            **data,
        )
    return tool_result(success=True, action=action, **data)


def register_wiki_tools(ctx: Any) -> None:
    ctx.register_tool(
        name="feishu_wiki",
        toolset="feishu_doc",
        schema=FEISHU_WIKI_SCHEMA,
        handler=handle_feishu_wiki,
        check_fn=_check_feishu,
        requires_env=[],
        is_async=False,
        description="Resolve, read, browse, search, and manage Feishu Wiki nodes",
        emoji="📚",
    )


__all__ = ["FEISHU_WIKI_SCHEMA", "handle_feishu_wiki", "register_wiki_tools"]
