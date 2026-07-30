# -*- coding: utf-8 -*-
"""Testy czystej logiki: dekodowanie raportów HID, renderowanie, grupowanie, filtry.

Nie wymagają żadnego urządzenia ani (poza kilkoma przypadkami) systemu Windows.

    python -m unittest discover -s tests -v
"""

import ctypes
import json
import re
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import device_tester as dt  # noqa: E402


def read_text(path):
    """Odczyt pliku z jawnym zamknięciem — inaczej testy sypią ResourceWarning."""
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestStructSizes(unittest.TestCase):
    """Zły rozmiar struktury = ciche czytanie śmieci z bufora raw input."""

    EXPECTED_X64 = {
        "RAWINPUTHEADER": 24, "RAWMOUSE": 24, "RAWKEYBOARD": 16, "RAWHID": 12,
        "RAWINPUT": 48, "RID_DEVICE_INFO": 32, "RAWINPUTDEVICE": 16,
        "RAWINPUTDEVICELIST": 16, "RID_DEVICE_INFO_MOUSE": 16,
        "RID_DEVICE_INFO_KEYBOARD": 24, "RID_DEVICE_INFO_HID": 16,
    }

    @unittest.skipUnless(ctypes.sizeof(ctypes.c_void_p) == 8, "rozmiary dla 64-bit")
    def test_sizes(self):
        for name, expect in self.EXPECTED_X64.items():
            with self.subTest(struct=name):
                self.assertEqual(ctypes.sizeof(getattr(dt, name)), expect)

    def test_hid_data_offset_matches_layout(self):
        self.assertEqual(dt.HID_DATA_OFFSET,
                         dt.RAWINPUT.data.offset + dt.RAWHID.bRawData.offset)


class TestDiffReport(unittest.TestCase):
    def setUp(self):
        self.st = {}

    def test_first_report_shows_full(self):
        r = dt.diff_report(self.st, 1, bytes([0x00, 0x01, 0x02]), False)
        self.assertIsNotNone(r)
        self.assertIn("00 01 02", r[0])

    def test_identical_report_is_silent(self):
        dt.diff_report(self.st, 1, bytes([0, 1, 2]), False)
        self.assertIsNone(dt.diff_report(self.st, 1, bytes([0, 1, 2]), False))

    def test_identical_report_shown_in_verbose(self):
        dt.diff_report(self.st, 1, bytes([0, 1, 2]), True)
        self.assertIsNotNone(dt.diff_report(self.st, 1, bytes([0, 1, 2]), True))

    def test_changed_byte_reported(self):
        dt.diff_report(self.st, 1, bytes([0, 1, 2]), False)
        r = dt.diff_report(self.st, 1, bytes([0, 1, 5]), False)
        self.assertEqual(r[0], "[2] 02→05")

    def test_multiple_changed_bytes(self):
        dt.diff_report(self.st, 1, bytes([0, 0x7F, 0x00]), False)
        r = dt.diff_report(self.st, 1, bytes([0, 0xFF, 0x01]), False)
        self.assertEqual(r[0], "[1] 7F→FF [2] 00→01")

    def test_variable_length_report_is_not_swallowed(self):
        """Regresja: urządzenie z raportami różnej długości milkło po pierwszym."""
        dt.diff_report(self.st, 2, bytes(8), False)
        r = dt.diff_report(self.st, 2, bytes(14), False)
        self.assertIsNotNone(r, "raport innej długości musi coś wypisać")

    def test_report_ids_have_separate_state(self):
        """Regresja: przeplot dwóch Report ID dawał fałszywe zmiany wszystkich bajtów."""
        dt.diff_report(self.st, 3, bytes([0x01, 0xAA, 0xBB]), False)
        dt.diff_report(self.st, 3, bytes([0x02, 0x11, 0x22]), False)
        r = dt.diff_report(self.st, 3, bytes([0x01, 0xAA, 0xBC]), False)
        self.assertEqual(r[0], "rid=01 [2] BB→BC")

    def test_report_id_returning_to_same_state_is_silent(self):
        dt.diff_report(self.st, 3, bytes([0x02, 0x11, 0x22]), False)
        dt.diff_report(self.st, 3, bytes([0x01, 0xAA, 0xBB]), False)
        self.assertIsNone(dt.diff_report(self.st, 3, bytes([0x02, 0x11, 0x22]), False))

    def test_a_to_a_transition_is_detected(self):
        """Przycisk wciśnięty i puszczony między raportami innego rid nie może zniknąć."""
        dt.diff_report(self.st, 4, bytes([0x01, 0x00]), False)
        dt.diff_report(self.st, 4, bytes([0x02, 0xFF]), False)
        r = dt.diff_report(self.st, 4, bytes([0x01, 0x01]), False)
        self.assertEqual(r[0], "rid=01 [1] 00→01")

    def test_devices_have_separate_state(self):
        dt.diff_report(self.st, 10, bytes([0, 0xAA]), False)
        r = dt.diff_report(self.st, 11, bytes([0, 0xAA]), False)
        self.assertIsNotNone(r, "inny uchwyt to inne urządzenie")

    def test_empty_report(self):
        self.assertIsNone(dt.diff_report(self.st, 1, b"", False))


class TestUsageNames(unittest.TestCase):
    def test_button_page_is_numbered(self):
        self.assertEqual(dt.usage_name(0x09, 1), "przycisk 1")
        self.assertEqual(dt.usage_name(0x09, 12), "przycisk 12")

    def test_generic_desktop_axes(self):
        self.assertEqual(dt.usage_name(0x01, 0x30), "X")
        self.assertEqual(dt.usage_name(0x01, 0x35), "Rz")
        self.assertEqual(dt.usage_name(0x01, 0x39), "Hat (krzyżak)")

    def test_consumer_media(self):
        self.assertEqual(dt.usage_name(0x0C, 0xE9), "Głośność +")
        self.assertEqual(dt.usage_name(0x0C, 0xCD), "Play/Pauza")

    def test_keyboard_letters_and_digits_are_computed(self):
        self.assertEqual(dt.usage_name(0x07, 0x04), "A")
        self.assertEqual(dt.usage_name(0x07, 0x1D), "Z")
        self.assertEqual(dt.usage_name(0x07, 0x1E), "1")
        self.assertEqual(dt.usage_name(0x07, 0x27), "0")
        self.assertEqual(dt.usage_name(0x07, 0x3A), "F1")
        self.assertEqual(dt.usage_name(0x07, 0xE1), "Lewy Shift")

    def test_vendor_page_is_marked_as_such(self):
        self.assertEqual(dt.usage_name(0xFF02, 0x01), "vendor FF02:0001")

    def test_unknown_usage_still_readable(self):
        self.assertIn("Digitizer", dt.usage_name(0x0D, 0x0999))

    def test_macro_function_keys(self):
        """Klawiatury z blokiem makro mapują dodatkowe klawisze na F13-F24."""
        self.assertEqual(dt.usage_name(0x07, 0x68), "F13")
        self.assertEqual(dt.usage_name(0x07, 0x73), "F24")

    def test_keypad_block(self):
        self.assertEqual(dt.usage_name(0x07, 0x54), "Num /")
        self.assertEqual(dt.usage_name(0x07, 0x58), "Num Enter")
        self.assertEqual(dt.usage_name(0x07, 0x62), "Num 0")

    def test_keypad_range_has_no_gaps(self):
        for u in range(0x54, 0x64):
            self.assertTrue(dt.usage_name(0x07, u).startswith("Num "), hex(u))


