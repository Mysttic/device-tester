# Device Tester — uniwersalny tester urządzeń wejściowych (Windows)

Jeden plik, zero zależności (tylko standardowy Python + `ctypes`). Pokazuje, **co**
zostało kliknięte / wciśnięte i **z którego urządzenia** to przyszło — dla dowolnego
urządzenia HID: klawiatur, myszy, padów/joysticków, pilotów, urządzeń Bluetooth
udających klawiaturę, klawiszy multimedialnych itd.

Działa na **Windows Raw Input API** — jedynym mechanizmie Windows, który odbiera
wejście ze *wszystkich* urządzeń jednocześnie i mówi, które je wysłało.

## Wymagania

- Windows 10/11
- Python 3.8+ (64-bit zalecany) — sprawdź: `python --version`
- **Nie** wymaga administratora ani instalacji pakietów.

## Uruchomienie

```bash
python device_tester.py                 # nasłuch: klawisze, przyciski, kółko, HID
python device_tester.py --verbose       # dodatkowo ruch myszy i pełne raporty HID
python device_tester.py --list          # tylko wypisz podłączone urządzenia i wyjdź
python device_tester.py --log plik.log  # nasłuch + zapis wszystkiego do pliku na bieżąco
python device_tester.py --log           # jw., automatyczna nazwa device-tester-DATA-CZAS.log
```

Zatrzymanie: **Ctrl+C**.

### Logowanie do pliku (`--log`)

Zapisuje do pliku dokładnie to, co widać na konsoli — **na bieżąco, zaraz po
wystąpieniu każdego zdarzenia** (flush po każdej linii), a nie dopiero na koniec.
Dzięki temu nawet jeśli program zostanie ubity, w pliku są wszystkie zdarzenia aż do
ostatniej chwili. Plik otwierany jest w trybie dopisywania (`append`) — kolejne
uruchomienia nie nadpisują poprzedniego logu. Błąd zapisu logu (np. brak uprawnień)
nie przerywa nasłuchu — narzędzie działa dalej, tylko bez logu. Można łączyć
z `--verbose`.

Nasłuch działa też, gdy okno terminala **nie ma fokusu** (`RIDEV_INPUTSINK`) — możesz
klikać w innych aplikacjach i nadal widzieć zdarzenia. Podłączanie/odłączanie urządzeń
w trakcie działania jest wykrywane na żywo.

## Jak czytać wyjście

```
10:47:37.322  Glorious Model I (MOUSE)
    MYSZ     LPM ↓
10:12:03.998  CORSAIR K70 ... (KEYBOARD)
    KLAWISZ  A                    ↓ wciśnięty  (VK=0x41 scan=0x1E)
10:15:44.101  Xbox Controller (HID VID:045E PID:02EA [UP:01 U:05])
    HID      zmiana: [3] 7F→FF, [9] 00→01
```

- **KLAWISZ** — nazwa klawisza (zlokalizowana), kierunek (↓/↑), kod VK i scancode.
- **MYSZ** — przyciski (LPM/PPM/ŚPM/PRZ4/PRZ5) i kółko; ruch tylko w `--verbose`.
- **HID** (pady, piloty, nietypowe urządzenia) — pokazuje, **które bajty raportu się
  zmieniły** względem poprzedniego. To uniwersalny sposób „co zostało wciśnięte", bo
  nie zakłada niczego o konkretnym urządzeniu. W `--verbose` widać cały raport w hex.

## Co jest łapane

Rejestrowane top-level HID collections (Generic Desktop + Consumer):

| UsagePage | Usage | Urządzenia |
|-----------|-------|------------|
| 0x01 | 0x02 | mysz |
| 0x01 | 0x04 | joystick |
| 0x01 | 0x05 | gamepad |
| 0x01 | 0x06 | klawiatura |
| 0x01 | 0x07 | keypad |
| 0x01 | 0x08 | kontroler wieloosiowy |
| 0x01 | 0x80 | System Control (power/sleep/wake) |
| 0x0C | 0x01 | Consumer Control (piloty, klawisze multimedialne) |

Każda para rejestrowana osobno — jeśli jedna zawiedzie, reszta działa dalej.

## Ograniczenia

- Tylko Windows (Raw Input to API Windows).
- Interpretacja przycisków pada/pilota jest pokazywana jako zmiana surowych bajtów
  raportu HID — czytelna i uniwersalna, ale bez tłumaczenia „bajt 3 = przycisk A".
  Aby zobaczyć znaczenie, obserwuj który bajt/bit zmienia się przy danym przycisku.
