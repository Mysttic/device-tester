# Rozwój

Opis samego narzędzia jest w [README.md](README.md). Ten plik jest dla osób,
które zmieniają kod.

## Środowisko

Do uruchomienia i testowania nie trzeba niczego instalować — wystarczy Python 3.8+
na Windows 10/11. Pillow jest potrzebny wyłącznie do przegenerowania zrzutów ekranu.

## Testy

```bash
python device_tester.py --self-test
python -m unittest discover -s tests -v
```

`--self-test` sprawdza logikę bez żadnego urządzenia: rozmiary struktur ctypes,
dekodowanie raportów HID, renderowanie, grupowanie i filtry. Jest wbudowany
w narzędzie, więc działa też u użytkownika przy diagnozowaniu zgłoszenia.

Zestaw w `tests/` dokłada test całej ścieżki live: zdarzenia wstrzykiwane przez
`SendInput` przechodzą przez okno komunikatów, dekodery i wyjście. Używane są
wyłącznie klawisze F13-F15, więc test **nie wpisuje niczego w aktywne okno**.

Testy wymagające fizycznego urządzenia same się pomijają (`skipTest`), gdy go nie ma —
dlatego przechodzą na CI, które sprzętu nie ma. Jeśli zmieniasz dekodowanie HID,
napisz w PR-ze, na jakim urządzeniu to sprawdziłeś.

## Układ kodu

`device_tester.py` jest celowo **jednym plikiem** — narzędzie diagnostyczne ma dać się
skopiować na cudzą maszynę i uruchomić bez instalowania czegokolwiek. Mapa sekcji
i zasady, które łatwo złamać przy zmianach, są w docstringu na początku pliku.

Cztery niezmienniki warte zapamiętania:

- `WM_INPUT` **musi** trafić do `DefWindowProc`, inaczej system nie zwolni bufora
  raw input; dlatego obsługa zdarzenia siedzi w `try/except`.
- Kolory nigdy nie trafiają do pliku logu ani do eksportu — kolorowanie zachodzi
  dopiero przy renderowaniu do konsoli.
- Treść zdarzenia nie jest obcinana; do szerokości konsoli docinane są wyłącznie
  metadane.
- Znacznik czasu powstaje na wejściu do `Capture.on_raw_input`, przed rozwiązywaniem
  urządzenia — od tego zależą `--hz` i `--chatter`.

## Zrzuty ekranu

Obrazki w README są generowane z **prawdziwych** uruchomień narzędzia:

```bash
pip install pillow
python tools/make_screenshots.py
```

`tools/make_screenshots.py` przechwytuje wyjście wraz z sekwencjami ANSI, odtwarza je
w minimalnym emulatorze terminala i renderuje siatkę znaków do PNG. Dzięki temu zrzuty
pokazują też panel na żywo i nadpisywanie linii przy scalaniu `×N`. Wyniki lądują
w `docs/`.

Przegeneruj je, jeśli zmienił się wygląd wyjścia.

---

# Wydawanie wersji

**Nie zakłada się tagów ręcznie.** Wydanie wyzwala scalenie na `master`, a numer
wersji ustawia się w jednym miejscu — [VERSION.md](VERSION.md).

```
develop  ──PR──▶  master  ──▶  automat: CHANGELOG, tag, release
   ▲                                            │
   └────────────── merge z powrotem ────────────┘
```

## Jak wydać wersję

1. **Na `develop`**: podnieś numer w [VERSION.md](VERSION.md) i opisz zmiany
   w [CHANGELOG.md](CHANGELOG.md) pod nagłówkiem `## [Nieopublikowane]`.
2. Zrób PR `develop` → `master` i scal go, gdy testy na PR-ze są zielone.
3. Gotowe. Resztę robi automat.

Jeśli scalisz PR bez zmiany `VERSION.md`, nic się nie wyda — zmiana trafi
na `master` i poczeka na następne wydanie. Numer wersji jest jedynym przełącznikiem.

## Co robi automat

