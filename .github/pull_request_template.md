## Co i po co

<!-- Jedno-dwa zdania: co się zmienia i dlaczego. -->

## Lista kontrolna

- [ ] `python device_tester.py --self-test` przechodzi
- [ ] `python -m unittest discover -s tests` przechodzi
- [ ] Nowe zachowanie ma test; naprawiony błąd ma test regresji
- [ ] Zmiany opisane w `CHANGELOG.md` pod `## [Nieopublikowane]`
- [ ] README zaktualizowany, jeśli doszła/zmieniła się flaga
- [ ] Zrzuty przegenerowane (`python docs/make_screenshots.py`), jeśli zmienił się wygląd wyjścia

## Czy ten PR ma wydać wersję?

Wydanie wyzwala **zmiana numeru w `VERSION.md`** — nie tag, nie sam merge.

- [ ] **Tak** — numer w `VERSION.md` podniesiony wg SemVer ([RELEASING.md](../RELEASING.md)),
      a `python tools/release_tools.py check` przechodzi
- [ ] **Nie** — `VERSION.md` bez zmian, praca poczeka na kolejne wydanie

## Sprzęt, na którym sprawdzono

<!-- Np. „klawiatura Corsair K70, mysz Glorious Model I". Jeśli zmiana dotyka
     dekodowania HID, napisz na jakim urządzeniu ją widziałeś — CI nie ma sprzętu. -->
