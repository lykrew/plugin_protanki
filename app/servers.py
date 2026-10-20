from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.paths import MODES, SERVERS_PATH, SETTINGS_PATH

DEFAULT_PROCESS_NAMES = ["ProTanki.exe", "ProTanki", "protanki.exe"]


def _empty_catalog() -> dict[str, list[dict[str, str]]]:
    return {mode: [] for mode in MODES}


def load_catalog() -> dict[str, list[dict[str, str]]]:
    catalog = _empty_catalog()
    if not SERVERS_PATH.exists():
        return catalog
    raw = json.loads(SERVERS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return catalog
    for mode in MODES:
        items = raw.get(mode, [])
        if not isinstance(items, list):
            continue
        cleaned: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            cleaned.append({"name": name, "note": str(item.get("note", "")).strip()})
        catalog[mode] = cleaned
    return catalog


def save_catalog(catalog: dict[str, list[dict[str, str]]]) -> None:
    SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {mode: deepcopy(catalog.get(mode, [])) for mode in MODES}
    SERVERS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {
        "last_mode": "CP",
        "last_servers": {mode: "" for mode in MODES},
        "process_names": list(DEFAULT_PROCESS_NAMES),
        "bell_enabled": True,
    }
    if not SETTINGS_PATH.exists():
        return settings
    raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return settings
    mode = str(raw.get("last_mode", "CP")).strip().upper()
    settings["last_mode"] = mode if mode in MODES else "CP"
    last_servers = raw.get("last_servers")
    if isinstance(last_servers, dict):
        for key in MODES:
            settings["last_servers"][key] = str(last_servers.get(key, "")).strip()
    legacy = str(raw.get("last_server", "")).strip()
    if legacy and not settings["last_servers"].get(settings["last_mode"]):
        settings["last_servers"][settings["last_mode"]] = legacy
    names = raw.get("process_names", DEFAULT_PROCESS_NAMES)
    if isinstance(names, list) and names:
        settings["process_names"] = [str(name).strip() for name in names if str(name).strip()]
    settings["bell_enabled"] = bool(raw.get("bell_enabled", True))
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    last_servers = settings.get("last_servers") or {}
    last_mode = settings.get("last_mode", "CP")
    payload = {
        "last_mode": last_mode,
        "last_server": last_servers.get(last_mode, ""),
        "last_servers": {mode: last_servers.get(mode, "") for mode in MODES},
        "process_names": settings.get("process_names", list(DEFAULT_PROCESS_NAMES)),
        "bell_enabled": bool(settings.get("bell_enabled", True)),
    }
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def add_server(catalog: dict[str, list[dict[str, str]]], mode: str, name: str, note: str) -> None:
    name = name.strip()
    if mode not in MODES or not name:
        return
    catalog.setdefault(mode, [])
    for item in catalog[mode]:
        if item["name"].casefold() == name.casefold():
            item["note"] = note.strip()
            return
    catalog[mode].append({"name": name, "note": note.strip()})


def remove_server(catalog: dict[str, list[dict[str, str]]], mode: str, name: str) -> None:
    if mode not in MODES:
        return
    catalog[mode] = [item for item in catalog.get(mode, []) if item["name"] != name]
