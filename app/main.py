from __future__ import annotations

import customtkinter as ctk

from app.game_watch import is_game_running
from app.paths import MODES
from app.servers import (
    add_server,
    load_catalog,
    load_settings,
    remove_server,
    save_catalog,
    save_settings,
)

POLL_MS = 2000


class CompanionApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ProTanki companion")
        self.geometry("720x480")
        self.minsize(640, 420)

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.catalog = load_catalog()
        self.settings = load_settings()
        self.current_mode = self.settings.get("last_mode", "CP")
        if self.current_mode not in MODES:
            self.current_mode = "CP"

        self.status_var = ctk.StringVar(value="Игра: проверка…")
        self.selected_var = ctk.StringVar(value="")
        self.name_var = ctk.StringVar()
        self.note_var = ctk.StringVar()

        self._build()
        self._refresh_list()
        self._restore_selection()
        self._poll_game()

    def _build(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(16, 8))

        ctk.CTkLabel(header, text="Режим", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w"
        )
        modes = ctk.CTkFrame(header, fg_color="transparent")
        modes.pack(fill="x", pady=(8, 0))
        self.mode_buttons: dict[str, ctk.CTkButton] = {}
        for mode in MODES:
            button = ctk.CTkButton(
                modes,
                text=mode,
                width=80,
                command=lambda value=mode: self._set_mode(value),
            )
            button.pack(side="left", padx=(0, 8))
            self.mode_buttons[mode] = button
        self._paint_mode_buttons()

        ctk.CTkLabel(header, textvariable=self.status_var).pack(anchor="w", pady=(12, 0))
        ctk.CTkLabel(
            header,
            text="Список серверов — твой справочник. В бой заходишь сам в клиенте.",
            text_color="gray70",
        ).pack(anchor="w", pady=(4, 0))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=8)

        self.server_list = ctk.CTkScrollableFrame(body)
        self.server_list.pack(side="left", fill="both", expand=True, padx=(0, 12))
        self.server_rows: list[ctk.CTkBaseClass] = []

        form = ctk.CTkFrame(body)
        form.pack(side="right", fill="y")
        ctk.CTkLabel(form, text="Добавить сервер").pack(anchor="w", padx=12, pady=(12, 4))
        ctk.CTkEntry(form, textvariable=self.name_var, placeholder_text="Название", width=220).pack(
            padx=12, pady=4
        )
        ctk.CTkEntry(form, textvariable=self.note_var, placeholder_text="Заметка", width=220).pack(
            padx=12, pady=4
        )
        ctk.CTkButton(form, text="Сохранить", command=self._save_server).pack(
            fill="x", padx=12, pady=(8, 4)
        )
        ctk.CTkButton(form, text="Удалить выбранный", command=self._delete_selected).pack(
            fill="x", padx=12, pady=4
        )
        ctk.CTkLabel(form, text="Выбран:", text_color="gray70").pack(
            anchor="w", padx=12, pady=(16, 0)
        )
        ctk.CTkLabel(form, textvariable=self.selected_var, wraplength=200, justify="left").pack(
            anchor="w", padx=12, pady=(0, 12)
        )

    def _paint_mode_buttons(self) -> None:
        for mode, button in self.mode_buttons.items():
            if mode == self.current_mode:
                button.configure(fg_color=("#3B8ED0", "#1F6AA5"))
            else:
                button.configure(fg_color=("#565B5E", "#343638"))

    def _set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.settings["last_mode"] = mode
        save_settings(self.settings)
        self._paint_mode_buttons()
        self._refresh_list()
        self._restore_selection()

    def _refresh_list(self) -> None:
        for row in self.server_rows:
            row.destroy()
        self.server_rows.clear()

        items = self.catalog.get(self.current_mode, [])
        if not items:
            empty = ctk.CTkLabel(
                self.server_list,
                text="Для этого режима серверов нет. Добавь справа.",
                text_color="gray70",
            )
            empty.pack(anchor="w", pady=8)
            self.server_rows.append(empty)
            return

        selected = self._selected_name()
        for item in items:
            row = ctk.CTkFrame(self.server_list)
            row.pack(fill="x", pady=4)
            label = f"{item['name']}"
            if item.get("note"):
                label += f"\n{item['note']}"
            is_selected = item["name"] == selected
            button = ctk.CTkButton(
                row,
                text=label,
                anchor="w",
                fg_color=("#1F6AA5", "#144870") if is_selected else "transparent",
                command=lambda name=item["name"]: self._select_server(name),
            )
            button.pack(fill="x", padx=4, pady=4)
            self.server_rows.append(row)

    def _selected_name(self) -> str:
        last_servers = self.settings.setdefault("last_servers", {})
        return str(last_servers.get(self.current_mode, "") or "")

    def _select_server(self, name: str) -> None:
        self.settings["last_mode"] = self.current_mode
        self.settings.setdefault("last_servers", {})[self.current_mode] = name
        save_settings(self.settings)
        self.selected_var.set(f"{self.current_mode} · {name}")
        self._refresh_list()

    def _restore_selection(self) -> None:
        last = self._selected_name()
        names = [item["name"] for item in self.catalog.get(self.current_mode, [])]
        if last in names:
            self.selected_var.set(f"{self.current_mode} · {last}")
        else:
            self.selected_var.set("не выбран")
            if last:
                self.settings.setdefault("last_servers", {})[self.current_mode] = ""
                save_settings(self.settings)

    def _save_server(self) -> None:
        name = self.name_var.get().strip()
        note = self.note_var.get().strip()
        if not name:
            return
        add_server(self.catalog, self.current_mode, name, note)
        save_catalog(self.catalog)
        self.name_var.set("")
        self.note_var.set("")
        self._select_server(name)

    def _delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        remove_server(self.catalog, self.current_mode, name)
        save_catalog(self.catalog)
        self.settings.setdefault("last_servers", {})[self.current_mode] = ""
        save_settings(self.settings)
        self._refresh_list()
        self._restore_selection()

    def _poll_game(self) -> None:
        names = self.settings.get("process_names") or []
        running = is_game_running(list(names))
        if running:
            self.status_var.set("Игра: запущена")
        else:
            self.status_var.set("Игра: не найдена")
        self.after(POLL_MS, self._poll_game)


def main() -> None:
    app = CompanionApp()
    app.mainloop()


if __name__ == "__main__":
    main()
