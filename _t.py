#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ręczny podgląd ścieżki live: wstrzykuje kilka zdarzeń i pokazuje, co narzędzie z nich zrobi.

Do szybkiego oka. Właściwy zestaw testów jest w tests/ i sprawdza to samo asercjami:

    python device_tester.py --self-test          # logika, bez urządzeń
    python -m unittest discover -s tests -v      # w tym pełna ścieżka WM_INPUT

Używane są wyłącznie klawisze F13/F14, których żadna typowa aplikacja nie obsługuje,
więc nic nie zostanie wpisane w aktywne okno.
"""

import ctypes
import sys
import threading
import time
from ctypes import wintypes

import device_tester as dt

user32 = ctypes.WinDLL("user32", use_last_error=True)


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUT_U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001


def send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    time.sleep(0.04)


def key(vk, up=False):
    i = INPUT()
    i.type = 1
    i.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
    return i


def move(dx, dy):
    i = INPUT()
    i.type = 0
    i.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)
    return i


def main():
    if not dt.IS_WINDOWS:
        print("Tylko Windows.")
        return 1

    out = dt.Output(color=dt.resolve_color(None), dedup=True, width=dt.console_width())
    layout = dt.Layout(out.width)
    cap = dt.Capture(dt.Config(verbose=True, color=out.color), out, layout)
    cap.create_window()
    dt.enumerate_devices(cap.registry)
    print("zarejestrowane usage:", cap.register_devices(), "/", len(dt.TARGET_USAGES))
    dt.print_devices(out, cap.registry, layout)
    out.raw("")
    out.segments([(layout.header(), "head")])

    def inject():
        time.sleep(0.4)
        for vk in (0x7C, 0x7D):          # F13, F14
            send(key(vk))
            send(key(vk, up=True))
        for _ in range(5):               # powtórzenia -> scalanie ×N
            send(key(0x7C))
        send(key(0x7C, up=True))
        for _ in range(4):
            send(move(2, 1))
        send(move(-8, -4))
        time.sleep(0.3)
        cap.stop()

    t = threading.Thread(target=inject, daemon=True)
    t.start()
    try:
        cap.message_loop()
    finally:
        t.join(timeout=5)
        cap.cleanup()
    cap.stats.render(out, layout)
    dt.restore_console()
    return 0


if __name__ == "__main__":
    sys.exit(main())
