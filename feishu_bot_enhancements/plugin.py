"""Compose and register Feishu bot enhancements."""

from __future__ import annotations

from dataclasses import replace

from gateway.platform_registry import platform_registry
from plugins.platforms.feishu import adapter as _bundled

from . import document_access as _document_access
from . import document_objects as _document_objects
from . import document_tools as _document_tools
from . import menu_events as _menu_events
from .menu_events import MenuEventEnhancementMixin, parse_menu_command, resolve_dm_chat_id


BundledFeishuAdapter = _bundled.FeishuAdapter


class EnhancedFeishuAdapter(MenuEventEnhancementMixin, BundledFeishuAdapter):
    """Bundled Feishu adapter plus all locally managed enhancements."""


# Compatibility alias for existing callers and tests.
MenuEnabledFeishuAdapter = EnhancedFeishuAdapter


def _build_adapter(config):
    adapter = EnhancedFeishuAdapter(config)
    _document_access.bind_adapter(adapter)
    return adapter


def register(ctx) -> None:
    """Wrap the registered Feishu entry while preserving bundled metadata."""
    existing = platform_registry.get("feishu")
    if existing is None:
        raise RuntimeError("Bundled Feishu platform entry is unavailable")
    platform_registry.register(
        replace(
            existing,
            adapter_factory=_build_adapter,
            source="plugin",
            plugin_name=ctx.manifest.name,
        )
    )
    _document_tools.register_document_tools(ctx)
    _document_objects.register_document_object_tools(ctx)
    ctx.register_hook("pre_tool_call", _document_access.inject_document_client)
    ctx.register_hook("post_tool_call", _document_access.clear_document_client)


__all__ = [
    "BundledFeishuAdapter",
    "EnhancedFeishuAdapter",
    "MenuEnabledFeishuAdapter",
    "_build_adapter",
    "_bundled",
    "_document_access",
    "_document_objects",
    "_document_tools",
    "_menu_events",
    "parse_menu_command",
    "platform_registry",
    "register",
    "resolve_dm_chat_id",
]
