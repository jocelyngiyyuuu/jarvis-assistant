#!/usr/bin/python3
"""Enumerate or activate controls in the active desktop window via AT-SPI."""

import hashlib
import json
import sys

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402


ACTION_ROLES = {
    "push button", "toggle button", "check box", "radio button", "link",
    "menu item", "check menu item", "radio menu item", "page tab",
    "entry", "password text", "text", "combo box", "list item",
}


def _state(accessible, state):
    try:
        return accessible.get_state_set().contains(state)
    except Exception:
        return False


def _text(value):
    return " ".join(str(value or "").split())[:180]


def _actions(accessible):
    try:
        interface = accessible.get_action_iface()
        if interface is None:
            return []
        return [_text(interface.get_name(index)).casefold()
                for index in range(interface.get_n_actions())]
    except Exception:
        return []


def _bounds(accessible):
    try:
        component = accessible.get_component_iface()
        if component is None:
            return None
        rect = component.get_extents(Atspi.CoordType.SCREEN)
        if rect.width <= 0 or rect.height <= 0:
            return None
        return [int(rect.x), int(rect.y), int(rect.width), int(rect.height)]
    except Exception:
        return None


def _identity(item):
    material = "\0".join([
        item["app"], item["window"], item["role"], item["name"],
        ",".join(map(str, item.get("bounds") or [])),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _active_roots(window_hint=""):
    desktop = Atspi.get_desktop(0)
    active = []
    visible = []
    for app_index in range(desktop.get_child_count()):
        app = desktop.get_child_at_index(app_index)
        if app is None:
            continue
        app_name = _text(app.get_name())
        for index in range(app.get_child_count()):
            window = app.get_child_at_index(index)
            if window is None or not _state(window, Atspi.StateType.SHOWING):
                continue
            entry = (window, app_name, _text(window.get_name()))
            visible.append(entry)
            if _state(window, Atspi.StateType.ACTIVE):
                active.append(entry)
    roots = active or visible[-1:]
    hint = _text(window_hint).casefold()
    if hint:
        matching = [entry for entry in visible if (
            hint in entry[2].casefold() or entry[2].casefold() in hint
        )]
        if len(matching) == 1:
            roots = matching
    return roots


def activate(target_id, window_hint=""):
    matches = [item for item in enumerate_with_objects(limit=240, window_hint=window_hint)
               if item["data"]["id"] == target_id]
    if len(matches) != 1:
        return {"ok": False, "error": "target_changed"}
    accessible, data = matches[0]["accessible"], matches[0]["data"]
    try:
        action = accessible.get_action_iface()
        if action is not None:
            names = data["actions"]
            preferred = ("click", "press", "activate", "jump", "open")
            index = next(
                (names.index(name) for name in preferred if name in names),
                0 if names else None,
            )
            if index is not None and action.do_action(index):
                return {"ok": True, "method": "action", "target": data}
    except Exception:
        pass
    x, y, width, height = data["bounds"]
    try:
        Atspi.generate_mouse_event(x + width // 2, y + height // 2, "b1c")
        return {"ok": True, "method": "mouse", "target": data}
    except Exception:
        return {"ok": False, "error": "activation_failed"}


def enumerate_with_objects(limit=160, window_hint=""):
    """Internal equivalent of enumerate_controls retaining object handles."""
    output = []
    visited = 0
    stack = [(root, app, window, 0) for root, app, window in _active_roots(window_hint)]
    while stack and len(output) < limit and visited < 1200:
        accessible, app_name, window_name, depth = stack.pop()
        visited += 1
        if accessible is None or depth > 24:
            continue
        name = _text(accessible.get_name())
        try:
            role = _text(accessible.get_role_name()).casefold()
        except Exception:
            role = ""
        actions, bounds = _actions(accessible), _bounds(accessible)
        if name and bounds and (actions or role in ACTION_ROLES):
            data = {"app": app_name, "window": window_name, "name": name,
                    "role": role, "actions": actions, "bounds": bounds}
            data["id"] = _identity(data)
            output.append({"accessible": accessible, "data": data})
        try:
            children = [accessible.get_child_at_index(index)
                        for index in range(accessible.get_child_count())]
        except Exception:
            children = []
        for child in reversed(children):
            stack.append((child, app_name, window_name, depth + 1))
    return output


# Keep activation and public enumeration on the same traversal implementation.
def enumerate_controls(limit=160, window_hint=""):
    return [entry["data"] for entry in enumerate_with_objects(limit, window_hint)]


def main():
    if len(sys.argv) < 2:
        return 2
    if sys.argv[1] == "list":
        result = {"ok": True, "controls": enumerate_controls(
            window_hint=sys.argv[2] if len(sys.argv) > 2 else ""
        )}
    elif sys.argv[1] == "activate" and len(sys.argv) in {3, 4}:
        result = activate(sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else "")
    else:
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
