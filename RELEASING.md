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
2. Zrób PR `develop` → `master` i scal go, gdy CI jest zielone.
3. Gotowe. Resztę robi automat.

Jeśli scalisz PR bez zmiany `VERSION.md`, nic się nie wyda — zmiana trafi
na `master` i poczeka na następne wydanie. Numer wersji jest jedynym przełącznikiem.

## Co robi automat

[release.yml](.github/workflows/release.yml) przy każdym pushu na `master`:

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
Użytkownik pobiera jeden plik i uruchamia; nie ma co budować ani instalować.

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
python device_tester.py --self-test
python -m unittest discover -s tests
```

`check` symuluje promocję CHANGELOG-a bez zapisu i powie, czego brakuje.
Podgląd notatek, które trafią do release'u:

```bash
python tools/release_tools.py notes
```

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