[release.yml](.github/workflows/release.yml) przy pushu na `master`, który zmienia
`VERSION.md` (albo uruchomiony ręcznie przez *Run workflow* — **wyłącznie z gałęzi
`master`**; z innej gałęzi job jest pomijany, bo automat i tak pushuje `HEAD`
na `master`):

1. czyta numer z `VERSION.md`,
2. **jeśli tag `vX.Y.Z` już istnieje — kończy bez żadnego skutku** (to jest bramka:
   dzięki niej scalenie niezwiązane z wydaniem niczego nie publikuje, a ponowne
   uruchomienie przebiegu jest bezpieczne),
3. uruchamia self-test i pełny zestaw testów,
4. przenosi sekcję `[Nieopublikowane]` z CHANGELOG-a pod `## [X.Y.Z] - data`,
   zakłada nową pustą sekcję `[Nieopublikowane]` i odświeża odnośniki,
5. wpisuje numer do `__version__` w `device_tester.py`,
6. commituje te dwa pliki z powrotem na `master`,
7. zakłada tag `vX.Y.Z`,
8. tworzy release z notatkami z CHANGELOG-a i dołącza `device_tester.py` oraz `LICENSE`.

Artefaktem wydania jest **sam plik `device_tester.py`** — to cały program.

Numer z myślnikiem (np. `1.1.0-rc1`) tworzy pre-release.

## Skąd bierze się opis wydania

W kolejności:

1. sekcja `## [Nieopublikowane]` z `CHANGELOG.md` — **to jest normalna droga**,
2. proza dopisana pod numerem w `VERSION.md`, jeśli sekcja jest pusta,
3. sekcja `## [X.Y.Z]` napisana ręcznie, jeśli już istnieje (wtedy CHANGELOG
   nie jest przepisywany, a jego treść trafia prosto do notatek).

Jeśli żadne z tych źródeł nie ma treści, wydanie **zatrzymuje się z błędem**.
Wydanie bez opisu jest bezużyteczne dla kogoś, kto potem szuka, co się zmieniło.

## Numerowanie (SemVer dla narzędzia CLI)

Interfejsem tego programu są **flagi CLI oraz format `--log`, `--jsonl` i `--csv`**.
Wyjście na konsolę traktujemy jako prezentację, nie kontrakt.

| Zmiana | Człon |
|---|---|
| usunięcie flagi, zmiana jej znaczenia, usunięcie pola z `--jsonl`/`--csv` | MAJOR |
| nowa flaga, nowe pole eksportu, nowe urządzenia/usage, nowa sekcja podsumowania | MINOR |
| naprawa błędu, poprawka wydajności, zmiana układu konsoli, dokumentacja | PATCH |

Zmiana wyglądu konsoli to PATCH — ale jeśli zmienia **treść** logu albo eksportu,
to już MINOR (dodanie pola) lub MAJOR (usunięcie).

## Sprawdzenie przed PR-em

```bash
python tools/release_tools.py check      # czy da się wydać obecny stan
python tools/release_tools.py notes      # podgląd notatek, które trafią do release'u
```

`check` symuluje promocję CHANGELOG-a bez zapisu i powie, czego brakuje.

## Gdzie biegną testy

| Zdarzenie | Co się uruchamia |
|---|---|
| PR `develop` → `master` | `testy` — Windows, Python 3.8 i 3.13 |
| PR do `master` z innej gałęzi (np. `hotfix/*`) | nic — job jest pomijany, a przebieg GitHub pokaże jako **zielony** |
| PR zmieniający wyłącznie dokumentację (`README.md`, `CONTRIBUTING.md`, `docs/`) | nic — dokumentacja nie wymaga testów; `VERSION.md`, `CHANGELOG.md` i `LICENSE` **nie** są na tej liście, bo czytają je testy |
| PR do `develop` | nic |
| commit na `develop` | nic — te same zmiany przeszły już testy na PR-ze |
| commit na `master` zmieniający `VERSION.md` | `wydanie`, a ono uruchamia self-test i pełny zestaw **przed** publikacją |
| commit na `master` bez zmiany `VERSION.md` | nic |
| ręcznie | oba workflow przez *Run workflow* |

