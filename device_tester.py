#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
device_tester.py — uniwersalny tester podłączonych urządzeń wejściowych (Windows).

Przechwytuje wejście ze WSZYSTKICH urządzeń HID przez Windows Raw Input API:
klawiatury, myszy, pady/joysticki, piloty, urządzenia Bluetooth udające klawiaturę,
piloty multimedialne itd. Dla każdego zdarzenia pokazuje, KTÓRE urządzenie je wysłało
oraz CO zostało wciśnięte / kliknięte.

Cechy:
  - zero zależności (tylko biblioteka standardowa Pythona, ctypes),
  - działa gdy okno nie ma fokusu (RIDEV_INPUTSINK) — można klikać w innych aplikacjach,
  - wykrywa podłączanie/odłączanie urządzeń w locie (WM_INPUT_DEVICE_CHANGE),
  - dla nieznanych urządzeń HID pokazuje surowe bajty raportu + które bajty się zmieniły
    (uniwersalne „co zostało wciśnięte" dla dowolnego pada/pilota),
  - nie wymaga uprawnień administratora.

Uruchomienie:
    python device_tester.py                 # klawisze / przyciski / kółko / HID (bez ruchu myszy)
    python device_tester.py --verbose       # dodatkowo ruch myszy i pełne raporty HID
    python device_tester.py --list          # tylko wypisz podłączone urządzenia i zakończ
    python device_tester.py --log plik.log  # dodatkowo zapisuj wszystko do pliku na bieżąco
    python device_tester.py --log           # jw., automatyczna nazwa device-tester-DATA.log

Zatrzymanie: Ctrl+C.
"""

import ctypes
import sys
import signal
import argparse
from ctypes import wintypes
from datetime import datetime

# --------------------------------------------------------------------------- #
#  Konsola: wymuś UTF-8 (polskie nazwy klawiszy) i niebuforowane wyjście.
# --------------------------------------------------------------------------- #
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# Uchwyt pliku logu (None = brak logowania). Ustawiany przez open_log().
_log_fh = None


def open_log(path: str) -> bool:
    """Otwórz plik logu do dopisywania (append), UTF-8, line-buffered.
    Zwraca True jeśli się udało. Nigdy nie rzuca — błąd tylko sygnalizuje."""
    global _log_fh
    try:
        # buffering=1 = buforowanie liniowe (tekst); dodatkowo flush po każdej linii.
        _log_fh = open(path, "a", encoding="utf-8", errors="replace", buffering=1, newline="")
        return True
    except Exception as e:
        _log_fh = None
        try:
            sys.stderr.write(f"[uwaga] nie udało się otworzyć pliku logu '{path}': {e!r}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return False


def close_log() -> None:
    global _log_fh
    fh, _log_fh = _log_fh, None
    if fh is not None:
        try:
            fh.flush()
            fh.close()
        except Exception:
            pass


def out(line: str) -> None:
    """Wypisz linię na konsolę i (jeśli włączony) do pliku logu — natychmiast,
    z flushem po każdej linii. Wszystko opakowane tak, by nigdy nie wywalić programu."""
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except Exception:
        pass
    fh = _log_fh
    if fh is not None:
        try:
            fh.write(line + "\n")
            fh.flush()
        except Exception:
            # Logowanie nie może przerwać nasłuchu — po prostu pomiń tę linię.
            pass


# --------------------------------------------------------------------------- #
#  Typy pomocnicze / stałe Win32.
# --------------------------------------------------------------------------- #
LRESULT = ctypes.c_ssize_t          # LONG_PTR
ULONG_PTR = ctypes.c_size_t

# Komunikaty
WM_INPUT = 0x00FF
WM_INPUT_DEVICE_CHANGE = 0x00FE
WM_QUIT = 0x0012

# Raw input – typy urządzeń
RIM_TYPEMOUSE = 0
RIM_TYPEKEYBOARD = 1
RIM_TYPEHID = 2

# Rejestracja
RIDEV_INPUTSINK = 0x00000100        # odbieraj wejście nawet bez fokusu
RIDEV_DEVNOTIFY = 0x00002000        # powiadomienia o podłączeniu/odłączeniu

# GetRawInputData
RID_INPUT = 0x10000003
RID_HEADER = 0x10000005

# GetRawInputDeviceInfo
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000B

# GetRawInputDeviceList
RIDI_TYPE_NAMES = {RIM_TYPEMOUSE: "MOUSE", RIM_TYPEKEYBOARD: "KEYBOARD", RIM_TYPEHID: "HID"}

# WM_INPUT_DEVICE_CHANGE wParam
GIDC_ARRIVAL = 1
GIDC_REMOVAL = 2

# Flagi klawiatury
RI_KEY_MAKE = 0x00
RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02
RI_KEY_E1 = 0x04

# Flagi przycisków myszy
RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_BUTTON_5_DOWN = 0x0100
RI_MOUSE_BUTTON_5_UP = 0x0200
RI_MOUSE_WHEEL = 0x0400
RI_MOUSE_HWHEEL = 0x0800

MOUSE_BUTTON_EVENTS = [
    (RI_MOUSE_LEFT_BUTTON_DOWN, "LPM ↓"),
    (RI_MOUSE_LEFT_BUTTON_UP, "LPM ↑"),
    (RI_MOUSE_RIGHT_BUTTON_DOWN, "PPM ↓"),
    (RI_MOUSE_RIGHT_BUTTON_UP, "PPM ↑"),
    (RI_MOUSE_MIDDLE_BUTTON_DOWN, "ŚPM ↓"),
    (RI_MOUSE_MIDDLE_BUTTON_UP, "ŚPM ↑"),
    (RI_MOUSE_BUTTON_4_DOWN, "PRZ4 ↓"),
    (RI_MOUSE_BUTTON_4_UP, "PRZ4 ↑"),
    (RI_MOUSE_BUTTON_5_DOWN, "PRZ5 ↓"),
    (RI_MOUSE_BUTTON_5_UP, "PRZ5 ↑"),
]

# Top-level HID collections, które chcemy złapać (Generic Desktop + Consumer).
# (UsagePage, Usage) — 0 w usage = użyj RIDEV_PAGEONLY dla całej strony.
TARGET_USAGES = [
    # Generic Desktop (0x01)
    (0x01, 0x02),  # Mouse
    (0x01, 0x04),  # Joystick
    (0x01, 0x05),  # Gamepad
    (0x01, 0x06),  # Keyboard
    (0x01, 0x07),  # Keypad
    (0x01, 0x08),  # Multi-axis Controller
    (0x01, 0x09),  # Tablet PC System Controls (przyciski 2-in-1, rotation-lock)
    (0x01, 0x0C),  # Wireless Radio Controls (tryb samolotowy / radio)
    (0x01, 0x0E),  # System Multi-Axis Controller
    (0x01, 0x0F),  # Spatial Controller
    (0x01, 0x80),  # System Control (power/sleep/wake)
    # VR Controls (0x03)
    (0x03, 0x04),  # Glove
    (0x03, 0x05),  # Head Tracker
    (0x03, 0x06),  # Head Mounted Display (HMD)
    (0x03, 0x07),  # Hand Tracker
    # Game Controls (0x05)
    (0x05, 0x01),  # 3D Game Controller
    (0x05, 0x02),  # Pinball Device
    (0x05, 0x03),  # Gun Device (light guns)
    # Consumer (0x0C)
    (0x0C, 0x01),  # Consumer Control (piloty, klawisze multimedialne)
    (0x0C, 0x03),  # Programmable Buttons (macro pady / stream decki)
    # Digitizers (0x0D)
    (0x0D, 0x01),  # Digitizer (tablety graficzne)
    (0x0D, 0x02),  # Pen (rysik)
    (0x0D, 0x04),  # Touch Screen (ekran dotykowy)
    (0x0D, 0x05),  # Touch Pad (Windows Precision Touchpad, multitouch)
]


# --------------------------------------------------------------------------- #
#  Struktury Raw Input.
# --------------------------------------------------------------------------- #
class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
        ("dwFlags", wintypes.DWORD),
        ("hwndTarget", wintypes.HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSize", wintypes.DWORD),
        ("hDevice", wintypes.HANDLE),
        ("wParam", wintypes.WPARAM),
    ]


class _RAWMOUSE_BUTTONS(ctypes.Structure):
    _fields_ = [
        ("usButtonFlags", wintypes.USHORT),
        ("usButtonData", wintypes.SHORT),   # delta kółka (ze znakiem)
    ]


class _RAWMOUSE_U(ctypes.Union):
    _fields_ = [
        ("ulButtons", wintypes.DWORD),
        ("btn", _RAWMOUSE_BUTTONS),
    ]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("usFlags", wintypes.USHORT),
        ("u", _RAWMOUSE_U),
        ("ulRawButtons", wintypes.DWORD),
        ("lLastX", wintypes.LONG),
        ("lLastY", wintypes.LONG),
        ("ulExtraInformation", wintypes.DWORD),
    ]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT),
        ("Message", wintypes.UINT),
        ("ExtraInformation", wintypes.ULONG),
    ]


class RAWHID(ctypes.Structure):
    _fields_ = [
        ("dwSizeHid", wintypes.DWORD),
        ("dwCount", wintypes.DWORD),
        ("bRawData", wintypes.BYTE * 1),   # tablica zmiennej długości
    ]


class _RAWINPUT_U(ctypes.Union):
    _fields_ = [
        ("mouse", RAWMOUSE),
        ("keyboard", RAWKEYBOARD),
        ("hid", RAWHID),
    ]


class RAWINPUT(ctypes.Structure):
    _fields_ = [
        ("header", RAWINPUTHEADER),
        ("data", _RAWINPUT_U),
    ]


class RID_DEVICE_INFO_MOUSE(ctypes.Structure):
    _fields_ = [
        ("dwId", wintypes.DWORD),
        ("dwNumberOfButtons", wintypes.DWORD),
        ("dwSampleRate", wintypes.DWORD),
        ("fHasHorizontalWheel", wintypes.BOOL),
    ]


class RID_DEVICE_INFO_KEYBOARD(ctypes.Structure):
    _fields_ = [
        ("dwType", wintypes.DWORD),
        ("dwSubType", wintypes.DWORD),
        ("dwKeyboardMode", wintypes.DWORD),
        ("dwNumberOfFunctionKeys", wintypes.DWORD),
        ("dwNumberOfIndicators", wintypes.DWORD),
        ("dwNumberOfKeysTotal", wintypes.DWORD),
    ]


class RID_DEVICE_INFO_HID(ctypes.Structure):
    _fields_ = [
        ("dwVendorId", wintypes.DWORD),
        ("dwProductId", wintypes.DWORD),
        ("dwVersionNumber", wintypes.DWORD),
        ("usUsagePage", wintypes.USHORT),
        ("usUsage", wintypes.USHORT),
    ]


class _RID_DEVICE_INFO_U(ctypes.Union):
    _fields_ = [
        ("mouse", RID_DEVICE_INFO_MOUSE),
        ("keyboard", RID_DEVICE_INFO_KEYBOARD),
        ("hid", RID_DEVICE_INFO_HID),
    ]


class RID_DEVICE_INFO(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("dwType", wintypes.DWORD),
        ("u", _RID_DEVICE_INFO_U),
    ]


class RAWINPUTDEVICELIST(ctypes.Structure):
    _fields_ = [
        ("hDevice", wintypes.HANDLE),
        ("dwType", wintypes.DWORD),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


# --------------------------------------------------------------------------- #
#  Prototypy funkcji Win32 (argtypes/restype są krytyczne dla 64-bit).
# --------------------------------------------------------------------------- #
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
hid = ctypes.WinDLL("hid", use_last_error=True)

user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
user32.RegisterClassW.restype = wintypes.ATOM

user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND

user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT

user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL

user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL

user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.TranslateMessage.restype = wintypes.BOOL

user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT

user32.MsgWaitForMultipleObjectsEx.argtypes = [
    wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
]
user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD

user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.RegisterRawInputDevices.restype = wintypes.BOOL

user32.GetRawInputData.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputData.restype = wintypes.UINT

user32.GetRawInputDeviceInfoW.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT),
]
user32.GetRawInputDeviceInfoW.restype = wintypes.UINT

user32.GetRawInputDeviceList.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICELIST), ctypes.POINTER(wintypes.UINT), wintypes.UINT,
]
user32.GetRawInputDeviceList.restype = wintypes.UINT

user32.GetKeyNameTextW.argtypes = [wintypes.LONG, wintypes.LPWSTR, ctypes.c_int]
user32.GetKeyNameTextW.restype = ctypes.c_int

user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.PostQuitMessage.restype = None

kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE

kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
    wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
]
kernel32.CreateFileW.restype = wintypes.HANDLE

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

hid.HidD_GetProductString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetProductString.restype = wintypes.BOOLEAN

hid.HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.ULONG]
hid.HidD_GetManufacturerString.restype = wintypes.BOOLEAN

HWND_MESSAGE = wintypes.HWND(-3)
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3


# --------------------------------------------------------------------------- #
#  Informacje o urządzeniach (cache: hDevice -> opis).
# --------------------------------------------------------------------------- #
class DeviceInfo:
    __slots__ = ("handle", "type", "path", "vid", "pid", "usage_page", "usage", "friendly")

    def __init__(self, handle):
        self.handle = handle
        self.type = None
        self.path = ""
        self.vid = None
        self.pid = None
        self.usage_page = None
        self.usage = None
        self.friendly = ""

    def label(self) -> str:
        tname = RIDI_TYPE_NAMES.get(self.type, "?")
        if self.handle in (0, None) and not self.friendly:
            return f"wstrzyknięte/systemowe ({tname})"
        parts = []
        if self.friendly:
            parts.append(self.friendly)
        ids = ""
        if self.vid is not None and self.pid is not None:
            ids = f" VID:{self.vid:04X} PID:{self.pid:04X}"
        if self.usage_page is not None:
            ids += f" [UP:{self.usage_page:02X} U:{self.usage:02X}]"
        head = parts[0] if parts else (self.path.split("#")[1] if "#" in self.path else self.path or "urządzenie")
        return f"{head} ({tname}{ids})"


_device_cache = {}


def _get_raw_input_device_name(handle) -> str:
    size = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
    if size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(size.value + 1)
    r = user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buf, ctypes.byref(size))
    if r == 0xFFFFFFFF:  # (UINT)-1 == błąd
        return ""
    return buf.value


def _get_raw_input_device_info(handle):
    info = RID_DEVICE_INFO()
    info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
    size = wintypes.UINT(ctypes.sizeof(RID_DEVICE_INFO))
    r = user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICEINFO, ctypes.byref(info), ctypes.byref(size))
    if r == 0xFFFFFFFF or r == 0:
        return None
    return info


def _get_friendly_name(path: str) -> str:
    """Best-effort: odczyt nazwy produktu przez HidD_GetProductString."""
    if not path:
        return ""
    h = kernel32.CreateFileW(
        path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None
    )
    if not h or h == INVALID_HANDLE_VALUE:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(256)
        name = ""
        if hid.HidD_GetProductString(h, buf, ctypes.sizeof(buf)):
            name = buf.value.strip()
        if not name:
            mbuf = ctypes.create_unicode_buffer(256)
            if hid.HidD_GetManufacturerString(h, mbuf, ctypes.sizeof(mbuf)):
                name = mbuf.value.strip()
        return name
    except Exception:
        return ""
    finally:
        kernel32.CloseHandle(h)


def resolve_device(handle) -> DeviceInfo:
    """Zwróć (i zapamiętaj) opis urządzenia dla danego uchwytu."""
    if handle in _device_cache:
        return _device_cache[handle]
    di = DeviceInfo(handle)
    try:
        di.path = _get_raw_input_device_name(handle)
        info = _get_raw_input_device_info(handle)
        if info is not None:
            di.type = info.dwType
            if info.dwType == RIM_TYPEHID:
                di.vid = info.hid.dwVendorId
                di.pid = info.hid.dwProductId
                di.usage_page = info.hid.usUsagePage
                di.usage = info.hid.usUsage
        di.friendly = _get_friendly_name(di.path)
    except Exception:
        pass
    _device_cache[handle] = di
    return di


# --------------------------------------------------------------------------- #
#  Dekodowanie zdarzeń.
# --------------------------------------------------------------------------- #
def key_name(vkey: int, make_code: int, e0: bool) -> str:
    """Czytelna, zlokalizowana nazwa klawisza (GetKeyNameTextW) z fallbackiem."""
    lparam = (make_code & 0xFF) << 16
    if e0:
        lparam |= 1 << 24  # bit rozszerzony
    lparam |= 1 << 25      # "nie rozróżniaj lewy/prawy" — pełna nazwa
    buf = ctypes.create_unicode_buffer(128)
    n = user32.GetKeyNameTextW(wintypes.LONG(lparam), buf, 128)
    if n > 0 and buf.value.strip():
        return buf.value.strip()
    if vkey in _VK_NAMES:
        return _VK_NAMES[vkey]
    # 0-9 oraz A-Z mają VK == kod ASCII znaku.
    if 0x30 <= vkey <= 0x39 or 0x41 <= vkey <= 0x5A:
        return chr(vkey)
    return f"VK_0x{vkey:02X}"


# Fallback dla klawiszy, których GetKeyNameText nie nazwie (media/consumer często = VK 0).
_VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift", 0x11: "Ctrl",
    0x12: "Alt", 0x13: "Pause", 0x14: "CapsLock", 0x1B: "Esc", 0x20: "Spacja",
    0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win L", 0x5C: "Win P", 0x5D: "Menu",
    0xA6: "Przeglądarka Wstecz", 0xA7: "Przeglądarka Dalej", 0xA8: "Przeglądarka Odśwież",
    0xAD: "Wycisz", 0xAE: "Głośność −", 0xAF: "Głośność +",
    0xB0: "Następny utwór", 0xB1: "Poprzedni utwór", 0xB2: "Stop", 0xB3: "Play/Pauza",
}


def handle_keyboard(di: DeviceInfo, kb: RAWKEYBOARD, verbose: bool) -> None:
    # 0xFF/0x00 make code z VKey==255 to zdarzenia "overrun"/fake — pomiń.
    if kb.VKey == 0xFF:
        return
    e0 = bool(kb.Flags & RI_KEY_E0)
    is_break = bool(kb.Flags & RI_KEY_BREAK)
    action = "↑ zwolniony" if is_break else "↓ wciśnięty"
    name = key_name(kb.VKey, kb.MakeCode, e0)
    out(f"{_ts()}  {di.label()}\n    KLAWISZ  {name:<20} {action}  "
        f"(VK=0x{kb.VKey:02X} scan=0x{kb.MakeCode:02X}{' E0' if e0 else ''})")


def handle_mouse(di: DeviceInfo, ms: RAWMOUSE, verbose: bool) -> None:
    flags = ms.btn.usButtonFlags
    events = [label for bit, label in MOUSE_BUTTON_EVENTS if flags & bit]

    if flags & RI_MOUSE_WHEEL:
        delta = ms.btn.usButtonData
        events.append(f"kółko {'+' if delta > 0 else ''}{delta}")
    if flags & RI_MOUSE_HWHEEL:
        delta = ms.btn.usButtonData
        events.append(f"kółko poziome {'+' if delta > 0 else ''}{delta}")

    if events:
        out(f"{_ts()}  {di.label()}\n    MYSZ     {', '.join(events)}")
    elif verbose and (ms.lLastX or ms.lLastY):
        out(f"{_ts()}  {di.label()}\n    MYSZ     ruch dx={ms.lLastX} dy={ms.lLastY}")


# Poprzedni raport per urządzenie (do wykrywania zmienionych bajtów).
_prev_hid = {}


def handle_hid(di: DeviceInfo, ri: RAWINPUT, buf: ctypes.Array, verbose: bool) -> None:
    size_hid = ri.data.hid.dwSizeHid
    count = ri.data.hid.dwCount
    if size_hid == 0 or count == 0:
        return
    # Adres bajtów raportu = adres pola bRawData wewnątrz odebranego bufora.
    base = ctypes.addressof(ri.data.hid.bRawData)
    total = size_hid * count
    raw = ctypes.string_at(base, total)

    for i in range(count):
        report = raw[i * size_hid:(i + 1) * size_hid]
        prev = _prev_hid.get(di.handle)
        changed = []
        if prev is not None and len(prev) == len(report):
            changed = [j for j in range(len(report)) if prev[j] != report[j]]
        _prev_hid[di.handle] = report

        hex_full = " ".join(f"{b:02X}" for b in report)
        if changed and not verbose:
            # Pokaż tylko zmienione bajty — „co zostało wciśnięte".
            diff = ", ".join(f"[{j}] {prev[j]:02X}→{report[j]:02X}" for j in changed)
            out(f"{_ts()}  {di.label()}\n    HID      zmiana: {diff}")
        elif verbose or prev is None:
            marker = "  (zmiana: " + ",".join(str(j) for j in changed) + ")" if changed else ""
            out(f"{_ts()}  {di.label()}\n    HID      raport: {hex_full}{marker}")


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# --------------------------------------------------------------------------- #
#  Główna pętla WM_INPUT.
# --------------------------------------------------------------------------- #
_verbose = False
_running = True


def _on_sigint(signum, frame):
    # Ustawienie flagi z handlera nigdy nie rzuca wyjątku, więc nie może zostać
    # "połknięte" na granicy callbacku ctypes (WNDPROC). Pętla kończy się czysto.
    global _running
    _running = False


def on_raw_input(hrawinput) -> None:
    size = wintypes.UINT(0)
    if user32.GetRawInputData(hrawinput, RID_INPUT, None, ctypes.byref(size),
                              ctypes.sizeof(RAWINPUTHEADER)) == 0xFFFFFFFF:
        return
    if size.value == 0:
        return
    buf = (ctypes.c_byte * size.value)()
    got = user32.GetRawInputData(hrawinput, RID_INPUT, buf, ctypes.byref(size),
                                 ctypes.sizeof(RAWINPUTHEADER))
    if got == 0xFFFFFFFF or got == 0:
        return
    ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
    # Prawidłowy uchwyt urządzenia nigdy nie jest 0; NULL (None) = wejście
    # wstrzyknięte (SendInput / hooki) — normalizuj do 0 dla spójnego klucza.
    di = resolve_device(ri.header.hDevice or 0)

    try:
        if ri.header.dwType == RIM_TYPEKEYBOARD:
            if di.type is None:
                di.type = RIM_TYPEKEYBOARD
            handle_keyboard(di, ri.data.keyboard, _verbose)
        elif ri.header.dwType == RIM_TYPEMOUSE:
            if di.type is None:
                di.type = RIM_TYPEMOUSE
            handle_mouse(di, ri.data.mouse, _verbose)
        elif ri.header.dwType == RIM_TYPEHID:
            handle_hid(di, ri, buf, _verbose)
    except Exception as e:
        out(f"{_ts()}  [błąd dekodowania] {e!r}")


def on_device_change(wparam, lparam) -> None:
    # lparam niesie wartość HANDLE urządzenia jako liczbę całkowitą.
    handle = int(lparam) & ((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1)
    if wparam == GIDC_REMOVAL:
        di = _device_cache.pop(handle, None)
        _prev_hid.pop(handle, None)
        label = di.label() if di is not None else f"uchwyt {handle}"
        out(f"{_ts()}  >>> ODŁĄCZONO: {label}")
    elif wparam == GIDC_ARRIVAL:
        # Urządzenia obecne przy starcie są już w cache (z list_devices) —
        # nie ogłaszaj ich ponownie; pokaż tylko realnie nowe podłączenia.
        if handle in _device_cache:
            return
        di = resolve_device(handle)
        out(f"{_ts()}  >>> PODŁĄCZONO: {di.label()}")


def _wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        on_raw_input(lparam)
        # WM_INPUT WYMAGA wywołania DefWindowProc, żeby system posprzątał bufor
        # raw-input po każdym zdarzeniu (inaczej wyciek przy ciągłym działaniu).
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
    if msg == WM_INPUT_DEVICE_CHANGE:
        on_device_change(wparam, lparam)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


# Trzymamy referencję globalnie, żeby GC nie zwolnił callbacku (krytyczne!).
_wnd_proc_ptr = WNDPROC(_wnd_proc)


# --------------------------------------------------------------------------- #
#  Rejestracja i uruchomienie.
# --------------------------------------------------------------------------- #
def create_message_window() -> wintypes.HWND:
    hinst = kernel32.GetModuleHandleW(None)
    cls = WNDCLASS()
    cls.lpfnWndProc = _wnd_proc_ptr
    cls.hInstance = hinst
    cls.lpszClassName = "DeviceTesterRawInputWnd"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        err = ctypes.get_last_error()
        # 1410 = klasa już zarejestrowana — akceptowalne przy ponownym uruchomieniu.
        if err not in (0, 1410):
            raise ctypes.WinError(err)
    hwnd = user32.CreateWindowExW(
        0, cls.lpszClassName, "DeviceTester", 0, 0, 0, 0, 0,
        HWND_MESSAGE, None, hinst, None,
    )
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    return hwnd


def register_devices(hwnd) -> int:
    """Rejestruj każdą parę (usage_page, usage) osobno — jeśli jedna się nie uda,
    reszta i tak działa (większa niezawodność)."""
    ok = 0
    for up, u in TARGET_USAGES:
        rid = RAWINPUTDEVICE()
        rid.usUsagePage = up
        rid.usUsage = u
        rid.dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY
        rid.hwndTarget = hwnd
        if user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)):
            ok += 1
        else:
            err = ctypes.get_last_error()
            out(f"[uwaga] nie udało się zarejestrować UsagePage=0x{up:02X} Usage=0x{u:02X} "
                f"(błąd {err}) — pomijam")
    return ok


def list_devices() -> None:
    count = wintypes.UINT(0)
    user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if count.value == 0:
        out("Brak wykrytych urządzeń Raw Input.")
        return
    arr = (RAWINPUTDEVICELIST * count.value)()
    n = user32.GetRawInputDeviceList(arr, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if n == 0xFFFFFFFF:
        out("Błąd pobierania listy urządzeń.")
        return
    out(f"Wykryte urządzenia wejściowe ({n}):")
    out("-" * 70)
    for i in range(n):
        di = resolve_device(arr[i].hDevice)
        if di.type is None:
            di.type = arr[i].dwType
        out(f"  {i + 1:>2}. {di.label()}")
        if di.path:
            out(f"      {di.path}")
    out("-" * 70)


def message_loop() -> None:
    global _running
    QS_ALLINPUT = 0x04FF
    PM_REMOVE = 0x0001
    msg = wintypes.MSG()
    while _running:
        # Czekaj max 200 ms — pozwala obsłużyć Ctrl+C i wyjść czysto.
        user32.MsgWaitForMultipleObjectsEx(0, None, 200, QS_ALLINPUT, 0)
        while _running and user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_QUIT:
                _running = False
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


def main() -> int:
    global _verbose, _running
    parser = argparse.ArgumentParser(
        description="Uniwersalny tester podłączonych urządzeń wejściowych (Windows Raw Input).",
    )
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="pokazuj też ruch myszy i pełne raporty HID")
    parser.add_argument("--list", "-l", action="store_true",
                        help="tylko wypisz podłączone urządzenia i zakończ")
    parser.add_argument("--log", "-o", nargs="?", const="__auto__", default=None,
                        metavar="PLIK",
                        help="zapisuj wszystko z konsoli na bieżąco do pliku (append, flush "
                             "po każdej linii); bez nazwy = auto device-tester-DATA-CZAS.log")
    args = parser.parse_args()
    _verbose = args.verbose

    if sys.platform != "win32":
        out("Ten program działa tylko na Windows.")
        return 1

    if args.log is not None:
        log_path = args.log
        if log_path == "__auto__":
            log_path = datetime.now().strftime("device-tester-%Y%m%d-%H%M%S.log")
        if open_log(log_path):
            out(f"# Log: {log_path}  (start {datetime.now().strftime('%Y-%m-%d %H:%M:%S')})")

    if args.list:
        list_devices()
        close_log()
        return 0

    hwnd = create_message_window()
    ok = register_devices(hwnd)
    if ok == 0:
        out("Nie udało się zarejestrować żadnego typu urządzenia. Przerywam.")
        return 1

    out("=" * 70)
    out(" TESTER URZĄDZEŃ WEJŚCIOWYCH — nasłuch aktywny")
    out("=" * 70)
    list_devices()
    out("")
    out("Wciskaj klawisze / przyciski pada / pilota / myszy — pojawią się poniżej.")
    out("Działa też gdy to okno nie ma fokusu. Zatrzymanie: Ctrl+C.")
    if not _verbose:
        out("(ruch myszy i pełne raporty HID ukryte — użyj --verbose aby pokazać)")
    out("=" * 70)

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except (ValueError, OSError):
        pass  # np. nie w głównym wątku — fallback na KeyboardInterrupt niżej

    try:
        message_loop()
    except KeyboardInterrupt:
        _running = False
    finally:
        out("\nZatrzymano.")
        close_log()
    return 0


if __name__ == "__main__":
    sys.exit(main())
