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
  - grupuje kolekcje HID jednego fizycznego urządzenia pod wspólnym tagiem #N,
  - kolorowe, jednoliniowe wyjście (kolory NIGDY nie trafiają do pliku logu),
  - dla nieznanych urządzeń HID pokazuje, które bajty raportu się zmieniły
    (uniwersalne „co zostało wciśnięte" dla dowolnego pada/pilota), z podziałem na Report ID,
  - filtry urządzeń i typów, podsumowanie sesji po Ctrl+C,
  - nie wymaga uprawnień administratora.

Uruchomienie:
    python device_tester.py                 # klawisze / przyciski / kółko / HID
    python device_tester.py --verbose       # dodatkowo ruch myszy i pełne raporty HID
    python device_tester.py --list          # tylko wypisz podłączone urządzenia i zakończ
    python device_tester.py --log plik.log  # dodatkowo zapisuj wszystko do pliku na bieżąco
    python device_tester.py --only mouse    # tylko mysz
    python device_tester.py --device 1B1C:1B55
    python device_tester.py --self-test     # testy wewnętrzne bez urządzeń

Zatrzymanie: Ctrl+C.


MAPA PLIKU
==========
Plik jest celowo jeden — narzędzie diagnostyczne ma dać się skopiować na cudzą
maszynę i uruchomić bez instalowania czegokolwiek. Warstwy są rozdzielone tak,
że dekodery i renderowanie nie dotykają Win32 i dają się testować bez urządzenia.
Szukaj nagłówków sekcji (`grep "^#  "`), kolejność od góry:

  Stałe i struktury Win32 ....... stałe raw input, RAWINPUT, HIDP_*, WNDCLASS
  Prototypy Win32 ............... _load_win32(); jedyne miejsce ładujące DLL-e
  Konsola ....................... tryb VT, szerokość/wysokość okna
  Kolory i renderowanie ......... segmenty (tekst, styl) -> render_plain/render_ansi
  Ujścia strukturalne ........... event_to_dict, JsonlSink, CsvSink
  Wyjście ....................... Output: konsola + log + eksport, scalanie ×N
  Informacje o urządzeniach ..... DeviceInfo, DeviceGroup, DeviceRegistry
  Model zdarzenia ............... Event, merge_events
  Układ wiersza ................. Layout, make_renderer
  Dekodery ...................... decode_keyboard / decode_mouse / decode_hid
  Nazwy HID Usage ............... _USAGE_NAMES, usage_name, HidDecoder
  Pomiary ....................... RateMeter, ChatterDetector, AnalogTracker
  Filtry i statystyki ........... Filters, Stats
  Panel na żywo ................. Dashboard (region przewijania DECSTBM)
  Sesja przechwytywania ......... Capture: okno komunikatów, WM_INPUT, pętla
  Wyjścia jednorazowe ........... print_devices, dump_caps, self_test
  CLI ........................... Config, build_parser, main

Zasady, które łatwo złamać przy zmianach:
  - WM_INPUT MUSI trafić do DefWindowProc, inaczej system nie zwolni bufora;
    dlatego obsługa zdarzenia siedzi w try/except (patrz Capture._wnd_proc).
  - Kolory nigdy nie trafiają do pliku logu ani do eksportu — kolorowanie
    zachodzi dopiero przy renderowaniu do konsoli (Output.event).
  - Treść zdarzenia nie jest obcinana; do szerokości konsoli docinane są
    wyłącznie metadane (fit_segments).
  - Znacznik czasu powstaje na wejściu do Capture.on_raw_input, przed
    rozwiązywaniem urządzenia — od tego zależą --hz i --chatter.
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import json
import os
import re
import signal
import sys
import time
import zlib
from ctypes import wintypes

IS_WINDOWS = sys.platform == "win32"

# --------------------------------------------------------------------------- #
#  Konsola: wymuś UTF-8 (polskie nazwy klawiszy) i niebuforowane wyjście.
# --------------------------------------------------------------------------- #
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# --------------------------------------------------------------------------- #
#  Typy pomocnicze / stałe Win32 (czyste dane — brak zależności od DLL).
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
RIDEV_REMOVE = 0x00000001
RIDEV_INPUTSINK = 0x00000100        # odbieraj wejście nawet bez fokusu
RIDEV_DEVNOTIFY = 0x00002000        # powiadomienia o podłączeniu/odłączeniu

# GetRawInputData
RID_INPUT = 0x10000003
RID_HEADER = 0x10000005

# GetRawInputDeviceInfo
RIDI_PREPARSEDDATA = 0x20000005
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000B

RIDI_TYPE_NAMES = {RIM_TYPEMOUSE: "MOUSE", RIM_TYPEKEYBOARD: "KEYBOARD", RIM_TYPEHID: "HID"}
RIDI_TYPE_PL = {RIM_TYPEMOUSE: "MYSZY", RIM_TYPEKEYBOARD: "KLAWIATURY", RIM_TYPEHID: "HID / POZOSTAŁE"}

# WM_INPUT_DEVICE_CHANGE wParam
GIDC_ARRIVAL = 1
GIDC_REMOVAL = 2

# Flagi klawiatury
RI_KEY_MAKE = 0x00
RI_KEY_BREAK = 0x01
RI_KEY_E0 = 0x02
RI_KEY_E1 = 0x04

# Flagi RAWMOUSE.usFlags
MOUSE_MOVE_RELATIVE = 0x00
MOUSE_MOVE_ABSOLUTE = 0x01
MOUSE_VIRTUAL_DESKTOP = 0x02
MOUSE_ATTRIBUTES_CHANGED = 0x04

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

RI_MOUSE_ANY_BUTTON = 0x03FF

MOUSE_BUTTON_EVENTS = (
    (RI_MOUSE_LEFT_BUTTON_DOWN, "LPM ↓", True),
    (RI_MOUSE_LEFT_BUTTON_UP, "LPM ↑", False),
    (RI_MOUSE_RIGHT_BUTTON_DOWN, "PPM ↓", True),
    (RI_MOUSE_RIGHT_BUTTON_UP, "PPM ↑", False),
    (RI_MOUSE_MIDDLE_BUTTON_DOWN, "ŚPM ↓", True),
    (RI_MOUSE_MIDDLE_BUTTON_UP, "ŚPM ↑", False),
    (RI_MOUSE_BUTTON_4_DOWN, "PRZ4 ↓", True),
    (RI_MOUSE_BUTTON_4_UP, "PRZ4 ↑", False),
    (RI_MOUSE_BUTTON_5_DOWN, "PRZ5 ↓", True),
    (RI_MOUSE_BUTTON_5_UP, "PRZ5 ↑", False),
)

# Konsola / VT
STD_OUTPUT_HANDLE = -11
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

# MsgWaitForMultipleObjectsEx
QS_ALLINPUT = 0x04FF
PM_REMOVE = 0x0001
MWMO_INPUTAVAILABLE = 0x0004
WAIT_FAILED = 0xFFFFFFFF

UINT_ERR = 0xFFFFFFFF   # (UINT)-1 zwracane przez API raw input przy błędzie

# Top-level HID collections, które chcemy złapać (Generic Desktop + Consumer).
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


# Przesunięcie bajtów raportu HID względem początku RAWINPUT — do kontroli granic bufora.
HID_DATA_OFFSET = RAWINPUT.data.offset + RAWHID.bRawData.offset


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


# --------------------------------------------------------------------------- #
#  Struktury HID Parsing Library (hid.dll) — tłumaczenie surowych raportów na
#  nazwane przyciski i osie.
# --------------------------------------------------------------------------- #
HIDP_INPUT = 0

HIDP_STATUS_SUCCESS = 0x00110000
HIDP_STATUS_INCOMPATIBLE_REPORT_ID = 0xC011000A
HIDP_STATUS_USAGE_NOT_FOUND = 0xC0110004
HIDP_STATUS_INVALID_REPORT_LENGTH = 0xC0110003
HIDP_STATUS_INVALID_REPORT_TYPE = 0xC0110002

USAGE = wintypes.USHORT
BOOLEAN = ctypes.c_ubyte
UCHAR = ctypes.c_ubyte


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", USAGE),
        ("UsagePage", USAGE),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _HIDP_RANGE(ctypes.Structure):
    _fields_ = [
        ("UsageMin", USAGE), ("UsageMax", USAGE),
        ("StringMin", wintypes.USHORT), ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT), ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT), ("DataIndexMax", wintypes.USHORT),
    ]


class _HIDP_NOTRANGE(ctypes.Structure):
    _fields_ = [
        ("Usage", USAGE), ("Reserved1", USAGE),
        ("StringIndex", wintypes.USHORT), ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT), ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT), ("Reserved4", wintypes.USHORT),
    ]


class _HIDP_CAPS_U(ctypes.Union):
    _fields_ = [("Range", _HIDP_RANGE), ("NotRange", _HIDP_NOTRANGE)]


