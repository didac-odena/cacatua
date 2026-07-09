"""Local voice-to-text transcriber with a toggle hotkey and floating UI.

Press HOTKEY to start recording, press again to stop, transcribe and paste.
"""
import atexit
import ctypes
import functools
import json
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import winsound
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent / "whisper.log"
try:
    _log_file = open(_LOG_PATH, "a", encoding="utf-8", buffering=1)
    sys.stdout = _log_file
    sys.stderr = _log_file
    print(f"\n=== startup {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
except Exception:
    pass

if sys.platform == "win32":
    _nvidia = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    for _sub in ("cublas", "cudnn", "cuda_nvrtc"):
        _dll_dir = _nvidia / _sub / "bin"
        if _dll_dir.is_dir():
            os.add_dll_directory(str(_dll_dir))

from ctypes import wintypes

import keyboard
import numpy as np
import pyperclip
import pystray
import sounddevice as sd
from faster_whisper import WhisperModel
from PIL import Image, ImageDraw

HOTKEY = "f9"
EXIT_HOTKEY = "ctrl+alt+q"
HOTKEY_VK = 0x78  # VK_F9
EXIT_HOTKEY_VK = 0x51  # VK_Q
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID_TOGGLE = 1
HOTKEY_ID_EXIT = 2
SAMPLE_RATE = 16000
CHANNELS = 1
LANGUAGE = "es"
AUTO_PASTE = True
DEVICE = "cuda"
COMPUTE_TYPE = "int8_float32"
BEAM_SIZE = 1

try:
    import ctranslate2 as _ct2
    if _ct2.get_cuda_device_count() == 0:
        DEVICE = "cpu"
        COMPUTE_TYPE = "int8"
except Exception:
    DEVICE = "cpu"
    COMPUTE_TYPE = "int8"

AVAILABLE_MODELS = ("small", "medium")
DEFAULT_MODEL = "small"

INITIAL_PROMPT = (
    "Hola, ¿qué tal? Estoy dictando al ordenador en español. "
    "A veces hablo rápido; otras veces me paro a pensar. "
    "Quiero que la puntuación sea correcta: comas, puntos, "
    "signos de interrogación como ¿esto? y exclamaciones "
    "como ¡así! También puntos y comas; y algún guion, claro."
)

VAD_PARAMETERS = {
    "min_silence_duration_ms": 4000,
    "speech_pad_ms": 600,
}

HALLUCINATION_PATTERNS = [
    re.compile(
        r"[¡!¿?.,\s]*(?:por favor,?\s+)?(?:no olvides\s+|no te olvides de\s+)?"
        r"suscr[íi]b(?:ete|anse|ase|ense)(?:\s+(?:al\s+)?canal)?[¡!¿?.,\s]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"[¡!¿?.,\s]*(?:muchas\s+)?gracias (?:por (?:ver|vernos|haber visto|"
        r"acompañarnos)|a todos)(?:\s+el\s+v[íi]deo)?[¡!¿?.,\s]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"[¡!¿?.,\s]*subt[íi]tulos (?:realizados\s+)?(?:por (?:la comunidad "
        r"de\s+)?amara\.org|en español por)[¡!¿?.,\s]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"[¡!¿?.,\s]*(?:dale(?:le)?|dejad(?:nos)?|denos)\s+(?:un\s+|a\s+)?"
        r"like[¡!¿?.,\s]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"[¡!¿?.,\s]*hasta (?:el |la )?(?:próximo|próxima|siguiente|pronto)"
        r"(?:\s+v[íi]deo)?[¡!¿?.,\s]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"[¡!¿?.,\s]*nos vemos(?:\s+(?:en (?:el|la))?\s*(?:próximo|próxima)"
        r"(?:\s+v[íi]deo)?)?[¡!¿?.,\s]*$",
        re.IGNORECASE,
    ),
]


def strip_hallucinations(text: str) -> str:
    changed = True
    while changed and text:
        changed = False
        for pattern in HALLUCINATION_PATTERNS:
            new_text = pattern.sub("", text).rstrip()
            if new_text != text:
                text = new_text
                changed = True
    return text.strip()

WIN_W, WIN_H = 180, 38
MARGIN_RIGHT = 20
MARGIN_BOTTOM = 70

BG = "#101014"
FG = "#f0f0f5"
FG_MUTED = "#8a8a95"
ACCENT = "#ff3366"
ACCENT_WARN = "#ffaa33"


def _scale_color(hex_color: str, factor: float) -> str:
    """Blend a #rrggbb color towards black by factor (0..1)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, min(255, int(c * factor))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


ACCENT_DIM = _scale_color(ACCENT, 0.35)

TRAY_TOOLTIP = "Whisper — F9 para dictar"

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
PID_PATH = Path(__file__).resolve().parent / "whisper.pid"
RESTART_SCRIPT_PATH = Path(__file__).resolve().parent / "restart.ps1"

SOUNDS_ENABLED = True


def beep(frequency: int, duration_ms: int) -> None:
    if SOUNDS_ENABLED:
        winsound.Beep(frequency, duration_ms)

PREFERRED_HOSTAPIS = ("Windows WASAPI", "MME", "Windows DirectSound")

SINGLE_INSTANCE_MUTEX = "Local\\WhisperToggleToTextMutex"


def acquire_single_instance_lock() -> bool:
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.windll.kernel32
    ERROR_ALREADY_EXISTS = 183
    handle = kernel32.CreateMutexW(None, True, SINGLE_INSTANCE_MUTEX)
    if not handle:
        return True
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    # Intentionally leak the handle so the mutex lives for the process lifetime.
    return True


def write_pid_file() -> None:
    try:
        PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        return
    atexit.register(_remove_pid_file)


def _remove_pid_file() -> None:
    try:
        PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def list_input_devices() -> list[tuple[int, str]]:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    for hostapi_name in PREFERRED_HOSTAPIS:
        idx = next(
            (i for i, h in enumerate(hostapis) if h["name"] == hostapi_name),
            None,
        )
        if idx is None:
            continue
        result = [
            (i, d["name"])
            for i, d in enumerate(devices)
            if d["max_input_channels"] > 0 and d["hostapi"] == idx
        ]
        if result:
            return result
    return [
        (i, d["name"])
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0
    ]


def find_device_index(name: str | None) -> int | None:
    if not name:
        return None
    for idx, dev_name in list_input_devices():
        if dev_name == name:
            return idx
    return None


def _wasapi_auto_convert_settings(device_index: int | None):
    try:
        info = sd.query_devices(device_index if device_index is not None else sd.default.device[0])
        hostapi_name = sd.query_hostapis(info["hostapi"])["name"]
        if hostapi_name != "Windows WASAPI":
            return None
        return sd.WasapiSettings(auto_convert=True)
    except Exception:
        return None


def apply_rounded_corners(root: tk.Tk) -> None:
    try:
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2
        value = ctypes.c_int(DWMWCP_ROUND)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(value),
            ctypes.sizeof(value),
        )
    except Exception:
        pass


class Recorder:
    def __init__(self, model: WhisperModel, on_state) -> None:
        self.model = model
        self.on_state = on_state
        self.lock = threading.Lock()
        self.state = "idle"
        self.last_toggle = 0.0
        self.stream: sd.InputStream | None = None
        self.chunks: list[np.ndarray] = []
        self.device_index: int | None = None
        self.model_lock = threading.Lock()
        self.target_hwnd: int = 0

    def set_model(self, model: WhisperModel) -> None:
        with self.model_lock:
            self.model = model

    def set_device(self, index: int | None) -> None:
        self.device_index = index

    def toggle(self) -> None:
        now = time.monotonic()
        with self.lock:
            if now - self.last_toggle < 0.3:
                return
            state = self.state
            if state == "transcribing":
                return
            self.last_toggle = now
        if state == "listening":
            threading.Thread(target=self._stop_and_process, daemon=True).start()
        else:
            self._start()

    def _start(self) -> None:
        hwnd = 0
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)
            except Exception:
                pass
            try:
                hwnd = ctypes.windll.user32.GetForegroundWindow()
            except Exception:
                hwnd = 0
        with self.lock:
            if self.state != "idle":
                return
            self.target_hwnd = hwnd
            self.chunks = []

            def callback(indata, *_):
                self.chunks.append(indata.copy())

            kwargs = dict(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=self.device_index,
                callback=callback,
            )
            extra = _wasapi_auto_convert_settings(self.device_index)
            if extra is not None:
                kwargs["extra_settings"] = extra
            try:
                info = sd.query_devices(self.device_index) if self.device_index is not None else None
                hostapi = sd.query_hostapis(info["hostapi"])["name"] if info else "?"
                print(
                    f"[audio open] device_index={self.device_index} "
                    f"name={info['name'] if info else '?'} "
                    f"hostapi={hostapi} auto_convert={extra is not None}",
                    file=sys.stderr,
                )
            except Exception as exc:
                print(f"[audio probe error] {exc}", file=sys.stderr)
            try:
                self.stream = sd.InputStream(**kwargs)
                self.stream.start()
            except Exception as exc:
                print(f"[audio error] {exc}", file=sys.stderr)
                if self.stream is not None:
                    try:
                        self.stream.close()
                    except Exception:
                        pass
                self.stream = None
                return
            self.state = "listening"

        beep(880, 90)
        self.on_state("listening")

    def _stop_and_process(self) -> None:
        if sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoInitializeEx(None, 0x0)
            except Exception:
                pass
        with self.lock:
            if self.state != "listening":
                return
            self.state = "transcribing"
            stream = self.stream
            self.stream = None
            chunks = self.chunks
            self.chunks = []

        if stream is not None:
            stream.stop()
            stream.close()

        beep(520, 90)
        self.on_state("transcribing")

        try:
            if not chunks:
                return
            audio = np.concatenate(chunks).flatten()
            duration = audio.size / SAMPLE_RATE
            t0 = time.perf_counter()
            text = self._transcribe(audio)
            elapsed = time.perf_counter() - t0
            print(
                f"[{duration:.1f}s audio -> {elapsed:.1f}s transcribe, "
                f"{duration / elapsed if elapsed else 0:.1f}x realtime]",
                file=sys.stderr,
            )
            if text:
                print(f"> {text}", file=sys.stderr)
                self._deliver(text)
        except Exception as exc:
            print(f"[transcription error] {exc}", file=sys.stderr)
        finally:
            with self.lock:
                self.state = "idle"
            self.on_state("idle")

    def _transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        with self.model_lock:
            segments, _ = self.model.transcribe(
                audio,
                language=LANGUAGE,
                beam_size=BEAM_SIZE,
                vad_filter=True,
                vad_parameters=VAD_PARAMETERS,
                initial_prompt=INITIAL_PROMPT,
                condition_on_previous_text=False,
                hallucination_silence_threshold=2.0,
            )
            text = " ".join(seg.text.strip() for seg in segments).strip()
            return strip_hallucinations(text)

    def _focus_target(self) -> bool:
        if sys.platform != "win32" or not self.target_hwnd:
            return False
        user32 = ctypes.windll.user32
        hwnd = self.target_hwnd
        if not user32.IsWindow(hwnd):
            return False
        try:
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
            current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
            fg = user32.GetForegroundWindow()
            fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = False
            if fg_thread and fg_thread != current_thread:
                attached = bool(
                    user32.AttachThreadInput(fg_thread, current_thread, True)
                )
            user32.SetForegroundWindow(hwnd)
            if attached:
                user32.AttachThreadInput(fg_thread, current_thread, False)
        except Exception:
            return False
        time.sleep(0.12)
        return user32.GetForegroundWindow() == hwnd

    def _deliver(self, text: str) -> None:
        pyperclip.copy(text)
        if AUTO_PASTE:
            self._focus_target()
            time.sleep(0.08)
            keyboard.send("ctrl+v")


class ControlPanel:
    """Small draggable status pill shown while listening/transcribing/loading."""

    def __init__(self, root: tk.Tk, initial_pos: list[int] | None) -> None:
        self.root = root
        self.visible = False
        self.hide_job: str | None = None
        self.state = "idle"
        self._drag_dx = 0
        self._drag_dy = 0

        root.title("Whisper")
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.97)
        root.configure(bg=BG)

        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        if initial_pos and len(initial_pos) == 2:
            x, y = initial_pos
        else:
            x = screen_w - WIN_W - MARGIN_RIGHT
            y = screen_h - WIN_H - MARGIN_BOTTOM
        x = max(0, min(int(x), screen_w - WIN_W))
        y = max(0, min(int(y), screen_h - WIN_H))
        root.geometry(f"{WIN_W}x{WIN_H}+{x}+{y}")

        root.withdraw()
        root.after(50, lambda: apply_rounded_corners(root))

        container = tk.Frame(root, bg=BG)
        container.pack(expand=True, fill="both", padx=14, pady=8)

        self.dot = tk.Canvas(
            container, width=10, height=10, bg=BG,
            highlightthickness=0, bd=0,
        )
        self.dot.pack(side="left")
        self.dot_id = self.dot.create_oval(
            1, 1, 9, 9, fill=FG_MUTED, outline=""
        )

        self.status = tk.Label(
            container, text="Esperando", fg=FG_MUTED, bg=BG,
            font=("Segoe UI Semibold", 10),
        )
        self.status.pack(side="left", padx=(8, 0))

        for widget in (root, container, self.dot, self.status):
            widget.bind("<Button-1>", self._on_drag_start)
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_end)

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_dx = event.x_root - self.root.winfo_x()
        self._drag_dy = event.y_root - self.root.winfo_y()

    def _on_drag_motion(self, event: tk.Event) -> None:
        x = event.x_root - self._drag_dx
        y = event.y_root - self._drag_dy
        self.root.geometry(f"+{x}+{y}")

    def _on_drag_end(self, _event: tk.Event) -> None:
        current = load_config()
        current["pill_pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        save_config(current)

    def _set_status(self, text: str, color: str) -> None:
        self.status.config(text=text, fg=color)
        self.dot.itemconfig(self.dot_id, fill=color)

    def show(self) -> None:
        if self.hide_job is not None:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if not self.visible:
            self.root.deiconify()
            self.root.after(30, lambda: apply_rounded_corners(self.root))
            self.visible = True

    def hide(self) -> None:
        if self.hide_job is not None:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if self.visible:
            self.root.withdraw()
            self.visible = False

    def _schedule_hide(self, delay_ms: int = 900) -> None:
        if self.hide_job is not None:
            self.root.after_cancel(self.hide_job)
        self.hide_job = self.root.after(delay_ms, self.hide)

    def set_state(self, state: str, extra: str | None = None) -> None:
        self.state = state
        if state == "listening":
            self._set_status("Escuchando", ACCENT)
            self.show()
        elif state == "transcribing":
            self._set_status("Transcribiendo\u2026", ACCENT_WARN)
            self.show()
        elif state == "loading":
            self._set_status(f"Cargando {extra}\u2026", ACCENT_WARN)
            self.show()
        elif state == "error":
            self._set_status("Error al cargar modelo", ACCENT)
            self.show()
        else:
            self.state = "idle"
            self._set_status("Esperando", FG_MUTED)
            self._schedule_hide()

    def set_blink(self, bright: bool) -> None:
        if self.state != "listening":
            return
        self.dot.itemconfig(self.dot_id, fill=ACCENT if bright else ACCENT_DIM)


def make_tray_image(fill_color: str) -> Image.Image:
    """Mic glyph on a dark rounded badge, supersampled 4x for crisp edges."""
    s = 4
    size = 64 * s
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=15 * s, fill="#15151b")
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=15 * s, outline="#33333f", width=s)
    d.rounded_rectangle([25 * s, 8 * s, 39 * s, 34 * s], radius=7 * s, fill=fill_color)
    d.arc([17 * s, 16 * s, 47 * s, 44 * s], start=0, end=180, fill=fill_color, width=4 * s)
    d.line([(32 * s, 44 * s), (32 * s, 51 * s)], fill=fill_color, width=4 * s)
    d.line([(24 * s, 54 * s), (40 * s, 54 * s)], fill=fill_color, width=4 * s)
    # round caps on arc ends, stem foot and base ends
    for cx, cy in ((17, 30), (47, 30), (32, 51), (24, 54), (40, 54)):
        d.ellipse([(cx - 2) * s, (cy - 2) * s, (cx + 2) * s, (cy + 2) * s], fill=fill_color)
    return img.resize((64, 64), Image.LANCZOS)


TRAY_IMG_IDLE = make_tray_image(FG_MUTED)
TRAY_IMG_LISTENING = make_tray_image(ACCENT)
TRAY_IMG_LISTENING_DIM = make_tray_image(ACCENT_DIM)
TRAY_IMG_BUSY = make_tray_image(ACCENT_WARN)


class TrayController:
    """System tray icon: state indicator + mic/model/sounds/restart/exit menu."""

    def __init__(
        self,
        root: tk.Tk,
        panel: ControlPanel,
        initial_device_name: str | None,
        initial_model: str,
        sounds_enabled: bool,
        hidden_devices: list[str],
        on_device_change,
        on_model_change,
    ) -> None:
        self.root = root
        self.panel = panel
        self.on_device_change = on_device_change
        self.on_model_change = on_model_change
        self.current_device = initial_device_name
        self.current_model = initial_model
        self.sounds_enabled = sounds_enabled
        self.hidden_devices = set(hidden_devices)
        self.model_loading = False
        self.state = "idle"

        self.icon = pystray.Icon(
            "whisper",
            TRAY_IMG_IDLE,
            TRAY_TOOLTIP,
            menu=pystray.Menu(self._menu_items),
        )

    def run(self) -> None:
        self.icon.run_detached()

    # -- state / blink, called from the tk thread --------------------------

    def set_state(self, state: str) -> None:
        self.state = state
        if state == "listening":
            self.icon.icon = TRAY_IMG_LISTENING
        elif state in ("transcribing", "loading"):
            self.icon.icon = TRAY_IMG_BUSY
        else:
            self.icon.icon = TRAY_IMG_IDLE

    def set_blink(self, bright: bool) -> None:
        if self.state != "listening":
            return
        self.icon.icon = TRAY_IMG_LISTENING if bright else TRAY_IMG_LISTENING_DIM

    # -- menu construction (regenerated every time the menu opens) ---------

    def _menu_items(self):
        yield pystray.MenuItem("Micrófono", pystray.Menu(self._mic_items))
        yield pystray.MenuItem("Modelo", pystray.Menu(self._model_items))
        yield pystray.MenuItem(
            "Sonidos", self._toggle_sounds, checked=lambda item: self.sounds_enabled,
        )
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Reiniciar", self._select_restart)
        yield pystray.MenuItem("Salir", self._select_exit)

    def _mic_items(self):
        for _, name in list_input_devices():
            if name in self.hidden_devices and name != self.current_device:
                continue
            yield pystray.MenuItem(
                name,
                functools.partial(self._select_device, name),
                checked=functools.partial(self._device_checked, name),
                radio=True,
            )
        yield pystray.Menu.SEPARATOR
        yield pystray.MenuItem("Editar lista…", pystray.Menu(self._edit_mic_items))

    def _edit_mic_items(self):
        for _, name in list_input_devices():
            yield pystray.MenuItem(
                name,
                functools.partial(self._toggle_device_hidden, name),
                checked=functools.partial(self._device_visible, name),
            )

    def _model_items(self):
        for name in AVAILABLE_MODELS:
            yield pystray.MenuItem(
                name,
                functools.partial(self._select_model, name),
                checked=functools.partial(self._model_checked, name),
                radio=True,
            )

    # -- microphone ----------------------------------------------------------

    def _select_device(self, name, _icon, _item) -> None:
        self.root.after(0, self._apply_device, name)

    def _device_checked(self, name, _item) -> bool:
        return name == self.current_device

    def _apply_device(self, name: str) -> None:
        self.on_device_change(name)
        self.current_device = name

    def _device_visible(self, name, _item) -> bool:
        return name not in self.hidden_devices

    def _toggle_device_hidden(self, name, _icon, _item) -> None:
        if name in self.hidden_devices:
            self.hidden_devices.discard(name)
        else:
            self.hidden_devices.add(name)
        current = load_config()
        current["hidden_devices"] = sorted(self.hidden_devices)
        save_config(current)

    # -- model -----------------------------------------------------------

    def _select_model(self, name, _icon, _item) -> None:
        if self.model_loading or name == self.current_model:
            return
        self.model_loading = True
        self.root.after(0, self._begin_model_switch, name)

    def _model_checked(self, name, _item) -> bool:
        return name == self.current_model

    def _begin_model_switch(self, name: str) -> None:
        self.panel.set_state("loading", name)
        self.set_state("loading")
        threading.Thread(
            target=self._load_model_thread, args=(name,), daemon=True,
        ).start()

    def _load_model_thread(self, name: str) -> None:
        ok = self.on_model_change(name)
        self.root.after(0, self._end_model_switch, ok, name)

    def _end_model_switch(self, ok: bool, name: str) -> None:
        self.model_loading = False
        if ok:
            self.current_model = name
            self.panel.set_state("idle")
        else:
            self.panel.set_state("error")
        self.set_state("idle")

    # -- sounds ------------------------------------------------------------

    def _toggle_sounds(self, _icon, _item) -> None:
        global SOUNDS_ENABLED
        self.sounds_enabled = not self.sounds_enabled
        SOUNDS_ENABLED = self.sounds_enabled
        current = load_config()
        current["sounds"] = self.sounds_enabled
        save_config(current)

    # -- restart / exit ------------------------------------------------------

    def _select_restart(self, _icon, _item) -> None:
        subprocess.Popen(
            [
                "powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                "-File", str(RESTART_SCRIPT_PATH),
            ],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )

    def _select_exit(self, _icon, _item) -> None:
        self.root.after(0, self.stop_and_destroy)

    def stop_and_destroy(self) -> None:
        self.icon.stop()
        self.root.destroy()


def run_hotkey_thread(on_toggle, on_exit) -> None:
    """Register global hotkeys via Win32 RegisterHotKey and pump WM_HOTKEY.

    More robust than a low-level keyboard hook: survives elevated/foreground
    games (e.g. Diablo 4) that block ordinary keyboard hooks.
    """
    user32 = ctypes.windll.user32
    user32.RegisterHotKey.argtypes = [
        wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT,
    ]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND,
        wintypes.UINT, wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int

    if not user32.RegisterHotKey(None, HOTKEY_ID_TOGGLE, MOD_NOREPEAT, HOTKEY_VK):
        print("[hotkey] RegisterHotKey F9 failed", file=sys.stderr)
        return
    if not user32.RegisterHotKey(
        None, HOTKEY_ID_EXIT,
        MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, EXIT_HOTKEY_VK,
    ):
        print("[hotkey] RegisterHotKey Ctrl+Alt+Q failed", file=sys.stderr)

    print("Hotkeys registered (Win32): F9, Ctrl+Alt+Q", file=sys.stderr)

    msg = wintypes.MSG()
    while True:
        rv = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if rv <= 0:
            return
        if msg.message == WM_HOTKEY:
            if msg.wParam == HOTKEY_ID_TOGGLE:
                on_toggle()
            elif msg.wParam == HOTKEY_ID_EXIT:
                on_exit()
                return


def load_model(name: str) -> WhisperModel:
    print(
        f"Loading faster-whisper model ({name}) on {DEVICE}/{COMPUTE_TYPE}...",
        file=sys.stderr,
    )
    model = WhisperModel(name, device=DEVICE, compute_type=COMPUTE_TYPE)
    warmup = np.zeros(SAMPLE_RATE, dtype=np.float32)
    list(model.transcribe(warmup, language=LANGUAGE, beam_size=BEAM_SIZE)[0])
    return model


def main() -> None:
    if not acquire_single_instance_lock():
        print(
            "Another Whisper instance is already running. Exiting.",
            file=sys.stderr,
        )
        return

    write_pid_file()

    global SOUNDS_ENABLED

    cfg = load_config()
    model_name = cfg.get("model", DEFAULT_MODEL)
    if model_name not in AVAILABLE_MODELS:
        model_name = DEFAULT_MODEL
    device_name = cfg.get("input_device")
    sounds_enabled = cfg.get("sounds", True)
    pill_pos = cfg.get("pill_pos")
    hidden_devices = cfg.get("hidden_devices", [])
    SOUNDS_ENABLED = sounds_enabled

    if not device_name:
        available = list_input_devices()
        if available:
            device_name = available[0][1]

    model = load_model(model_name)

    print(
        f"Ready. {HOTKEY.upper()} start/stop. {EXIT_HOTKEY.upper()} quit.",
        file=sys.stderr,
    )

    root = tk.Tk()

    def on_state(state: str) -> None:
        root.after(0, panel.set_state, state)
        root.after(0, tray.set_state, state)

    recorder = Recorder(model, on_state)
    recorder.set_device(find_device_index(device_name))

    def on_device_change(name: str) -> None:
        recorder.set_device(find_device_index(name))
        current = load_config()
        current["input_device"] = name
        save_config(current)

    def on_model_change(new_model: str) -> bool:
        try:
            new = load_model(new_model)
        except Exception as exc:
            print(f"[model load error] {exc}", file=sys.stderr)
            return False
        recorder.set_model(new)
        current = load_config()
        current["model"] = new_model
        save_config(current)
        print(f"Model switched to {new_model}", file=sys.stderr)
        return True

    panel = ControlPanel(root, initial_pos=pill_pos)

    tray = TrayController(
        root,
        panel,
        initial_device_name=device_name,
        initial_model=model_name,
        sounds_enabled=sounds_enabled,
        hidden_devices=hidden_devices,
        on_device_change=on_device_change,
        on_model_change=on_model_change,
    )
    tray.run()

    threading.Thread(
        target=run_hotkey_thread,
        args=(recorder.toggle, lambda: root.after(0, tray.stop_and_destroy)),
        daemon=True,
    ).start()

    blink_state = {"bright": True}

    def blink_tick() -> None:
        blink_state["bright"] = not blink_state["bright"]
        panel.set_blink(blink_state["bright"])
        tray.set_blink(blink_state["bright"])
        root.after(500, blink_tick)

    root.after(500, blink_tick)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