class TestHidChangeDescription(unittest.TestCase):
    META = {("val", 0x01, 0x30): (-32768, 32767)}

    def test_button_press_and_release(self):
        self.assertEqual(dt.describe_hid_changes({}, {("btn", 0x09, 3): True}, {}, False),
                         ["przycisk 3 ↓"])
        self.assertEqual(dt.describe_hid_changes({("btn", 0x09, 3): True}, {}, {}, False),
                         ["przycisk 3 ↑"])

    def test_unchanged_state_is_silent(self):
        s = {("btn", 0x09, 1): True, ("val", 0x01, 0x30): 5}
        self.assertEqual(dt.describe_hid_changes(s, dict(s), self.META, False), [])

    def test_axis_noise_is_filtered_by_default(self):
        """Analogi szumią w spoczynku — bez progu każdy pad zalewałby ekran."""
        prev = {("val", 0x01, 0x30): 0}
        cur = {("val", 0x01, 0x30): 300}          # <1/64 zakresu 65535
        self.assertEqual(dt.describe_hid_changes(prev, cur, self.META, False), [])

    def test_real_axis_movement_is_reported(self):
        prev = {("val", 0x01, 0x30): 0}
        cur = {("val", 0x01, 0x30): 20000}
        self.assertEqual(dt.describe_hid_changes(prev, cur, self.META, False), ["X=20000"])

    def test_verbose_reports_every_change(self):
        prev = {("val", 0x01, 0x30): 0}
        cur = {("val", 0x01, 0x30): 3}
        self.assertEqual(dt.describe_hid_changes(prev, cur, self.META, True), ["X=3"])

    def test_slow_ramp_is_visible(self):
        """Regresja: próg porównywany z poprzednią PRÓBKĄ nigdy się nie kumulował,
        więc oś przesuwana wolniej niż span/64 na raport była całkiem niewidoczna —
        odwrotnie do intencji filtra szumu."""
        meta = {("val", 1, 0x30): (0, 65535)}
        for krok in (3, 10, 100):
            prev, shown, n = {("val", 1, 0x30): 0}, {}, 0
            for v in range(0, 65535, krok):
                cur = {("val", 1, 0x30): v}
                if dt.describe_hid_changes(prev, cur, meta, False, shown=shown):
                    n += 1
                prev = cur
            self.assertGreater(n, 50, "krok %d dał tylko %d linii" % (krok, n))
            self.assertLess(n, 80, "krok %d dał aż %d linii" % (krok, n))

    def test_ramp_output_is_independent_of_report_rate(self):
        """Ta sama droga osi ma dać tyle samo linii, niezależnie od tego,
        w ilu raportach ją pokonano."""
        meta = {("val", 1, 0x30): (0, 65535)}
        counts = []
        for krok in (2, 8, 32):
            prev, shown, n = {("val", 1, 0x30): 0}, {}, 0
            for v in range(0, 65535, krok):
                cur = {("val", 1, 0x30): v}
                if dt.describe_hid_changes(prev, cur, meta, False, shown=shown):
                    n += 1
                prev = cur
            counts.append(n)
        self.assertLess(max(counts) - min(counts), 5, str(counts))

    def test_noise_still_filtered_with_shown_base(self):
        """Poprawka nie może przepuścić szumu wokół spoczynku."""
        import random
        meta = {("val", 1, 0x30): (0, 65535)}
        rnd = random.Random(3)
        prev, shown, n = {("val", 1, 0x30): 32768}, {}, 0
        for _ in range(3000):
            cur = {("val", 1, 0x30): 32768 + rnd.randint(-400, 400)}
            if dt.describe_hid_changes(prev, cur, meta, False, shown=shown):
                n += 1
            prev = cur
        self.assertEqual(n, 0)

    def test_hat_switch_release_is_reported(self):
        """Krzyżak z HasNull koduje „środek" wartością spoza zakresu logicznego,
        więc oś znika ze stanu — puszczenie musi być mimo to widoczne."""
        meta = {("val", 0x01, 0x39): (0, 7)}
        parts = dt.describe_hid_changes({("val", 0x01, 0x39): 2}, {}, meta, False)
        self.assertEqual(parts, ["Hat (krzyżak): środek"])

    def test_axis_without_metadata_is_always_reported(self):
        prev = {("val", 0xFF00, 0x01): 0}
        cur = {("val", 0xFF00, 0x01): 1}
        self.assertEqual(len(dt.describe_hid_changes(prev, cur, {}, False)), 1)


@unittest.skipUnless(dt.IS_WINDOWS, "wymaga hid.dll i podłączonego urządzenia")
class TestHidDecoderOnRealDevice(unittest.TestCase):
    """Buduje dekoder z prawdziwych preparsed data i przepuszcza przez HidP_*
    syntetyczny raport. Pomijany, gdy w systemie nie ma kolekcji Consumer Control."""

    @classmethod
    def setUpClass(cls):
        reg = dt.DeviceRegistry()
        dt.enumerate_devices(reg)
        cls.dec = None
        for g in reg.sorted_groups():
            for m in g.all():
                if m.decoder is not None and 0x0C in m.decoder._usage_bufs:
                    cls.dec = m.decoder
                    return

    def setUp(self):
        if self.dec is None:
            self.skipTest("brak urządzenia z kolekcją Consumer Control")
        self.dec._prev.clear()
        import ctypes as c
        from ctypes import wintypes as w
        n = w.USHORT(self.dec.caps.NumberInputButtonCaps)
        arr = (dt.HIDP_BUTTON_CAPS * n.value)()
        dt.hid.HidP_GetButtonCaps(dt.HIDP_INPUT, arr, c.byref(n), self.dec._pp)
        self.rid = arr[0].ReportID
        self.length = self.dec.caps.InputReportByteLength

    def _report(self, *usages):
        b = bytearray(self.length)
        b[0] = self.rid
        for i, u in enumerate(usages):
            b[1 + i * 2] = u & 0xFF
            b[2 + i * 2] = (u >> 8) & 0xFF
        return bytes(b)

    def test_idle_report_is_silent(self):
        self.assertIsNone(self.dec.decode(self._report(), False))

    def test_media_key_is_named_not_hexdumped(self):
        self.dec.decode(self._report(), False)
        self.assertEqual(self.dec.decode(self._report(0x00E9), False), ["Głośność + ↓"])

    def test_held_key_does_not_repeat(self):
        self.dec.decode(self._report(0x00E9), False)
        self.assertIsNone(self.dec.decode(self._report(0x00E9), False))

    def test_release_is_detected(self):
        self.dec.decode(self._report(0x00E9), False)
        self.assertEqual(self.dec.decode(self._report(), False), ["Głośność + ↑"])

    def test_two_keys_at_once(self):
        self.dec.decode(self._report(), False)
        parts = self.dec.decode(self._report(0x00E9, 0x00CD), False)
        self.assertEqual(sorted(parts), sorted(["Głośność + ↓", "Play/Pauza ↓"]))


class TestGrouping(unittest.TestCase):
    @staticmethod
    def _registry(members, product="Urządzenie"):
        paths = {}
        reg = dt.DeviceRegistry(name_fn=lambda h: paths[h],
                                info_fn=lambda h: None,
                                product_fn=lambda p: (product, ""))
        for i, (path, typ) in enumerate(members):
            paths[100 + i] = path
            reg.resolve(100 + i, typ)
        return reg

    def test_mouse_with_keyboard_collection_is_a_mouse(self):
        reg = self._registry([
            (r"\\?\HID#VID_22D4&PID_1503&MI_00#x", dt.RIM_TYPEMOUSE),
            (r"\\?\HID#VID_22D4&PID_1503&MI_01&Col01#x", dt.RIM_TYPEKEYBOARD),
        ])
        g = next(iter(reg.groups.values()))
        self.assertEqual(g.type, dt.RIM_TYPEMOUSE)

    def test_keyboard_with_mouse_collection_is_a_keyboard(self):
        reg = self._registry([
            (r"\\?\HID#VID_1B1C&PID_1B55&MI_00&Col05#x", dt.RIM_TYPEMOUSE),
            (r"\\?\HID#VID_1B1C&PID_1B55&MI_00&Col01#x", dt.RIM_TYPEKEYBOARD),
        ])
        g = next(iter(reg.groups.values()))
        self.assertEqual(g.type, dt.RIM_TYPEKEYBOARD)

    def test_collections_of_one_device_share_a_tag(self):
        reg = self._registry([
            (r"\\?\HID#VID_1B1C&PID_1B55&MI_00&Col01#x", dt.RIM_TYPEKEYBOARD),
            (r"\\?\HID#VID_1B1C&PID_1B55&MI_00&Col02#x", dt.RIM_TYPEHID),
            (r"\\?\HID#VID_1B1C&PID_1B55&MI_01#x", dt.RIM_TYPEHID),
        ])
        self.assertEqual(len(reg.groups), 1)
        g = next(iter(reg.groups.values()))
        self.assertEqual(len(g.members), 3)
        self.assertEqual(g.kinds_text(), "KLAW 2×HID")

    def test_different_devices_get_different_tags(self):
        reg = self._registry([
            (r"\\?\HID#VID_1111&PID_2222&MI_00#x", dt.RIM_TYPEMOUSE),
            (r"\\?\HID#VID_3333&PID_4444&MI_00#x", dt.RIM_TYPEKEYBOARD),
        ])
        tags = {g.tag_text() for g in reg.groups.values()}
        self.assertEqual(tags, {"#1", "#2"})

    def test_forget_removes_member_from_group(self):
        reg = self._registry([
            (r"\\?\HID#VID_1111&PID_2222&MI_00#x", dt.RIM_TYPEMOUSE),
            (r"\\?\HID#VID_1111&PID_2222&MI_01#x", dt.RIM_TYPEHID),
        ])
        g = next(iter(reg.groups.values()))
        reg.forget(101)
        self.assertEqual(len(g.members), 1)
        self.assertFalse(reg.known(101))

    def test_forget_last_member_drops_the_group(self):
        reg = self._registry([(r"\\?\HID#VID_1111&PID_2222&MI_00#x", dt.RIM_TYPEMOUSE)])
        reg.forget(100)
        self.assertEqual(len(reg.groups), 0)

    def test_repeated_resolve_does_not_duplicate_members(self):
        """Regresja: urządzenie bez danych z RIDI_DEVICEINFO było dopisywane do
        grupy przy KAŻDYM zdarzeniu — nieograniczony wzrost pamięci."""
        reg = self._registry([(r"\\?\HID#VID_1111&PID_2222&MI_00#x", dt.RIM_TYPEMOUSE)])
        for _ in range(50):
            reg.resolve(100, dt.RIM_TYPEMOUSE)
        g = next(iter(reg.groups.values()))
        self.assertEqual(len(g.members), 1)

    def test_incomplete_device_stops_retrying(self):
        """Ponawianie odczytu musi być ograniczone — inaczej każde zdarzenie
        urządzenia bez danych kosztuje CreateFileW na gorącej ścieżce."""
        calls = []

        def info_fn(h):
            calls.append(h)
            return None

        reg = dt.DeviceRegistry(name_fn=lambda h: "", info_fn=info_fn,
                                product_fn=lambda p: ("", ""))
        for _ in range(50):
            reg.resolve(5, dt.RIM_TYPEHID)
        self.assertEqual(len(calls), dt.DeviceRegistry.MAX_ATTEMPTS)
        self.assertFalse(reg.resolve(5).complete)

    def test_injected_input_is_complete_immediately(self):
        """Uchwyt 0 = wejście wstrzyknięte (SendInput) — nie ma czego uzupełniać."""
        calls = []
        reg = dt.DeviceRegistry(name_fn=lambda h: "",
                                info_fn=lambda h: calls.append(h) or None,
                                product_fn=lambda p: ("", ""))
        for _ in range(10):
            reg.resolve(0, dt.RIM_TYPEKEYBOARD)
        self.assertEqual(len(calls), 1)

    def test_device_moves_group_when_vid_pid_appears_later(self):
        """Po udanym ponowieniu urządzenie musi przejść z grupy zastępczej do właściwej."""
        state = {"path": "", "info": None}
        reg = dt.DeviceRegistry(name_fn=lambda h: state["path"],
                                info_fn=lambda h: state["info"],
                                product_fn=lambda p: ("", ""))
        di = reg.resolve(9, dt.RIM_TYPEHID)
        self.assertEqual(di.group_key(), "handle:9")
        state["path"] = r"\\?\HID#VID_ABCD&PID_1234&MI_00#x"
        reg.resolve(9, dt.RIM_TYPEHID)
        self.assertEqual(di.group_key(), "ABCD:1234")
        self.assertEqual(len(reg.groups), 1)
        self.assertEqual(list(reg.groups)[0], "ABCD:1234")

    def test_vid_pid_read_from_path_for_non_hid_types(self):
        """RID_DEVICE_INFO podaje VID/PID tylko dla RIM_TYPEHID."""
        reg = self._registry([(r"\\?\HID#VID_046D&PID_C52B&MI_00#x", dt.RIM_TYPEKEYBOARD)])
        di = reg.resolve(100)
        self.assertEqual((di.vid, di.pid), (0x046D, 0xC52B))
        self.assertEqual(di.group_key(), "046D:C52B")

    def test_bluetooth_path_yields_vid_pid(self):
        """Ścieżka BT ma inny format niż USB — bez tego jedna klawiatura BT
        rozpadała się na kilka „urządzeń" z osobnymi tagami."""
        bt = (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}"
              r"_VID&0002045e_PID&0b13&Col01#9&abc&0&0000")
        self.assertEqual(dt.parse_vid_pid(bt), (0x045E, 0x0B13))

    def test_bluetooth_collections_share_one_group(self):
        reg = self._registry([
            (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}"
             r"_VID&0002045e_PID&0b13&Col01#9&a&0&0000", dt.RIM_TYPEKEYBOARD),
            (r"\\?\HID#{00001812-0000-1000-8000-00805f9b34fb}"
             r"_VID&0002045e_PID&0b13&Col02#9&a&0&0001", dt.RIM_TYPEHID),
        ])
        self.assertEqual(len(reg.groups), 1)
        self.assertEqual(list(reg.groups)[0], "045E:0B13")

    def test_usb_path_still_wins(self):
        self.assertEqual(dt.parse_vid_pid(r"\\?\HID#VID_1B1C&PID_1B55&MI_00#x"),
                         (0x1B1C, 0x1B55))
        self.assertEqual(dt.parse_vid_pid(r"\\?\ACPI#PNP0303#4&x&0"), (None, None))

    def test_parse_collection(self):
        self.assertEqual(dt.parse_collection(r"\\?\HID#VID_1&PID_2&MI_01&Col05#x"), (1, 5))
        self.assertEqual(dt.parse_collection(r"\\?\HID#VID_1&PID_2&MI_02#x"), (2, 0))
        self.assertEqual(dt.parse_collection(r"\\?\HID#VID_1&PID_2#x"), (0, 0))
        self.assertEqual(dt.parse_collection(""), (0, 0))

    def test_device_style_is_stable_across_processes(self):
        """crc32, nie hash() — hash() stringów jest solony per proces."""
        self.assertEqual(dt.device_style("1B1C:1B55"), dt.device_style("1B1C:1B55"))
        self.assertEqual(dt.device_style("1B1C:1B55"), "\x1b[38;5;%dm" % dt.DEVICE_COLORS[
            __import__("zlib").crc32(b"1B1C:1B55") % len(dt.DEVICE_COLORS)])