class HIDP_BUTTON_CAPS(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("UsagePage", USAGE),
        ("ReportID", UCHAR),
        ("IsAlias", BOOLEAN),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", USAGE),
        ("LinkUsagePage", USAGE),
        ("IsRange", BOOLEAN),
        ("IsStringRange", BOOLEAN),
        ("IsDesignatorRange", BOOLEAN),
        ("IsAbsolute", BOOLEAN),
        ("Reserved", wintypes.ULONG * 10),
        ("u", _HIDP_CAPS_U),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [
        ("UsagePage", USAGE),
        ("ReportID", UCHAR),
        ("IsAlias", BOOLEAN),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", USAGE),
        ("LinkUsagePage", USAGE),
        ("IsRange", BOOLEAN),
        ("IsStringRange", BOOLEAN),
        ("IsDesignatorRange", BOOLEAN),
        ("IsAbsolute", BOOLEAN),
        ("HasNull", BOOLEAN),
        ("Reserved", UCHAR),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", wintypes.LONG), ("LogicalMax", wintypes.LONG),
        ("PhysicalMin", wintypes.LONG), ("PhysicalMax", wintypes.LONG),
        ("u", _HIDP_CAPS_U),
    ]


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT),
                ("Right", wintypes.SHORT), ("Bottom", wintypes.SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", COORD),
        ("dwCursorPosition", COORD),
        ("wAttributes", wintypes.WORD),
        ("srWindow", SMALL_RECT),
        ("dwMaximumWindowSize", COORD),
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
#  Ładowane wyłącznie na Windows, więc dekodery i renderowanie nie zależą od
#  żadnego uchwytu DLL i dają się testować bez podłączonego urządzenia.
#  (Sam moduł wymaga Windows — `ctypes.wintypes` nie importuje się gdzie indziej.)
# --------------------------------------------------------------------------- #
user32 = kernel32 = hid = None

HWND_MESSAGE = wintypes.HWND(-3)
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3


def _load_win32() -> None:
    """Załaduj biblioteki systemowe i ustaw prototypy. Wołane raz, tylko na Windows."""
    global user32, kernel32, hid
    if user32 is not None:
        return

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    hid = ctypes.WinDLL("hid", use_last_error=True)

    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
    user32.RegisterClassW.restype = wintypes.ATOM

    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND

    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = wintypes.BOOL

    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL

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

    user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    user32.MapVirtualKeyW.restype = wintypes.UINT

    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None

    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.GetStdHandle.restype = wintypes.HANDLE

    kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetConsoleMode.restype = wintypes.BOOL

    kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.SetConsoleMode.restype = wintypes.BOOL

    kernel32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE,
                                                    ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
    kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL

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

    # NTSTATUS jako ULONG: kody błędów HIDP_STATUS_* mają ustawiony najstarszy bit,
    # więc jako LONG byłyby ujemne i porównania z 0xC011000A wymagałyby maskowania.
    hid.HidP_GetCaps.argtypes = [wintypes.LPVOID, ctypes.POINTER(HIDP_CAPS)]
    hid.HidP_GetCaps.restype = wintypes.ULONG

    hid.HidP_GetButtonCaps.argtypes = [
        ctypes.c_int, ctypes.POINTER(HIDP_BUTTON_CAPS),
        ctypes.POINTER(wintypes.USHORT), wintypes.LPVOID,
    ]
    hid.HidP_GetButtonCaps.restype = wintypes.ULONG

    hid.HidP_GetValueCaps.argtypes = [
        ctypes.c_int, ctypes.POINTER(HIDP_VALUE_CAPS),
        ctypes.POINTER(wintypes.USHORT), wintypes.LPVOID,
    ]
    hid.HidP_GetValueCaps.restype = wintypes.ULONG

    hid.HidP_MaxUsageListLength.argtypes = [ctypes.c_int, USAGE, wintypes.LPVOID]
    hid.HidP_MaxUsageListLength.restype = wintypes.ULONG

    hid.HidP_GetUsages.argtypes = [
        ctypes.c_int, USAGE, wintypes.USHORT, ctypes.POINTER(USAGE),
        ctypes.POINTER(wintypes.ULONG), wintypes.LPVOID, ctypes.c_char_p, wintypes.ULONG,
    ]
    hid.HidP_GetUsages.restype = wintypes.ULONG

    hid.HidP_GetUsageValue.argtypes = [
        ctypes.c_int, USAGE, wintypes.USHORT, USAGE,
        ctypes.POINTER(wintypes.ULONG), wintypes.LPVOID, ctypes.c_char_p, wintypes.ULONG,
    ]
    hid.HidP_GetUsageValue.restype = wintypes.ULONG


if IS_WINDOWS:
    _load_win32()


# --------------------------------------------------------------------------- #
#  Konsola: tryb VT (kolory) i szerokość okna.
# --------------------------------------------------------------------------- #
_vt_restore = None   # (handle, poprzedni_tryb) — do przywrócenia przy wyjściu


def console_supports_vt() -> bool:
    """Włącz ENABLE_VIRTUAL_TERMINAL_PROCESSING na uchwycie stdout.
    Zwraca True jeśli konsola obsługuje sekwencje ANSI."""
    global _vt_restore
    if not IS_WINDOWS or kernel32 is None:
        return False
    try:
        h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if not h or h == INVALID_HANDLE_VALUE:
            return False
        mode = wintypes.DWORD(0)
        if not kernel32.GetConsoleMode(h, ctypes.byref(mode)):
            return False        # stdout przekierowany do pliku/potoku
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        if kernel32.SetConsoleMode(h, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING):
            _vt_restore = (h, mode.value)
            return True
        return False
    except Exception:
        return False


def restore_console() -> None:
    """Przywróć tryb konsoli zmieniony przez console_supports_vt(). Nigdy nie rzuca."""
    global _vt_restore
    st, _vt_restore = _vt_restore, None
    if st is None or kernel32 is None:
        return
    try:
        kernel32.SetConsoleMode(st[0], st[1])
    except Exception:
        pass


def console_width(default: int = 100) -> int:
    """Realna szerokość okna konsoli. shutil.get_terminal_size() bywa nieprawdziwe
    na Windows (zwraca 80 przy szerszym oknie) — czytamy srWindow."""
    if IS_WINDOWS and kernel32 is not None:
        try:
            h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            if h and h != INVALID_HANDLE_VALUE:
                info = CONSOLE_SCREEN_BUFFER_INFO()
                if kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
                    w = info.srWindow.Right - info.srWindow.Left + 1
                    # Zwracamy realną szerokość także dla bardzo wąskiego okna —
                    # podstawienie 100 sprawiłoby, że każda linia by się zawijała,
                    # a scalanie powtórzeń nadpisywałoby zły wiersz.
                    if w >= 20:
                        return w
        except Exception:
            pass
    try:
        w = int(os.environ.get("COLUMNS", "0"))
        if w >= 20:
            return w
    except ValueError:
        pass
    return default


def console_height(default: int = 30) -> int:
    """Liczba widocznych wierszy okna konsoli — potrzebna do regionu przewijania."""
    if IS_WINDOWS and kernel32 is not None:
        try:
            h = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
            if h and h != INVALID_HANDLE_VALUE:
                info = CONSOLE_SCREEN_BUFFER_INFO()
                if kernel32.GetConsoleScreenBufferInfo(h, ctypes.byref(info)):
                    rows = info.srWindow.Bottom - info.srWindow.Top + 1
                    if rows >= 5:
                        return rows
        except Exception:
            pass
    try:
        rows = int(os.environ.get("LINES", "0"))
        if rows >= 5:
            return rows
    except ValueError:
        pass
    return default


def stdout_is_console() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Kolory i renderowanie.
#
#  Treść jest budowana jako lista segmentów (tekst, styl). Styl to KLUCZ palety,
#  nigdy gotowy kod ANSI — dzięki temu ten sam segment renderuje się do konsoli
#  z kolorem i do pliku logu bez ani jednego bajtu ESC.
# --------------------------------------------------------------------------- #
RESET = "\x1b[0m"

PALETTE = {
    "ts": "\x1b[38;5;240m",
    "meta": "\x1b[38;5;240m",
    "frame": "\x1b[38;5;238m",
    "head": "\x1b[1;38;5;252m",
    "kind": "\x1b[38;5;245m",
    "down": "\x1b[1;38;5;40m",
    "up": "\x1b[38;5;244m",
    "repeat": "\x1b[38;5;220m",
    "mouse": "\x1b[38;5;45m",
    "wheel": "\x1b[38;5;177m",
    "hid": "\x1b[38;5;214m",
    "hidchg": "\x1b[1;38;5;214m",
    "arrival": "\x1b[1;38;5;46m",
    "removal": "\x1b[1;38;5;196m",
    "warn": "\x1b[38;5;208m",
}

# Kolory przydzielane urządzeniom — rozłączne z paletą semantyczną powyżej.
DEVICE_COLORS = (39, 79, 111, 141, 170, 208, 116, 151, 186, 210, 75, 180, 147, 114)


def device_style(group_key: str) -> str:
    """Stabilny kolor urządzenia. crc32, NIE hash() — hash() stringów jest solony
    per proces, więc kolory zmieniałyby się przy każdym uruchomieniu."""
    idx = zlib.crc32(group_key.encode("utf-8", "replace")) % len(DEVICE_COLORS)
    return "\x1b[38;5;%dm" % DEVICE_COLORS[idx]


def render_plain(segs) -> str:
    return "".join(text for text, _ in segs)


def fit_segments(segs, limit: int):
    """Dociśnij linię do szerokości konsoli, obcinając WYŁĄCZNIE metadane.

    Treść zdarzenia (pełny raport HID, długa nazwa klawisza) nigdy nie jest tracona —
    jeśli i tak się nie mieści, linia się zawinie, a wywołujący wyłączy scalanie ×N.
    Stosowane tylko do konsoli: plik logu dostaje pełną wersję, żeby jego zawartość
    nie zależała od rozmiaru okna.
    """
    total = sum(len(t) for t, _ in segs)
    if total <= limit:
        return segs
    segs = list(segs)
    while total > limit and segs and segs[-1][1] == "meta":
        text, style = segs[-1]
        keep = len(text) - (total - limit)
        if keep <= 0:
            total -= len(text)
            segs.pop()
            while segs and not segs[-1][0].strip():
                total -= len(segs[-1][0])
                segs.pop()
        else:
            segs[-1] = (trunc(text, keep), style)
            break
    return segs


def render_ansi(segs) -> str:
    parts = []
    for text, style in segs:
        if not text:
            continue
        if style is None:
            parts.append(text)
        else:
            code = PALETTE.get(style, style if style.startswith("\x1b") else None)
            parts.append(text if code is None else code + text + RESET)
    return "".join(parts)


class Box:
    """Ramka o dokładnie `width` znakach szerokości. Wszystkie krawędzie liczone
    w jednym miejscu — ręczne arytmetyki rozjeżdżały prawą krawędź o znak."""

    def __init__(self, out: "Output", width: int):
        self.out = out
        self.w = max(20, width)
        self.content_w = self.w - 4        # "│ " + treść + " │"

    def _edge(self, left: str, right: str, title: str = "") -> None:
        # Tytuł musi się zmieścić, inaczej krawędź byłaby dłuższa od reszty ramki
        # i zawinęłaby się — czyli dokładnie ten defekt, który ta klasa eliminuje.
        head = "%s─ %s " % (left, trunc(title, max(0, self.w - 5))) if title else left
        self.out.segments([(head + "─" * max(0, self.w - 1 - len(head)) + right, "frame")])

    def top(self, title: str = "") -> None:
        self._edge("┌", "┐", title)

    def sep(self, title: str = "") -> None:
        self._edge("├", "┤", title)

    def bottom(self) -> None:
        self.out.segments([("└" + "─" * (self.w - 2) + "┘", "frame")])

    def row(self, text: str, style=None) -> None:
        self.out.segments([("│ ", "frame"), (pad(text, self.content_w), style), (" │", "frame")])


def trunc(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    return s[:width - 1] + "…"


def pad(s: str, width: int) -> str:
    return trunc(s, width).ljust(width)


# --------------------------------------------------------------------------- #
#  Ujścia strukturalne: eksport zdarzeń do formatów nadających się do liczenia,
#  a nie tylko do czytania.
# --------------------------------------------------------------------------- #
def event_to_dict(ev: "Event") -> dict:
    """Zdarzenie jako płaski słownik. Pola opisowe (body/meta) obok strukturalnych,
    żeby eksport dało się czytać oczami i przetwarzać maszynowo."""
    di = ev.dev
    g = di.group if di is not None else None
    rec = {
        "time": "%sT%s" % (_ts_day(), ev.ts),
        "kind": ev.kind,
        "body": ev.body,
    }
    if ev.meta:
        rec["meta"] = ev.meta
    if di is not None:
        rec["device"] = {
            "tag": g.tag_text() if g is not None else None,
            "name": g.name if g is not None else di.name(),
            "type": RIDI_TYPE_NAMES.get(ev.ev_type if ev.ev_type is not None
                                         else di.type),
            "vid": "%04X" % di.vid if di.vid is not None else None,
            "pid": "%04X" % di.pid if di.pid is not None else None,
            "usage_page": "%04X" % di.usage_page if di.usage_page is not None else None,
            "usage": "%04X" % di.usage if di.usage is not None else None,
            "handle": di.handle,
        }
    if ev.kname is not None:
        rec["key"] = ev.kname
        rec["down"] = not ev.is_break
        if ev.hold_ms is not None:
            rec["hold_ms"] = ev.hold_ms
    if ev.dx or ev.dy:
        rec["dx"], rec["dy"] = ev.dx, ev.dy
    return rec


class FileSink:
    """Wspólna obsługa pliku: dopisywanie, flush po każdym rekordzie, błąd zapisu
    nie przerywa nasłuchu."""

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "a", encoding="utf-8", errors="replace",
                        buffering=1, newline="")
        self._errors = 0

    def _write(self, text: str) -> None:
        try:
            self._fh.write(text)
            self._fh.flush()
        except Exception:
            self._errors += 1

    def close(self) -> None:
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass


class JsonlSink(FileSink):
    """Jeden obiekt JSON na linię. Strumieniowy: przerwanie procesu nie psuje
    pliku, w przeciwieństwie do jednej wielkiej tablicy JSON."""

    def event(self, ev: "Event") -> None:
        try:
            self._write(json.dumps(event_to_dict(ev), ensure_ascii=False) + "\n")
        except Exception:
            self._errors += 1


class CsvSink(FileSink):
    """Płaskie kolumny do arkusza. Zagnieżdżone dane urządzenia rozwinięte."""

    COLUMNS = ("time", "tag", "device", "dev_type", "vid", "pid", "kind",
               "event", "meta", "key", "down", "hold_ms", "dx", "dy")

    def __init__(self, path: str):
        write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        FileSink.__init__(self, path)
        self._w = csv.writer(self._fh, lineterminator="\n")
        if write_header:
            self._w.writerow(self.COLUMNS)
            self._fh.flush()

    def event(self, ev: "Event") -> None:
        try:
            r = event_to_dict(ev)
            d = r.get("device") or {}
            self._w.writerow([
                r["time"], d.get("tag"), d.get("name"), d.get("type"),
                d.get("vid"), d.get("pid"), r["kind"], r["body"], r.get("meta", ""),
                r.get("key", ""), r.get("down", ""), r.get("hold_ms", ""),
                r.get("dx", ""), r.get("dy", ""),
            ])
            self._fh.flush()
        except Exception:
            self._errors += 1


# --------------------------------------------------------------------------- #
#  Wyjście: konsola (opcjonalnie kolor + scalanie powtórzeń) + plik logu (zawsze
#  czysty tekst, jedna linia na KAŻDE zdarzenie).
# --------------------------------------------------------------------------- #
class Output:
    def __init__(self, color: bool = False, dedup: bool = True, width: int = 100):
        self.color = color
        self.dedup = dedup and color and stdout_is_console()
        self.width = width
        self._log_fh = None
        self._log_errors = 0
        self._log_warned = False
        self.sinks = []            # JsonlSink / CsvSink — eksport strukturalny
        # Liczba linii dopisanych na dole ekranu. Nadpisania w miejscu (scalanie ×N)
        # NIE są liczone, bo nie przesuwają kursora. Dashboard wylicza z tego, w którym
        # wierszu stoi kursor — terminala nie da się o to zapytać.
        self.lines_out = 0
        # stan scalania powtórzeń ostatniej linii konsoli
        self._last_key = None
        self._last_ev = None
        self._last_count = 0
        self._last_fits = False

    # -- log ---------------------------------------------------------------- #
    def open_log(self, path: str) -> bool:
        """Otwórz plik logu do dopisywania (append), UTF-8, line-buffered.
        Zwraca True jeśli się udało. Nigdy nie rzuca — błąd tylko sygnalizuje."""
        try:
            self._log_fh = open(path, "a", encoding="utf-8", errors="replace",
                                buffering=1, newline="")
            return True
        except Exception as e:
            self._log_fh = None
            try:
                sys.stderr.write(f"[uwaga] nie udało się otworzyć pliku logu '{path}': {e!r}\n")
                sys.stderr.flush()
            except Exception:
                pass
            return False

    def add_sink(self, kind: str, path: str) -> bool:
        """Podłącz eksport strukturalny. Błąd nie przerywa nasłuchu."""
        try:
            self.sinks.append((JsonlSink if kind == "jsonl" else CsvSink)(path))
            return True
        except Exception as e:
            try:
                sys.stderr.write("[uwaga] nie udało się otworzyć '%s': %r\n" % (path, e))
                sys.stderr.flush()
            except Exception:
                pass
            return False

    def close_log(self) -> None:
        fh, self._log_fh = self._log_fh, None
        if fh is not None:
            try:
                fh.flush()
                fh.close()
            except Exception:
                pass
        sinks, self.sinks = self.sinks, []
        for s in sinks:
            s.close()

    def _to_log(self, line: str) -> None:
        fh = self._log_fh
        if fh is None:
            return
        try:
            fh.write(line + "\n")
            fh.flush()
        except Exception:
            # Logowanie nie może przerwać nasłuchu, ale nie może też zniknąć bez śladu
            # (np. zapełniony dysk) — ostrzeż raz i licz dalej.
            self._log_errors += 1
            if not self._log_warned:
                self._log_warned = True
                try:
                    sys.stderr.write("[uwaga] błąd zapisu do pliku logu — dalsze błędy pomijam\n")
                    sys.stderr.flush()
                except Exception:
                    pass

    # -- konsola ------------------------------------------------------------ #
    def _write(self, text: str) -> None:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except Exception:
            pass

    def _count_rows(self, plain: str) -> None:
        """Policz WIERSZE EKRANU, nie linie logiczne — linia dłuższa niż okno
        zawija się i zjada ich kilka. Dashboard odtwarza z tego pozycję kursora."""
        self.lines_out += 1 + max(0, len(plain) - 1) // max(1, self.width)

    def write_raw(self, text: str) -> None:
        """Sekwencje sterujące terminala — nigdy do logu ani do eksportu."""
        self._write(text)

    def raw(self, line: str = "") -> None:
        """Zwykła linia (baner, nagłówek, komunikat) — bez scalania powtórzeń."""
        self._reset_dedup()
        self._write(line + "\n")
        self._count_rows(line)
        self._to_log(line)

    def segments(self, segs) -> None:
        """Linia zbudowana z segmentów — kolor tylko na konsoli."""
        self._reset_dedup()
        plain = render_plain(segs)
        self._write((render_ansi(segs) if self.color else plain) + "\n")
        self._count_rows(plain)
        self._to_log(plain)

    def event(self, ev: "Event", renderer) -> None:
        """Zdarzenie: konsola scala powtórzenia w miejscu (×N), log dostaje
        każdą linię osobno wraz z jej własnym znacznikiem czasu.

        Treść logu jest niezależna od szerokości okna konsoli — plik ma być
        wiarygodnym zapisem sesji, a nie zrzutem tego, co akurat się zmieściło.
        """
        segs = renderer(ev, 1)
        if self._log_fh is not None:
            # Pełna wersja, przed docięciem do konsoli.
            self._to_log(render_plain(segs))
        for s in self.sinks:
            s.event(ev)

        if self.dedup and ev.key is not None and ev.key == self._last_key \
                and self._last_fits:
            self._last_count += 1
            merged = merge_events(self._last_ev, ev)
            self._last_ev = merged
            msegs = fit_segments(renderer(merged, self._last_count), self.width - 1)
            mplain = render_plain(msegs)
            if len(mplain) < self.width:
                # Nadpisz poprzednią linię w miejscu (kursor w górę + wyczyść).
                line = render_ansi(msegs) if self.color else mplain
                self._write("\x1b[1A\r\x1b[2K" + line + "\n")
                return
            # Scalona linia przestała się mieścić — przerwij scalanie zamiast
            # nadpisywać zły wiersz (\x1b[1A cofa kursor tylko o jeden wiersz,
            # a zawinięta linia zajmuje ich kilka).
            self._reset_dedup()

        csegs = fit_segments(segs, self.width - 1)
        cplain = render_plain(csegs)
        self._write((render_ansi(csegs) if self.color else cplain) + "\n")
        self._count_rows(cplain)
        self._last_key = ev.key
        self._last_ev = ev
        self._last_count = 1
        self._last_fits = len(cplain) < self.width

    def _reset_dedup(self) -> None:
        self._last_key = None
        self._last_ev = None
        self._last_count = 0
        self._last_fits = False


# Globalne wyjście — ustawiane w main(), żeby moduł dało się importować bez efektów ubocznych.
_out = Output()


def out(line: str) -> None:
    """Kompatybilny skrót: wypisz linię tekstu na konsolę i do logu."""
    _out.raw(line)


# --------------------------------------------------------------------------- #
#  Znacznik czasu — memoizowany na sekundę (strftime jest drogie na gorącej ścieżce).
# --------------------------------------------------------------------------- #
_ts_sec = -1
_ts_str = ""
_ts_day_sec = -1
_ts_day_str = ""


def _ts_day() -> str:
    """Data bieżącego dnia, memoizowana — używana tylko przy eksporcie."""
    global _ts_day_sec, _ts_day_str
    sec = int(time.time())
    if sec != _ts_day_sec:
        _ts_day_sec = sec
        _ts_day_str = time.strftime("%Y-%m-%d", time.localtime(sec))
    return _ts_day_str


def _ts() -> str:
    global _ts_sec, _ts_str
    t = time.time()
    sec = int(t)
    if sec != _ts_sec:
        _ts_sec = sec
        _ts_str = time.strftime("%H:%M:%S", time.localtime(sec))
    ms = int((t - sec) * 1000.0)
    if ms > 999:
        ms = 999
    return "%s.%03d" % (_ts_str, ms)


# --------------------------------------------------------------------------- #
#  Informacje o urządzeniach.
# --------------------------------------------------------------------------- #
_VIDPID_RE = re.compile(r"VID_([0-9A-Fa-f]{4})&PID_([0-9A-Fa-f]{4})")
# Bluetooth ma inny format ścieżki:
#   \\?\HID#{00001812-...}_VID&0002045e_PID&0b13&Col01#...
# gdzie po VID& stoi 8 cyfr (pierwsze 4 to źródło identyfikatora, nie producent).
_VIDPID_BT_RE = re.compile(r"_VID&[0-9A-Fa-f]{4}([0-9A-Fa-f]{4})_PID&([0-9A-Fa-f]{4})")
_MI_RE = re.compile(r"&MI_([0-9A-Fa-f]{2})")
_COL_RE = re.compile(r"&Col([0-9A-Fa-f]{2})")


def parse_vid_pid(path: str):
    """Wyciągnij VID/PID ze ścieżki urządzenia. Działa dla wszystkich typów,
    nie tylko HID — RID_DEVICE_INFO podaje je wyłącznie dla RIM_TYPEHID."""
    if not path:
        return None, None
    m = _VIDPID_RE.search(path) or _VIDPID_BT_RE.search(path)
    if not m:
        return None, None
    return int(m.group(1), 16), int(m.group(2), 16)


def parse_collection(path: str):
    """(numer interfejsu USB, numer kolekcji HID) ze ścieżki. Brak = 0.

    Kolejność tych dwóch liczb wyznacza funkcję główną urządzenia złożonego:
    pierwsza kolekcja pierwszego interfejsu to ta, do czego urządzenie służy.
    Bez tego mysz z kolekcją klawiatury (makra) trafiała do sekcji KLAWIATURY,
    a klawiatura z kolekcją myszy — do MYSZY.
    """
    mi = _MI_RE.search(path or "")
    col = _COL_RE.search(path or "")
    return (int(mi.group(1), 16) if mi else 0,
            int(col.group(1), 16) if col else 0)


class DeviceInfo:
    __slots__ = ("handle", "type", "path", "vid", "pid", "usage_page", "usage",
                 "product", "manufacturer", "version", "group", "extra", "order",
                 "complete", "attempts", "decoder", "_label")

    def __init__(self, handle: int):
        self.handle = handle
        self.complete = False      # czy dane systemowe udało się w pełni odczytać
        self.attempts = 0
        self.type = None
        self.path = ""
        self.vid = None
        self.pid = None
        self.usage_page = None
        self.usage = None
        self.product = ""
        self.manufacturer = ""
        self.version = None
        self.group = None          # DeviceGroup
        self.extra = ""            # dodatkowe dane z RID_DEVICE_INFO (do --list)
        self.order = (0, 0)        # (interfejs, kolekcja) — kolejność w urządzeniu złożonym
        self.decoder = None        # HidDecoder albo None (wtedy surowy diff bajtów)
        self._label = None

    def usage_text(self) -> str:
        if self.usage_page is None:
            return "-"
        return "%04X/%02X" % (self.usage_page, self.usage)

    # Klucz grupowania: fizyczne urządzenie zwykle wystawia kilka kolekcji HID,
    # każda z własnym uchwytem. VID:PID jest stabilne między przepięciami USB,
    # w przeciwieństwie do hDevice.
    def group_key(self) -> str:
        if self.vid is not None and self.pid is not None:
            return "%04X:%04X" % (self.vid, self.pid)
        if self.path:
            return self.path.rsplit("#", 1)[0]
        return "handle:%d" % self.handle

    def name(self) -> str:
        if self.product:
            return self.product
        if self.manufacturer:
            return self.manufacturer
        if self.handle in (0, None):
            # hDevice == NULL to wejście wstrzyknięte (SendInput, hooki, oprogramowanie
            # producenta), a nie fizyczne urządzenie.
            return "wstrzyknięte"
        if "#" in self.path:
            return self.path.split("#")[1]
        return self.path or "urządzenie"

    def label(self) -> str:
        """Pełny opis (używany w --list i w komunikatach o podłączeniu)."""
        if self._label is not None:
            return self._label
        tname = RIDI_TYPE_NAMES.get(self.type, "?")
        if self.handle in (0, None) and not self.product:
            self._label = f"wstrzyknięte/systemowe ({tname})"
            return self._label
        ids = ""
        if self.vid is not None and self.pid is not None:
            ids = f" VID:{self.vid:04X} PID:{self.pid:04X}"
        if self.usage_page is not None:
            # %04X, nie %02X — strony vendor (FF02) mają 4 cyfry i rozjeżdżają kolumny.
            ids += f" [UP:{self.usage_page:04X} U:{self.usage:04X}]"
        self._label = f"{self.name()} ({tname}{ids})"
        return self._label


class DeviceGroup:
    """Fizyczne urządzenie: zbiór kolekcji HID o wspólnym VID:PID."""
    __slots__ = ("key", "tag", "name", "members", "style")

    def __init__(self, key: str, tag: int):
        self.key = key
        self.tag = tag
        self.name = ""
        # Kluczowane uchwytem, nie listą: to samo urządzenie może być rozwiązywane
        # ponownie (np. gdy pierwszy odczyt danych systemowych zawiódł), a lista
        # rosłaby wtedy w nieskończoność.
        self.members = {}
        self.style = device_style(key)

    def tag_text(self) -> str:
        return "#%d" % self.tag

    def all(self):
        return list(self.members.values())

    def refresh_name(self) -> None:
        """Przelicz nazwę z wszystkich kolekcji.

        Prawdziwa nazwa produktu zawsze wygrywa z nazwą wyprowadzoną ze ścieżki:
        HidD_GetProductString bywa niedostępne dla kolekcji vendorowych, a kolejność
        rozwiązywania zależy od GetRawInputDeviceList — bez przeliczania nazwa całego
        urządzenia zależałaby od tego, która kolekcja trafiła do grupy jako pierwsza.
        """
        products = [m.product for m in self.members.values() if m.product]
        if products:
            self.name = max(products, key=len)
        elif not self.name:
            first = next(iter(self.members.values()), None)
            self.name = first.name() if first is not None else ""

    def primary(self) -> DeviceInfo:
        """Kolekcja wyznaczająca funkcję urządzenia: najniższe (interfejs, kolekcja)
        spośród kolekcji myszy/klawiatury, a gdy takich nie ma — pierwsza w ogóle."""
        members = self.all()
        if not members:
            return None
        typed = [m for m in members if m.type in (RIM_TYPEMOUSE, RIM_TYPEKEYBOARD)]
        return min(typed or members, key=lambda m: m.order)

    @property
    def type(self):
        p = self.primary()
        return p.type if p is not None else None

    def kinds_text(self) -> str:
        """Zwięzły opis kolekcji, np. „MYSZ KLAW 3×HID"."""
        counts = {}
        for m in self.members.values():
            counts[m.type] = counts.get(m.type, 0) + 1
        short = {RIM_TYPEKEYBOARD: "KLAW", RIM_TYPEMOUSE: "MYSZ", RIM_TYPEHID: "HID", None: "?"}
        order = (RIM_TYPEKEYBOARD, RIM_TYPEMOUSE, RIM_TYPEHID, None)
        parts = []
        for t in order:
            n = counts.get(t)
            if n:
                parts.append(short[t] if n == 1 else "%d×%s" % (n, short[t]))
        return " ".join(parts)


class DeviceRegistry:
    """Cache urządzeń + grupowanie. Stan trzymany w obiekcie, nie w module —
    dzięki temu testy się nie przeciekają i można mieć dwie niezależne sesje."""

    def __init__(self, name_fn=None, info_fn=None, product_fn=None, decoder_fn=None,
                 decode_hid=True):
        self._by_handle = {}
        self._groups = {}
        self._next_tag = 1
        self._name_fn = name_fn or _get_raw_input_device_name
        self._info_fn = info_fn or _get_raw_input_device_info
        self._product_fn = product_fn or _get_product_strings
        self._decoder_fn = decoder_fn or build_hid_decoder
        self._decode_hid = decode_hid

    @property
    def groups(self):
        return self._groups

    def known(self, handle: int) -> bool:
        return handle in self._by_handle

    def lookup(self, handle: int):
        """Zwróć znane urządzenie albo None — bez tworzenia wpisu i grupy.
        resolve() dla nieistniejącego uchwytu zakładałby widmowe urządzenie
        z nowym tagiem i fałszywym wierszem w podsumowaniu."""
        return self._by_handle.get(handle)

    # Ile razy próbować uzupełnić dane urządzenia, które przy pierwszym zdarzeniu
    # jeszcze się nie „obudziło" (typowe dla urządzeń Bluetooth). Bez limitu każde
    # zdarzenie takiego urządzenia kosztowałoby CreateFileW na gorącej ścieżce.
    MAX_ATTEMPTS = 5

    def forget(self, handle: int):
        di = self._by_handle.pop(handle, None)
        if di is not None:
            self._detach(di)
        return di

    def resolve(self, handle: int, ev_type=None) -> DeviceInfo:
        """Zwróć (i zapamiętaj) opis urządzenia. Wpis niekompletny — np. urządzenie
        BT budzące się w chwili pierwszego zdarzenia — jest ponawiany, a nie
        zamrażany jako „urządzenie (?)" do końca sesji."""
        di = self._by_handle.get(handle)
        if di is None:
            di = DeviceInfo(handle)
            self._by_handle[handle] = di
            self._enrich(di)
        elif not di.complete and di.attempts < self.MAX_ATTEMPTS:
            self._enrich(di)
        if di.type is None and ev_type is not None:
            di.type = ev_type
            di._label = None
        return di

    def _enrich(self, di: DeviceInfo) -> None:
        """Odczytaj z systemu wszystko, co się da. Idempotentne i odporne na błędy."""
        di.attempts += 1
        ok = True
        try:
            if not di.path:
                di.path = self._name_fn(di.handle)
            info = self._info_fn(di.handle)
            if info is None:
                ok = False
            else:
                di.type = info.dwType
                if info.dwType == RIM_TYPEHID:
                    di.vid = info.hid.dwVendorId
                    di.pid = info.hid.dwProductId
                    di.version = info.hid.dwVersionNumber
                    di.usage_page = info.hid.usUsagePage
                    di.usage = info.hid.usUsage
                    di.extra = "wersja %04X" % info.hid.dwVersionNumber
                elif info.dwType == RIM_TYPEMOUSE:
                    m = info.mouse
                    # Kolekcja sklasyfikowana przez Windows jako mysz z definicji jest
                    # Generic Desktop / Mouse; RID_DEVICE_INFO tego pola nie wypełnia.
                    di.usage_page, di.usage = 0x01, 0x02
                    bits = ["%d przyc." % m.dwNumberOfButtons]
                    if m.fHasHorizontalWheel:
                        bits.append("kółko poziome")
                    if m.dwSampleRate:
                        bits.append("%d Hz" % m.dwSampleRate)
                    di.extra = ", ".join(bits)
                elif info.dwType == RIM_TYPEKEYBOARD:
                    k = info.keyboard
                    di.usage_page, di.usage = 0x01, 0x06
                    di.extra = "%d klawiszy, %d funkcyjnych, %d LED" % (
                        k.dwNumberOfKeysTotal, k.dwNumberOfFunctionKeys, k.dwNumberOfIndicators)
            if di.vid is None:
                di.vid, di.pid = parse_vid_pid(di.path)
            di.order = parse_collection(di.path)
            if not di.product:
                di.product, di.manufacturer = self._product_fn(di.path)
        except Exception:
            ok = False
        if di.handle == 0:
            ok = True          # wejście wstrzyknięte nie ma czego uzupełniać
        if ok and self._decode_hid and di.type == RIM_TYPEHID and di.decoder is None:
            di.decoder = self._decoder_fn(di)
        di.complete = ok
        di._label = None
        self._attach_group(di)

    def _detach(self, di: DeviceInfo) -> None:
        g = di.group
        if g is None:
            return
        g.members.pop(di.handle, None)
        di.group = None
        if not g.members:
            self._groups.pop(g.key, None)

    def _attach_group(self, di: DeviceInfo) -> None:
        key = di.group_key()
        if di.group is not None and di.group.key != key:
            # Ponowna próba odczytu znalazła VID/PID — urządzenie przenosi się
            # z grupy zastępczej (po ścieżce) do właściwej.
            self._detach(di)
        g = di.group
        if g is None:
            g = self._groups.get(key)
            if g is None:
                g = DeviceGroup(key, self._next_tag)
                self._next_tag += 1
                self._groups[key] = g
            di.group = g
        g.members[di.handle] = di
        g.refresh_name()

    def sorted_groups(self):
        order = {RIM_TYPEKEYBOARD: 0, RIM_TYPEMOUSE: 1, RIM_TYPEHID: 2, None: 3}
        return sorted(self._groups.values(), key=lambda g: (order.get(g.type, 3), g.tag))


def _get_raw_input_device_name(handle) -> str:
    size = wintypes.UINT(0)
    user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, None, ctypes.byref(size))
    if size.value == 0:
        return ""
    buf = ctypes.create_unicode_buffer(size.value + 1)
    r = user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICENAME, buf, ctypes.byref(size))
    if r == UINT_ERR:
        return ""
    return buf.value


