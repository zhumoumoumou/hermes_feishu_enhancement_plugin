"""Reliable high-level workflows for Feishu Bitable objects."""

from __future__ import annotations

import json
from typing import Any

from tools.registry import tool_error, tool_result

from .document_tools import _client_or_error, _request


_LIST_BLOCKS_URI = "/open-apis/docx/v1/documents/:document_id/blocks"
_TABLE_BASE = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id"
_VIEW_TYPES = {"grid", "kanban", "gallery", "gantt", "form"}
_BATCH_SIZE = 500
_MAX_PAGES = 50


BITABLE_FIELD_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "field_name": {"type": "string"},
        "type": {"type": "integer", "minimum": 1},
        "primary": {
            "type": "boolean",
            "default": False,
            "description": "Rename the existing primary field instead of creating another field.",
        },
        "property": {"type": "object", "additionalProperties": True},
        "description": {"type": "object", "additionalProperties": True},
        "ui_type": {"type": "string"},
    },
    "required": ["field_name", "type"],
}

BITABLE_RECORD_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {"type": "object", "additionalProperties": True},
    },
    "required": ["fields"],
}

BITABLE_VIEW_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "view_name": {"type": "string"},
        "view_type": {
            "type": "string",
            "enum": ["grid", "kanban", "gallery", "gantt", "form"],
        },
        "start_field": {
            "type": "string",
            "description": "Required for a validated gantt workflow.",
        },
        "end_field": {
            "type": "string",
            "description": "Required for a validated gantt workflow.",
        },
        "group_field": {
            "type": "string",
            "description": "Required for a validated kanban workflow; must be single-select.",
        },
        "require_complete_ranges": {
            "type": "boolean",
            "default": True,
            "description": "Require every supplied record to contain a valid start/end range.",
        },
    },
    "required": ["view_name", "view_type"],
}

BITABLE_SETUP_SCHEMA = {
    "type": "object",
    "properties": {
        "fields": {
            "type": "array",
            "maxItems": 200,
            "items": BITABLE_FIELD_SPEC_SCHEMA,
        },
        "records": {
            "type": "array",
            "maxItems": 5000,
            "items": BITABLE_RECORD_SPEC_SCHEMA,
        },
        "key_field": {
            "type": "string",
            "description": "Unique field used to create or update records idempotently.",
        },
        "views": {
            "type": "array",
            "maxItems": 50,
            "items": BITABLE_VIEW_SPEC_SCHEMA,
        },
        "delete_blank_records": {
            "type": "boolean",
            "default": False,
            "description": "Delete records whose fields object is empty.",
        },
        "max_items": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20000,
            "default": 5000,
        },
        "start_field": {
            "type": "string",
            "description": "Convenience field for the requested gantt view when embedding.",
        },
        "end_field": {
            "type": "string",
            "description": "Convenience field for the requested gantt view when embedding.",
        },
        "group_field": {
            "type": "string",
            "description": "Convenience field for the requested kanban view when embedding.",
        },
    },
}

FEISHU_BITABLE_SYNC_SCHEMA = {
    "name": "feishu_bitable_sync",
    "description": (
        "Declaratively configure and idempotently update an existing Feishu Bitable. "
        "Creates missing fields, upserts records by a unique key, optionally removes "
        "blank default rows, and creates missing views only after fields and records "
        "exist. Gantt date ranges and kanban grouping candidates are validated; UI-only "
        "view bindings are reported as manual verification instead of false success."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_token": {"type": "string"},
            "table_id": {"type": "string"},
            **BITABLE_SETUP_SCHEMA["properties"],
        },
        "required": ["app_token", "table_id"],
    },
}

FEISHU_DOC_RESOLVE_BITABLE_SCHEMA = {
    "name": "feishu_doc_resolve_bitable",
    "description": (
        "Inspect a Feishu docx document and resolve embedded Bitable block IDs, raw "
        "tokens, app tokens, and table IDs. By default exactly one matching embedded "
        "Bitable is required, preventing accidental updates to a duplicate data source."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "block_id": {"type": "string"},
            "app_token": {"type": "string"},
            "table_id": {"type": "string"},
            "require_unique": {"type": "boolean", "default": True},
        },
        "required": ["document_id"],
    },
}


