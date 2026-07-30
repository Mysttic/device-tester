#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generator zrzutów ekranu do README.

Uruchamia PRAWDZIWY kod device_tester.py, przechwytuje jego wyjście wraz
z sekwencjami ANSI, odtwarza je w minimalnym emulatorze terminala i renderuje
siatkę znaków do PNG. Dzięki temu zrzut pokazuje dokładnie to, co zobaczyłby
użytkownik — łącznie z panelem na żywo i nadpisywaniem linii przy scalaniu ×N.

To narzędzie deweloperskie, NIE część device_tester.py. Wymaga Pillow, którego
samo narzędzie nie potrzebuje:

    pip install pillow
    python docs/make_screenshots.py
"""

import ctypes
import os
import re
import sys
import threading
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import device_tester as dt  # noqa: E402

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\lucon.ttf",
]
FONT_BOLD_CANDIDATES = [
    r"C:\Windows\Fonts\CascadiaMono.ttf",
    r"C:\Windows\Fonts\consolab.ttf",
]

FONT_SIZE = 16
BG = (12, 12, 12)
FG = (204, 204, 204)
CHROME = (32, 32, 32)
PAD = 14
TITLEBAR = 30


# --------------------------------------------------------------------------- #
#  Paleta xterm-256.
# --------------------------------------------------------------------------- #
def xterm256(n: int):
    if n < 16:
        base = [(12, 12, 12), (197, 15, 31), (19, 161, 14), (193, 156, 0),
                (0, 55, 218), (136, 23, 152), (58, 150, 221), (204, 204, 204),
                (118, 118, 118), (231, 72, 86), (22, 198, 12), (249, 241, 165),
                (59, 120, 255), (180, 0, 158), (97, 214, 214), (242, 242, 242)]
        return base[n]
    if n < 232:
        n -= 16
        levels = (0, 95, 135, 175, 215, 255)
        return (levels[n // 36], levels[(n // 6) % 6], levels[n % 6])
    v = 8 + (n - 232) * 10
    return (v, v, v)


# --------------------------------------------------------------------------- #
#  Minimalny emulator terminala — obsługuje dokładnie ten podzbiór sekwencji,
#  którego używa device_tester.py.
# --------------------------------------------------------------------------- #
class Term:
    def __init__(self, cols: int, rows: int):
        self.cols = cols
        self.rows = rows
        self.grid = [[(" ", FG, False) for _ in range(cols)] for _ in range(rows)]
        self.cx = 0
        self.cy = 0
        self.fg = FG
        self.bold = False
        self.top = 0
        self.bottom = rows - 1
        self.saved = (0, 0)

    # -- pomocnicze --------------------------------------------------------- #
    def _blank_row(self):
        return [(" ", FG, False) for _ in range(self.cols)]

    def _scroll_region_up(self):
        del self.grid[self.top]
        self.grid.insert(self.bottom, self._blank_row())

    def _newline(self):
        if self.cy >= self.bottom:
            self._scroll_region_up()
            self.cy = self.bottom
        else:
            self.cy += 1

    def _put(self, ch: str):
        if self.cx >= self.cols:
            self.cx = 0
            self._newline()
        if 0 <= self.cy < self.rows:
            self.grid[self.cy][self.cx] = (ch, self.fg, self.bold)
        self.cx += 1

    # -- SGR ---------------------------------------------------------------- #
    def _sgr(self, params):
        i = 0
        if not params:
            params = [0]
        while i < len(params):
            p = params[i]
            if p == 0:
                self.fg, self.bold = FG, False
            elif p == 1:
                self.bold = True
            elif p == 38 and i + 2 < len(params) and params[i + 1] == 5:
                self.fg = xterm256(params[i + 2])
                i += 2
            i += 1

    # -- główna pętla ------------------------------------------------------- #
    CSI = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")

    def feed(self, text: str):
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":
                if text.startswith("\x1b7", i):
                    self.saved = (self.cx, self.cy)
                    i += 2
                    continue
                if text.startswith("\x1b8", i):
                    self.cx, self.cy = self.saved
                    i += 2
                    continue
                m = self.CSI.match(text, i)
                if not m:
                    i += 1
                    continue
                raw, cmd = m.group(1), m.group(2)
                params = [int(x) for x in raw.split(";") if x.isdigit()]
                self._csi(cmd, params, raw)
                i = m.end()
                continue
            if ch == "\n":
                self.cx = 0
                self._newline()
            elif ch == "\r":
                self.cx = 0
            elif ch == "\t":
                self.cx = min(self.cols - 1, (self.cx // 8 + 1) * 8)
            else:
                self._put(ch)
            i += 1

    def _csi(self, cmd, params, raw):
        if cmd == "m":
            self._sgr(params)
        elif cmd == "H":
            self.cy = (params[0] - 1) if params else 0
            self.cx = (params[1] - 1) if len(params) > 1 else 0
            self.cy = max(0, min(self.rows - 1, self.cy))
            self.cx = max(0, min(self.cols - 1, self.cx))
        elif cmd == "A":
            self.cy = max(0, self.cy - (params[0] if params else 1))
        elif cmd == "B":
            self.cy = min(self.rows - 1, self.cy + (params[0] if params else 1))
        elif cmd == "J":
            mode = params[0] if params else 0
            if mode == 2:
                self.grid = [self._blank_row() for _ in range(self.rows)]
                self.cx = self.cy = 0
        elif cmd == "K":
            mode = params[0] if params else 0
            if mode == 2:
                self.grid[self.cy] = self._blank_row()
            elif mode == 0:
                for x in range(self.cx, self.cols):
                    self.grid[self.cy][x] = (" ", FG, False)
        elif cmd == "r":
            if len(params) >= 2:
                self.top = max(0, params[0] - 1)
                self.bottom = min(self.rows - 1, params[1] - 1)
            else:
                self.top, self.bottom = 0, self.rows - 1

    # -- render ------------------------------------------------------------- #
    def trim(self):
        """Utnij puste wiersze na dole, żeby zrzut nie miał pustej połowy."""
        last = 0
        for y in range(self.rows):
            if any(c[0] != " " for c in self.grid[y]):
                last = y
        self.rows = last + 1
        self.grid = self.grid[:self.rows]
        return self

    def to_png(self, path: str, title: str):
        font = _load(FONT_CANDIDATES, FONT_SIZE)
        font_b = _load(FONT_BOLD_CANDIDATES, FONT_SIZE)
        probe = Image.new("RGB", (10, 10))
        d = ImageDraw.Draw(probe)
        box = d.textbbox((0, 0), "M", font=font)
        cw = box[2] - box[0]
        chh = int(FONT_SIZE * 1.35)

        w = PAD * 2 + cw * self.cols
        h = TITLEBAR + PAD * 2 + chh * self.rows
        img = Image.new("RGB", (w, h), BG)
        dr = ImageDraw.Draw(img)

        # pasek tytułu — żeby zrzut czytelnie wyglądał jak okno terminala
        dr.rectangle([0, 0, w, TITLEBAR], fill=CHROME)
        for i, col in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            dr.ellipse([12 + i * 18, 10, 22 + i * 18, 20], fill=col)
        dr.text((78, 7), title, font=font, fill=(160, 160, 160))

        for y in range(self.rows):
            row = self.grid[y]
            x = 0
            while x < self.cols:
                ch, fg, bold = row[x]
                if ch == " ":
                    x += 1
                    continue
                run = [ch]
                x2 = x + 1
                while x2 < self.cols and row[x2][1] == fg and row[x2][2] == bold \
                        and row[x2][0] != " ":
                    run.append(row[x2][0])
                    x2 += 1
                dr.text((PAD + x * cw, TITLEBAR + PAD + y * chh), "".join(run),
                        font=font_b if bold else font, fill=fg)
                x = x2

        img.save(path)
        print("  %-28s %d×%d" % (os.path.basename(path), w, h))


def _load(candidates, size):
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


# --------------------------------------------------------------------------- #
#  Wstrzykiwanie zdarzeń (te same struktury co w tests/test_live.py).
# --------------------------------------------------------------------------- #
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


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_WHEEL = 0x0800


def send(i, pause=0.045):
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
    time.sleep(pause)


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


def wheel(delta):
    i = INPUT()
    i.type = 0
    i.mi = MOUSEINPUT(0, 0, delta & 0xFFFFFFFF, MOUSEEVENTF_WHEEL, 0, 0)
    return i


# --------------------------------------------------------------------------- #
#  Scenariusze.
# --------------------------------------------------------------------------- #
def make_output(term, cols, dedup=True):
    out = dt.Output(color=True, dedup=dedup, width=cols)
    out.dedup = dedup                      # stdout nie jest tu konsolą
    out._write = term.feed
    return out


def shot_list(cols=98):
    term = Term(cols, 24)
    out = make_output(term, cols, dedup=False)
    reg = dt.DeviceRegistry()
    dt.enumerate_devices(reg)
    dt.print_devices(out, reg, dt.Layout(cols))
    return term.trim()


def _run_capture(term, out, cols, cfg, inject, seconds=3.0, dashboard=False):
    cap = dt.Capture(cfg, out, dt.Layout(cols))
    cap.create_window()
    dt.enumerate_devices(cap.registry)
    cap.register_devices()
    if dashboard:
        cap.dashboard = dt.Dashboard(out, cap, cols, term.rows)
        cap.dashboard.start()

    done = threading.Event()

    def worker():
        time.sleep(0.35)
        try:
            inject(cap)
        finally:
            time.sleep(0.35)
            cap.stop()
            done.set()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    cap.message_loop()
    done.wait(timeout=seconds)
    if cap.dashboard is not None:
        cap.dashboard.stop()
    cap.cleanup()
    cap.flush_rates()
    if cap.chatter is not None:
        cap.stats.chatter_hits = cap.chatter.hits
    return cap


def shot_events(cols=98):
    term = Term(cols, 26)
    out = make_output(term, cols)
    out.segments([(dt.Layout(cols).header(), "head")])

    def inject(cap):
        for vk in (0x41, 0x53, 0x44):          # A, S, D
            send(key(vk))
            send(key(vk, up=True))
        for _ in range(6):                     # autorepeat -> scalenie ×N
            send(key(0x27), 0.03)              # strzałka w prawo
        send(key(0x27, up=True))
        send(key(0xA0))                        # lewy Shift
        send(key(0xA0, up=True))
        for _ in range(4):
            send(move(9, -4), 0.03)
        send(wheel(120))
        send(wheel(-120))

    _run_capture(term, out, cols, dt.Config(verbose=True, color=True), inject)
    return term.trim()


def shot_summary(cols=84):
    term = Term(cols, 30)
    out = make_output(term, cols, dedup=False)

    def inject(cap):
        for vk in (0x41, 0x53, 0x44, 0x46):
            send(key(vk))
            send(key(vk, up=True), 0.12)
        # zużyty mikroswitch: odbicia poniżej progu
        for _ in range(3):
            send(key(0x47), 0.005)
            send(key(0x47, up=True), 0.006)
        for _ in range(6):
            send(move(14, 7), 0.02)
        send(wheel(120))

    cfg = dt.Config(verbose=False, color=True, hz=True, chatter_ms=30.0, analog=True)
    cap = _run_capture(term, out, cols, cfg, inject)

    # Osie analogowe: na tej maszynie nie ma pada, więc pokazujemy sam szkielet
    # raportu tylko wtedy, gdy realnie coś zmierzono.
    if cap.analog is not None:
        cap.stats.analog_rows = cap.analog.report()
    term.grid = [term._blank_row() for _ in range(term.rows)]
    term.cx = term.cy = 0
    cap.stats.render(out, dt.Layout(cols))
    return term.trim()


def shot_hid(cols=98):
    """Dekodowanie HID na nazwane przyciski.

    Deskryptor i dekoder są PRAWDZIWE — bierzemy preparsed data realnej kolekcji
    Consumer Control z podłączonej klawiatury i przepuszczamy raporty przez
    HidP_GetUsages z hid.dll. Same raporty są syntetyczne, bo nie da się
    programowo wcisnąć klawisza multimedialnego (SendInput idzie ścieżką
    klawiatury, nie HID). Zwraca None, gdy w systemie nie ma takiej kolekcji.
    """
    reg = dt.DeviceRegistry()
    dt.enumerate_devices(reg)
    target = None
    for g in reg.sorted_groups():
        for m in g.all():
            if m.decoder is not None and 0x0C in m.decoder._usage_bufs:
                target = m
                break
        if target:
            break
    if target is None:
        print("  (pomijam zrzut HID — brak kolekcji Consumer Control)")
        return None

    dec = target.decoder
    n = wintypes.USHORT(dec.caps.NumberInputButtonCaps)
    arr = (dt.HIDP_BUTTON_CAPS * n.value)()
    dt.hid.HidP_GetButtonCaps(dt.HIDP_INPUT, arr, ctypes.byref(n), dec._pp)
    rid = arr[0].ReportID
    length = dec.caps.InputReportByteLength

    def report(*usages):
        b = bytearray(length)
        b[0] = rid
        for i, u in enumerate(usages):
            b[1 + i * 2] = u & 0xFF
            b[2 + i * 2] = (u >> 8) & 0xFF
        return bytes(b)

    term = Term(cols, 20)
    out = make_output(term, cols, dedup=False)
    out.segments([(dt.Layout(cols).header(), "head")])

    sekwencja = [
        report(), report(0x00E9), report(0x00E9), report(),
        report(0x00EA), report(),
        report(0x00CD), report(),
        report(0x00B5), report(),
        report(0x00E2), report(),
        report(0x00E9, 0x00CD), report(),
    ]
    layout = dt.Layout(cols)
    render = dt.make_renderer(layout)
    base = time.time()
    for i, rep in enumerate(sekwencja):
        parts = dec.decode(rep, False)
        if not parts:
            continue
        t = base + i * 0.31
        ts = time.strftime("%H:%M:%S", time.localtime(t)) + ".%03d" % int(t % 1 * 1000)
        out.event(dt.Event(ts, 0.0, target, "HID", ", ".join(parts),
                           "%d B" % length, key=(target.handle, "H", i),
                           style="hidchg", ev_type=dt.RIM_TYPEHID), render)
    return term.trim()


def shot_dashboard(cols=98):
    term = Term(cols, 22)
    out = make_output(term, cols)

    def inject(cap):
        for vk in (0x41, 0x53, 0x44, 0x46, 0x47):
            send(key(vk), 0.06)
            send(key(vk, up=True), 0.06)
        for _ in range(8):
            send(move(11, -5), 0.04)

    cfg = dt.Config(verbose=True, color=True, hz=True)
    _run_capture(term, out, cols, cfg, inject, dashboard=True)
    return term


def main():
    if not dt.IS_WINDOWS:
        print("Zrzuty da się wygenerować tylko na Windows.")
        return 1
    print("Generuję zrzuty (wciskaj klawisze tylko jeśli chcesz je zobaczyć w zrzucie):")
    for name, title, fn in (
        ("screenshot-list.png", "device_tester.py --list", shot_list),
        ("screenshot-events.png", "device_tester.py --verbose", shot_events),
        ("screenshot-summary.png",
         "device_tester.py --hz --chatter  (podsumowanie po Ctrl+C)", shot_summary),
        ("screenshot-dashboard.png", "device_tester.py --dashboard --hz", shot_dashboard),
        ("screenshot-hid.png", "device_tester.py  —  dekodowanie raportów HID", shot_hid),
    ):
        term = fn()
        if term is not None:
            term.to_png(os.path.join(OUT_DIR, name), title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
