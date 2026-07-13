"""Structured Block and media-object CRUD for Feishu docx documents."""

from __future__ import annotations

import asyncio
import io
import json
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from hermes_constants import get_hermes_home
from tools.registry import tool_error, tool_result

from .document_tools import (
    _CREATE_CHILDREN_URI,
    _RICH_TEXT_ELEMENTS_SCHEMA,
    _STYLE_SCHEMA_PROPERTIES,
    _TEXT_BLOCK_STYLE_SCHEMA_PROPERTIES,
    _TEXT_BLOCK_TYPES,
    _UPDATE_BLOCK_URI,
    _build_text_elements,
    _check_feishu,
    _client_or_error,
    _parse_response_data,
    _request,
    _text_block_style,
)


_MAX_MEDIA_BYTES = 20 * 1024 * 1024
_BATCH_UPDATE_BLOCKS_URI = (
    "/open-apis/docx/v1/documents/:document_id/blocks/batch_update"
)
_BATCH_DELETE_CHILDREN_URI = (
    "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children/batch_delete"
)
_GET_BLOCK_URI = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"

_HEADING_BLOCK_TYPES = {f"heading{level}": level + 2 for level in range(1, 10)}
_OBJECT_BLOCK_TYPES = {
    "bitable": 18,
    "callout": 19,
    "chat_card": 20,
    "divider": 22,
    "grid": 24,
    "iframe": 26,
    "isv": 28,
    "sheet": 30,
    "table": 31,
    "quote_container": 34,
    "add_ons": 40,
    "wiki_catalog": 42,
    "board": 43,
    "link_preview": 48,
    "sub_page_list": 51,
}
_SUPPORTED_BLOCK_TYPES = {
    **_TEXT_BLOCK_TYPES,
    **_HEADING_BLOCK_TYPES,
    **_OBJECT_BLOCK_TYPES,
}


_BLOCK_SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(_SUPPORTED_BLOCK_TYPES),
            "description": "Feishu Block type.",
        },
        "content": {
            "type": "string",
            "description": "Single-style text for text-like blocks.",
        },
        "elements": _RICH_TEXT_ELEMENTS_SCHEMA,
        **_STYLE_SCHEMA_PROPERTIES,
        **_TEXT_BLOCK_STYLE_SCHEMA_PROPERTIES,
        "data": {
            "type": "object",
            "description": (
                "Object data. Examples: table={property:{row_size,column_size}}, "
                "sheet={row_size,column_size}, bitable={view_type}, "
                "grid={column_size}, iframe={component:{type,url}}, "
                "chat_card={chat_id,align}, callout={background_color,"
                "border_color,text_color,emoji_id}, board={align,width,height}, "
                "wiki_catalog/sub_page_list={wiki_token}, "
                "link_preview={url,url_type}."
            ),
        },
    },
    "required": ["type"],
}


FEISHU_DOC_INSERT_BLOCKS_SCHEMA = {
    "name": "feishu_doc_insert_blocks",
    "description": (
        "Insert 1-50 mixed structured blocks in one Feishu docx API call. "
        "Supports text, headings, equations and mentions inside text, lists, "
        "code, quote, todo, divider, table, sheet, bitable, callout, grid, "
        "iframe, chat card, quote container, ISV, add-ons, Wiki catalogs, "
        "message-link previews, and board blocks."
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
            "blocks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 50,
                "items": _BLOCK_SPEC_SCHEMA,
            },
        },
        "required": ["document_id", "blocks"],
    },
}


FEISHU_DOC_INSERT_MEDIA_SCHEMA = {
    "name": "feishu_doc_insert_media",
    "description": (
        "Insert an image or file attachment into a Feishu docx document. "
        "The tool creates the Block, uploads the media, and binds its token."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "kind": {
                "type": "string",
                "enum": ["image", "file"],
                "default": "image",
            },
            "source": {
                "type": "string",
                "description": (
                    "Image path, safe HTTP(S) URL, or base64 data URL. File "
                    "attachments currently use a local workspace/cache path."
                ),
            },
            "file_name": {
                "type": "string",
                "description": "Optional display filename, maximum 250 characters.",
            },
            "image_align": {
                "type": "integer",
                "enum": [1, 2, 3],
                "description": "Image alignment: 1 left, 2 center, 3 right.",
            },
            "caption": {
                "type": "string",
                "description": "Optional plain-text image caption.",
            },
            "file_view_type": {
                "type": "integer",
                "enum": [1, 2],
                "description": "Attachment view: 1 card, 2 preview.",
            },
            "parent_block_id": {
                "type": "string",
                "description": "Defaults to the document root block.",
            },
            "index": {
                "type": "integer",
                "default": -1,
                "description": "Insertion index; -1 appends.",
            },
        },
        "required": ["document_id", "source"],
    },
}


