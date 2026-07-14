"""Editors for Sheet, Bitable, Board, and Task objects embedded in documents."""

from __future__ import annotations

import time
from typing import Any

from tools.registry import tool_error, tool_result

from .bitable_workflows import (
    BITABLE_SETUP_SCHEMA,
    FEISHU_BITABLE_SYNC_SCHEMA,
    FEISHU_DOC_RESOLVE_BITABLE_SCHEMA,
    handle_bitable_sync,
    handle_doc_resolve_bitable,
    list_all_pages,
    split_embedded_bitable_token,
    sync_bitable,
)
from .document_tools import (
    _CREATE_CHILDREN_URI,
    _check_feishu,
    _client_or_error,
    _request,
)


_DATA_SCHEMA = {
    "type": "object",
    "description": "Operation-specific data documented in the tool description.",
}

FEISHU_SHEET_EDIT_SCHEMA = {
    "name": "feishu_sheet_edit",
    "description": (
        "Edit a Feishu Sheet embedded in a document or stored independently. "
        "write_values/append_values use data={range,values}; set_style uses "
        "{range,style}; add_dimensions uses {sheet_id,major_dimension,length}; "
        "insert/delete_dimensions use {sheet_id,major_dimension,start_index," 
        "end_index[,inherit_style]}; move_dimensions additionally uses "
        "destination_index; batch_sheets passes official addSheet/copySheet/" 
        "deleteSheet requests. Formula strings such as '=SUM(A1:A3)' are "
        "accepted as cell values."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "spreadsheet_token": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": [
                    "write_values",
                    "append_values",
                    "set_style",
                    "add_dimensions",
                    "insert_dimensions",
                    "delete_dimensions",
                    "move_dimensions",
                    "batch_sheets",
                ],
            },
            "data": _DATA_SCHEMA,
        },
        "required": ["spreadsheet_token", "operation", "data"],
    },
}


FEISHU_BITABLE_EDIT_SCHEMA = {
    "name": "feishu_bitable_edit",
    "description": (
        "Inspect and edit a Feishu Bitable. Supports app/table/field/view/record "
        "reads, app metadata updates, batch table creation/deletion, field and "
        "view CRUD, and batch record creation/update/deletion. Read operations "
        "accept data={page_size,page_token} or data={fetch_all:true,max_items:N}; "
        "write data is the corresponding "
        "official request body. Use list_tables after creating an embedded "
        "Bitable instead of guessing its table_id."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": [
                    "get_app",
                    "list_tables",
                    "list_fields",
                    "list_views",
                    "list_records",
                    "update_app",
                    "batch_create_tables",
                    "batch_delete_tables",
                    "create_field",
                    "update_field",
                    "delete_field",
                    "create_view",
                    "update_view",
                    "delete_view",
                    "batch_create_records",
                    "batch_update_records",
                    "batch_delete_records",
                ],
            },
            "table_id": {"type": "string"},
            "field_id": {"type": "string"},
            "view_id": {"type": "string"},
            "data": _DATA_SCHEMA,
        },
        "required": ["app_token", "operation", "data"],
    },
}


FEISHU_DOC_EMBED_BITABLE_SCHEMA = {
    "name": "feishu_doc_embed_bitable",
    "description": (
        "Create a new Bitable embedded in a Feishu docx document and return its "
        "block_id, app_token, and default table_id. grid and kanban are native "
        "Docx initial views. gallery, gantt, and form are created as additional "
        "Bitable views because Docx only supports grid/kanban initially; the "
        "result marks when a manual view switch is required. If a partial result "
        "is returned, continue with feishu_bitable_edit and do not create a "
        "duplicate block. Load feishu-bot-enhancements:document-authoring for "
        "the complete workflow."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "parent_block_id": {
                "type": "string",
                "description": "Defaults to the document root block.",
            },
            "index": {
                "type": "integer",
                "default": -1,
                "description": "Insertion index; -1 appends.",
            },
            "view_type": {
                "type": "string",
                "enum": ["grid", "kanban", "gallery", "gantt", "form"],
                "default": "grid",
            },
            "view_name": {
                "type": "string",
                "description": (
                    "Name for gallery/gantt/form views; defaults to the type name."
                ),
            },
            "setup": {
                **BITABLE_SETUP_SCHEMA,
                "description": (
                    "Optional single-call, resumable setup. Fields and records are written before "
                    "gantt/kanban/gallery/form views are created. For gantt set "
                    "start_field/end_field; for kanban set group_field."
                ),
            },
        },
        "required": ["document_id"],
    },
}


