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
    handle_doc_insert_blocks,
    handle_doc_insert_media,
    register_document_object_tools,
)
from .menu_events import MenuEventEnhancementMixin, parse_menu_command, resolve_dm_chat_id
from .plugin import EnhancedFeishuAdapter, register

__all__ = [
    "EnhancedFeishuAdapter",
    "MenuEventEnhancementMixin",
    "bind_adapter",
    "clear_document_client",
    "get_bound_client",
    "handle_doc_append_text",
    "handle_doc_create",
    "handle_doc_insert_blocks",
    "handle_doc_insert_media",
    "handle_doc_share",
    "handle_doc_update_text",
    "inject_document_client",
    "parse_menu_command",
    "register",
    "register_document_tools",
    "register_document_object_tools",
    "resolve_session_open_id",
    "resolve_dm_chat_id",
]
