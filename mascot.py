import argparse
import ctypes
import json
import queue
import time
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox
from uuid import uuid4

from PIL import Image, ImageDraw, ImageTk
from pynput import keyboard


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


if hasattr(ctypes, "windll"):
    USER32 = ctypes.windll.user32
else:
    USER32 = None


@dataclass
class CharacterConfig:
    character_id: str
    name: str
    idle_image_path: str
    left_image_path: str
    right_image_path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.character_id,
            "name": self.name,
            "idle_image_path": self.idle_image_path,
            "left_image_path": self.left_image_path,
            "right_image_path": self.right_image_path,
        }


class DesktopMascot:
    def __init__(
        self,
        default_character: CharacterConfig | None,
        x: int,
        y: int,
        scale: float,
        config_path: Path,
        fps: int = 60,
    ):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "magenta")
        self.root.configure(bg="magenta")

        self.default_character = default_character
        self.config_path = config_path
        self.characters: list[CharacterConfig] = []
        self.current_character_id = ""

        self.image_idle_src: Image.Image | None = None
        self.image_left_src: Image.Image | None = None
        self.image_right_src: Image.Image | None = None

        self.initial_scale = scale
        self.scale = scale
        self.scale_step = 0.05
        self.min_scale = 0.10
        self.max_scale = 2.50

        self.image_idle: Image.Image | None = None
        self.image_left: Image.Image | None = None
        self.image_right: Image.Image | None = None
        self.width = 1
        self.height = 1

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg="magenta",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()

        self.photo = None
        self.image_id = self.canvas.create_image(0, 0, anchor="nw")

        self.event_queue: "queue.Queue[str]" = queue.Queue()
        self.last_key_ts = 0.0
        self.anim_index = 0
        self.tick_interval_ms = max(8, int(1000 / fps))
        self.running = True

        self.edge_margin = 16
        self.snap_distance = 40
        self.snap_offset_y = 10
        self.window_snap_enabled = True
        self.window_snap_var = tk.BooleanVar(value=True)
        self.start_hand_var = tk.StringVar(value="left")

        self.character_selection_var = tk.StringVar(value="")
        self.context_menu = tk.Menu(self.root, tearoff=0)
        self.character_editor_window: tk.Toplevel | None = None

        settings = self._load_settings()
        self.characters = self._load_characters(settings)
        if not self.characters and self.default_character is not None:
            self.characters = [self.default_character]
        if not self.characters:
            self.characters = [self._create_placeholder_character()]

        self.current_character_id = self._resolve_saved_character_id(settings)
        if not self.current_character_id:
            self.current_character_id = self.characters[0].character_id
        self.character_selection_var.set(self.current_character_id)

        saved_start_hand = settings.get("start_hand")
        if saved_start_hand not in ("left", "right"):
            saved_start_hand = settings.get("display_side")
        if saved_start_hand in ("left", "right"):
            self.start_hand_var.set(saved_start_hand)

        saved_snap = settings.get("window_snap_enabled")
        if isinstance(saved_snap, bool):
            self.window_snap_enabled = saved_snap
            self.window_snap_var.set(saved_snap)

        self._apply_start_hand(save=False)

        self.resize_state: dict[str, int | float | str | None] = {
            "active": False,
            "edge": None,
            "mx": 0,
            "my": 0,
            "wx": 0,
            "wy": 0,
            "ww": 0,
            "wh": 0,
            "scale": 1.0,
        }
        self.move_state: dict[str, int | bool] = {"active": False, "mx": 0, "my": 0, "wx": 0, "wy": 0}

        start_x = x
        start_y = y
        start_scale = scale
        saved_x = settings.get("x")
        saved_y = settings.get("y")
        saved_scale = settings.get("scale")
        if isinstance(saved_x, int):
            start_x = saved_x
        if isinstance(saved_y, int):
            start_y = saved_y
        if isinstance(saved_scale, (int, float)):
            start_scale = float(saved_scale)

        if not self._load_character_images(self.current_character_id, notify=False):
            if not self._load_first_available_character():
                placeholder = self._ensure_placeholder_character()
                self._load_character_images(placeholder.character_id, notify=False)

        self._apply_scale(start_scale, x=start_x, y=start_y)
        self._setup_drag()
        self._setup_context_menu()
        self._setup_close_shortcut()
        self._start_keyboard_listener()

    def _load_settings(self) -> dict:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_settings(self):
        current_character = self._get_character_by_id(self.current_character_id)
        state = {
            "character": current_character.name if current_character is not None else "",
            "current_character_id": self.current_character_id,
            "characters": [character.to_dict() for character in self.characters],
            "x": int(self.root.winfo_x()),
            "y": int(self.root.winfo_y()),
            "scale": float(self.scale),
            "start_hand": self.start_hand_var.get(),
            "window_snap_enabled": bool(self.window_snap_var.get()),
        }
        try:
            self.config_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _normalize_character_name(value: str) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

    @staticmethod
    def _resolve_image_path(path_text: str) -> Path:
        if not isinstance(path_text, str):
            raise ValueError("画像パスは文字列で指定してください。")
        trimmed = path_text.strip().strip('"')
        if not trimmed:
            raise ValueError("画像パスが空です。")
        return Path(trimmed).expanduser().resolve()

    @classmethod
    def _validate_image_path(cls, path_text: str, label: str) -> Path:
        path = cls._resolve_image_path(path_text)
        if not path.is_file():
            raise ValueError(f"{label}が見つかりません: {path}")
        try:
            with Image.open(path) as image:
                image.verify()
        except OSError as exc:
            raise ValueError(f"{label}として読み込めない画像です: {path}") from exc
        return path

    @classmethod
    def _load_image_rgba(cls, path_text: str) -> Image.Image:
        path = cls._resolve_image_path(path_text)
        with Image.open(path) as image:
            return image.convert("RGBA")

    def _create_character_id(self) -> str:
        used = {character.character_id for character in self.characters}
        while True:
            candidate = f"char-{uuid4().hex[:8]}"
            if candidate not in used:
                return candidate

    @staticmethod
    def _create_placeholder_character() -> CharacterConfig:
        return CharacterConfig(
            character_id="__placeholder__",
            name="未設定キャラクター",
            idle_image_path="",
            left_image_path="",
            right_image_path="",
        )

    def _load_characters(self, settings: dict) -> list[CharacterConfig]:
        raw_characters = settings.get("characters")
        if not isinstance(raw_characters, list):
            return []

        parsed: list[CharacterConfig] = []
        used_ids: set[str] = set()
        for raw in raw_characters:
            if not isinstance(raw, dict):
                continue

            raw_name = self._normalize_character_name(raw.get("name", ""))
            if not raw_name:
                continue

            raw_id = raw.get("id")
            if not isinstance(raw_id, str) or not raw_id.strip():
                raw_id = f"char-{uuid4().hex[:8]}"
            raw_id = raw_id.strip()
            if raw_id in used_ids:
                raw_id = f"char-{uuid4().hex[:8]}"
            used_ids.add(raw_id)

            idle = raw.get("idle_image_path", "")
            left = raw.get("left_image_path", "")
            right = raw.get("right_image_path", "")
            if not isinstance(idle, str) or not isinstance(left, str) or not isinstance(right, str):
                continue

            parsed.append(
                CharacterConfig(
                    character_id=raw_id,
                    name=raw_name,
                    idle_image_path=idle,
                    left_image_path=left,
                    right_image_path=right,
                )
            )

        return parsed

    def _resolve_saved_character_id(self, settings: dict) -> str:
        saved_id = settings.get("current_character_id")
        if isinstance(saved_id, str) and self._get_character_by_id(saved_id) is not None:
            return saved_id

        # backward compatibility for older settings format
        saved_name = settings.get("character")
        if isinstance(saved_name, str):
            for character in self.characters:
                if character.name == saved_name:
                    return character.character_id
        return ""

    def _get_character_by_id(self, character_id: str) -> CharacterConfig | None:
        for character in self.characters:
            if character.character_id == character_id:
                return character
        return None

    def _ensure_placeholder_character(self) -> CharacterConfig:
        existing = self._get_character_by_id("__placeholder__")
        if existing is not None:
            return existing
        placeholder = self._create_placeholder_character()
        self.characters.insert(0, placeholder)
        return placeholder

    @staticmethod
    def _resize_image(image: Image.Image, scale: float) -> Image.Image:
        if scale == 1.0:
            return image
        w, h = image.size
        return image.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)

    def _generate_placeholder_frame(self, label: str, accent_color: tuple[int, int, int]) -> Image.Image:
        width, height = 440, 300
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((20, 20, width - 20, height - 20), radius=42, fill=(28, 33, 42, 232))
        draw.rounded_rectangle((20, 20, width - 20, height - 20), radius=42, outline=(*accent_color, 255), width=6)
        draw.text((width // 2, height // 2 - 24), "Desktop Mascot", fill=(240, 240, 240, 255), anchor="mm")
        draw.text((width // 2, height // 2 + 22), label, fill=(*accent_color, 255), anchor="mm")
        return image

    def _load_placeholder_images(self):
        self.image_idle_src = self._generate_placeholder_frame("待機", (118, 179, 255))
        self.image_left_src = self._generate_placeholder_frame("左", (255, 167, 66))
        self.image_right_src = self._generate_placeholder_frame("右", (255, 103, 130))

    def _load_character_images(self, character_id: str, notify: bool = True) -> bool:
        character = self._get_character_by_id(character_id)
        if character is None:
            return False

        if (
            character.character_id == "__placeholder__"
            and not character.idle_image_path.strip()
            and not character.left_image_path.strip()
            and not character.right_image_path.strip()
        ):
            self._load_placeholder_images()
            self.current_character_id = character.character_id
            self.character_selection_var.set(character.character_id)
            return True

        try:
            idle_image = self._load_image_rgba(character.idle_image_path)
            left_image = self._load_image_rgba(character.left_image_path)
            right_image = self._load_image_rgba(character.right_image_path)
        except (ValueError, OSError) as exc:
            if notify:
                messagebox.showerror("画像読み込みエラー", f"キャラクター「{character.name}」を読み込めません。\n{exc}")
            return False

        self.image_idle_src = idle_image
        self.image_left_src = left_image
        self.image_right_src = right_image
        self.current_character_id = character.character_id
        self.character_selection_var.set(character.character_id)
        return True

    def _load_first_available_character(self) -> bool:
        for character in self.characters:
            if self._load_character_images(character.character_id, notify=False):
                return True
        return False

    def _switch_character_by_id(self, character_id: str):
        if character_id == self.current_character_id:
            return
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        if not self._load_character_images(character_id, notify=True):
            self.character_selection_var.set(self.current_character_id)
            return
        self._apply_scale(self.scale, x=x, y=y)
        self._save_settings()

    def _apply_scale(self, new_scale: float, x: int | None = None, y: int | None = None):
        new_scale = max(self.min_scale, min(self.max_scale, new_scale))
        self.scale = new_scale

        if self.image_idle_src is None or self.image_left_src is None or self.image_right_src is None:
            return

        self.image_idle = self._resize_image(self.image_idle_src, self.scale)
        self.image_left = self._resize_image(self.image_left_src, self.scale)
        self.image_right = self._resize_image(self.image_right_src, self.scale)

        self.width = max(self.image_idle.width, self.image_left.width, self.image_right.width)
        self.height = max(self.image_idle.height, self.image_left.height, self.image_right.height)

        pos_x = self.root.winfo_x() if x is None else x
        pos_y = self.root.winfo_y() if y is None else y
        self.canvas.config(width=self.width, height=self.height)
        self.root.geometry(f"{self.width}x{self.height}+{pos_x}+{pos_y}")

    def _change_scale(self, delta: int):
        self._apply_scale(self.scale + self.scale_step * delta)
        self._save_settings()

    def _reset_scale(self):
        self._apply_scale(self.initial_scale)
        self._save_settings()

    def _to_canvas_size(self, image: Image.Image) -> Image.Image:
        out = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        ox = (self.width - image.width) // 2
        oy = (self.height - image.height) // 2
        out.alpha_composite(image, (ox, oy))
        return out

    def _setup_drag(self):
        def hit_test_edge(x: int, y: int) -> str | None:
            left = x <= self.edge_margin
            right = x >= self.width - self.edge_margin
            top = y <= self.edge_margin
            bottom = y >= self.height - self.edge_margin
            if left and top:
                return "top_left"
            if right and top:
                return "top_right"
            if left and bottom:
                return "bottom_left"
            if right and bottom:
                return "bottom_right"
            if left:
                return "left"
            if right:
                return "right"
            if top:
                return "top"
            if bottom:
                return "bottom"
            return None

        def on_press(event):
            edge = hit_test_edge(event.x, event.y)
            if edge is not None:
                self.resize_state["active"] = True
                self.resize_state["edge"] = edge
                self.resize_state["mx"] = event.x_root
                self.resize_state["my"] = event.y_root
                self.resize_state["wx"] = self.root.winfo_x()
                self.resize_state["wy"] = self.root.winfo_y()
                self.resize_state["ww"] = self.width
                self.resize_state["wh"] = self.height
                self.resize_state["scale"] = self.scale
                return

            self.move_state["active"] = True
            self.move_state["mx"] = event.x_root
            self.move_state["my"] = event.y_root
            self.move_state["wx"] = self.root.winfo_x()
            self.move_state["wy"] = self.root.winfo_y()

        def on_drag(event):
            if self.resize_state["active"]:
                self._resize_by_drag(event.x_root, event.y_root)
                return
            if self.move_state["active"]:
                dx = event.x_root - int(self.move_state["mx"])
                dy = event.y_root - int(self.move_state["my"])
                self.root.geometry(
                    f"{self.width}x{self.height}+{int(self.move_state['wx']) + dx}+{int(self.move_state['wy']) + dy}"
                )

        def on_release(_event):
            self.resize_state["active"] = False
            self.resize_state["edge"] = None
            self.move_state["active"] = False
            self._snap_to_surface()
            self._save_settings()

        self.canvas.bind("<ButtonPress-1>", on_press)
        self.canvas.bind("<B1-Motion>", on_drag)
        self.canvas.bind("<ButtonRelease-1>", on_release)

    def _resize_by_drag(self, x_root: int, y_root: int):
        edge = self.resize_state["edge"]
        if edge is None:
            return

        start_mx = int(self.resize_state["mx"])
        start_my = int(self.resize_state["my"])
        start_x = int(self.resize_state["wx"])
        start_y = int(self.resize_state["wy"])
        start_w = int(self.resize_state["ww"])
        start_h = int(self.resize_state["wh"])
        start_scale = float(self.resize_state["scale"])

        dx = x_root - start_mx
        dy = y_root - start_my

        new_w = start_w
        new_h = start_h
        if "left" in edge:
            new_w = start_w - dx
        if "right" in edge:
            new_w = start_w + dx
        if "top" in edge:
            new_h = start_h - dy
        if "bottom" in edge:
            new_h = start_h + dy

        ratio_w = new_w / start_w if start_w > 0 else 1.0
        ratio_h = new_h / start_h if start_h > 0 else 1.0
        if edge in ("left", "right"):
            ratio = ratio_w
        elif edge in ("top", "bottom"):
            ratio = ratio_h
        else:
            ratio = ratio_w if abs(dx) >= abs(dy) else ratio_h

        new_scale = max(self.min_scale, min(self.max_scale, start_scale * ratio))

        right_anchor = start_x + start_w
        bottom_anchor = start_y + start_h
        new_x = start_x
        new_y = start_y
        if "left" in edge:
            predicted_w = max(1, int(start_w * (new_scale / start_scale)))
            new_x = right_anchor - predicted_w
        if "top" in edge:
            predicted_h = max(1, int(start_h * (new_scale / start_scale)))
            new_y = bottom_anchor - predicted_h

        self._apply_scale(new_scale, x=new_x, y=new_y)

    def _apply_start_hand(self, save: bool = True):
        # Keep parity so the first key press starts from the selected hand.
        self.anim_index = 1 if self.start_hand_var.get() == "left" else 0
        if save:
            self._save_settings()

    def _rebuild_context_menu(self):
        menu = self.context_menu
        menu.delete(0, "end")
        menu.add_command(label="キャラクター編集...", command=self._open_character_editor)

        self.character_selection_var.set(self.current_character_id)
        for character in self.characters:
            menu.add_radiobutton(
                label=character.name,
                variable=self.character_selection_var,
                value=character.character_id,
                command=lambda cid=character.character_id: self._switch_character_by_id(cid),
            )

        menu.add_separator()
        menu.add_command(label="大きくする", command=lambda: self._change_scale(1))
        menu.add_command(label="小さくする", command=lambda: self._change_scale(-1))
        menu.add_command(label="サイズをリセット", command=self._reset_scale)
        menu.add_separator()
        menu.add_checkbutton(
            label="ウィンドウ吸着",
            variable=self.window_snap_var,
            command=self._toggle_window_snap,
            onvalue=True,
            offvalue=False,
        )

        start_hand_menu = tk.Menu(menu, tearoff=0)
        start_hand_menu.add_radiobutton(
            label="左手から上げる",
            variable=self.start_hand_var,
            value="left",
            command=self._apply_start_hand,
        )
        start_hand_menu.add_radiobutton(
            label="右手から上げる",
            variable=self.start_hand_var,
            value="right",
            command=self._apply_start_hand,
        )
        menu.add_cascade(label="打鍵開始の手", menu=start_hand_menu)

    def _setup_context_menu(self):
        def on_right_click(event):
            self._rebuild_context_menu()
            try:
                self.context_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.context_menu.grab_release()

        self.canvas.bind("<Button-3>", on_right_click)

    def _open_character_editor(self):
        if self.character_editor_window is not None and self.character_editor_window.winfo_exists():
            self.character_editor_window.focus_force()
            self.character_editor_window.lift()
            return

        window = tk.Toplevel(self.root)
        window.title("キャラクター編集")
        window.resizable(False, False)
        window.transient(self.root)
        window.columnconfigure(1, minsize=360)
        self.character_editor_window = window

        listbox = tk.Listbox(window, width=28, height=12, exportselection=False)
        listbox.grid(row=0, column=0, rowspan=8, padx=(12, 8), pady=12, sticky="ns")

        name_var = tk.StringVar()
        idle_var = tk.StringVar()
        left_var = tk.StringVar()
        right_var = tk.StringVar()
        editing_state: dict[str, str | None] = {"id": None}
        list_index_to_id: list[str] = []

        tk.Label(window, text="表示名").grid(row=0, column=1, sticky="w", padx=(0, 6), pady=(12, 2))
        name_entry = tk.Entry(window, textvariable=name_var, width=42)
        name_entry.grid(row=1, column=1, columnspan=2, sticky="we", padx=(0, 10), pady=(0, 8))

        tk.Label(window, text="待機用画像").grid(row=2, column=1, sticky="w", padx=(0, 6), pady=(0, 2))
        tk.Entry(window, textvariable=idle_var, width=42).grid(row=3, column=1, sticky="we", padx=(0, 4), pady=(0, 6))

        tk.Label(window, text="左用画像").grid(row=4, column=1, sticky="w", padx=(0, 6), pady=(0, 2))
        tk.Entry(window, textvariable=left_var, width=42).grid(row=5, column=1, sticky="we", padx=(0, 4), pady=(0, 6))

        tk.Label(window, text="右用画像").grid(row=6, column=1, sticky="w", padx=(0, 6), pady=(0, 2))
        tk.Entry(window, textvariable=right_var, width=42).grid(row=7, column=1, sticky="we", padx=(0, 4), pady=(0, 10))

        def browse_image(target_var: tk.StringVar, dialog_title: str):
            selected = filedialog.askopenfilename(
                parent=window,
                title=dialog_title,
                filetypes=[
                    ("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"),
                    ("All files", "*.*"),
                ],
            )
            if selected:
                target_var.set(selected)

        tk.Button(
            window,
            text="参照",
            width=8,
            command=lambda: browse_image(idle_var, "待機用画像を選択"),
        ).grid(row=3, column=2, sticky="w", padx=(0, 10), pady=(0, 6))
        tk.Button(
            window,
            text="参照",
            width=8,
            command=lambda: browse_image(left_var, "左用画像を選択"),
        ).grid(row=5, column=2, sticky="w", padx=(0, 10), pady=(0, 6))
        tk.Button(
            window,
            text="参照",
            width=8,
            command=lambda: browse_image(right_var, "右用画像を選択"),
        ).grid(row=7, column=2, sticky="w", padx=(0, 10), pady=(0, 10))

        def load_character_to_form(character_id: str):
            character = self._get_character_by_id(character_id)
            if character is None:
                return
            editing_state["id"] = character.character_id
            name_var.set(character.name)
            idle_var.set(character.idle_image_path)
            left_var.set(character.left_image_path)
            right_var.set(character.right_image_path)

        def refresh_character_list(selected_id: str | None = None):
            nonlocal list_index_to_id
            listbox.delete(0, "end")
            list_index_to_id = []
            for character in self.characters:
                listbox.insert("end", character.name)
                list_index_to_id.append(character.character_id)

            target_id = selected_id if selected_id is not None else editing_state.get("id")
            if target_id not in list_index_to_id:
                target_id = self.current_character_id if self.current_character_id in list_index_to_id else None

            if target_id is not None:
                index = list_index_to_id.index(target_id)
                listbox.selection_set(index)
                listbox.activate(index)
                load_character_to_form(target_id)
            elif list_index_to_id:
                listbox.selection_set(0)
                listbox.activate(0)
                load_character_to_form(list_index_to_id[0])

        def on_character_select(_event=None):
            selected_indices = listbox.curselection()
            if not selected_indices:
                return
            index = selected_indices[0]
            if index < 0 or index >= len(list_index_to_id):
                return
            load_character_to_form(list_index_to_id[index])

        def clear_form_for_new():
            editing_state["id"] = None
            listbox.selection_clear(0, "end")
            name_var.set("新しいキャラクター")
            idle_var.set("")
            left_var.set("")
            right_var.set("")
            name_entry.focus_set()

        def save_character():
            name = self._normalize_character_name(name_var.get())
            if not name:
                messagebox.showerror("入力エラー", "表示名を入力してください。", parent=window)
                return

            try:
                idle_path = str(self._validate_image_path(idle_var.get(), "待機用画像"))
                left_path = str(self._validate_image_path(left_var.get(), "左用画像"))
                right_path = str(self._validate_image_path(right_var.get(), "右用画像"))
            except ValueError as exc:
                messagebox.showerror("入力エラー", str(exc), parent=window)
                return

            editing_id = editing_state.get("id")
            target_id = editing_id if isinstance(editing_id, str) and editing_id else self._create_character_id()
            existing = self._get_character_by_id(target_id)
            if existing is None:
                self.characters.append(
                    CharacterConfig(
                        character_id=target_id,
                        name=name,
                        idle_image_path=idle_path,
                        left_image_path=left_path,
                        right_image_path=right_path,
                    )
                )
            else:
                existing.name = name
                existing.idle_image_path = idle_path
                existing.left_image_path = left_path
                existing.right_image_path = right_path

            if len(self.characters) > 1:
                placeholder = self._get_character_by_id("__placeholder__")
                if (
                    placeholder is not None
                    and placeholder.character_id != target_id
                    and not placeholder.idle_image_path.strip()
                    and not placeholder.left_image_path.strip()
                    and not placeholder.right_image_path.strip()
                ):
                    self.characters = [character for character in self.characters if character.character_id != "__placeholder__"]

            x = self.root.winfo_x()
            y = self.root.winfo_y()
            if self.current_character_id == "__placeholder__":
                self._switch_character_by_id(target_id)
            elif self.current_character_id == target_id:
                if self._load_character_images(target_id, notify=True):
                    self._apply_scale(self.scale, x=x, y=y)

            editing_state["id"] = target_id
            self._save_settings()
            self._rebuild_context_menu()
            refresh_character_list(target_id)

        def delete_character():
            editing_id = editing_state.get("id")
            if not isinstance(editing_id, str) or not editing_id:
                messagebox.showwarning("削除できません", "削除するキャラクターを選択してください。", parent=window)
                return

            target = self._get_character_by_id(editing_id)
            if target is None:
                messagebox.showwarning("削除できません", "対象キャラクターが見つかりません。", parent=window)
                return

            if not messagebox.askyesno("削除確認", f"「{target.name}」を削除しますか？", parent=window):
                return

            self.characters = [character for character in self.characters if character.character_id != editing_id]
            if not self.characters:
                self.characters = [self._create_placeholder_character()]

            x = self.root.winfo_x()
            y = self.root.winfo_y()
            if self.current_character_id == editing_id:
                if not self._load_first_available_character():
                    placeholder = self._ensure_placeholder_character()
                    self._load_character_images(placeholder.character_id, notify=False)
                self._apply_scale(self.scale, x=x, y=y)

            editing_state["id"] = None
            self._save_settings()
            self._rebuild_context_menu()
            refresh_character_list(self.current_character_id)

        def on_close():
            self.character_editor_window = None
            window.destroy()

        listbox.bind("<<ListboxSelect>>", on_character_select)

        button_frame = tk.Frame(window)
        button_frame.grid(row=8, column=0, columnspan=3, sticky="we", padx=12, pady=(0, 12))
        tk.Button(button_frame, text="新規追加", command=clear_form_for_new).pack(side="left")
        tk.Button(button_frame, text="保存", command=save_character).pack(side="left", padx=(8, 0))
        tk.Button(button_frame, text="削除", command=delete_character).pack(side="left", padx=(8, 0))
        tk.Button(button_frame, text="閉じる", command=on_close).pack(side="right")

        window.protocol("WM_DELETE_WINDOW", on_close)
        refresh_character_list()

    def _toggle_window_snap(self):
        self.window_snap_enabled = bool(self.window_snap_var.get())
        self._save_settings()

    @staticmethod
    def _rect_to_tuple(rect: RECT) -> tuple[int, int, int, int]:
        return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)

    def _collect_candidate_surfaces(self, x: int, width: int) -> list[tuple[int, str]]:
        if USER32 is None:
            return []

        mascot_left = x
        mascot_right = x + width
        min_overlap = max(24, width // 5)
        candidates: list[tuple[int, str]] = []

        def overlap_ok(left: int, right: int) -> bool:
            overlap = min(mascot_right, right) - max(mascot_left, left)
            return overlap >= min_overlap

        taskbar_hwnd = USER32.FindWindowW("Shell_TrayWnd", None)
        if taskbar_hwnd:
            rect = RECT()
            if USER32.GetWindowRect(taskbar_hwnd, ctypes.byref(rect)):
                left, top, right, _ = self._rect_to_tuple(rect)
                if overlap_ok(left, right):
                    candidates.append((top, "taskbar"))

        if self.window_snap_enabled:
            self_hwnd = int(self.root.winfo_id())
            enum_windows_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def callback(hwnd, _lparam):
                if hwnd == self_hwnd:
                    return True
                if not USER32.IsWindowVisible(hwnd):
                    return True
                if USER32.IsIconic(hwnd):
                    return True

                rect = RECT()
                if not USER32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                left, top, right, bottom = self._rect_to_tuple(rect)

                if right - left < 120 or bottom - top < 60:
                    return True
                if overlap_ok(left, right):
                    candidates.append((top, "window"))
                return True

            proc = enum_windows_proc(callback)
            USER32.EnumWindows(proc, 0)

        return candidates

    def _snap_to_surface(self):
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        bottom = y + self.height
        candidates = self._collect_candidate_surfaces(x, self.width)
        if not candidates:
            return

        nearest_target_bottom = None
        nearest_dist = self.snap_distance + 1
        for surface_top, _source in candidates:
            target_bottom = surface_top + self.snap_offset_y
            dist = abs(bottom - target_bottom)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_target_bottom = target_bottom

        if nearest_target_bottom is None or nearest_dist > self.snap_distance:
            return

        snapped_y = int(nearest_target_bottom - self.height)
        self.root.geometry(f"{self.width}x{self.height}+{x}+{snapped_y}")

    def _setup_close_shortcut(self):
        def close(_event=None):
            self._save_settings()
            self.running = False
            self.root.destroy()

        self.root.bind("<Escape>", close)
        self.root.bind("<Control-q>", close)

    def _start_keyboard_listener(self):
        def on_press(_key):
            if self.running:
                self.event_queue.put("key")

        listener = keyboard.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()

    def _compute_frame(self):
        now = time.time()
        had_key = False
        while not self.event_queue.empty():
            try:
                self.event_queue.get_nowait()
            except queue.Empty:
                break
            had_key = True
            self.last_key_ts = now

        active = now - self.last_key_ts <= 0.15
        if not active:
            return self._to_canvas_size(self.image_idle)

        if had_key:
            self.anim_index = 1 - self.anim_index
        frame = self.image_left if self.anim_index == 0 else self.image_right
        return self._to_canvas_size(frame)

    def _tick(self):
        if not self.running:
            return
        frame = self._compute_frame()
        self.photo = ImageTk.PhotoImage(frame)
        self.canvas.itemconfigure(self.image_id, image=self.photo)
        self.root.after(self.tick_interval_ms, self._tick)

    def run(self):
        self._tick()
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(
        description="Desktop mascot using idle + left/right images and global keyboard input."
    )
    parser.add_argument("--character-name", default="Character 1", help="Initial character display name.")
    parser.add_argument("--idle-image", default=None, help="Initial character idle image path.")
    parser.add_argument("--left-image", default=None, help="Initial character left image path.")
    parser.add_argument("--right-image", default=None, help="Initial character right image path.")
    parser.add_argument("--x", type=int, default=1200, help="Window X position.")
    parser.add_argument("--y", type=int, default=520, help="Window Y position.")
    parser.add_argument("--scale", type=float, default=0.35, help="Image scale (e.g. 0.35).")
    parser.add_argument("--config", default="mascot_settings.json", help="Path to settings JSON.")
    args = parser.parse_args()

    initial_image_args = [args.idle_image, args.left_image, args.right_image]
    if any(initial_image_args) and not all(initial_image_args):
        parser.error("--idle-image, --left-image, --right-image must be set together.")

    default_character = None
    if all(initial_image_args):
        idle_image_path = DesktopMascot._validate_image_path(args.idle_image, "Idle image")
        left_image_path = DesktopMascot._validate_image_path(args.left_image, "Left image")
        right_image_path = DesktopMascot._validate_image_path(args.right_image, "Right image")
        default_character = CharacterConfig(
            character_id="initial-character",
            name=args.character_name.strip() or "Character 1",
            idle_image_path=str(idle_image_path),
            left_image_path=str(left_image_path),
            right_image_path=str(right_image_path),
        )

    config_path = Path(args.config).resolve()

    app = DesktopMascot(
        default_character=default_character,
        x=args.x,
        y=args.y,
        scale=args.scale,
        config_path=config_path,
    )
    app.run()


if __name__ == "__main__":
    main()
