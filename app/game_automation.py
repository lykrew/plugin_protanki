from __future__ import annotations

import ctypes
import re
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes
from typing import Callable

import psutil


POLL_SECONDS = 1.5
RETRY_SECONDS = 8.0


def battle_id_from_link(value: str) -> str:
    match = re.search(r"#/battle/([0-9a-f]{8,40})", value.strip(), re.IGNORECASE)
    return match.group(1).lower() if match else ""


@dataclass(frozen=True)
class GameWindow:
    handle: int
    pid: int
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class BattleScreen:
    selected: bool
    red_free: int
    blue_free: int
    join_buttons_visible: bool


class BattleJoinWatcher:
    """Use the running game UI to wait for a free seat and join that battle."""

    def __init__(
        self,
        link: str,
        process_names: list[str],
        on_status: Callable[[str], None],
        on_finished: Callable[[str], None],
    ) -> None:
        self.battle_id = battle_id_from_link(link)
        self.process_names = process_names
        self.on_status = on_status
        self.on_finished = on_finished
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.battle_id:
            raise ValueError("Enter a battle link like #/battle/0123456789abcdef")
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _say(self, message: str) -> None:
        self.on_status(message)

    def _finish(self, message: str) -> None:
        self._stop.set()
        self.on_finished(message)

    def _run(self) -> None:
        if __import__("sys").platform != "win32":
            self._finish("Automatic joining is available on Windows only.")
            return

        try:
            from PIL import ImageGrab
            from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

            # Russian OCR runs locally. RapidOCR fetches its model files the first time.
            ocr = RapidOCR(
                params={
                    "Det.engine_type": EngineType.ONNXRUNTIME,
                    "Det.lang_type": LangDet.MULTI,
                    "Det.model_type": ModelType.MOBILE,
                    "Det.ocr_version": OCRVersion.PPOCRV5,
                    "Rec.engine_type": EngineType.ONNXRUNTIME,
                    "Rec.lang_type": LangRec.CYRILLIC,
                    "Rec.model_type": ModelType.MOBILE,
                    "Rec.ocr_version": OCRVersion.PPOCRV5,
                }
            )
        except Exception as exc:
            self._finish(f"OCR setup failed: {exc}")
            return

        window = _find_game_window(self.process_names)
        if window is None:
            self._finish("ProTanki window was not found.")
            return

        user32 = ctypes.windll.user32
        # SW_RESTORE also unmaximizes a maximized client and can move it back
        # to its stale normal-window bounds. Restore only when minimized.
        if user32.IsIconic(window.handle):
            user32.ShowWindow(window.handle, 9)  # SW_RESTORE
        user32.SetForegroundWindow(window.handle)
        time.sleep(0.3)

        last_attempt = 0.0
        target_confirmed = False
        command_sent = False
        pending_join = False
        self._say("Looking for the selected battle\u2026")

        while not self._stop.is_set():
            current_window = _find_game_window(self.process_names)
            if current_window is None:
                self._finish("ProTanki was closed; monitoring stopped.")
                return

            foreground = user32.GetForegroundWindow()
            if foreground != current_window.handle:
                self._say("Monitoring paused. Bring ProTanki to the front to continue.")
                self._stop.wait(POLL_SECONDS)
                continue

            try:
                image = ImageGrab.grab(
                    bbox=(
                        current_window.left,
                        current_window.top,
                        current_window.right,
                        current_window.bottom,
                    ),
                    all_screens=True,
                )
                state = _read_battle_screen(ocr, image, self.battle_id)
            except Exception as exc:
                self._finish(f"Could not read the game screen: {exc}")
                return

            if not state.selected:
                if target_confirmed:
                    self._finish("The battle screen changed. Check ProTanki to confirm entry.")
                    return
                if not command_sent:
                    if not _chat_input_is_empty(ocr, image):
                        self._finish("Clear the ProTanki chat input, then start monitoring again.")
                        return
                    _send_showbattle(current_window, self.battle_id)
                    command_sent = True
                    self._say("Searching for the saved battle in ProTanki\u2026")
                else:
                    self._say("Waiting for ProTanki to open the saved battle\u2026")
                self._stop.wait(POLL_SECONDS)
                continue

            target_confirmed = True
            if pending_join and (not state.selected or not state.join_buttons_visible):
                self._finish("ProTanki accepted the join. Check the game screen.")
                return

            free = state.red_free + state.blue_free
            if free == 0:
                self._say("Battle found; waiting for an empty player slot\u2026")
                self._stop.wait(POLL_SECONDS)
                continue

            if not state.join_buttons_visible:
                self._say("A slot is free. Waiting for the game to show its Join buttons\u2026")
                self._stop.wait(POLL_SECONDS)
                continue

            now = time.monotonic()
            if now - last_attempt < RETRY_SECONDS:
                self._stop.wait(POLL_SECONDS)
                continue

            # Prefer the team with more free slots; try red first if tied.
            button_x = 0.715 if state.red_free >= state.blue_free and state.red_free else 0.955
            _click_ratio(current_window, button_x, 0.96)
            last_attempt = now
            pending_join = True
            self._say("A free slot appeared. Trying to join\u2026")
            self._stop.wait(RETRY_SECONDS)

        self.on_finished("Automatic joining stopped.")


