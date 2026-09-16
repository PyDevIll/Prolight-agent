"""Builtin tools for ProLight-agent. Auto-registered on import."""

from loguru import logger


def register_all(registry):
    from . import meta_tools
    meta_tools.register_all(registry)
    logger.info("All builtin tools registered")
