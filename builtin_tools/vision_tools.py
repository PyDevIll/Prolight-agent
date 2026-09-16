"""Vision analysis tool for ProLight-agent.

Tool:
  - vision_analyze : analyze an existing image file with the multimodal model

For capturing a window and analyzing it in one step, prefer ``win_see``.
"""

import json
from pathlib import Path

from loguru import logger

from lib.vision_client import analyze_image


async def vision_analyze(image_path: str, query: str) -> str:
    """Analyze an image file with the vision model.

    Args:
        image_path: path to an image file (e.g. saved by win_get_image).
        query: specific question about the image.
    """
    path = Path(image_path)
    if not path.exists():
        return json.dumps({"ok": False, "error": f"Image not found: {image_path}"}, ensure_ascii=False)

    try:
        answer = await analyze_image(path, query)
    except Exception as e:
        logger.error(f"vision_analyze failed: {e}")
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)

    return json.dumps({"ok": True, "path": str(path), "answer": answer}, ensure_ascii=False, indent=2)


TOOL_DEFINITIONS = [
    (
        "vision_analyze",
        vision_analyze,
        "Analyze an existing image file with the vision model. Ask a specific "
        "question. (To capture and analyze a window in one step, use win_see.)",
        {
            "type": "object",
            "properties": {
                "image_path": {"type": "string", "description": "Path to the image file"},
                "query": {"type": "string", "description": "Specific question about the image"},
            },
            "required": ["image_path", "query"],
        },
    ),
]


def register_all(registry):
    for name, func, desc, params in TOOL_DEFINITIONS:
        registry.register_function(func, name, desc, params)
    logger.info(f"Registered {len(TOOL_DEFINITIONS)} vision tool(s)")
