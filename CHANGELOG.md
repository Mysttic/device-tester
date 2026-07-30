# Historia zmian

Format wg [Keep a Changelog](https://keepachangelog.com/pl/1.1.0/),
wersjonowanie wg [SemVer](https://semver.org/lang/pl/).

Nagłówek `## [X.Y.Z] - RRRR-MM-DD` jest czytany przez `release.yml` i staje się
treścią notatek wydania na GitHubie — pisz go dla ludzi, nie dla maszyny.

## [Nieopublikowane]

## [1.0.0] - 2026-07-30

Pierwsze wydanie oznaczone wersją. Narzędzie działało wcześniej, ale bez
wersjonowania, testów i procesu wydawania.

### Dodane

- Dekodowanie raportów HID przez `HidP_*` — zamiast „bajt 3 się zmienił"
  narzędzie pokazuje `przycisk 1 ↓` i `X ▕────█───┼────────▏ -16412`.
  Preparsed data pobierane przez `RIDI_PREPARSEDDATA`, bez uprawnień administratora.
  Tablica ~120 nazw HID Usage po polsku; surowy diff bajtów zostaje jako fallback
  dla urządzeń bez opisu raportów (`--raw-hid` wymusza go zawsze).
- Grupowanie kolekcji HID jednego urządzenia pod wspólnym tagiem `#N` i stałym
  kolorem. Obsługa formatu ścieżek Bluetooth. Klasa urządzenia wyznaczana przez
  kolekcję główną, więc mysz z kolekcją makr nie trafia do sekcji klawiatur.
- `--hz` — pomiar częstotliwości raportowania (średnia z okna 1 s).
- `--chatter [MS]` — wykrywanie drgania styków zużytego mikroswitcha.
- `--analog` — paski osi na żywo, zasięg i martwa strefa liczona z dominującego
  położenia spoczynkowego.
- `--dashboard` — przypięty panel na żywo (region przewijania DECSTBM, bez TUI).
- `--jsonl` i `--csv` — eksport strukturalny z polami zamiast sformatowanych napisów.
- `--only`, `--device`, `--exclude` — filtrowanie po typie, `#N`, `VID:PID` lub nazwie.
- Podsumowanie sesji po Ctrl+C: liczniki, czasy trzymania, top klawisze, dystans myszy.
- Kolory ANSI z poszanowaniem `NO_COLOR`, scalanie powtórzeń w linię `×N`.
- `--self-test`, `--dump-caps`, `--version`, pogrupowane `--help`.
- Zestaw testów (`tests/`) i CI na `windows-latest` dla Pythona 3.8 i 3.13.

### Naprawione

- **Urządzenia HID z wieloma Report ID milkły po pierwszym raporcie.** Stan diffu
  był kluczowany samym uchwytem i chroniony wyłącznie porównaniem długości.
- **Pause dekodował się jako „Ctrl", a NumLock jako „Pause"** — prefiks E1 był
  ignorowany, a scancode 0x45 wymaga bitu rozszerzonego.
- **Klawisze multimedialne pokazywały się jako litery** (`Głośność +` → `B`),
  bo dzielą scancode z klawiszami znakowymi, a rozróżnia je dopiero kod VK.
- **Lewy i prawy Shift/Ctrl/Alt były nierozróżnialne** — bit 25 `lparam` scalał je
  w jedną nazwę, mimo że scancode jest jedynym dyskryminatorem.
- **Ekran dotykowy i RDP raportowały „ruch dx=32768"** przy nieruchomym urządzeniu —
  `RAWMOUSE.usFlags` nie było czytane, więc pozycja absolutna udawała przesunięcie.
- **Typ urządzenia zamrażał się na pierwszym zdarzeniu** — kółko myszy trafiało
  pod etykietę `(KEYBOARD)`.
- **Wyjątek w procedurze okna pomijał `DefWindowProc`** dla `WM_INPUT`, czyli
  powodował wyciek bufora raw input.
- **Urządzenie bez danych z `RIDI_DEVICEINFO` dopisywało się do grupy przy każdym
  zdarzeniu** — nieograniczony wzrost pamięci.
- **Mysz 1000 Hz raportowała 10 Hz** — niedokończone okno pomiarowe było wliczane
  z pełną wagą, a średnia liczona arytmetycznie z okien o różnej długości.
- **Oś analogowa przesuwana wolniej niż 1/64 zakresu na raport była niewidoczna** —
  próg szumu porównywał się z poprzednią próbką i nigdy się nie kumulował.
- Widmowe urządzenie w podsumowaniu po odłączeniu sprzętu w trakcie pomiaru.
- Brak `DestroyWindow` / `UnregisterClass` / `RIDEV_REMOVE` przy wyjściu.
- Pętla komunikatów kręcąca się na 100% CPU przy `WAIT_FAILED`.
- Krzyżak z `HasNull` raportowany jako skrajne wychylenie.

### Zmienione

- Wyjście przepisane na jednoliniowe z kolumnami; treść zdarzenia nigdy nie jest
  obcinana, docinane są wyłącznie metadane i tylko na konsoli.
- **Zawartość pliku `--log` nie zależy już od szerokości okna** i nigdy nie
  zawiera sekwencji ANSI.
- `--list` grupuje kolekcje i pokazuje dane z `RID_DEVICE_INFO`, które wcześniej
  były odczytywane, ale nieużywane.

[Nieopublikowane]: https://github.com/Mysttic/device-tester/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/Mysttic/device-tester/releases/tag/v1.0.0