_BOARD_SYNTAX_TYPES = {"plantuml": 1, "mermaid": 2, "svg": 3}
_BOARD_DIAGRAM_TYPES = {
    "auto": 0,
    "mindmap": 1,
    "sequence": 2,
    "activity": 3,
    "class": 4,
    "er": 5,
    "flowchart": 6,
    "state": 7,
    "component": 8,
}


FEISHU_DOC_EMBED_DIAGRAM_SCHEMA = {
    "name": "feishu_doc_embed_diagram",
    "description": (
        "Create a native Feishu Board directly embedded in a docx document, "
        "render PlantUML, Mermaid, or SVG into it, and verify that Board nodes "
        "exist. Use this for directly embedded UML class/sequence/component/state "
        "diagrams, ER diagrams, flowcharts, activity diagrams, and mind maps. "
        "Feishu's native Diagram Block is not writable through OpenAPI; this tool "
        "uses the supported embedded Board Block instead. If status=partial, "
        "continue with feishu_board_edit and do not create another Board."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "parent_block_id": {
                "type": "string",
                "description": "Defaults to the document root block.",
            },
            "index": {
                "type": "integer",
                "default": -1,
                "description": "Insertion index; -1 appends.",
            },
            "source": {
                "type": "string",
                "minLength": 1,
                "maxLength": 100000,
                "description": "PlantUML, Mermaid, or self-contained SVG source.",
            },
            "syntax_type": {
                "type": "string",
                "enum": list(_BOARD_SYNTAX_TYPES),
                "default": "mermaid",
            },
            "diagram_type": {
                "type": "string",
                "enum": list(_BOARD_DIAGRAM_TYPES),
                "default": "auto",
                "description": "A parsing hint; auto is normally sufficient.",
            },
            "align": {"type": "integer", "enum": [1, 2, 3], "default": 2},
            "width": {"type": "integer", "minimum": 90, "default": 800},
            "height": {"type": "integer", "minimum": 90, "default": 450},
            "client_token": {
                "type": "string",
                "minLength": 10,
                "maxLength": 64,
                "description": "Optional idempotency token for syntax rendering.",
            },
        },
        "required": ["document_id", "source"],
    },
}


FEISHU_BOARD_EDIT_SCHEMA = {
    "name": "feishu_board_edit",
    "description": (
        "Inspect Board nodes/theme, create up to 3000 raw nodes, render "
        "PlantUML/Mermaid/SVG, recursively delete up to 100 node IDs, or update "
        "the Board theme. render_syntax data uses {source,syntax_type,diagram_type,"
        "overwrite?,client_token?}; create_nodes accepts {nodes,overwrite?,"
        "client_token?}. Feishu currently publishes no API for updating existing "
        "node properties in place."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "whiteboard_id": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": [
                    "get_nodes",
                    "get_theme",
                    "create_nodes",
                    "render_syntax",
                    "delete_nodes",
                    "update_theme",
                ],
            },
            "data": _DATA_SCHEMA,
        },
        "required": ["whiteboard_id", "operation", "data"],
    },
}


FEISHU_TASK_EDIT_SCHEMA = {
    "name": "feishu_task_edit",
    "description": (
        "Create, update, or delete Feishu Task v2 tasks and manage members, "
        "reminders, dependencies, and task-list membership. update uses the "
        "official {task:{...},update_fields:[...]} body, including summary, "
        "description, start, due, completion, and custom fields."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
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
                ],
            },
            "task_guid": {"type": "string"},
            "data": _DATA_SCHEMA,
        },
        "required": ["operation", "data"],
    },
}


def _data_or_error(args: dict) -> tuple[dict[str, Any] | None, str | None]:
    data = args.get("data")
    if not isinstance(data, dict):
        return None, "data must be an object"
    return dict(data), None


def _required_text(data: dict[str, Any], field: str) -> tuple[str, str | None]:
    value = str(data.get(field) or "").strip()
    return (value, None) if value else ("", f"data.{field} is required")


def _dimension_body(
    data: dict[str, Any], *, include_length: bool = False
) -> tuple[dict[str, Any] | None, str | None]:
    sheet_id, error = _required_text(data, "sheet_id")
    if error:
        return None, error
    major = str(data.get("major_dimension") or "").strip().upper()
    if major not in {"ROWS", "COLUMNS"}:
        return None, "data.major_dimension must be ROWS or COLUMNS"
    dimension: dict[str, Any] = {"sheetId": sheet_id, "majorDimension": major}
    if include_length:
        length = data.get("length")
        if isinstance(length, bool) or not isinstance(length, int) or not 1 <= length <= 5000:
            return None, "data.length must be between 1 and 5000"
        dimension["length"] = length
    else:
        start = data.get("start_index")
        end = data.get("end_index")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (start, end)):
            return None, "data.start_index and data.end_index must be integers"
        if start < 0 or start >= end or end - start > 5000:
            return None, "data dimension range must be valid and contain at most 5000 rows/columns"
        dimension.update({"startIndex": start, "endIndex": end})
    return {"dimension": dimension}, None


