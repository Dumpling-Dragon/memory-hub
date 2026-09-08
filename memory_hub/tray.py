from __future__ import annotations

import threading
import webbrowser
from .config import Config, local_url


def start_tray(sync_callback, config: Config | None = None) -> bool:
    """Start an optional tray icon. Core operation never depends on pystray."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return False

    image = Image.new("RGBA", (64, 64), (23, 107, 90, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((16, 16, 48, 48), fill=(255, 255, 255, 255))

    def open_page(icon, item):
        webbrowser.open(local_url(config or Config()))

    def sync_now(icon, item):
        threading.Thread(target=sync_callback, daemon=True).start()

    icon = pystray.Icon("MemoryHub", image, "Memory Hub", menu=pystray.Menu(
        pystray.MenuItem("Open Memory Hub", open_page, default=True),
        pystray.MenuItem("Sync now", sync_now),
        pystray.MenuItem("Quit", lambda icon, item: icon.stop()),
    ))
    threading.Thread(target=icon.run, name="memory-hub-tray", daemon=True).start()
    return True
