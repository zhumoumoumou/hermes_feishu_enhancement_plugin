---
name: document-authoring
description: Create, edit, inspect, and verify Feishu documents with rich text, structured blocks, media, embedded Bitable views, and directly embedded UML or diagram Boards. Use for new embedded Bitable creation, safe updates to an existing embedded Base, PlantUML/Mermaid/SVG rendering, idempotent synchronization, or recovery from a partial Feishu document write.
---

# Feishu Document Authoring

Use dedicated `feishu_doc` tools for complex objects. Do not assemble raw
Bitable Blocks when `feishu_doc_embed_bitable` can create and resolve them.

## Embed UML and diagrams directly

For UML, ER, flowcharts, activity diagrams, and mind maps, call
`feishu_doc_embed_diagram`. It creates a native Feishu Board Block inside the
document and then renders the supplied source into that Board. The result is
directly visible and editable in the document; do not substitute a bare Board
link, image, iframe, or code block.

Choose the source format deliberately:

- Prefer Mermaid for concise flowcharts, class diagrams, sequence diagrams,
  state diagrams, ER diagrams, and mind maps.
- Prefer PlantUML when its UML notation expresses the requested diagram more
  clearly, especially detailed class, component, activity, or sequence models.
- Use SVG for custom visual layouts that UML syntax cannot express. SVG is
  embedded in the same native Board, not uploaded as a document image.

Example of a directly embedded class diagram:

```json
{
  "document_id": "<docx id>",
  "syntax_type": "mermaid",
  "diagram_type": "class",
  "source": "classDiagram\nclass User {\n  +String name\n  +login()\n}\nclass Session\nUser \"1\" --> \"*\" Session"
}
```

For an existing Board, use `feishu_board_edit` with
`operation="render_syntax"`. Set `data.overwrite=true` only when the requested
change replaces the whole diagram; otherwise new diagram content is added.
Supply a stable 10-64 character `client_token` when retrying the same render.

Treat a `partial` embed result as a successful Block creation with unfinished
rendering. Keep its `block_id` and `whiteboard_id`, repair the syntax, and call
`feishu_board_edit render_syntax`; never call `feishu_doc_embed_diagram` again
for the same intended diagram. Finish by calling `get_nodes` and require a
non-empty node list.

Do not try to create Docx block type 21 directly. Although Feishu defines a
Diagram/UML Block structurally, Docx OpenAPI does not support creating, reading,
or editing it. The embedded Board workflow is the supported API route.

## Choose the Bitable workflow

- For a new embedded Base, call `feishu_doc_embed_bitable` once with `setup`.
- For an existing document, call `feishu_doc_resolve_bitable` first. Keep
  `require_unique=true`; stop if zero or multiple Blocks match.
- For an existing Base, call `feishu_bitable_sync` with the resolved
  `app_token` and `table_id`.
- Use `feishu_bitable_edit` only for low-level operations not covered by the
  declarative workflow. Use `data={"fetch_all":true}` for complete reads.

Never create a second embedded Base to recover from a partial result. Continue
with the returned identifiers.

## Create and configure in one call

Define fields, records, a unique record key, and views together. Mark one field
`primary=true` to rename the default primary field. Dates are Unix milliseconds.

```json
{
  "document_id": "<docx id>",
  "view_type": "gantt",
  "view_name": "项目甘特图",
  "setup": {
    "fields": [
      {"field_name": "任务", "type": 1, "primary": true},
      {"field_name": "开始时间", "type": 5},
      {"field_name": "结束时间", "type": 5},
      {"field_name": "看板阶段", "type": 3}
    ],
    "records": [
      {"fields": {
        "任务": "示例任务",
        "开始时间": 1784044800000,
        "结束时间": 1784048400000,
        "看板阶段": "进行中"
      }}
    ],
    "key_field": "任务",
    "start_field": "开始时间",
    "end_field": "结束时间",
    "views": [
      {"view_name": "工作看板", "view_type": "kanban", "group_field": "看板阶段"}
    ]
  }
}
```

The tool creates the document Block first, then fields and records, and only
then creates gantt/kanban/gallery/form views. This order prevents empty-table
view initialization from losing date or grouping candidates. A new gantt or
kanban view requires at least one supplied record with populated date or group
fields.

If no `setup` is supplied for gantt, gallery, or form, the tool creates only
the embedded grid and returns `needs_setup`. Continue with
`feishu_bitable_sync`; do not call the embed tool again.

## Update safely

Pass the desired fields and changed records to `feishu_bitable_sync`.

- `key_field` must uniquely identify each supplied and existing record.
- Existing keys are updated; missing keys are created; unchanged rows are left
  alone. Records absent from the request are not deleted.
- Set `delete_blank_records=true` only when default empty rows are known to be
  disposable. It deletes only records whose `fields` object is empty.
- Fields with matching names must have matching types. The tool stops instead
  of silently replacing an incompatible field.
- Existing view names must have matching view types. The tool never recreates
  a canonical matching view.
- Pagination, batch chunking, duplicate detection, and write verification are
  automatic and bounded.

## Treat view binding honestly

Feishu's server API creates native `gantt` and `kanban` views but does not
expose the editor's start/end-field or grouping-field selection for reliable
readback. The tool therefore validates the candidate fields and returns
`needs_manual_configuration` plus `manual_actions`.

Do not report a view as visually complete merely because `view_type` is
correct. Open the view once and confirm:

- gantt: start and end bindings use the declared date fields;
- kanban: grouping uses the declared single-select field.

An unambiguous candidate reduces risk but is not proof of the UI binding.

## Preserve embedded data during document edits

Before replacing surrounding document content, resolve and save the embedded
`block_id` and raw token. Delete only ranges before or after that Block, then
resolve it again and require the same token. Never clear the entire document
when it contains a canonical embedded Base.

## Respect platform limits

The Docx API creates a new empty embedded Bitable. Its returned `token` is
read-only, so it cannot attach an existing standalone Base. Edit an existing
Base directly and add a document link when manual UI insertion is unavailable.

Keep `link_preview` plus `sub_page_list` to five or fewer per insert call.
Split resource-heavy Blocks and do not retry an unchanged `1770035` request.
After every structural edit, inspect the document Blocks and relevant Bitable
fields, views, and records.
