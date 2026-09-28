# Plan rozbudowy silnika perkusyjnego Reaper Daemon

**Status:** propozycja implementacyjna do przeglądu (wersja poprawiona po analizie kodu)
**Zakres:** `skills/drum-apparatus/`, `drum_workshop.py`, integracja CLI/MCP/REAPER
**Cel:** od briefu i riffu do kilku sprawdzalnych wariantów aranżacji perkusyjnej, z odsłuchem i kontrolowanym zapisem do projektu.

## 1. Stan wyjściowy

Repozytorium ma silnik DSL i MIDI perkusji (`groovekit.py`), analizę riffu (`riff.py`), profilowanie materiału (`profile.py`), humanizację (`humanize.py`), regułę niepowtarzania velocity (`goldenrule.py`), wykrywanie map zestawu (`mapdetect.py`) i uczenie wzorców (`learn.py`).

`drum_workshop.py` przygotowuje brief i trzy żądania kompozycji (`fresh`, `contrast` albo `reference`, `wildcard`), przyjmuje od agenta `candidate.dsl` i `intent.json`, renderuje je, porównuje strukturę i zapisuje feedback z odsłuchu. Działa offline, nie wywołuje modelu, nie ocenia jakości muzycznej i nie wybiera zwycięzcy.

Stan zweryfikowany w kodzie (baseline):

| Fakt | Miejsce | Skutek dla planu |
|---|---|---|
| 145 testów `skills/drum-apparatus/tests` + `tests/test_drum_workshop.py` przechodzi | — | Baseline Etapu 0 |
| Metrum 4/4 zaszyte w rendererze i warsztacie | `groovekit.py` (`step_qn = 4.0 / grid`, `bars * 4.0`), `drum_workshop.evaluate` (`bars*4*ppq`, `bars*240/tempo`) | MVP obsługuje tylko 4/4 |
| Warsztat przyjmuje 1–64 takty | `drum_workshop.parse` | Dłuższy utwór wymaga decyzji (§3.1) |
| Manifest akceptuje tylko `fresh/contrast/wildcard` lub `fresh/reference/wildcard` i `version == 1` | `drum_workshop.load_workspace` | Nowe role wymagają manifestu v2 |
| Brief ma zamkniętą listę pól; `exclude_families` jest globalne | `drum_workshop.validate_brief` | Nowe pola i zakazy per sekcja wymagają zmiany schematu |
| Rola bez mapowania i bez fallbacku jest **po cichu pomijana** | `groovekit.render` → `pitch_for` zwraca `None` | Realna luka; kontrola mapowania to pierwsza nowa bramka |
| `build()` ustawia na sztywno `humanize: 20` i nie przekazuje parametrów wykonania, choć `render()` przyjmuje `kick_vel_max`, `kick_vel_min`, `kick_run_band` | `groovekit.build` | Performer profile = przekazanie parametrów, nie nowy humanizer |
| `insert_groove` renderuje DSL od nowa (inny seed, bez warsztatowego `enforce()` i przycinania) | `reaperd.render_groove` | Do REAPER-a trafiłaby inna partia niż odsłuchana |
| Istnieje `insert_midi_file` | `reaper_mcp.py` | Zapis zamrożonego `.mid` z ewaluacji |
| `grooves.json` zawiera 50 wzorców wyłącznie kick/snare; `groovekit` celowo go nie używa | `catalog/grooves.json` | Katalog nie wystarczy do strategii talerzy ani fillów |
| `riff.py` i `profile.py` czytają zapisany `.rpp`, nie stan na żywo | `riff.parse_project`, `profile.profile_track` | Wnioski z audio niosą zastrzeżenie o zapisanym projekcie |
| `humanize.detect_fills` wykrywa fille w istniejącym take | `humanize.py` | Do reużycia w ewaluacji fillów |
| Feedback: `usefulness` ∈ `use/revise/reject`, bez sekcji | `drum_workshop.feedback` | Zachować nazwy; `section_id` jako pole opcjonalne |

**Wniosek architektoniczny:** nie budować drugiego warsztatu, drugiego renderera ani drugiego humanizera. Rozszerzyć istniejący przepływ o jawny plan aranżacji, raport kontroli technicznej, iterację per sekcja i zapis dokładnie tej partii, którą użytkownik usłyszał.

## 2. Cel wersji MVP

