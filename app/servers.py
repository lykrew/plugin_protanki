from __future__ import annotations

import json
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
            link = str(item.get("battle_link", "")).strip()
            if not link and name.startswith("#/battle/"):
                link = name
            cleaned.append(
                {
                    "name": name,
                    "note": str(item.get("note", "")).strip(),
                    "battle_link": link,
                }
            )
        catalog[mode] = cleaned
    return catalog


def save_catalog(catalog: dict[str, list[dict[str, str]]]) -> None:
    SERVERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        mode: [
            {
                "name": str(item.get("name", "")).strip(),
                "note": str(item.get("note", "")).strip(),
                "battle_link": str(item.get("battle_link", "")).strip(),
            }
            for item in catalog.get(mode, [])
        ]
        for mode in MODES
    }
    SERVERS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def load_settings() -> dict[str, Any]:
    settings: dict[str, Any] = {
        "last_mode": "CP",
        "last_servers": {mode: "" for mode in MODES},
        "multi_targets": {mode: [] for mode in MODES},
        "multi_target_order": [],
        "process_names": list(DEFAULT_PROCESS_NAMES),
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
    multi_targets = raw.get("multi_targets")
    if isinstance(multi_targets, dict):
        for key in MODES:
            values = multi_targets.get(key, [])
            if isinstance(values, list):
                settings["multi_targets"][key] = list(dict.fromkeys(
                    str(value).strip() for value in values if str(value).strip()
                ))
    order = raw.get("multi_target_order")
    if isinstance(order, list):
        seen: set[tuple[str, str]] = set()
        for entry in order:
            if not isinstance(entry, dict):
                continue
            target_mode = str(entry.get("mode", "")).strip().upper()
            name = str(entry.get("name", "")).strip()
            key = (target_mode, name)
            if target_mode in MODES and name and name in settings["multi_targets"][target_mode] and key not in seen:
                settings["multi_target_order"].append({"mode": target_mode, "name": name})
                seen.add(key)
    for target_mode in MODES:
        for name in settings["multi_targets"][target_mode]:
            key = (target_mode, name)
            if key not in {(entry["mode"], entry["name"]) for entry in settings["multi_target_order"]}:
                settings["multi_target_order"].append({"mode": target_mode, "name": name})
    legacy = str(raw.get("last_server", "")).strip()
    if legacy and not settings["last_servers"].get(settings["last_mode"]):
        settings["last_servers"][settings["last_mode"]] = legacy
    names = raw.get("process_names", DEFAULT_PROCESS_NAMES)
    if isinstance(names, list) and names:
        settings["process_names"] = [str(name).strip() for name in names if str(name).strip()]
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    last_servers = settings.get("last_servers") or {}
    last_mode = settings.get("last_mode", "CP")
    payload = {
        "last_mode": last_mode,
        "last_server": last_servers.get(last_mode, ""),
        "last_servers": {mode: last_servers.get(mode, "") for mode in MODES},
        "multi_targets": {
            mode: list(dict.fromkeys(
                str(name).strip()
                for name in (settings.get("multi_targets", {}).get(mode, []) or [])
                if str(name).strip()
            ))
            for mode in MODES
        },
        "multi_target_order": [
            {"mode": str(entry.get("mode", "")).strip().upper(), "name": str(entry.get("name", "")).strip()}
            for entry in (settings.get("multi_target_order", []) or [])
            if isinstance(entry, dict)
            and str(entry.get("mode", "")).strip().upper() in MODES
            and str(entry.get("name", "")).strip()
            and str(entry.get("name", "")).strip() in settings.get("multi_targets", {}).get(
                str(entry.get("mode", "")).strip().upper(), []
            )
        ],
        "process_names": settings.get("process_names", list(DEFAULT_PROCESS_NAMES)),
    }
    SETTINGS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def add_server(
    catalog: dict[str, list[dict[str, str]]],
    mode: str,
    name: str,
    note: str,
    battle_link: str = "",
) -> None:
    name = name.strip()
    if mode not in MODES or not name:
        return
    catalog.setdefault(mode, [])
    for item in catalog[mode]:
        if item["name"].casefold() == name.casefold():
            item["note"] = note.strip()
            item["battle_link"] = battle_link.strip()
            return
    catalog[mode].append(
        {"name": name, "note": note.strip(), "battle_link": battle_link.strip()}
    )


def remove_server(catalog: dict[str, list[dict[str, str]]], mode: str, name: str) -> None:
    if mode not in MODES:
        return
    catalog[mode] = [item for item in catalog.get(mode, []) if item["name"] != name]