def handle_sheet_edit(args: dict, **_: Any) -> str:
    token = str(args.get("spreadsheet_token") or "").strip()
    operation = str(args.get("operation") or "").strip()
    data, error = _data_or_error(args)
    if not token or operation not in FEISHU_SHEET_EDIT_SCHEMA["parameters"]["properties"]["operation"]["enum"]:
        return tool_error("spreadsheet_token and a supported operation are required")
    if error:
        return tool_error(error)

    paths = {"spreadsheet_token": token}
    if operation in {"write_values", "append_values"}:
        cell_range, error = _required_text(data, "range")
        values = data.get("values")
        if error or not isinstance(values, list) or not values:
            return tool_error(error or "data.values must be a non-empty array")
        method = "PUT" if operation == "write_values" else "POST"
        suffix = "values" if operation == "write_values" else "values_append"
        uri = f"/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/{suffix}"
        body = {"valueRange": {"range": cell_range, "values": values}}
    elif operation == "set_style":
        cell_range, error = _required_text(data, "range")
        style = data.get("style")
        if error or not isinstance(style, dict) or not style:
            return tool_error(error or "data.style must be a non-empty object")
        method = "PUT"
        uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style"
        body = {"appendStyle": {"range": cell_range, "style": style}}
    elif operation == "add_dimensions":
        body, error = _dimension_body(data, include_length=True)
        if error:
            return tool_error(error)
        method = "POST"
        uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/dimension_range"
    elif operation in {"insert_dimensions", "delete_dimensions"}:
        body, error = _dimension_body(data)
        if error:
            return tool_error(error)
        if operation == "insert_dimensions" and "inherit_style" in data:
            inherit = str(data["inherit_style"] or "").strip().upper()
            if inherit not in {"BEFORE", "AFTER"}:
                return tool_error("data.inherit_style must be BEFORE or AFTER")
            body["inheritStyle"] = inherit
        method = "POST" if operation == "insert_dimensions" else "DELETE"
        suffix = "insert_dimension_range" if operation == "insert_dimensions" else "dimension_range"
        uri = f"/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/{suffix}"
    elif operation == "move_dimensions":
        dimension, error = _dimension_body(data)
        destination = data.get("destination_index")
        if error:
            return tool_error(error)
        if isinstance(destination, bool) or not isinstance(destination, int) or destination < 0:
            return tool_error("data.destination_index must be a non-negative integer")
        sheet_id = dimension["dimension"].pop("sheetId")
        source = {
            "major_dimension": dimension["dimension"]["majorDimension"],
            "start_index": dimension["dimension"]["startIndex"],
            "end_index": dimension["dimension"]["endIndex"],
        }
        paths["sheet_id"] = sheet_id
        method = "POST"
        uri = (
            "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/"
            ":sheet_id/move_dimension"
        )
        body = {"source": source, "destination_index": destination}
    else:
        requests = data.get("requests")
        if not isinstance(requests, list) or not requests:
            return tool_error("data.requests must be a non-empty array")
        method = "POST"
        uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/sheets_batch_update"
        body = {"requests": requests}
    client, error = _client_or_error()
    if error:
        return error
    code, msg, response = _request(client, method, uri, paths=paths, body=body)
    if code != 0:
        return tool_error(f"Sheet {operation} failed: code={code} msg={msg}")
    return tool_result(success=True, operation=operation, **response)


_BITABLE_READ_OPERATIONS = {
    "get_app",
    "list_tables",
    "list_fields",
    "list_views",
    "list_records",
}
_BITABLE_TABLE_OPERATIONS = {
    "list_fields",
    "list_views",
    "list_records",
    "create_field",
    "update_field",
    "delete_field",
    "create_view",
    "update_view",
    "delete_view",
    "batch_create_records",
    "batch_update_records",
    "batch_delete_records",
}
_DIRECT_DOCX_BITABLE_VIEWS = {"grid": 1, "kanban": 2}
_BITABLE_VIEW_TYPES = {*_DIRECT_DOCX_BITABLE_VIEWS, "gallery", "gantt", "form"}


def _positive_page_size(
    data: dict[str, Any], maximum: int = 100
) -> tuple[int, str | None]:
    value = data.get("page_size", maximum)
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= maximum
    ):
        return 0, f"data.page_size must be an integer between 1 and {maximum}"
    return value, None