Użytkownik podaje brief, strukturę utworu, mapę zestawu oraz opcjonalnie riff. System:

1. Tworzy czytelny plan ról perkusji dla sekcji (`ArrangementPlan`).
2. Przekazuje plan do żądań kompozycji; agent pisze 3 kandydatury w istniejącym DSL, w tym obowiązkowy wildcard.
3. Parsuje i renderuje każdą kandydaturę do MIDI.
4. Raportuje błędy, ostrzeżenia i mierzalne cechy aranżacji.
5. Umożliwia odsłuch na tym samym VSTi i zapis decyzji użytkownika.
6. Dopiero po wyborze wstawia **zamrożony plik MIDI z ewaluacji** do REAPER-a i weryfikuje wynik.

```text
Brief + sekcje + opcjonalny riff
               ↓
      ArrangementPlan JSON
               ↓
  request.json × 3 (fresh / contrast|reference / wildcard) z planem sekcji
               ↓
  agent pisze candidate.dsl + intent.json
               ↓
Parser DSL → kontrola techniczna → MIDI (zamrożone)
               ↓
      Odsłuch i feedback (per sekcja)
               ↓
  Wybór → insert_midi_file zamrożonego .mid → weryfikacja
```

### Kto komponuje

Warsztat zakłada, że komponuje agent, a kod planuje i sprawdza. Plan zachowuje ten podział:

- `candidates.py` **nie** składa kandydatur z katalogu wzorców. Wzbogaca `request.json` o plan sekcji, ograniczenia i przejścia.
- Opcjonalny szkic algorytmiczny (np. kick z riffu) może powstać, ale jest traktowany jak materiał referencyjny: trafia tylko do żądania z `reference_access: true`, nigdy do `fresh`.
- Wildcard pozostaje zawsze. Strategie `tight`, `breathing`, `aggressive` są wskazówkami kierunku dla ról, a nie zamiennikiem ról.

### Poza zakresem MVP

- Trenowanie modelu generatywnego i wywoływanie modelu z kodu.
- Automatyczny wybór „najlepszego” wariantu.
- Metrum inne niż 4/4.
- Pełna produkcja i miks perkusji.
- Zapis do projektu bez jawnej decyzji użytkownika.
- Traktowanie analizy riffu jako pewnej transkrypcji intencji.

## 3. Kontrakty danych

### 3.1. ArrangementPlan

Projektowany kontrakt (nie jest jeszcze formatem repozytorium):

```json
{
  "schema_version": 1,
  "tempo": 182,
  "meter": "4/4",
  "kit_map": "RS Monarch",
  "seed": 42,
  "exclude_families": ["choke"],
  "sections": [
    {
      "id": "verse_1",
      "bars": 8,
      "role": "verse",
      "energy": 0.55,
      "kick_strategy": "riff_selective",
      "snare_strategy": "backbeat",
      "cymbal_strategy": "closed_hat",
      "exclude_families": ["ride"],
      "transition_out": "short_pickup",
      "evidence": {
        "section_boundary": "user_explicit",
        "kick_grid": "audio_inferred"
      }
    }
  ]
}
```

Wymagania:

- `schema_version` i jawna walidacja przed renderowaniem; nieznane pola są błędem, tak jak w `validate_brief`.
- `meter` przyjmuje w MVP wyłącznie `"4/4"`; inna wartość to błąd walidacji, nie ciche zignorowanie.
- Sekcje są kolejne i ciągłe, jak `[sekcje]` w DSL. `start_bar` nie jest danymi wejściowymi; jest wyliczany i raportowany. Sekcja planu = sekcja DSL o tym samym `id` i liczbie taktów.
- Suma taktów ≤ 64 (limit warsztatu). Dłuższy utwór dzielony jest na osobne workshopy po grupach sekcji; podniesienie limitu to osobna decyzja z testem wydajności `compare()`.
- `exclude_families` globalne i per sekcja; sekcyjne sumują się z globalnymi. Sprzeczność (nakaz i zakaz tej samej rodziny w sekcji) jest błędem.
- `evidence` przyjmuje `user_explicit`, `audio_inferred` albo `default`. `audio_inferred` zawsze niesie zastrzeżenie, że źródłem był zapisany `.rpp`, a nie stan na żywo.
- Ręczny brief i twarde wykluczenia mają pierwszeństwo nad heurystykami.
- Ten sam plan, wersja generatora i seed dają identyczny wynik.

