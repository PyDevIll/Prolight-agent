"""Builtin tools for ProLight-agent. Auto-registered on import."""

from loguru import logger


def register_all(registry):
    from . import meta_tools, win_tools, capture_tools, vision_tools
    meta_tools.register_all(registry)
    win_tools.register_all(registry)
    capture_tools.register_all(registry)
    vision_tools.register_all(registry)
    logger.info("All builtin tools registered")
