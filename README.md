# boxbox

**Formula 1 in your terminal.** Live timing, a track position line, driver battles, standings, results and the
season calendar. One Python file, no dependencies.

```
  ● LIVE   GREEN FLAG                             Bahrain Grand Prix · Race · Lap 29/55 · 1:04:12 left
 OVERTAKE  NOR passes PIA for P7  2s ago

╭─ Track ──────────────────────────────────────────────────────────────────────────────────────────────╮
│                                                         GAS                          STR             │
│     LAW       HAM    HAD    RUS ANT    VER               │ALB            COL          │ LIN NOR      │
│ S/F ●──────────●──────●─●────●───●──────●───────────────●●●●──────────────●──────────●●─●●───●── S/F │
│     PIA                PER                              │ BOR                        │ HUL  OCO      │
│                                                        SAI                          ALO              │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Timing ─────────────────────────────────────────────────────────────────────────────────────────────╮
│ POS   +/-   DRIVER                  GAP      INT  LAST      BEST      TYRE    PIT                    │
│   1     ·   █ VER Verstappen     Leader           1:41.900  1:40.953  (S) 18    1                    │
│   2    ▲1   █ ANT Antonelli      +8.613   +8.613  1:42.190  1:41.337  (M) 19    1                    │
│   3    ▲4   █ RUS Russell       +13.011   +4.391  1:42.739  1:41.536  (M) 19    1                    │
│   4    ▲4   █ HAD Hadjar        +20.801   +7.736  1:43.096  1:41.867  (S) 18    1                    │
│ > 5    ▼3   █ HAM Hamilton      +28.290   +7.667  1:42.276  1:41.727  (S)  1    1                    │
│   6    ▲5   █ LAW Lawson        +40.205  +11.898  1:43.911  1:43.085  (H) 18    1                    │
│   7    ▼1   █ PIA Piastri       +40.654   +0.449  1:43.544  1:42.616  (H) 19    2                    │
│  ...                                                                                                 │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯
╭─ Driver ─────────────────────────────────────────────────────────────────────────────────────────────╮
│ █ HAM  Lewis Hamilton  #44 · Ferrari               ─ BEST                                            │
│ P5  ▼3 from P2  ·  1 stop                          Lap 1:41.727 P5  (lap 25)                         │
│                                                    S1 25.706 P6  S2 34.215 P1  S3 41.388 P11         │
│ ─ BATTLE                                           Top speed 322 km/h P6                             │
│ P4 HAD Hadjar        +7.667  dropping 0.4s/lap                                                       │
│ P5 HAM Hamilton                                    ─ TYRES                                           │
│ P6 LAW Lawson       +11.898                        (S) 24 → (S) 1 now                                │
│                                                                                                      │
│ ─ THIS LAP · KM/H                                  ─ LAP TIMES · TALLER = SLOWER                     │
│ S1 26.136  S2 34.371  S3 41.769                    ▃▁█▆█  5 laps                                     │
│ ▰▰▰▱▱ ▱▱▱▱▱▱ ▱▱▱▱▱▱▱▱▱                                                                               │
│ I1 272  I2 129  Finish 282  Trap 295                                                                 │
╰──────────────────────────────────────────────────────────────────────────────────────────────────────╯
```

*In a real terminal everything is in team colours, with purple and green for fastest overall and personal bests.*

## Features

**Live timing** (`boxbox live`), during practice, qualifying, sprint and race sessions:

- **Status banner** coloured by track status: green flag, yellow, Safety Car, VSC, red flag, chequered.
- **Event pop-ups** for overtakes, pit stops, fastest laps, retirements and Safety Car / red flags.
- **Track line** showing every car as a dot on one lap, so you can see who is physically near whom,
  lapped cars included. Press `g` to switch to a gap view, which shows trains of cars and DRS fights.
- **Timing tower**:
  - positions gained or lost against the grid
  - gap and interval, with intervals under 1s highlighted for DRS range
  - last and best laps
  - live mini-sectors
  - tyre compound and laps since the stop
  - pit stops
- **Driver focus** (`←` / `→`):
  - the battle with the cars ahead and behind, and whether each gap is closing
  - live sector times and speed traps
  - best laps and sectors, each with its rank in the field
  - the full tyre history
  - a lap-time graph
- **Race control** messages, colour-coded.

**Everything else:**

| Command | What it shows |
|---|---|
| `boxbox` | Dashboard: next race with session countdowns, last podium, both championships |
| `boxbox next [--watch]` | Next race weekend, with a live countdown to every session |
| `boxbox drivers` | Driver championship |
| `boxbox teams` | Constructor championship |
| `boxbox schedule` | Season calendar, with sprint weekends marked |
| `boxbox results [ROUND]` | Race result: grid, time or retirement, points, fastest lap |
| `boxbox quali [ROUND]` | Qualifying with Q1 / Q2 / Q3 times |
| `boxbox sprint [ROUND]` | Sprint result |

Options: `-s 2021` for any past season, `-r` to skip the 5-minute cache, `--no-color`, `--version`.
All times are shown in your local timezone.

## Install

You need Python 3.8 or newer. There are no other dependencies.

With [pipx](https://pipx.pypa.io) (recommended):

```bash
pipx install git+https://github.com/bilal-psd/boxbox.git
```

Or with pip:

```bash
pip install git+https://github.com/bilal-psd/boxbox.git
```

Or just grab the single file and run it:

```bash
curl -O https://raw.githubusercontent.com/bilal-psd/boxbox/main/boxbox.py
python3 boxbox.py
```

## Live timing keys

| Key | Action |
|---|---|
| `←` / `→` (or `h` / `l`) | Highlight a driver. Race control is replaced by their driver panel |
| `g` | Switch between track position and gap-to-leader view |
| `esc` | Clear the highlight |
| `q` / `Ctrl+C` | Quit |

The layout adapts to your terminal. On wide screens the side panel sits next to the timing tower. On narrower ones
it moves underneath, and less important columns are hidden. Panels keep a fixed height, so nothing jumps around
while the cars move.

## How it works

- **Standings, results and calendar** come from the [Jolpica F1 API](https://github.com/jolpica/jolpica-f1),
  the community-run successor to Ergast. Responses are cached for 5 minutes in `~/.cache/boxbox`, and if you're
  offline the cached data is used.
- **Live timing** reads Formula 1's public live timing feed, the same one that powers the official live timing
  pages. No account is needed.
- **Track positions are estimates.** The feed's car coordinates need an F1 TV subscription, so boxbox works out
  each car's place on the lap from timing data instead:
  - **Race:** the leader's mini-sector progress, plus each car's gap.
  - **Practice and qualifying:** each car's own mini-sectors.

  They're usually within a second or two of where the car really is. They're less precise behind the Safety Car,
  when lap times change suddenly.

## Limitations

- **Driver history:** F1 locks a session's archive until it ends. So the lap-time graph and "closing / dropping"
  trends in the driver panel only cover laps since you started boxbox.
- **Not available:** car telemetry, the circuit map, team radio and the live championship projection all need an
  F1 TV login.
- **Platforms:** tested on macOS. Linux should behave the same. On Windows, live timing runs without keyboard
  controls; WSL or Windows Terminal is recommended.

## Disclaimer

boxbox is an unofficial fan project. It is not associated in any way with the Formula 1 companies. F1, FORMULA ONE,
FORMULA 1, FIA FORMULA ONE WORLD CHAMPIONSHIP, GRAND PRIX and related marks are trade marks of Formula One Licensing B.V.
Timing data belongs to its respective owners. This tool only displays it for personal use.

## License

[MIT](LICENSE)
