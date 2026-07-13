"""Shared enhancement modules for Hermes Feishu bots."""

from .document_access import (
    bind_adapter,
    clear_document_client,
    get_bound_client,
    inject_document_client,
)
from .menu_events import MenuEventEnhancementMixin, parse_menu_command, resolve_dm_chat_id
from .plugin import EnhancedFeishuAdapter, register

__all__ = [
    "EnhancedFeishuAdapter",
    "MenuEventEnhancementMixin",
    "bind_adapter",
    "clear_document_client",
    "get_bound_client",
    "inject_document_client",
    "parse_menu_command",
    "register",
    "resolve_dm_chat_id",
]
