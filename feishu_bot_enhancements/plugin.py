"""Compose and register Feishu bot enhancements."""

from __future__ import annotations

from dataclasses import replace

from gateway.platform_registry import platform_registry
from plugins.platforms.feishu import adapter as _bundled

from . import document_access as _document_access
from . import document_domains as _document_domains
from . import document_management as _document_management
from . import document_objects as _document_objects
from . import document_tools as _document_tools
from . import menu_events as _menu_events
from . import wiki_tools as _wiki_tools
from .menu_events import MenuEventEnhancementMixin, parse_menu_command, resolve_dm_chat_id
from .topic_context import TopicContextEnhancementMixin


BundledFeishuAdapter = _bundled.FeishuAdapter


class EnhancedFeishuAdapter(
    TopicContextEnhancementMixin,
    MenuEventEnhancementMixin,
    BundledFeishuAdapter,
):
    """Bundled Feishu adapter plus all locally managed enhancements."""

    def _build_lark_client(self, domain):
        """Build the shared SDK client with a finite network timeout."""
        client = super()._build_lark_client(domain)
        sdk_config = getattr(client, "_config", None)
        if sdk_config is not None:
            sdk_config.timeout = _document_access.request_timeout_seconds(self)
        return client


# Compatibility alias for existing callers and tests.
MenuEnabledFeishuAdapter = EnhancedFeishuAdapter


def _build_adapter(config):
    adapter = EnhancedFeishuAdapter(config)
    try:
        from hermes_cli.profiles import get_active_profile_name

        adapter._enhancement_profile_name = get_active_profile_name() or "default"
    except Exception:
        adapter._enhancement_profile_name = "default"
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
    _document_management.register_document_management_tools(ctx)
    _document_domains.register_document_domain_tools(ctx)
    _wiki_tools.register_wiki_tools(ctx)
    ctx.register_hook("pre_tool_call", _document_access.inject_document_client)
    ctx.register_hook("post_tool_call", _document_access.clear_document_client)


__all__ = [
    "BundledFeishuAdapter",
    "EnhancedFeishuAdapter",
    "MenuEnabledFeishuAdapter",
    "_build_adapter",
    "_bundled",
    "_document_access",
    "_document_domains",
    "_document_management",
    "_document_objects",
    "_document_tools",
    "_menu_events",
    "_wiki_tools",
    "TopicContextEnhancementMixin",
    "parse_menu_command",
    "platform_registry",
    "register",
    "resolve_dm_chat_id",
]