def split_embedded_bitable_token(token: str) -> tuple[str, str]:
    """Split Docx's read-only ``AppToken_TableID`` token when possible."""
    value = str(token or "").strip()
    separator = value.rfind("_tbl")
    if separator > 0:
        app_token = value[:separator]
        table_id = value[separator + 1 :]
        if app_token and table_id:
            return app_token, table_id
    return value, ""


def _request_ok(
    client: Any,
    method: str,
    uri: str,
    *,
    paths: dict[str, str],
    queries: list[tuple[str, str]] | None = None,
    body: dict[str, Any] | None = None,
    operation: str,
) -> dict[str, Any]:
    code, msg, data = _request(
        client, method, uri, paths=paths, queries=queries, body=body
    )
    if code != 0:
        raise RuntimeError(f"{operation} failed: code={code} msg={msg}")
    return data


def list_all_pages(
    client: Any,
    uri: str,
    *,
    paths: dict[str, str],
    page_size: int = 100,
    max_items: int = 5000,
    base_queries: list[tuple[str, str]] | None = None,
    operation: str,
) -> dict[str, Any]:
    """Follow Feishu pagination with explicit bounds and loop protection."""
    items: list[Any] = []
    page_token = ""
    seen_tokens: set[str] = set()
    last_data: dict[str, Any] = {}
    for page_count in range(1, _MAX_PAGES + 1):
        queries = list(base_queries or [])
        queries.append(("page_size", str(page_size)))
        if page_token:
            queries.append(("page_token", page_token))
        last_data = _request_ok(
            client,
            "GET",
            uri,
            paths=paths,
            queries=queries,
            operation=operation,
        )
        page_items = last_data.get("items")
        if isinstance(page_items, list):
            items.extend(page_items)
        if len(items) > max_items:
            raise RuntimeError(
                f"{operation} exceeded max_items={max_items}; narrow the target"
            )
        next_token = str(last_data.get("page_token") or "")
        has_more = last_data.get("has_more") is True
        if not has_more:
            result = dict(last_data)
            result.update(items=items, page_count=page_count, truncated=False)
            return result
        if not next_token or next_token in seen_tokens:
            raise RuntimeError(f"{operation} returned an invalid pagination token")
        seen_tokens.add(next_token)
        page_token = next_token
    raise RuntimeError(f"{operation} exceeded {_MAX_PAGES} pages")


def _field_body(spec: dict[str, Any]) -> dict[str, Any]:
    body = {
        "field_name": str(spec.get("field_name") or "").strip(),
        "type": spec.get("type"),
    }
    for key in ("property", "description", "ui_type"):
        if key in spec:
            body[key] = spec[key]
    return body


def _validate_setup(config: dict[str, Any]) -> tuple[list, list, list, str, bool, int]:
    fields = config.get("fields", [])
    records = config.get("records", [])
    views = config.get("views", [])
    key_field = str(config.get("key_field") or "").strip()
    delete_blank = config.get("delete_blank_records", False)
    max_items = config.get("max_items", 5000)
    if not isinstance(fields, list) or not all(isinstance(item, dict) for item in fields):
        raise RuntimeError("fields must be an array of objects")
    if len(fields) > 200:
        raise RuntimeError("fields must contain at most 200 items")
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise RuntimeError("records must be an array of objects")
    if len(records) > 5000:
        raise RuntimeError("records must contain at most 5000 items")
    if not isinstance(views, list) or not all(isinstance(item, dict) for item in views):
        raise RuntimeError("views must be an array of objects")
    if len(views) > 50:
        raise RuntimeError("views must contain at most 50 items")
    if records and not key_field:
        raise RuntimeError("key_field is required when records are supplied")
    if not isinstance(delete_blank, bool):
        raise RuntimeError("delete_blank_records must be a boolean")
    if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 20000:
        raise RuntimeError("max_items must be an integer between 1 and 20000")
    return fields, records, views, key_field, delete_blank, max_items