_UPDATE_OPERATIONS = [
    "update_text_elements",
    "update_text_style",
    "update_text",
    "update_table_property",
    "insert_table_row",
    "insert_table_column",
    "delete_table_rows",
    "delete_table_columns",
    "merge_table_cells",
    "unmerge_table_cells",
    "insert_grid_column",
    "delete_grid_column",
    "update_grid_column_width_ratio",
    "replace_image",
    "replace_file",
    "update_task",
]

FEISHU_DOC_UPDATE_BLOCKS_SCHEMA = {
    "name": "feishu_doc_update_blocks",
    "description": (
        "Update 1-200 distinct Feishu docx blocks in one API call. Supports "
        "rich text/formulas and block style, table properties/rows/columns/cell "
        "merging, grid columns, tasks, and image/file replacement. For media, "
        "supply data.token or source; source is uploaded automatically. If "
        "both are omitted, the existing media token is fetched and reused."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "updates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 200,
                "items": {
                    "type": "object",
                    "properties": {
                        "block_id": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": _UPDATE_OPERATIONS,
                        },
                        "data": {
                            "type": "object",
                            "description": (
                                "Operation payload. Text operations accept content/elements; "
                                "style operations use style plus fields 1-7. Table/grid "
                                "operations use the official row/column indexes. Media "
                                "replacement accepts token and image width/height/align/caption."
                            ),
                        },
                        "source": {
                            "type": "string",
                            "description": (
                                "For replace_image/replace_file, upload this source and use "
                                "its token. Images accept safe URL/data/local sources; files "
                                "accept workspace/cache paths."
                            ),
                        },
                        "file_name": {
                            "type": "string",
                            "description": "Optional uploaded media filename (1-250 chars).",
                        },
                    },
                    "required": ["block_id", "operation", "data"],
                },
            },
        },
        "required": ["document_id", "updates"],
    },
}

FEISHU_DOC_DELETE_BLOCKS_SCHEMA = {
    "name": "feishu_doc_delete_blocks",
    "description": (
        "Delete a contiguous range of child blocks from a Feishu docx parent. "
        "Indexes are zero-based and [start_index, end_index) is left-closed, "
        "right-open. Delete table rows/columns or grid columns with "
        "feishu_doc_update_blocks instead."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "document_id": {"type": "string"},
            "parent_block_id": {
                "type": "string",
                "description": "Defaults to the document root block.",
            },
            "start_index": {"type": "integer", "minimum": 0},
            "end_index": {
                "type": "integer",
                "minimum": 1,
                "description": "Exclusive end index; must exceed start_index.",
            },
        },
        "required": ["document_id", "start_index", "end_index"],
    },
}


def _validate_index(value: Any) -> tuple[int | None, str | None]:
    if isinstance(value, bool):
        return None, "index must be an integer"
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None, "index must be an integer"
    if index < -1:
        return None, "index must be -1 or greater"
    return index, None


def _validate_color(data: dict[str, Any], field: str, maximum: int) -> str | None:
    if field not in data:
        return None
    value = data[field]
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        return f"{field} must be between 1 and {maximum}"
    return None


