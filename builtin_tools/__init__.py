"""Builtin tools for ProLight-agent. Auto-registered on import."""

from loguru import logger


def register_all(registry):
    from . import (
        meta_tools, win_tools, vision_tools,
        search_tools, mouse_tools, keybd_tools, uia_tools,
        probe_tools, learning_tools,
        fs_tools, edit_tools
    )
    meta_tools.register_all(registry)
    win_tools.register_all(registry)
    vision_tools.register_all(registry)
    search_tools.register_all(registry)
    mouse_tools.register_all(registry)
    keybd_tools.register_all(registry)
    uia_tools.register_all(registry)
    probe_tools.register_all(registry)
    learning_tools.register_all(registry)
    fs_tools.register_all(registry)
    edit_tools.register_all(registry)
    logger.info("All builtin tools registered")
