#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narzędzia procesu wydawania — czyta VERSION.md, promuje CHANGELOG.md, synchronizuje kod.

Logika wydania siedzi tutaj, a nie w YAML-u, z jednego powodu: da się ją
uruchomić lokalnie i pokryć testami (tests/test_release.py). Skrypt wklejony
w krok workflow jest testowany dopiero na produkcji, czyli na tagu.

Użycie:
    python tools/release_tools.py version          # numer z VERSION.md
    python tools/release_tools.py notes            # treść notatek wydania
    python tools/release_tools.py check            # walidacja bez zmian w plikach
    python tools/release_tools.py release --date 2026-08-01
                                                   # promuje CHANGELOG + synchronizuje kod
"""

import argparse
import io
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = "https://github.com/Mysttic/device-tester"

UNRELEASED = "## [Nieopublikowane]"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")
VERSION_LINE = re.compile(r"^\s*(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\s*$")
SOURCE_VERSION = re.compile(r'^__version__\s*=\s*"[^"]*"', re.M)
# Blok definicji odnośników na końcu pliku: '[1.0.0]: https://...'
LINK_DEF = re.compile(r"^\[[^\]]+\]:\s")


def read(path):
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def newline_of(path) -> str:
    """Końce linii pliku. Zapis LF-em do repozytorium z CRLF dałby commit wydania
    zmieniający każdą linię — nie do przejrzenia i mylący w historii."""
    try:
        with io.open(path, "rb") as fh:
            return "\r\n" if b"\r\n" in fh.read(65536) else "\n"
    except Exception:
        return "\n"


def write(path, text, newline=None):
    if newline is None:
        newline = newline_of(path) if os.path.exists(path) else "\n"
    with io.open(path, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)


# --------------------------------------------------------------------------- #
#  VERSION.md
# --------------------------------------------------------------------------- #
def parse_version(text: str) -> str:
    """Numer wersji = pierwsza samodzielna linia wyglądająca jak SemVer.

    Reszta pliku (nagłówek, komentarz HTML, opis) jest dowolna, żeby dało się
    tam trzymać instrukcję dla człowieka.
    """
    in_comment = False
    for raw in text.splitlines():
        line = raw.strip()
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        m = VERSION_LINE.match(raw)
        if m:
            return m.group(1)
    raise ValueError("VERSION.md nie zawiera numeru wersji w formacie X.Y.Z")


def parse_version_notes(text: str) -> str:
    """Opis wydania z VERSION.md — proza poza komentarzem, pod numerem wersji."""
    out, seen_version, in_comment = [], False, False
    for raw in text.splitlines():
        line = raw.strip()
        if "<!--" in line:
            in_comment = True
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if not seen_version:
            if VERSION_LINE.match(raw):
                seen_version = True
            continue
        if line.startswith("#"):
            continue
        out.append(raw.rstrip())
    return "\n".join(out).strip()


# --------------------------------------------------------------------------- #
#  CHANGELOG.md
# --------------------------------------------------------------------------- #
def split_unreleased(text: str):
    """Zwróć (przed, treść_nieopublikowanych, po) w podziale na linie."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == UNRELEASED)
    except StopIteration:
        raise ValueError("CHANGELOG.md nie zawiera sekcji '%s'" % UNRELEASED)
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## [") or LINK_DEF.match(lines[i]):
            end = i
            break
    return lines[:start], lines[start + 1:end], lines[end:]


