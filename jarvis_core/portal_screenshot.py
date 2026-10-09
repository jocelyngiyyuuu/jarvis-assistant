#!/usr/bin/python3
"""Take one Wayland screenshot through the desktop portal."""

from pathlib import Path
import shutil
import sys
import uuid
from urllib.parse import unquote, urlparse

import dbus
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib


def main():
    if len(sys.argv) != 2:
        return 2
    destination = Path(sys.argv[1]).resolve()
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    portal = bus.get_object(
        "org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"
    )
    interface = dbus.Interface(portal, "org.freedesktop.portal.Screenshot")
    loop = GLib.MainLoop()
    outcome = {"ok": False}
    token = "jarvis_" + uuid.uuid4().hex

    request_path = interface.Screenshot(
        "",
        {
            "handle_token": dbus.String(token),
            "interactive": dbus.Boolean(False),
        },
    )
    request = bus.get_object("org.freedesktop.portal.Desktop", request_path)

    def on_response(response, results):
        try:
            uri = str(results.get("uri", ""))
            parsed = urlparse(uri)
            source = Path(unquote(parsed.path)) if parsed.scheme == "file" else None
            if int(response) == 0 and source and source.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                outcome["ok"] = destination.is_file() and destination.stat().st_size > 0
        finally:
            loop.quit()

    request.connect_to_signal(
        "Response", on_response, dbus_interface="org.freedesktop.portal.Request"
    )
    timeout_id = GLib.timeout_add_seconds(20, lambda: (loop.quit(), False)[1])
    loop.run()
    GLib.source_remove(timeout_id)
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
