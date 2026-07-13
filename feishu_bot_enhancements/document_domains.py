"""Editors for Sheet, Bitable, Board, and Task objects embedded in documents."""

from __future__ import annotations

from typing import Any

from tools.registry import tool_error, tool_result

from .document_tools import _check_feishu, _client_or_error, _request


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
        "Edit the internal structure and records of a Feishu Bitable. Supports "
        "app metadata, batch table creation/deletion, field and view CRUD, and "
        "batch record creation/update/deletion. data is the corresponding "
        "official request body (for example {records:[{fields:{...}}]})."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {"type": "string"},
            "operation": {
                "type": "string",
                "enum": [
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


FEISHU_BOARD_EDIT_SCHEMA = {
    "name": "feishu_board_edit",
    "description": (
        "Inspect Board nodes/theme, create up to 3000 nodes, recursively delete "
        "up to 100 node IDs, or update the Board theme. Feishu currently "
        "publishes no API for "
        "updating existing node properties in place. data uses {nodes:[...]}, "
        "{ids:[...]}, or {theme:classic|minimalist_gray|retro|vibrant_color|default}."
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
    needs_table = operation in {
        "create_field", "update_field", "delete_field", "create_view",
        "update_view", "delete_view", "batch_create_records",
        "batch_update_records", "batch_delete_records",
    }
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
    if operation == "update_app":
        method, uri = "PUT", base
    elif operation in {"batch_create_tables", "batch_delete_tables"}:
        uri = f"{base}/tables/{'batch_create' if operation.endswith('create_tables') else 'batch_delete'}"
    else:
        paths["table_id"] = table_id
        table_base = f"{base}/tables/:table_id"
        if operation.endswith("_field"):
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
    if method == "DELETE":
        body = None
    client, error = _client_or_error()
    if error:
        return error
    code, msg, response = _request(
        client,
        method,
        uri,
        paths=paths,
        queries=[("user_id_type", "open_id")],
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
        "delete_nodes",
        "update_theme",
    }:
        return tool_error("whiteboard_id and a supported operation are required")
    if error:
        return tool_error(error)
    paths = {"whiteboard_id": whiteboard_id}
    base = "/open-apis/board/v1/whiteboards/:whiteboard_id"
    if operation == "get_nodes":
        method, uri, body = "GET", f"{base}/nodes", None
    elif operation == "get_theme":
        method, uri, body = "GET", f"{base}/theme", None
    elif operation == "create_nodes":
        nodes = data.get("nodes")
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 3000:
            return tool_error("data.nodes must contain 1-3000 nodes")
        method, uri, body = "POST", f"{base}/nodes", {"nodes": nodes}
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
    code, msg, response = _request(client, method, uri, paths=paths, body=body)
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
        ("feishu_bitable_edit", FEISHU_BITABLE_EDIT_SCHEMA, handle_bitable_edit, "Edit Bitable tables, fields, views, and records", "🗃️"),
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
    "handle_bitable_edit",
    "handle_board_edit",
    "handle_sheet_edit",
    "handle_task_edit",
    "register_document_domain_tools",
]
