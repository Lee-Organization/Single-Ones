import os
import sys
import time
import json
import threading
import traceback
import uuid
import ctypes
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from pathlib import Path

APP_NAME = "Ultimate Key Presser"
BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "Settings.json"
CLICKERS_FILE = BASE_DIR / "Clickers.json"
PRESSERS_FILE = BASE_DIR / "Pressers.json"
TYPERS_FILE = BASE_DIR / "Typers.json"
COMBOS_FILE = BASE_DIR / "Combos.json"
CRASH_FILE = BASE_DIR / "Crash.log"

try:
    import pydirectinput
except Exception:
    pydirectinput = None

try:
    import keyboard
except Exception:
    keyboard = None

DEFAULT_SETTINGS = {
    "theme": "dark",
    "emergency_stop": "f12",
    "emergency_key_exception": False,
    "max_speed": 100,
    "pin_to_top": True
}


def write_crash(text):
    try:
        with open(CRASH_FILE, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 78 + "\n")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            f.write(str(text).rstrip() + "\n")
    except Exception:
        pass


def load_json(path, default):
    try:
        if not path.exists():
            save_json(path, default)
            return default.copy() if isinstance(default, dict) else list(default)
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        write_crash(traceback.format_exc())
        return default.copy() if isinstance(default, dict) else list(default)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def ensure_ids(items):
    changed = False
    for item in items:
        if "id" not in item:
            item["id"] = uuid.uuid4().hex
            changed = True
    return changed


def short_text(text, limit=42):
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return text[:limit - 3] + "..."



class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

def fast_cursor_position():
    """Cheap cursor read for the clicker safety check; falls back to PyDirectInput."""
    try:
        point = _POINT()
        if ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return int(point.x), int(point.y)
    except Exception:
        pass
    if pydirectinput is not None:
        return pydirectinput.position()
    return (0, 0)

