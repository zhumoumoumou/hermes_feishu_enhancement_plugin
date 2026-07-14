"""Shared enhancement modules for Hermes Feishu bots."""

from .document_access import (
    bind_adapter,
    clear_document_client,
    get_bound_client,
    inject_document_client,
)
from .document_tools import (
    handle_doc_append_text,
    handle_doc_create,
    handle_doc_share,
    handle_doc_update_text,
    register_document_tools,
    resolve_session_open_id,
)
from .document_objects import (
    handle_doc_delete_blocks,
    handle_doc_insert_blocks,
    handle_doc_insert_media,
    handle_doc_update_blocks,
    register_document_object_tools,
)
from .document_management import (
    handle_doc_comments,
    handle_doc_get_blocks,
    handle_doc_manage,
    register_document_management_tools,
)
from .document_domains import (
    handle_bitable_edit,
    handle_bitable_sync,
    handle_board_edit,
    handle_doc_embed_bitable,
    handle_doc_embed_diagram,
    handle_doc_resolve_bitable,
    handle_sheet_edit,
    handle_task_edit,
    register_document_domain_tools,
)
from .menu_events import MenuEventEnhancementMixin, parse_menu_command, resolve_dm_chat_id
from .wiki_tools import handle_feishu_wiki, register_wiki_tools
from .topic_context import TopicContextEnhancementMixin
from .plugin import EnhancedFeishuAdapter, register

__all__ = [
    "EnhancedFeishuAdapter",
    "MenuEventEnhancementMixin",
    "TopicContextEnhancementMixin",
    "bind_adapter",
    "clear_document_client",
    "get_bound_client",
    "handle_bitable_edit",
    "handle_bitable_sync",
    "handle_board_edit",
    "handle_doc_comments",
    "handle_doc_get_blocks",
    "handle_doc_manage",
    "handle_doc_append_text",
    "handle_doc_create",
    "handle_doc_delete_blocks",
    "handle_doc_embed_bitable",
    "handle_doc_embed_diagram",
    "handle_doc_resolve_bitable",
    "handle_doc_insert_blocks",
    "handle_doc_insert_media",
    "handle_doc_share",
    "handle_doc_update_blocks",
    "handle_doc_update_text",
    "handle_sheet_edit",
    "handle_task_edit",
    "handle_feishu_wiki",
    "inject_document_client",
    "parse_menu_command",
    "register",
    "register_document_tools",
    "register_document_object_tools",
    "register_document_domain_tools",
    "register_document_management_tools",
    "register_wiki_tools",
    "resolve_session_open_id",
    "resolve_dm_chat_id",
]
