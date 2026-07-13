"""Make Hermes' built-in Feishu document reader usable in normal bot chats."""

from __future__ import annotations

import logging
import threading
from typing import Any


logger = logging.getLogger("hermes.plugins.feishu_bot_enhancements.document_access")
_DOC_READ_TOOL = "feishu_doc_read"
_adapter_lock = threading.RLock()
_active_adapter: Any = None


def bind_adapter(adapter: Any) -> None:
    """Expose the process-local Feishu adapter to tool-call hooks."""
    global _active_adapter
    with _adapter_lock:
        _active_adapter = adapter


def get_bound_client() -> Any:
    """Return the current adapter's Lark client, if it is connected."""
    with _adapter_lock:
        adapter = _active_adapter
    if adapter is None or getattr(adapter, "_running", None) is False:
        return None
    return getattr(adapter, "_client", None)


def inject_document_client(*, tool_name: str = "", **_: Any) -> None:
    """Inject the connected client into the built-in reader's worker thread."""
    if tool_name != _DOC_READ_TOOL:
        return

    client = get_bound_client()
    if client is None:
        logger.debug("Feishu document read requested before adapter client was ready")
        return

    from tools.feishu_doc_tool import set_client

    set_client(client)


def clear_document_client(*, tool_name: str = "", **_: Any) -> None:
    """Remove the thread-local client after one document tool call."""
    if tool_name != _DOC_READ_TOOL:
        return

    from tools.feishu_doc_tool import set_client

    set_client(None)


__all__ = [
    "bind_adapter",
    "clear_document_client",
    "get_bound_client",
    "inject_document_client",
]