def extract_section(text: str, version: str):
    """Treść sekcji '## [wersja]' albo None, gdy takiej sekcji nie ma."""
    lines = text.splitlines()
    head = "## [%s]" % version
    start = next((i for i, l in enumerate(lines) if l.startswith(head)), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        # Definicje odnośników są granicą tak samo jak kolejny nagłówek —
        # inaczej ostatnia sekcja wciągnęłaby je do treści notatek.
        if lines[i].startswith("## [") or LINK_DEF.match(lines[i]):
            end = i
            break
    return "\n".join(lines[start + 1:end]).strip()


def previous_version(text: str):
    """Ostatnio wydany numer albo None."""
    for line in text.splitlines():
        m = re.match(r"^## \[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\]", line)
        if m:
            return m.group(1)
    return None


def promote_changelog(text: str, version: str, date: str, notes: str = "",
                      repo: str = REPO_URL):
    """Przenieś [Nieopublikowane] pod numer wersji i odśwież odnośniki.

    Zwraca (nowy_changelog, treść_notatek).
    """
    if not SEMVER.match(version):
        raise ValueError("Numer wersji '%s' nie jest zgodny z SemVer" % version)

    # Sekcja już istnieje (napisana ręcznie albo poprzedni przebieg ją utworzył):
    # nic nie przepisujemy, bierzemy jej treść jako notatki. Dzięki temu operacja
    # jest idempotentna, a przed powtórnym wydaniem chroni istnienie tagu,
    # a nie stan pliku.
    existing = extract_section(text, version)
    if existing is not None:
        if not existing:
            raise ValueError(
                "CHANGELOG.md ma pustą sekcję [%s] — opisz wydanie" % version)
        return text, existing

    before, body, after = split_unreleased(text)
    prev = previous_version("\n".join(after))

    content = "\n".join(body).strip()
    if not content:
        content = notes.strip()
    if not content:
        raise ValueError(
            "Brak treści wydania: sekcja [Nieopublikowane] w CHANGELOG.md jest pusta "
            "i VERSION.md nie zawiera opisu")

    nowe = list(before)
    nowe.append(UNRELEASED)
    nowe.append("")
    nowe.append("## [%s] - %s" % (version, date))
    nowe.append("")
    nowe.extend(content.splitlines())
    nowe.append("")
    nowe.extend(after)

    out = _update_links("\n".join(nowe).rstrip() + "\n", version, prev, repo)
    return out, content


def _update_links(text: str, version: str, prev, repo: str) -> str:
    """Odnośniki na dole pliku: [Nieopublikowane] i wpis nowej wersji."""
    lines = text.splitlines()
    unrel = "[Nieopublikowane]: %s/compare/v%s...HEAD" % (repo, version)
    if prev:
        entry = "[%s]: %s/compare/v%s...v%s" % (version, repo, prev, version)
    else:
        entry = "[%s]: %s/releases/tag/v%s" % (version, repo, version)

    done_unrel = False
    for i, line in enumerate(lines):
        if line.startswith("[Nieopublikowane]:"):
            lines[i] = unrel
            done_unrel = True
            break
    if not done_unrel:
        lines.append("")
        lines.append(unrel)

    if not any(l.startswith("[%s]:" % version) for l in lines):
        idx = next((i for i, l in enumerate(lines)
                    if l.startswith("[Nieopublikowane]:")), len(lines) - 1)
        lines.insert(idx + 1, entry)
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- #
#  device_tester.py
# --------------------------------------------------------------------------- #
def sync_source_version(source: str, version: str) -> str:
    """Ustaw __version__ w kodzie na wartość z VERSION.md."""
    if not SOURCE_VERSION.search(source):
        raise ValueError("Nie znaleziono __version__ w device_tester.py")
    return SOURCE_VERSION.sub('__version__ = "%s"' % version, source, count=1)


def source_version(source: str) -> str:
    m = re.search(r'^__version__\s*=\s*"([^"]*)"', source, re.M)
    return m.group(1) if m else ""


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #
def _paths():
    return (os.path.join(ROOT, "VERSION.md"),
            os.path.join(ROOT, "CHANGELOG.md"),
            os.path.join(ROOT, "device_tester.py"))


def _tag_exists(tag: str) -> bool:
    try:
        out = subprocess.run(["git", "tag", "--list", tag], cwd=ROOT,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return bool(out.stdout.strip())
    except Exception:
        return False


def _emit(text: str, path=None) -> None:
    """Notatki zawierają polskie znaki i symbole (↓, █) — stdout na Windows ma
    domyślnie kodowanie strony kodowej i by się na nich wywalił."""
    if path:
        write(path, text.rstrip() + "\n")
        return
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(text.rstrip() + "\n")


def _fail(message: str) -> int:
    """Czytelny błąd zamiast tracebacku — to komunikat dla człowieka
    (w logu CI albo w konsoli przed PR-em), a nie dla debugera."""
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stderr.write("BŁĄD WYDANIA: %s\n" % message)
    return 1


def main(argv=None) -> int:
    try:
        return _main(argv)
    except ValueError as e:
        return _fail(str(e))


def _main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Narzędzia procesu wydawania.")
    p.add_argument("polecenie", choices=("version", "notes", "check", "release"))
    p.add_argument("--date", default=None, help="data wydania RRRR-MM-DD")
    p.add_argument("--out", default=None, metavar="PLIK",
                   help="zapisz notatki do pliku zamiast na stdout")
    p.add_argument("--repo", default=REPO_URL)
    args = p.parse_args(argv)

    vpath, cpath, spath = _paths()
    version = parse_version(read(vpath))

    if args.polecenie == "version":
        print(version)
        return 0

    changelog = read(cpath)
    notes_fallback = parse_version_notes(read(vpath))

    if args.polecenie == "notes":
        _, notes = promote_changelog(changelog, version, args.date or "0000-00-00",
                                     notes_fallback, args.repo)
        _emit(notes, args.out)
        return 0

    if args.polecenie == "check":
        promote_changelog(changelog, version, args.date or "0000-00-00",
                          notes_fallback, args.repo)
        print("OK: wersja %s, CHANGELOG gotowy do promocji" % version)
        return 0

    if not args.date:
        p.error("polecenie 'release' wymaga --date")
    new_changelog, notes = promote_changelog(changelog, version, args.date,
                                             notes_fallback, args.repo)
    write(cpath, new_changelog)
    write(spath, sync_source_version(read(spath), version))
    sys.stderr.write("Wydano %s (%d znakow notatek)\n" % (version, len(notes)))
    _emit(notes, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