def _bitable_read_queries(
    operation: str, data: dict[str, Any], view_id: str
) -> tuple[list[tuple[str, str]], str | None]:
    page_size, error = _positive_page_size(data)
    if error:
        return [], error
    queries = [
        ("user_id_type", "open_id"),
        ("page_size", str(page_size)),
    ]
    page_token = str(data.get("page_token") or "").strip()
    if page_token:
        queries.append(("page_token", page_token))
    if operation == "list_records" and view_id:
        queries.append(("view_id", view_id))
    return queries, None


def _document_index(value: Any) -> tuple[int | None, str | None]:
    if isinstance(value, bool):
        return None, "index must be an integer"
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None, "index must be an integer"
    if index < -1:
        return None, "index must be -1 or greater"
    return index, None


def _created_bitable_identity(data: dict[str, Any]) -> tuple[str, str]:
    children = data.get("children")
    if (
        not isinstance(children, list)
        or not children
        or not isinstance(children[0], dict)
    ):
        return "", ""
    child = children[0]
    bitable = child.get("bitable")
    if not isinstance(bitable, dict):
        return str(child.get("block_id") or ""), ""
    return str(child.get("block_id") or ""), str(bitable.get("token") or "")


def _created_board_identity(data: dict[str, Any]) -> tuple[str, str]:
    children = data.get("children")
    if (
        not isinstance(children, list)
        or not children
        or not isinstance(children[0], dict)
    ):
        return "", ""
    child = children[0]
    board = child.get("board")
    if not isinstance(board, dict):
        return str(child.get("block_id") or ""), ""
    whiteboard_id = board.get("token") or board.get("whiteboard_id")
    return str(child.get("block_id") or ""), str(whiteboard_id or "")


def _board_client_token(data: dict[str, Any]) -> tuple[str, str | None]:
    raw = data.get("client_token")
    if raw is None or raw == "":
        return "", None
    if not isinstance(raw, str) or not 10 <= len(raw) <= 64:
        return "", "client_token must contain 10-64 characters"
    if any(ord(character) < 32 for character in raw):
        return "", "client_token must not contain control characters"
    return raw, None


def _board_syntax_request(
    data: dict[str, Any], *, default_overwrite: bool = False
) -> tuple[dict[str, Any] | None, list[tuple[str, str]], str | None]:
    source = data.get("source")
    if not isinstance(source, str) or not source.strip():
        return None, [], "source must be a non-empty string"
    if len(source) > 100000:
        return None, [], "source must not exceed 100000 characters"
    syntax_type = str(data.get("syntax_type") or "mermaid").strip().lower()
    if syntax_type not in _BOARD_SYNTAX_TYPES:
        return None, [], "syntax_type must be plantuml, mermaid, or svg"
    diagram_type = str(data.get("diagram_type") or "auto").strip().lower()
    if diagram_type not in _BOARD_DIAGRAM_TYPES:
        return (
            None,
            [],
            "diagram_type must be auto, mindmap, sequence, activity, class, er, "
            "flowchart, state, or component",
        )
    overwrite = data.get("overwrite", default_overwrite)
    if not isinstance(overwrite, bool):
        return None, [], "overwrite must be a boolean"
    parse_mode = data.get("parse_mode", 1)
    if isinstance(parse_mode, bool) or parse_mode not in {0, 1}:
        return None, [], "parse_mode must be 0 or 1"
    client_token, error = _board_client_token(data)
    if error:
        return None, [], error
    queries = [("client_token", client_token)] if client_token else []
    return (
        {
            "plant_uml_code": source,
            "syntax_type": _BOARD_SYNTAX_TYPES[syntax_type],
            "diagram_type": _BOARD_DIAGRAM_TYPES[diagram_type],
            "parse_mode": parse_mode,
            "overwrite": overwrite,
        },
        queries,
        None,
    )


def _lookup_first_bitable_table(
    client: Any, app_token: str
) -> tuple[str, dict[str, Any], str | None]:
    last_error = "Bitable default table is not visible yet"
    last_data: dict[str, Any] = {}
    for attempt in range(3):
        code, msg, data = _request(
            client,
            "GET",
            "/open-apis/bitable/v1/apps/:app_token/tables",
            paths={"app_token": app_token},
            queries=[("page_size", "100")],
        )
        last_data = data
        items = data.get("items")
        if code == 0 and isinstance(items, list) and items:
            first = items[0]
            if isinstance(first, dict) and str(first.get("table_id") or "").strip():
                return str(first["table_id"]), first, None
        if code != 0:
            return "", data, (
                f"List embedded Bitable tables failed: code={code} msg={msg}"
            )
        last_error = "Embedded Bitable exists but its default table is not visible yet"
        if attempt < 2:
            time.sleep(0.2 * (attempt + 1))
    return "", last_data, last_error