### 3.2. Workshop i kandydatura

- Manifest `version: 2` rozszerza `version: 1` o `plan` (ArrangementPlan) i `generator_version`. `load_workspace` nadal przyjmuje `version: 1` bez zmian semantyki.
- Role kandydatur bez zmian: `fresh`, `contrast` lub `reference`, `wildcard`. Wildcard jest obowiązkowy.
- Katalog kandydatury: `candidate.dsl` + `intent.json` (`premise`, `development`, `exploration`). Nowy, opcjonalny `evaluation.json` zawiera raport techniczny i nie zastępuje porównania strukturalnego.

### 3.3. Raport ewaluacji

- `error`: blokuje eksport lub zapis (składnia, długość, granice sekcji, zakazana rodzina, rola bez mapowania i fallbacku, Golden Rule niemożliwa do spełnienia).
- `warning`: pozwala odsłuchać (powtarzalność, podejrzany fill, gęste talerze, możliwa kolizja kończyn).
- `info`: surowe metryki (gęstość, artykulacje, liczba filli, relacja kick–riff, role rozwiązane przez fallback).

Bez punktacji `0–100`. Progi `warning` kalibrowane dopiero na podstawie odsłuchów.

### 3.4. Feedback

Zachować obecny format (`usefulness`: `use`/`revise`/`reject`, `novelty`, `reason`, `report`, `scope`). Dodać opcjonalne `section_id` (musi istnieć w planie). Rewizja tworzy nową ewaluację z polem `parent_report`, dzięki któremu zaakceptowane sekcje da się porównać bajt po bajcie.

## 4. Podział odpowiedzialności w kodzie

| Komponent | Rola | Zasada integracji |
|---|---|---|
| `drumgen/arrangement.py` | Walidacja i budowa planu sekcji z briefu i opcjonalnych obserwacji | Bez MIDI i bez REAPER-a |
| `drumgen/evaluate.py` | Kontrole techniczne i raport | Kod wydzielony z `drum_workshop.evaluate`; reużywa `goldenrule`, `parse_dsl`, `detect_fills` |
| `drumgen/candidates.py` | Wzbogacenie `request.json` o plan; opcjonalny szkic jako referencja | Nie komponuje kandydatury `fresh` |
| `drumgen/fills.py` | Kontrola i opis przejść między sekcjami | Po ustaleniu planu; wynik to wskazówki i ostrzeżenia |
| `drumgen/performer.py` | Profile parametrów wykonania | Przekazuje parametry przez `groovekit.build(params=...)` |
| `drum_workshop.py` | Brief, żądania, ewaluacja, feedback | Offline, bez mutacji projektu |
| CLI/MCP | `drum-workshop` / `drum_workshop` | Cienkie adaptery |
| Most REAPER | Zapis zamrożonego `.mid` przez `insert_midi_file` | Brak ponownego renderu DSL przy zapisie |

## 5. Etapy

### Etap 0 — baseline, schematy, fixtures

1. Zapisać baseline: 145 testów drum-apparatus i warsztatu.
2. Fixtures: prosty riff 4/4, blast beat, breakdown z pauzą, dwa zbliżone verse’y, utwór bez riffu, niepełna mapa MIDI (rola bez fallbacku), zakaz rodziny globalny i per sekcja, plan > 64 takty, `meter: "7/8"`.
3. Schematy planu, raportu i manifestu v2; test, że manifest v1 i obecne `groove`, `humanize`, `drum-workshop evaluate` działają bez zmian.
4. Utworzyć `HANDOFF.md` (wymagany przez `AGENTS.md`, obecnie go brak) z bieżącym stanem prac.

**Odbiór:** testy regresyjne przechodzą; żadne istniejące polecenie nie wymaga nowych pól.

### Etap 1 — ewaluacja techniczna

Pierwsza, bo działa od razu dla kandydatur pisanych przez agenta.

