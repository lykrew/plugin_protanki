from __future__ import annotations

import psutil


def _normalize(name: str) -> str:
    return name.casefold().removesuffix(".exe")


def find_game_process(process_names: list[str]) -> tuple[str, str] | None:
    wanted = {_normalize(name) for name in process_names if name.strip()}
    if not wanted:
        return None
    for process in psutil.process_iter(["name", "exe"]):
        try:
            current = process.info.get("name") or ""
            if _normalize(current) in wanted:
                return current, process.info.get("exe") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return None


def is_game_running(process_names: list[str]) -> bool:
    return find_game_process(process_names) is not None
