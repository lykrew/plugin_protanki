from __future__ import annotations

import customtkinter as ctk
from tkinter import Canvas, messagebox

from app.game_automation import BattleJoinWatcher, battle_id_from_link, preload_ocr
from app.game_watch import find_game_process
from app.notify import play_join_success
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
BG = "#090A0C"
SURFACE = "#17181B"
SURFACE_2 = "#202125"
LINE = "#44464B"
TEXT = "#F5F5F6"
MUTED = "#A5A6AA"
CYAN = "#E5E6E8"  # Neutral highlight kept for existing widget styles.
CYAN_DARK = "#424348"


class CompanionApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ProTanki · Companion")
        self.geometry("1140x850")
        self.minsize(960, 740)
        self.state("zoomed")
        self.configure(fg_color=BG)
        self._backdrop = Canvas(self, highlightthickness=0, bd=0, bg=BG)
        self._backdrop.place(x=0, y=0, relwidth=1, relheight=1)
        # Canvas.lower() addresses canvas items; use Tk's window stacking
        # command to place the backdrop behind the CustomTkinter widgets.
        self.tk.call("lower", self._backdrop._w)
        self._backdrop_job = None
        self._backdrop.bind("<Configure>", self._schedule_backdrop)
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
        self.name_var = ctk.StringVar()
        self.note_var = ctk.StringVar()
        self.battle_link_var = ctk.StringVar()
        self.auto_joiner: BattleJoinWatcher | None = None
        self.multi_target_vars: dict[str, ctk.BooleanVar] = {}
        self._ocr_result: str | None = None
        self._build()
        self.auto_join_button.configure(state="disabled", text="Подготавливаю распознавание…")
        preload_ocr(self._store_ocr_result)
        self.after(50, self._poll_ocr_ready)
        self._refresh_list()
        self._restore_selection()
        self._poll_game()
        self.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _blend(first: str, second: str, amount: float) -> str:
        a = tuple(int(first[index:index + 2], 16) for index in (1, 3, 5))
        b = tuple(int(second[index:index + 2], 16) for index in (1, 3, 5))
        channels = tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
        return "#" + "".join(f"{channel:02X}" for channel in channels)

    def _schedule_backdrop(self, _event=None) -> None:
        if self._backdrop_job is not None:
            try:
                self.after_cancel(self._backdrop_job)
            except Exception:
                pass
        self._backdrop_job = self.after(90, self._paint_backdrop)

    def _paint_backdrop(self) -> None:
        self._backdrop_job = None
        width = max(1, self._backdrop.winfo_width())
        height = max(1, self._backdrop.winfo_height())
        self._backdrop.delete("backdrop")

        bands = 90
        band_height = max(1, height // bands)
        for index in range(bands):
            ratio = index / max(1, bands - 1)
            color = self._blend("#17181B", BG, ratio * 0.75)
            y = index * height / bands
            self._backdrop.create_rectangle(
                0, y, width, y + band_height + 1,
                fill=color, outline="", tags="backdrop",
            )

        # Soft blue and violet light pools give the dark panels a frosted-glass backdrop.
        radius = min(width, height) * 0.64
        for center_x, center_y, tint, opacity in (
            (width * 0.10, height * 0.10, "#74767C", 0.16),
            (width * 0.92, height * 0.82, "#55575D", 0.12),
        ):
            for step in range(30):
                scale = 1.0 - step / 30
                current_radius = radius * scale
                alpha = opacity * (1.0 - scale) ** 1.6
                color = self._blend("#111215", tint, alpha)
                self._backdrop.create_oval(
                    center_x - current_radius, center_y - current_radius,
                    center_x + current_radius, center_y + current_radius,
                    fill=color, outline="", tags="backdrop",
                )

    @staticmethod
    def _add_glass_edge(frame: ctk.CTkFrame) -> None:
        sheen = ctk.CTkFrame(frame, height=1, corner_radius=1, fg_color="#77797F")
        sheen.place(relx=0.045, rely=0.006, relwidth=0.91)

    def _build(self) -> None:
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=26, pady=24)

        top = ctk.CTkFrame(
            shell, fg_color=SURFACE, corner_radius=26, border_width=1, border_color="#56585E"
        )
        top.pack(fill="x", pady=(0, 18))
        self._add_glass_edge(top)
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
            selected_hover_color="#55575D",
            unselected_color="#141518",
            unselected_hover_color="#303136",
            text_color=TEXT,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.mode_buttons.pack(side="right", padx=22, pady=20)
        self.mode_buttons.set(self.current_mode)

        status_bar = ctk.CTkFrame(
            shell, fg_color="#202125", corner_radius=18, border_width=1, border_color="#52545A"
        )
        status_bar.pack(fill="x", pady=(0, 18))
        self._add_glass_edge(status_bar)
        ctk.CTkLabel(
            status_bar, text="●", text_color=CYAN,
            font=ctk.CTkFont(size=12, weight="bold"),
        ).pack(side="left", padx=(16, 9), pady=10)
        ctk.CTkLabel(
            status_bar, textvariable=self.status_var, text_color="#C9CACE",
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
            content, fg_color=SURFACE, corner_radius=26, border_width=1, border_color="#56585E"
        )
        list_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._add_glass_edge(list_card)
        ctk.CTkButton(
            list_card, text="Удалить все", command=self._delete_all_servers,
            width=105, height=32, corner_radius=11, fg_color="transparent",
            hover_color="#303136", border_width=1, border_color="#66686E",
            text_color="#D0D1D4", font=ctk.CTkFont(size=11, weight="bold"),
        ).place(relx=0.96, y=17, anchor="ne")
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
            content, fg_color=SURFACE, corner_radius=26, border_width=1, border_color="#56585E"
        )
        form.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self._add_glass_edge(form)
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
            form, text="ССЫЛКА НА БОЙ", text_color="#B9BABE",
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
            fg_color="#111215",
            border_color=LINE,
            text_color=TEXT,
            placeholder_text_color="#77797F",
            font=ctk.CTkFont(size=13),
        )
        self.link_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._bind_paste(self.link_entry)
        ctk.CTkButton(
            link_row, text="Вставить", width=84, height=44, corner_radius=14,
            fg_color="#303136", hover_color="#45474D", text_color=TEXT,
            border_width=1, border_color="#686A70",
            command=self._paste_battle_link,
        ).pack(side="right")

        ctk.CTkLabel(
            form, text="Название", text_color="#B9BABE",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(16, 7))
        self.name_entry = ctk.CTkEntry(
            form, textvariable=self.name_var, placeholder_text="Например, Фарм",
            height=42, corner_radius=14, fg_color="#111215", border_color=LINE,
            text_color=TEXT, placeholder_text_color="#77797F",
        )
        self.name_entry.pack(fill="x", padx=22)
        self._bind_paste(self.name_entry)

        ctk.CTkLabel(
            form, text="Заметка", text_color="#B9BABE",
            font=ctk.CTkFont(size=10, weight="bold"),
        ).pack(anchor="w", padx=22, pady=(13, 7))
        self.note_entry = ctk.CTkEntry(
            form, textvariable=self.note_var, placeholder_text="Необязательно",
            height=42, corner_radius=14, fg_color="#111215", border_color=LINE,
            text_color=TEXT, placeholder_text_color="#77797F",
        )
        self.note_entry.pack(fill="x", padx=22)
        self._bind_paste(self.note_entry)

        ctk.CTkButton(
            form, text="Сохранить бой", command=self._save_server,
            height=42, corner_radius=14, fg_color="#33353A", hover_color="#484A50",
            border_width=1, border_color="#66686E",
            text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(fill="x", padx=22, pady=(16, 7))
        ctk.CTkButton(
            form, text="Удалить выбранный", command=self._delete_selected,
            height=38, corner_radius=14, fg_color="transparent", hover_color="#303136",
            border_width=1, border_color="#66686E", text_color="#D0D1D4",
        ).pack(fill="x", padx=22, pady=(0, 12))
        self.auto_join_button = ctk.CTkButton(
            form,
            text="Начать автоподключение",
            command=self._toggle_auto_join,
            height=50,
            corner_radius=16,
            fg_color=CYAN,
            hover_color="#FFFFFF",
            border_width=1,
            border_color="#FFFFFF",
            text_color="#111215",
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        self.auto_join_button.pack(fill="x", padx=22, pady=(4, 5))
        ctk.CTkLabel(
            form, textvariable=self.selected_var, text_color=MUTED,
            font=ctk.CTkFont(size=11), wraplength=360, justify="left",
        ).pack(anchor="w", padx=22, pady=(3, 3))

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
        self.multi_target_vars.clear()
        items = self.catalog.get(self.current_mode, [])
        if not items:
            empty = ctk.CTkLabel(
                self.server_list, text="В этом режиме пока нет сохранённых боёв.",
                text_color=MUTED,
            )
            empty.pack(anchor="w", padx=10, pady=14)
            self.server_rows.append(empty)
            self._update_auto_join_button_text()
            return

        selected = self._selected_name()
        multi_targets = self.settings.setdefault("multi_targets", {}).setdefault(self.current_mode, [])
        for item in items:
            name = item["name"]
            link = item.get("battle_link", "")
            note = item.get("note", "")
            card = ctk.CTkFrame(
                self.server_list,
                fg_color="#303136" if name == selected else SURFACE_2,
                corner_radius=18,
                border_width=1,
                border_color="#BFC0C4" if name == selected else "#44464B",
            )
            card.pack(fill="x", padx=4, pady=5)
            self.server_rows.append(card)
            target_var = ctk.BooleanVar(value=name in multi_targets)
            self.multi_target_vars[name] = target_var
            ctk.CTkCheckBox(
                card, text="", variable=target_var, width=22, height=22,
                checkbox_width=20, checkbox_height=20,
                fg_color="#D9DADD", hover_color="#FFFFFF",
                border_color="#77797F", checkmark_color="#111215",
                command=lambda target=name, var=target_var: self._set_multi_target(target, var.get()),
            ).pack(side="left", padx=(13, 0), pady=8)
            text_box = ctk.CTkFrame(card, fg_color="transparent")
            text_box.pack(side="left", fill="x", expand=True, padx=13, pady=11)
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
                widget.bind("<Button-1>", lambda event, value=name: self._select_server(value))
        self._update_auto_join_button_text()

    def _set_multi_target(self, name: str, selected: bool) -> None:
        targets = self.settings.setdefault("multi_targets", {}).setdefault(self.current_mode, [])
        order = self.settings.setdefault("multi_target_order", [])
        if selected and name not in targets:
            targets.append(name)
            order.append({"mode": self.current_mode, "name": name})
        elif not selected and name in targets:
            targets.remove(name)
            self.settings["multi_target_order"] = [
                entry for entry in order
                if not (entry.get("mode") == self.current_mode and entry.get("name") == name)
            ]
        save_settings(self.settings)
        self._update_auto_join_button_text()

    def _update_auto_join_button_text(self) -> None:
        if not hasattr(self, "auto_join_button"):
            return
        if self.auto_joiner and self.auto_joiner.running:
            label = "Остановить мультизаход" if len(self.auto_joiner.battle_ids) > 1 else "Остановить автоподключение"
        else:
            targets = self.settings.setdefault("multi_targets", {})
            count = sum(len(targets.setdefault(mode, [])) for mode in MODES)
            label = f"Начать мультизаход ({count})" if count > 1 else "Начать автоподключение"
        self.auto_join_button.configure(text=label)

    def _selected_name(self) -> str:
        return str(self.settings.setdefault("last_servers", {}).get(self.current_mode, "") or "")

    def _select_server(self, name: str) -> None:
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
        self._select_server(name)

    def _delete_selected(self) -> None:
        name = self._selected_name()
        if not name:
            return
        remove_server(self.catalog, self.current_mode, name)
        save_catalog(self.catalog)
        self.settings.setdefault("last_servers", {})[self.current_mode] = ""
        targets = self.settings.setdefault("multi_targets", {}).setdefault(self.current_mode, [])
        if name in targets:
            targets.remove(name)
        self.settings["multi_target_order"] = [
            entry for entry in self.settings.setdefault("multi_target_order", [])
            if not (entry.get("mode") == self.current_mode and entry.get("name") == name)
        ]
        save_settings(self.settings)
        self._refresh_list()
        self._restore_selection()

    def _delete_all_servers(self) -> None:
        if not any(self.catalog.get(mode, []) for mode in MODES):
            return
        if not messagebox.askyesno(
            "Удалить все бои",
            "Удалить все сохранённые бои во всех режимах? Это действие нельзя отменить.",
        ):
            return
        self.catalog = {mode: [] for mode in MODES}
        self.settings["last_servers"] = {mode: "" for mode in MODES}
        self.settings["multi_targets"] = {mode: [] for mode in MODES}
        self.settings["multi_target_order"] = []
        save_catalog(self.catalog)
        save_settings(self.settings)
        self._refresh_list()
        self._restore_selection()

    def _toggle_auto_join(self) -> None:
        if self.auto_joiner and self.auto_joiner.running:
            self.auto_joiner.stop()
            self._update_auto_join_button_text()
            self.auto_join_status_var.set("Автоподключение остановлено")
            return
        all_targets = self.settings.setdefault("multi_targets", {})
        selected_targets = [
            (entry["mode"], entry["name"])
            for entry in self.settings.setdefault("multi_target_order", [])
            if isinstance(entry, dict)
            and entry.get("mode") in MODES
            and entry.get("name") in all_targets.setdefault(entry["mode"], [])
        ]
        ordered_keys = set(selected_targets)
        selected_targets.extend(
            (mode, name)
            for mode in MODES
            for name in all_targets.setdefault(mode, [])
            if (mode, name) not in ordered_keys
        )
        if selected_targets:
            selected_items = [
                (mode, item)
                for mode, name in selected_targets
                for item in self.catalog.get(mode, [])
                if item["name"] == name
            ]
            invalid_names = [
                item["name"] for _mode, item in selected_items
                if not battle_id_from_link(item.get("battle_link", ""))
            ]
            if invalid_names or len(selected_items) != len(selected_targets):
                messagebox.showerror(
                    "ProTanki",
                    "Проверьте ссылки выбранных боёв: " + ", ".join(
                        invalid_names or [name for _mode, name in selected_targets]
                    ),
                )
                return
            links = [item["battle_link"] for _mode, item in selected_items]
            ids = [battle_id_from_link(link) for link in links]
            if len(set(ids)) != len(ids):
                messagebox.showerror(
                    "ProTanki",
                    "В выбранных сохранённых боях есть одинаковые ссылки. Уберите повтор, чтобы каждый выбранный бой проверялся отдельно.",
                )
                return
        else:
            links = [self.battle_link_var.get().strip()]
        if not any(battle_id_from_link(link) for link in links):
            messagebox.showerror("ProTanki", "Выберите бой или вставьте ссылку на него.")
            return
        self.auto_joiner = BattleJoinWatcher(
            links,
            list(self.settings.get("process_names") or []),
            on_status=lambda text: self.after(0, self.auto_join_status_var.set, text),
            on_finished=self._auto_join_finished,
        )
        try:
            self.auto_joiner.start()
        except ValueError as exc:
            messagebox.showerror("ProTanki", str(exc))
            return
        self._update_auto_join_button_text()

    def _store_ocr_result(self, error: str) -> None:
        # The loader runs on a worker thread; Tk widgets are updated only by
        # _poll_ocr_ready on the main UI thread.
        self._ocr_result = error

    def _poll_ocr_ready(self) -> None:
        if self._ocr_result is None:
            self.after(50, self._poll_ocr_ready)
            return
        self._ocr_ready(self._ocr_result)

    def _ocr_ready(self, error: str) -> None:
        self.auto_join_button.configure(
            state="normal",
        )
        self._update_auto_join_button_text()
        if error:
            self.auto_join_status_var.set("Не удалось подготовить распознавание; запуск покажет причину")
        else:
            self.auto_join_status_var.set("Готово к быстрому подключению")

    def _auto_join_finished(self, text: str) -> None:
        try:
            def apply_result() -> None:
                self.auto_join_status_var.set(text)
                self.auto_join_button.configure(state="normal")
                self._update_auto_join_button_text()
                if text == "Вход в бой выполнен.":
                    play_join_success()

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
