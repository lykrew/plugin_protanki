from __future__ import annotations

import sys
import threading
from pathlib import Path


def play_join_success() -> None:
    """Play the bundled success chime without blocking the UI thread."""
    if sys.platform != "win32":
        return

    def play() -> None:
        try:
            import winsound

            sound = Path(__file__).resolve().parent / "assets" / "join-success.wav"
            winsound.PlaySound(
                str(sound), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            )
        except (ImportError, OSError):
            pass

    threading.Thread(target=play, daemon=True, name="protanki-join-sound").start()
