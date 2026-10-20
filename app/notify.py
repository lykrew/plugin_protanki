from __future__ import annotations

import sys
import threading


def play_join_bell() -> None:
    threading.Thread(target=_ring, daemon=True).start()


def _ring() -> None:
    if sys.platform != "win32":
        return
    try:
        import winsound
    except ImportError:
        return
    winsound.Beep(880, 120)
    winsound.Beep(1174, 220)