class App:
    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.geometry("980x680+120+80")
        self.root.minsize(820, 560)

        self.settings = DEFAULT_SETTINGS.copy()
        self.settings.update(load_json(SETTINGS_FILE, DEFAULT_SETTINGS))
        self.clickers = load_json(CLICKERS_FILE, [])
        self.pressers = load_json(PRESSERS_FILE, [])
        self.typers = load_json(TYPERS_FILE, [])
        self.combos = load_json(COMBOS_FILE, [])

        if ensure_ids(self.clickers):
            save_json(CLICKERS_FILE, self.clickers)
        if ensure_ids(self.pressers):
            save_json(PRESSERS_FILE, self.pressers)
        if ensure_ids(self.typers):
            save_json(TYPERS_FILE, self.typers)
        if ensure_ids(self.combos):
            save_json(COMBOS_FILE, self.combos)

        self.stop_event = threading.Event()
        self.worker_thread = None
        self.emergency_hook = None
        self.mouse_monitor_thread = None
        self.last_expected_pos = None
        self.ignore_mouse_until = 0.0
        self.active_name = None
        self.restore_borderless_pending = False

        # COMBO can start clickers/pressers/typers that keep running until
        # their matching STOP block (or global emergency stop) is reached.
        self.combo_child_lock = threading.Lock()
        self.combo_child_stops = {"clicker": [], "presser": [], "typer": []}
        self.combo_child_threads = []
        self.collapsed = False
        self.expanded_geometry = self.root.geometry()
        self.drag_start = None
        self.classic_scales = []

        if pydirectinput:
            try:
                pydirectinput.PAUSE = 0
                pydirectinput.FAILSAFE = False
            except Exception:
                pass

        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.build_window()
        self.apply_theme()
        self.apply_pin_setting()

    # ---------------- THEME / WINDOW ----------------

    def colors(self):
        if self.settings.get("theme", "dark") == "dark":
            return {
                "bg": "#15171a", "panel": "#1f2328", "panel2": "#262b31",
                "fg": "#f3f5f7", "muted": "#a6afb9", "border": "#39414a",
                "entry": "#111315", "button": "#2b3138", "button_active": "#363d46",
                "accent": "#3c7fe8", "accent_active": "#326bc4",
                "danger": "#b84444", "danger_active": "#993737",
                "title": "#0f1113", "loop": "#6e4caf"
            }
        return {
            "bg": "#f3f4f6", "panel": "#ffffff", "panel2": "#f7f8fa",
            "fg": "#16181b", "muted": "#61666e", "border": "#cfd4da",
            "entry": "#ffffff", "button": "#e5e8ec", "button_active": "#d9dde2",
            "accent": "#2f6feb", "accent_active": "#255dc6",
            "danger": "#cf4444", "danger_active": "#b63939",
            "title": "#e6e9ed", "loop": "#7651b7"
        }

    def build_window(self):
        self.title_bar = tk.Frame(self.root, height=38, bd=0, highlightthickness=0)
        self.title_bar.pack(fill="x", side="top")
        self.title_bar.pack_propagate(False)

        self.title_label = tk.Label(self.title_bar, text=APP_NAME, font=("Segoe UI", 10, "bold"), anchor="w")
        self.title_label.pack(side="left", padx=(12, 6), fill="y")

        self.pin_indicator = tk.Label(self.title_bar, text="📌", font=("Segoe UI Emoji", 9))
        self.pin_indicator.pack(side="left", padx=2)

        self.close_btn = tk.Button(self.title_bar, text="✕", width=4, bd=0, command=self.on_close)
        self.close_btn.pack(side="right", fill="y")
        self.min_btn = tk.Button(self.title_bar, text="—", width=4, bd=0, command=self.minimize_window)
        self.min_btn.pack(side="right", fill="y")
        self.collapse_btn = tk.Button(self.title_bar, text="▴", width=4, bd=0, command=self.toggle_collapse)
        self.collapse_btn.pack(side="right", fill="y")

        for widget in (self.title_bar, self.title_label, self.pin_indicator):
            widget.bind("<ButtonPress-1>", self.start_drag)
            widget.bind("<B1-Motion>", self.do_drag)
            widget.bind("<Double-Button-1>", lambda e: self.toggle_collapse())

        self.root.bind("<Map>", self.on_window_mapped, add="+")

        self.main_area = ttk.Frame(self.root)
        self.main_area.pack(fill="both", expand=True)

        self.tabs = ttk.Notebook(self.main_area)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=12)

        self.clicker_tab = ttk.Frame(self.tabs)
        self.presser_tab = ttk.Frame(self.tabs)
        self.typer_tab = ttk.Frame(self.tabs)
        self.combo_tab = ttk.Frame(self.tabs)
        self.settings_tab = ttk.Frame(self.tabs)

        self.tabs.add(self.clicker_tab, text="CLICKER")
        self.tabs.add(self.presser_tab, text="PRESSER")
        self.tabs.add(self.typer_tab, text="TYPER")
        self.tabs.add(self.combo_tab, text="COMBO")
        self.tabs.add(self.settings_tab, text="SETTINGS")

        self.build_clicker_tab()
        self.build_presser_tab()
        self.build_typer_tab()
        self.build_combo_tab()
        self.build_settings_tab()

    def apply_theme(self):
        c = self.colors()
        self.root.configure(bg=c["bg"])
        self.title_bar.configure(bg=c["title"])
        self.title_label.configure(bg=c["title"], fg=c["fg"])
        self.pin_indicator.configure(bg=c["title"], fg=c["fg"])

        for btn in (self.min_btn, self.collapse_btn):
            btn.configure(bg=c["title"], fg=c["fg"], activebackground=c["button_active"], activeforeground=c["fg"], font=("Segoe UI", 10))
        self.close_btn.configure(bg=c["title"], fg=c["fg"], activebackground=c["danger"], activeforeground="#ffffff", font=("Segoe UI", 10))

        self.style.configure("TFrame", background=c["bg"])
        self.style.configure("Panel.TFrame", background=c["panel"])
        self.style.configure("TLabel", background=c["bg"], foreground=c["fg"], font=("Segoe UI", 10))
        self.style.configure("Panel.TLabel", background=c["panel"], foreground=c["fg"], font=("Segoe UI", 10))
        self.style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"], font=("Segoe UI", 9))
        self.style.configure("PanelMuted.TLabel", background=c["panel"], foreground=c["muted"], font=("Segoe UI", 9))

        self.style.configure("TButton", background=c["button"], foreground=c["fg"], bordercolor=c["border"], lightcolor=c["button"], darkcolor=c["button"], padding=(10, 7), relief="flat", font=("Segoe UI", 9))
        self.style.map("TButton", background=[("active", c["button_active"])])
        self.style.configure("Accent.TButton", background=c["accent"], foreground="#ffffff", bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"], padding=(11, 7), relief="flat", font=("Segoe UI", 9, "bold"))
        self.style.map("Accent.TButton", background=[("active", c["accent_active"])])
        self.style.configure("Danger.TButton", background=c["danger"], foreground="#ffffff", bordercolor=c["danger"], lightcolor=c["danger"], darkcolor=c["danger"], padding=(10, 7), relief="flat")
        self.style.map("Danger.TButton", background=[("active", c["danger_active"])])

        self.style.configure("TEntry", fieldbackground=c["entry"], foreground=c["fg"], insertcolor=c["fg"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=6)
        self.style.configure("TCombobox", fieldbackground=c["entry"], foreground=c["fg"], background=c["button"], bordercolor=c["border"], arrowcolor=c["fg"], padding=5)
        self.style.map("TCombobox", fieldbackground=[("readonly", c["entry"])], foreground=[("readonly", c["fg"])])
        self.style.configure("TCheckbutton", background=c["panel"], foreground=c["fg"])
        self.style.map("TCheckbutton", background=[("active", c["panel"])])

        self.style.configure("TNotebook", background=c["bg"], borderwidth=0)
        self.style.configure("TNotebook.Tab", background=c["panel2"], foreground=c["muted"], padding=(17, 9), borderwidth=0, font=("Segoe UI", 9, "bold"))
        self.style.map("TNotebook.Tab", background=[("selected", c["panel"])], foreground=[("selected", c["fg"])])

        self.style.configure("Card.TLabelframe", background=c["panel"], bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], relief="solid", borderwidth=1)
        self.style.configure("Card.TLabelframe.Label", background=c["panel"], foreground=c["fg"], font=("Segoe UI", 10, "bold"))

        for scale in list(self.classic_scales):
            try:
                scale.configure(bg=c["panel"], fg=c["fg"], troughcolor=c["entry"], activebackground=c["accent"], highlightthickness=0)
            except Exception:
                pass

    def apply_pin_setting(self):
        pinned = bool(self.settings.get("pin_to_top", True))
        try:
            self.root.attributes("-topmost", pinned)
        except Exception:
            pass
        self.pin_indicator.configure(text="📌" if pinned else "")

    def start_drag(self, event):
        self.drag_start = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def do_drag(self, event):
        if not self.drag_start:
            return
        sx, sy, wx, wy = self.drag_start
        self.root.geometry(f"+{wx + (event.x_root - sx)}+{wy + (event.y_root - sy)}")

    def toggle_collapse(self):
        if not self.collapsed:
            self.expanded_geometry = self.root.geometry()
            self.main_area.pack_forget()
            self.root.minsize(340, 38)
            self.root.geometry(f"380x38+{self.root.winfo_x()}+{self.root.winfo_y()}")
            self.collapse_btn.configure(text="▾")
            self.collapsed = True
        else:
            self.main_area.pack(fill="both", expand=True)
            self.root.minsize(820, 560)
            self.root.geometry(self.expanded_geometry)
            self.collapse_btn.configure(text="▴")
            self.collapsed = False

    def minimize_window(self):
        # Temporarily restore native window management so iconify behaves correctly.
        # No polling loop: Windows/Tk tells us when the window is mapped again.
        try:
            self.restore_borderless_pending = True
            self.root.overrideredirect(False)
            self.root.iconify()
        except Exception:
            self.restore_borderless_pending = False

    def on_window_mapped(self, event=None):
        if not self.restore_borderless_pending:
            return
        try:
            if self.root.state() == "normal":
                self.restore_borderless_pending = False
                self.root.after_idle(self.finish_borderless_restore)
        except Exception:
            self.restore_borderless_pending = False

    def finish_borderless_restore(self):
        try:
            self.root.overrideredirect(True)
            self.apply_pin_setting()
        except Exception:
            pass

    # ---------------- GENERAL UI HELPERS ----------------

    def make_top_bar(self, parent, add_text, add_command):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=14, pady=(14, 8))
        ttk.Button(top, text=add_text, style="Accent.TButton", command=add_command).pack(side="left")
        ttk.Button(top, text="STOP ALL", style="Danger.TButton", command=self.stop_all).pack(side="right")

    def empty_state(self, parent, text):
        card = ttk.Frame(parent, style="Panel.TFrame")
        card.pack(fill="both", expand=True)
        ttk.Label(card, style="Panel.TLabel", text=text, justify="center", font=("Segoe UI", 13)).pack(expand=True)

    def make_card(self, parent, title, lines, start_cmd, edit_cmd, delete_cmd):
        card = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe")
        card.pack(fill="x", pady=7)
        left = ttk.Frame(card, style="Panel.TFrame")
        left.pack(side="left", fill="x", expand=True, padx=12, pady=10)
        for i, line in enumerate(lines):
            ttk.Label(left, style="Panel.TLabel" if i == 0 else "PanelMuted.TLabel", text=line).pack(anchor="w", pady=(0 if i == 0 else 3, 0))
        buttons = ttk.Frame(card, style="Panel.TFrame")
        buttons.pack(side="right", padx=10, pady=10)
        ttk.Button(buttons, text="START", style="Accent.TButton", command=start_cmd).pack(side="left", padx=3)
        ttk.Button(buttons, text="EDIT", command=edit_cmd).pack(side="left", padx=3)
        ttk.Button(buttons, text="DELETE", style="Danger.TButton", command=delete_cmd).pack(side="left", padx=3)

    def make_scale(self, parent, variable, max_value):
        c = self.colors()
        scale = tk.Scale(parent, from_=1, to=int(max_value), orient="horizontal", variable=variable, resolution=1, showvalue=True, bg=c["panel"], fg=c["fg"], troughcolor=c["entry"], activebackground=c["accent"], highlightthickness=0)
        self.classic_scales.append(scale)
        return scale

    def dependencies_ok(self):
        if pydirectinput is None:
            messagebox.showerror("Missing dependency", "PyDirectInput is not installed.\n\nRun:\npy -m pip install pydirectinput keyboard")
            return False
        return True

    def automation_busy(self):
        return self.worker_thread is not None and self.worker_thread.is_alive()

    def start_worker(self, name, function):
        if self.automation_busy():
            messagebox.showwarning("Already running", f'"{self.active_name or "Automation"}" is already running.')
            return
        self.stop_event.clear()
        self.active_name = name

        # No always-on keyboard hook while idle. Emergency detection exists
        # only while automation is running.
        self.install_emergency_key()
        self.root.withdraw()

        def runner():
            try:
                if self.stop_event.wait(3):
                    return
                function()
            except Exception:
                write_crash(traceback.format_exc())
                self.stop_event.set()
                self.root.after(0, lambda: messagebox.showerror("Automation error", f'"{name}" stopped because of an error.\n\nDetails were saved to:\n{CRASH_FILE}'))
            finally:
                self.remove_emergency_key_hook()
                self.active_name = None
                self.root.after(0, self.root.deiconify)
                self.root.after(5, self.apply_pin_setting)

        self.worker_thread = threading.Thread(target=runner, daemon=True)
        self.worker_thread.start()

    def stop_all(self):
        self.stop_event.set()
        self.stop_combo_children()

    def program_move(self, x, y):
        self.last_expected_pos = (int(x), int(y))
        self.ignore_mouse_until = time.perf_counter() + 0.08
        pydirectinput.moveTo(int(x), int(y))
        self.ignore_mouse_until = time.perf_counter() + 0.08

    # ---------------- CLICKER ----------------

    def build_clicker_tab(self):
        self.make_top_bar(self.clicker_tab, "+ ADD CLICKER", self.add_clicker)
        self.clicker_list_frame = ttk.Frame(self.clicker_tab)
        self.clicker_list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.refresh_clickers()

    def refresh_clickers(self):
        for w in self.clicker_list_frame.winfo_children():
            w.destroy()
        if not self.clickers:
            self.empty_state(self.clicker_list_frame, "No clickers yet.\n\nClick + ADD CLICKER to make one.")
            return
        for i, item in enumerate(self.clickers):
            self.make_card(self.clicker_list_frame, item.get("name", f"Clicker {i+1}"), [f'Coordinates: ({item["x"]}, {item["y"]})', f'Speed: {item["speed"]} CPS   •   Delay: {item["delay"]} s'], lambda idx=i: self.start_clicker(idx), lambda idx=i: self.edit_clicker(idx), lambda idx=i: self.delete_item("clicker", idx))

    def capture_position(self):
        if not self.dependencies_ok():
            return None
        self.root.withdraw()
        overlay = tk.Toplevel()
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        c = self.colors()
        overlay.configure(bg=c["panel"])
        overlay.geometry("360x120+25+25")
        label = tk.Label(overlay, font=("Segoe UI", 16, "bold"), bg=c["panel"], fg=c["fg"])
        label.pack(expand=True, fill="both")
        try:
            for n in range(5, 0, -1):
                label.config(text=f"Move mouse to target...\nCapturing in {n}")
                overlay.update()
                time.sleep(1)
            x, y = pydirectinput.position()
            label.config(text=f"Captured\nX: {x}   Y: {y}")
            overlay.update()
            time.sleep(0.55)
            return int(x), int(y)
        finally:
            try: overlay.destroy()
            except Exception: pass
            self.root.deiconify()
            self.apply_pin_setting()

    def add_clicker(self):
        pos = self.capture_position()
        if not pos:
            return
        item = {"id": uuid.uuid4().hex, "name": f"Clicker {len(self.clickers)+1}", "x": pos[0], "y": pos[1], "speed": min(10, int(self.settings["max_speed"])), "delay": 0.1}
        self.edit_clicker_data(item, new=True)

    def edit_clicker(self, idx):
        self.edit_clicker_data(dict(self.clickers[idx]), new=False, idx=idx)

    def edit_clicker_data(self, item, new=False, idx=None):
        win = self.editor_window("Clicker Settings", "570x430")
        body = self.editor_body(win)
        ttk.Label(body, style="Panel.TLabel", text="NAME", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        name_var = tk.StringVar(value=item["name"])
        ttk.Entry(body, textvariable=name_var).pack(fill="x", padx=18)

        ttk.Label(body, style="Panel.TLabel", text=f'SPEED (1 - {int(self.settings["max_speed"]):,})', font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        speed_var = tk.IntVar(value=max(1, min(int(item["speed"]), int(self.settings["max_speed"]))))
        self.make_scale(body, speed_var, self.settings["max_speed"]).pack(fill="x", padx=18)

        ttk.Label(body, style="Panel.TLabel", text="DELAY (extra seconds)", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        delay_var = tk.StringVar(value=str(item["delay"]))
        ttk.Entry(body, textvariable=delay_var).pack(fill="x", padx=18)

        coord_label = ttk.Label(body, style="PanelMuted.TLabel", text=f'Coordinates: X={item["x"]}   Y={item["y"]}')
        coord_label.pack(anchor="w", padx=18, pady=(18, 5))

        def recapture():
            win.grab_release()
            win.withdraw()
            pos = self.capture_position()
            win.deiconify()
            win.grab_set()
            if pos:
                item["x"], item["y"] = pos
                coord_label.config(text=f'Coordinates: X={item["x"]}   Y={item["y"]}')
        ttk.Button(body, text="RECAPTURE POSITION", command=recapture).pack(anchor="w", padx=18)

        def save():
            try:
                delay = float(delay_var.get())
                if delay < 0: raise ValueError
            except Exception:
                messagebox.showerror("Invalid delay", "Delay must be 0 or greater.", parent=win); return
            item["name"] = (name_var.get().strip() or "Unnamed Clicker")
            item["speed"] = int(speed_var.get())
            item["delay"] = delay
            if new: self.clickers.append(item)
            else: self.clickers[idx] = item
            save_json(CLICKERS_FILE, self.clickers)
            self.refresh_clickers(); self.refresh_combos(); win.destroy()
        ttk.Button(body, text="SAVE", style="Accent.TButton", command=save).pack(pady=20)

    def start_clicker(self, idx):
        if not self.dependencies_ok(): return
        item = dict(self.clickers[idx])
        def run():
            self.program_move(item["x"], item["y"])
            target_pos = (int(item["x"]), int(item["y"]))
            interval = (1.0 / max(1, int(item["speed"]))) + max(0.0, float(item["delay"]))
            last_mouse_check = 0.0

            while not self.stop_event.is_set():
                now = time.perf_counter()

                # Check for real user mouse movement in this same worker.
                # No second polling thread is needed for a normal clicker.
                if now - last_mouse_check >= 0.05:
                    last_mouse_check = now
                    if now >= self.ignore_mouse_until:
                        pos = fast_cursor_position()
                        if abs(pos[0] - target_pos[0]) > 4 or abs(pos[1] - target_pos[1]) > 4:
                            self.stop_event.set()
                            break

                pydirectinput.click()

                # Back-pressure timing: wait AFTER each delivered click.
                # We intentionally do not try to "catch up" missed clicks because
                # that can build a Windows/target-app input queue over long runs.
                if interval > 0:
                    self.stop_event.wait(interval)
                else:
                    time.sleep(0)
        self.start_worker(item["name"], run)

    # ---------------- PRESSER ----------------

    def build_presser_tab(self):
        self.make_top_bar(self.presser_tab, "+ ADD PRESSER", self.add_presser)
        self.presser_list_frame = ttk.Frame(self.presser_tab)
        self.presser_list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.refresh_pressers()

    def refresh_pressers(self):
        for w in self.presser_list_frame.winfo_children(): w.destroy()
        if not self.pressers:
            self.empty_state(self.presser_list_frame, "No pressers yet.\n\nClick + ADD PRESSER to make one."); return
        for i, item in enumerate(self.pressers):
            self.make_card(self.presser_list_frame, item.get("name", f"Presser {i+1}"), [f'Key: {str(item["key"]).upper()}', f'Speed: {item["speed"]} presses/sec   •   Delay: {item["delay"]} s'], lambda idx=i: self.start_presser(idx), lambda idx=i: self.edit_presser(idx), lambda idx=i: self.delete_item("presser", idx))

    def capture_key(self, parent=None):
        if keyboard is None:
            messagebox.showerror("Missing dependency", "The keyboard package is required.\n\nRun:\npy -m pip install keyboard", parent=parent)
            return None
        popup = tk.Toplevel(parent or self.root)
        popup.title("Choose Key")
        popup.geometry("390x150")
        popup.transient(parent or self.root)
        popup.grab_set()
        body = ttk.Frame(popup, style="Panel.TFrame"); body.pack(fill="both", expand=True, padx=12, pady=12)
        label = ttk.Label(body, style="Panel.TLabel", text="Press the key you want to use...", font=("Segoe UI", 12, "bold")); label.pack(expand=True)
        result = {"key": None}
        done = threading.Event()
        def worker():
            try:
                while not done.is_set():
                    ev = keyboard.read_event(suppress=False)
                    if ev.event_type == keyboard.KEY_DOWN:
                        result["key"] = ev.name
                        done.set()
                        popup.after(0, popup.destroy)
                        return
            except Exception:
                write_crash(traceback.format_exc()); done.set(); popup.after(0, popup.destroy)
        threading.Thread(target=worker, daemon=True).start()
        popup.wait_window()
        done.set()
        return result["key"]

    def add_presser(self):
        key = self.capture_key(self.root)
        if not key: return
        item = {"id": uuid.uuid4().hex, "name": f"Presser {len(self.pressers)+1}", "key": key, "speed": min(10, int(self.settings["max_speed"])), "delay": 0.1}
        self.edit_presser_data(item, new=True)

    def edit_presser(self, idx):
        self.edit_presser_data(dict(self.pressers[idx]), new=False, idx=idx)

    def edit_presser_data(self, item, new=False, idx=None):
        win = self.editor_window("Presser Settings", "570x420")
        body = self.editor_body(win)
        ttk.Label(body, style="Panel.TLabel", text="NAME", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        name_var = tk.StringVar(value=item["name"]); ttk.Entry(body, textvariable=name_var).pack(fill="x", padx=18)

        ttk.Label(body, style="Panel.TLabel", text="KEY", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        key_var = tk.StringVar(value=item["key"])
        key_btn = ttk.Button(body, text=str(item["key"]).upper()); key_btn.pack(anchor="w", padx=18)
        def change_key():
            k = self.capture_key(win)
            if k:
                key_var.set(k); key_btn.config(text=k.upper())
        key_btn.config(command=change_key)

        ttk.Label(body, style="Panel.TLabel", text=f'SPEED (1 - {int(self.settings["max_speed"]):,})', font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        speed_var = tk.IntVar(value=max(1, min(int(item["speed"]), int(self.settings["max_speed"]))))
        self.make_scale(body, speed_var, self.settings["max_speed"]).pack(fill="x", padx=18)

        ttk.Label(body, style="Panel.TLabel", text="DELAY (extra seconds)", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        delay_var = tk.StringVar(value=str(item["delay"])); ttk.Entry(body, textvariable=delay_var).pack(fill="x", padx=18)

        def save():
            try:
                delay = float(delay_var.get()); assert delay >= 0
            except Exception:
                messagebox.showerror("Invalid delay", "Delay must be 0 or greater.", parent=win); return
            if key_var.get().lower() == str(self.settings.get("emergency_stop", "f12")).lower():
                if not messagebox.askyesno("Emergency key conflict", "This presser uses the emergency-stop key. Starting it will immediately trigger emergency stop.\n\nSave anyway?", parent=win): return
            item["name"] = name_var.get().strip() or "Unnamed Presser"
            item["key"] = key_var.get(); item["speed"] = int(speed_var.get()); item["delay"] = delay
            if new: self.pressers.append(item)
            else: self.pressers[idx] = item
            save_json(PRESSERS_FILE, self.pressers)
            self.refresh_pressers(); self.refresh_combos(); win.destroy()
        ttk.Button(body, text="SAVE", style="Accent.TButton", command=save).pack(pady=22)

    def start_presser(self, idx):
        if not self.dependencies_ok(): return
        item = dict(self.pressers[idx])
        def run():
            interval = (1.0 / max(1, int(item["speed"]))) + max(0.0, float(item["delay"]))
            while not self.stop_event.is_set():
                pydirectinput.press(item["key"])
                if interval > 0:
                    self.stop_event.wait(interval)
                else:
                    time.sleep(0)
        self.start_worker(item["name"], run)

    # ---------------- TYPER ----------------

    def build_typer_tab(self):
        self.make_top_bar(self.typer_tab, "+ ADD TYPER", self.add_typer)
        self.typer_list_frame = ttk.Frame(self.typer_tab)
        self.typer_list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.refresh_typers()

    def refresh_typers(self):
        for w in self.typer_list_frame.winfo_children(): w.destroy()
        if not self.typers:
            self.empty_state(self.typer_list_frame, "No typers yet.\n\nClick + ADD TYPER to make one."); return
        for i, item in enumerate(self.typers):
            self.make_card(self.typer_list_frame, item.get("name", f"Typer {i+1}"), [f'Text: {short_text(item.get("text", ""))}', f'Speed: {item["speed"]} chars/sec   •   Delay: {item["delay"]} s'], lambda idx=i: self.start_typer(idx), lambda idx=i: self.edit_typer(idx), lambda idx=i: self.delete_item("typer", idx))

    def add_typer(self):
        item = {"id": uuid.uuid4().hex, "name": f"Typer {len(self.typers)+1}", "text": "Hello", "speed": min(10, int(self.settings["max_speed"])), "delay": 0.1}
        self.edit_typer_data(item, new=True)

    def edit_typer(self, idx):
        self.edit_typer_data(dict(self.typers[idx]), new=False, idx=idx)

    def edit_typer_data(self, item, new=False, idx=None):
        win = self.editor_window("Typer Settings", "620x540")
        body = self.editor_body(win)
        ttk.Label(body, style="Panel.TLabel", text="NAME", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        name_var = tk.StringVar(value=item["name"]); ttk.Entry(body, textvariable=name_var).pack(fill="x", padx=18)

        ttk.Label(body, style="Panel.TLabel", text="TEXT", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        c = self.colors()
        text_box = tk.Text(body, height=8, wrap="word", bg=c["entry"], fg=c["fg"], insertbackground=c["fg"], relief="flat", highlightthickness=1, highlightbackground=c["border"], highlightcolor=c["accent"])
        text_box.pack(fill="both", expand=False, padx=18); text_box.insert("1.0", item.get("text", ""))

        ttk.Label(body, style="Panel.TLabel", text=f'SPEED (1 - {int(self.settings["max_speed"]):,} chars/sec)', font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        speed_var = tk.IntVar(value=max(1, min(int(item["speed"]), int(self.settings["max_speed"]))))
        self.make_scale(body, speed_var, self.settings["max_speed"]).pack(fill="x", padx=18)

        ttk.Label(body, style="Panel.TLabel", text="DELAY (after each full message)", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=18, pady=(18, 4))
        delay_var = tk.StringVar(value=str(item["delay"])); ttk.Entry(body, textvariable=delay_var).pack(fill="x", padx=18)

        def save():
            try:
                delay = float(delay_var.get()); assert delay >= 0
            except Exception:
                messagebox.showerror("Invalid delay", "Delay must be 0 or greater.", parent=win); return
            item["name"] = name_var.get().strip() or "Unnamed Typer"
            item["text"] = text_box.get("1.0", "end-1c")
            item["speed"] = int(speed_var.get()); item["delay"] = delay
            if new: self.typers.append(item)
            else: self.typers[idx] = item
            save_json(TYPERS_FILE, self.typers)
            self.refresh_typers(); self.refresh_combos(); win.destroy()
        ttk.Button(body, text="SAVE", style="Accent.TButton", command=save).pack(pady=20)

    def type_text_once(self, item):
        interval = 1.0 / max(1, int(item["speed"]))
        for ch in item.get("text", ""):
            if self.stop_event.is_set(): return
            if ch == "\n": pydirectinput.press("enter")
            elif ch == "\t": pydirectinput.press("tab")
            else: pydirectinput.write(ch)
            if interval > 0: self.stop_event.wait(interval)

    def start_typer(self, idx):
        if not self.dependencies_ok(): return
        item = dict(self.typers[idx])
        def run():
            while not self.stop_event.is_set():
                self.type_text_once(item)
                if self.stop_event.is_set(): return
                self.stop_event.wait(max(0.0, float(item["delay"])))
        self.start_worker(item["name"], run)

    # ---------------- COMBO ----------------

    def build_combo_tab(self):
        self.make_top_bar(self.combo_tab, "+ ADD COMBO", self.add_combo)
        self.combo_list_frame = ttk.Frame(self.combo_tab)
        self.combo_list_frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.refresh_combos()

    def refresh_combos(self):
        for w in self.combo_list_frame.winfo_children(): w.destroy()
        if not self.combos:
            self.empty_state(self.combo_list_frame, "No combos yet.\n\nBuild Scratch-style action chains from your saved tools."); return
        for i, item in enumerate(self.combos):
            blocks = item.get("blocks", [])
            self.make_card(self.combo_list_frame, item.get("name", f"Combo {i+1}"), [f'{len(blocks)} block(s)', self.combo_preview(blocks)], lambda idx=i: self.start_combo(idx), lambda idx=i: self.edit_combo(idx), lambda idx=i: self.delete_item("combo", idx))

    def combo_preview(self, blocks):
        if not blocks: return "Empty combo"
        return " → ".join(self.block_label(b) for b in blocks[:4]) + (" → ..." if len(blocks) > 4 else "")

    def find_by_id(self, items, target_id):
        return next((x for x in items if x.get("id") == target_id), None)

    def block_label(self, block):
        t = block.get("type")
        if t == "clicker":
            x = self.find_by_id(self.clickers, block.get("target_id")); return f'CLICK {x["name"]}' if x else "CLICK [missing]"
        if t == "presser":
            x = self.find_by_id(self.pressers, block.get("target_id")); return f'PRESS {x["name"]}' if x else "PRESS [missing]"
        if t == "typer":
            x = self.find_by_id(self.typers, block.get("target_id")); return f'TYPE {x["name"]}' if x else "TYPE [missing]"
        if t == "delay": return f'DELAY {block.get("seconds", 0)}s'
        if t == "loop_start": return "LOOP FOREVER" if int(block.get("count", 0)) == 0 else f'LOOP x{int(block.get("count", 1))}'
        if t == "loop_end": return "END LOOP"
        if t == "stop_clicker": return "STOP CLICKER"
        if t == "stop_presser": return "STOP PRESSER"
        if t == "stop_typer": return "STOP TYPER"
        if t == "stop": return "STOP COMBO"
        return "UNKNOWN"

    def add_combo(self):
        item = {"id": uuid.uuid4().hex, "name": f"Combo {len(self.combos)+1}", "blocks": []}
        self.edit_combo_data(item, new=True)

    def edit_combo(self, idx):
        item = json.loads(json.dumps(self.combos[idx]))
        self.edit_combo_data(item, new=False, idx=idx)

    def edit_combo_data(self, item, new=False, idx=None):
        win = self.editor_window("Combo Builder", "920x700")
        body = self.editor_body(win)
        top = ttk.Frame(body, style="Panel.TFrame"); top.pack(fill="x", padx=14, pady=(14, 8))
        ttk.Label(top, style="Panel.TLabel", text="NAME", font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 8))
        name_var = tk.StringVar(value=item["name"]); ttk.Entry(top, textvariable=name_var, width=34).pack(side="left")

        workspace = ttk.Frame(body, style="Panel.TFrame"); workspace.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        palette = ttk.LabelFrame(workspace, text="BLOCKS", style="Card.TLabelframe"); palette.pack(side="left", fill="y", padx=(0, 10))
        seqframe = ttk.LabelFrame(workspace, text="COMBO", style="Card.TLabelframe"); seqframe.pack(side="left", fill="both", expand=True)

        c = self.colors()
        listbox = tk.Listbox(seqframe, bg=c["entry"], fg=c["fg"], selectbackground=c["accent"], selectforeground="#ffffff", relief="flat", highlightthickness=1, highlightbackground=c["border"], font=("Consolas", 10))
        listbox.pack(fill="both", expand=True, padx=10, pady=10)

        blocks = item.setdefault("blocks", [])
        def refresh_list(select=None):
            listbox.delete(0, "end")
            indent = 0
            for n, b in enumerate(blocks):
                if b.get("type") == "loop_end": indent = max(0, indent - 1)
                listbox.insert("end", ("    " * indent) + self.block_label(b))
                if b.get("type") == "loop_start": indent += 1
            if select is not None and 0 <= select < len(blocks):
                listbox.selection_set(select); listbox.see(select)

        def choose_target(kind):
            data = {"clicker": self.clickers, "presser": self.pressers, "typer": self.typers}[kind]
            if not data:
                messagebox.showwarning("Nothing to add", f"Create a {kind.upper()} first.", parent=win); return
            chooser = tk.Toplevel(win); chooser.title(f"Choose {kind.title()}"); chooser.geometry("420x380"); chooser.transient(win); chooser.grab_set()
            cb = ttk.Frame(chooser, style="Panel.TFrame"); cb.pack(fill="both", expand=True, padx=12, pady=12)
            ttk.Label(cb, style="Panel.TLabel", text=f"Choose an existing {kind}:", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
            for x in data:
                ttk.Button(cb, text=x["name"], command=lambda target=x: (blocks.append({"type": kind, "target_id": target["id"]}), refresh_list(len(blocks)-1), chooser.destroy())).pack(fill="x", pady=3)

        ttk.Button(palette, text="+ CLICKER", command=lambda: choose_target("clicker")).pack(fill="x", padx=10, pady=(10, 4))
        ttk.Button(palette, text="+ PRESSER", command=lambda: choose_target("presser")).pack(fill="x", padx=10, pady=4)
        ttk.Button(palette, text="+ TYPER", command=lambda: choose_target("typer")).pack(fill="x", padx=10, pady=4)

        def add_delay():
            v = simpledialog.askfloat("Delay", "Delay in seconds:", initialvalue=0.5, minvalue=0.0, parent=win)
            if v is not None: blocks.append({"type": "delay", "seconds": float(v)}); refresh_list(len(blocks)-1)
        ttk.Button(palette, text="+ DELAY", command=add_delay).pack(fill="x", padx=10, pady=(12, 4))

        def add_loop():
            v = simpledialog.askinteger("Loop", "How many times?", initialvalue=2, minvalue=1, maxvalue=1000000, parent=win)
            if v is not None: blocks.append({"type": "loop_start", "count": int(v)}); refresh_list(len(blocks)-1)
        ttk.Button(palette, text="+ LOOP", command=add_loop).pack(fill="x", padx=10, pady=4)
        ttk.Button(palette, text="+ LOOP FOREVER", command=lambda: (blocks.append({"type": "loop_start", "count": 0}), refresh_list(len(blocks)-1))).pack(fill="x", padx=10, pady=4)
        ttk.Button(palette, text="+ END LOOP", command=lambda: (blocks.append({"type": "loop_end"}), refresh_list(len(blocks)-1))).pack(fill="x", padx=10, pady=4)

        ttk.Button(palette, text="+ STOP CLICKER", command=lambda: (blocks.append({"type": "stop_clicker"}), refresh_list(len(blocks)-1))).pack(fill="x", padx=10, pady=(12, 4))
        ttk.Button(palette, text="+ STOP PRESSER", command=lambda: (blocks.append({"type": "stop_presser"}), refresh_list(len(blocks)-1))).pack(fill="x", padx=10, pady=4)
        ttk.Button(palette, text="+ STOP TYPER", command=lambda: (blocks.append({"type": "stop_typer"}), refresh_list(len(blocks)-1))).pack(fill="x", padx=10, pady=4)
        ttk.Button(palette, text="+ STOP COMBO", command=lambda: (blocks.append({"type": "stop"}), refresh_list(len(blocks)-1))).pack(fill="x", padx=10, pady=(4, 10))

        controls = ttk.Frame(seqframe, style="Panel.TFrame"); controls.pack(fill="x", padx=10, pady=(0, 10))
        def selected():
            s = listbox.curselection(); return s[0] if s else None
        def move(delta):
            i = selected()
            if i is None: return
            j = i + delta
            if 0 <= j < len(blocks):
                blocks[i], blocks[j] = blocks[j], blocks[i]; refresh_list(j)
        def remove():
            i = selected()
            if i is not None: blocks.pop(i); refresh_list(min(i, len(blocks)-1))
        ttk.Button(controls, text="↑ MOVE UP", command=lambda: move(-1)).pack(side="left", padx=3)
        ttk.Button(controls, text="↓ MOVE DOWN", command=lambda: move(1)).pack(side="left", padx=3)
        ttk.Button(controls, text="DELETE BLOCK", style="Danger.TButton", command=remove).pack(side="left", padx=3)

        refresh_list()

        def save():
            if not self.validate_combo(blocks, win): return
            item["name"] = name_var.get().strip() or "Unnamed Combo"
            if new: self.combos.append(item)
            else: self.combos[idx] = item
            save_json(COMBOS_FILE, self.combos); self.refresh_combos(); win.destroy()
        ttk.Button(body, text="SAVE COMBO", style="Accent.TButton", command=save).pack(pady=(0, 14))

    def validate_combo(self, blocks, parent=None):
        depth = 0
        for b in blocks:
            if b.get("type") == "loop_start": depth += 1
            elif b.get("type") == "loop_end":
                depth -= 1
                if depth < 0:
                    messagebox.showerror("Loop error", "END LOOP appears before a LOOP block.", parent=parent); return False
        if depth != 0:
            messagebox.showerror("Loop error", "Every LOOP needs a matching END LOOP.", parent=parent); return False
        return True

    def start_combo(self, idx):
        if not self.dependencies_ok(): return
        item = json.loads(json.dumps(self.combos[idx]))
        if not self.validate_combo(item.get("blocks", []), self.root): return
        def run(): self.execute_combo(item.get("blocks", []))
        self.start_worker(item["name"], run)

    def combo_wait(self, seconds, local_stop=None):
        """Interruptible wait that responds to both global and per-tool stops."""
        seconds = max(0.0, float(seconds))
        end = time.perf_counter() + seconds
        while not self.stop_event.is_set():
            if local_stop is not None and local_stop.is_set():
                return True
            left = end - time.perf_counter()
            if left <= 0:
                return False
            # Small capped waits avoid both busy-spinning and sluggish stop response.
            if self.stop_event.wait(min(left, 0.025)):
                return True
        return True

    def combo_children_running(self):
        with self.combo_child_lock:
            self.combo_child_threads = [t for t in self.combo_child_threads if t.is_alive()]
            return bool(self.combo_child_threads)

    def stop_combo_type(self, kind):
        with self.combo_child_lock:
            events = list(self.combo_child_stops.get(kind, []))
            self.combo_child_stops[kind] = []
        for ev in events:
            ev.set()

    def stop_combo_children(self):
        for kind in ("clicker", "presser", "typer"):
            self.stop_combo_type(kind)

    def start_combo_child(self, kind, item):
        local_stop = threading.Event()

        with self.combo_child_lock:
            self.combo_child_stops[kind].append(local_stop)

        def should_stop():
            return self.stop_event.is_set() or local_stop.is_set()

        def cleanup():
            with self.combo_child_lock:
                try:
                    self.combo_child_stops[kind].remove(local_stop)
                except ValueError:
                    pass
                self.combo_child_threads[:] = [
                    t for t in self.combo_child_threads
                    if t is not threading.current_thread() and t.is_alive()
                ]

        def run_clicker():
            interval = (1.0 / max(1, int(item["speed"]))) + max(0.0, float(item["delay"]))
            self.program_move(item["x"], item["y"])
            target_pos = (int(item["x"]), int(item["y"]))
            last_mouse_check = 0.0

            while not should_stop():
                now = time.perf_counter()
                if now - last_mouse_check >= 0.05:
                    last_mouse_check = now
                    if now >= self.ignore_mouse_until:
                        pos = fast_cursor_position()
                        if abs(pos[0] - target_pos[0]) > 4 or abs(pos[1] - target_pos[1]) > 4:
                            self.stop_event.set()
                            return

                self.last_expected_pos = target_pos
                pydirectinput.click()

                if interval > 0:
                    if self.combo_wait(interval, local_stop):
                        return
                else:
                    time.sleep(0)

        def run_presser():
            interval = (1.0 / max(1, int(item["speed"]))) + max(0.0, float(item["delay"]))
            while not should_stop():
                pydirectinput.press(item["key"])
                if interval > 0:
                    if self.combo_wait(interval, local_stop):
                        return
                else:
                    time.sleep(0)

        def run_typer():
            interval = 1.0 / max(1, int(item["speed"]))
            text = item.get("text", "")
            while not should_stop():
                for ch in text:
                    if should_stop():
                        return
                    if ch == "\n":
                        pydirectinput.press("enter")
                    elif ch == "\t":
                        pydirectinput.press("tab")
                    else:
                        pydirectinput.write(ch)
                    if interval > 0 and self.combo_wait(interval, local_stop):
                        return
                if self.combo_wait(max(0.0, float(item["delay"])), local_stop):
                    return

        target = {
            "clicker": run_clicker,
            "presser": run_presser,
            "typer": run_typer,
        }[kind]

        def runner():
            try:
                target()
            except Exception:
                write_crash(traceback.format_exc())
                self.stop_event.set()
            finally:
                cleanup()

        thread = threading.Thread(
            target=runner,
            daemon=True,
            name=f"Combo-{kind}-{item.get('name', kind)}"
        )
        with self.combo_child_lock:
            self.combo_child_threads.append(thread)
        thread.start()

    def execute_combo(self, blocks):
        pc = 0
        loop_stack = []

        try:
            while pc < len(blocks) and not self.stop_event.is_set():
                b = blocks[pc]
                t = b.get("type")

                if t == "clicker":
                    x = self.find_by_id(self.clickers, b.get("target_id"))
                    if x:
                        self.start_combo_child("clicker", dict(x))

                elif t == "presser":
                    x = self.find_by_id(self.pressers, b.get("target_id"))
                    if x:
                        self.start_combo_child("presser", dict(x))

                elif t == "typer":
                    x = self.find_by_id(self.typers, b.get("target_id"))
                    if x:
                        self.start_combo_child("typer", dict(x))

                elif t == "stop_clicker":
                    self.stop_combo_type("clicker")

                elif t == "stop_presser":
                    self.stop_combo_type("presser")

                elif t == "stop_typer":
                    self.stop_combo_type("typer")

                elif t == "delay":
                    self.combo_wait(max(0.0, float(b.get("seconds", 0))))

                elif t == "loop_start":
                    count = int(b.get("count", 1))
                    loop_stack.append({
                        "start": pc,
                        "remaining": None if count == 0 else count
                    })

                elif t == "loop_end":
                    if loop_stack:
                        top = loop_stack[-1]
                        if top["remaining"] is None:
                            pc = top["start"]
                        elif top["remaining"] > 1:
                            top["remaining"] -= 1
                            pc = top["start"]
                        else:
                            loop_stack.pop()

                elif t == "stop":
                    self.stop_combo_children()
                    return

                pc += 1

                # A tiny yield keeps huge zero-delay LOOPs from monopolizing Python.
                if pc % 32 == 0:
                    time.sleep(0)

            # If the sequence ends while a started clicker/presser/typer is still
            # running, the combo remains alive until STOP blocks or global stop.
            while self.combo_children_running() and not self.stop_event.is_set():
                self.stop_event.wait(0.05)

        finally:
            self.stop_combo_children()

    # ---------------- DELETE / EDITOR HELPERS ----------------

    def delete_item(self, kind, idx):
        mapping = {
            "clicker": (self.clickers, CLICKERS_FILE, self.refresh_clickers),
            "presser": (self.pressers, PRESSERS_FILE, self.refresh_pressers),
            "typer": (self.typers, TYPERS_FILE, self.refresh_typers),
            "combo": (self.combos, COMBOS_FILE, self.refresh_combos),
        }
        items, path, refresh = mapping[kind]
        if not (0 <= idx < len(items)): return
        name = items[idx].get("name", kind.title())
        if messagebox.askyesno("Delete", f'Delete "{name}"?'):
            items.pop(idx); save_json(path, items); refresh(); self.refresh_combos()

    def editor_window(self, title, geometry):
        win = tk.Toplevel(self.root)
        win.title(title); win.geometry(geometry); win.transient(self.root); win.grab_set()
        c = self.colors(); win.configure(bg=c["bg"])
        return win

    def editor_body(self, win):
        body = ttk.Frame(win, style="Panel.TFrame")
        body.pack(fill="both", expand=True, padx=14, pady=14)
        return body

    # ---------------- SETTINGS ----------------

    def build_settings_tab(self):
        card = ttk.Frame(self.settings_tab, style="Panel.TFrame")
        card.pack(fill="both", expand=True, padx=14, pady=14)
        inner = ttk.Frame(card, style="Panel.TFrame")
        inner.pack(anchor="nw", padx=24, pady=24)

        ttk.Label(inner, style="Panel.TLabel", text="THEME", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", pady=11)
        self.theme_var = tk.StringVar(value=self.settings["theme"])
        combo = ttk.Combobox(inner, state="readonly", textvariable=self.theme_var, values=["dark", "light"], width=20)
        combo.grid(row=0, column=1, sticky="w", padx=20); combo.bind("<<ComboboxSelected>>", lambda e: self.change_theme())

        ttk.Label(inner, style="Panel.TLabel", text="PIN TO TOP", font=("Segoe UI", 9, "bold")).grid(row=1, column=0, sticky="w", pady=11)
        self.pin_var = tk.BooleanVar(value=bool(self.settings.get("pin_to_top", True)))
        ttk.Checkbutton(inner, text="ON", variable=self.pin_var, command=self.change_pin).grid(row=1, column=1, sticky="w", padx=20)

        ttk.Label(inner, style="Panel.TLabel", text="EMERGENCY STOP", font=("Segoe UI", 9, "bold")).grid(row=2, column=0, sticky="w", pady=11)
        self.emergency_btn = ttk.Button(inner, text=str(self.settings["emergency_stop"]).upper(), command=self.change_emergency_key)
        self.emergency_btn.grid(row=2, column=1, sticky="w", padx=20)

        ttk.Label(inner, style="Panel.TLabel", text="EMERGENCY KEY EXCEPTION", font=("Segoe UI", 9, "bold")).grid(row=3, column=0, sticky="w", pady=11)
        self.exception_var = tk.BooleanVar(value=bool(self.settings["emergency_key_exception"]))
        ttk.Checkbutton(inner, text="ON", variable=self.exception_var, command=self.save_exception).grid(row=3, column=1, sticky="w", padx=20)

        ttk.Label(inner, style="Panel.TLabel", text="MAX SPEED", font=("Segoe UI", 9, "bold")).grid(row=4, column=0, sticky="w", pady=11)
        self.max_speed_var = tk.StringVar(value=str(self.settings["max_speed"]))
        ttk.Entry(inner, textvariable=self.max_speed_var, width=22).grid(row=4, column=1, sticky="w", padx=20)
        ttk.Button(inner, text="APPLY", command=self.apply_max_speed).grid(row=4, column=2, padx=6)
        ttk.Label(inner, style="PanelMuted.TLabel", text="Allowed: 1 - 100,000\nAbove 1,000 can heavily load the system.", justify="left").grid(row=5, column=1, columnspan=2, sticky="w", padx=20, pady=(0, 10))

    def change_theme(self):
        self.settings["theme"] = self.theme_var.get(); save_json(SETTINGS_FILE, self.settings); self.apply_theme()
        self.refresh_clickers(); self.refresh_pressers(); self.refresh_typers(); self.refresh_combos()

    def change_pin(self):
        self.settings["pin_to_top"] = bool(self.pin_var.get()); save_json(SETTINGS_FILE, self.settings); self.apply_pin_setting()

    def save_exception(self):
        self.settings["emergency_key_exception"] = bool(self.exception_var.get()); save_json(SETTINGS_FILE, self.settings)

    def change_emergency_key(self):
        if keyboard is None:
            messagebox.showerror("Missing dependency", "The keyboard package is required.\n\nRun:\npy -m pip install keyboard"); return
        self.emergency_btn.config(text="PRESS A KEY...")
        def worker():
            try:
                while True:
                    ev = keyboard.read_event(suppress=False)
                    if ev.event_type == keyboard.KEY_DOWN:
                        key = ev.name; break
                self.settings["emergency_stop"] = key; save_json(SETTINGS_FILE, self.settings)
                self.root.after(0, lambda: self.emergency_btn.config(text=key.upper()))
                if self.automation_busy():
                    self.root.after(0, self.install_emergency_key)
            except Exception:
                write_crash(traceback.format_exc())
                self.root.after(0, lambda: self.emergency_btn.config(text=str(self.settings.get("emergency_stop", "f12")).upper()))
        threading.Thread(target=worker, daemon=True).start()

    def install_emergency_key(self):
        if keyboard is None:
            return
        self.remove_emergency_key_hook()
        key = self.settings.get("emergency_stop", "f12")

        def callback(event):
            if event.event_type != keyboard.KEY_DOWN:
                return
            self.stop_event.set()
            if self.settings.get("emergency_key_exception", False):
                os._exit(99)

        try:
            self.emergency_hook = keyboard.hook_key(key, callback, suppress=False)
        except Exception:
            write_crash(traceback.format_exc())
            self.emergency_hook = None

    def remove_emergency_key_hook(self):
        if keyboard is None:
            self.emergency_hook = None
            return
        try:
            if self.emergency_hook is not None:
                keyboard.unhook(self.emergency_hook)
        except Exception:
            pass
        self.emergency_hook = None

    def apply_max_speed(self):
        try: value = int(self.max_speed_var.get().strip())
        except Exception:
            messagebox.showerror("Invalid value", "MAX SPEED must be a whole number from 1 to 100,000."); return
        if not 1 <= value <= 100000:
            messagebox.showerror("Invalid value", "MAX SPEED must be between 1 and 100,000."); return
        if value == 100000:
            warnings = [
                "MAXIMUM SPEED SELECTED.",
                "This may cause extreme lag and very high CPU usage.",
                "This may cause extensive heating, application freezes, or crashes.",
                "100,000 is only a target speed; Windows and target programs may not process that many inputs."
            ]
            for msg in warnings:
                if not messagebox.askyesno("MAX SPEED WARNING", msg + "\n\nContinue?"):
                    self.max_speed_var.set(str(self.settings["max_speed"])); return
        elif value >= 10000:
            if not messagebox.askyesno("VERY HIGH SPEED WARNING", "10,000+ target inputs/sec can cause severe lag, high CPU load, heating, freezes, and crashes.\n\nContinue?"):
                self.max_speed_var.set(str(self.settings["max_speed"])); return
        elif value > 1000:
            if not messagebox.askyesno("HIGH SPEED WARNING", "Speeds above 1,000 may cause lag, extensive CPU usage/heating, or application crashes.\n\nContinue?"):
                self.max_speed_var.set(str(self.settings["max_speed"])); return
        self.settings["max_speed"] = value; save_json(SETTINGS_FILE, self.settings)
        messagebox.showinfo("Saved", f"MAX SPEED set to {value:,}.\n\nNew/edit screens will use this slider limit.")

    # ---------------- CLOSE ----------------

    def on_close(self):
        self.stop_event.set()
        try:
            if keyboard is not None: keyboard.unhook_all()
        except Exception: pass
        self.root.destroy()


def show_fatal_error(text):
    write_crash(text)
    try:
        r = tk.Tk(); r.withdraw()
        messagebox.showerror("Ultimate Key Presser failed to start", f"The program hit an error instead of silently closing.\n\nDetails were saved to:\n{CRASH_FILE}")
        r.destroy()
    except Exception:
        pass


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        show_fatal_error(traceback.format_exc())