class TestRendering(unittest.TestCase):
    def setUp(self):
        self.layout = dt.Layout(100)
        self.render = dt.make_renderer(self.layout)
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1B1C&PID_1B55#x",
                                info_fn=lambda h: None,
                                product_fn=lambda p: ("Testowa Klawiatura", "ACME"))
        self.di = reg.resolve(7, dt.RIM_TYPEKEYBOARD)
        self.ev = dt.Event("10:00:00.000", 0.0, self.di, "KLAW", "A ↓",
                           "VK 41 sc 1E", key=("k",), style="down")

    def test_plain_has_no_escape_sequences(self):
        """Kolory NIE MOGĄ trafić do pliku logu."""
        self.assertNotIn("\x1b", dt.render_plain(self.render(self.ev, 1)))

    def test_ansi_has_escape_sequences(self):
        self.assertIn("\x1b", dt.render_ansi(self.render(self.ev, 1)))

    def test_ansi_and_plain_carry_the_same_text(self):
        plain = dt.render_plain(self.render(self.ev, 1))
        ansi = dt.render_ansi(self.render(self.ev, 1))
        import re
        self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", ansi), plain)

    def test_repeat_marker_keeps_column_width(self):
        one = dt.render_plain(self.render(self.ev, 1))
        many = dt.render_plain(self.render(self.ev, 17))
        self.assertEqual(len(one), len(many))
        self.assertIn("×17", many)

    def test_typical_line_fits_the_console(self):
        for width in (60, 80, 100, 120, 200):
            layout = dt.Layout(width)
            render = dt.make_renderer(layout)
            ev = dt.Event("10:00:00.000", 0.0, self.di, "KLAW", "Prawy Ctrl ↑",
                          "VK 11 sc 1D E0 · 12345 ms", ("k",), "up")
            for count in (1, 17):
                line = dt.render_plain(dt.fit_segments(render(ev, count), width - 1))
                self.assertLess(len(line), width,
                                "szerokość %d, count %d: %r" % (width, count, line))

    def test_hid_report_is_never_truncated(self):
        """Pełny raport HID to podstawowa informacja narzędzia — kolumna treści
        może się rozlać, ale nie wolno jej obciąć."""
        report = " ".join("%02X" % b for b in range(64))
        for width in (40, 80, 100):
            render = dt.make_renderer(dt.Layout(width))
            ev = dt.Event("10:00:00.000", 0.0, self.di, "HID", report, "64 B", ("h",), "hid")
            self.assertIn(report, dt.render_plain(render(ev, 1)),
                          "szerokość %d" % width)

    def test_long_key_name_is_never_truncated(self):
        render = dt.make_renderer(dt.Layout(80))
        ev = dt.Event("10:00:00.000", 0.0, self.di, "KLAW", "Przeglądarka Odśwież ↓",
                      "VK A8 sc 67 E0", ("k",), "down")
        self.assertIn("Przeglądarka Odśwież ↓", dt.render_plain(render(ev, 1)))

    def test_line_contains_tag_and_content(self):
        line = dt.render_plain(self.render(self.ev, 1))
        self.assertIn("#1", line)
        self.assertIn("A ↓", line)
        self.assertIn("10:00:00.000", line)

    def test_mouse_move_merge_sums_deltas(self):
        a = dt.Event("t", 0.0, self.di, "MYSZ", "ruch dx=+1 dy=+0", "", ("m",), "mouse",
                     1, 0, "sum")
        b = dt.Event("t", 0.1, self.di, "MYSZ", "ruch dx=+2 dy=-1", "", ("m",), "mouse",
                     2, -1, "sum")
        self.assertEqual(dt.merge_events(a, b).body, "ruch dx=+3 dy=-1")

    def test_absolute_position_merge_shows_latest(self):
        """Ekran dotykowy / tablet / RDP: linia musi pokazywać BIEŻĄCĄ pozycję,
        inaczej ×N zamraża ją na pierwszym odczycie."""
        a = dt.Event("t", 0.0, self.di, "MYSZ", "pozycja x=100 y=100", "ekran",
                     ("ma",), "mouse", merge_mode="latest")
        b = dt.Event("t", 0.1, self.di, "MYSZ", "pozycja x=900 y=400", "ekran",
                     ("ma",), "mouse", merge_mode="latest")
        merged = dt.merge_events(a, b)
        self.assertEqual(merged.body, "pozycja x=900 y=400")
        self.assertEqual(merged.ts, a.ts, "znacznik czasu zostaje z początku serii")

    def test_non_move_merge_keeps_original(self):
        self.assertIs(dt.merge_events(self.ev, self.ev), self.ev)

    def test_trunc_and_pad(self):
        self.assertEqual(dt.trunc("abcdefgh", 4), "abc…")
        self.assertEqual(dt.trunc("ab", 4), "ab")
        self.assertEqual(dt.trunc("abc", 0), "")
        self.assertEqual(len(dt.pad("abcdefgh", 4)), 4)
        self.assertEqual(len(dt.pad("ab", 6)), 6)


class _CapturingOutput(dt.Output):
    def __init__(self):
        dt.Output.__init__(self)
        self.lines = []

    def segments(self, segs):
        self.lines.append(dt.render_plain(segs))

    def raw(self, line=""):
        self.lines.append(line)


class TestBox(unittest.TestCase):
    def test_every_edge_has_the_same_width(self):
        for w in (40, 60, 96):
            out = _CapturingOutput()
            box = dt.Box(out, w)
            box.top("TYTUŁ")
            box.row("treść")
            box.sep("SEKCJA")
            box.row("x" * 500)
            box.bottom()
            self.assertEqual({len(x) for x in out.lines}, {w}, "szerokość %d" % w)

    def test_rows_are_closed(self):
        out = _CapturingOutput()
        box = dt.Box(out, 50)
        box.top("T")
        box.row("a")
        box.bottom()
        for line in out.lines:
            self.assertIn(line[0], "┌├│└")
            self.assertIn(line[-1], "┐┤│┘")


