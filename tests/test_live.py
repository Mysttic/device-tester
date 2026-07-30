# -*- coding: utf-8 -*-
"""Test całej ścieżki live: okno komunikatów → WM_INPUT → dekodery → wyjście.

Zdarzenia są wstrzykiwane przez SendInput, więc trafiają do tej samej kolejki
raw input co prawdziwe urządzenia. Używane są WYŁĄCZNIE klawisze F13-F15, których
żadna typowa aplikacja nie obsługuje — test nie wpisuje niczego w aktywne okno.

Wejście wstrzyknięte ma hDevice = NULL, więc pokrywa ścieżkę uchwytu 0.
"""

import ctypes
import os
import sys
import threading
import time
import unittest
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import device_tester as dt  # noqa: E402

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
VK_F13, VK_F14 = 0x7C, 0x7D


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


class CollectingOutput(dt.Output):
    """Wyjście zbierające linie zamiast pisać na konsolę."""

    def __init__(self):
        dt.Output.__init__(self, color=False, dedup=False, width=120)
        self.lines = []

    def _write(self, text):
        self.lines.append(text.rstrip("\n"))

    def text(self):
        return "\n".join(self.lines)


@unittest.skipUnless(dt.IS_WINDOWS, "Raw Input to API Windows")
class TestLiveCapture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.user32 = ctypes.WinDLL("user32", use_last_error=True)
        cls.user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        cls.user32.SendInput.restype = wintypes.UINT
        cls.out = CollectingOutput()
        cls.cap = cls._run_session(cls.out)

    @classmethod
    def _send(cls, *inputs):
        arr = (INPUT * len(inputs))(*inputs)
        sent = cls.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        time.sleep(0.02)
        return sent

    @classmethod
    def _key(cls, vk, up=False):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, 0)
        return i

    @classmethod
    def _move(cls, dx, dy):
        i = INPUT()
        i.type = INPUT_MOUSE
        i.mi = MOUSEINPUT(dx, dy, 0, MOUSEEVENTF_MOVE, 0, 0)
        return i

    @classmethod
    def _run_session(cls, out):
        cfg = dt.Config(verbose=True, color=False, dedup=False)
        cap = dt.Capture(cfg, out, dt.Layout(120))
        cap.create_window()
        # Ta sama kolejność co w main(): spis przed rejestracją, inaczej
        # RIDEV_DEVNOTIFY ogłosi wszystkie już podłączone urządzenia.
        dt.enumerate_devices(cap.registry)
        cls.registered = cap.register_devices()

        def inject():
            time.sleep(0.4)
            cls._send(cls._key(VK_F13))
            cls._send(cls._key(VK_F13, up=True))
            cls._send(cls._key(VK_F14))
            cls._send(cls._key(VK_F14, up=True))
            # Ruch względny: sprawdza, czy typ urządzenia nie został zamrożony
            # na KEYBOARD przez pierwsze zdarzenie z tego samego uchwytu (0).
            cls._send(cls._move(3, 0))
            cls._send(cls._move(-3, 0))
            time.sleep(0.3)
            cap.stop()

        t = threading.Thread(target=inject, daemon=True)
        t.start()
        cap.message_loop()
        t.join(timeout=5)
        cap.cleanup()
        return cap

    def test_registration_succeeded(self):
        self.assertGreater(self.registered, 0)
        self.assertEqual(self.registered, len(dt.TARGET_USAGES))

    def test_keyboard_events_captured(self):
        body = self.out.text()
        self.assertIn("KLAW", body)
        self.assertIn("↓", body)
        self.assertIn("↑", body)

    def test_key_hold_duration_measured(self):
        self.assertIn(" ms", self.out.text())

    def test_mouse_movement_captured(self):
        self.assertIn("ruch dx=", self.out.text())

    def test_device_type_is_not_frozen_by_first_event(self):
        """Regresja: typ był ustawiany raz w cache'owanym obiekcie, więc ruch myszy
        z uchwytu 0 pojawiał się pod etykietą „(KEYBOARD)"."""
        kinds = set()
        for line in self.out.lines:
            for kind in ("KLAW", "MYSZ"):
                if (" %s " % kind) in line:
                    kinds.add(kind)
        self.assertEqual(kinds, {"KLAW", "MYSZ"},
                         "oba typy muszą wystąpić; otrzymano %s" % kinds)

    def test_no_ansi_when_color_disabled(self):
        self.assertNotIn("\x1b", self.out.text())

    def test_no_trailing_whitespace(self):
        for line in self.out.lines:
            self.assertEqual(line, line.rstrip(), repr(line))

    def test_already_connected_devices_are_not_announced(self):
        """Regresja kolejności: rejestracja przed spisem urządzeń zalewała start
        komunikatami „PODŁĄCZONO" o sprzęcie, który był podłączony cały czas."""
        self.assertNotIn("PODŁĄCZONO", self.out.text())

    def test_injected_input_is_labelled_as_such(self):
        self.assertIn("wstrzyknięte", self.out.text())

    def test_function_key_name_resolved_without_scancode(self):
        """SendInput daje scancode 0 — nazwa musi powstać z kodu VK."""
        self.assertIn("F13", self.out.text())
        self.assertNotIn("VK_0x7C", self.out.text())

    def test_stats_counted_every_event(self):
        self.assertGreaterEqual(self.cap.stats.total, 6)

    def test_cleanup_released_the_window(self):
        self.assertIsNone(self.cap.hwnd)
        self.assertEqual(self.cap._registered, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