def _get_raw_input_device_info(handle):
    info = RID_DEVICE_INFO()
    info.cbSize = ctypes.sizeof(RID_DEVICE_INFO)
    size = wintypes.UINT(ctypes.sizeof(RID_DEVICE_INFO))
    r = user32.GetRawInputDeviceInfoW(handle, RIDI_DEVICEINFO, ctypes.byref(info), ctypes.byref(size))
    if r == UINT_ERR or r == 0:
        return None
    return info


def _get_product_strings(path: str):
    """Best-effort: nazwa produktu i producenta przez HidD_*String."""
    if not path:
        return "", ""
    h = kernel32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, 0, None)
    if not h or h == INVALID_HANDLE_VALUE:
        return "", ""
    try:
        product = manufacturer = ""
        buf = ctypes.create_unicode_buffer(256)
        if hid.HidD_GetProductString(h, buf, ctypes.sizeof(buf)):
            product = buf.value.strip()
        mbuf = ctypes.create_unicode_buffer(256)
        if hid.HidD_GetManufacturerString(h, mbuf, ctypes.sizeof(mbuf)):
            manufacturer = mbuf.value.strip()
        return product, manufacturer
    except Exception:
        return "", ""
    finally:
        kernel32.CloseHandle(h)


def _get_preparsed_size(handle) -> int:
    """Rozmiar preparsed data — przydatne przy diagnostyce dekodowania HID."""
    size = wintypes.UINT(0)
    r = user32.GetRawInputDeviceInfoW(handle, RIDI_PREPARSEDDATA, None, ctypes.byref(size))
    if r == UINT_ERR:
        return 0
    return size.value


# --------------------------------------------------------------------------- #
#  Model zdarzenia.
# --------------------------------------------------------------------------- #
class Event:
    __slots__ = ("ts", "mono", "dev", "kind", "body", "meta", "key", "style",
                 "dx", "dy", "merge_mode", "kname", "is_break", "hold_ms", "ev_type")

    def __init__(self, ts, mono, dev, kind, body, meta="", key=None, style=None,
                 dx=0, dy=0, merge_mode=None, kname=None, is_break=False, hold_ms=None,
                 ev_type=None):
        # Typ RAW INPUT tego zdarzenia, nie typ urządzenia: uchwyt 0 (wejście
        # wstrzyknięte) niesie zarówno klawiaturę, jak i mysz, więc di.type
        # nie opisuje pojedynczego zdarzenia.
        self.ev_type = ev_type
        self.ts = ts
        self.mono = mono
        self.dev = dev
        self.kind = kind
        self.body = body
        self.meta = meta
        self.key = key
        self.style = style
        self.dx = dx
        self.dy = dy
        # Jak scalać powtórzenia: "sum" (ruch względny), "latest" (pozycja absolutna),
        # None (powtórz tę samą treść z licznikiem).
        self.merge_mode = merge_mode
        # Dane strukturalne dla statystyk. Odczytywanie ich z pola `body` sklejałoby
        # liczniki z formatem wyświetlania („Right Shift" liczyłoby się jako „Right").
        self.kname = kname
        self.is_break = is_break
        self.hold_ms = hold_ms


def merge_events(prev: Event, new: Event) -> Event:
    """Scal powtórzenie z poprzednim zdarzeniem tego samego rodzaju."""
    if prev.merge_mode == "sum":
        # Ruch względny: sumuj przesunięcia, inaczej ×N nie niosłoby informacji.
        dx = prev.dx + new.dx
        dy = prev.dy + new.dy
        return Event(prev.ts, new.mono, prev.dev, prev.kind,
                     "ruch dx=%+d dy=%+d" % (dx, dy), prev.meta, prev.key, prev.style,
                     dx, dy, "sum", ev_type=prev.ev_type)
    if prev.merge_mode == "latest":
        # Pozycja absolutna (ekran dotykowy, tablet, RDP): pokaż BIEŻĄCĄ pozycję.
        # Zatrzymanie pierwszej zamrażałoby linię na starcie ruchu rysika.
        return Event(prev.ts, new.mono, new.dev, new.kind, new.body, new.meta,
                     new.key, new.style, new.dx, new.dy, "latest", ev_type=new.ev_type)
    return prev