class TestAxisBar(unittest.TestCase):
    def test_width_is_constant(self):
        for v in (-32768, -1, 0, 1, 32767):
            self.assertEqual(len(dt.axis_bar(v, -32768, 32767, 17)), 19)  # +2 ramki

    def test_marker_position_follows_value(self):
        self.assertTrue(dt.axis_bar(-32768, -32768, 32767)[1] == "█")
        self.assertTrue(dt.axis_bar(32767, -32768, 32767)[-2] == "█")
        mid = dt.axis_bar(0, -32768, 32767)
        self.assertEqual(mid.index("█"), 1 + (17 - 1) // 2)

    def test_centre_mark_present_when_not_covered(self):
        self.assertIn("┼", dt.axis_bar(-32768, -32768, 32767))

    def test_trigger_range_starts_at_left(self):
        self.assertEqual(dt.axis_bar(0, 0, 255).index("█"), 1)
        self.assertEqual(dt.axis_bar(255, 0, 255)[-2], "█")

    def test_degenerate_range_does_not_crash(self):
        self.assertEqual(len(dt.axis_bar(5, 10, 10)), 19)
        self.assertEqual(len(dt.axis_bar(5, 10, 0)), 19)

    def test_value_outside_range_is_clamped(self):
        self.assertEqual(dt.axis_bar(999999, 0, 255)[-2], "█")
        self.assertEqual(dt.axis_bar(-999999, 0, 255)[1], "█")


class TestAnalogTracker(unittest.TestCase):
    META = {("val", 0x01, 0x30): (-32768, 32767)}
    AXIS = ("val", 0x01, 0x30)

    def setUp(self):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_045E&PID_02EA#x",
                                info_fn=lambda h: None,
                                product_fn=lambda p: ("Pad", ""))
        self.di = reg.resolve(1, dt.RIM_TYPEHID)
        self.tr = dt.AnalogTracker()

    def _feed(self, *values):
        for v in values:
            self.tr.feed(self.di, {self.AXIS: v}, self.META)

    def test_range_is_tracked(self):
        self._feed(0, -30000, 12000, 31000, 0)
        a = list(self.tr.axes.values())[0]
        self.assertEqual((a["min"], a["max"]), (-30000, 31000))
        self.assertEqual(a["cur"], 0)

    KEY = None

    def _band(self):
        return self.tr.rest_band(list(self.tr.axes)[0])

    def test_rest_band_needs_consecutive_quiet_samples(self):
        self._feed(0, 0, 0)                       # za mało próbek
        self.assertIsNone(self._band())
        self._feed(0, 0, 0)
        self.assertIsNotNone(self._band())

    def test_drifting_axis_widens_the_rest_band(self):
        self._feed(*([0] * 6))
        self._feed(*[i for i in range(1, 40)])     # powolny dryf, każdy krok mały
        mn, mx, _ = self._band()
        self.assertGreater(mx - mn, 10)

    def test_fast_movement_resets_quiescence(self):
        self._feed(*([0] * 6))
        before = self._band()
        self._feed(30000)                          # skok — nie jest spoczynkiem
        self.assertEqual(self._band(), before)
        self.assertEqual(list(self.tr.axes.values())[0]["still"], 0)

    def test_two_stable_positions_do_not_merge_into_one_band(self):
        """Spust bywa nieruchomy i puszczony, i wciśnięty do oporu. Rozciągnięcie
        pasa spoczynku na oba dałoby martwą strefę 100% i zero informacji."""
        meta = {("val", 0x01, 0x32): (0, 255)}
        axis = ("val", 0x01, 0x32)
        for v in [0] * 30 + [255] * 8 + [0] * 30:
            self.tr.feed(self.di, {axis: v}, meta)
        mn, mx, share = self.tr.rest_band(list(self.tr.axes)[0])
        self.assertEqual((mn, mx), (0, 0), "dominuje pozycja puszczona")
        self.assertGreater(share, 80)

    def test_share_reflects_time_spent_at_rest(self):
        meta = {("val", 0x01, 0x32): (0, 255)}
        axis = ("val", 0x01, 0x32)
        for v in [0] * 20 + [255] * 20:
            self.tr.feed(self.di, {axis: v}, meta)
        mn, mx, share = self.tr.rest_band(list(self.tr.axes)[0])
        self.assertLess(share, 70, "dwa równie częste położenia = brak dominacji")

    def test_report_mentions_range_and_dead_zone(self):
        self._feed(*([0] * 6))
        self._feed(-32000, 32000)
        rows = self.tr.report()
        self.assertEqual(len(rows), 1)
        tag, name, reach, rest = rows[0]
        self.assertEqual(name, "X")
        self.assertIn("zasięg -32000..32000", reach)
        self.assertIn("martwa strefa", rest)

    def test_report_without_quiet_samples(self):
        self._feed(0, 30000)
        self.assertIn("brak pomiaru", self.tr.report()[0][3])

    def test_axes_are_tracked_separately(self):
        self.tr.feed(self.di, {("val", 0x01, 0x30): 1, ("val", 0x01, 0x31): 2}, {})
        self.assertEqual(len(self.tr.axes), 2)

    def test_buttons_are_ignored(self):
        self.tr.feed(self.di, {("btn", 0x09, 1): True}, {})
        self.assertEqual(self.tr.axes, {})


class TestHidBars(unittest.TestCase):
    META = {("val", 0x01, 0x30): (-32768, 32767)}

    def test_bars_disabled_by_default(self):
        parts = dt.describe_hid_changes({("val", 0x01, 0x30): 0},
                                        {("val", 0x01, 0x30): 20000}, self.META, False)
        self.assertEqual(parts, ["X=20000"])

    def test_bars_enabled(self):
        parts = dt.describe_hid_changes({("val", 0x01, 0x30): 0},
                                        {("val", 0x01, 0x30): 20000}, self.META, False,
                                        bars=True)
        self.assertIn("█", parts[0])
        self.assertTrue(parts[0].startswith("X "))
        self.assertTrue(parts[0].endswith("20000"))

    def test_bars_skipped_without_known_range(self):
        parts = dt.describe_hid_changes({("val", 0xFF00, 1): 0},
                                        {("val", 0xFF00, 1): 5}, {}, False, bars=True)
        self.assertEqual(parts, ["vendor FF00:0001=5"])


class TestMouseStats(unittest.TestCase):
    """Statystyki nie mogą zależeć od tego, co akurat jest wyświetlane."""

    class _Btn:
        def __init__(self, flags=0, data=0):
            self.usButtonFlags, self.usButtonData = flags, data

    class _MS:
        def __init__(self, dx=0, dy=0, flags=0, usflags=0, data=0):
            self.lLastX, self.lLastY, self.usFlags = dx, dy, usflags
            self.btn = TestMouseStats._Btn(flags, data)

    def setUp(self):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("M", ""))
        self.di = reg.resolve(1, dt.RIM_TYPEMOUSE)
        self.st = dt.Stats()

    def _decode(self, ms, verbose=False):
        return dt.decode_mouse(self.di, ms, "t", 0.0, verbose, self.st)

    def test_distance_counted_without_verbose(self):
        """Regresja: dystans liczył się tylko w --verbose, więc podsumowanie
        po zwykłej sesji nigdy go nie pokazywało."""
        self._decode(self._MS(dx=3, dy=4))
        self.assertAlmostEqual(self.st.distance, 5.0)

    def test_distance_counted_when_packet_also_carries_a_button(self):
        """Regresja: pakiet z przyciskiem zwracał tylko zdarzenie przycisku,
        a przesunięcie z tego samego pakietu przepadało."""
        self._decode(self._MS(dx=3, dy=4, flags=dt.RI_MOUSE_LEFT_BUTTON_DOWN))
        self.assertAlmostEqual(self.st.distance, 5.0)

    def test_absolute_coordinates_are_not_distance(self):
        """Ekran dotykowy raportuje POZYCJĘ 0..65535, nie przesunięcie."""
        self._decode(self._MS(dx=32000, dy=41000, usflags=dt.MOUSE_MOVE_ABSOLUTE))
        self.assertEqual(self.st.distance, 0.0)

    def test_button_event_still_returned(self):
        ev = self._decode(self._MS(dx=1, dy=1, flags=dt.RI_MOUSE_LEFT_BUTTON_DOWN))
        self.assertIn("LPM", ev.body)

    def test_movement_event_only_in_verbose(self):
        self.assertIsNone(self._decode(self._MS(dx=3, dy=4)))
        self.assertIsNotNone(self._decode(self._MS(dx=3, dy=4), verbose=True))