def handle_doc_embed_bitable(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    requested_view = str(args.get("view_type") or "grid").strip().lower()
    view_name = str(args.get("view_name") or requested_view.title()).strip()
    if not document_id or not parent_block_id:
        return tool_error("document_id is required")
    if requested_view not in _BITABLE_VIEW_TYPES:
        return tool_error("view_type must be grid, kanban, gallery, gantt, or form")
    if requested_view not in _DIRECT_DOCX_BITABLE_VIEWS and not view_name:
        return tool_error("view_name must be non-empty")
    index, error = _document_index(args.get("index", -1))
    if error:
        return tool_error(error)

    setup = args.get("setup")
    if setup is not None and not isinstance(setup, dict):
        return tool_error("setup must be an object")
    client, error = _client_or_error()
    if error:
        return error
    # A configured kanban must be created only after its grouping field exists.
    # Advanced views likewise need fields and records before view creation.
    embedded_view = (
        requested_view
        if setup is None and requested_view in _DIRECT_DOCX_BITABLE_VIEWS
        else "grid"
    )
    code, msg, create_data = _request(
        client,
        "POST",
        _CREATE_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": parent_block_id},
        queries=[("document_revision_id", "-1")],
        body={
            "index": index,
            "children": [
                {
                    "block_type": 18,
                    "bitable": {"view_type": _DIRECT_DOCX_BITABLE_VIEWS[embedded_view]},
                }
            ],
        },
    )
    if code != 0:
        return tool_error(f"Create embedded Bitable failed: code={code} msg={msg}")

    block_id, raw_token = _created_bitable_identity(create_data)
    app_token, token_table_id = split_embedded_bitable_token(raw_token)
    base_result: dict[str, Any] = {
        "success": True,
        "status": "complete",
        "document_id": document_id,
        "block_id": block_id,
        "raw_token": raw_token,
        "app_token": app_token,
        "requested_view_type": requested_view,
        "embedded_view_type": embedded_view,
        "requires_manual_view_switch": requested_view != embedded_view,
    }
    if "document_revision_id" in create_data:
        base_result["document_revision_id"] = create_data["document_revision_id"]
    if not app_token:
        base_result.update(
            status="partial",
            setup_error=(
                "The Bitable block was created but Feishu did not return its app_token. "
                "Inspect the block before continuing; do not create another block."
            ),
        )
        return tool_result(**base_result)

    table_id = token_table_id
    table: dict[str, Any] = {}
    table_error: str | None = None
    if not table_id:
        table_id, table, table_error = _lookup_first_bitable_table(client, app_token)
    if table_id:
        base_result["table_id"] = table_id
        if table:
            base_result["table"] = table
    else:
        base_result.update(
            status="partial",
            setup_error=(
                f"{table_error}. The embedded block already exists; use "
                "feishu_bitable_edit list_tables with the returned app_token. "
                "Do not create another block."
            ),
        )
        return tool_result(**base_result)

    if setup is None:
        if requested_view == "grid":
            return tool_result(**base_result)
        if requested_view == "kanban":
            base_result.update(
                status="needs_manual_configuration",
                manual_actions=[
                    "Create a single-select grouping field and confirm the embedded "
                    "kanban groups by it. Prefer setup.group_field for reliable creation."
                ],
            )
            return tool_result(**base_result)
        base_result.update(
            status="needs_setup",
            setup_error=(
                f"The embedded grid exists, but the requested {requested_view} view "
                "was intentionally not created against an empty table. Call "
                "feishu_bitable_sync with fields, records, and the requested view; "
                "do not create another embedded block."
            ),
        )
        return tool_result(**base_result)

    config = dict(setup)
    views = list(config.get("views") or [])
    if requested_view != "grid" and not any(
        isinstance(item, dict)
        and str(item.get("view_name") or "").strip() == view_name
        for item in views
    ):
        requested_spec: dict[str, Any] = {
            "view_name": view_name,
            "view_type": requested_view,
        }
        if requested_view == "gantt":
            requested_spec["start_field"] = str(config.get("start_field") or "").strip()
            requested_spec["end_field"] = str(config.get("end_field") or "").strip()
        elif requested_view == "kanban":
            requested_spec["group_field"] = str(config.get("group_field") or "").strip()
        views.append(requested_spec)
    config["views"] = views
    for convenience_key in ("start_field", "end_field", "group_field"):
        config.pop(convenience_key, None)
    if "delete_blank_records" not in config:
        config["delete_blank_records"] = True
    try:
        sync_result = sync_bitable(client, app_token, table_id, config)
    except RuntimeError as exc:
        base_result.update(
            status="partial",
            setup_error=(
                f"Embedded Bitable setup failed: {exc}. The embedded block already "
                "exists; fix the configuration and continue with feishu_bitable_sync. "
                "Do not create another block."
            ),
        )
        return tool_result(**base_result)

    base_result["status"] = sync_result["status"]
    base_result["sync"] = sync_result
    base_result["manual_actions"] = sync_result["manual_actions"]
    return tool_result(**base_result)


def handle_doc_embed_diagram(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    if not document_id or not parent_block_id:
        return tool_error("document_id is required")
    index, error = _document_index(args.get("index", -1))
    if error:
        return tool_error(error)
    align = args.get("align", 2)
    width = args.get("width", 800)
    height = args.get("height", 450)
    if isinstance(align, bool) or align not in {1, 2, 3}:
        return tool_error("align must be 1, 2, or 3")
    for field, value in (("width", width), ("height", height)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 90:
            return tool_error(f"{field} must be an integer of at least 90")
    syntax_data = {
        "source": args.get("source"),
        "syntax_type": args.get("syntax_type", "mermaid"),
        "diagram_type": args.get("diagram_type", "auto"),
        "parse_mode": 1,
        "overwrite": False,
        "client_token": args.get("client_token"),
    }
    render_body, render_queries, error = _board_syntax_request(syntax_data)
    if error:
        return tool_error(error)

    client, error = _client_or_error()
    if error:
        return error
    code, msg, create_data = _request(
        client,
        "POST",
        _CREATE_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": parent_block_id},
        queries=[("document_revision_id", "-1")],
        body={
            "index": index,
            "children": [
                {
                    "block_type": 43,
                    "board": {"align": align, "width": width, "height": height},
                }
            ],
        },
    )
    if code != 0:
        return tool_error(f"Create embedded Board failed: code={code} msg={msg}")

    block_id, whiteboard_id = _created_board_identity(create_data)
    result: dict[str, Any] = {
        "success": True,
        "status": "complete",
        "document_id": document_id,
        "block_id": block_id,
        "whiteboard_id": whiteboard_id,
        "syntax_type": str(args.get("syntax_type") or "mermaid").lower(),
        "diagram_type": str(args.get("diagram_type") or "auto").lower(),
    }
    if "document_revision_id" in create_data:
        result["document_revision_id"] = create_data["document_revision_id"]
    if not whiteboard_id:
        result.update(
            status="partial",
            render_error=(
                "The embedded Board was created but Feishu did not return its "
                "whiteboard_id. Inspect the Board Block before continuing; do not "
                "create another Board."
            ),
        )
        return tool_result(**result)

    board_paths = {"whiteboard_id": whiteboard_id}
    board_base = "/open-apis/board/v1/whiteboards/:whiteboard_id"
    code, msg, render_data = _request(
        client,
        "POST",
        f"{board_base}/nodes/plantuml",
        paths=board_paths,
        queries=render_queries,
        body=render_body,
    )
    if code != 0:
        result.update(
            status="partial",
            render_error=(
                f"Render diagram failed: code={code} msg={msg}. The embedded Board "
                "already exists; retry with feishu_board_edit operation=render_syntax "
                "and the returned whiteboard_id. Do not create another Board."
            ),
        )
        return tool_result(**result)
    if render_data.get("node_id"):
        result["created_node_id"] = render_data["node_id"]

    code, msg, verify_data = _request(
        client,
        "GET",
        f"{board_base}/nodes",
        paths=board_paths,
    )
    nodes = verify_data.get("nodes")
    if code != 0 or not isinstance(nodes, list) or not nodes:
        result.update(
            status="partial",
            verification_error=(
                f"Diagram render returned success but node verification failed: "
                f"code={code} msg={msg}. Re-read with feishu_board_edit get_nodes "
                "before retrying; do not create another Board."
            ),
        )
        return tool_result(**result)
    result["verified_node_count"] = len(nodes)
    return tool_result(**result)


def handle_bitable_edit(args: dict, **_: Any) -> str:
    app_token = str(args.get("app_token") or "").strip()
    operation = str(args.get("operation") or "").strip()
    data, error = _data_or_error(args)
    operations = FEISHU_BITABLE_EDIT_SCHEMA["parameters"]["properties"]["operation"]["enum"]
    if not app_token or operation not in operations:
        return tool_error("app_token and a supported operation are required")
    if error:
        return tool_error(error)
    table_id = str(args.get("table_id") or "").strip()
    field_id = str(args.get("field_id") or "").strip()
    view_id = str(args.get("view_id") or "").strip()
    needs_table = operation in _BITABLE_TABLE_OPERATIONS
    if needs_table and not table_id:
        return tool_error("table_id is required for this operation")
    if operation in {"update_field", "delete_field"} and not field_id:
        return tool_error("field_id is required for this operation")
    if operation in {"update_view", "delete_view"} and not view_id:
        return tool_error("view_id is required for this operation")

    base = "/open-apis/bitable/v1/apps/:app_token"
    paths = {"app_token": app_token}
    method, uri = "POST", base
    body: dict[str, Any] | None = data
    queries = [("user_id_type", "open_id")]
    if operation == "get_app":
        method, body = "GET", None
    elif operation == "list_tables":
        method, uri, body = "GET", f"{base}/tables", None
    elif operation == "update_app":
        method, uri = "PUT", base
    elif operation in {"batch_create_tables", "batch_delete_tables"}:
        uri = f"{base}/tables/{'batch_create' if operation.endswith('create_tables') else 'batch_delete'}"
    else:
        paths["table_id"] = table_id
        table_base = f"{base}/tables/:table_id"
        if operation == "list_fields":
            method, uri, body = "GET", f"{table_base}/fields", None
        elif operation == "list_views":
            method, uri, body = "GET", f"{table_base}/views", None
        elif operation == "list_records":
            method, uri, body = "GET", f"{table_base}/records", None
        elif operation.endswith("_field"):
            uri = f"{table_base}/fields"
            if operation != "create_field":
                uri += "/:field_id"
                paths["field_id"] = field_id
                method = "PUT" if operation == "update_field" else "DELETE"
        elif operation.endswith("_view"):
            uri = f"{table_base}/views"
            if operation != "create_view":
                uri += "/:view_id"
                paths["view_id"] = view_id
                method = "PATCH" if operation == "update_view" else "DELETE"
        else:
            record_suffix = {
                "batch_create_records": "batch_create",
                "batch_update_records": "batch_update",
                "batch_delete_records": "batch_delete",
            }[operation]
            uri = f"{table_base}/records/{record_suffix}"
    if (
        operation in _BITABLE_READ_OPERATIONS
        and "fetch_all" in data
        and not isinstance(data["fetch_all"], bool)
    ):
        return tool_error("data.fetch_all must be a boolean")
    if operation in _BITABLE_READ_OPERATIONS and operation != "get_app":
        queries, error = _bitable_read_queries(operation, data, view_id)
        if error:
            return tool_error(error)
    if method == "DELETE":
        body = None
    client, error = _client_or_error()
    if error:
        return error
    if operation in _BITABLE_READ_OPERATIONS - {"get_app"} and data.get("fetch_all") is True:
        if data.get("page_token"):
            return tool_error("data.page_token cannot be combined with fetch_all")
        max_items = data.get("max_items", 5000)
        if (
            isinstance(max_items, bool)
            or not isinstance(max_items, int)
            or not 1 <= max_items <= 20000
        ):
            return tool_error("data.max_items must be an integer between 1 and 20000")
        page_size, pagination_error = _positive_page_size(data)
        if pagination_error:
            return tool_error(pagination_error)
        base_queries = [("user_id_type", "open_id")]
        if operation == "list_records" and view_id:
            base_queries.append(("view_id", view_id))
        try:
            response = list_all_pages(
                client,
                uri,
                paths=paths,
                page_size=page_size,
                max_items=max_items,
                base_queries=base_queries,
                operation=f"Bitable {operation}",
            )
        except RuntimeError as exc:
            return tool_error(str(exc))
        return tool_result(success=True, operation=operation, **response)
    code, msg, response = _request(
        client,
        method,
        uri,
        paths=paths,
        queries=queries,
        body=body,
    )
    if code != 0:
        return tool_error(f"Bitable {operation} failed: code={code} msg={msg}")
    return tool_result(success=True, operation=operation, **response)


def handle_board_edit(args: dict, **_: Any) -> str:
    whiteboard_id = str(args.get("whiteboard_id") or "").strip()
    operation = str(args.get("operation") or "").strip()
    data, error = _data_or_error(args)
    if not whiteboard_id or operation not in {
        "get_nodes",
        "get_theme",
        "create_nodes",
        "render_syntax",
        "delete_nodes",
        "update_theme",
    }:
        return tool_error("whiteboard_id and a supported operation are required")
    if error:
        return tool_error(error)
    paths = {"whiteboard_id": whiteboard_id}
    base = "/open-apis/board/v1/whiteboards/:whiteboard_id"
    queries: list[tuple[str, str]] = []
    if operation == "get_nodes":
        method, uri, body = "GET", f"{base}/nodes", None
    elif operation == "get_theme":
        method, uri, body = "GET", f"{base}/theme", None
    elif operation == "create_nodes":
        nodes = data.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 3000:
            return tool_error("data.nodes must contain 1-3000 nodes")
        overwrite = data.get("overwrite", False)
        if not isinstance(overwrite, bool):
            return tool_error("data.overwrite must be a boolean")
        client_token, error = _board_client_token(data)
        if error:
            return tool_error(f"data.{error}")
        if client_token:
            queries.append(("client_token", client_token))
        method, uri, body = (
            "POST",
            f"{base}/nodes",
            {"nodes": nodes, "overwrite": overwrite},
        )
    elif operation == "render_syntax":
        body, queries, error = _board_syntax_request(data)
        if error:
            return tool_error(f"data.{error}")
        method, uri = "POST", f"{base}/nodes/plantuml"
    elif operation == "delete_nodes":
        ids = data.get("ids")
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(
            not isinstance(value, str) or not value.strip() for value in ids
        ):
            return tool_error("data.ids must contain 1-100 non-empty node IDs")
        method, uri, body = "DELETE", f"{base}/nodes/batch_delete", {"ids": ids}
    else:
        theme = str(data.get("theme") or "").strip()
        if theme not in {"classic", "minimalist_gray", "retro", "vibrant_color", "default"}:
            return tool_error("data.theme is unsupported")
        method, uri, body = "POST", f"{base}/update_theme", {"theme": theme}
    client, error = _client_or_error()
    if error:
        return error
    code, msg, response = _request(
        client, method, uri, paths=paths, queries=queries, body=body
    )
    if code != 0:
        return tool_error(f"Board {operation} failed: code={code} msg={msg}")
    return tool_result(success=True, operation=operation, **response)


def handle_task_edit(args: dict, **_: Any) -> str:
    operation = str(args.get("operation") or "").strip()
    task_guid = str(args.get("task_guid") or "").strip()
    data, error = _data_or_error(args)
    operations = FEISHU_TASK_EDIT_SCHEMA["parameters"]["properties"]["operation"]["enum"]
    if operation not in operations:
        return tool_error("a supported operation is required")
    if error:
        return tool_error(error)
    if operation != "create" and not task_guid:
        return tool_error("task_guid is required for this operation")
    base = "/open-apis/task/v2/tasks"
    paths: dict[str, str] = {}
    queries = [("user_id_type", "open_id")]
    body: dict[str, Any] | None = data
    if operation == "create":
        method, uri = "POST", base
    else:
        paths["task_guid"] = task_guid
        uri = f"{base}/:task_guid"
        if operation == "update":
            method = "PATCH"
        elif operation == "delete":
            method, body = "DELETE", None
        else:
            method, uri = "POST", f"{uri}/{operation}"
    client, error = _client_or_error()
    if error:
        return error
    code, msg, response = _request(
        client, method, uri, paths=paths, queries=queries, body=body
    )
    if code != 0:
        return tool_error(f"Task {operation} failed: code={code} msg={msg}")
    return tool_result(success=True, operation=operation, **response)


def register_document_domain_tools(ctx: Any) -> None:
    for name, schema, handler, description, emoji in (
        ("feishu_sheet_edit", FEISHU_SHEET_EDIT_SCHEMA, handle_sheet_edit, "Edit Sheet cells, styles, rows, columns, and tabs", "📊"),
        ("feishu_doc_resolve_bitable", FEISHU_DOC_RESOLVE_BITABLE_SCHEMA, handle_doc_resolve_bitable, "Resolve the unique Bitable embedded in a document", "🔎"),
        ("feishu_doc_embed_bitable", FEISHU_DOC_EMBED_BITABLE_SCHEMA, handle_doc_embed_bitable, "Create and safely configure an embedded Bitable", "🧩"),
        ("feishu_doc_embed_diagram", FEISHU_DOC_EMBED_DIAGRAM_SCHEMA, handle_doc_embed_diagram, "Create and render a directly embedded UML or diagram Board", "🗺️"),
        ("feishu_bitable_sync", FEISHU_BITABLE_SYNC_SCHEMA, handle_bitable_sync, "Declaratively configure and idempotently sync a Bitable", "🔄"),
        ("feishu_bitable_edit", FEISHU_BITABLE_EDIT_SCHEMA, handle_bitable_edit, "Inspect and edit Bitable tables, fields, views, and records", "🗃️"),
        ("feishu_board_edit", FEISHU_BOARD_EDIT_SCHEMA, handle_board_edit, "Inspect/create/delete Board nodes and update its theme", "🎨"),
        ("feishu_task_edit", FEISHU_TASK_EDIT_SCHEMA, handle_task_edit, "Create and fully manage Feishu Task v2 tasks", "✅"),
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
    "handle_bitable_sync",
    "handle_doc_resolve_bitable",
    "handle_doc_embed_bitable",
    "handle_doc_embed_diagram",
    "handle_bitable_edit",
    "handle_board_edit",
    "handle_sheet_edit",
    "handle_task_edit",
    "register_document_domain_tools",
]
