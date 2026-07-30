# -*- coding: utf-8 -*-
"""Testy procesu wydawania.

Ta logika uruchamia się raz na wydanie, na gałęzi master, i zapisuje pliki
w repozytorium — czyli w miejscu, gdzie błąd jest najdroższy do cofnięcia.
Dlatego jest testowana mocniej niż reszta narzędzi pomocniczych.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import release_tools as rt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CHANGELOG = """# Historia zmian

## [Nieopublikowane]

### Dodane

- nowa flaga `--foo`

### Naprawione

- coś nie działało

## [1.0.0] - 2026-07-30

- pierwsze wydanie

[Nieopublikowane]: https://example.com/compare/v1.0.0...HEAD
[1.0.0]: https://example.com/releases/tag/v1.0.0
"""


class TestParseVersion(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(rt.parse_version("1.2.3"), "1.2.3")

    def test_with_markdown_around(self):
        self.assertEqual(rt.parse_version("# Wersja\n\n2.0.1\n"), "2.0.1")

    def test_prerelease(self):
        self.assertEqual(rt.parse_version("# Wersja\n\n1.1.0-rc1\n"), "1.1.0-rc1")

    def test_ignores_numbers_inside_comment(self):
        """W komentarzu są przykłady numerów — nie mogą wygrać z prawdziwym."""
        text = "# Wersja\n\n3.1.4\n\n<!--\nkiedyś było 9.9.9\n-->\n"
        self.assertEqual(rt.parse_version(text), "3.1.4")

    def test_comment_before_version(self):
        text = "<!--\n0.0.1\n-->\n\n4.5.6\n"
        self.assertEqual(rt.parse_version(text), "4.5.6")

    def test_missing_version_raises(self):
        with self.assertRaises(ValueError):
            rt.parse_version("# Wersja\n\nbrak\n")

    def test_real_file_parses(self):
        text = rt.read(os.path.join(ROOT, "VERSION.md"))
        self.assertRegex(rt.parse_version(text), r"^\d+\.\d+\.\d+")

    def test_notes_from_version_file(self):
        text = "# Wersja\n\n1.2.3\n\nDrobne poprawki wydajności.\n\n<!-- komentarz -->\n"
        self.assertEqual(rt.parse_version_notes(text), "Drobne poprawki wydajności.")

    def test_notes_empty_when_only_comment(self):
        text = rt.read(os.path.join(ROOT, "VERSION.md"))
        self.assertEqual(rt.parse_version_notes(text), "")


class TestPromoteChangelog(unittest.TestCase):
    def _promote(self, version="1.1.0", text=CHANGELOG, notes=""):
        return rt.promote_changelog(text, version, "2026-08-01", notes,
                                    "https://example.com")

    def test_creates_section_with_date(self):
        out, _ = self._promote()
        self.assertIn("## [1.1.0] - 2026-08-01", out)

    def test_keeps_empty_unreleased_on_top(self):
        out, _ = self._promote()
        self.assertIn("## [Nieopublikowane]", out)
        pos_unrel = out.index("## [Nieopublikowane]")
        pos_new = out.index("## [1.1.0]")
        self.assertLess(pos_unrel, pos_new, "nowa sekcja musi być POD nieopublikowanymi")

    def test_unreleased_section_is_emptied(self):
        out, _ = self._promote()
        _, body, _ = rt.split_unreleased(out)
        self.assertEqual("\n".join(body).strip(), "")

    def test_content_is_moved_not_copied(self):
        out, notes = self._promote()
        self.assertIn("nowa flaga `--foo`", notes)
        self.assertEqual(out.count("nowa flaga `--foo`"), 1)

    def test_older_versions_survive(self):
        out, _ = self._promote()
        self.assertIn("## [1.0.0] - 2026-07-30", out)
        self.assertIn("pierwsze wydanie", out)

    def test_links_are_updated(self):
        out, _ = self._promote()
        self.assertIn("[Nieopublikowane]: https://example.com/compare/v1.1.0...HEAD", out)
        self.assertIn("[1.1.0]: https://example.com/compare/v1.0.0...v1.1.0", out)
        self.assertIn("[1.0.0]: https://example.com/releases/tag/v1.0.0", out)

    def test_first_release_links_to_tag(self):
        text = "# Historia\n\n## [Nieopublikowane]\n\n- start\n"
        out, _ = rt.promote_changelog(text, "0.1.0", "2026-01-01", "",
                                      "https://example.com")
        self.assertIn("[0.1.0]: https://example.com/releases/tag/v0.1.0", out)

    def test_existing_section_is_reused_not_duplicated(self):
        """Sekcja napisana ręcznie ma zostać użyta jako notatki, bez przepisywania
        pliku — inaczej pierwsze wydanie po wprowadzeniu automatu by się wywaliło."""
        out, notes = self._promote(version="1.0.0")
        self.assertEqual(out, CHANGELOG, "plik nie może się zmienić")
        self.assertEqual(notes, "- pierwsze wydanie")

    def test_promotion_is_idempotent(self):
        """Powtórzony przebieg nie może dołożyć drugiej sekcji."""
        once, _ = self._promote()
        twice, notes = rt.promote_changelog(once, "1.1.0", "2026-08-01", "",
                                            "https://example.com")
        self.assertEqual(once, twice)
        self.assertIn("nowa flaga `--foo`", notes)
        self.assertEqual(twice.count("## [1.1.0]"), 1)

    def test_existing_but_empty_section_is_rejected(self):
        text = "# H\n\n## [Nieopublikowane]\n\n## [1.1.0] - 2026-08-01\n\n## [1.0.0] - x\n\n- a\n"
        with self.assertRaises(ValueError):
            rt.promote_changelog(text, "1.1.0", "2026-08-01", "", "https://example.com")

    def test_invalid_semver_is_rejected(self):
        with self.assertRaises(ValueError):
            self._promote(version="1.0")

    def test_missing_unreleased_section_is_rejected(self):
        with self.assertRaises(ValueError):
            rt.promote_changelog("# Historia\n", "1.1.0", "2026-08-01")

    def test_empty_unreleased_falls_back_to_version_notes(self):
        text = "# Historia\n\n## [Nieopublikowane]\n\n## [1.0.0] - 2026-07-30\n\n- x\n"
        out, notes = rt.promote_changelog(text, "1.0.1", "2026-08-01",
                                          "Poprawka literówki.", "https://example.com")
        self.assertEqual(notes, "Poprawka literówki.")
        self.assertIn("Poprawka literówki.", out)

    def test_empty_everything_is_rejected(self):
        """Wydanie bez opisu jest bezużyteczne — lepiej zatrzymać niż wypuścić."""
        text = "# Historia\n\n## [Nieopublikowane]\n\n## [1.0.0] - 2026-07-30\n\n- x\n"
        with self.assertRaises(ValueError):
            rt.promote_changelog(text, "1.0.1", "2026-08-01", "", "https://example.com")

    def test_next_release_builds_on_the_previous_one(self):
        """Po promocji plik nadal daje się promować dla kolejnej wersji."""
        out, _ = self._promote()
        out2, _ = rt.promote_changelog(
            out.replace("## [Nieopublikowane]\n",
                        "## [Nieopublikowane]\n\n- kolejna zmiana\n", 1),
            "1.2.0", "2026-09-01", "", "https://example.com")
        self.assertIn("## [1.2.0] - 2026-09-01", out2)
        self.assertIn("## [1.1.0] - 2026-08-01", out2)
        self.assertIn("[1.2.0]: https://example.com/compare/v1.1.0...v1.2.0", out2)

    def test_previous_version_detection(self):
        self.assertEqual(rt.previous_version(CHANGELOG), "1.0.0")
        self.assertIsNone(rt.previous_version("## [Nieopublikowane]\n"))


class TestSyncSourceVersion(unittest.TestCase):
    SRC = 'import sys\n\n__version__ = "1.0.0"\n\nIS_WINDOWS = True\n'

    def test_replaces_version(self):
        out = rt.sync_source_version(self.SRC, "1.1.0")
        self.assertIn('__version__ = "1.1.0"', out)
        self.assertNotIn('"1.0.0"', out)

    def test_leaves_rest_untouched(self):
        out = rt.sync_source_version(self.SRC, "2.0.0")
        self.assertIn("IS_WINDOWS = True", out)
        self.assertIn("import sys", out)

    def test_only_first_occurrence(self):
        src = self.SRC + '\nprint("__version__ = \\"x\\"")\n'
        out = rt.sync_source_version(src, "3.0.0")
        self.assertEqual(out.count('__version__ = "3.0.0"'), 1)

    def test_missing_field_raises(self):
        with self.assertRaises(ValueError):
            rt.sync_source_version("print(1)\n", "1.0.0")

    def test_reads_back_what_it_wrote(self):
        out = rt.sync_source_version(self.SRC, "4.5.6")
        self.assertEqual(rt.source_version(out), "4.5.6")

    def test_real_source_has_version(self):
        src = rt.read(os.path.join(ROOT, "device_tester.py"))
        self.assertRegex(rt.source_version(src), r"^\d+\.\d+\.\d+")


class TestFileWriting(unittest.TestCase):
    """Zapis nie może zmieniać niczego poza treścią — commit wydania ma być
    czytelny, a nie przepisywać całego pliku."""

    def setUp(self):
        import tempfile
        self.dir = tempfile.mkdtemp()

    def _roundtrip(self, newline):
        path = os.path.join(self.dir, "p%s.txt" % len(newline))
        with open(path, "wb") as fh:
            fh.write(("a" + newline + "b" + newline).encode("utf-8"))
        rt.write(path, rt.read(path))
        with open(path, "rb") as fh:
            return fh.read()

    def test_crlf_is_preserved(self):
        self.assertEqual(self._roundtrip("\r\n"), b"a\r\nb\r\n")

    def test_lf_is_preserved(self):
        self.assertEqual(self._roundtrip("\n"), b"a\nb\n")

    def test_newline_detection(self):
        path = os.path.join(self.dir, "d.txt")
        with open(path, "wb") as fh:
            fh.write(b"x\r\ny\r\n")
        self.assertEqual(rt.newline_of(path), "\r\n")
        with open(path, "wb") as fh:
            fh.write(b"x\ny\n")
        self.assertEqual(rt.newline_of(path), "\n")

    def test_missing_file_defaults_to_lf(self):
        self.assertEqual(rt.newline_of(os.path.join(self.dir, "brak.txt")), "\n")

    def test_utf8_survives_roundtrip(self):
        path = os.path.join(self.dir, "pl.md")
        rt.write(path, "zażółć gęślą jaźń ↓ █\n")
        self.assertIn("zażółć", rt.read(path))
        self.assertIn("↓", rt.read(path))


class TestRealRepositoryState(unittest.TestCase):
    """Stan repo musi być gotowy do wydania w każdej chwili."""

    def test_version_file_matches_source(self):
        v = rt.parse_version(rt.read(os.path.join(ROOT, "VERSION.md")))
        s = rt.source_version(rt.read(os.path.join(ROOT, "device_tester.py")))
        self.assertEqual(v, s,
                         "VERSION.md i __version__ rozjechały się — po wydaniu "
                         "automat synchronizuje je sam, więc rozjazd oznacza "
                         "ręczną edycję device_tester.py")

    def test_current_version_has_release_notes(self):
        """Wersja z VERSION.md musi mieć z czego zbudować notatki — albo sekcję
        w CHANGELOG-u, albo wpisy pod [Nieopublikowane], albo opis w VERSION.md."""
        version = rt.parse_version(rt.read(os.path.join(ROOT, "VERSION.md")))
        changelog = rt.read(os.path.join(ROOT, "CHANGELOG.md"))
        notes = rt.parse_version_notes(rt.read(os.path.join(ROOT, "VERSION.md")))
        _, body = rt.promote_changelog(changelog, version, "2099-01-01", notes)
        self.assertTrue(body.strip())

    def test_real_changelog_promotes_for_a_new_version(self):
        """Symulacja przyszłego wydania na prawdziwym pliku, bez zapisu."""
        changelog = rt.read(os.path.join(ROOT, "CHANGELOG.md"))
        seeded = changelog.replace(rt.UNRELEASED,
                                   rt.UNRELEASED + "\n\n- przykładowa zmiana", 1)
        out, body = rt.promote_changelog(seeded, "99.0.0", "2099-01-01")
        self.assertIn("## [99.0.0] - 2099-01-01", out)
        self.assertIn("przykładowa zmiana", body)
        self.assertIn("[99.0.0]: https://github.com/Mysttic/device-tester/compare/", out)

    def test_cli_reports_errors_without_traceback(self):
        """Komunikat trafia do logu CI i do konsoli przed PR-em — ma być czytelny."""
        import io as _io
        import tempfile
        d = tempfile.mkdtemp()
        old_root, rt.ROOT = rt.ROOT, d
        rt.write(os.path.join(d, "VERSION.md"), "# Wersja\n\nbrak\n")
        rt.write(os.path.join(d, "CHANGELOG.md"), "## [Nieopublikowane]\n")
        err, old_err = _io.StringIO(), sys.stderr
        sys.stderr = err
        try:
            rc = rt.main(["version"])
        finally:
            sys.stderr = old_err
            rt.ROOT = old_root
        self.assertEqual(rc, 1)
        self.assertIn("BŁĄD WYDANIA", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_cli_version_command(self):
        import io as _io
        buf, old = _io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            rc = rt.main(["version"])
        finally:
            sys.stdout = old
        self.assertEqual(rc, 0)
        self.assertRegex(buf.getvalue().strip(), r"^\d+\.\d+\.\d+")


if __name__ == "__main__":
    unittest.main(verbosity=2)