class TestRateMeter(unittest.TestCase):
    def test_window_not_closed_early(self):
        m = dt.RateMeter()
        m.add(1, 0.0)
        self.assertFalse(m.due(1, 0.5))
        self.assertTrue(m.due(1, 1.0))

    def test_average_over_window(self):
        m = dt.RateMeter()
        for i in range(1000):
            m.add(1, i / 1000.0)
        hz, n, span = m.take(1, 1.0)
        self.assertEqual(n, 1000)
        self.assertAlmostEqual(hz, 1000.0, delta=1.0)

    def test_peak_is_tracked(self):
        m = dt.RateMeter()
        for i in range(100):
            m.add(1, i / 1000.0)
        m.take(1, 0.1)                       # 1000 Hz
        for i in range(50):
            m.add(1, 0.1 + i / 1000.0)
        m.take(1, 1.1)                       # 50 Hz
        self.assertAlmostEqual(m.peak[1], 1000.0, delta=10.0)
        self.assertAlmostEqual(m.last[1], 50.0, delta=1.0)

    def test_devices_are_measured_separately(self):
        m = dt.RateMeter()
        for i in range(10):
            m.add(1, i / 100.0)
        m.add(2, 0.0)
        self.assertEqual(m.take(1, 1.0)[1], 10)
        self.assertEqual(m.take(2, 1.0)[1], 1)

    def test_empty_window_returns_none(self):
        m = dt.RateMeter()
        m.add(1, 0.0)
        m.take(1, 1.0)
        self.assertIsNone(m.take(1, 2.0))

    def test_long_tail_window_is_rejected(self):
        """Regresja: ogon po Ctrl+C wliczany z pełną wagą pokazywał „10 Hz"
        dla urządzenia raportującego 1000 Hz."""
        m = dt.RateMeter()
        for i in range(50):
            m.add(1, i / 1000.0)
        self.assertIsNone(m.take(1, 5.0, partial=True))

    def test_short_tail_window_is_rejected(self):
        m = dt.RateMeter()
        m.add(1, 0.0)
        m.add(1, 0.01)
        self.assertIsNone(m.take(1, 0.05, partial=True))

    def test_reasonable_tail_window_is_accepted(self):
        m = dt.RateMeter()
        for i in range(300):
            m.add(1, i / 1000.0)
        res = m.take(1, 0.4, partial=True)
        self.assertIsNotNone(res)
        self.assertAlmostEqual(res[0], 750.0, delta=10.0)

    def test_partial_window_never_sets_peak(self):
        m = dt.RateMeter()
        for i in range(300):
            m.add(1, i / 1000.0)
        m.take(1, 0.4, partial=True)
        self.assertEqual(m.peak.get(1, 0.0), 0.0)

    def test_forget_drops_all_state(self):
        m = dt.RateMeter()
        for i in range(10):
            m.add(1, i / 100.0)
        m.take(1, 1.0)
        m.forget(1)
        self.assertNotIn(1, m.pending())
        self.assertNotIn(1, m.peak)
        self.assertNotIn(1, m.last)


class TestHzSummary(unittest.TestCase):
    """Hz zbierane per kolekcja, prezentowane per urządzenie."""

    def setUp(self):
        self.reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_22D4&PID_1503#x",
                                     info_fn=lambda h: None,
                                     product_fn=lambda p: ("Mysz", ""))
        self.fast = self.reg.resolve(1, dt.RIM_TYPEMOUSE)     # sensor 1000 Hz
        self.slow = self.reg.resolve(2, dt.RIM_TYPEHID)       # kolekcja makr 2 Hz

    def _render(self, st):
        out = _CapturingOutput()
        st.render(out, dt.Layout(90))
        return "\n".join(out.lines)

    def test_weighted_average_not_mean_of_windows(self):
        """Okno 60 s z dwoma raportami nie może ważyć tyle co 1 s z tysiącem."""
        st = dt.Stats()
        st.rate(self.fast, 1000.0, 1000, 1.0)
        st.rate(self.fast, 2.0, 2, 60.0)
        self.assertIn("16 Hz", self._render(st))          # 1002/61, nie (1000+2)/2

    def test_fastest_collection_wins(self):
        """Regresja: mysz 1000 Hz raportowała 2 Hz, bo wygrywała kolejność
        enumeracji, a nie faktyczna częstotliwość."""
        st = dt.Stats()
        st.rate(self.slow, 2.0, 2, 1.0)
        st.rate(self.fast, 1000.0, 1000, 1.0)
        text = self._render(st)
        self.assertIn("1000 Hz", text)
        self.assertIn("najszybszą kolekcję", text)

    def test_single_collection_has_no_footnote(self):
        st = dt.Stats()
        st.rate(self.fast, 1000.0, 1000, 1.0)
        self.assertNotIn("najszybszą kolekcję", self._render(st))


class TestChatterDetector(unittest.TestCase):
    def test_first_press_is_never_chatter(self):
        d = dt.ChatterDetector(30.0)
        self.assertIsNone(d.press(("m", "LPM"), 1.0))

    def test_healthy_click_is_not_flagged(self):
        d = dt.ChatterDetector(30.0)
        d.release(("m", "LPM"), 1.0)
        self.assertIsNone(d.press(("m", "LPM"), 1.2))
        self.assertEqual(d.hits, [])

    def test_bounce_is_flagged(self):
        d = dt.ChatterDetector(30.0)
        d.release(("m", "LPM"), 1.0)
        gap = d.press(("m", "LPM"), 1.004, "LPM")
        self.assertIsNotNone(gap)
        self.assertAlmostEqual(gap, 4.0, delta=0.01)
        self.assertEqual(d.hits[0][0], "LPM")

    def test_threshold_is_respected(self):
        d = dt.ChatterDetector(5.0)
        d.release(("m", "LPM"), 1.0)
        self.assertIsNone(d.press(("m", "LPM"), 1.010))

    def test_controls_are_independent(self):
        d = dt.ChatterDetector(30.0)
        d.release(("m", "LPM"), 1.0)
        self.assertIsNone(d.press(("m", "PPM"), 1.001))


class TestKeyboardChatter(unittest.TestCase):
    class _KB:
        def __init__(self, vk, scan, flags=0):
            self.VKey, self.MakeCode, self.Flags = vk, scan, flags

    def setUp(self):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("X", ""))
        self.di = reg.resolve(1, dt.RIM_TYPEKEYBOARD)
        self.hold = {}
        self.det = dt.ChatterDetector(30.0)

    def _ev(self, flags=0, mono=0.0):
        return dt.decode_keyboard(self.di, self._KB(0x41, 0x1E, flags), "t", mono,
                                  self.hold, self.det)

    def test_bounce_marks_the_event(self):
        self._ev(0, 1.00)
        self._ev(dt.RI_KEY_BREAK, 1.005)
        ev = self._ev(0, 1.009)
        self.assertIn("DRGANIE", ev.meta)
        self.assertEqual(ev.style, "warn")

    def test_autorepeat_is_not_chatter(self):
        """Autorepeat to powtórzone MAKE bez BREAK — nie jest nowym wciśnięciem."""
        self._ev(0, 1.0)
        for i in range(5):
            ev = self._ev(0, 1.03 + i * 0.03)
            self.assertNotIn("DRGANIE", ev.meta)
        self.assertEqual(self.det.hits, [])

    def test_normal_typing_is_clean(self):
        for i in range(5):
            self._ev(0, i * 0.2)
            self._ev(dt.RI_KEY_BREAK, i * 0.2 + 0.05)
        self.assertEqual(self.det.hits, [])


class TestFilters(unittest.TestCase):
    def setUp(self):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1B1C&PID_1B55#x",
                                info_fn=lambda h: None,
                                product_fn=lambda p: ("Testowa Klawiatura", "ACME"))
        self.di = reg.resolve(7, dt.RIM_TYPEKEYBOARD)

    def test_inactive_filter_accepts_everything(self):
        f = dt.Filters()
        self.assertFalse(f.active)
        self.assertTrue(f.accept(self.di))

    def test_only_type(self):
        self.assertTrue(dt.Filters(only=["keyboard"]).accept(self.di))
        self.assertFalse(dt.Filters(only=["mouse"]).accept(self.di))
        self.assertTrue(dt.Filters(only=["klawiatura"]).accept(self.di))

    def test_match_by_vid_pid(self):
        self.assertTrue(dt.Filters(include=["1B1C:1B55"]).accept(self.di))
        self.assertFalse(dt.Filters(include=["DEAD:BEEF"]).accept(self.di))

    def test_match_by_tag(self):
        self.assertTrue(dt.Filters(include=["#1"]).accept(self.di))
        self.assertFalse(dt.Filters(include=["#9"]).accept(self.di))

    def test_match_by_name_fragment(self):
        self.assertTrue(dt.Filters(include=["testowa"]).accept(self.di))
        self.assertTrue(dt.Filters(include=["ACME"]).accept(self.di))

    def test_exclude_wins_over_include(self):
        self.assertFalse(dt.Filters(include=["testowa"], exclude=["#1"]).accept(self.di))

    def test_unknown_type_alias_is_ignored(self):
        self.assertFalse(dt.Filters(only=["bzdura"]).active)