# --------------------------------------------------------------------------- #
#  Układ wiersza.
# --------------------------------------------------------------------------- #
class Layout:
    HEAD = ("CZAS", "DEV", "URZĄDZENIE", "TYP", "ZDARZENIE", "METADANE")
    META_W = 26

    def __init__(self, width: int = 100):
        self.width = width
        self.ts_w = 12
        self.tag_w = 4
        self.kind_w = 4
        # Stały narzut: znacznik czasu, tag, typ, separatory i spacja przed metadanymi.
        fixed = self.ts_w + 2 + self.tag_w + 1 + 1 + self.kind_w + 1 + 1
        # Metadane są ostatnie i nie są dopełniane, ale rezerwujemy im tyle, ile
        # zajmuje najdłuższy realny wariant („VK 11 sc 1D E0 · 12345 ms").
        avail = max(12, width - 1 - fixed - self.META_W)
        self.name_w = max(6, min(24, avail * 2 // 5))
        self.body_w = max(6, min(40, avail - self.name_w))

    def header(self):
        h = self.HEAD
        return ("%s  %s %s %s %s %s" % (
            pad(h[0], self.ts_w), pad(h[1], self.tag_w), pad(h[2], self.name_w),
            pad(h[3], self.kind_w), pad(h[4], self.body_w), h[5])).rstrip()


def make_renderer(layout: Layout):
    """Zwróć funkcję (Event, count) -> lista segmentów."""

    def render(ev: Event, count: int = 1):
        g = ev.dev.group if ev.dev is not None else None
        tag = g.tag_text() if g is not None else "-"
        name = g.name if (g is not None and g.name) else (ev.dev.name() if ev.dev else "?")
        dev_style = g.style if g is not None else "meta"

        body = ev.body
        segs = [
            (pad(ev.ts, layout.ts_w), "ts"),
            ("  ", None),
            (pad(tag, layout.tag_w), dev_style),
            (" ", None),
            (pad(name, layout.name_w), dev_style),
            (" ", None),
            (pad(ev.kind, layout.kind_w), "kind"),
            (" ", None),
        ]
        # Treść zdarzenia NIGDY nie jest obcinana — to jest ta informacja, dla której
        # narzędzie istnieje (pełny raport HID potrafi mieć 200 znaków). Kolumna jest
        # tylko dopełniana do minimalnej szerokości, żeby metadane się wyrównały.
        if count > 1:
            rep = " ×%d" % count
            segs.append((body.ljust(max(0, layout.body_w - len(rep))), ev.style))
            segs.append((rep, "repeat"))
        else:
            segs.append((body.ljust(layout.body_w), ev.style))
        if ev.meta:
            segs.append((" ", None))
            segs.append((ev.meta, "meta"))

        # Bez metadanych ostatnia kolumna kończyłaby się wypełnieniem — nie zostawiaj
        # spacji na końcu linii (śmieci w logu, problem przy kopiowaniu z konsoli).
        while segs and not segs[-1][0].strip():
            segs.pop()
        if segs:
            segs[-1] = (segs[-1][0].rstrip(), segs[-1][1])
        return segs

    return render


# --------------------------------------------------------------------------- #
#  Dekodowanie klawiatury.
# --------------------------------------------------------------------------- #
VK_PAUSE = 0x13
VK_NUMLOCK = 0x90
MAPVK_VK_TO_VSC = 0
# Wariant _EX zwraca scancode z prefiksem 0xE0 w starszym bajcie — ale TYLKO dla
# prawych modyfikatorów. Dla klastra nawigacyjnego zwraca gołe scancody dzielone
# z klawiaturą numeryczną, więc te kody trzeba znać z listy.
MAPVK_VK_TO_VSC_EX = 4

# Klawisze wysyłane przez sprzęt z prefiksem E0. Bez tego wejście wstrzyknięte
# (SendInput ze scancode 0) nazywałoby strzałkę w prawo „Num 6" — dzielą scancode 4D.
_EXTENDED_VKS = frozenset((
    0x21, 0x22, 0x23, 0x24,        # PageUp, PageDown, End, Home
    0x25, 0x26, 0x27, 0x28,        # strzałki
    0x2D, 0x2E,                    # Insert, Delete
    0x5B, 0x5C, 0x5D,              # Win L/P, Menu
    0x6F,                          # NumPad /
    0xA3, 0xA5,                    # prawy Ctrl, prawy Alt
))

# Fallback dla klawiszy, których GetKeyNameText nie nazwie (media/consumer często = VK 0).
_VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x10: "Shift", 0x11: "Ctrl",
    0x12: "Alt", 0x13: "Pause", 0x14: "CapsLock", 0x1B: "Esc", 0x20: "Spacja",
    0x21: "PageUp", 0x22: "PageDown", 0x23: "End", 0x24: "Home",
    0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓",
    0x2C: "PrintScreen", 0x2D: "Insert", 0x2E: "Delete",
    0x5B: "Win L", 0x5C: "Win P", 0x5D: "Menu",
    0x90: "NumLock", 0x91: "ScrollLock",
    0xA0: "Lewy Shift", 0xA1: "Prawy Shift", 0xA2: "Lewy Ctrl", 0xA3: "Prawy Ctrl",
    0xA4: "Lewy Alt", 0xA5: "Prawy Alt",
    0xA6: "Przeglądarka Wstecz", 0xA7: "Przeglądarka Dalej", 0xA8: "Przeglądarka Odśwież",
    0xA9: "Przeglądarka Stop", 0xAA: "Przeglądarka Szukaj", 0xAB: "Ulubione",
    0xAC: "Przeglądarka Start",
    0xAD: "Wycisz", 0xAE: "Głośność −", 0xAF: "Głośność +",
    0xB0: "Następny utwór", 0xB1: "Poprzedni utwór", 0xB2: "Stop", 0xB3: "Play/Pauza",
    0xB4: "Poczta", 0xB5: "Wybór multimediów", 0xB6: "Aplikacja 1", 0xB7: "Aplikacja 2",
}
# F1-F24 mają VK 0x70-0x87; GetKeyNameTextW nazwie tylko te, które są na klawiaturze.
_VK_NAMES.update({0x70 + i: "F%d" % (i + 1) for i in range(24)})


def key_name(vkey: int, make_code: int, e0: bool, e1: bool = False) -> str:
    """Czytelna, zlokalizowana nazwa klawisza (GetKeyNameTextW) z fallbackiem."""
    # Sekwencja z prefiksem E1 to w praktyce wyłącznie Pause (E1 1D 45).
    # GetKeyNameTextW dla scancode 0x1D zwróciłby „Ctrl".
    if e1:
        return _VK_NAMES.get(vkey, "VK_0x%02X" % vkey)

    # Klawisze multimedialne, przeglądarkowe i modyfikatory (VK >= 0xA0) dzielą
    # scancode z klawiszami znakowymi — rozróżnia je dopiero kod VK. GetKeyNameTextW
    # patrzy wyłącznie na scancode, więc dla „Głośność +" (VK AF, scan 30) zwraca „B".
    if vkey >= 0xA0 and vkey in _VK_NAMES:
        return _VK_NAMES[vkey]

    if user32 is not None:
        # Wejście wstrzyknięte (SendInput) często ma scancode 0 — wtedy wyprowadź
        # go z kodu VK, inaczej każdy taki klawisz nazywałby się „VK_0x7C".
        if make_code == 0 and vkey:
            sc = user32.MapVirtualKeyW(vkey, MAPVK_VK_TO_VSC_EX)
            if sc:
                if (sc >> 8) == 0xE0 or vkey in _EXTENDED_VKS:
                    e0 = True
                make_code = sc & 0xFF
        lparam = (make_code & 0xFF) << 16
        # NumLock ma scancode 0x45 bez E0; bez bitu rozszerzonego Windows nazywa
        # go „Pause". Ustawienie bitu daje poprawną nazwę.
        if e0 or vkey == VK_NUMLOCK:
            lparam |= 1 << 24
        # Bit 25 („nie rozróżniaj lewy/prawy") celowo NIE jest ustawiany —
        # Raw Input daje VK=0x10 zarówno dla lewego, jak i prawego Shiftu,
        # więc scancode jest jedynym sposobem, by je rozróżnić.
        buf = ctypes.create_unicode_buffer(128)
        n = user32.GetKeyNameTextW(wintypes.LONG(lparam), buf, 128)
        if n > 0 and buf.value.strip():
            return buf.value.strip()

    if vkey in _VK_NAMES:
        return _VK_NAMES[vkey]
    # 0-9 oraz A-Z mają VK == kod ASCII znaku.
    if 0x30 <= vkey <= 0x39 or 0x41 <= vkey <= 0x5A:
        return chr(vkey)
    return "VK_0x%02X" % vkey


def decode_keyboard(di: DeviceInfo, kb, ts: str, mono: float, hold: dict, chatter=None):
    """Zwróć Event albo None. `hold` mapuje (uchwyt, vk, scan, e0) -> czas wciśnięcia."""
    # VKey 0xFF to zdarzenie „overrun"/fake — pomiń.
    if kb.VKey == 0xFF:
        return None
    e0 = bool(kb.Flags & RI_KEY_E0)
    e1 = bool(kb.Flags & RI_KEY_E1)
    is_break = bool(kb.Flags & RI_KEY_BREAK)
    name = key_name(kb.VKey, kb.MakeCode, e0, e1)

    meta = "VK %02X sc %02X" % (kb.VKey, kb.MakeCode)
    if e0:
        meta += " E0"
    if e1:
        meta += " E1"

    # Klucz musi zawierać scancode: Enter i NumPad Enter dzielą VK 0x0D i scan 0x1C,
    # różni je wyłącznie bit E0.
    hkey = (di.handle, kb.VKey, kb.MakeCode, e0)
    hold_ms = None
    style = "up" if is_break else "down"
    if is_break:
        t0 = hold.pop(hkey, None)
        if t0 is not None:
            hold_ms = int((mono - t0) * 1000.0)
            meta += " · %d ms" % hold_ms
        if chatter is not None:
            chatter.release(hkey, mono)
    else:
        was_down = hkey in hold
        hold.setdefault(hkey, mono)
        # Autorepeat to powtórzone MAKE bez BREAK — nie jest nowym wciśnięciem.
        if chatter is not None and not was_down:
            gap = chatter.press(hkey, mono, name)
            if gap is not None:
                meta += " · DRGANIE %.1f ms" % gap
                style = "warn"

    arrow = "↑" if is_break else "↓"
    return Event(ts, mono, di, "KLAW", "%s %s" % (name, arrow), meta,
                 key=(di.handle, "K", kb.VKey, kb.MakeCode, e0, is_break),
                 style=style, kname=name, is_break=is_break, hold_ms=hold_ms,
                 ev_type=RIM_TYPEKEYBOARD)


# --------------------------------------------------------------------------- #
#  Dekodowanie myszy.
# --------------------------------------------------------------------------- #
def decode_mouse(di: DeviceInfo, ms, ts: str, mono: float, verbose: bool, stats=None,
                 chatter=None):
    flags = ms.btn.usButtonFlags
    absolute = bool(ms.usFlags & MOUSE_MOVE_ABSOLUTE)

    # Dystans zbieramy ZAWSZE, nie tylko w --verbose: statystyka sesji nie może
    # zależeć od trybu wyświetlania. Liczy się też, gdy ten sam pakiet niesie
    # przycisk — wcześniej przesunięcie w takim pakiecie przepadało.
    if stats is not None and not absolute and (ms.lLastX or ms.lLastY):
        stats.motion(di, ms.lLastX, ms.lLastY)

    if flags:
        events = []
        style = "mouse"
        extra_meta = []
        for bit, label, is_down in MOUSE_BUTTON_EVENTS:
            if not (flags & bit):
                continue
            events.append(label)
            if stats is not None:
                stats.mouse_button(di, label)
            if chatter is not None:
                ctl = (di.handle, label.split(" ")[0])
                if is_down:
                    gap = chatter.press(ctl, mono, label.split(" ")[0])
                    if gap is not None:
                        extra_meta.append("DRGANIE %.1f ms" % gap)
                        style = "warn"
                else:
                    chatter.release(ctl, mono)
        if flags & RI_MOUSE_WHEEL:
            delta = ms.btn.usButtonData
            events.append("kółko %+d" % delta)
            style = "wheel"
            if stats is not None:
                stats.wheel(di, delta)
        if flags & RI_MOUSE_HWHEEL:
            delta = ms.btn.usButtonData
            events.append("kółko poziome %+d" % delta)
            style = "wheel"
            if stats is not None:
                stats.wheel(di, delta)
        if events:
            body = ", ".join(events)
            return Event(ts, mono, di, "MYSZ", body, " · ".join(extra_meta),
                         key=(di.handle, "M", body), style=style,
                         ev_type=RIM_TYPEMOUSE)

    if not verbose:
        return None

    if absolute:
        # Współrzędne 0..65535 (ekran dotykowy, tablet, RDP, maszyna wirtualna) —
        # to POZYCJA, nie przesunięcie. Traktowanie ich jako dx/dy dawało
        # „ruch dx=32768" przy nieruchomym urządzeniu.
        space = "pulpit wirt." if (ms.usFlags & MOUSE_VIRTUAL_DESKTOP) else "ekran"
        return Event(ts, mono, di, "MYSZ",
                     "pozycja x=%d y=%d" % (ms.lLastX, ms.lLastY), space,
                     key=(di.handle, "MA"), style="mouse", merge_mode="latest",
                     ev_type=RIM_TYPEMOUSE)

    if ms.lLastX or ms.lLastY:
        return Event(ts, mono, di, "MYSZ",
                     "ruch dx=%+d dy=%+d" % (ms.lLastX, ms.lLastY), "",
                     key=(di.handle, "MV"), style="mouse",
                     dx=ms.lLastX, dy=ms.lLastY, merge_mode="sum",
                     ev_type=RIM_TYPEMOUSE)
    return None


# --------------------------------------------------------------------------- #
#  Dekodowanie HID.
#
#  Raport HID z Raw Input ZAWSZE zaczyna się bajtem Report ID (0 gdy urządzenie
#  ich nie używa), więc report[0] jest bezpiecznym kluczem podziału stanu.
# --------------------------------------------------------------------------- #
def diff_report(state: dict, handle: int, report: bytes, verbose: bool):
    """Porównaj raport z poprzednim raportem o TYM SAMYM Report ID.

    Zwraca (tekst, styl) albo None gdy nie ma nic do pokazania.
    Czysta funkcja — testowalna bez urządzenia.
    """
    if not report:
        return None
    rid = report[0]
    skey = (handle, rid)
    prev = state.get(skey)
    state[skey] = report

    prefix = "" if rid == 0 else "rid=%02X " % rid

    if prev is None or len(prev) != len(report):
        # Pierwszy raport tego Report ID albo zmiana długości: nie ma sensownego
        # odniesienia, więc pokaż całość. Poprzednio ten przypadek nie trafiał
        # w ŻADNĄ gałąź i urządzenie milkło na resztę sesji.
        return prefix + report.hex(" ").upper(), "hid"

    if prev == report:
        # Bezczynny pad/touchpad raportuje ze stałą częstotliwością — najczęstszy
        # przypadek, więc wychodzimy przed kosztowną pętlą porównania bajtów.
        if not verbose:
            return None
        return prefix + report.hex(" ").upper(), "hid"

    changed = [j for j in range(len(report)) if prev[j] != report[j]]
    if verbose:
        marker = "  (zmiana: %s)" % ",".join(str(j) for j in changed)
        return prefix + report.hex(" ").upper() + marker, "hidchg"
    diff = " ".join("[%d] %02X→%02X" % (j, prev[j], report[j]) for j in changed)
    return prefix + diff, "hidchg"


# --------------------------------------------------------------------------- #
#  Nazwy HID Usage — bez nich HidP_* awansuje wyjście tylko z „bajt 3" na „usage 0xE9".
#  Podzbiór kuratorowany: to, co realnie wysyłają pady, piloty i tablety.
# --------------------------------------------------------------------------- #
_USAGE_NAMES = {
    # Generic Desktop (0x01) — osie i sterowanie
    (0x01, 0x30): "X", (0x01, 0x31): "Y", (0x01, 0x32): "Z",
    (0x01, 0x33): "Rx", (0x01, 0x34): "Ry", (0x01, 0x35): "Rz",
    (0x01, 0x36): "Suwak", (0x01, 0x37): "Pokrętło", (0x01, 0x38): "Kółko",
    (0x01, 0x39): "Hat (krzyżak)", (0x01, 0x3D): "Start", (0x01, 0x3E): "Select",
    (0x01, 0x40): "Vx", (0x01, 0x41): "Vy", (0x01, 0x42): "Vz",
    (0x01, 0x43): "Vbrx", (0x01, 0x44): "Vbry", (0x01, 0x45): "Vbrz", (0x01, 0x46): "Vno",
    (0x01, 0x81): "Zasilanie", (0x01, 0x82): "Uśpienie", (0x01, 0x83): "Wybudzenie",
    (0x01, 0x90): "D-pad ↑", (0x01, 0x91): "D-pad ↓",
    (0x01, 0x92): "D-pad →", (0x01, 0x93): "D-pad ←",
    # Simulation Controls (0x02) — pedały i drążki
    (0x02, 0xBA): "Ster", (0x02, 0xBB): "Przepustnica",
    (0x02, 0xC4): "Gaz", (0x02, 0xC5): "Hamulec", (0x02, 0xC6): "Sprzęgło",
    (0x02, 0xC8): "Kierownica",
    # Game Controls (0x05)
    (0x05, 0x20): "Punkt widzenia", (0x05, 0x21): "Ruch gracza",
    # Keyboard/Keypad (0x07) — modyfikatory i klawisze sterujące
    (0x07, 0x28): "Enter", (0x07, 0x29): "Esc", (0x07, 0x2A): "Backspace",
    (0x07, 0x2B): "Tab", (0x07, 0x2C): "Spacja",
    (0x07, 0x39): "CapsLock", (0x07, 0x47): "ScrollLock", (0x07, 0x48): "Pause",
    (0x07, 0x49): "Insert", (0x07, 0x4A): "Home", (0x07, 0x4B): "PageUp",
    (0x07, 0x4C): "Delete", (0x07, 0x4D): "End", (0x07, 0x4E): "PageDown",
    (0x07, 0x4F): "→", (0x07, 0x50): "←", (0x07, 0x51): "↓", (0x07, 0x52): "↑",
    (0x07, 0x53): "NumLock",
    (0x07, 0xE0): "Lewy Ctrl", (0x07, 0xE1): "Lewy Shift", (0x07, 0xE2): "Lewy Alt",
    (0x07, 0xE3): "Lewy Win", (0x07, 0xE4): "Prawy Ctrl", (0x07, 0xE5): "Prawy Shift",
    (0x07, 0xE6): "Prawy Alt", (0x07, 0xE7): "Prawy Win",
    # Consumer (0x0C) — piloty i klawisze multimedialne
    (0x0C, 0x30): "Zasilanie", (0x0C, 0x40): "Menu", (0x0C, 0x41): "Menu wybór",
    (0x0C, 0x42): "Menu ↑", (0x0C, 0x43): "Menu ↓", (0x0C, 0x44): "Menu ←",
    (0x0C, 0x45): "Menu →", (0x0C, 0x46): "Menu Esc",
    (0x0C, 0xB0): "Play", (0x0C, 0xB1): "Pauza", (0x0C, 0xB2): "Nagrywanie",
    (0x0C, 0xB3): "Przewiń do przodu", (0x0C, 0xB4): "Przewiń do tyłu",
    (0x0C, 0xB5): "Następny utwór", (0x0C, 0xB6): "Poprzedni utwór",
    (0x0C, 0xB7): "Stop", (0x0C, 0xB8): "Wysuń",
    (0x0C, 0xCD): "Play/Pauza", (0x0C, 0xE2): "Wycisz",
    (0x0C, 0xE9): "Głośność +", (0x0C, 0xEA): "Głośność −",
    (0x0C, 0x183): "Wybór multimediów", (0x0C, 0x18A): "Poczta",
    (0x0C, 0x192): "Kalkulator", (0x0C, 0x194): "Mój komputer",
    (0x0C, 0x221): "Szukaj", (0x0C, 0x223): "Strona domowa",
    (0x0C, 0x224): "Wstecz", (0x0C, 0x225): "Dalej", (0x0C, 0x226): "Stop",
    (0x0C, 0x227): "Odśwież", (0x0C, 0x22A): "Ulubione",
    # Digitizers (0x0D)
    (0x0D, 0x30): "Nacisk", (0x0D, 0x31): "Nachylenie X", (0x0D, 0x32): "Nachylenie Y",
    (0x0D, 0x42): "Dotyk rysika", (0x0D, 0x44): "Przycisk boczny",
    (0x0D, 0x45): "Przycisk gumki", (0x0D, 0x47): "Potwierdzony dotyk",
    (0x0D, 0x51): "ID kontaktu", (0x0D, 0x56): "Znacznik czasu",
}

_USAGE_PAGE_NAMES = {
    0x01: "Generic Desktop", 0x02: "Simulation", 0x03: "VR", 0x04: "Sport",
    0x05: "Game", 0x06: "Generic Device", 0x07: "Keyboard", 0x08: "LED",
    0x09: "Button", 0x0A: "Ordinal", 0x0B: "Telephony", 0x0C: "Consumer",
    0x0D: "Digitizer", 0x0F: "PID", 0x14: "Alphanumeric Display",
}


def usage_name(page: int, usage: int) -> str:
    """Czytelna nazwa dla pary (UsagePage, Usage)."""
    if page == 0x09:                      # Button page: usage to numer przycisku
        return "przycisk %d" % usage
    name = _USAGE_NAMES.get((page, usage))
    if name:
        return name
    if page == 0x07:                      # klawiatura: litery i cyfry są ciągłe
        if 0x04 <= usage <= 0x1D:
            return chr(ord("A") + usage - 0x04)
        if 0x1E <= usage <= 0x26:
            return chr(ord("1") + usage - 0x1E)
        if usage == 0x27:
            return "0"
        if 0x3A <= usage <= 0x45:
            return "F%d" % (usage - 0x39)
        if 0x54 <= usage <= 0x63:
            # Blok numeryczny — używany przez klawiatury raportujące przez kolekcję
            # HID zamiast RIM_TYPEKEYBOARD.
            return "Num " + ("/", "*", "−", "+", "Enter", "1", "2", "3", "4", "5",
                             "6", "7", "8", "9", "0", ",")[usage - 0x54]
        if 0x68 <= usage <= 0x73:
            # F13-F24: klawisze makro (m.in. Corsair K95) i sterowniki przemysłowe.
            return "F%d" % (usage - 0x68 + 13)
    if page >= 0xFF00:
        return "vendor %04X:%04X" % (page, usage)
    return "%s %04X" % (_USAGE_PAGE_NAMES.get(page, "UP %04X" % page), usage)


def axis_bar(value: int, lo: int, hi: int, width: int = 17) -> str:
    """Pozioma skala osi analogowej. Czysta funkcja — pozycja znacznika liniowo
    odwzorowuje wartość na zakres logiczny, środek zaznaczony pionową kreską."""
    if width < 3:
        width = 3
    span = hi - lo
    if span <= 0:
        return "▕" + "─" * width + "▏"
    pos = int(round((value - lo) * (width - 1) / float(span)))
    pos = max(0, min(width - 1, pos))
    mid = (width - 1) // 2
    cells = ["─"] * width
    cells[mid] = "┼"
    cells[pos] = "█"
    return "▕" + "".join(cells) + "▏"


class AnalogTracker:
    """Zakres, strefa spoczynku i dryf osi analogowych.

    Nie zakłada, gdzie oś „powinna" spoczywać: drążek centruje się w środku zakresu,
    a spust w jego dolnym końcu. Zamiast zgadywać, mierzymy pas wartości obserwowany
    wtedy, gdy oś stoi nieruchomo — jego szerokość to martwa strefa, której
    urządzenie realnie potrzebuje.
    """

    QUIESCENT_DIV = 256        # zmiana < zakres/256 uznawana za bezruch
    QUIESCENT_N = 5            # tyle kolejnych spokojnych próbek zaczyna pas spoczynku
    BUCKETS = 64               # rozdzielczość histogramu położeń spoczynkowych

    def __init__(self):
        self.axes = {}         # (klucz grupy, page, usage) -> pomiary

    def feed(self, di: "DeviceInfo", state: dict, meta: dict) -> None:
        gkey = di.group.key if di.group is not None else di.group_key()
        for k, v in state.items():
            if k[0] != "val":
                continue
            lo, hi = meta.get(k, (0, 0))
            key = (gkey, k[1], k[2])
            a = self.axes.get(key)
            if a is None:
                a = {"lo": lo, "hi": hi, "min": v, "max": v, "cur": v, "n": 0,
                     "rest": {}, "quiet": 0, "still": 0, "prev": v,
                     "tag": di.group.tag_text() if di.group else "-",
                     "name": di.group.name if di.group else di.name()}
                self.axes[key] = a
            a["n"] += 1
            a["cur"] = v
            if v < a["min"]:
                a["min"] = v
            if v > a["max"]:
                a["max"] = v
            span = (a["hi"] - a["lo"]) or 1
            if abs(v - a["prev"]) * self.QUIESCENT_DIV <= span:
                a["still"] += 1
                if a["still"] >= self.QUIESCENT_N:
                    # Histogram położeń spoczynkowych zamiast jednego pasa min..max:
                    # spust bywa nieruchomy zarówno puszczony, jak i wciśnięty do oporu,
                    # a rozciągnięty na oba pas obejmowałby cały zakres i nic nie znaczył.
                    b = int((v - a["lo"]) * self.BUCKETS / span)
                    b = max(0, min(self.BUCKETS - 1, b))
                    slot = a["rest"].get(b)
                    if slot is None:
                        a["rest"][b] = [1, v, v]
                    else:
                        slot[0] += 1
                        slot[1] = min(slot[1], v)
                        slot[2] = max(slot[2], v)
                    a["quiet"] += 1
            else:
                a["still"] = 0
            a["prev"] = v

    def rest_band(self, key):
        """Dominujące położenie spoczynkowe jako (min, max, udział próbek) albo None."""
        a = self.axes.get(key)
        if not a or not a["rest"]:
            return None
        count, mn, mx = max(a["rest"].values(), key=lambda s: s[0])
        share = count * 100.0 / max(1, a["quiet"])
        return mn, mx, share

    def report(self):
        """Lista wierszy podsumowania: (tag, nazwa osi, zasięg, spoczynek)."""
        rows = []
        for key in sorted(self.axes):
            a = self.axes[key]
            page, usage = key[1], key[2]
            span = (a["hi"] - a["lo"]) or 1
            used = (a["max"] - a["min"]) * 100.0 / span
            reach = "zasięg %d..%d z %d..%d (%.0f%%)" % (
                a["min"], a["max"], a["lo"], a["hi"], used)
            band = self.rest_band(key)
            if band is None:
                rest = "spoczynek: brak pomiaru"
            else:
                mn, mx, share = band
                rest = ("spoczynek %d..%d (martwa strefa %.1f%% zakresu, "
                        "%.0f%% próbek w bezruchu)" % (mn, mx, (mx - mn) * 100.0 / span,
                                                       share))
            rows.append((a["tag"], usage_name(page, usage), reach, rest))
        return rows


def describe_hid_changes(prev: dict, cur: dict, value_meta: dict, verbose: bool,
                         bars: bool = False, shown: dict = None):
    """Opisz różnicę między dwoma odczytami raportu HID. Czysta funkcja.

    `prev`/`cur` mapują ("btn", page, usage) -> True oraz ("val", page, usage) -> int.
    `value_meta` mapuje ("val", page, usage) -> (logical_min, logical_max).
    Osie filtrujemy progiem, bo analogi szumią o kilka jednostek w spoczynku i bez
    tego każdy pad generowałby ciągły strumień linii.
    """
    parts = []
    for k in cur:
        if k[0] != "btn":
            continue
        if k not in prev:
            parts.append(usage_name(k[1], k[2]) + " ↓")
    for k in prev:
        if k[0] != "btn":
            continue
        if k not in cur:
            parts.append(usage_name(k[1], k[2]) + " ↑")

    for k, v in cur.items():
        if k[0] != "val":
            continue
        old = prev.get(k)
        if old == v:
            continue
        lo, hi = value_meta.get(k, (0, 0))
        if old is not None and not verbose:
            span = hi - lo
            # Próg liczymy od ostatnio POKAZANEJ wartości, nie od poprzedniej próbki.
            # Inaczej próg nigdy się nie kumuluje i oś przesuwana wolniej niż
            # span/64 na raport jest niewidoczna niezależnie od przebytej drogi —
            # czyli dokładnie odwrotnie do intencji filtra.
            # setdefault, nie get: przy pierwszym odczycie osi zapamiętujemy jej
            # wartość jako punkt odniesienia. Bez tego baza cofałaby się do
            # poprzedniej próbki po każdym odrzuceniu i próg nigdy by nie urósł.
            base = old if shown is None else shown.setdefault(k, old)
            if span > 0 and abs(v - base) * 64 < span:
                continue          # szum spoczynkowy analoga
        if shown is not None:
            shown[k] = v
        if bars and hi > lo:
            parts.append("%s %s %d" % (usage_name(k[1], k[2]), axis_bar(v, lo, hi), v))
        else:
            parts.append("%s=%d" % (usage_name(k[1], k[2]), v))

    # Oś, która zniknęła ze stanu, to wartość spoza zakresu logicznego przy
    # HasNull — czyli krzyżak wrócił do środka. Bez tego puszczenie przepadłoby.
    for k in prev:
        if k[0] == "val" and k not in cur:
            parts.append("%s: środek" % usage_name(k[1], k[2]))
            if shown is not None:
                shown.pop(k, None)
    return parts


class HidDecoder:
    """Tłumaczy surowy raport HID na nazwane przyciski i osie (hid.dll / HidP_*).

    Tworzony leniwie z preparsed data, które Raw Input udostępnia bez CreateFileW
    i bez uprawnień administratora. Gdy urządzenie nie ma preparsed data albo
    HidP_* zawiedzie, wywołujący wraca do surowego diffu bajtów.
    """

    MAX_RANGE_USAGES = 64      # ile osi rozwinąć z jednego wpisu caps z zakresem

    def __init__(self, preparsed):
        self._pp = preparsed
        self.truncated_ranges = 0
        self.caps = HIDP_CAPS()
        if hid.HidP_GetCaps(preparsed, ctypes.byref(self.caps)) != HIDP_STATUS_SUCCESS:
            raise ValueError("HidP_GetCaps nie powiodło się")

        self.button_pages = []
        n = wintypes.USHORT(self.caps.NumberInputButtonCaps)
        if n.value:
            arr = (HIDP_BUTTON_CAPS * n.value)()
            if hid.HidP_GetButtonCaps(HIDP_INPUT, arr, ctypes.byref(n),
                                      preparsed) == HIDP_STATUS_SUCCESS:
                for i in range(n.value):
                    page = arr[i].UsagePage
                    if page not in self.button_pages:
                        self.button_pages.append(page)

        self.values = []          # (page, usage, logical_min, logical_max, bit_size)
        self.value_meta = {}
        n = wintypes.USHORT(self.caps.NumberInputValueCaps)
        if n.value:
            arr = (HIDP_VALUE_CAPS * n.value)()
            if hid.HidP_GetValueCaps(HIDP_INPUT, arr, ctypes.byref(n),
                                     preparsed) == HIDP_STATUS_SUCCESS:
                for i in range(n.value):
                    c = arr[i]
                    if c.IsRange:
                        # Każda oś to osobne wywołanie HidP_GetUsageValue na KAŻDY
                        # raport. Wpis vendor o zakresie 0x0000..0x0FFF dałby 4096
                        # wywołań na gorącej ścieżce WM_INPUT, więc zakres ucinamy.
                        lo_u, hi_u = c.Range.UsageMin, c.Range.UsageMax
                        usages = range(lo_u, min(hi_u, lo_u + self.MAX_RANGE_USAGES - 1) + 1)
                        if hi_u - lo_u + 1 > self.MAX_RANGE_USAGES:
                            self.truncated_ranges += 1
                    else:
                        usages = [c.NotRange.Usage]
                    for u in usages:
                        self.values.append((c.UsagePage, u, c.LogicalMin,
                                            c.LogicalMax, c.BitSize, bool(c.HasNull)))
                        self.value_meta[("val", c.UsagePage, u)] = (c.LogicalMin, c.LogicalMax)

        # Bufory alokowane raz — dekodowanie siedzi na gorącej ścieżce WM_INPUT.
        self._usage_bufs = {}
        for page in self.button_pages:
            n_max = hid.HidP_MaxUsageListLength(HIDP_INPUT, page, preparsed)
            if n_max:
                self._usage_bufs[page] = ((USAGE * n_max)(), n_max)
        self._report_buf = ctypes.create_string_buffer(
            max(1, self.caps.InputReportByteLength))
        self._value_out = wintypes.ULONG(0)
        self._prev = {}
        # Ostatnio WYPISANE wartości osi (rid -> {klucz: wartość}). Osobno od _prev,
        # bo próg szumu musi się kumulować względem tego, co użytkownik widzi.
        self._shown = {}
        self.last_state = {}      # ostatni odczyt — używany przez AnalogTracker

    @property
    def usable(self) -> bool:
        return bool(self._usage_bufs or self.values)

    def read_state(self, report: bytes) -> dict:
        """Odczytaj stan przycisków i osi z jednego raportu."""
        state = {}
        # Bufor alokowany raz — dekodowanie siedzi na gorącej ścieżce WM_INPUT.
        if len(report) > len(self._report_buf):
            self._report_buf = ctypes.create_string_buffer(len(report))
        ctypes.memset(self._report_buf, 0, len(self._report_buf))
        ctypes.memmove(self._report_buf, report, len(report))
        buf = self._report_buf
        length = wintypes.ULONG(len(report))

        for page, (arr, n_max) in self._usage_bufs.items():
            count = wintypes.ULONG(n_max)
            st = hid.HidP_GetUsages(HIDP_INPUT, page, 0, arr, ctypes.byref(count),
                                    self._pp, buf, length)
            if st != HIDP_STATUS_SUCCESS:
                # INCOMPATIBLE_REPORT_ID = ta strona nie należy do tego raportu.
                continue
            for i in range(count.value):
                state[("btn", page, arr[i])] = True

        for page, usage, lo, hi, bits, has_null in self.values:
            st = hid.HidP_GetUsageValue(HIDP_INPUT, page, 0, usage,
                                        ctypes.byref(self._value_out), self._pp, buf, length)
            if st != HIDP_STATUS_SUCCESS:
                continue
            v = self._value_out.value
            # HidP_GetUsageValue zwraca surowe bity bez znaku; osie ze znakiem
            # trzeba rozszerzyć ręcznie według BitSize.
            if lo < 0 and bits and bits <= 32 and v >= (1 << (bits - 1)):
                v -= (1 << bits)
            if has_null and not (lo <= v <= hi):
                # Krzyżak (hat switch) koduje „nic nie wciśnięte" wartością POZA
                # zakresem logicznym. Zapisany jako zwykła pozycja wyglądałby jak
                # skrajne wychylenie i fałszował histogram spoczynku.
                continue
            state[("val", page, usage)] = v
        return state

    def decode(self, report: bytes, verbose: bool, bars: bool = False):
        """Zwróć listę opisów zmian albo None, gdy raport nic nie wnosi."""
        rid = report[0] if report else 0
        try:
            cur = self.read_state(report)
        except Exception:
            # Bez tego AnalogTracker policzyłby stan z POPRZEDNIEGO raportu jeszcze
            # raz i uznał oś za nieruchomą mimo realnego ruchu.
            self.last_state = {}
            return None
        self.last_state = cur
        prev = self._prev.get(rid)
        self._prev[rid] = cur
        if prev is None:
            # Pierwszy raport: pokaż stan wciśnięty, ale nie zalewaj osiami w spoczynku.
            parts = [usage_name(k[1], k[2]) + " ↓" for k in cur if k[0] == "btn"]
            return parts or None
        shown = self._shown.setdefault(rid, {})
        parts = describe_hid_changes(prev, cur, self.value_meta, verbose, bars, shown)
        return parts or None


def _get_preparsed_data(handle):
    """Preparsed data z Raw Input — dostępne bez CreateFileW i bez uprawnień."""
    size = wintypes.UINT(0)
    if user32.GetRawInputDeviceInfoW(handle, RIDI_PREPARSEDDATA, None,
                                     ctypes.byref(size)) == UINT_ERR:
        return None
    if size.value == 0:
        return None
    buf = (ctypes.c_byte * size.value)()
    if user32.GetRawInputDeviceInfoW(handle, RIDI_PREPARSEDDATA, buf,
                                     ctypes.byref(size)) == UINT_ERR:
        return None
    return buf


def build_hid_decoder(di: DeviceInfo):
    """Zbuduj dekoder dla urządzenia albo zwróć None (wtedy zostaje surowy diff)."""
    if hid is None:
        return None
    try:
        pp = _get_preparsed_data(di.handle)
        if pp is None:
            return None
        dec = HidDecoder(pp)
        return dec if dec.usable else None
    except Exception:
        return None


def decode_hid(di: DeviceInfo, ri, buf_len: int, ts: str, mono: float,
               state: dict, verbose: bool, analog=None):
    """Zwróć listę zdarzeń (jeden raport HID może nieść kilka podraportów)."""
    size_hid = ri.data.hid.dwSizeHid
    count = ri.data.hid.dwCount
    if size_hid == 0 or count == 0:
        return []
    total = size_hid * count
    if HID_DATA_OFFSET + total > buf_len:
        # Nigdy nie powinno wystąpić; lepiej przyciąć niż czytać poza buforem.
        count = max(0, (buf_len - HID_DATA_OFFSET) // size_hid)
        total = size_hid * count
        if count == 0:
            return []
    raw = ctypes.string_at(ctypes.addressof(ri.data.hid.bRawData), total)

    events = []
    for i in range(count):
        report = raw[i * size_hid:(i + 1) * size_hid]
        rid = report[0] if report else 0
        prefix = "" if rid == 0 else "rid=%02X " % rid

        # Ścieżka preferowana: nazwane przyciski i osie. Surowy diff bajtów zostaje
        # dla urządzeń bez preparsed data albo gdy HidP_* nic nie rozpozna.
        if di.decoder is not None:
            parts = di.decoder.decode(report, verbose, bars=analog is not None)
            if analog is not None:
                # Karmimy tracker KAŻDYM raportem, nie tylko tymi, które coś wypisują:
                # pas spoczynku powstaje właśnie z próbek, gdy nic się nie dzieje.
                analog.feed(di, di.decoder.last_state, di.decoder.value_meta)
            if parts:
                body = prefix + ", ".join(parts)
                events.append(Event(ts, mono, di, "HID", body, "%d B" % size_hid,
                                    key=(di.handle, "H", body), style="hidchg",
                                    ev_type=RIM_TYPEHID))
                # Stan surowy trzymamy aktualny, żeby przełączenie na --raw-hid
                # w kolejnej sesji nie startowało od fałszywej różnicy.
                state[(di.handle, rid)] = report
                continue
            if di.decoder.usable:
                state[(di.handle, rid)] = report
                continue

        res = diff_report(state, di.handle, report, verbose)
        if res is None:
            continue
        body, style = res
        events.append(Event(ts, mono, di, "HID", body, "%d B" % size_hid,
                            key=(di.handle, "H", body), style=style,
                            ev_type=RIM_TYPEHID))
    return events


# --------------------------------------------------------------------------- #
#  Pomiar częstotliwości raportowania i wykrywanie drgania styków.
# --------------------------------------------------------------------------- #
class RateMeter:
    """Częstotliwość raportowania urządzenia (Hz) w oknie o stałej długości.

    Liczona jest wyłącznie ŚREDNIA z okna (n/Δt). Mediana i percentyle odstępów
    mierzyłyby ścieżkę dostarczania zdarzenia do procesu, a nie sprzęt — czas
    jest stemplowany na wejściu do obsługi WM_INPUT, nie w urządzeniu.
    """

    WINDOW = 1.0

    def __init__(self):
        self._count = {}          # uchwyt -> liczba raportów w bieżącym oknie
        self._start = {}          # uchwyt -> początek okna
        self.peak = {}            # uchwyt -> maksymalne zaobserwowane Hz
        self.last = {}            # uchwyt -> ostatnie Hz

    def add(self, handle: int, mono: float) -> None:
        self._count[handle] = self._count.get(handle, 0) + 1
        self._start.setdefault(handle, mono)

    def due(self, handle: int, mono: float) -> bool:
        t0 = self._start.get(handle)
        return t0 is not None and (mono - t0) >= self.WINDOW

    MIN_PARTIAL = 0.25         # krótszego ogona nie warto raportować

    def take(self, handle: int, mono: float, partial: bool = False):
        """Zamknij okno i zwróć (Hz, liczba raportów, długość okna) albo None.

        `partial=True` oznacza domknięcie przy zatrzymaniu sesji. Takie okno bywa
        wielokrotnie dłuższe od pełnego (użytkownik nacisnął parę klawiszy i czekał
        minutę do Ctrl+C), więc jego Hz nie ma nic wspólnego z częstotliwością
        urządzenia — wliczony z pełną wagą pokazywał „10 Hz" dla myszy 1000 Hz.
        """
        t0 = self._start.get(handle)
        if t0 is None:
            return None
        span = mono - t0
        n = self._count.get(handle, 0)
        self._count[handle] = 0
        self._start[handle] = mono
        if span <= 0 or n == 0:
            return None
        if partial and (span < self.MIN_PARTIAL or span > self.WINDOW * 1.5 or n < 2):
            return None
        hz = n / span
        self.last[handle] = hz
        # Szczyt tylko z okien pełnych — ogon zaniżałby go po każdej sesji.
        if not partial and hz > self.peak.get(handle, 0.0):
            self.peak[handle] = hz
        return hz, n, span

    def forget(self, handle: int) -> None:
        """Zapomnij urządzenie odłączone w trakcie sesji."""
        for d in (self._count, self._start, self.peak, self.last):
            d.pop(handle, None)

    def pending(self):
        return list(self._start.keys())


class ChatterDetector:
    """Wykrywa drganie styków: ponowne wciśnięcie zbyt szybko po zwolnieniu.

    Zużyty mikroswitch myszy „odbija" — jedno fizyczne kliknięcie daje kilka par
    naciśnięcie/zwolnienie w odstępie kilku milisekund. Mierzymy czas dostarczenia
    zdarzenia, nie czas w sprzęcie, więc wynik jest przybliżeniem od góry.
    """

    def __init__(self, threshold_ms: float = 30.0):
        self.threshold = threshold_ms / 1000.0
        self._released = {}
        self.hits = []            # (opis kontrolki, odstęp w ms)

    def press(self, control, mono: float, label: str = ""):
        t0 = self._released.get(control)
        if t0 is None:
            return None
        gap = mono - t0
        if 0 <= gap < self.threshold:
            ms = gap * 1000.0
            self.hits.append((label or str(control), ms))
            return ms
        return None

    def release(self, control, mono: float) -> None:
        self._released[control] = mono


# --------------------------------------------------------------------------- #
#  Filtry urządzeń.
# --------------------------------------------------------------------------- #
TYPE_ALIASES = {
    "keyboard": RIM_TYPEKEYBOARD, "klawiatura": RIM_TYPEKEYBOARD, "kb": RIM_TYPEKEYBOARD,
    "mouse": RIM_TYPEMOUSE, "mysz": RIM_TYPEMOUSE,
    "hid": RIM_TYPEHID,
}


class Filters:
    """--only / --device / --exclude. Dopasowanie po #N, VID:PID albo fragmencie nazwy."""

    def __init__(self, only=None, include=None, exclude=None):
        self.types = set()
        for name in (only or []):
            t = TYPE_ALIASES.get(name.strip().lower())
            if t is not None:
                self.types.add(t)
        self.include = [s.strip().lower() for s in (include or []) if s.strip()]
        self.exclude = [s.strip().lower() for s in (exclude or []) if s.strip()]

    @property
    def active(self) -> bool:
        return bool(self.types or self.include or self.exclude)

    @staticmethod
    def _matches(pattern: str, di: DeviceInfo) -> bool:
        if pattern.startswith("#"):
            return di.group is not None and di.group.tag_text().lower() == pattern
        if ":" in pattern and len(pattern) == 9:
            return di.group_key().lower() == pattern
        hay = " ".join(filter(None, (di.product, di.manufacturer, di.path,
                                     di.group.name if di.group else ""))).lower()
        return pattern in hay

    def accept(self, di: DeviceInfo, ev_type=None) -> bool:
        # Typ ZDARZENIA ma pierwszeństwo przed typem urządzenia: wejście wstrzyknięte
        # (uchwyt 0) niesie i klawiaturę, i mysz, więc `--only mouse` nie może go
        # odrzucać tylko dlatego, że pierwsze zdarzenie było klawiaturowe.
        t = ev_type if ev_type is not None else di.type
        if self.types and t not in self.types:
            return False
        for p in self.exclude:
            if self._matches(p, di):
                return False
        if self.include:
            return any(self._matches(p, di) for p in self.include)
        return True


# --------------------------------------------------------------------------- #
#  Statystyki sesji.
# --------------------------------------------------------------------------- #
class Stats:
    def __init__(self):
        self.start = time.time()
        self.total = 0
        self.per_device = {}       # group_key -> {"name":.., "tag":.., "kind":Counter}
        self.keys = {}             # nazwa klawisza -> liczba wciśnięć
        self.hold_ms = []          # czasy trzymania
        self.buttons = {}          # etykieta -> liczba
        self.wheel_delta = 0
        self.distance = 0.0
        self.dropped_hint = 0
        self.rates = {}            # klucz grupy -> (nazwa, tag, [Hz, ...])
        self.chatter_hits = []
        self.analog_rows = []

    def _bucket(self, di: DeviceInfo):
        g = di.group
        key = g.key if g is not None else di.group_key()
        b = self.per_device.get(key)
        if b is None:
            b = {"name": g.name if g else di.name(), "tag": g.tag_text() if g else "-",
                 "kind": {}, "n": 0}
            self.per_device[key] = b
        return b

    def event(self, ev: Event) -> None:
        self.total += 1
        if ev.dev is None:
            return
        b = self._bucket(ev.dev)
        b["n"] += 1
        b["kind"][ev.kind] = b["kind"].get(ev.kind, 0) + 1

    def key_press(self, name: str) -> None:
        self.keys[name] = self.keys.get(name, 0) + 1

    def key_hold(self, ms: int) -> None:
        self.hold_ms.append(ms)

    def mouse_button(self, di, label: str) -> None:
        self.buttons[label] = self.buttons.get(label, 0) + 1

    def wheel(self, di, delta: int) -> None:
        # Kółka wysokiej rozdzielczości (Logitech MagSpeed i inne) raportują
        # wielokrotnie mniejsze delty niż WHEEL_DELTA=120, więc sumujemy surowe
        # delty i dzielimy dopiero przy prezentacji. Delta 0 nie jest kliknięciem.
        self.wheel_delta += abs(delta)

    def motion(self, di, dx: int, dy: int) -> None:
        self.distance += (dx * dx + dy * dy) ** 0.5

    def rate(self, di, hz: float, n: int = 0, span: float = 0.0) -> None:
        """Zbieraj sumy, nie listę Hz.

        Średnia arytmetyczna z okien o różnej długości jest bez sensu: okno 60 s
        z dwoma raportami ważyłoby tyle samo co okno 1 s z tysiącem. Kluczujemy
        po (grupa, uchwyt), bo jedno urządzenie ma kilka kolekcji o zupełnie
        różnych częstotliwościach — mysz 1000 Hz ma obok kolekcję makr 2 Hz.
        """
        b = self._bucket(di)
        gkey = di.group.key if di.group else di.group_key()
        slot = self.rates.get((gkey, di.handle))
        if slot is None:
            slot = {"name": b["name"], "tag": b["tag"], "n": 0, "span": 0.0, "peak": 0.0}
            self.rates[(gkey, di.handle)] = slot
        slot["n"] += n or 1
        slot["span"] += span or (1.0 / hz if hz else 0.0)
        if hz > slot["peak"]:
            slot["peak"] = hz

    def render(self, out: Output, layout: Layout) -> None:
        dur = max(0.001, time.time() - self.start)
        box = Box(out, min(layout.width, 80))
        out.raw("")
        box.top("PODSUMOWANIE SESJI")
        box.row("czas: %s · zdarzeń: %d · średnio %.1f/s"
                % (_fmt_dur(dur), self.total, self.total / dur))
        if self.per_device:
            box.sep()
            for key, b in sorted(self.per_device.items(), key=lambda kv: -kv[1]["n"]):
                kinds = ", ".join("%s %d" % (k, v) for k, v in sorted(b["kind"].items()))
                box.row("%-5s %-24s %5d  (%s)" % (b["tag"], trunc(b["name"], 24), b["n"], kinds))
        extras = []
        if self.keys:
            top = sorted(self.keys.items(), key=lambda kv: -kv[1])[:8]
            extras.append("top klawisze: " + ", ".join("%s×%d" % (k, v) for k, v in top))
        if self.hold_ms:
            avg = sum(self.hold_ms) / len(self.hold_ms)
            extras.append("czas trzymania: śr. %d ms, min %d, max %d"
                          % (avg, min(self.hold_ms), max(self.hold_ms)))
        if self.buttons:
            extras.append("przyciski: " + ", ".join("%s×%d" % (k.replace(" ↓", ""), v)
                                                    for k, v in sorted(self.buttons.items())
                                                    if "↓" in k))
        if self.wheel_delta:
            extras.append("kółko: %.1f kliknięć (suma delt %d)"
                          % (self.wheel_delta / 120.0, self.wheel_delta))
        if self.distance:
            extras.append("dystans myszy: %.0f jedn." % self.distance)
        if extras:
            box.sep()
            for e in extras:
                for chunk in _wrap(e, box.content_w):
                    box.row(chunk)

        if self.rates:
            box.sep("CZĘSTOTLIWOŚĆ RAPORTOWANIA")
            # Jedno urządzenie = kilka kolekcji o różnych częstotliwościach.
            # Pokazujemy najszybszą, bo to ona odpowiada na pytanie „ile Hz ma ta mysz".
            best = {}
            for (gkey, _handle), s in self.rates.items():
                if s["span"] <= 0:
                    continue
                hz = s["n"] / s["span"]
                if hz > best.get(gkey, (0.0,))[0]:
                    best[gkey] = (hz, s)
            for hz, s in sorted(best.values(), key=lambda kv: -kv[0]):
                box.row("%-5s %-24s śr. %.0f Hz · szczyt %.0f Hz · %d raportów"
                        % (s["tag"], trunc(s["name"], 24), hz, s["peak"], s["n"]))
            if len(self.rates) > len(best):
                box.row("(pokazano najszybszą kolekcję każdego urządzenia)", "meta")

        if self.analog_rows:
            box.sep("OSIE ANALOGOWE")
            for tag, name, reach, rest in self.analog_rows:
                box.row("%-5s %s" % (tag, name), "head")
                box.row("      " + reach, "meta")
                box.row("      " + rest, "meta")

        if self.chatter_hits:
            box.sep("DRGANIE STYKÓW")
            per = {}
            for ctl, ms in self.chatter_hits:
                slot = per.setdefault(ctl, [])
                slot.append(ms)
            for ctl, gaps in sorted(per.items(), key=lambda kv: -len(kv[1])):
                box.row("%-16s %d× · najkrótszy odstęp %.1f ms"
                        % (trunc(ctl, 16), len(gaps), min(gaps)), "removal")
            box.row("odstęp poniżej progu = powtórzone wciśnięcie bez fizycznego "
                    "kliknięcia", "meta")

        box.bottom()


def _fmt_dur(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    if h:
        return "%d:%02d:%02d" % (h, m, s)
    return "%d:%02d" % (m, s)


def _wrap(text: str, width: int):
    words = text.split(" ")
    line = ""
    for w in words:
        if line and len(line) + 1 + len(w) > width:
            yield line
            line = w
        else:
            line = (line + " " + w) if line else w
    if line:
        yield line


# --------------------------------------------------------------------------- #
#  Przypięty panel na żywo (--dashboard).
# --------------------------------------------------------------------------- #
class Dashboard:
    """Stały panel u góry ekranu + przewijający się log poniżej.

    Realizowane regionem przewijania terminala (DECSTBM): wiersze 1..N zostają
    nieruchome, a wszystko wypisywane niżej przewija się tylko wewnątrz regionu.

    UWAGA: linie wypchnięte poza region NIE trafiają do bufora przewijania
    terminala — po zakończeniu sesji zobaczysz tylko ostatni ekran. Dlatego przy
    włączonym panelu narzędzie sugeruje równoległy --log.
    """

    MIN_HEIGHT = 12
    REFRESH = 0.25

    def __init__(self, out: "Output", cap, width: int, height: int):
        self.out = out
        self.cap = cap
        self.width = width
        self.height = height
        self.rows = 0
        self._last = 0.0
        self._active = False
        self._seen = 0             # ile wierszy logu policzył Output przy ostatniej synchronizacji
        self._row = 1              # wiersz, w którym stoi kursor (stan, nie wyliczenie)

    def _panel_lines(self):
        """Zbuduj zawartość panelu jako listę list segmentów."""
        groups = self.cap.registry.sorted_groups()
        stats = self.cap.stats
        dur = max(0.001, time.time() - stats.start)
        lines = [[("─" * self.width, "frame")],
                 [("  TESTER URZĄDZEŃ · ", "head"),
                  ("czas %s · zdarzeń %d · %.0f/s" % (_fmt_dur(dur), stats.total,
                                                      stats.total / dur), "meta")]]

        for g in groups[:self.rows_for_devices]:
            key = g.key
            b = stats.per_device.get(key)
            n = b["n"] if b else 0
            hz = ""
            if self.cap.rate is not None:
                # Najszybsza kolekcja, nie pierwsza napotkana: mysz 1000 Hz ma obok
                # kolekcję makr raportującą 2 Hz i to ona wygrywała kolejnością.
                v = max((self.cap.rate.last.get(m, 0.0) for m in g.members), default=0.0)
                if v:
                    hz = " · %.0f Hz" % v
            lines.append([("  %-4s " % g.tag_text(), g.style),
                          (pad(trunc(g.name or "?", 28), 28), g.style),
                          (" %6d zdarz.%s" % (n, hz), "meta")])

        if self.cap.analog is not None:
            for tag, name, a in self._analog_rows():
                lines.append([("  %-4s " % tag, "meta"),
                              (pad(trunc(name, 14), 14), "meta"),
                              (" %s " % axis_bar(a["cur"], a["lo"], a["hi"]), "wheel"),
                              ("%d" % a["cur"], "meta")])

        lines.append([("─" * self.width, "frame")])
        return lines

    def _analog_rows(self):
        rows = []
        for (gkey, page, usage), a in sorted(self.cap.analog.axes.items()):
            rows.append((a["tag"], usage_name(page, usage), a))
        return rows[:self.rows_for_axes]

    @property
    def rows_for_devices(self):
        return 6

    @property
    def rows_for_axes(self):
        return 8

    def start(self) -> bool:
        """Zarezerwuj górę ekranu. Zwraca False, gdy terminal tego nie obsłuży."""
        if not self.out.color or self.height < self.MIN_HEIGHT:
            return False
        self.out.write_raw("\x1b[2J\x1b[H")
        self._active = True
        self._seen = self.out.lines_out
        self._row = 1
        self._set_region(len(self._panel_lines()), initial=True)
        self.update(force=True)
        return True

    def _set_region(self, needed: int, initial: bool = False) -> None:
        """Dopasuj wysokość panelu do treści i przestaw region przewijania.

        DECSTBM przenosi kursor do początku regionu, więc po każdej zmianie trzeba
        go jawnie ustawić — inaczej kolejne linie logu nadpisałyby panel.
        """
        needed = max(3, min(self.height - 4, needed))
        if needed == self.rows:
            return
        self._sync_cursor()
        if needed < self.rows:
            # Wiersze zwolnione przez kurczący się panel wracają do regionu
            # przewijania — bez wyczyszczenia zostałaby w nich stara treść panelu.
            for r in range(needed + 1, self.rows + 1):
                self.out.write_raw("\x1b[%d;1H\x1b[2K" % r)
        self.rows = needed
        if initial:
            self._row = self.rows + 1
        else:
            # Kursor zostaje tam, gdzie faktycznie jest; przy rosnącym panelu
            # zostaje tylko wypchnięty pod jego nową krawędź.
            self._row = min(self.height, max(self.rows + 1, self._row))
        self.out.write_raw("\x1b[%d;%dr" % (self.rows + 1, self.height))
        # DECSTBM przenosi kursor na początek regionu — odtwórz jego pozycję.
        self.out.write_raw("\x1b[%d;1H" % self._row)

    def update(self, force: bool = False) -> None:
        if not self._active:
            return
        now = time.monotonic()
        if not force and (now - self._last) < self.REFRESH:
            return
        self._last = now
        self._sync_cursor()
        lines = self._panel_lines()
        if len(lines) != self.rows:
            self._set_region(len(lines))
        chunks = ["\x1b7"]                       # zapisz pozycję kursora
        for i, segs in enumerate(lines[:self.rows]):
            chunks.append("\x1b[%d;1H\x1b[2K" % (i + 1))
            chunks.append(render_ansi(fit_segments(segs, self.width)))
        chunks.append("\x1b8")                   # przywróć pozycję kursora
        self.out.write_raw("".join(chunks))

    def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        # Skasuj region przewijania i zejdź pod panel, żeby nie nadpisać go tekstem.
        self.out.write_raw("\x1b[r\x1b[%d;1H" % self.height)

    def _sync_cursor(self) -> None:
        """Dolicz wiersze wypisane przez Output od ostatniej synchronizacji.

        Pozycji kursora nie da się odczytać z terminala, a przeliczanie jej
        z licznika i BIEŻĄCEJ wysokości panelu było błędne: linie zapisano przy
        starej wysokości, więc po każdej zmianie rozmiaru kursor lądował
        na zajętym wierszu.
        """
        now = self.out.lines_out
        self._row = min(self.height, self._row + max(0, now - self._seen))
        self._seen = now

    def cursor_row(self) -> int:
        """Wiersz, w którym stoi kursor (do testów)."""
        self._sync_cursor()
        return self._row

    def rows_needed(self) -> int:
        """Ile wierszy zajmie panel przy obecnej zawartości (do testów)."""
        return len(self._panel_lines())


# --------------------------------------------------------------------------- #
#  Sesja przechwytywania.
# --------------------------------------------------------------------------- #
class Capture:
    """Cały stan jednej sesji nasłuchu. Bez zmiennych globalnych poza `_out`."""

    def __init__(self, cfg, out: Output, layout: Layout):
        self.cfg = cfg
        self.out = out
        self.layout = layout
        self.render = make_renderer(layout)
        self.registry = DeviceRegistry(decode_hid=cfg.decode_hid)
        self.hid_state = {}
        self.hold = {}
        self.stats = Stats()
        self.rate = RateMeter() if cfg.hz else None
        self.chatter = ChatterDetector(cfg.chatter_ms) if cfg.chatter_ms else None
        self.analog = AnalogTracker() if cfg.analog else None
        self.dashboard = None
        self.filters = cfg.filters
        self.running = True
        self.hwnd = None
        self.class_name = "DeviceTesterRawInputWnd"
        self._wnd_proc_ptr = WNDPROC(self._wnd_proc)   # referencja MUSI przeżyć okno
        self._buf = (ctypes.c_byte * 4096)()
        self._buf_size = ctypes.sizeof(self._buf)
        self._registered = []
        self._burst = 0
        self._burst_warned = False

    # -- okno --------------------------------------------------------------- #
    def create_window(self):
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASS()
        cls.lpfnWndProc = self._wnd_proc_ptr
        cls.hInstance = hinst
        cls.lpszClassName = self.class_name
        atom = user32.RegisterClassW(ctypes.byref(cls))
        if not atom:
            err = ctypes.get_last_error()
            # 1410 = klasa już zarejestrowana. Wtedy okno dostałoby STARĄ procedurę
            # okna, więc rejestrujemy się pod unikalną nazwą.
            if err == 1410:
                self.class_name = "DeviceTesterRawInputWnd_%d" % (id(self) & 0xFFFFFF)
                cls.lpszClassName = self.class_name
                atom = user32.RegisterClassW(ctypes.byref(cls))
            if not atom:
                err = ctypes.get_last_error()
                if err not in (0, 1410):
                    raise ctypes.WinError(err)
        hwnd = user32.CreateWindowExW(
            0, self.class_name, "DeviceTester", 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinst, None,
        )
        if not hwnd:
            raise ctypes.WinError(ctypes.get_last_error())
        self.hwnd = hwnd
        return hwnd

    def register_devices(self) -> int:
        """Rejestruj każdą parę (usage_page, usage) osobno — jeśli jedna się nie uda,
        reszta i tak działa."""
        ok = 0
        for up, u in TARGET_USAGES:
            rid = RAWINPUTDEVICE()
            rid.usUsagePage = up
            rid.usUsage = u
            rid.dwFlags = RIDEV_INPUTSINK | RIDEV_DEVNOTIFY
            rid.hwndTarget = self.hwnd
            if user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)):
                ok += 1
                self._registered.append((up, u))
            else:
                err = ctypes.get_last_error()
                self.out.segments([
                    ("[uwaga] nie udało się zarejestrować UsagePage=0x%02X Usage=0x%02X "
                     "(błąd %d) — pomijam" % (up, u, err), "warn")])
        return ok

    def cleanup(self) -> None:
        """Wyrejestruj raw input, zniszcz okno i klasę. Nigdy nie rzuca."""
        try:
            for up, u in self._registered:
                rid = RAWINPUTDEVICE()
                rid.usUsagePage = up
                rid.usUsage = u
                rid.dwFlags = RIDEV_REMOVE
                rid.hwndTarget = None      # RIDEV_REMOVE wymaga hwndTarget == NULL
                user32.RegisterRawInputDevices(ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE))
            self._registered = []
        except Exception:
            pass
        try:
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
        except Exception:
            pass
        try:
            user32.UnregisterClassW(self.class_name, kernel32.GetModuleHandleW(None))
        except Exception:
            pass

    # -- zdarzenia ---------------------------------------------------------- #
    def _emit(self, ev: Event) -> None:
        self.stats.event(ev)
        self.out.event(ev, self.render)

    def _emit_rate(self, di: DeviceInfo, mono: float) -> None:
        res = self.rate.take(di.handle, mono)
        if res is None:
            return
        hz, n, span = res
        self.stats.rate(di, hz, n, span)
        self._emit(Event(_ts(), mono, di, "HZ", "%.0f Hz" % hz,
                         "n=%d w %.2f s · szczyt %.0f Hz"
                         % (n, span, self.rate.peak.get(di.handle, hz)),
                         key=(di.handle, "HZ"), style="wheel"))

    def flush_rates(self) -> None:
        """Zamknij niedokończone okna pomiarowe — wołane przy zatrzymaniu."""
        if self.rate is None:
            return
        mono = time.perf_counter()
        for handle in self.rate.pending():
            di = self.registry.lookup(handle)
            if di is None:
                continue          # urządzenie odłączone w trakcie okna
            res = self.rate.take(handle, mono, partial=True)
            if res is not None:
                self.stats.rate(di, res[0], res[1], res[2])

    def on_raw_input(self, hrawinput) -> None:
        # Stempel czasu POWSTAJE TU, przed rozwiązywaniem urządzenia i drukiem —
        # inaczej mierzylibyśmy własny czas przetwarzania zamiast odstępów zdarzeń.
        mono = time.perf_counter()
        size = wintypes.UINT(self._buf_size)
        got = user32.GetRawInputData(hrawinput, RID_INPUT, self._buf, ctypes.byref(size),
                                     ctypes.sizeof(RAWINPUTHEADER))
        if got == UINT_ERR:
            # Bufor za mały (gigantyczny raport HID) — jednorazowa alokacja awaryjna.
            size = wintypes.UINT(0)
            if user32.GetRawInputData(hrawinput, RID_INPUT, None, ctypes.byref(size),
                                      ctypes.sizeof(RAWINPUTHEADER)) == UINT_ERR or size.value == 0:
                return
            big = (ctypes.c_byte * size.value)()
            got = user32.GetRawInputData(hrawinput, RID_INPUT, big, ctypes.byref(size),
                                         ctypes.sizeof(RAWINPUTHEADER))
            if got == UINT_ERR or got == 0:
                return
            buf = big
        else:
            if got == 0:
                return
            buf = self._buf

        ri = ctypes.cast(buf, ctypes.POINTER(RAWINPUT)).contents
        ev_type = ri.header.dwType
        # Prawidłowy uchwyt urządzenia nigdy nie jest 0; NULL (None) = wejście
        # wstrzyknięte (SendInput / hooki) — normalizuj do 0 dla spójnego klucza.
        di = self.registry.resolve(ri.header.hDevice or 0, ev_type)

        if self.filters.active and not self.filters.accept(di, ev_type):
            return

        if self.rate is not None:
            self.rate.add(di.handle, mono)
            if self.rate.due(di.handle, mono):
                self._emit_rate(di, mono)

        ts = _ts()

        if ev_type == RIM_TYPEKEYBOARD:
            ev = decode_keyboard(di, ri.data.keyboard, ts, mono, self.hold, self.chatter)
            if ev is not None:
                if ev.is_break:
                    if ev.hold_ms is not None:
                        self.stats.key_hold(ev.hold_ms)
                else:
                    self.stats.key_press(ev.kname)
                self._emit(ev)
        elif ev_type == RIM_TYPEMOUSE:
            ev = decode_mouse(di, ri.data.mouse, ts, mono, self.cfg.verbose, self.stats,
                              self.chatter)
            if ev is not None:
                self._emit(ev)
        elif ev_type == RIM_TYPEHID:
            for ev in decode_hid(di, ri, got, ts, mono, self.hid_state,
                                 self.cfg.verbose, self.analog):
                self._emit(ev)

    def on_device_change(self, wparam, lparam) -> None:
        handle = int(lparam) & ((1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1)
        if wparam == GIDC_REMOVAL:
            di = self.registry.forget(handle)
            # Klucze stanu HID to (uchwyt, report_id) — trzeba usunąć WSZYSTKIE
            # kubełki tego uchwytu, inaczej wpisy przeciekają.
            for k in [k for k in self.hid_state if k[0] == handle]:
                del self.hid_state[k]
            for k in [k for k in self.hold if k[0] == handle]:
                del self.hold[k]
            if self.rate is not None:
                self.rate.forget(handle)
            label = di.label() if di is not None else "uchwyt %d" % handle
            self.out.segments([(_ts(), "ts"), ("  ", None),
                               ("--- ODŁĄCZONO: ", "removal"), (label, "removal")])
        elif wparam == GIDC_ARRIVAL:
            if self.registry.known(handle):
                # Urządzenia obecne przy starcie są już w rejestrze — nie ogłaszaj
                # ich ponownie.
                return
            di = self.registry.resolve(handle)
            self.out.segments([(_ts(), "ts"), ("  ", None),
                               ("+++ PODŁĄCZONO: ", "arrival"),
                               ("%s %s" % (di.group.tag_text() if di.group else "", di.label()),
                                "arrival")])

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        # WM_INPUT WYMAGA wywołania DefWindowProc, żeby system posprzątał bufor
        # raw-input po każdym zdarzeniu. Dlatego obsługa jest w try/except:
        # wyjątek przelatujący przez granicę callbacku ctypes pominąłby DefWindowProc.
        if msg == WM_INPUT:
            try:
                self._burst += 1
                self.on_raw_input(lparam)
            except Exception as e:
                self._safe_error(e)
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        if msg == WM_INPUT_DEVICE_CHANGE:
            try:
                self.on_device_change(wparam, lparam)
            except Exception as e:
                self._safe_error(e)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _safe_error(self, exc: BaseException) -> None:
        try:
            self.out.segments([(_ts(), "ts"), ("  [błąd dekodowania] %r" % (exc,), "warn")])
        except Exception:
            pass

    # -- pętla -------------------------------------------------------------- #
    def stop(self) -> None:
        self.running = False

    def message_loop(self) -> None:
        msg = wintypes.MSG()
        pmsg = ctypes.byref(msg)          # jeden byref zamiast jednego na komunikat
        while self.running:
            # Czekaj max 200 ms — pozwala obsłużyć Ctrl+C i wyjść czysto.
            r = user32.MsgWaitForMultipleObjectsEx(0, None, 200, QS_ALLINPUT,
                                                   MWMO_INPUTAVAILABLE)
            if r == WAIT_FAILED:
                # Bez tego pętla kręciłaby się na 100% CPU (timeout nie jest respektowany).
                time.sleep(0.05)
            self._burst = 0
            while self.running and user32.PeekMessageW(pmsg, None, 0, 0, PM_REMOVE):
                if msg.message == WM_QUIT:
                    self.running = False
                    break
                # TranslateMessage celowo pominięte: okno jest message-only, nigdy
                # nie ma fokusu, WM_CHAR nie jest obsługiwany — to czysty narzut.
                user32.DispatchMessageW(pmsg)
            if self.dashboard is not None:
                self.dashboard.update()
            if self._burst > 2000 and not self._burst_warned:
                self._burst_warned = True
                self.out.segments([
                    ("[uwaga] bardzo duży napływ zdarzeń (%d w jednym cyklu) — kolejka raw "
                     "input Windows ma twardy limit ~10000 i nadmiar znika bezgłośnie."
                     % self._burst, "warn")])


# --------------------------------------------------------------------------- #
#  Wypisywanie listy urządzeń.
# --------------------------------------------------------------------------- #
def enumerate_devices(registry: DeviceRegistry) -> int:
    count = wintypes.UINT(0)
    user32.GetRawInputDeviceList(None, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if count.value == 0:
        return 0
    arr = (RAWINPUTDEVICELIST * count.value)()
    n = user32.GetRawInputDeviceList(arr, ctypes.byref(count), ctypes.sizeof(RAWINPUTDEVICELIST))
    if n == UINT_ERR:
        return 0
    for i in range(n):
        registry.resolve(arr[i].hDevice, arr[i].dwType)
    return n


DEV_ROW = "%-5s %-28s %-5s %-5s %-8s %s"


def print_devices(out: Output, registry: DeviceRegistry, layout: Layout,
                  filters: Filters = None, show_paths: bool = False) -> None:
    groups = registry.sorted_groups()
    total = sum(len(g.members) for g in groups)
    if not groups:
        out.raw("Brak wykrytych urządzeń Raw Input.")
        return
    box = Box(out, min(layout.width, 96))

    box.top("URZĄDZENIA WEJŚCIOWE · %d urządz. / %d kolekcji" % (len(groups), total))
    box.row(DEV_ROW % ("#", "NAZWA", "VID", "PID", "UP/U", "KOLEKCJE"), "head")

    last_type = object()
    for g in groups:
        p = g.primary()
        if g.type != last_type:
            last_type = g.type
            box.sep(RIDI_TYPE_PL.get(g.type, "INNE"))
        vid = "%04X" % p.vid if p.vid is not None else "----"
        pidv = "%04X" % p.pid if p.pid is not None else "----"
        dim = "" if (filters is None or not filters.active or filters.accept(p)) else "  (odfiltr.)"
        box.row(DEV_ROW % (g.tag_text(), trunc(g.name or "?", 28), vid, pidv,
                           p.usage_text(), g.kinds_text() + dim), g.style)
        if p.extra:
            box.row("      " + p.extra, "meta")
        if show_paths:
            for m in sorted(g.all(), key=lambda m: m.order):
                box.row("      %-9s %s" % (RIDI_TYPE_NAMES.get(m.type, "?"),
                                           trunc(m.path, box.content_w - 16)), "meta")
    box.bottom()


def dump_caps(out: Output, registry: DeviceRegistry) -> None:
    """Zrzut wszystkiego, co system wie o urządzeniach — materiał diagnostyczny
    dla dekodowania raportów HID (HidP_*)."""
    for g in registry.sorted_groups():
        out.raw("")
        out.raw("=== %s  %s  [%s]" % (g.tag_text(), g.name or "?", g.key))
        for di in sorted(g.all(), key=lambda m: m.order):
            out.raw("  handle=%s type=%s" % (di.handle, RIDI_TYPE_NAMES.get(di.type, "?")))
            out.raw("    path        : %s" % di.path)
            out.raw("    product     : %s" % (di.product or "-"))
            out.raw("    manufacturer: %s" % (di.manufacturer or "-"))
            if di.vid is not None:
                out.raw("    vid/pid     : %04X:%04X" % (di.vid, di.pid))
            if di.usage_page is not None:
                out.raw("    usage       : page=%04X usage=%04X" % (di.usage_page, di.usage))
            if di.extra:
                out.raw("    info        : %s" % di.extra)
            try:
                out.raw("    preparsed   : %d B" % _get_preparsed_size(di.handle))
            except Exception:
                pass


# --------------------------------------------------------------------------- #
#  Testy wewnętrzne (--self-test) — bez urządzeń i bez zależności.
# --------------------------------------------------------------------------- #
def self_test(out: Output) -> int:
    checks = []

    def check(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # 1. Rozmiary struktur na 64-bit.
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        for struct, expect in ((RAWINPUTHEADER, 24), (RAWMOUSE, 24), (RAWKEYBOARD, 16),
                               (RAWHID, 12), (RAWINPUT, 48), (RID_DEVICE_INFO, 32),
                               (RAWINPUTDEVICE, 16), (RAWINPUTDEVICELIST, 16)):
            got = ctypes.sizeof(struct)
            check("sizeof(%s) == %d" % (struct.__name__, expect), got == expect, "otrzymano %d" % got)

    # 2. Diff HID: pierwszy raport, brak zmiany, zmiana bajtu.
    st = {}
    r1 = diff_report(st, 1, bytes([0x00, 0x01, 0x02]), False)
    check("HID: pierwszy raport pokazuje całość", r1 is not None and "00 01 02" in r1[0], repr(r1))
    r2 = diff_report(st, 1, bytes([0x00, 0x01, 0x02]), False)
    check("HID: identyczny raport nic nie wypisuje", r2 is None, repr(r2))
    r3 = diff_report(st, 1, bytes([0x00, 0x01, 0x05]), False)
    check("HID: zmiana bajtu pokazuje diff", r3 is not None and "[2] 02→05" in r3[0], repr(r3))

    # 3. Raporty o zmiennej długości — regresja, przez którą urządzenie milkło.
    st = {}
    diff_report(st, 2, bytes([0x00] * 8), False)
    r4 = diff_report(st, 2, bytes([0x00] * 14), False)
    check("HID: zmiana długości raportu nie gubi zdarzenia", r4 is not None, repr(r4))

    # 4. Rozdział stanu po Report ID.
    st = {}
    diff_report(st, 3, bytes([0x01, 0xAA, 0xBB]), False)
    diff_report(st, 3, bytes([0x02, 0x11, 0x22]), False)
    r5 = diff_report(st, 3, bytes([0x01, 0xAA, 0xBC]), False)
    check("HID: przeplot Report ID nie generuje szumu",
          r5 is not None and r5[0] == "rid=01 [2] BB→BC", repr(r5))
    r6 = diff_report(st, 3, bytes([0x02, 0x11, 0x22]), False)
    check("HID: powrót do tego samego stanu innego rid jest ciszą", r6 is None, repr(r6))

    # 5. Renderowanie: kolory nie mogą trafić do logu.
    layout = Layout(100)
    render = make_renderer(layout)
    reg = DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1B1C&PID_1B55#x",
                         info_fn=lambda h: None,
                         product_fn=lambda p: ("Testowa Klawiatura", "ACME"))
    di = reg.resolve(7, RIM_TYPEKEYBOARD)
    ev = Event("10:00:00.000", 0.0, di, "KLAW", "A ↓", "VK 41 sc 1E", key=("k",), style="down")
    plain = render_plain(render(ev, 1))
    ansi = render_ansi(render(ev, 1))
    check("render_plain nie zawiera sekwencji ESC", "\x1b" not in plain, repr(plain))
    check("render_ansi zawiera sekwencje ESC", "\x1b" in ansi)
    check("render_plain zawiera treść zdarzenia", "A ↓" in plain and "#1" in plain, repr(plain))
    check("scalanie ×N nie rozjeżdża szerokości",
          len(render_plain(render(ev, 17))) == len(plain),
          "%d vs %d" % (len(render_plain(render(ev, 17))), len(plain)))

    # 6. Grupowanie i stabilny kolor.
    check("VID/PID czytane ze ścieżki dla klawiatury", di.vid == 0x1B1C and di.pid == 0x1B55,
          "%s/%s" % (di.vid, di.pid))
    check("klucz grupy to VID:PID", di.group_key() == "1B1C:1B55", di.group_key())
    check("kolor urządzenia jest deterministyczny",
          device_style("1B1C:1B55") == device_style("1B1C:1B55"))

    # 7. Scalanie powtórzeń.
    m1 = Event("t", 0.0, di, "MYSZ", "ruch dx=+1 dy=+0", "", ("m",), "mouse", 1, 0, "sum")
    m2 = Event("t", 0.1, di, "MYSZ", "ruch dx=+2 dy=-1", "", ("m",), "mouse", 2, -1, "sum")
    check("scalanie ruchu myszy sumuje dx/dy",
          merge_events(m1, m2).body == "ruch dx=+3 dy=-1", merge_events(m1, m2).body)
    a1 = Event("t", 0.0, di, "MYSZ", "pozycja x=1 y=1", "", ("a",), "mouse", merge_mode="latest")
    a2 = Event("t", 0.1, di, "MYSZ", "pozycja x=900 y=400", "", ("a",), "mouse", merge_mode="latest")
    check("scalanie pozycji absolutnej pokazuje bieżącą",
          merge_events(a1, a2).body == "pozycja x=900 y=400", merge_events(a1, a2).body)

    # 7b. Treść zdarzenia nigdy nie może zostać obcięta; metadane wolno.
    raport = " ".join("%02X" % b for b in range(48))
    hid_ev = Event("t", 0.0, di, "HID", raport, "48 B", ("h",), "hid")
    for w in (40, 80, 120):
        segs = make_renderer(Layout(w))(hid_ev, 1)
        full = render_plain(segs)
        konsola = render_plain(fit_segments(segs, w - 1))
        check("raport HID nietknięty przy szerokości %d" % w, raport in full)
        check("raport HID nietknięty także na konsoli (%d)" % w, raport in konsola)
        # Linia i tak się nie zmieści (sam raport jest dłuższy niż okno), ale
        # metadane muszą zostać oddane jako pierwsze.
        check("metadane ustępują treści przy szerokości %d" % w,
              len(konsola) < len(full) and not konsola.endswith("48 B"),
              "%d vs %d" % (len(konsola), len(full)))

    # 8. Filtry.
    f = Filters(only=["keyboard"], include=["1b1c:1b55"], exclude=[])
    check("filtr --only przepuszcza klawiaturę", f.accept(di))
    check("filtr --only odrzuca inny typ", not f.accept(di, RIM_TYPEMOUSE) or di.type is not None)
    f2 = Filters(exclude=["#1"])
    check("filtr --exclude po tagu działa", not f2.accept(di))
    f3 = Filters(include=["testowa"])
    check("filtr po fragmencie nazwy działa", f3.accept(di))

    # 9. Klasyfikacja urządzenia złożonego: mysz z kolekcją klawiatury (makra)
    #    i klawiatura z kolekcją myszy muszą trafić do właściwych sekcji.
    for label, members, expect in (
        ("mysz z kolekcją klawiatury",
         [(r"\\?\HID#VID_22D4&PID_1503&MI_00#x", RIM_TYPEMOUSE),
          (r"\\?\HID#VID_22D4&PID_1503&MI_01&Col01#x", RIM_TYPEKEYBOARD),
          (r"\\?\HID#VID_22D4&PID_1503&MI_01&Col03#x", RIM_TYPEHID)],
         RIM_TYPEMOUSE),
        ("klawiatura z kolekcją myszy",
         [(r"\\?\HID#VID_1B1C&PID_1B55&MI_00&Col05#x", RIM_TYPEMOUSE),
          (r"\\?\HID#VID_1B1C&PID_1B55&MI_00&Col01#x", RIM_TYPEKEYBOARD),
          (r"\\?\HID#VID_1B1C&PID_1B55&MI_01#x", RIM_TYPEHID)],
         RIM_TYPEKEYBOARD),
    ):
        paths = {}
        r = DeviceRegistry(name_fn=lambda h: paths[h], info_fn=lambda h: None,
                           product_fn=lambda p: ("Urządzenie", ""))
        for i, (path, typ) in enumerate(members):
            paths[100 + i] = path
            r.resolve(100 + i, typ)
        g = next(iter(r.groups.values()))
        check("klasyfikacja: %s" % label, g.type == expect,
              "otrzymano %s" % RIDI_TYPE_NAMES.get(g.type, "?"))
    check("opis kolekcji grupy", g.kinds_text() == "KLAW MYSZ HID", g.kinds_text())
    check("kolejność kolekcji ze ścieżki",
          parse_collection(r"\\?\HID#VID_1&PID_2&MI_01&Col05#x") == (1, 5),
          str(parse_collection(r"\\?\HID#VID_1&PID_2&MI_01&Col05#x")))

    # 10. Geometria ramki — każda krawędź musi mieć tę samą szerokość.
    lines = []

    class _Cap(Output):
        def segments(self, segs):
            lines.append(render_plain(segs))

    box = Box(_Cap(), 40)
    box.top("TYTUŁ")
    box.row("treść")
    box.sep("SEKCJA")
    box.row("x" * 200)
    box.bottom()
    check("ramka ma równą szerokość", all(len(x) == 40 for x in lines),
          str([len(x) for x in lines]))
    check("ramka domyka każdy wiersz",
          all(x[0] in "┌├│└" and x[-1] in "┐┤│┘" for x in lines), repr(lines[:2]))

    # 11. Nazwy klawiszy — ścieżki fallbacku (bez Win32 też mają działać).
    check("Pause z prefiksem E1 nie jest nazwany Ctrl",
          key_name(VK_PAUSE, 0x1D, False, True) == "Pause",
          key_name(VK_PAUSE, 0x1D, False, True))

    # 12. Znacznik czasu.
    ts = _ts()
    check("format znacznika czasu", len(ts) == 12 and ts[2] == ":" and ts[8] == ".", ts)

    # 13. Truncacja i wypełnianie.
    check("trunc dodaje wielokropek", trunc("abcdefgh", 4) == "abc…", trunc("abcdefgh", 4))
    check("pad ma dokładną szerokość", len(pad("abcdefgh", 4)) == 4 and len(pad("ab", 6)) == 6)

    failed = [c for c in checks if not c[1]]
    for name, ok, detail in checks:
        mark = "  OK  " if ok else " BŁĄD "
        style = "arrival" if ok else "removal"
        segs = [("[", None), (mark, style), ("] ", None), (name, None)]
        if not ok and detail:
            segs.append(("  → " + detail, "warn"))
        out.segments(segs)
    out.raw("")
    out.segments([("%d/%d testów przeszło" % (len(checks) - len(failed), len(checks)),
                   "removal" if failed else "arrival")])
    return 1 if failed else 0


# --------------------------------------------------------------------------- #
#  CLI.
# --------------------------------------------------------------------------- #
class Config:
    __slots__ = ("verbose", "color", "dedup", "filters", "log_path", "summary",
                 "paths", "decode_hid", "hz", "chatter_ms", "analog", "dashboard")

    def __init__(self, verbose=False, color=False, dedup=True, filters=None,
                 log_path=None, summary=True, paths=False, decode_hid=True,
                 hz=False, chatter_ms=None, analog=False, dashboard=False):
        self.hz = hz
        self.chatter_ms = chatter_ms
        self.analog = analog
        self.dashboard = dashboard
        self.verbose = verbose
        self.color = color
        self.dedup = dedup
        self.filters = filters or Filters()
        self.log_path = log_path
        self.summary = summary
        self.paths = paths
        self.decode_hid = decode_hid


# Lista komend do --help. Trzymana osobno od argparse, bo domyślny zrzut argparse
# jest jednym nieposortowanym blokiem — przy 20 opcjach nie da się w nim znaleźć
# tego, czego się szuka.
HELP_SECTIONS = (
    ("TRYBY PRACY", (
        ("(bez opcji)", "nasłuch: klawisze, przyciski, kółko, raporty HID"),
        ("-v, --verbose", "dodatkowo ruch myszy i pełne raporty HID"),
        ("-l, --list", "wypisz podłączone urządzenia i zakończ"),
        ("--dump-caps", "pełne dane diagnostyczne urządzeń i zakończ"),
        ("--self-test", "testy wewnętrzne (nie wymagają urządzeń) i zakończ"),
        ("-h, --help", "ta pomoc"),
    )),
    ("FILTROWANIE", (
        ("--only TYP", "tylko keyboard | mouse | hid (można powtórzyć)"),
        ("--device WZORZEC", "tylko wskazane: #2, 1B1C:1B55 lub fragment nazwy"),
        ("--exclude WZORZEC", "pomiń wskazane (ta sama składnia)"),
    )),
    ("DIAGNOSTYKA SPRZĘTU", (
        ("--hz", "częstotliwość raportowania (średnia z okna 1 s)"),
        ("--chatter [MS]", "drganie styków; próg domyślnie 30 ms"),
        ("--analog", "paski osi na żywo + zasięg i martwa strefa"),
        ("--raw-hid", "surowe bajty raportu zamiast nazw przycisków"),
    )),
    ("WYGLĄD", (
        ("--dashboard", "przypięty panel na żywo u góry ekranu"),
        ("--color / --no-color", "wymuś lub wyłącz kolory (działa też NO_COLOR)"),
        ("--no-dedup", "nie scalaj powtórzeń w linię ×N"),
        ("--no-summary", "bez podsumowania sesji po zatrzymaniu"),
        ("--paths", "w --list pokaż pełne ścieżki wszystkich kolekcji"),
    )),
    ("ZAPIS I EKSPORT", (
        ("--log [PLIK]", "log tekstowy na bieżąco (bez nazwy = automatyczna)"),
        ("--jsonl [PLIK]", "eksport strukturalny, jeden obiekt JSON na linię"),
        ("--csv [PLIK]", "eksport do arkusza, płaskie kolumny"),
    )),
)

HELP_EXAMPLES = (
    ("python device_tester.py --verbose", "wszystko, łącznie z ruchem myszy"),
    ("python device_tester.py --only mouse --hz", "ile Hz ma moja mysz"),
    ("python device_tester.py --chatter", "czy mikroswitch nie odbija"),
    ("python device_tester.py --device 1B1C:1B55", "tylko jedno urządzenie"),
    ("python device_tester.py --dashboard --log s.log", "panel na żywo + zapis sesji"),
)


def print_help(out: Output) -> None:
    """Pomoc pogrupowana tematycznie, w tej samej stylistyce co reszta wyjścia."""
    out.segments([("device_tester.py", "head"),
                  (" — uniwersalny tester urządzeń wejściowych (Windows Raw Input)", None)])
    out.raw("")
    out.segments([("  UŻYCIE", "head")])
    out.segments([("    python device_tester.py [opcje]", None)])

    width = max(len(name) for _, opts in HELP_SECTIONS for name, _ in opts) + 2
    for title, opts in HELP_SECTIONS:
        out.raw("")
        out.segments([("  " + title, "head")])
        for name, desc in opts:
            out.segments([("    ", None), (pad(name, width), "down"), (desc, None)])

    out.raw("")
    out.segments([("  PRZYKŁADY", "head")])
    ex_w = max(len(c) for c, _ in HELP_EXAMPLES) + 2
    for cmd, why in HELP_EXAMPLES:
        out.segments([("    ", None), (pad(cmd, ex_w), "mouse"), ("# " + why, "meta")])

    out.raw("")
    out.segments([("  Zatrzymanie: Ctrl+C. Nasłuch działa też, gdy okno nie ma "
                   "fokusu — możesz klikać", "meta")])
    out.segments([("  w innych aplikacjach. Nie wymaga uprawnień administratora.",
                   "meta")])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Uniwersalny tester podłączonych urządzeń wejściowych (Windows Raw Input).",
        add_help=False,
    )
    # Własne -h: domyślne argparse wypisałoby płaską listę bez podziału na sekcje.
    p.add_argument("-h", "--help", action="store_true",
                   help="pokaż pogrupowaną listę komend i zakończ")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="pokazuj też ruch myszy i pełne raporty HID")
    p.add_argument("--list", "-l", action="store_true",
                   help="tylko wypisz podłączone urządzenia i zakończ")
    p.add_argument("--paths", action="store_true",
                   help="w --list pokaż pełne ścieżki wszystkich kolekcji")
    p.add_argument("--log", "-o", nargs="?", const="__auto__", default=None, metavar="PLIK",
                   help="zapisuj wszystko z konsoli na bieżąco do pliku (append, flush po każdej "
                        "linii); bez nazwy = auto device-tester-DATA-CZAS.log")
    p.add_argument("--only", action="append", metavar="TYP",
                   help="pokazuj tylko dany typ: keyboard | mouse | hid (można powtórzyć)")
    p.add_argument("--device", action="append", metavar="WZORZEC",
                   help="pokazuj tylko wskazane urządzenie: #N, VID:PID lub fragment nazwy")
    p.add_argument("--exclude", action="append", metavar="WZORZEC",
                   help="pomijaj wskazane urządzenie (ta sama składnia co --device)")
    p.add_argument("--color", dest="color", action="store_const", const=True, default=None,
                   help="wymuś kolory")
    p.add_argument("--no-color", dest="color", action="store_const", const=False,
                   help="wyłącz kolory (respektowane jest też NO_COLOR)")
    p.add_argument("--no-dedup", action="store_true",
                   help="nie scalaj powtórzeń w linię ×N")
    p.add_argument("--raw-hid", action="store_true",
                   help="nie tłumacz raportów HID na nazwy przycisków/osi — pokaż surowe bajty")
    p.add_argument("--jsonl", nargs="?", const="__auto__", default=None, metavar="PLIK",
                   help="eksportuj zdarzenia jako JSON Lines (jeden obiekt na linię)")
    p.add_argument("--csv", nargs="?", const="__auto__", default=None, metavar="PLIK",
                   help="eksportuj zdarzenia jako CSV (płaskie kolumny do arkusza)")
    p.add_argument("--analog", action="store_true",
                   help="analiza osi analogowych: paski na żywo, zasięg i martwa strefa")
    p.add_argument("--dashboard", action="store_true",
                   help="przypięty panel na żywo u góry ekranu (wymaga konsoli z ANSI)")
    p.add_argument("--hz", action="store_true",
                   help="mierz częstotliwość raportowania urządzeń (średnia z okna 1 s)")
    p.add_argument("--chatter", nargs="?", type=float, const=30.0, default=None,
                   metavar="MS",
                   help="wykrywaj drganie styków: ponowne wciśnięcie szybciej niż MS "
                        "po zwolnieniu (domyślnie 30 ms)")
    p.add_argument("--no-summary", action="store_true",
                   help="nie pokazuj podsumowania sesji po zatrzymaniu")
    p.add_argument("--dump-caps", action="store_true",
                   help="wypisz pełne dane diagnostyczne urządzeń i zakończ")
    p.add_argument("--self-test", action="store_true",
                   help="uruchom testy wewnętrzne (nie wymaga urządzeń) i zakończ")
    return p