| Kontrola | Poziom | Warunek | Stan |
|---|---|---|---|
| Składnia DSL | `error` | Parser przyjmuje plik | Jest, wydzielić |
| Tempo, mapa, takty | `error` | Zgodne z briefem | Jest, wydzielić |
| Twarde wykluczenia | `error` | Brak zakazanej rodziny (globalnie i w sekcji) | Jest globalnie; dodać per sekcja |
| Duplikat wyzwolenia tej samej wysokości | `error` | Brak | Jest, wydzielić |
| Golden Rule velocity | `error` | `violations()` puste po `enforce()` | Jest, wydzielić |
| Zgodność MIDI po odczycie | `error` | Nuty identyczne po `parse_smf` | Jest, wydzielić |
| Mapowanie ról | `error` / `info` | Rola bez mapowania i fallbacku = `error`; rozwiązana przez fallback = `info` | **Nowe** |
| Granice sekcji | `error` | Sekcje DSL = sekcje planu (id, takty) | **Nowe** (gdy jest plan) |
| Powtarzalność | `warning` | Nadmiar identycznych taktów | **Nowe** |
| Kick–riff | `info` / `warning` | Pokrycie onsetów z tolerancją, z oznaczeniem `audio_inferred` | **Nowe** |
| Wykonalność | `warning` | Zachowawcza kolizja kończyn (na bazie `ROLE_LIMB`) | Zastąpić zgrubne liczenie rodzin |

Zadania:

1. Wydzielić logikę z `drum_workshop.evaluate` do `drumgen/evaluate.py` bez zmiany wyniku dla istniejących testów.
2. Mapowanie: `groovekit` zwraca listę ról pominiętych i rozwiązanych przez fallback, żeby nic nie znikało po cichu.
3. Raport JSON + krótka wersja tekstowa.
4. Testy negatywne dla każdej bramki `error` i seria seedów.
5. Test regresji: oszczędny groove nie jest karany za małą liczbę nut.

**Odbiór:** znane błędy są wykrywane, poprawne kandydatury nie są blokowane, raport v1 bez planu jest zgodny wstecznie.

### Etap 2 — plan aranżacji offline

1. Walidacja: zakres taktów (≤ 64), `meter == "4/4"`, tempo 40–320, znana mapa, unikalne `id`, ciągłość sekcji, sprzeczne ograniczenia.
2. Ręczna mapa utworu ma najwyższy priorytet.
3. Opcjonalnie `profile.find_boundaries` proponuje granice, a `riff.riff_to_kicks` punkty kicka; oba jako `audio_inferred` z zastrzeżeniem o zapisanym `.rpp`.
4. Plan bez MIDI i bez REAPER-a.

**Testy:** deterministyczność, priorytet źródeł, odrzucenie 7/8 i > 64 taktów, obsługa braku audio.

**Odbiór:** ten sam brief i seed dają identyczny plan; jawne życzenie użytkownika wygrywa z analizą riffu.

### Etap 3 — żądania kompozycji z planem

1. Manifest v2: `plan`, `generator_version`, skróty SHA-256 wejść.
2. `candidates.py` dopisuje do każdego `request.json` sekcje, zakazy per sekcja, przejścia i kierunek strategii (`tight` → fresh, `breathing` → contrast, `aggressive` jako wskazówka; wildcard dostaje plan bez strategii).
3. Opcjonalny szkic kicka z riffu trafia wyłącznie do żądania `reference`.
4. Ewaluacja sprawdza zgodność sekcji DSL z planem; `compare()` wykrywa warianty pozornie różne.

**Odbiór:** trzy kandydatury z wildcardem parsują się, renderują i spełniają plan; `fresh` nie widzi materiału referencyjnego.

### Etap 4 — fille i profile wykonawcze

**Fille**

1. Rola przejścia: pickup, pauza, stop, tom run, wejście po ciszy.
2. Długość fillu z dostępnego miejsca i energii sekcji docelowej, jako wskazówka w żądaniu.
3. Ewaluacja przez `humanize.detect_fills`: fill nie wychodzi poza sekcję, downbeat zachowany, ten sam fill nie powtarza się automatycznie.

**Profile wykonawcze**

1. `groovekit.build(text, seed, default_map, params=None)`; `None` zachowuje obecne wartości (`humanize: 20`, `KICK_VEL_*`, `KICK_RUN_BAND`, `VOICE_PROFILE`).
2. Profile `tight_modern_metal` i `raw_black_metal` jako zestawy parametrów, zapisywane w raporcie.
3. Limity szybkości to konfiguracja i ostrzeżenie, nie „prawo fizyczne”.

**Odbiór:** profil zmienia charakter partii powtarzalnie; brak profilu daje identyczne MIDI jak dziś.