def _object_block_data(
    block_type: str, raw_data: Any
) -> tuple[dict[str, Any] | None, str | None]:
    if raw_data is None:
        data: dict[str, Any] = {}
    elif isinstance(raw_data, dict):
        data = dict(raw_data)
    else:
        return None, "data must be an object"

    if block_type in {"divider", "quote_container"}:
        return {}, None
    if block_type == "bitable":
        view_type = data.get("view_type", 1)
        if isinstance(view_type, bool) or view_type not in {1, 2}:
            return None, "bitable.view_type must be 1 or 2"
        data["view_type"] = view_type
        data.pop("token", None)
    elif block_type == "callout":
        for field, maximum in (
            ("background_color", 15),
            ("border_color", 7),
            ("text_color", 7),
        ):
            error = _validate_color(data, field, maximum)
            if error:
                return None, f"callout.{error}"
    elif block_type == "chat_card":
        if not str(data.get("chat_id") or "").strip():
            return None, "chat_card.chat_id is required"
        if "align" in data and (
            isinstance(data["align"], bool) or data["align"] not in {1, 2, 3}
        ):
            return None, "chat_card.align must be 1, 2, or 3"
    elif block_type == "grid":
        if isinstance(data.get("column_size"), bool) or data.get(
            "column_size"
        ) not in {2, 3, 4, 5}:
            return None, "grid.column_size must be between 2 and 5"
    elif block_type == "iframe":
        component = data.get("component")
        if not isinstance(component, dict):
            return None, "iframe.component is required"
        component_type = component.get("type")
        url = str(component.get("url") or "").strip()
        if (
            isinstance(component_type, bool)
            or component_type not in {*range(1, 16), 99}
            or not url
        ):
            return None, "iframe.component requires type 1-15 or 99 and url"
        data["component"] = {**component, "url": quote(url, safe="")}
    elif block_type == "sheet":
        data.pop("token", None)
        for field in ("row_size", "column_size"):
            if field not in data:
                continue
            value = data.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 9:
                return None, f"sheet.{field} must be between 1 and 9"
    elif block_type == "table":
        prop = data.get("property")
        if not isinstance(prop, dict):
            return None, "table.property is required"
        for field in ("row_size", "column_size"):
            value = prop.get(field)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 9
            ):
                return None, f"table.property.{field} must be between 1 and 9"
    elif block_type == "board":
        if "align" in data and (
            isinstance(data["align"], bool) or data["align"] not in {1, 2, 3}
        ):
            return None, "board.align must be 1, 2, or 3"
        for field in ("width", "height"):
            if field not in data:
                continue
            value = data[field]
            if isinstance(value, bool) or not isinstance(value, int) or value < 90:
                return None, f"board.{field} must be at least 90"
        data.pop("token", None)
    elif block_type == "wiki_catalog":
        if "wiki_token" in data and not str(data["wiki_token"] or "").strip():
            return None, "wiki_catalog.wiki_token must be non-empty"
        if "wiki_token" in data:
            data["wiki_token"] = str(data["wiki_token"]).strip()
    elif block_type == "sub_page_list":
        wiki_token = str(data.get("wiki_token") or "").strip()
        if len(wiki_token) != 27:
            return None, "sub_page_list.wiki_token must contain 27 characters"
        data["wiki_token"] = wiki_token
    elif block_type == "link_preview":
        url = str(data.get("url") or "").strip()
        if not url or data.get("url_type") != "MessageLink":
            return None, "link_preview requires url and url_type=MessageLink"
        data["url"] = url
    elif block_type == "add_ons":
        if not str(data.get("component_type_id") or "").strip():
            return None, "add_ons.component_type_id is required"
    elif block_type == "isv" and not data:
        return None, f"{block_type}.data is required"
    return data, None


def _build_block(spec: Any, index: int) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(spec, dict):
        return None, f"blocks[{index}] must be an object"
    block_type = str(spec.get("type") or "").strip()
    if block_type not in _SUPPORTED_BLOCK_TYPES:
        return None, f"blocks[{index}].type is unsupported"

    if block_type in _TEXT_BLOCK_TYPES or block_type in _HEADING_BLOCK_TYPES:
        elements, error = _build_text_elements(spec)
        if error:
            return None, f"blocks[{index}].{error}"
        style_type = block_type if block_type in _TEXT_BLOCK_TYPES else "text"
        style, error = _text_block_style(spec, style_type)
        if error:
            return None, f"blocks[{index}].{error}"
        block_data: dict[str, Any] = {"elements": elements}
        if style:
            block_data["style"] = style
        return {
            "block_type": _SUPPORTED_BLOCK_TYPES[block_type],
            block_type: block_data,
        }, None

    data, error = _object_block_data(block_type, spec.get("data"))
    if error:
        return None, f"blocks[{index}].{error}"
    return {
        "block_type": _OBJECT_BLOCK_TYPES[block_type],
        block_type: data,
    }, None