def _ensure_fields(
    client: Any,
    app_token: str,
    table_id: str,
    specs: list[dict[str, Any]],
    max_items: int,
) -> tuple[dict[str, dict[str, Any]], int, int]:
    paths = {"app_token": app_token, "table_id": table_id}
    uri = f"{_TABLE_BASE}/fields"
    data = list_all_pages(
        client,
        uri,
        paths=paths,
        max_items=max_items,
        base_queries=[("user_id_type", "open_id")],
        operation="list Bitable fields",
    )
    existing = [item for item in data["items"] if isinstance(item, dict)]
    names = [str(item.get("field_name") or "") for item in existing]
    duplicates = sorted({name for name in names if name and names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"Bitable contains duplicate field names: {duplicates}")
    requested_names: set[str] = set()
    primary_specs = 0
    created = 0
    renamed_primary = 0
    by_name = {str(item.get("field_name") or ""): item for item in existing}
    for index, spec in enumerate(specs):
        name = str(spec.get("field_name") or "").strip()
        field_type = spec.get("type")
        if not name or isinstance(field_type, bool) or not isinstance(field_type, int) or field_type < 1:
            raise RuntimeError(f"fields[{index}] requires a non-empty field_name and integer type")
        if name in requested_names:
            raise RuntimeError(f"fields contains duplicate name: {name}")
        requested_names.add(name)
        is_primary = spec.get("primary", False)
        if not isinstance(is_primary, bool):
            raise RuntimeError(f"fields[{index}].primary must be a boolean")
        primary_specs += int(is_primary)
        if primary_specs > 1:
            raise RuntimeError("only one field may set primary=true")
        current = by_name.get(name)
        if current:
            if int(current.get("type") or 0) != field_type:
                raise RuntimeError(f"field {name} exists with a different type")
            if is_primary and current.get("is_primary") is not True:
                raise RuntimeError(f"field {name} exists but is not the primary field")
            continue
        if is_primary:
            primary = [item for item in existing if item.get("is_primary") is True]
            if len(primary) != 1:
                raise RuntimeError("Bitable must have exactly one existing primary field")
            field_id = str(primary[0].get("field_id") or "")
            if not field_id:
                raise RuntimeError("existing primary field has no field_id")
            _request_ok(
                client,
                "PUT",
                f"{uri}/:field_id",
                paths={**paths, "field_id": field_id},
                queries=[("user_id_type", "open_id")],
                body=_field_body(spec),
                operation=f"rename primary field to {name}",
            )
            renamed_primary += 1
        else:
            _request_ok(
                client,
                "POST",
                uri,
                paths=paths,
                queries=[("user_id_type", "open_id")],
                body=_field_body(spec),
                operation=f"create field {name}",
            )
            created += 1
    refreshed = list_all_pages(
        client,
        uri,
        paths=paths,
        max_items=max_items,
        base_queries=[("user_id_type", "open_id")],
        operation="verify Bitable fields",
    )["items"]
    by_name = {
        str(item.get("field_name") or ""): item
        for item in refreshed
        if isinstance(item, dict)
    }
    missing = sorted(requested_names - set(by_name))
    if missing:
        raise RuntimeError(f"created fields are not visible: {missing}")
    return by_name, created, renamed_primary


def _record_key(value: Any) -> str:
    if value in (None, "", []):
        raise RuntimeError("record key values must be non-empty")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _chunks(values: list[Any], size: int = _BATCH_SIZE):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _upsert_records(
    client: Any,
    app_token: str,
    table_id: str,
    records: list[dict[str, Any]],
    key_field: str,
    delete_blank: bool,
    max_items: int,
) -> dict[str, int]:
    paths = {"app_token": app_token, "table_id": table_id}
    uri = f"{_TABLE_BASE}/records"
    existing = list_all_pages(
        client,
        uri,
        paths=paths,
        max_items=max_items,
        base_queries=[("user_id_type", "open_id")],
        operation="list Bitable records",
    )["items"]
    blank_ids = [
        str(item.get("record_id") or "")
        for item in existing
        if isinstance(item, dict) and not (item.get("fields") or {})
    ]
    blank_ids = [value for value in blank_ids if value]
    if delete_blank:
        for chunk in _chunks(blank_ids):
            _request_ok(
                client,
                "POST",
                f"{uri}/batch_delete",
                paths=paths,
                queries=[("user_id_type", "open_id")],
                body={"records": chunk},
                operation="delete blank Bitable records",
            )
        existing = [item for item in existing if str(item.get("record_id") or "") not in blank_ids]
    else:
        blank_ids = []

    by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") or {}
        if key_field not in fields or fields.get(key_field) in (None, "", []):
            continue
        key = _record_key(fields[key_field])
        if key in by_key:
            raise RuntimeError(f"Bitable contains duplicate key_field value: {fields[key_field]!r}")
        by_key[key] = item

    creates: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    unchanged = 0
    input_keys: set[str] = set()
    for index, item in enumerate(records):
        fields = item.get("fields")
        if not isinstance(fields, dict):
            raise RuntimeError(f"records[{index}].fields must be an object")
        if key_field not in fields:
            raise RuntimeError(f"records[{index}] is missing key field {key_field}")
        key = _record_key(fields[key_field])
        if key in input_keys:
            raise RuntimeError(f"records contains duplicate key_field value: {fields[key_field]!r}")
        input_keys.add(key)
        current = by_key.get(key)
        if current is None:
            creates.append({"fields": fields})
            continue
        current_fields = current.get("fields") or {}
        if all(current_fields.get(name) == value for name, value in fields.items()):
            unchanged += 1
            continue
        record_id = str(current.get("record_id") or "")
        if not record_id:
            raise RuntimeError(f"existing record for {fields[key_field]!r} has no record_id")
        updates.append({"record_id": record_id, "fields": fields})

    for chunk in _chunks(creates):
        _request_ok(
            client,
            "POST",
            f"{uri}/batch_create",
            paths=paths,
            queries=[("user_id_type", "open_id")],
            body={"records": chunk},
            operation="create Bitable records",
        )
    for chunk in _chunks(updates):
        _request_ok(
            client,
            "POST",
            f"{uri}/batch_update",
            paths=paths,
            queries=[("user_id_type", "open_id")],
            body={"records": chunk},
            operation="update Bitable records",
        )
    return {
        "created": len(creates),
        "updated": len(updates),
        "unchanged": unchanged,
        "deleted_blank": len(blank_ids),
    }


def _validate_view(
    spec: dict[str, Any],
    fields: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    name = str(spec.get("view_name") or "").strip()
    view_type = str(spec.get("view_type") or "").strip().lower()
    if not name or view_type not in _VIEW_TYPES:
        raise RuntimeError("each view requires a non-empty view_name and supported view_type")
    result = {"view_name": name, "view_type": view_type}
    if view_type == "gantt":
        start_name = str(spec.get("start_field") or "").strip()
        end_name = str(spec.get("end_field") or "").strip()
        if not start_name or not end_name or start_name == end_name:
            raise RuntimeError(f"gantt view {name} requires distinct start_field and end_field")
        for field_name in (start_name, end_name):
            field = fields.get(field_name)
            if field is None or int(field.get("type") or 0) != 5:
                raise RuntimeError(f"gantt field {field_name} must exist with date type 5")
        require_ranges = spec.get("require_complete_ranges", True)
        if not isinstance(require_ranges, bool):
            raise RuntimeError("require_complete_ranges must be a boolean")
        if require_ranges:
            invalid: list[int] = []
            for index, record in enumerate(records):
                values = record.get("fields") or {}
                start = values.get(start_name)
                end = values.get(end_name)
                if (
                    isinstance(start, bool)
                    or not isinstance(start, int)
                    or isinstance(end, bool)
                    or not isinstance(end, int)
                    or start >= end
                ):
                    invalid.append(index)
            if invalid:
                raise RuntimeError(f"gantt view {name} has invalid record ranges at indexes {invalid}")
        date_fields = [
            field_name
            for field_name, field in fields.items()
            if int(field.get("type") or 0) == 5
        ]
        result.update(
            start_field=start_name,
            end_field=end_name,
            binding_candidate_unambiguous=set(date_fields) == {start_name, end_name},
            binding_status="manual_verification_required",
        )
    elif view_type == "kanban":
        group_name = str(spec.get("group_field") or "").strip()
        if not group_name:
            raise RuntimeError(f"kanban view {name} requires group_field")
        field = fields.get(group_name)
        if field is None or int(field.get("type") or 0) != 3:
            raise RuntimeError(f"kanban group field {group_name} must exist with single-select type 3")
        select_fields = [
            field_name
            for field_name, candidate in fields.items()
            if int(candidate.get("type") or 0) == 3
        ]
        result.update(
            group_field=group_name,
            binding_candidate_unambiguous=select_fields == [group_name],
            binding_status="manual_verification_required",
        )
    return result


def _ensure_views(
    client: Any,
    app_token: str,
    table_id: str,
    specs: list[dict[str, Any]],
    fields: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    max_items: int,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    paths = {"app_token": app_token, "table_id": table_id}
    uri = f"{_TABLE_BASE}/views"
    existing = list_all_pages(
        client,
        uri,
        paths=paths,
        max_items=max_items,
        base_queries=[("user_id_type", "open_id")],
        operation="list Bitable views",
    )["items"]
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in existing:
        if isinstance(item, dict):
            by_name.setdefault(str(item.get("view_name") or ""), []).append(item)
    requested_names: set[str] = set()
    results: list[dict[str, Any]] = []
    created = 0
    manual_actions: list[str] = []
    for spec in specs:
        checked = _validate_view(spec, fields, records)
        name = checked["view_name"]
        view_type = checked["view_type"]
        if name in requested_names:
            raise RuntimeError(f"views contains duplicate name: {name}")
        requested_names.add(name)
        matches = by_name.get(name, [])
        if len(matches) > 1:
            raise RuntimeError(f"Bitable contains duplicate view name: {name}")
        if not matches and view_type == "gantt" and not records:
            raise RuntimeError(
                f"gantt view {name} requires at least one supplied dated record "
                "before initial view creation"
            )
        if not matches and view_type == "kanban":
            group_field = checked["group_field"]
            if not any(
                (record.get("fields") or {}).get(group_field) not in (None, "", [])
                for record in records
            ):
                raise RuntimeError(
                    f"kanban view {name} requires at least one supplied record with "
                    f"a non-empty {group_field} value before initial view creation"
                )
        if matches:
            view = matches[0]
            if view.get("view_type") != view_type:
                raise RuntimeError(f"view {name} exists with a different type")
            checked.update(view_id=str(view.get("view_id") or ""), created=False)
        else:
            data = _request_ok(
                client,
                "POST",
                uri,
                paths=paths,
                queries=[("user_id_type", "open_id")],
                body={"view_name": name, "view_type": view_type},
                operation=f"create {view_type} view {name}",
            )
            view = data.get("view") or data
            checked.update(view_id=str(view.get("view_id") or ""), created=True)
            created += 1
        if checked.get("binding_status") == "manual_verification_required":
            if view_type == "gantt":
                manual_actions.append(
                    f"Open view {name} and confirm its start/end bindings are "
                    f"{checked['start_field']} and {checked['end_field']}."
                )
            elif view_type == "kanban":
                manual_actions.append(
                    f"Open view {name} and confirm it groups by {checked['group_field']}."
                )
        results.append(checked)
    return results, created, manual_actions


def _verify_records(
    client: Any,
    app_token: str,
    table_id: str,
    records: list[dict[str, Any]],
    key_field: str,
    max_items: int,
) -> int:
    if not records:
        return 0
    paths = {"app_token": app_token, "table_id": table_id}
    current = list_all_pages(
        client,
        f"{_TABLE_BASE}/records",
        paths=paths,
        max_items=max_items,
        base_queries=[("user_id_type", "open_id")],
        operation="verify Bitable records",
    )["items"]
    by_key: dict[str, dict[str, Any]] = {}
    for item in current:
        if not isinstance(item, dict):
            continue
        fields = item.get("fields") or {}
        if fields.get(key_field) in (None, "", []):
            continue
        key = _record_key(fields[key_field])
        if key in by_key:
            raise RuntimeError(f"verification found a duplicate key: {fields[key_field]!r}")
        by_key[key] = fields
    for item in records:
        expected = item["fields"]
        key = _record_key(expected[key_field])
        actual = by_key.get(key)
        if actual is None:
            raise RuntimeError(f"record verification is missing key: {expected[key_field]!r}")
        mismatched = [name for name, value in expected.items() if actual.get(name) != value]
        if mismatched:
            raise RuntimeError(
                f"record verification mismatch for {expected[key_field]!r}: {mismatched}"
            )
    return len(records)


def sync_bitable(
    client: Any,
    app_token: str,
    table_id: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Apply one bounded, idempotent Bitable configuration."""
    if not app_token or not table_id:
        raise RuntimeError("app_token and table_id are required")
    fields, records, views, key_field, delete_blank, max_items = _validate_setup(config)
    fields_by_name, created_fields, renamed_primary = _ensure_fields(
        client, app_token, table_id, fields, max_items
    )
    if key_field and key_field not in fields_by_name:
        raise RuntimeError(f"key_field {key_field} does not exist")
    record_result = _upsert_records(
        client,
        app_token,
        table_id,
        records,
        key_field,
        delete_blank,
        max_items,
    )
    view_result, created_views, manual_actions = _ensure_views(
        client,
        app_token,
        table_id,
        views,
        fields_by_name,
        records,
        max_items,
    )
    verified_records = _verify_records(
        client, app_token, table_id, records, key_field, max_items
    )
    return {
        "success": True,
        "status": "needs_manual_configuration" if manual_actions else "complete",
        "app_token": app_token,
        "table_id": table_id,
        "created_fields": created_fields,
        "renamed_primary_fields": renamed_primary,
        "created_records": record_result["created"],
        "updated_records": record_result["updated"],
        "unchanged_records": record_result["unchanged"],
        "deleted_blank_records": record_result["deleted_blank"],
        "verified_records": verified_records,
        "created_views": created_views,
        "views": view_result,
        "manual_actions": manual_actions,
    }


def handle_bitable_sync(args: dict, **_: Any) -> str:
    app_token = str(args.get("app_token") or "").strip()
    table_id = str(args.get("table_id") or "").strip()
    client, error = _client_or_error()
    if error:
        return error
    try:
        return tool_result(**sync_bitable(client, app_token, table_id, args))
    except RuntimeError as exc:
        return tool_error(str(exc))


def resolve_document_bitables(
    client: Any,
    document_id: str,
    *,
    block_id: str = "",
    app_token: str = "",
    table_id: str = "",
    require_unique: bool = True,
) -> dict[str, Any]:
    if not document_id:
        raise RuntimeError("document_id is required")
    data = list_all_pages(
        client,
        _LIST_BLOCKS_URI,
        paths={"document_id": document_id},
        page_size=500,
        max_items=10000,
        base_queries=[("document_revision_id", "-1")],
        operation="list document blocks for embedded Bitables",
    )
    matches: list[dict[str, Any]] = []
    for item in data["items"]:
        if not isinstance(item, dict) or not isinstance(item.get("bitable"), dict):
            continue
        raw_token = str(item["bitable"].get("token") or "").strip()
        resolved_app, resolved_table = split_embedded_bitable_token(raw_token)
        candidate = {
            "block_id": str(item.get("block_id") or ""),
            "parent_id": str(item.get("parent_id") or ""),
            "raw_token": raw_token,
            "app_token": resolved_app,
            "table_id": resolved_table,
            "embedded_view_type": item["bitable"].get("view_type"),
        }
        if block_id and candidate["block_id"] != block_id:
            continue
        if app_token and candidate["app_token"] != app_token:
            continue
        if table_id and candidate["table_id"] != table_id:
            continue
        matches.append(candidate)
    if require_unique and len(matches) != 1:
        raise RuntimeError(f"expected exactly one matching embedded Bitable, found {len(matches)}")
    return {
        "success": True,
        "document_id": document_id,
        "count": len(matches),
        "matches": matches,
        "resolved": matches[0] if len(matches) == 1 else None,
        "page_count": data["page_count"],
    }


def handle_doc_resolve_bitable(args: dict, **_: Any) -> str:
    require_unique = args.get("require_unique", True)
    if not isinstance(require_unique, bool):
        return tool_error("require_unique must be a boolean")
    client, error = _client_or_error()
    if error:
        return error
    try:
        return tool_result(
            **resolve_document_bitables(
                client,
                str(args.get("document_id") or "").strip(),
                block_id=str(args.get("block_id") or "").strip(),
                app_token=str(args.get("app_token") or "").strip(),
                table_id=str(args.get("table_id") or "").strip(),
                require_unique=require_unique,
            )
        )
    except RuntimeError as exc:
        return tool_error(str(exc))


__all__ = [
    "BITABLE_SETUP_SCHEMA",
    "FEISHU_BITABLE_SYNC_SCHEMA",
    "FEISHU_DOC_RESOLVE_BITABLE_SCHEMA",
    "handle_bitable_sync",
    "handle_doc_resolve_bitable",
    "list_all_pages",
    "resolve_document_bitables",
    "split_embedded_bitable_token",
    "sync_bitable",
]