def _find_game_window(process_names: list[str]) -> GameWindow | None:
    if __import__("sys").platform != "win32":
        return None

    wanted = {name.casefold().removesuffix(".exe") for name in process_names if name.strip()}
    pids: set[int] = set()
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if (process.info.get("name") or "").casefold().removesuffix(".exe") in wanted:
                pids.add(int(process.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if not pids:
        return None

    user32 = ctypes.windll.user32
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    candidates: list[GameWindow] = []
    rect = wintypes.RECT()

    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids or not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width >= 800 and height >= 600:
            candidates.append(GameWindow(hwnd, pid.value, rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return max(candidates, key=lambda candidate: candidate.width * candidate.height, default=None)


def _read_battle_screen(ocr, image, target_id: str) -> BattleScreen:
    width, height = image.size
    # The AIR client places battle information and team seats in the right panel.
    left = int(width * 0.665)
    right = int(width * 0.998)
    top = int(height * 0.405)
    # Include the bottom row of the battle panel where the team Join buttons sit.
    bottom = int(height * 0.999)
    panel = image.crop((left, top, right, bottom))
    result = ocr(panel)
    texts = result.txts or []
    boxes = result.boxes
    selected = False
    red_free = 0
    blue_free = 0
    join_visible = False

    for index, text in enumerate(texts):
        normalized = re.sub(r"[^0-9a-f]", "", str(text).casefold())
        if target_id in normalized:
            selected = True

        plain = "".join(character for character in str(text).casefold() if character.isalnum())
        box = boxes[index] if boxes is not None and index < len(boxes) else None
        center_x = center_y = 0.0
        if box is not None:
            center_x = sum(float(point[0]) for point in box) / 4 + left
            center_y = sum(float(point[1]) for point in box) / 4 + top

        relative_y = center_y / height if height else 0
        if "\u0438\u0433\u0440\u0430\u0442\u044c" in plain and relative_y > 0.87:
            join_visible = True

        # Only count the repeated seat labels, not chat or the copied battle link.
        if ("\u043f\u0443\u0441\u0442\u043e" in plain or "\u043f\u0443\u0441\u0442o" in plain or "\u043f\u0443\u0441\u04420" in plain) and 0.47 <= relative_y <= 0.88:
            if center_x < width * 0.832:
                red_free += 1
            else:
                blue_free += 1

    return BattleScreen(selected, red_free, blue_free, join_visible)


def _chat_input_is_empty(ocr, image) -> bool:
    width, height = image.size
    chat = image.crop((int(width * 0.005), int(height * 0.945), int(width * 0.27), int(height * 0.99)))
    result = ocr(chat)
    return not any(str(text).strip() for text in (result.txts or []))


def _send_showbattle(window: GameWindow, target_id: str) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
    user32.SetCursorPos(window.left + int(window.width * 0.14), window.top + int(window.height * 0.965))
    user32.mouse_event(0x0002, 0, 0, 0, 0)
    user32.mouse_event(0x0004, 0, 0, 0, 0)

    keybd_event = user32.keybd_event
    unicode_flag = 0x0004
    key_up_flag = 0x0002
    command = f"/showbattle #/battle/{target_id}"
    for character in command:
        scan_code = ord(character)
        keybd_event(0, scan_code, unicode_flag, 0)
        keybd_event(0, scan_code, unicode_flag | key_up_flag, 0)
    keybd_event(0x0D, 0, 0, 0)
    keybd_event(0x0D, 0, key_up_flag, 0)


def _click_ratio(window: GameWindow, x_ratio: float, y_ratio: float) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    x = window.left + int(window.width * x_ratio)
    y = window.top + int(window.height * y_ratio)
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