def handle_doc_insert_blocks(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    raw_blocks = args.get("blocks")
    if not document_id or not parent_block_id:
        return tool_error("document_id is required")
    if not isinstance(raw_blocks, list) or not 1 <= len(raw_blocks) <= 50:
        return tool_error("blocks must contain 1-50 items")
    index, error = _validate_index(args.get("index", -1))
    if error:
        return tool_error(error)

    blocks: list[dict[str, Any]] = []
    for position, spec in enumerate(raw_blocks):
        block, error = _build_block(spec, position)
        if error:
            return tool_error(error)
        blocks.append(block)

    client, error = _client_or_error()
    if error:
        return error
    code, msg, data = _request(
        client,
        "POST",
        _CREATE_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": parent_block_id},
        queries=[("document_revision_id", "-1")],
        body={"index": index, "children": blocks},
    )
    if code != 0:
        return tool_error(f"Insert document blocks failed: code={code} msg={msg}")
    return tool_result(success=True, **data)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_local_attachment(source: str, cwd: str) -> tuple[bytes, str, str]:
    candidate = source[len("file://") :] if source.lower().startswith("file://") else source
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = Path(cwd or ".") / path
    path = path.resolve()
    allowed_roots = [Path(cwd or ".").resolve(), (get_hermes_home() / "cache").resolve()]
    if not any(_path_is_within(path, root) for root in allowed_roots):
        raise ValueError("file source must be inside the workspace or Hermes media cache")
    from agent.file_safety import raise_if_read_blocked

    raise_if_read_blocked(str(path))
    if not path.is_file():
        raise ValueError(f"file source not found: {path}")
    size = path.stat().st_size
    if size < 1 or size > _MAX_MEDIA_BYTES:
        raise ValueError("file source must contain 1 byte to 20 MB")
    return path.read_bytes(), mimetypes.guess_type(path.name)[0] or "", path.name


async def _resolve_media_source(
    kind: str, source: str, *, task_id: str, cwd: str
) -> tuple[bytes, str, str]:
    if kind == "file":
        return await asyncio.to_thread(_read_local_attachment, source, cwd)

    from tools.image_source import ResolveContext, resolve_image_source

    normalized_source = source
    if not source.startswith(("data:", "http://", "https://", "file://")):
        path = Path(source).expanduser()
        if not path.is_absolute():
            normalized_source = str(Path(cwd or ".") / path)
    resolved = await resolve_image_source(
        normalized_source, ResolveContext(task_id=task_id or None)
    )
    if not resolved.data or len(resolved.data) > _MAX_MEDIA_BYTES:
        raise ValueError("image source must contain 1 byte to 20 MB")
    source_name = ""
    if resolved.origin == "http":
        parsed = urlsplit(source)
        source_name = Path(parsed.path).name if parsed.path else ""
    elif resolved.origin in {"file", "local", "container"}:
        candidate = source[len("file://") :] if source.startswith("file://") else source
        source_name = Path(candidate).name
    extension = mimetypes.guess_extension(resolved.mime) or ".png"
    if source_name and not Path(source_name).suffix:
        source_name = f"{source_name}{extension}"
    return resolved.data, resolved.mime, source_name or f"image{extension}"


def _upload_media(
    client: Any,
    *,
    data: bytes,
    file_name: str,
    parent_type: str,
    block_id: str,
    document_id: str,
) -> tuple[Any, str, dict[str, Any]]:
    from lark_oapi.api.drive.v1.model.upload_all_media_request import (
        UploadAllMediaRequest,
    )
    from lark_oapi.api.drive.v1.model.upload_all_media_request_body import (
        UploadAllMediaRequestBody,
    )

    body = (
        UploadAllMediaRequestBody.builder()
        .file_name(file_name)
        .parent_type(parent_type)
        .parent_node(block_id)
        .size(len(data))
        .extra(json.dumps({"drive_route_token": document_id}))
        .file(io.BytesIO(data))
        .build()
    )
    request = UploadAllMediaRequest.builder().request_body(body).build()
    response = client.drive.v1.media.upload_all(request)
    return (
        getattr(response, "code", None),
        str(getattr(response, "msg", "") or ""),
        _parse_response_data(response),
    )


def _created_media_block_id(kind: str, data: dict[str, Any]) -> str:
    children = data.get("children")
    if not isinstance(children, list) or not children or not isinstance(children[0], dict):
        return ""
    created = children[0]
    if kind == "image":
        return str(created.get("block_id") or "")
    file_children = created.get("children")
    if isinstance(file_children, list) and file_children:
        return str(file_children[0] or "")
    return str(created.get("block_id") or "")


async def handle_doc_insert_media(args: dict, **kwargs: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    kind = str(args.get("kind") or "image").strip()
    source = str(args.get("source") or "").strip()
    if not document_id or not parent_block_id or not source:
        return tool_error("document_id and source are required")
    if kind not in {"image", "file"}:
        return tool_error("kind must be image or file")
    index, error = _validate_index(args.get("index", -1))
    if error:
        return tool_error(error)

    try:
        media, _mime, inferred_name = await _resolve_media_source(
            kind,
            source,
            task_id=str(kwargs.get("task_id") or ""),
            cwd=str(kwargs.get("cwd") or ""),
        )
    except Exception as exc:
        return tool_error(f"Unable to read media source: {exc}")
    file_name = str(args.get("file_name") or inferred_name).strip()
    if not file_name or len(file_name) > 250 or Path(file_name).name != file_name:
        return tool_error("file_name must be a basename of 1-250 characters")

    client, error = _client_or_error()
    if error:
        return error
    if kind == "image":
        image: dict[str, Any] = {}
        if "image_align" in args:
            if isinstance(args["image_align"], bool) or args["image_align"] not in {
                1,
                2,
                3,
            }:
                return tool_error("image_align must be 1, 2, or 3")
            image["align"] = args["image_align"]
        if "caption" in args:
            caption = args["caption"]
            if not isinstance(caption, str) or not caption.strip():
                return tool_error("caption must be non-empty text")
            image["caption"] = {"content": caption}
        create_block = {"block_type": 27, "image": image}
    else:
        view_type = args.get("file_view_type", 1)
        if isinstance(view_type, bool) or view_type not in {1, 2}:
            return tool_error("file_view_type must be 1 or 2")
        create_block = {
            "block_type": 23,
            "file": {"token": "", "view_type": view_type},
        }
    code, msg, create_data = await asyncio.to_thread(
        _request,
        client,
        "POST",
        _CREATE_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": parent_block_id},
        queries=[("document_revision_id", "-1")],
        body={"index": index, "children": [create_block]},
    )
    if code != 0:
        return tool_error(f"Create {kind} block failed: code={code} msg={msg}")
    block_id = _created_media_block_id(kind, create_data)
    if not block_id:
        return tool_error(f"Create {kind} block returned no block_id")

    code, msg, upload_data = await asyncio.to_thread(
        _upload_media,
        client,
        data=media,
        file_name=file_name,
        parent_type=f"docx_{kind}",
        block_id=block_id,
        document_id=document_id,
    )
    file_token = str(upload_data.get("file_token") or "")
    if code != 0 or not file_token:
        return tool_error(
            f"Upload {kind} failed: code={code} msg={msg}; block_id={block_id}"
        )

    operation = "replace_image" if kind == "image" else "replace_file"
    code, msg, replace_data = await asyncio.to_thread(
        _request,
        client,
        "PATCH",
        _UPDATE_BLOCK_URI,
        paths={"document_id": document_id, "block_id": block_id},
        queries=[("document_revision_id", "-1")],
        body={operation: {"token": file_token}},
    )
    if code != 0:
        return tool_error(
            f"Bind {kind} failed: code={code} msg={msg}; "
            f"block_id={block_id} file_token={file_token}"
        )
    return tool_result(
        success=True,
        kind=kind,
        block_id=block_id,
        file_token=file_token,
        file_name=file_name,
        **replace_data,
    )


_TEXT_STYLE_FIELDS = {
    "align": 1,
    "done": 2,
    "folded": 3,
    "language": 4,
    "wrap": 5,
    "background_color": 6,
    "indentation_level": 7,
}
_BLOCK_BACKGROUND_COLORS = {
    "LightGrayBackground",
    "LightRedBackground",
    "LightOrangeBackground",
    "LightYellowBackground",
    "LightGreenBackground",
    "LightBlueBackground",
    "LightPurpleBackground",
    "PaleGrayBackground",
    "DarkGrayBackground",
    "DarkRedBackground",
    "DarkOrangeBackground",
    "DarkYellowBackground",
    "DarkGreenBackground",
    "DarkBlueBackground",
    "DarkPurpleBackground",
}


def _strict_integer(value: Any, field: str, minimum: int) -> tuple[int | None, str | None]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None, f"{field} must be an integer"
    if value < minimum:
        return None, f"{field} must be at least {minimum}"
    return value, None


def _unexpected_fields(data: dict[str, Any], allowed: set[str]) -> str | None:
    unexpected = sorted(set(data) - allowed)
    if unexpected:
        return f"unsupported data fields: {', '.join(unexpected)}"
    return None


def _build_text_style_update(
    data: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    style = data.get("style")
    if not isinstance(style, dict) or not style:
        return None, "data.style must be a non-empty object"
    unexpected = _unexpected_fields(style, set(_TEXT_STYLE_FIELDS))
    if unexpected:
        return None, f"data.style {unexpected}"

    if "align" in style and (
        isinstance(style["align"], bool) or style["align"] not in {1, 2, 3}
    ):
        return None, "data.style.align must be 1, 2, or 3"
    for field in ("done", "folded", "wrap"):
        if field in style and not isinstance(style[field], bool):
            return None, f"data.style.{field} must be a boolean"
    if "language" in style and (
        isinstance(style["language"], bool)
        or not isinstance(style["language"], int)
        or not 1 <= style["language"] <= 75
    ):
        return None, "data.style.language must be between 1 and 75"
    if "background_color" in style and style["background_color"] not in (
        _BLOCK_BACKGROUND_COLORS
    ):
        return None, "data.style.background_color is unsupported"
    if "indentation_level" in style and style["indentation_level"] not in {
        "NoIndent",
        "OneLevelIndent",
    }:
        return None, "data.style.indentation_level is unsupported"

    raw_fields = data.get("fields")
    if raw_fields is None:
        fields = [_TEXT_STYLE_FIELDS[field] for field in style]
    elif not isinstance(raw_fields, list) or not raw_fields:
        return None, "data.fields must be a non-empty array"
    else:
        fields = raw_fields
    if any(
        isinstance(field, bool) or not isinstance(field, int) or field not in range(1, 8)
        for field in fields
    ):
        return None, "data.fields may contain only integers 1-7"
    if len(fields) != len(set(fields)):
        return None, "data.fields must not contain duplicates"
    expected = {_TEXT_STYLE_FIELDS[field] for field in style}
    if not expected.issubset(set(fields)):
        return None, "data.fields must include every supplied style field"
    return {"style": dict(style), "fields": fields}, None


def _build_range_operation(
    data: dict[str, Any],
    start_field: str,
    end_field: str,
) -> tuple[dict[str, Any] | None, str | None]:
    unexpected = _unexpected_fields(data, {start_field, end_field})
    if unexpected:
        return None, unexpected
    start, error = _strict_integer(data.get(start_field), f"data.{start_field}", 0)
    if error:
        return None, error
    end, error = _strict_integer(data.get(end_field), f"data.{end_field}", 1)
    if error:
        return None, error
    if start >= end:
        return None, f"data.{start_field} must be less than data.{end_field}"
    return {start_field: start, end_field: end}, None


def _build_update_request(
    spec: Any, index: int
) -> tuple[dict[str, Any] | None, str | None]:
    prefix = f"updates[{index}]"
    if not isinstance(spec, dict):
        return None, f"{prefix} must be an object"
    block_id = str(spec.get("block_id") or "").strip()
    operation = str(spec.get("operation") or "").strip()
    data = spec.get("data")
    if not block_id:
        return None, f"{prefix}.block_id is required"
    if operation not in _UPDATE_OPERATIONS:
        return None, f"{prefix}.operation is unsupported"
    if not isinstance(data, dict):
        return None, f"{prefix}.data must be an object"

    payload: dict[str, Any] | None
    error: str | None = None
    if operation == "update_text_elements":
        allowed = {"content", "elements", *_STYLE_SCHEMA_PROPERTIES}
        error = _unexpected_fields(data, allowed)
        if error is None:
            elements, error = _build_text_elements(
                data, allow_existing_inline=True
            )
            payload = {"elements": elements} if error is None else None
        else:
            payload = None
    elif operation in {"update_text_style", "update_text"}:
        allowed = {"style", "fields"}
        if operation == "update_text":
            allowed.update({"content", "elements", *_STYLE_SCHEMA_PROPERTIES})
        error = _unexpected_fields(data, allowed)
        payload = None
        if error is None:
            style_payload, error = _build_text_style_update(data)
            if error is None and operation == "update_text":
                elements, error = _build_text_elements(
                    data, allow_existing_inline=True
                )
                if error is None:
                    payload = {"elements": elements, **style_payload}
            elif error is None:
                payload = style_payload
    elif operation == "update_table_property":
        allowed = {"column_width", "column_index", "header_row", "header_column"}
        error = _unexpected_fields(data, allowed)
        payload = dict(data)
        if error is None and not payload:
            error = "data must contain a table property"
        if error is None and "column_width" in payload:
            _, error = _strict_integer(payload["column_width"], "data.column_width", 50)
        if error is None and "column_index" in payload:
            _, error = _strict_integer(payload["column_index"], "data.column_index", 0)
        if error is None:
            for field in ("header_row", "header_column"):
                if field in payload and not isinstance(payload[field], bool):
                    error = f"data.{field} must be a boolean"
                    break
        if error is None and ("column_width" in payload) != ("column_index" in payload):
            error = "data.column_width and data.column_index must be supplied together"
    elif operation in {"insert_table_row", "insert_table_column"}:
        field = "row_index" if operation.endswith("row") else "column_index"
        error = _unexpected_fields(data, {field})
        value = None
        if error is None:
            value, error = _strict_integer(data.get(field), f"data.{field}", -1)
        payload = {field: value} if error is None else None
    elif operation in {"delete_table_rows", "delete_table_columns"}:
        stem = "row" if operation.endswith("rows") else "column"
        payload, error = _build_range_operation(
            data, f"{stem}_start_index", f"{stem}_end_index"
        )
    elif operation == "merge_table_cells":
        allowed = {
            "row_start_index",
            "row_end_index",
            "column_start_index",
            "column_end_index",
        }
        error = _unexpected_fields(data, allowed)
        payload = None
        if error is None:
            rows, error = _build_range_operation(
                {key: data[key] for key in ("row_start_index", "row_end_index")},
                "row_start_index",
                "row_end_index",
            )
            if error is None:
                columns, error = _build_range_operation(
                    {key: data[key] for key in ("column_start_index", "column_end_index")},
                    "column_start_index",
                    "column_end_index",
                )
                if error is None:
                    payload = {**rows, **columns}
    elif operation == "unmerge_table_cells":
        error = _unexpected_fields(data, {"row_index", "column_index"})
        payload = None
        if error is None:
            row, error = _strict_integer(data.get("row_index"), "data.row_index", 0)
            if error is None:
                column, error = _strict_integer(
                    data.get("column_index"), "data.column_index", 0
                )
                if error is None:
                    payload = {"row_index": row, "column_index": column}
    elif operation in {"insert_grid_column", "delete_grid_column"}:
        error = _unexpected_fields(data, {"column_index"})
        payload = None
        if error is None:
            column, error = _strict_integer(
                data.get("column_index"), "data.column_index", -1
            )
            if error is None and operation == "insert_grid_column" and column == 0:
                error = "data.column_index cannot be 0 when inserting a grid column"
            if error is None:
                payload = {"column_index": column}
    elif operation == "update_grid_column_width_ratio":
        error = _unexpected_fields(data, {"width_ratios"})
        ratios = data.get("width_ratios")
        payload = None
        if error is None and (
            not isinstance(ratios, list)
            or not ratios
            or any(
                isinstance(ratio, bool)
                or not isinstance(ratio, int)
                or not 1 <= ratio <= 99
                for ratio in ratios
            )
        ):
            error = "data.width_ratios must be a non-empty array of integers 1-99"
        if error is None:
            payload = {"width_ratios": ratios}
    elif operation == "replace_image":
        allowed = {"token", "width", "height", "align", "caption"}
        error = _unexpected_fields(data, allowed)
        payload = dict(data)
        for field in ("width", "height"):
            if error is None and field in payload:
                _, error = _strict_integer(payload[field], f"data.{field}", 1)
        if error is None and "align" in payload and (
            isinstance(payload["align"], bool) or payload["align"] not in {1, 2, 3}
        ):
            error = "data.align must be 1, 2, or 3"
        if error is None and "caption" in payload:
            caption = payload["caption"]
            if isinstance(caption, str) and caption.strip():
                payload["caption"] = {"content": caption}
            elif not (
                isinstance(caption, dict)
                and isinstance(caption.get("content"), str)
                and caption["content"].strip()
            ):
                error = "data.caption must be non-empty text or {content: text}"
        if "token" in payload:
            payload["token"] = str(payload["token"]).strip()
    elif operation == "replace_file":
        error = _unexpected_fields(data, {"token"})
        payload = dict(data)
        if "token" in payload:
            payload["token"] = str(payload["token"]).strip()
    else:  # update_task
        error = _unexpected_fields(data, {"task_id", "folded"})
        payload = dict(data)
        if error is None and not payload:
            error = "data must contain task_id or folded"
        if error is None and "task_id" in payload and not str(
            payload["task_id"] or ""
        ).strip():
            error = "data.task_id must be non-empty"
        if error is None and "folded" in payload and not isinstance(
            payload["folded"], bool
        ):
            error = "data.folded must be a boolean"

    if error:
        return None, f"{prefix}.{error}"
    return {"block_id": block_id, operation: payload}, None


async def handle_doc_update_blocks(args: dict, **kwargs: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    raw_updates = args.get("updates")
    if not document_id:
        return tool_error("document_id is required")
    if not isinstance(raw_updates, list) or not 1 <= len(raw_updates) <= 200:
        return tool_error("updates must contain 1-200 items")

    requests: list[dict[str, Any]] = []
    seen_block_ids: set[str] = set()
    for index, spec in enumerate(raw_updates):
        request, error = _build_update_request(spec, index)
        if error:
            return tool_error(error)
        block_id = request["block_id"]
        if block_id in seen_block_ids:
            return tool_error(
                f"updates[{index}].block_id duplicates {block_id}; Feishu allows "
                "only one update per block in a batch"
            )
        seen_block_ids.add(block_id)
        requests.append(request)

    client, error = _client_or_error()
    if error:
        return error
    uploaded_media: list[dict[str, str]] = []
    for index, (spec, request) in enumerate(zip(raw_updates, requests)):
        operation = str(spec["operation"])
        source = str(spec.get("source") or "").strip()
        if operation in {"replace_image", "replace_file"} and not source and not str(
            request[operation].get("token") or ""
        ).strip():
            kind = "image" if operation == "replace_image" else "file"
            code, msg, block_data = await asyncio.to_thread(
                _request,
                client,
                "GET",
                _GET_BLOCK_URI,
                paths={
                    "document_id": document_id,
                    "block_id": request["block_id"],
                },
                queries=[("document_revision_id", "-1")],
            )
            block = block_data.get("block", block_data)
            media_data = block.get(kind) if isinstance(block, dict) else None
            token = (
                str(media_data.get("token") or "")
                if isinstance(media_data, dict)
                else ""
            )
            if code != 0 or not token:
                return tool_error(
                    f"Read updates[{index}] existing {kind} token failed: "
                    f"code={code} msg={msg}"
                )
            request[operation]["token"] = token
        if not source:
            continue
        if operation not in {"replace_image", "replace_file"}:
            return tool_error(f"updates[{index}].source is only valid for media replacement")
        kind = "image" if operation == "replace_image" else "file"
        try:
            media, _mime, inferred_name = await _resolve_media_source(
                kind,
                source,
                task_id=str(kwargs.get("task_id") or ""),
                cwd=str(kwargs.get("cwd") or ""),
            )
        except Exception as exc:
            return tool_error(f"Unable to read updates[{index}] media source: {exc}")
        file_name = str(spec.get("file_name") or inferred_name).strip()
        if not file_name or len(file_name) > 250 or Path(file_name).name != file_name:
            return tool_error(
                f"updates[{index}].file_name must be a basename of 1-250 characters"
            )
        code, msg, upload_data = await asyncio.to_thread(
            _upload_media,
            client,
            data=media,
            file_name=file_name,
            parent_type=f"docx_{kind}",
            block_id=request["block_id"],
            document_id=document_id,
        )
        token = str(upload_data.get("file_token") or "")
        if code != 0 or not token:
            return tool_error(
                f"Upload updates[{index}] {kind} failed: code={code} msg={msg}"
            )
        request[operation]["token"] = token
        uploaded_media.append(
            {
                "block_id": request["block_id"],
                "kind": kind,
                "file_name": file_name,
                "file_token": token,
            }
        )

    code, msg, data = await asyncio.to_thread(
        _request,
        client,
        "PATCH",
        _BATCH_UPDATE_BLOCKS_URI,
        paths={"document_id": document_id},
        queries=[("document_revision_id", "-1")],
        body={"requests": requests},
    )
    if code != 0:
        return tool_error(f"Update document blocks failed: code={code} msg={msg}")
    return tool_result(
        success=True,
        updated_block_count=len(requests),
        uploaded_media=uploaded_media,
        **data,
    )


def handle_doc_delete_blocks(args: dict, **_: Any) -> str:
    document_id = str(args.get("document_id") or "").strip()
    parent_block_id = str(args.get("parent_block_id") or document_id).strip()
    if not document_id or not parent_block_id:
        return tool_error("document_id is required")
    start, error = _strict_integer(args.get("start_index"), "start_index", 0)
    if error:
        return tool_error(error)
    end, error = _strict_integer(args.get("end_index"), "end_index", 1)
    if error:
        return tool_error(error)
    if start >= end:
        return tool_error("start_index must be less than end_index")

    client, error = _client_or_error()
    if error:
        return error
    code, msg, data = _request(
        client,
        "DELETE",
        _BATCH_DELETE_CHILDREN_URI,
        paths={"document_id": document_id, "block_id": parent_block_id},
        queries=[("document_revision_id", "-1")],
        body={"start_index": start, "end_index": end},
    )
    if code != 0:
        return tool_error(f"Delete document blocks failed: code={code} msg={msg}")
    return tool_result(success=True, deleted_count=end - start, **data)


def register_document_object_tools(ctx: Any) -> None:
    ctx.register_tool(
        name="feishu_doc_insert_blocks",
        toolset="feishu_doc",
        schema=FEISHU_DOC_INSERT_BLOCKS_SCHEMA,
        handler=handle_doc_insert_blocks,
        check_fn=_check_feishu,
        requires_env=[],
        is_async=False,
        description="Insert structured Feishu document blocks",
        emoji="🧱",
    )
    ctx.register_tool(
        name="feishu_doc_insert_media",
        toolset="feishu_doc",
        schema=FEISHU_DOC_INSERT_MEDIA_SCHEMA,
        handler=handle_doc_insert_media,
        check_fn=_check_feishu,
        requires_env=[],
        is_async=True,
        description="Insert an image or attachment into a Feishu document",
        emoji="🖼️",
    )
    ctx.register_tool(
        name="feishu_doc_update_blocks",
        toolset="feishu_doc",
        schema=FEISHU_DOC_UPDATE_BLOCKS_SCHEMA,
        handler=handle_doc_update_blocks,
        check_fn=_check_feishu,
        requires_env=[],
        is_async=True,
        description="Batch-update Feishu document blocks and media",
        emoji="✏️",
    )
    ctx.register_tool(
        name="feishu_doc_delete_blocks",
        toolset="feishu_doc",
        schema=FEISHU_DOC_DELETE_BLOCKS_SCHEMA,
        handler=handle_doc_delete_blocks,
        check_fn=_check_feishu,
        requires_env=[],
        is_async=False,
        description="Delete a range of Feishu document blocks",
        emoji="🗑️",
    )


__all__ = [
    "handle_doc_delete_blocks",
    "handle_doc_insert_blocks",
    "handle_doc_insert_media",
    "handle_doc_update_blocks",
    "register_document_object_tools",
]
