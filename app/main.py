from __future__ import annotations

import customtkinter as ctk
from tkinter import messagebox

from app.game_automation import BattleJoinWatcher
from app.game_watch import find_game_process
from app.notify import play_join_bell
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
BG = "#090E18"
SURFACE = "#111A29"
SURFACE_2 = "#172235"
LINE = "#26364C"
TEXT = "#F4F8FF"
MUTED = "#91A0B5"
CYAN = "#76E4F7"
CYAN_DARK = "#153C50"


class CompanionApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ProTanki · Companion")
        self.geometry("1140x850")
        self.minsize(960, 740)
        self.state("zoomed")
        self.configure(fg_color=BG)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.catalog = load_catalog()
        self.settings = load_settings()
        self.current_mode = self.settings.get("last_mode", "CP")
        if self.current_mode not in MODES:
            self.current_mode = "CP"

        self.status_var = ctk.StringVar(value="Проверяем игру…")
        self.selected_var = ctk.StringVar(value="")
        self.auto_join_status_var = ctk.StringVar(value="Автоподключение выключено")
        self.bell_var = ctk.BooleanVar(value=bool(self.settings.get("bell_enabled", True)))
        self.name_var = ctk.StringVar()
        self.note_var = ctk.StringVar()
        self.battle_link_var = ctk.StringVar()
        self.auto_joiner: BattleJoinWatcher | None = None
        self._build()
        self._refresh_list()
        self._restore_selection()
        self._poll_game()
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=26, pady=24)

        top = ctk.CTkFrame(
            shell, fg_color=SURFACE, corner_radius=26, border_width=1, border_color=LINE
        )
        top.pack(fill="x", pady=(0, 18))
        brand = ctk.CTkFrame(top, fg_color="transparent")
        brand.pack(side="left", padx=22, pady=18)
        ctk.CTkLabel(
            brand, text="◈", text_color=CYAN, font=ctk.CTkFont(size=27, weight="bold")
        ).pack(side="left", padx=(0, 12))
        title_box = ctk.CTkFrame(brand, fg_color="transparent")
        title_box.pack(side="left")
        ctk.CTkLabel(
            title_box, text="PROTANKI", text_color=TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text="BATTLE COMPANION", text_color=MUTED,
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", pady=(1, 0))

        self.mode_buttons = ctk.CTkSegmentedButton(
            top,
            values=list(MODES),
            command=self._set_mode,
            height=38,
            corner_radius=14,
            selected_color=CYAN_DARK,
            selected_hover_color="#1E5268",
            unselected_color="#0D1522",
            unselected_hover_color="#1B293C",
            text_color=TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.mode_buttons.pack(side="right", padx=22, pady=20)
        self.mode_buttons.set(self.current_mode)

        status_bar = ctk.CTkFrame(
            shell, fg_color="#10202C", corner_radius=16, border_width=1, border_color="#244454"
        )
        status_bar.pack(fill="x", pady=(0, 18))
        ctk.CTkLabel(
            status_bar, text="●", text_color=CYAN,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(16, 9), pady=10)
        ctk.CTkLabel(
            status_bar, textvariable=self.status_var, text_color="#C7D4E4",
            font=ctk.CTkFont(size=12),
        ).pack(side="left", pady=10)
        ctk.CTkLabel(
            status_bar, textvariable=self.auto_join_status_var, text_color=CYAN,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="right", padx=16, pady=10)

        content = ctk.CTkFrame(shell, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=6, uniform="panels")
        content.grid_columnconfigure(1, weight=5, uniform="panels")
        content.grid_rowconfigure(0, weight=1)

        list_card = ctk.CTkFrame(
            content, fg_color=SURFACE, corner_radius=26, border_width=1, border_color=LINE
        )
        list_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(
            list_card, text="Сохранённые бои", text_color=TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(20, 2))
        ctk.CTkLabel(
            list_card, text="Выберите бой, чтобы включить наблюдение",
            text_color=MUTED, font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=22, pady=(0, 14))
        self.server_list = ctk.CTkScrollableFrame(
            list_card, fg_color="transparent", corner_radius=18,
        )
        self.server_list.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.server_rows: list[ctk.CTkBaseClass] = []

        form = ctk.CTkFrame(
            content, fg_color=SURFACE, corner_radius=26, border_width=1, border_color=LINE
        )
        form.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        ctk.CTkLabel(
            form, text="Автопилот", text_color=TEXT,
            font=ctk.CTkFont(size=20, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(20, 2))
        ctk.CTkLabel(
            form,
            text="Слежу за свободными местами и подключаю через клиент.",
            text_color=MUTED, wraplength=380, justify="left",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=22, pady=(0, 18))

        ctk.CTkLabel(
            form, text="ССЫЛКА НА БОЙ", text_color="#AFC0D3",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(0, 7))
        link_row = ctk.CTkFrame(form, fg_color="transparent")
        link_row.pack(fill="x", padx=22)
        self.link_entry = ctk.CTkEntry(
            link_row,
            textvariable=self.battle_link_var,
            placeholder_text="#/battle/…",
            height=44,
            corner_radius=14,
            fg_color="#0B1320",
            border_color=LINE,
            text_color=TEXT,
            placeholder_text_color="#64748B",
            font=ctk.CTkFont(size=13),
        )
        self.link_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._bind_paste(self.link_entry)
        ctk.CTkButton(
            link_row, text="Вставить", width=84, height=44, corner_radius=14,
            fg_color="#213149", hover_color="#2B405C", text_color=TEXT,
            command=self._paste_battle_link,
        ).pack(side="right")

        ctk.CTkLabel(
            form, text="Название", text_color="#AFC0D3",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(16, 7))
        self.name_entry = ctk.CTkEntry(
            form, textvariable=self.name_var, placeholder_text="Например, Фарм",
            height=42, corner_radius=14, fg_color="#0B1320", border_color=LINE,
            text_color=TEXT, placeholder_text_color="#64748B",
        )
        self.name_entry.pack(fill="x", padx=22)
        self._bind_paste(self.name_entry)

        ctk.CTkLabel(
            form, text="Заметка", text_color="#AFC0D3",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(13, 7))
        self.note_entry = ctk.CTkEntry(
            form, textvariable=self.note_var, placeholder_text="Необязательно",
            height=42, corner_radius=14, fg_color="#0B1320", border_color=LINE,
            text_color=TEXT, placeholder_text_color="#64748B",
        )
        self.note_entry.pack(fill="x", padx=22)
        self._bind_paste(self.note_entry)

        ctk.CTkButton(
            form, text="Сохранить бой", command=self._save_server,
            height=42, corner_radius=14, fg_color="#25364D", hover_color="#304763",
            text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(fill="x", padx=22, pady=(16, 7))
        ctk.CTkButton(
            form, text="Удалить выбранный", command=self._delete_selected,
            height=38, corner_radius=14, fg_color="transparent", hover_color="#382632",
            border_width=1, border_color="#61404D", text_color="#EBA8B5",
        ).pack(fill="x", padx=22, pady=(0, 12))
        self.auto_join_button = ctk.CTkButton(
            form,
            text="Начать автоподключение",
            command=self._toggle_auto_join,
            height=50,
            corner_radius=16,
            fg_color=CYAN,
            hover_color="#9BF0FF",
            text_color="#07131D",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.auto_join_button.pack(fill="x", padx=22, pady=(4, 5))
        ctk.CTkLabel(
            form, textvariable=self.selected_var, text_color=MUTED,
            font=ctk.CTkFont(size=11), wraplength=360, justify="left",
        ).pack(anchor="w", padx=22, pady=(3, 3))
        ctk.CTkCheckBox(
            form, text="Звонок при выборе боя", variable=self.bell_var,
            command=self._toggle_bell, text_color="#B7C5D7",
            fg_color=CYAN_DARK, hover_color="#28627B", border_color="#52657D",
        ).pack(anchor="w", padx=22, pady=(8, 4))
        ctk.CTkButton(
            form, text="Проверить звук", command=self._test_bell,
            height=34, corner_radius=12, fg_color="transparent",
            hover_color="#1C2A3D", text_color=MUTED,
        ).pack(anchor="w", padx=16, pady=(0, 12))

    def _bind_paste(self, entry: ctk.CTkEntry) -> None:
        entry.bind("<Control-v>", lambda event, target=entry: self._paste_entry(target))
        entry.bind("<Control-V>", lambda event, target=entry: self._paste_entry(target))
        entry._entry.bind("<Control-v>", lambda event, target=entry: self._paste_entry(target))
        entry._entry.bind("<Control-V>", lambda event, target=entry: self._paste_entry(target))

    def _paste_entry(self, entry: ctk.CTkEntry) -> str:
        try:
            value = self.clipboard_get()
        except Exception:
            self.auto_join_status_var.set("Буфер обмена пуст")
            return "break"
        try:
            entry.delete("sel.first", "sel.last")
        except Exception:
            pass
        entry.insert("insert", value.strip())
        return "break"

    def _paste_battle_link(self) -> None:
        self.link_entry.focus_set()
        self._paste_entry(self.link_entry)

    def _set_mode(self, mode: str) -> None:
        self.current_mode = mode
        self.settings["last_mode"] = mode
        save_settings(self.settings)
        self._refresh_list()
        self._restore_selection()

    def _refresh_list(self) -> None:
        for row in self.server_rows:
            row.destroy()
        self.server_rows.clear()
        items = self.catalog.get(self.current_mode, [])
        if not items:
            empty = ctk.CTkLabel(
                self.server_list, text="В этом режиме пока нет сохранённых боёв.",
                text_color=MUTED,
            )
            empty.pack(anchor="w", padx=10, pady=14)
            self.server_rows.append(empty)
            return

        selected = self._selected_name()
        for item in items:
            name = item["name"]
            link = item.get("battle_link", "")
            note = item.get("note", "")
            card = ctk.CTkFrame(
                self.server_list,
                fg_color="#1B2A3C" if name == selected else SURFACE_2,
                corner_radius=18,
                border_width=1,
                border_color="#4A8192" if name == selected else "#202D41",
            )
            card.pack(fill="x", padx=4, pady=5)
            self.server_rows.append(card)
            text_box = ctk.CTkFrame(card, fg_color="transparent")
            text_box.pack(fill="x", padx=15, pady=11)
            ctk.CTkLabel(
                text_box, text=name, text_color=TEXT, anchor="w",
                font=ctk.CTkFont(size=14, weight="bold"),
            ).pack(anchor="w", fill="x")
            if link and link != name:
                ctk.CTkLabel(
                    text_box, text=link, text_color=CYAN, anchor="w",
                    font=ctk.CTkFont(size=11),
                ).pack(anchor="w", fill="x", pady=(3, 0))
            if note:
                ctk.CTkLabel(
                    text_box, text=note, text_color=MUTED, anchor="w",
                    font=ctk.CTkFont(size=11),
                ).pack(anchor="w", fill="x", pady=(3, 0))
            for widget in (card, text_box, *text_box.winfo_children()):
                widget.bind("<Button-1>", lambda event, value=name: self._select_server(value, ring=True))

    def _selected_name(self) -> str:
        return str(self.settings.setdefault("last_servers", {}).get(self.current_mode, "") or "")

    def _select_server(self, name: str, ring: bool = False) -> None:
        self.settings["last_mode"] = self.current_mode
        self.settings.setdefault("last_servers", {})[self.current_mode] = name
        save_settings(self.settings)
        item = next(
            (entry for entry in self.catalog.get(self.current_mode, []) if entry["name"] == name),
            None,
        )
        self.selected_var.set(f"{self.current_mode} · {name}")
        if item:
            self.name_var.set(item["name"])
            self.note_var.set(item.get("note", ""))
            self.battle_link_var.set(item.get("battle_link", ""))
        self._refresh_list()
        if ring:
            self._maybe_ring()

    def _toggle_bell(self) -> None:
        self.settings["bell_enabled"] = bool(self.bell_var.get())
        save_settings(self.settings)

    def _test_bell(self) -> None:
        play_join_bell()

    def _maybe_ring(self) -> None:
        if self.bell_var.get():
            play_join_bell()

    def _restore_selection(self) -> None:
        last = self._selected_name()
        item = next(
            (entry for entry in self.catalog.get(self.current_mode, []) if entry["name"] == last),
            None,
        )
        if item:
            self.selected_var.set(f"{self.current_mode} · {last}")
            self.name_var.set(item["name"])
            self.note_var.set(item.get("note", ""))
            self.battle_link_var.set(item.get("battle_link", ""))
        else:
            self.selected_var.set("Бой не выбран")
            self.name_var.set("")
            self.note_var.set("")
            self.battle_link_var.set("")
            if last:
                self.settings.setdefault("last_servers", {})[self.current_mode] = ""
                save_settings(self.settings)

    def _save_server(self) -> None:
        name = self.name_var.get().strip()
        note = self.note_var.get().strip()
        battle_link = self.battle_link_var.get().strip()
        if not name and battle_link:
            name = battle_link
        if not name:
            messagebox.showerror("ProTanki", "Вставьте ссылку на бой или укажите название.")
            return
        add_server(self.catalog, self.current_mode, name, note, battle_link)
        save_catalog(self.catalog)
        self._select_server(name, ring=True)

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

    def _toggle_auto_join(self) -> None:
        if self.auto_joiner and self.auto_joiner.running:
            self.auto_joiner.stop()
            self.auto_join_button.configure(text="Начать автоподключение")
            self.auto_join_status_var.set("Автоподключение остановлено")
            return
        link = self.battle_link_var.get().strip()
        if not link:
            messagebox.showerror("ProTanki", "Выберите бой или вставьте ссылку на него.")
            return
        self.auto_joiner = BattleJoinWatcher(
            link,
            list(self.settings.get("process_names") or []),
            on_status=lambda text: self.after(0, self.auto_join_status_var.set, text),
            on_finished=self._auto_join_finished,
        )
        try:
            self.auto_joiner.start()
        except ValueError as exc:
            messagebox.showerror("ProTanki", str(exc))
            return
        self.auto_join_button.configure(text="Остановить автоподключение")

    def _auto_join_finished(self, text: str) -> None:
        try:
            def apply_result() -> None:
                self.auto_join_status_var.set(text)
                self.auto_join_button.configure(text="Начать автоподключение")

            self.after(0, apply_result)
        except RuntimeError:
            pass

    def _close(self) -> None:
        if self.auto_joiner:
            self.auto_joiner.stop()
        self.destroy()

    def _poll_game(self) -> None:
        process = find_game_process(list(self.settings.get("process_names") or []))
        if process:
            name, path = process
            self.status_var.set(f"Игра найдена: {name}" + (f" · {path}" if path else ""))
        else:
            self.status_var.set("Игра не найдена")
        self.after(POLL_MS, self._poll_game)


def main() -> None:
    CompanionApp().mainloop()


if __name__ == "__main__":
    main()