class TestKeyNames(unittest.TestCase):
    def test_e1_pause_is_not_ctrl(self):
        """Regresja: Pause (E1 1D) był nazywany „Ctrl", bo prefiks E1 był ignorowany."""
        self.assertEqual(dt.key_name(dt.VK_PAUSE, 0x1D, False, True), "Pause")

    def test_unknown_vk_falls_back_to_hex(self):
        self.assertEqual(dt.key_name(0x00, 0x00, False, True), "VK_0x00")

    def test_media_keys_have_polish_names(self):
        self.assertEqual(dt.key_name(0xAF, 0x00, False, True), "Głośność +")

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga GetKeyNameTextW")
    def test_media_keys_are_not_named_after_letters(self):
        """Regresja: klawisze multimedialne dzielą scancode z literami (VK AF /
        scan 30 to fizycznie ta sama pozycja co „B"), a GetKeyNameTextW patrzy
        tylko na scancode — zwracał więc „B" zamiast „Głośność +"."""
        for vk, scan, expected in ((0xAF, 0x30, "Głośność +"), (0xAE, 0x2E, "Głośność −"),
                                   (0xAD, 0x20, "Wycisz"), (0xB3, 0x22, "Play/Pauza"),
                                   (0xB0, 0x19, "Następny utwór")):
            for e0 in (True, False):
                self.assertEqual(dt.key_name(vk, scan, e0), expected)

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga GetKeyNameTextW")
    def test_letter_keys_still_use_the_system_name(self):
        """Poprawka dla mediów nie może przejąć zwykłych klawiszy."""
        self.assertEqual(dt.key_name(0x41, 0x1E, False), "A")
        self.assertEqual(dt.key_name(0x42, 0x30, False), "B")

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga MapVirtualKeyW")
    def test_injected_navigation_keys_are_not_numpad(self):
        """SendInput daje scancode 0, a klaster nawigacyjny dzieli scancody
        z klawiaturą numeryczną — bez flagi E0 strzałka nazywała się „Num 6"."""
        for vk, wrong in ((0x27, "Num 6"), (0x25, "Num 4"), (0x24, "Num 7"),
                          (0x28, "Num 2"), (0x2E, "Num Del")):
            name = dt.key_name(vk, 0, False)
            self.assertNotEqual(name, wrong, "VK %02X" % vk)
            self.assertNotIn("Num", name, "VK %02X → %s" % (vk, name))

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga MapVirtualKeyW")
    def test_injected_numpad_keys_stay_numpad(self):
        """Poprawka dla klastra nawigacyjnego nie może przejąć klawiatury numerycznej."""
        self.assertIn("Num", dt.key_name(0x60, 0, False))     # NumPad 0
        self.assertIn("Num", dt.key_name(0x6F, 0, False))     # NumPad /

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga GetKeyNameTextW")
    def test_hardware_scancode_is_never_overridden(self):
        """Wyprowadzanie scancode dotyczy wyłącznie wejścia wstrzykniętego."""
        self.assertNotEqual(dt.key_name(0x27, 0x4D, True), dt.key_name(0x27, 0x4D, False))

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga GetKeyNameTextW")
    def test_numlock_is_not_named_pause(self):
        """Regresja: scancode 0x45 bez bitu rozszerzonego Windows nazywa „Pause"."""
        self.assertNotEqual(dt.key_name(dt.VK_NUMLOCK, 0x45, False, False), "Pause")

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga GetKeyNameTextW")
    def test_left_and_right_shift_are_distinguishable(self):
        """Regresja: bit 25 lparam scalał lewy i prawy modyfikator w jedną nazwę."""
        self.assertNotEqual(dt.key_name(0x10, 0x2A, False, False),
                            dt.key_name(0x10, 0x36, False, False))

    @unittest.skipUnless(dt.IS_WINDOWS, "wymaga GetKeyNameTextW")
    def test_enter_and_numpad_enter_are_distinguishable(self):
        self.assertNotEqual(dt.key_name(0x0D, 0x1C, False, False),
                            dt.key_name(0x0D, 0x1C, True, False))


class TestKeyboardStats(unittest.TestCase):
    """Statystyki muszą pochodzić z pól zdarzenia, nie z parsowania tekstu,
    który dekoder właśnie sformatował do wyświetlenia."""

    class _KB:
        def __init__(self, vk, scan, flags=0):
            self.VKey, self.MakeCode, self.Flags = vk, scan, flags

    def setUp(self):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("X", ""))
        self.di = reg.resolve(1, dt.RIM_TYPEKEYBOARD)
        self.hold = {}

    def _ev(self, vk, scan, flags=0, mono=0.0):
        return dt.decode_keyboard(self.di, self._KB(vk, scan, flags), "t", mono, self.hold)

    def test_multiword_key_name_is_not_split(self):
        """Regresja: nazwa cięta na pierwszej spacji scalała Right Shift,
        Right Control i Right Alt w jeden licznik „Right"."""
        ev = self._ev(0x10, 0x36)
        self.assertIsNotNone(ev.kname)
        self.assertNotEqual(ev.kname, "Right")
        self.assertIn(" ", ev.kname + " ")   # nazwa w całości, nie pierwszy wyraz

    def test_arrow_key_release_is_not_counted_as_press(self):
        """Regresja: klawisz o nazwie „↓" zawierał znak ↓ także przy zwolnieniu,
        więc heurystyka tekstowa liczyła zwolnienie jako wciśnięcie."""
        down = self._ev(0x28, 0x50, mono=1.0)
        up = self._ev(0x28, 0x50, dt.RI_KEY_BREAK, mono=1.05)
        self.assertFalse(down.is_break)
        self.assertTrue(up.is_break)
        self.assertEqual(up.hold_ms, 50)

    def test_hold_time_is_structured_not_parsed(self):
        self._ev(0x41, 0x1E, 0, mono=10.0)
        up = self._ev(0x41, 0x1E, dt.RI_KEY_BREAK, mono=10.25)
        self.assertEqual(up.hold_ms, 250)
        self.assertIn("250 ms", up.meta)

    def test_press_has_no_hold_time(self):
        self.assertIsNone(self._ev(0x41, 0x1E).hold_ms)

    def test_stats_counters_use_full_names(self):
        s = dt.Stats()
        for vk, scan in ((0x10, 0x36), (0x10, 0x36), (0x41, 0x1E)):
            ev = self._ev(vk, scan)
            s.key_press(ev.kname)
        self.assertEqual(len(s.keys), 2)
        self.assertEqual(max(s.keys.values()), 2)


class TestStats(unittest.TestCase):
    def test_summary_renders_without_events(self):
        out = _CapturingOutput()
        dt.Stats().render(out, dt.Layout(80))
        self.assertTrue(any("PODSUMOWANIE" in x for x in out.lines))

    def test_counts_per_device(self):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None,
                                product_fn=lambda p: ("X", ""))
        di = reg.resolve(1, dt.RIM_TYPEKEYBOARD)
        s = dt.Stats()
        for _ in range(3):
            s.event(dt.Event("t", 0.0, di, "KLAW", "A ↓", "", ("k",), "down"))
        self.assertEqual(s.total, 3)
        self.assertEqual(list(s.per_device.values())[0]["n"], 3)


class TestDedup(unittest.TestCase):
    """Powtórzenia są scalane W MIEJSCU na konsoli, ale log dostaje każdą linię
    osobno — indywidualne znaczniki czasu powtórzeń to dane diagnostyczne."""

    def setUp(self):
        self.writes = []
        self.out = dt.Output(color=True, dedup=True, width=100)
        self.out.dedup = True                    # wymuś: w testach stdout nie jest konsolą
        self.out._write = self.writes.append
        self.render = dt.make_renderer(dt.Layout(100))
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("X", ""))
        self.di = reg.resolve(1, dt.RIM_TYPEKEYBOARD)

    def _ev(self, key=("k",), body="A ↓", dx=0, dy=0, kind="KLAW", merge_mode=None):
        return dt.Event("10:00:00.000", 0.0, self.di, kind, body, "m", key, "down",
                        dx, dy, merge_mode)

    def test_first_event_is_not_a_rewrite(self):
        self.out.event(self._ev(), self.render)
        self.assertNotIn("\x1b[1A", self.writes[0])

    def test_repeat_rewrites_previous_line(self):
        for _ in range(3):
            self.out.event(self._ev(), self.render)
        self.assertEqual(len(self.writes), 3)
        self.assertIn("\x1b[1A", self.writes[1])
        self.assertIn("×2", self.writes[1])
        self.assertIn("×3", self.writes[2])

    def test_different_key_starts_a_new_line(self):
        self.out.event(self._ev(key=("a",)), self.render)
        self.out.event(self._ev(key=("b",), body="B ↓"), self.render)
        self.assertNotIn("\x1b[1A", self.writes[1])

    def test_banner_resets_merging(self):
        self.out.event(self._ev(), self.render)
        self.out.raw("--- baner ---")
        self.out.event(self._ev(), self.render)
        self.assertNotIn("\x1b[1A", self.writes[-1])

    def test_mouse_move_repeats_accumulate_deltas(self):
        for _ in range(4):
            self.out.event(self._ev(key=("mv",), body="ruch dx=+1 dy=+0",
                                    dx=1, dy=0, kind="MYSZ", merge_mode="sum"), self.render)
        self.assertIn("dx=+4", self.writes[-1])

    def test_log_gets_one_line_per_event(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "dedup.log")
        self.out.open_log(path)
        for _ in range(3):
            self.out.event(self._ev(), self.render)
        self.out.close_log()
        lines = [x for x in read_text(path).splitlines() if x.strip()]
        self.assertEqual(len(lines), 3)
        self.assertNotIn("×", "".join(lines))
        self.assertNotIn("\x1b", "".join(lines))

    def test_wrapping_line_is_never_rewritten_in_place(self):
        """\\x1b[1A cofa kursor o JEDEN wiersz — dla linii zawiniętej na kilka
        wierszy nadpisałoby to środek poprzedniego wpisu."""
        long_body = " ".join("%02X" % b for b in range(64))
        for _ in range(3):
            self.out.event(self._ev(key=("hid",), body=long_body, kind="HID"), self.render)
        self.assertFalse(any("\x1b[1A" in w for w in self.writes))

    def test_log_line_does_not_depend_on_console_width(self):
        """Plik logu ma być wiarygodnym zapisem sesji, a nie zrzutem tego,
        co akurat zmieściło się w oknie."""
        import tempfile
        report = " ".join("%02X" % b for b in range(64))
        bodies = {}
        for width in (40, 200):
            path = os.path.join(tempfile.mkdtemp(), "w%d.log" % width)
            out = dt.Output(color=False, dedup=False, width=width)
            out._write = lambda t: None
            out.open_log(path)
            render = dt.make_renderer(dt.Layout(width))
            out.event(dt.Event("10:00:00.000", 0.0, self.di, "HID", report,
                               "64 B", ("h",), "hid"), render)
            out.close_log()
            bodies[width] = read_text(path)
            self.assertIn(report, bodies[width], "szerokość %d" % width)

    def test_dedup_disabled_never_rewrites(self):
        out = dt.Output(color=True, dedup=False, width=100)
        writes = []
        out._write = writes.append
        for _ in range(3):
            out.event(self._ev(), self.render)
        self.assertFalse(any("\x1b[1A" in w for w in writes))


