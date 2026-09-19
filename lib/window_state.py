"""Window state snapshots and diffs for ProLight-agent.

Instead of many separate perception tools (window info, UIA tree, menu, image,
OCR), the agent takes **one** snapshot of the active window and then asks only
"what changed". This module builds that state, assigns stable element ids the
agent can act on, stores a bounded history, and computes diffs.

Sources merged into a snapshot:
  * window identity/state  вЂ” ``lib.winapi``
  * keyboard focus         вЂ” ``lib.winapi.get_thread_focus_info`` (+ probe)
  * menu-bar items         вЂ” ``lib.winapi.get_menu_items``
  * UI Automation controls вЂ” ``lib.ui_tree`` (with Chromium a11y auto-enable)
  * OCR text               вЂ” ``lib.ocr`` (Windows.Media.Ocr)
  * pixels                 вЂ” ``lib.image_ops`` (PrintWindow / mss)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

import mss
from loguru import logger
from PIL import Image

from lib import image_ops, ocr, ui_tree, winapi

# Control types worth showing (interactive first). Lower-cased for matching.
_ACTIONABLE_TYPES = [
    "button", "splitbutton", "checkbox", "radiobutton", "combobox", "edit",
    "textbox", "spinner", "slider", "hyperlink", "listitem", "menuitem",
    "tabitem", "tab", "treeitem", "text",
]
_ACTIONABLE = set(_ACTIONABLE_TYPES)

_CHROMIUM_PROC = ("chrome", "msedge", "brave", "opera", "vivaldi", "chromium")

_MAX_SNAPSHOTS = 8
_STORE_MAX_DIM = 640  # stored copy used for pixel diff


# в”Ђв”Ђ data model в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
@dataclass
class Element:
    id: str
    kind: str  # "control" | "text" | "menu"
    name: str
    rect: Optional[list] = None  # screen coords [left, top, right, bottom]
    control_type: str = ""
    automation_id: str = ""
    value: str = ""
    enabled: Optional[bool] = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "kind": self.kind, "name": self.name, "rect": self.rect}
        if self.kind == "control":
            d["type"] = self.control_type
            if self.automation_id:
                d["automation_id"] = self.automation_id
            if self.value:
                d["value"] = self.value
            if self.enabled is not None:
                d["enabled"] = self.enabled
        return d

    def center(self) -> Optional[tuple[int, int]]:
        if not self.rect:
            return None
        l, t, r, b = self.rect
        return (int((l + r) // 2), int((t + b) // 2))


@dataclass
class WindowState:
    snapshot_id: str
    created: float
    hwnd: Optional[int]
    mode: str  # "window" | "screen"
    window: dict = field(default_factory=dict)
    focus: dict = field(default_factory=dict)
    menu: list = field(default_factory=list)
    controls: list = field(default_factory=list)
    texts: list = field(default_factory=list)
    uia_coverage: str = "n/a"
    accessibility_enabled: Optional[bool] = None
    types: dict = field(default_factory=dict)
    image_path: Optional[str] = None
    image_meta: Optional[dict] = None
    image: Optional[Image.Image] = None
    notes: list = field(default_factory=list)
    include: list = field(default_factory=list)
    max_controls: int = 60
    max_text: int = 60

    def all_elements(self) -> list:
        return list(self.controls) + list(self.texts) + list(self.menu)

    def find(self, element_id: str) -> Optional[Element]:
        for e in self.all_elements():
            if e.id == element_id:
                return e
        return None


# в”Ђв”Ђ registry в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
_snapshots: dict[str, WindowState] = {}
_current: Optional[WindowState] = None
_id_counter = 0
_seq = 0


def _new_id(prefix: str) -> str:
    global _id_counter
    _id_counter += 1
    return f"{prefix}{_id_counter}"


def _identity(e: Element) -> tuple:
    if e.kind == "control":
        return ("control", e.automation_id or f"{e.control_type}|{e.name}")
    return (e.kind, _norm(e.name))


def store(state: WindowState, label: str = "") -> None:
    global _current
    _current = state
    if label:
        _snapshots[label] = state
    while len(_snapshots) > _MAX_SNAPSHOTS:
        oldest = min(_snapshots.values(), key=lambda s: s.created)
        for k in [k for k, v in _snapshots.items() if v is oldest]:
            del _snapshots[k]


def get(label: str = "") -> Optional[WindowState]:
    """Resolve a snapshot by ``label`` (or the most recent when empty).

    The auto-generated ``snapshot_id`` (``"s1"``) returned by ``win_snapshot``
    is also accepted as a handle, so the agent can feed it straight back into
    ``win_changes(label=...)`` without inventing its own label.
    """
    if not label:
        return _current
    st = _snapshots.get(label)
    if st is not None:
        return st
    if _current is not None and _current.snapshot_id == label:
        return _current
    for st in _snapshots.values():
        if st.snapshot_id == label:
            return st
    return None


def resolve_id(element_id: str, label: str = "") -> Optional[Element]:
    st = get(label)
    return st.find(element_id) if st else None


# в”Ђв”Ђ helpers в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def _norm(s) -> str:
    return " ".join((s or "").split()).strip().lower()


def _select_controls(raw: list[dict]) -> list[dict]:
    """Actionable controls, plus named non-actionable ones (e.g. a named pane
    that exposes app state). The window root is dropped."""
    return [
        c for c in raw
        if (c.get("control_type") or "").lower() in _ACTIONABLE
        or ((c.get("name") or "").strip()
            and (c.get("control_type") or "").lower() not in ("window", ""))
    ]


def _rank_controls(controls: list[dict]) -> list[dict]:
    order = {t: i for i, t in enumerate(_ACTIONABLE_TYPES)}

    def key(c):
        ct = (c.get("control_type") or "").lower()
        rect = c.get("rect") or {}
        return (order.get(ct, 99), rect.get("top", 0), rect.get("left", 0))

    return sorted(controls, key=key)


def _rect_list(rect) -> Optional[list]:
    if not rect:
        return None
    if isinstance(rect, dict):
        return [rect["left"], rect["top"], rect["right"], rect["bottom"]]
    return [int(v) for v in rect]


async def _enum_uia(hwnd: int, max_controls: int) -> list[dict]:
    try:
        return await ui_tree.enum_controls(
            hwnd, max_depth=3, max_controls=max(200, max_controls * 4)
        )
    except Exception as e:
        logger.warning(f"window_state: UIA enumeration failed: {e}")
        return []


# в”Ђв”Ђ capture в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
async def capture_state(
    *,
    hwnd: Optional[int] = None,
    region=None,
    monitor: Optional[int] = None,
    include=None,
    max_controls: int = 60,
    max_text: int = 60,
    probe: bool = False,
    lang: Optional[str] = None,
    save_image: bool = False,
    baseline: Optional[WindowState] = None,
) -> WindowState:
    """Build a full state snapshot of the active window (or a monitor)."""
    global _seq
    _seq += 1
    include = set(include) if include else {"uia", "text", "menu", "focus", "image"}

    if hwnd is None and monitor is None and region is None:
        hwnd = winapi.get_foreground_window() or None
    screen_mode = hwnd is None
    if screen_mode and monitor is None and region is None:
        monitor = 1

    state = WindowState(
        snapshot_id=f"s{_seq}", created=time.time(), hwnd=hwnd,
        mode="screen" if screen_mode else "window",
        include=sorted(include), max_controls=int(max_controls), max_text=int(max_text),
    )
    # One-to-one id reuse by identity, so duplicate names (common in OCR text)
    # don't collide and ids stay stable across snapshots.
    old_pool: dict = {}
    if baseline:
        for e in baseline.all_elements():
            old_pool.setdefault(_identity(e), []).append(e)
    claimed: set = set()

    def _reuse(el: Element) -> Optional[str]:
        for o in old_pool.get(_identity(el), []):
            if id(o) not in claimed:
                claimed.add(id(o))
                return o.id
        return None

    # в”Ђв”Ђ window identity + focus + menu в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if not screen_mode:
        state.window = winapi.get_window_info(hwnd)
        if "focus" in include:
            thread = winapi._get_window_thread_id(hwnd)
            info = winapi.get_thread_focus_info(thread)
            keyboard = bool(info.get("hwnd_focus")) and \
                winapi._get_window_thread_id(info["hwnd_focus"]) == thread
            state.focus = {
                "foreground": state.window.get("foreground"),
                "keyboard_focus": keyboard,
                "focus_hwnd": info.get("hwnd_focus", 0),
                "caret_hwnd": info.get("hwnd_caret", 0),
            }
            if probe:
                probe_res = winapi.verify_keyboard(hwnd)
                state.focus["keyboard_delivery"] = probe_res.get("keyboard_delivery")
                state.focus["mouse_ok"] = probe_res.get("mouse_ok")
                state.focus["keyboard_ok"] = probe_res.get("keyboard_ok")
        if "menu" in include:
            for m in winapi.get_menu_items(hwnd):
                rect = _rect_list(m.get("rect"))
                if rect:
                    el = Element(id="", kind="menu", name=m.get("text", ""), rect=rect)
                    el.id = _reuse(el) or _new_id("m")
                    state.menu.append(el)

    # в”Ђв”Ђ UIA controls (+ Chromium accessibility auto-enable) в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if "uia" in include and not screen_mode:
        raw = await _enum_uia(hwnd, max_controls)
        selected = _select_controls(raw)
        state.types = {}
        for c in raw:
            t = c.get("control_type") or "?"
            state.types[t] = state.types.get(t, 0) + 1
        coverage = "good" if len(selected) >= 5 else ("poor" if selected else "empty")
        state.uia_coverage = coverage

        proc = (state.window.get("process") or "").lower()
        is_chromium = any(b in proc for b in _CHROMIUM_PROC) or \
            "chrome_widgetwin" in (state.window.get("class") or "").lower()
        if is_chromium and coverage in ("poor", "empty"):
            winapi.send_getobject(hwnd)
            child = winapi.find_child_by_class(hwnd, "Chrome_RenderWidgetHostHWND")
            if child:
                winapi.send_getobject(child)
            await asyncio.sleep(1.0)
            raw = await _enum_uia(hwnd, max_controls)
            selected = _select_controls(raw)
            state.uia_coverage = "good" if len(selected) >= 5 else ("poor" if selected else "empty")
            state.accessibility_enabled = len(selected) >= 5
            state.types = {}
            for c in raw:
                t = c.get("control_type") or "?"
                state.types[t] = state.types.get(t, 0) + 1
            state.notes.append(
                "chromium accessibility "
                + ("enabled" if state.accessibility_enabled else "still off")
            )

        for c in _rank_controls(selected)[:max_controls]:
            rect = _rect_list(c.get("rect"))
            el = Element(
                id="", kind="control", name=c.get("name", ""), rect=rect,
                control_type=c.get("control_type", ""),
                automation_id=c.get("automation_id", ""),
                value=c.get("value", ""), enabled=c.get("enabled"),
            )
            el.id = _reuse(el) or _new_id("c")
            state.controls.append(el)

    # в”Ђв”Ђ image в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if screen_mode:
        if region is not None:
            screen_rect = image_ops.normalize_rect(region)
            full = image_ops.grab_screen(screen_rect)
        else:
            with mss.mss() as sct:
                mon = sct.monitors[monitor or 1]
            screen_rect = (mon["left"], mon["top"], mon["left"] + mon["width"], mon["top"] + mon["height"])
            full = image_ops.grab_screen(screen_rect)
    else:
        full = image_ops.grab_window(hwnd)
        wr = winapi.get_window_rect(hwnd)
        screen_rect = (wr["left"], wr["top"], wr["right"], wr["bottom"]) if wr else None

    if full is not None and screen_rect:
        state.image_meta = {
            "screen_rect": list(screen_rect),
            "image_size": {"width": full.width, "height": full.height},
            "scale_x": (screen_rect[2] - screen_rect[0]) / full.width if full.width else 1.0,
            "scale_y": (screen_rect[3] - screen_rect[1]) / full.height if full.height else 1.0,
        }
        state.image = image_ops.downscale(full, _STORE_MAX_DIM)
        if save_image:
            state.image_path = str(image_ops.save(full, f"snap_{hwnd or 'screen'}"))

    # в”Ђв”Ђ OCR text в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
    if "text" in include and full is not None:
        try:
            if lang in (None, "", "auto"):
                result = await ocr.recognize_auto(full)
            else:
                result = await ocr.recognize(full, lang=lang)
        except Exception as e:
            state.notes.append(f"ocr failed: {e}")
            result = None
        if result:
            control_names = {_norm(c.name) for c in state.controls if c.name}
            meta = state.image_meta or {}
            for line in result.get("lines", [])[:max_text]:
                text = line.get("text", "").strip()
                if not text or _norm(text) in control_names:
                    continue
                bbox = line.get("bbox")
                rect = None
                if bbox and meta:
                    x0, y0 = image_ops.map_image_point(meta, bbox[0], bbox[1])
                    x1, y1 = image_ops.map_image_point(meta, bbox[2], bbox[3])
                    rect = [x0, y0, x1, y1]
                el = Element(id="", kind="text", name=text, rect=rect)
                el.id = _reuse(el) or _new_id("t")
                state.texts.append(el)

    return state


# в”Ђв”Ђ diff в”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђв”Ђ
def diff_states(old: WindowState, new: WindowState, threshold: int = 12) -> dict:
    """Return only what changed between two snapshots (UIA/OCR/pixels)."""
    old_by_id = {e.id: e for e in old.all_elements()}
    new_by_id = {e.id: e for e in new.all_elements()}

    added = [e.to_dict() for eid, e in new_by_id.items() if eid not in old_by_id]
    removed = [eid for eid in old_by_id if eid not in new_by_id]
    changed = []
    for eid in old_by_id.keys() & new_by_id.keys():
        o, n = old_by_id[eid], new_by_id[eid]
        fields = {}
        if o.rect != n.rect:
            fields["rect"] = n.rect
        if o.name != n.name:
            fields["name"] = n.name
        if o.value != n.value:
            fields["value"] = n.value
        if o.enabled != n.enabled:
            fields["enabled"] = n.enabled
        if fields:
            changed.append({"id": eid, **fields})

    pixels = None
    if old.image is not None and new.image is not None:
        try:
            pixels = image_ops.pixel_diff(old.image, new.image, threshold=threshold)
        except Exception:
            pixels = None
    if pixels and pixels.get("bbox") and new.image_meta:
        b = pixels["bbox"]
        try:
            x0, y0 = image_ops.map_image_point(new.image_meta, b[0], b[1])
            x1, y1 = image_ops.map_image_point(new.image_meta, b[2], b[3])
            pixels["screen_bbox"] = [x0, y0, x1, y1]
        except Exception:
            pass

    changed_flag = bool(added or removed or changed or (pixels and pixels.get("changed")))
    return {
        "changed": changed_flag,
        "uia": {
            "added": added,
            "removed": removed,
            "changed": changed,
        },
        "pixels": pixels,
    }

