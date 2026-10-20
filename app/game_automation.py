from __future__ import annotations

import ctypes
import re
import threading
import time
from dataclasses import dataclass
from ctypes import wintypes
from typing import Callable

import psutil


POLL_SECONDS = 0.12
RETRY_SECONDS = 0.12
MULTI_TARGET_DWELL_SECONDS = 0.12
ROW_SELECT_SETTLE_SECONDS = 0.18

_ocr_lock = threading.Lock()
_ocr_engine = None


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        with _ocr_lock:
            if _ocr_engine is None:
                from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR

                engine = RapidOCR(
                    params={
                        # All relevant labels are horizontal; skipping orientation
                        # classification avoids one extra model pass per frame.
                        "Global.use_cls": False,
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
                # Run a tiny image once so the first live screenshot avoids runtime setup.
                from PIL import Image
                engine(Image.new("RGB", (96, 48), "black"))
                _ocr_engine = engine
    return _ocr_engine


def preload_ocr(on_ready: Callable[[str], None] | None = None) -> None:
    """Load and warm OCR before enabling auto-join."""
    def load() -> None:
        error = ""
        try:
            _get_ocr_engine()
        except Exception as exc:
            error = str(exc)
        if on_ready is not None:
            on_ready(error)

    threading.Thread(target=load, daemon=True, name="protanki-ocr-preload").start()


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
        link: str | list[str],
        process_names: list[str],
        on_status: Callable[[str], None],
        on_finished: Callable[[str], None],
    ) -> None:
        links = [link] if isinstance(link, str) else link
        self.battle_ids = list(dict.fromkeys(
            battle_id_from_link(value) for value in links if battle_id_from_link(value)
        ))
        self.battle_id = self.battle_ids[0] if self.battle_ids else ""
        self.process_names = process_names
        self.on_status = on_status
        self.on_finished = on_finished
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.battle_ids:
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
            ocr = _get_ocr_engine()
        except Exception as exc:
            self._finish(f"OCR setup failed: {exc}")
            return

        window = _find_game_window(self.process_names)
        if window is None:
            self._finish("ProTanki window was not found.")
            return

        user32 = ctypes.windll.user32
        # HWND is pointer-sized on 64-bit Windows; ctypes defaults undeclared
        # return values to 32-bit int, which can truncate the active window.
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.argtypes = []
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        # SW_RESTORE also unmaximizes a maximized client and can move it back
        # to its stale normal-window bounds. Restore only when minimized.
        if user32.IsIconic(window.handle):
            user32.ShowWindow(window.handle, 9)  # SW_RESTORE
        if int(user32.GetForegroundWindow() or 0) != window.handle:
            user32.SetForegroundWindow(window.handle)
            focus_deadline = time.monotonic() + 0.18
            while int(user32.GetForegroundWindow() or 0) != window.handle and time.monotonic() < focus_deadline:
                time.sleep(0.01)

        last_attempt = 0.0
        target_confirmed = False
        pending_join = False
        next_full_check = 0.0
        target_index = 0
        target_confirmed_at: float | None = None
        list_row_centers: list[int] | None = None
        list_row_index = 0
        last_row_click_at = 0.0
        target_rows: dict[str, int] = {}
        last_clicked_row_y: int | None = None
        cached_row_attempts = 0

        def advance_target() -> bool:
            nonlocal target_index, target_confirmed, pending_join, next_full_check
            nonlocal target_confirmed_at, list_row_centers, list_row_index, last_row_click_at
            nonlocal last_clicked_row_y, cached_row_attempts
            if len(self.battle_ids) < 2:
                return False
            target_index = (target_index + 1) % len(self.battle_ids)
            self.battle_id = self.battle_ids[target_index]
            target_confirmed = False
            pending_join = False
            next_full_check = 0.0
            target_confirmed_at = None
            list_row_centers = None
            list_row_index = 0
            last_row_click_at = 0.0
            last_clicked_row_y = None
            cached_row_attempts = 0
            self._say(f"Checking battle {target_index + 1}/{len(self.battle_ids)}...")
            return True

        self._say(f"Checking battle 1/{len(self.battle_ids)}…")

        while not self._stop.is_set():
            current_window = _find_game_window(self.process_names)
            if current_window is None:
                self._finish("ProTanki was closed; monitoring stopped.")
                return

            foreground = user32.GetForegroundWindow()
            foreground_pid = wintypes.DWORD()
            if foreground:
                user32.GetWindowThreadProcessId(foreground, ctypes.byref(foreground_pid))
            if foreground_pid.value != current_window.pid:
                self._say("Monitoring paused. Bring ProTanki to the front to continue.")
                self._stop.wait(POLL_SECONDS)
                continue

            try:
                screen_width = current_window.width
                screen_height = current_window.height
                # Include the left edge of the battle-link field in the info
                # panel so the selected battle ID is visible to OCR.
                panel_left = int(screen_width * 0.615)
                panel_top = int(screen_height * 0.405)
                panel_right = int(screen_width * 0.998)
                panel_bottom = int(screen_height * 0.999)
                image = ImageGrab.grab(
                    bbox=(
                        current_window.left + panel_left,
                        current_window.top + panel_top,
                        current_window.left + panel_right,
                        current_window.top + panel_bottom,
                    ),
                    all_screens=True,
                )
                full_check = not target_confirmed or time.monotonic() >= next_full_check
                if full_check:
                    state = _read_battle_screen(
                        ocr,
                        image,
                        self.battle_id,
                        screen_width=screen_width,
                        screen_height=screen_height,
                        panel_offset=(panel_left, panel_top),
                    )
                    next_full_check = time.monotonic() + 1.5
                    full_check = True
                else:
                    seats_top = max(0, int(screen_height * 0.47) - panel_top)
                    seats_bottom = min(image.height, int(screen_height * 0.88) - panel_top)
                    seats_image = image.crop((0, seats_top, image.width, seats_bottom))
                    red_free, blue_free = _read_free_seats(
                        ocr,
                        seats_image,
                        screen_width=screen_width,
                        screen_height=screen_height,
                        panel_offset=(panel_left, panel_top + seats_top),
                    )
                    state = BattleScreen(True, red_free, blue_free, True)
                if pending_join and state.red_free + state.blue_free == 0:
                    # Only pay for a full-screen OCR pass after the attempted slot
                    # disappears; when it is still free, retry from the fast scan.
                    state = _read_battle_screen(
                        ocr,
                        image,
                        self.battle_id,
                        screen_width=screen_width,
                        screen_height=screen_height,
                        panel_offset=(panel_left, panel_top),
                    )
                    next_full_check = time.monotonic() + 1.5
                    full_check = True
            except Exception as exc:
                self._finish(f"Could not read the game screen: {exc}")
                return

            if pending_join and full_check and (not state.selected or not state.join_buttons_visible):
                self._finish("Вход в бой выполнен.")
                return
            if pending_join and full_check:
                # The click did not change the battle view. Resume the lightweight
                # seat-only scan and retry promptly instead of waiting several seconds.
                pending_join = False
                # The click may have missed while the seat is still open.
                # Stay on this battle and retry it; rotating here used to
                # abandon the only target that had an available place.

            if not state.selected:
                if target_confirmed:
                    if advance_target():
                        continue
                    self._finish("The selected battle changed unexpectedly. Check ProTanki.")
                    return

                now = time.monotonic()
                if self.battle_id in target_rows:
                    cached_y = target_rows[self.battle_id]
                    if last_clicked_row_y != cached_y:
                        _click_ratio(current_window, 0.50, cached_y / screen_height)
                        last_clicked_row_y = cached_y
                        last_row_click_at = now
                        cached_row_attempts = 1
                        self._say(f"Rechecking battle {target_index + 1}/{len(self.battle_ids)}...")
                    elif now - last_row_click_at >= 0.45:
                        if cached_row_attempts >= 2:
                            target_rows.pop(self.battle_id, None)
                            list_row_centers = None
                            list_row_index = 0
                            last_clicked_row_y = None
                            cached_row_attempts = 0
                        else:
                            _click_ratio(current_window, 0.50, cached_y / screen_height)
                            last_row_click_at = now
                            cached_row_attempts += 1
                else:
                    if list_row_centers is None:
                        list_image = ImageGrab.grab(
                            bbox=(
                                current_window.left + int(screen_width * 0.335),
                                current_window.top + int(screen_height * 0.11),
                                current_window.left + int(screen_width * 0.66),
                                current_window.top + int(screen_height * 0.56),
                            ),
                            all_screens=True,
                        )
                        list_row_centers = _battle_list_row_centers(ocr, list_image, screen_height)
                        list_row_index = 0
                    if list_row_index < len(list_row_centers):
                        if now - last_row_click_at >= ROW_SELECT_SETTLE_SECONDS:
                            row_y = list_row_centers[list_row_index]
                            _click_ratio(current_window, 0.50, row_y / screen_height)
                            list_row_index += 1
                            last_clicked_row_y = row_y
                            last_row_click_at = now
                            self._say(f"Selecting saved battle {target_index + 1}/{len(self.battle_ids)}...")
                    elif not advance_target():
                        self._finish("Saved battle was not found in the visible battle list.")
                        return
                self._stop.wait(POLL_SECONDS)
                continue

            if last_clicked_row_y is not None:
                target_rows.setdefault(self.battle_id, last_clicked_row_y)
            cached_row_attempts = 0
            if not target_confirmed:
                target_confirmed_at = time.monotonic()
            target_confirmed = True
            if not pending_join:
                # While idling, the cropped seat scan is sufficient. Schedule a
                # full-screen confirmation only shortly after an actual click.
                next_full_check = float("inf")
            free = state.red_free + state.blue_free
            if free == 0:
                if (
                    len(self.battle_ids) > 1
                    and target_confirmed_at is not None
                    and time.monotonic() - target_confirmed_at >= MULTI_TARGET_DWELL_SECONDS
                ):
                    advance_target()
                    continue
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

            # The game places the red and blue Join buttons at the bottom of
            # the roster panels. Click their centers; edge clicks can animate
            # the button without activating its hit target.
            button_x = 0.716 if state.red_free >= state.blue_free and state.red_free else 0.953
            _click_ratio(current_window, button_x, 0.956)
            last_attempt = now
            pending_join = True
            next_full_check = now + RETRY_SECONDS
            self._say("A free slot appeared. Trying to join\u2026")
            # Recheck promptly; if the click did not work, RETRY_SECONDS still
            # prevents repeated clicks while the game processes the request.
            self._stop.wait(POLL_SECONDS)

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


def _read_battle_screen(
    ocr,
    image,
    target_id: str,
    *,
    screen_width: int | None = None,
    screen_height: int | None = None,
    panel_offset: tuple[int, int] = (0, 0),
) -> BattleScreen:
    width = screen_width or image.width
    height = screen_height or image.height
    left, top = panel_offset
    result = ocr(image)
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


def _read_free_seats(
    ocr,
    image,
    *,
    screen_width: int,
    screen_height: int,
    panel_offset: tuple[int, int],
) -> tuple[int, int]:
    """Recognize only the roster area during rapid seat monitoring."""
    result = ocr(image)
    red_free = 0
    blue_free = 0
    left, top = panel_offset
    for index, text in enumerate(result.txts or []):
        plain = "".join(character for character in str(text).casefold() if character.isalnum())
        if not any(marker in plain for marker in ("пусто", "пустo", "пуст0")):
            continue
        boxes = result.boxes
        if boxes is None or index >= len(boxes):
            continue
        box = boxes[index]
        center_x = sum(float(point[0]) for point in box) / 4 + left
        center_y = sum(float(point[1]) for point in box) / 4 + top
        relative_y = center_y / screen_height if screen_height else 0
        if not 0.47 <= relative_y <= 0.88:
            continue
        if center_x < screen_width * 0.832:
            red_free += 1
        else:
            blue_free += 1
    return red_free, blue_free


def _battle_list_row_centers(ocr, image, screen_height: int) -> list[int]:
    """Find row centers in the visible battle list from OCR text boxes."""
    result = ocr(image)
    boxes = result.boxes
    if boxes is None:
        return []
    y_centers: list[float] = []
    for index, text in enumerate(result.txts or []):
        if not str(text).strip() or index >= len(boxes):
            continue
        box = boxes[index]
        x = sum(float(point[0]) for point in box) / 4
        y = sum(float(point[1]) for point in box) / 4
        if image.width * 0.03 <= x <= image.width * 0.97:
            y_centers.append(y)
    if not y_centers:
        return []

    y_centers.sort()
    cluster_gap = max(8, int(image.height * 0.035))
    clusters: list[list[float]] = [[y_centers[0]]]
    for y in y_centers[1:]:
        if y - clusters[-1][-1] <= cluster_gap:
            clusters[-1].append(y)
        else:
            clusters.append([y])

    list_top = int(screen_height * 0.11)
    return [round(list_top + sum(cluster) / len(cluster)) for cluster in clusters]


def _click_ratio(window: GameWindow, x_ratio: float, y_ratio: float) -> None:
    user32 = ctypes.windll.user32
    user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
    x = window.left + int(window.width * x_ratio)
    y = window.top + int(window.height * y_ratio)
    user32.SetCursorPos(x, y)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
