"""Hermes entry point for the shared Feishu bot enhancement suite.

Standalone Hermes plugins may be loaded from their ``__init__.py`` without a
normal package parent. Add only this plugin root to ``sys.path`` so the unique
internal package can be imported consistently by Hermes and by tests.
"""

from pathlib import Path
import sys

_PLUGIN_ROOT = str(Path(__file__).resolve().parent)
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

from feishu_bot_enhancements.plugin import (  # noqa: E402
    BundledFeishuAdapter,
    EnhancedFeishuAdapter,
    MenuEnabledFeishuAdapter,
    TopicContextEnhancementMixin,
    _build_adapter,
    _bundled,
    _document_access,
    _document_domains,
    _document_management,
    _document_objects,
    _document_tools,
    _menu_events,
    parse_menu_command,
    platform_registry,
    register,
    resolve_dm_chat_id,
)

__all__ = [
    "BundledFeishuAdapter",
    "EnhancedFeishuAdapter",
    "MenuEnabledFeishuAdapter",
    "TopicContextEnhancementMixin",
    "_build_adapter",
    "_bundled",
    "_document_access",
    "_document_domains",
    "_document_management",
    "_document_objects",
    "_document_tools",
    "_menu_events",
    "parse_menu_command",
    "platform_registry",
    "register",
    "resolve_dm_chat_id",
]