class TestDashboard(unittest.TestCase):
    """Panel steruje terminalem sekwencjami ANSI — sprawdzamy, że wysyła właściwe
    i że nie próbuje tego robić tam, gdzie nie zadziała."""

    class _FakeCap:
        def __init__(self, registry, analog=None):
            self.registry = registry
            self.stats = dt.Stats()
            self.rate = None
            self.analog = analog

    def _out(self, color=True):
        out = dt.Output(color=color, dedup=False, width=80)
        out.writes = []
        out._write = out.writes.append
        return out

    def _cap(self, analog=None):
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1B1C&PID_1B55#x",
                                info_fn=lambda h: None,
                                product_fn=lambda p: ("Testowe Urządzenie", ""))
        reg.resolve(1, dt.RIM_TYPEKEYBOARD)
        return self._FakeCap(reg, analog)

    def test_refuses_without_color(self):
        out = self._out(color=False)
        d = dt.Dashboard(out, self._cap(), 80, 40)
        self.assertFalse(d.start())
        self.assertEqual(out.writes, [])

    def test_refuses_on_short_console(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, dt.Dashboard.MIN_HEIGHT - 1)
        self.assertFalse(d.start())

    def test_start_sets_scroll_region_below_panel(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        self.assertTrue(d.start())
        blob = "".join(out.writes)
        self.assertIn("\x1b[2J", blob)                       # czyszczenie ekranu
        self.assertIn("\x1b[%d;40r" % (d.rows + 1), blob)    # DECSTBM
        self.assertGreater(d.rows, 0)
        self.assertLess(d.rows, 40)

    def test_panel_reserves_only_what_it_uses(self):
        """Panel nie może rezerwować ekranu na zapas — reszta okna to log."""
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        d.start()
        self.assertEqual(d.rows, d.rows_needed())

    def test_region_follows_growing_content(self):
        tr = dt.AnalogTracker()
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_045E&PID_02EA#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("Pad", ""))
        di = reg.resolve(1, dt.RIM_TYPEHID)
        out = self._out()
        d = dt.Dashboard(out, self._FakeCap(reg, tr), 80, 40)
        d.start()
        before = d.rows
        for usage in (0x30, 0x31, 0x32):
            tr.feed(di, {("val", 0x01, usage): 0}, {("val", 0x01, usage): (0, 255)})
        out.writes.clear()
        d.update(force=True)
        self.assertGreater(d.rows, before)
        self.assertIn("\x1b[%d;40r" % (d.rows + 1), "".join(out.writes))

    def test_log_starts_directly_below_the_panel(self):
        """Na starcie log ma zapełniać ekran od góry regionu, nie od dołu —
        inaczej sesja zaczyna się od wielkiej pustej luki pod panelem."""
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        d.start()
        self.assertIn("\x1b[%d;1H" % (d.rows + 1), "".join(out.writes))

    def _grow(self, out, d, tr, di, usages):
        out.writes.clear()
        for usage in usages:
            tr.feed(di, {("val", 0x01, usage): 0}, {("val", 0x01, usage): (0, 255)})
        d.update(force=True)
        return "".join(out.writes)

    def _analog_setup(self, height=40):
        tr = dt.AnalogTracker()
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_045E&PID_02EA#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("Pad", ""))
        di = reg.resolve(1, dt.RIM_TYPEHID)
        out = self._out()
        d = dt.Dashboard(out, self._FakeCap(reg, tr), 80, height)
        d.start()
        return out, d, tr, di

    def test_resize_restores_cursor_to_where_the_log_ended(self):
        """DECSTBM przenosi kursor na początek regionu. Odtwarzamy jego pozycję
        z licznika wypisanych linii — terminala nie da się o nią zapytać."""
        out, d, tr, di = self._analog_setup()
        blob = self._grow(out, d, tr, di, (0x30, 0x31))
        self.assertIn("\x1b[%d;1H" % (d.rows + 1), blob, "brak linii logu = szczyt regionu")

    def test_resize_keeps_cursor_where_the_log_actually_ended(self):
        """Regresja: pozycję liczono od NOWEJ wysokości panelu, choć linie
        zapisano przy starej — kursor lądował na zajętym wierszu."""
        out, d, tr, di = self._analog_setup()
        top_before = d.rows + 1
        for _ in range(3):
            out.segments([("linia logu", None)])
        blob = self._grow(out, d, tr, di, (0x30, 0x31))
        self.assertGreater(d.rows + 1, top_before, "panel musiał urosnąć")
        self.assertIn("\x1b[%d;1H" % (top_before + 3), blob)
        self.assertEqual(d.cursor_row(), top_before + 3)

    def test_growing_panel_pushes_cursor_below_its_edge(self):
        """Gdy panel urośnie ponad miejsce kursora, kursor musi zejść pod panel,
        inaczej log pisałby po panelu."""
        out, d, tr, di = self._analog_setup()
        blob = self._grow(out, d, tr, di, (0x30, 0x31, 0x32, 0x33))
        self.assertEqual(d.cursor_row(), d.rows + 1)
        self.assertIn("\x1b[%d;1H" % (d.rows + 1), blob)

    def test_shrinking_panel_clears_freed_rows(self):
        """Zwolnione wiersze wracają do regionu przewijania — bez wyczyszczenia
        zostałaby w nich stara treść panelu."""
        out, d, tr, di = self._analog_setup()
        self._grow(out, d, tr, di, (0x30, 0x31, 0x32))
        big = d.rows
        tr.axes.clear()
        out.writes.clear()
        d.update(force=True)
        self.assertLess(d.rows, big)
        for r in range(d.rows + 1, big + 1):
            self.assertIn("\x1b[%d;1H\x1b[2K" % r, "".join(out.writes))

    def test_wrapped_line_counts_as_several_rows(self):
        """Linia dłuższa niż okno zawija się i zjada kilka wierszy ekranu —
        licznik logiczny rozjeżdżałby rachunek kursora."""
        out = self._out()
        out.width = 40
        before = out.lines_out
        out.segments([("x" * 95, None)])
        self.assertEqual(out.lines_out - before, 3)

    def test_cursor_never_leaves_the_console(self):
        """Gdy log przewinął region, kursor zostaje na ostatnim wierszu."""
        out, d, tr, di = self._analog_setup(height=14)
        for _ in range(200):
            out.segments([("linia logu", None)])
        blob = self._grow(out, d, tr, di, (0x30, 0x31))
        self.assertIn("\x1b[14;1H", blob)

    def test_in_place_rewrites_do_not_move_the_cursor(self):
        """Scalanie ×N nadpisuje linię w miejscu — nie wolno go liczyć jako nowej."""
        out = self._out()
        out.dedup = True
        render = dt.make_renderer(dt.Layout(80))
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("X", ""))
        di = reg.resolve(1, dt.RIM_TYPEKEYBOARD)
        ev = dt.Event("t", 0.0, di, "KLAW", "A ↓", "m", ("k",), "down")
        out.event(ev, render)
        after_first = out.lines_out
        for _ in range(5):
            out.event(ev, render)
        self.assertEqual(out.lines_out, after_first)

    def test_panel_never_taller_than_the_console(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, dt.Dashboard.MIN_HEIGHT)
        self.assertTrue(d.start())
        self.assertLessEqual(d.rows, dt.Dashboard.MIN_HEIGHT - 4)

    def test_update_preserves_cursor_position(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        d.start()
        out.writes.clear()
        d.update(force=True)
        blob = "".join(out.writes)
        self.assertTrue(blob.startswith("\x1b7"), "musi zapisać pozycję kursora")
        self.assertTrue(blob.endswith("\x1b8"), "musi ją przywrócić")

    def test_update_is_throttled(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        d.start()
        out.writes.clear()
        for _ in range(50):
            d.update()
        self.assertLessEqual(len(out.writes), 1, "odświeżanie musi być dławione")

    def test_panel_never_exceeds_reserved_rows(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        d.start()
        out.writes.clear()
        d.update(force=True)
        blob = "".join(out.writes)
        rows = [int(m) for m in re.findall(r"\x1b\[(\d+);1H\x1b\[2K", blob)]
        self.assertTrue(rows)
        self.assertLessEqual(max(rows), d.rows)

    def test_panel_lines_fit_the_width(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 60, 40)
        d.start()
        for segs in d._panel_lines():
            plain = dt.render_plain(dt.fit_segments(segs, 60))
            self.assertLessEqual(len(plain), 60)

    def test_stop_clears_the_scroll_region(self):
        out = self._out()
        d = dt.Dashboard(out, self._cap(), 80, 40)
        d.start()
        out.writes.clear()
        d.stop()
        self.assertIn("\x1b[r", "".join(out.writes))

    def test_stop_without_start_is_a_noop(self):
        out = self._out()
        dt.Dashboard(out, self._cap(), 80, 40).stop()
        self.assertEqual(out.writes, [])

    def test_analog_axes_appear_in_the_panel(self):
        tr = dt.AnalogTracker()
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_045E&PID_02EA#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("Pad", ""))
        di = reg.resolve(1, dt.RIM_TYPEHID)
        tr.feed(di, {("val", 0x01, 0x30): 1000}, {("val", 0x01, 0x30): (-32768, 32767)})
        out = self._out()
        d = dt.Dashboard(out, self._FakeCap(reg, tr), 80, 40)
        d.start()
        text = "\n".join(dt.render_plain(s) for s in d._panel_lines())
        self.assertIn("X", text)
        self.assertIn("█", text)


class TestVersioning(unittest.TestCase):
    """Rozjazd wersji, CHANGELOG-a i tagu to klasyczny błąd wydania.
    Te testy łapią go lokalnie, zanim zrobi to release.yml."""

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _changelog(self):
        return read_text(os.path.join(self.ROOT, "CHANGELOG.md"))

    def test_version_is_semver(self):
        self.assertRegex(dt.__version__, r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")

    def test_changelog_has_entry_for_current_version(self):
        base = dt.__version__.split("-")[0]
        self.assertIn("## [%s]" % base, self._changelog(),
                      "brak sekcji CHANGELOG dla wersji %s" % dt.__version__)

    def test_changelog_has_unreleased_section(self):
        self.assertIn("## [Nieopublikowane]", self._changelog())

    def test_version_flag_reports_the_same_version(self):
        self.assertIn(dt.__version__, "\n".join(dt.version_lines()))

    def test_version_lines_mention_environment(self):
        text = "\n".join(dt.version_lines())
        self.assertIn("Python", text)
        self.assertIn("bit", text)

    def test_license_exists(self):
        self.assertIn("MIT License", read_text(os.path.join(self.ROOT, "LICENSE")))


class TestHelp(unittest.TestCase):
    def _text(self, color=False):
        out = _CapturingOutput()
        out.color = color
        dt.print_help(out)
        return "\n".join(out.lines)

    def test_every_option_is_documented(self):
        """Najczęstszy sposób gnicia takiej listy: ktoś dodaje flagę i zapomina
        dopisać ją do pomocy."""
        help_text = " ".join(
            name for _, opts in dt.HELP_SECTIONS for name, _ in opts)
        for action in dt.build_parser()._actions:
            for opt in action.option_strings:
                with self.subTest(opt=opt):
                    self.assertIn(opt, help_text)

    def test_help_mentions_no_phantom_options(self):
        """I odwrotnie: pomoc nie może obiecywać opcji, których nie ma."""
        real = {o for a in dt.build_parser()._actions for o in a.option_strings}
        for _, opts in dt.HELP_SECTIONS:
            for name, _ in opts:
                for token in name.replace(",", " ").split():
                    if token.startswith("-"):
                        self.assertIn(token, real, name)

    def test_sections_are_not_empty(self):
        for title, opts in dt.HELP_SECTIONS:
            self.assertTrue(opts, title)

    def test_examples_are_runnable_syntax(self):
        real = {o for a in dt.build_parser()._actions for o in a.option_strings}
        for cmd, _ in dt.HELP_EXAMPLES:
            for token in cmd.split():
                if token.startswith("--"):
                    self.assertIn(token, real, cmd)

    def test_plain_help_has_no_escape_sequences(self):
        self.assertNotIn("\x1b", self._text())

    def test_help_lists_all_sections(self):
        text = self._text()
        for title, _ in dt.HELP_SECTIONS:
            self.assertIn(title, text)
        self.assertIn("PRZYKŁADY", text)

    def test_columns_are_aligned(self):
        """Opisy muszą zaczynać się w tej samej kolumnie."""
        out = _CapturingOutput()
        dt.print_help(out)
        starts = set()
        for _, opts in dt.HELP_SECTIONS:
            for name, desc in opts:
                line = next(x for x in out.lines if desc in x)
                starts.add(line.index(desc))
        self.assertEqual(len(starts), 1, str(starts))

    def test_help_flag_exists_and_defaults_off(self):
        self.assertFalse(dt.build_parser().parse_args([]).help)
        self.assertTrue(dt.build_parser().parse_args(["--help"]).help)
        self.assertTrue(dt.build_parser().parse_args(["-h"]).help)


class TestExport(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1B1C&PID_1B55#x",
                                info_fn=lambda h: None,
                                product_fn=lambda p: ("Testowa Klawiatura", "ACME"))
        self.di = reg.resolve(7, dt.RIM_TYPEKEYBOARD)

    def _key_event(self, **kw):
        kw.setdefault("ev_type", dt.RIM_TYPEKEYBOARD)
        return dt.Event("10:00:00.000", 0.0, self.di, "KLAW", "Prawy Shift ↑",
                        "VK 10 sc 36 · 65 ms", ("k",), "up",
                        kname="Prawy Shift", is_break=True, hold_ms=65, **kw)

    def test_dict_has_structured_key_fields(self):
        r = dt.event_to_dict(self._key_event())
        self.assertEqual(r["key"], "Prawy Shift")
        self.assertFalse(r["down"])
        self.assertEqual(r["hold_ms"], 65)
        self.assertEqual(r["kind"], "KLAW")
        self.assertEqual(r["device"]["vid"], "1B1C")
        self.assertEqual(r["device"]["tag"], "#1")

    def test_time_is_iso_like(self):
        r = dt.event_to_dict(self._key_event())
        self.assertRegex(r["time"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$")

    def test_device_type_follows_the_event_not_the_cache(self):
        """Regresja: wstrzyknięty ruch myszy eksportował się jako
        device.type=KEYBOARD, bo di.type zamarzał na pierwszym zdarzeniu."""
        ev = dt.Event("10:00:00.000", 0.0, self.di, "MYSZ", "ruch dx=+1 dy=+0",
                      "", ("m",), "mouse", 1, 0, "sum", ev_type=dt.RIM_TYPEMOUSE)
        self.assertEqual(dt.event_to_dict(ev)["device"]["type"], "MOUSE")

    def test_jsonl_is_one_object_per_line(self):
        path = os.path.join(self.dir, "e.jsonl")
        sink = dt.JsonlSink(path)
        for _ in range(3):
            sink.event(self._key_event())
        sink.close()
        lines = [x for x in read_text(path).splitlines() if x.strip()]
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertEqual(json.loads(line)["key"], "Prawy Shift")

    def test_jsonl_keeps_polish_characters_readable(self):
        path = os.path.join(self.dir, "pl.jsonl")
        sink = dt.JsonlSink(path)
        sink.event(self._key_event())
        sink.close()
        self.assertIn("Prawy Shift", read_text(path))

    def test_jsonl_survives_truncation(self):
        """Strumieniowy format: przerwanie procesu nie psuje wcześniejszych rekordów."""
        path = os.path.join(self.dir, "t.jsonl")
        sink = dt.JsonlSink(path)
        sink.event(self._key_event())
        sink.event(self._key_event())
        sink.close()
        raw = read_text(path)
        partial = raw[:len(raw) - 20]                    # symuluj ucięcie
        good = [x for x in partial.splitlines() if x.strip().endswith("}")]
        self.assertGreaterEqual(len(good), 1)
        json.loads(good[0])

    def test_csv_has_header_once(self):
        path = os.path.join(self.dir, "e.csv")
        for _ in range(2):
            sink = dt.CsvSink(path)
            sink.event(self._key_event())
            sink.close()
        rows = read_text(path).splitlines()
        self.assertEqual(rows[0].split(",")[0], "time")
        self.assertEqual(sum(1 for r in rows if r.startswith("time,")), 1)
        self.assertEqual(len(rows), 3)

    def test_csv_columns_match_header(self):
        import csv as _csv
        path = os.path.join(self.dir, "c.csv")
        sink = dt.CsvSink(path)
        sink.event(self._key_event())
        sink.close()
        with open(path, encoding="utf-8", newline="") as fh:
            rows = list(_csv.reader(fh))
        self.assertEqual(len(rows[0]), len(rows[1]))
        self.assertEqual(rows[1][rows[0].index("key")], "Prawy Shift")

    def test_sink_error_does_not_raise(self):
        path = os.path.join(self.dir, "err.jsonl")
        sink = dt.JsonlSink(path)
        sink._fh.close()                                  # wymuś błąd zapisu
        sink.event(self._key_event())                     # nie może rzucić
        self.assertEqual(sink._errors, 1)
        sink.close()

    def test_output_dispatches_to_sinks(self):
        path = os.path.join(self.dir, "o.jsonl")
        out = dt.Output(color=False, dedup=False, width=100)
        out._write = lambda t: None
        self.assertTrue(out.add_sink("jsonl", path))
        out.event(self._key_event(), dt.make_renderer(dt.Layout(100)))
        out.close_log()
        self.assertEqual(len(read_text(path).splitlines()), 1)


class TestLogSeparation(unittest.TestCase):
    def test_log_never_receives_ansi(self):
        import io
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "dt.log")
        out = dt.Output(color=True, dedup=False, width=100)
        out._write = io.StringIO().write     # nie zaśmiecaj wyjścia testów
        out.open_log(path)
        layout = dt.Layout(100)
        render = dt.make_renderer(layout)
        reg = dt.DeviceRegistry(name_fn=lambda h: r"\\?\HID#VID_1&PID_2#x",
                                info_fn=lambda h: None, product_fn=lambda p: ("X", ""))
        di = reg.resolve(1, dt.RIM_TYPEKEYBOARD)
        out.event(dt.Event("10:00:00.000", 0.0, di, "KLAW", "A ↓", "m", ("k",), "down"), render)
        out.segments([("kolorowa linia", "warn")])
        out.raw("zwykła linia")
        out.close_log()
        body = read_text(path)
        self.assertNotIn("\x1b", body)
        self.assertIn("A ↓", body)
        self.assertIn("kolorowa linia", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