def resolve_color(flag) -> bool:
    """--no-color > NO_COLOR > --color > autodetekcja."""
    if flag is False:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    if flag is True:
        return True
    if not stdout_is_console():
        return False           # przekierowanie do pliku/potoku — nigdy kolory
    return console_supports_vt()


def main(argv=None) -> int:
    global _out

    args = build_parser().parse_args(argv)

    color = resolve_color(args.color)
    if color and args.color is not True:
        pass  # tryb VT już włączony przez resolve_color()
    elif color:
        console_supports_vt()

    width = console_width()
    layout = Layout(width)
    out_ = Output(color=color, dedup=not args.no_dedup, width=width)
    _out = out_

    cfg = Config(
        verbose=args.verbose,
        color=color,
        dedup=not args.no_dedup,
        filters=Filters(args.only, args.device, args.exclude),
        log_path=args.log,
        summary=not args.no_summary,
        paths=args.paths,
        decode_hid=not args.raw_hid,
        hz=args.hz,
        chatter_ms=args.chatter,
        analog=args.analog,
        dashboard=args.dashboard,
    )

    try:
        # Pomoc przed sprawdzeniem platformy — musi działać wszędzie.
        if args.help:
            print_help(out_)
            return 0

        if args.self_test:
            return self_test(out_)

        if not IS_WINDOWS:
            out_.segments([("Ten program działa tylko na Windows "
                            "(Raw Input to API Windows).", "warn")])
            return 1

        if cfg.log_path is not None:
            path = cfg.log_path
            if path == "__auto__":
                path = time.strftime("device-tester-%Y%m%d-%H%M%S.log")
            if out_.open_log(path):
                out_.raw("# Log: %s  (start %s)" % (path, time.strftime("%Y-%m-%d %H:%M:%S")))

        for kind, value in (("jsonl", args.jsonl), ("csv", args.csv)):
            if value is None:
                continue
            path = value
            if path == "__auto__":
                path = time.strftime("device-tester-%Y%m%d-%H%M%S." + kind)
            if out_.add_sink(kind, path):
                out_.segments([("# Eksport %s: %s" % (kind.upper(), path), "meta")])

        registry = None
        if args.list or args.dump_caps:
            registry = DeviceRegistry()
            n = enumerate_devices(registry)
            if n == 0:
                out_.raw("Brak wykrytych urządzeń Raw Input.")
                return 0
            if args.dump_caps:
                dump_caps(out_, registry)
            else:
                print_devices(out_, registry, layout, cfg.filters, cfg.paths)
            return 0

        cap = Capture(cfg, out_, layout)
        cap.create_window()
        try:
            # Najpierw spis urządzeń, dopiero potem rejestracja: RIDEV_DEVNOTIFY
            # natychmiast kolejkuje GIDC_ARRIVAL dla wszystkiego, co już jest
            # podłączone, a znane uchwyty nie są ogłaszane po raz drugi.
            enumerate_devices(cap.registry)
            if cap.register_devices() == 0:
                out_.segments([("Nie udało się zarejestrować żadnego typu urządzenia. "
                                "Przerywam.", "removal")])
                return 1

            print_devices(out_, cap.registry, layout, cfg.filters, cfg.paths)
            out_.raw("")
            hints = ["Wciskaj klawisze / przyciski — zdarzenia pojawią się poniżej.",
                     "Działa też gdy to okno nie ma fokusu. Zatrzymanie: Ctrl+C."]
            if not cfg.verbose:
                hints.append("(ruch myszy i pełne raporty HID ukryte — użyj --verbose)")
            if cfg.filters.active:
                hints.append("(filtry aktywne — część urządzeń jest pomijana)")
            if cfg.hz:
                hints.append("(pomiar Hz: średnia z okna 1 s, liczona w chwili "
                             "dostarczenia zdarzenia)")
            if cfg.chatter_ms:
                hints.append("(wykrywanie drgania styków: próg %.0f ms)" % cfg.chatter_ms)
            for h in hints:
                out_.segments([(h, "meta")])
            out_.raw("")
            out_.segments([(layout.header(), "head")])

            try:
                signal.signal(signal.SIGINT, lambda *_: cap.stop())
            except (ValueError, OSError):
                pass  # np. nie w głównym wątku — fallback na KeyboardInterrupt niżej

            if cfg.dashboard:
                cap.dashboard = Dashboard(out_, cap, width, console_height())
                if not cap.dashboard.start():
                    cap.dashboard = None
                    out_.segments([("[uwaga] --dashboard wymaga konsoli obsługującej "
                                    "ANSI i co najmniej %d wierszy — pomijam"
                                    % Dashboard.MIN_HEIGHT, "warn")])
                elif cfg.log_path is None:
                    out_.segments([("[uwaga] linie wypchnięte poza panel nie trafiają "
                                    "do bufora przewijania — dodaj --log", "warn")])

            try:
                cap.message_loop()
            except KeyboardInterrupt:
                cap.stop()
        finally:
            if cap.dashboard is not None:
                cap.dashboard.stop()
            cap.cleanup()

        out_.raw("")
        out_.segments([("Zatrzymano.", "meta")])
        cap.flush_rates()
        if cap.chatter is not None:
            cap.stats.chatter_hits = cap.chatter.hits
        if cap.analog is not None:
            cap.stats.analog_rows = cap.analog.report()
        if cfg.summary:
            cap.stats.render(out_, layout)
        return 0
    finally:
        out_.close_log()
        restore_console()


if __name__ == "__main__":
    sys.exit(main())