### Etap 5 — odsłuch, rewizja, zapis

1. Eksport wariantów MIDI i raportów.
2. Odsłuch na tej samej bibliotece VSTi, presecie i porównywalnym poziomie.
3. Feedback `use` / `revise` / `reject` z opcjonalnym `section_id`.
4. Rewizja zmienia tylko wskazaną sekcję; `parent_report` pozwala udowodnić, że pozostałe sekcje się nie zmieniły.
5. Przed zapisem: status mostu, ścieżka po GUID lub zweryfikowanej nazwie, mapa, pozycja, zajętość zakresu (`dry_run` przy niepewności).
6. Zapis: `insert_midi_file` z zamrożonym `evaluation-*/<id>.mid`, nie `insert_groove`. Odczyt wstawionych nut i porównanie z plikiem; jedno Undo cofa zapis.
7. Opcjonalnie pomiary audio po renderze (clipping, poziom talerzy, kick–bas) na tym samym zamrożonym zakresie przed i po; nie są oceną kompozycji. Nie wolno wyciągać wniosków z cichego nagrania.

**Odbiór:** odrzucenie nie zmienia projektu; zapis tylko po wyborze; wstawione nuty są identyczne z odsłuchanym plikiem.

## 6. PR-y i zależności

| PR | Zakres | Zależność | Kryterium ukończenia |
|---|---|---|---|
| 1 | Baseline, schematy, fixtures, `HANDOFF.md` | Brak | 145+ testów przechodzi, manifest v1 bez zmian |
| 2 | `evaluate.py` + kontrola mapowania | PR 1 | Wykrywa naruszenia z fixtures, nie blokuje poprawnych |
| 3 | `arrangement.py` (plan offline) | PR 1 | Plan deterministyczny, walidacja 4/4 i ≤ 64 taktów |
| 4 | Manifest v2 + żądania z planem + granice sekcji | PR 2, PR 3 | 3 kandydatury z wildcardem zgodne z planem |
| 5 | Fille, `build(params=...)`, profile, rewizje, zapis `.mid` | PR 4 | Zapisane nuty = odsłuchany plik; brak profilu = dotychczasowe MIDI |

PR 2 i PR 3 mogą iść równolegle.

**Kamień milowy MVP:** po PR 4 użytkownik dostaje 3 odtwarzalne kandydatury MIDI (w tym wildcard) z raportami i sam decyduje, którą odsłuchać i wykorzystać. PR 5 zamyka pętlę w REAPER-ze.

## 7. Ryzyka i zabezpieczenia

- **Ciche gubienie nut przy niepełnej mapie:** bramka mapowania w PR 2, zanim cokolwiek innego.
- **Odsłuch ≠ zapis:** zapis wyłącznie zamrożonego `.mid`, weryfikacja nut po wstawieniu.
- **Wyciek referencji do `fresh`:** szkice i wzorce katalogowe tylko w żądaniu `reference`.
- **Utrata wildcardu:** manifest v2 odrzuca zestaw ról bez wildcardu.
- **Fałszywa pewność analizy riffu:** `audio_inferred` + zastrzeżenie o zapisanym `.rpp`, ręczna korekta ma pierwszeństwo.
- **Zbyt podobne warianty:** `compare()` i wymagana różnica w `intent.json`.
- **Duplikacja silnika:** nowe moduły planują i oceniają; DSL, renderer, humanizer i most bez zmian semantyki.
- **Pozorna „ocena muzykalności”:** konkretne zjawiska zamiast punktów.
- **Niedeterminizm:** seed, `generator_version`, skróty wejść i parametry profilu w manifeście i raporcie.
- **Zakres 4/4 i 64 taktów:** jawne błędy walidacji zamiast cichego obcięcia.

## 8. Definicja ukończenia wersji 1

Dla zestawu testowych utworów system powtarzalnie tworzy poprawne żądania i raporty, respektuje instrukcje użytkownika i mapę biblioteki (bez cichego gubienia nut), zachowuje wildcard, oznacza niepewne obserwacje, pozwala odsłuchać wynik przed zapisem i wstawia dokładnie odsłuchaną partię. Testy jednostkowe, integracyjne i ręczny test w REAPER-ze przechodzą bez regresji dotychczasowych komend. O wyborze najlepiej brzmiącej wersji decyduje człowiek.