Wniosek praktyczny: **niczego nie wypuścimy bez testów**, bo bramka siedzi
w `release.yml`, a nie w `tests.yml`. Jedyna nieprzetestowana ścieżka to
bezpośredni push na `master` bez podniesienia wersji — czyli zmiana, która
i tak niczego nie publikuje. Włączona ochrona gałęzi eliminuje i to.

Świadoma decyzja: PR spoza `develop` **nie uruchamia testów i mimo to jest
zielony** (pominięty job GitHub liczy jak sukces). Nie ma joba-zaślepki, który
miałby z tego zrobić czerwony wynik. Jeśli kiedyś włączysz ochronę `master`
z wymaganym checkiem `testy`, pamiętaj, że taki PR nigdy go nie zaraportuje.

## Po wydaniu

Automat dopisał commit na `master`, więc ściągnij go do `develop`:

```bash
git switch develop && git merge origin/master && git push
```

Bez tego następny PR pokaże fałszywe różnice w `CHANGELOG.md` i `device_tester.py`.

## Hotfix

```bash
git switch -c hotfix/1.0.1 master
# poprawka + wpis pod [Nieopublikowane] + PATCH w VERSION.md
```

PR `hotfix/1.0.1` → `master`. Potem **koniecznie** scal `master` z powrotem
do `develop`.

Na takim PR-ze `testy` się nie uruchomią (biegną tylko dla PR-a z `develop`),
więc **odpal je ręcznie** przez *Run workflow* na workflow `testy`, wybierając
gałąź `hotfix/*`. To krok obowiązkowy, a nie alternatywa dla bramki
w `release.yml`: ta uruchamia pełny zestaw wyłącznie na Pythonie 3.13, podczas
gdy deklarowane minimum z README to 3.8. Matrix 3.8 + 3.13 żyje tylko
w `tests.yml` — bez ręcznego uruchomienia hotfix wyjdzie do ludzi
nieprzetestowany na najstarszym wspieranym Pythonie.

## Wymagania po stronie repozytorium

- **Uprawnienia**: `Settings → Actions → General → Workflow permissions` musi być
  ustawione na *Read and write permissions*, inaczej automat nie zapisze commita ani tagu.
- **Ochrona gałęzi**: jeśli `master` jest chroniony, dodaj `github-actions[bot]`
  do wyjątków w regule (*Allow specified actors to bypass required pull requests*).
  Bez tego krok „Zapisz zmiany w repozytorium" zostanie odrzucony.
- Commit automatu ma w treści `[skip ci]`, a pushe robione tokenem `GITHUB_TOKEN`
  i tak nie wyzwalają kolejnych przebiegów — pętli nie będzie.

## Wycofanie wydania

Tagów, które wyszły publicznie, nie usuwamy — ktoś mógł już pobrać plik.
Zamiast tego wydaj wersję PATCH z poprawką, a wadliwy release oznacz na GitHubie
jako „pre-release" i dopisz ostrzeżenie w jego notatkach.

## Czego świadomie nie ma

**Gotowego pliku `.exe`.** Byłby wygodny dla użytkowników bez Pythona, ale program
z definicji przechwytuje wejście ze wszystkich klawiatur — niepodpisany plik
wykonywalny o takim zachowaniu jest niemal pewnym trafieniem dla SmartScreena
i skanerów antywirusowych, a użytkownik nie ma jak zweryfikować, co pobiera.
Plik `.py` jest czytelny przed uruchomieniem, więc zostaje jedynym artefaktem.
Jeśli kiedyś dojdzie `.exe`, powinien być podpisany certyfikatem code-signing.

**Publikacji na PyPI.** Narzędzie jest jednoplikowe i wyłącznie dla Windows;
`pip install` nie dałby nic ponad pobranie tego samego pliku.
