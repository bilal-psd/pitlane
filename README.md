# boxbox

**Formula 1 in your terminal.** Live timing, a track position line, driver battles, standings, results and the
season calendar. One Python file, no dependencies.

![boxbox live timing during the 2026 Bahrain Grand Prix: status banner, track position line, timing tower with mini-sectors and tyres, and race control](https://raw.githubusercontent.com/bilal-psd/boxbox/main/docs/screenshot-live.png)

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

With [pipx](https://pipx.pypa.io) or [uv](https://docs.astral.sh/uv/) (recommended, since they keep boxbox in its own
environment):

```bash
pipx install boxbox
```

```bash
uv tool install boxbox
```

Or try it without installing anything:

```bash
uvx boxbox live
```

Plain `pip install boxbox` works too. You can also grab the single file and run it:

```bash
curl -O https://raw.githubusercontent.com/bilal-psd/boxbox/main/boxbox.py
python3 boxbox.py
```

To update later: `pipx upgrade boxbox` or `uv tool upgrade boxbox`.

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
