from __future__ import annotations

import psutil


def _normalize(name: str) -> str:
    return name.casefold().removesuffix(".exe")


def is_game_running(process_names: list[str]) -> bool:
    wanted = {_normalize(name) for name in process_names if name.strip()}
    if not wanted:
        return False
    for process in psutil.process_iter(["name"]):
        current = process.info.get("name") or ""
        if _normalize(current) in wanted:
            return True
    return False
