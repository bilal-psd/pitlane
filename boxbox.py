#!/usr/bin/env python3
"""boxbox - a Formula 1 tracker for your terminal.

Data comes from the Jolpica F1 API (the community successor to Ergast), and
live timing from Formula 1's own public live-timing feed.
No third-party dependencies: runs on any Python 3.8+.
"""

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import shutil
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://api.jolpi.ca/ergast/f1"
CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "boxbox")
CACHE_TTL = 300  # seconds
__version__ = "0.1.0"
USER_AGENT = "boxbox/" + __version__
LIVE_URL = "https://livetiming.formula1.com/signalrcore"

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

USE_COLOR = ("NO_COLOR" not in os.environ
             and (sys.stdout.isatty() or "FORCE_COLOR" in os.environ))

TEAM_COLORS = {
    "red_bull": (54, 113, 198),
    "mercedes": (39, 244, 210),
    "ferrari": (232, 0, 32),
    "mclaren": (255, 128, 0),
    "aston_martin": (34, 153, 113),
    "alpine": (255, 135, 188),
    "williams": (100, 196, 255),
    "rb": (102, 146, 255),
    "sauber": (82, 226, 82),
    "audi": (190, 190, 190),
    "haas": (182, 186, 189),
    "cadillac": (200, 170, 90),
    # Older names, for past seasons.
    "alphatauri": (78, 124, 155),
    "alfa": (155, 0, 0),
    "racing_point": (245, 150, 200),
    "renault": (255, 245, 0),
    "toro_rosso": (70, 155, 255),
    "force_india": (245, 150, 200),
}


def _sgr(code, text):
    if not USE_COLOR:
        return text
    return "\033[{}m{}\033[0m".format(code, text)


def bold(t):
    return _sgr("1", t)


def dim(t):
    return _sgr("2", t)


def red(t):
    return _sgr("38;2;225;6;0", t)


def green(t):
    return _sgr("32", t)


def yellow(t):
    return _sgr("33", t)


def team_color(constructor_id, text):
    rgb = TEAM_COLORS.get(constructor_id)
    if rgb is None:
        return text
    return _sgr("38;2;{};{};{}".format(*rgb), text)


def team_bar(constructor_id):
    """A small coloured block used as a team marker."""
    if not USE_COLOR:
        return "|"
    return team_color(constructor_id, "█")


PODIUM = {"1": (255, 215, 0), "2": (192, 192, 192), "3": (205, 127, 50)}


def pos_style(pos):
    text = str(pos).rjust(2)
    rgb = PODIUM.get(str(pos))
    if rgb and USE_COLOR:
        return _sgr("1;38;2;{};{};{}".format(*rgb), text)
    return text


# ---------------------------------------------------------------------------
# Layout: panels, side-by-side blocks, ANSI-aware widths
# ---------------------------------------------------------------------------

ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
RESET = "\033[0m"
SECTION = "\x00"  # marks a section title inside a list of lines


def vlen(s):
    """Visible length of a string, ignoring colour codes."""
    return len(ANSI_RE.sub("", s))


def vpad(s, n):
    return s + " " * max(0, n - vlen(s))


def vrjust(s, n):
    return " " * max(0, n - vlen(s)) + s


def vtrunc(s, n):
    """Truncate to n visible characters without breaking colour codes."""
    if vlen(s) <= n:
        return s
    out, count, i = [], 0, 0
    while i < len(s) and count < n - 1:
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        out.append(s[i])
        count += 1
        i += 1
    return "".join(out) + "…" + (RESET if USE_COLOR else "")


def term_size():
    size = shutil.get_terminal_size((100, 40))
    return size.columns, size.lines


def rgb_fg(rgb, text):
    return _sgr("38;2;{};{};{}".format(*rgb), text)


def badge(text, rgb, fg=(0, 0, 0)):
    """A solid coloured label, e.g. a flag or event tag."""
    if not USE_COLOR:
        return "[" + text + "]"
    return _sgr("1;38;2;{};{};{};48;2;{};{};{}".format(*(fg + rgb)),
                " " + text + " ")


def panel(title, lines, width):
    inner = width - 4
    t = " {} ".format(title) if title else ""
    top = dim("╭─") + bold(t) + dim(
        "─" * max(0, width - 3 - vlen(t)) + "╮")
    body = [dim("│") + " " + vpad(vtrunc(l, inner), inner) + " "
            + dim("│") for l in lines]
    bottom = dim("╰" + "─" * (width - 2) + "╯")
    return [top] + body + [bottom]


def hjoin(blocks, gap=1):
    blocks = [b for b in blocks if b]
    if not blocks:
        return []
    widths = [max(vlen(l) for l in b) for b in blocks]
    height = max(len(b) for b in blocks)
    return [(" " * gap).join(vpad(b[i] if i < len(b) else "", w)
                             for b, w in zip(blocks, widths))
            for i in range(height)]


def split_sections(lines):
    pre, secs = [], []
    for l in lines:
        if l.startswith(SECTION):
            secs.append((l[1:], []))
        elif secs:
            secs[-1][1].append(l)
        else:
            pre.append(l)
    for i, (title, body) in enumerate(secs):
        while body and body[0] == "":
            body.pop(0)
        while body and body[-1] == "":
            body.pop()
        # The panel border provides the margin, so drop the shared indent.
        plain = [ANSI_RE.sub("", l) for l in body if ANSI_RE.sub("", l).strip()]
        indent = min([len(p) - len(p.lstrip(" ")) for p in plain] or [0])
        secs[i] = (title, [_drop_spaces(l, indent) for l in body])
    return pre, secs


def _drop_spaces(s, n):
    """Remove the first n visible spaces, keeping any colour codes."""
    out, i = [], 0
    while i < len(s) and n:
        m = ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
        elif s[i] == " ":
            n -= 1
            i += 1
        else:
            break
    return "".join(out) + s[i:]


def natural_width(title, body):
    return max([vlen(l) for l in body] + [vlen(title) + 4]) + 4


def boxed(lines):
    """Turn header-marked lines into panels of equal width."""
    pre, secs = split_sections(lines)
    width = min(term_size()[0],
                max([natural_width(t, b) for t, b in secs] + [40]))
    out = list(pre)
    for title, body in secs:
        out += panel(title, body, width)
    return out


def layout(rows):
    """Each row is a list of header-marked line lists. A row's panels sit
    side by side when the terminal is wide enough, otherwise they stack."""
    tw = term_size()[0]
    out = []
    for row in rows:
        secs = [s for part in row for s in split_sections(part)[1]]
        if not secs:
            continue
        widths = [natural_width(t, b) for t, b in secs]
        if len(secs) > 1 and sum(widths) + len(secs) - 1 <= tw:
            height = max(len(b) for _, b in secs)
            out += hjoin([panel(t, b + [""] * (height - len(b)), w)
                          for (t, b), w in zip(secs, widths)])
        else:
            w = min(tw, max(widths))
            for t, b in secs:
                out += panel(t, b, w)
    return out


# ---------------------------------------------------------------------------
# Data fetching (with a small on-disk cache)
# ---------------------------------------------------------------------------


class ApiError(Exception):
    pass


def fetch(path, ttl=CACHE_TTL, refresh=False):
    url = "{}/{}.json".format(API, path.strip("/"))
    key = hashlib.sha1(url.encode()).hexdigest()
    cache_file = os.path.join(CACHE_DIR, key + ".json")

    if not refresh and os.path.exists(cache_file):
        if time.time() - os.path.getmtime(cache_file) < ttl:
            with open(cache_file) as f:
                return json.load(f)

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, OSError, ValueError) as e:
        # Fall back to stale cache if we have one.
        if os.path.exists(cache_file):
            with open(cache_file) as f:
                return json.load(f)
        raise ApiError("Could not reach the F1 API ({}): {}".format(url, e))

    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(data, f)
    except OSError:
        pass
    return data


def mrdata(d):
    return d["MRData"] if "MRData" in d else d


def get_schedule(season, refresh=False):
    return mrdata(fetch(str(season), refresh=refresh))["RaceTable"]["Races"]


def get_driver_standings(season, refresh=False):
    d = mrdata(fetch("{}/driverstandings".format(season), refresh=refresh))
    lists = d["StandingsTable"]["StandingsLists"]
    return lists[0] if lists else None


def get_constructor_standings(season, refresh=False):
    d = mrdata(fetch("{}/constructorstandings".format(season), refresh=refresh))
    lists = d["StandingsTable"]["StandingsLists"]
    return lists[0] if lists else None


def get_session_results(season, rnd, kind, refresh=False):
    """kind is one of results / qualifying / sprint."""
    d = mrdata(fetch("{}/{}/{}".format(season, rnd, kind), refresh=refresh))
    races = d["RaceTable"]["Races"]
    return races[0] if races else None


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

SESSION_KEYS = [
    ("FirstPractice", "Practice 1"),
    ("SecondPractice", "Practice 2"),
    ("ThirdPractice", "Practice 3"),
    ("SprintQualifying", "Sprint Quali"),
    ("SprintShootout", "Sprint Shootout"),
    ("Sprint", "Sprint"),
    ("Qualifying", "Qualifying"),
]


def parse_dt(date, t=None):
    t = (t or "00:00:00Z").replace("Z", "+00:00")
    return datetime.fromisoformat("{}T{}".format(date, t))


def race_dt(race):
    return parse_dt(race["date"], race.get("time"))


def sessions(race):
    out = []
    for key, label in SESSION_KEYS:
        if key in race:
            s = race[key]
            out.append((label, parse_dt(s["date"], s.get("time"))))
    out.append(("Race", race_dt(race)))
    out.sort(key=lambda x: x[1])
    return out


def local(dt):
    return dt.astimezone()


def fmt_local(dt):
    return local(dt).strftime("%a %d %b  %H:%M")


def fmt_delta(delta_seconds):
    s = int(delta_seconds)
    if s < 0:
        return "started"
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    mins, secs = divmod(s, 60)
    if days:
        return "{}d {:02d}h {:02d}m".format(days, hours, mins)
    if hours:
        return "{}h {:02d}m {:02d}s".format(hours, mins, secs)
    return "{}m {:02d}s".format(mins, secs)


def now_utc():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def header(title):
    """Start a section; emit() draws each section as a boxed panel."""
    return SECTION + title


def trunc(s, n):
    return s if len(s) <= n else s[: n - 1] + "…"


def render_driver_standings(season, limit=None, refresh=False):
    st = get_driver_standings(season, refresh)
    if not st:
        return [header("Driver standings"), dim("  No standings yet.")]
    rows = st["DriverStandings"][:limit] if limit else st["DriverStandings"]
    leader = float(rows[0]["points"]) if rows else 0
    out = [header("Driver standings · {} after round {}".format(
        st["season"], st["round"]))]
    out.append(dim("  POS  DRIVER                    TEAM              PTS    GAP  WINS"))
    for r in rows:
        d = r["Driver"]
        c = r["Constructors"][-1] if r["Constructors"] else {}
        cid = c.get("constructorId", "")
        name = trunc("{} {}".format(d["givenName"], d["familyName"]), 22)
        pts = float(r["points"])
        gap = "" if pts == leader else "-{:g}".format(leader - pts)
        out.append("  {}  {} {}  {}  {:>5}  {:>6}  {:>3}".format(
            pos_style(r.get("positionText", r.get("position", "-"))),
            team_bar(cid),
            bold(name.ljust(22)),
            team_color(cid, trunc(c.get("name", ""), 16).ljust(16)),
            "{:g}".format(pts),
            dim(gap.rjust(6)) if gap else "      ",
            r["wins"],
        ))
    return out


def render_constructor_standings(season, refresh=False):
    st = get_constructor_standings(season, refresh)
    if not st:
        return [header("Constructor standings"), dim("  No standings yet.")]
    rows = st["ConstructorStandings"]
    leader = float(rows[0]["points"]) if rows else 0
    out = [header("Constructor standings · {} after round {}".format(
        st["season"], st["round"]))]
    out.append(dim("  POS  TEAM                       PTS    GAP  WINS"))
    for r in rows:
        c = r["Constructor"]
        cid = c["constructorId"]
        pts = float(r["points"])
        gap = "" if pts == leader else "-{:g}".format(leader - pts)
        out.append("  {}  {} {}  {:>5}  {:>6}  {:>3}".format(
            pos_style(r.get("positionText", r.get("position", "-"))),
            team_bar(cid),
            bold(team_color(cid, trunc(c["name"], 22).ljust(22))),
            "{:g}".format(pts),
            dim(gap.rjust(6)) if gap else "      ",
            r["wins"],
        ))
    return out


def render_schedule(season, refresh=False):
    races = get_schedule(season, refresh)
    now = now_utc()
    next_round = None
    for r in races:
        if race_dt(r) > now - _race_window():
            next_round = r["round"]
            break
    year = races[0]["season"] if races else season
    out = [header("{} calendar".format(year))]
    out.append(dim("  RND  DATE (local)          GRAND PRIX                       LOCATION"))
    for r in races:
        dt = race_dt(r)
        loc = r["Circuit"]["Location"]
        place = "{}, {}".format(loc["locality"], loc["country"])
        sprint = " " + yellow("S") if "Sprint" in r else "  "
        line = "  {:>3}  {}  {}{}  {}".format(
            r["round"], fmt_local(dt).ljust(20),
            trunc(r["raceName"], 30).ljust(30), sprint, trunc(place, 28))
        if r["round"] == next_round:
            line = bold(red("▶")) + bold(line[1:])
        elif dt < now:
            line = dim(line)
        out.append(line)
    out.append(dim("  S = sprint weekend · ▶ = next / current"))
    return out


def _race_window():
    return timedelta(hours=3)


def find_next_race(season, refresh=False):
    """Return the current/next race, treating a race as 'current' for a few
    hours after lights out so results day still shows the weekend."""
    races = get_schedule(season, refresh)
    now = now_utc()
    for r in races:
        if race_dt(r) > now - _race_window():
            return r
    return None


def render_next_race(season, refresh=False):
    race = find_next_race(season, refresh)
    if not race:
        return [header("Next race"), dim("  Season complete. See you next year!")]
    now = now_utc()
    loc = race["Circuit"]["Location"]
    out = [header("Round {} · {}".format(race["round"], race["raceName"]))]
    out.append("  " + race["Circuit"]["circuitName"] + dim(
        " · {}, {}".format(loc["locality"], loc["country"])))
    out.append("")
    upcoming_marked = False
    for label, dt in sessions(race):
        delta = (dt - now).total_seconds()
        # Races can run long with red flags; other sessions are about an hour.
        window = 4 * 3600 if label in ("Race", "Sprint") else 90 * 60
        if delta < -window:
            status = dim("done")
            line = dim("  {}  {}".format(label.ljust(16), fmt_local(dt)))
        elif delta < 0:
            status = badge("LIVE?", (225, 6, 0), (255, 255, 255)) + dim(
                "  try: boxbox live")
            line = "  {}  {}".format(bold(label.ljust(16)), fmt_local(dt))
        else:
            status = green("in " + fmt_delta(delta))
            if not upcoming_marked:
                status = bold(status)
                upcoming_marked = True
            line = "  {}  {}".format(label.ljust(16), fmt_local(dt))
        out.append("{}   {}".format(line, status))
    tz = local(now).strftime("%Z")
    out.append(dim("  Times shown in your local timezone ({}).".format(tz)))
    return out


def _result_time(r):
    if "Time" in r and r["Time"].get("time"):
        return r["Time"]["time"]
    return r.get("status", "")


def render_results(season, rnd, kind="results", refresh=False):
    race = get_session_results(season, rnd, kind, refresh)
    names = {"results": "Race result", "qualifying": "Qualifying",
             "sprint": "Sprint result"}
    if not race:
        return [header(names[kind]),
                dim("  No {} data for {} round {} yet.".format(
                    names[kind].lower(), season, rnd))]
    out = [header("{} · R{} {}".format(names[kind], race["round"],
                                            race["raceName"]))]

    if kind == "qualifying":
        rows = race["QualifyingResults"]
        out.append(dim("  POS  DRIVER                    TEAM              Q1        Q2        Q3"))
        for r in rows:
            d, c = r["Driver"], r["Constructor"]
            cid = c["constructorId"]
            out.append("  {}  {} {}  {}  {:<9} {:<9} {}".format(
                pos_style(r["position"]), team_bar(cid),
                bold(trunc("{} {}".format(d["givenName"], d["familyName"]), 22).ljust(22)),
                team_color(cid, trunc(c["name"], 16).ljust(16)),
                r.get("Q1", ""), r.get("Q2", ""), bold(r.get("Q3", "")),
            ))
        return out

    key = "SprintResults" if kind == "sprint" else "Results"
    rows = race[key]
    fastest = None
    out.append(dim("  POS  DRIVER                    TEAM              GRID  TIME/STATUS     PTS"))
    for r in rows:
        d, c = r["Driver"], r["Constructor"]
        cid = c["constructorId"]
        if r.get("FastestLap", {}).get("rank") == "1":
            fastest = (d, r["FastestLap"])
        grid = r.get("grid", "")
        grid = "PL" if grid == "0" else grid
        pts = r.get("points", "0")
        pos = r["positionText"] if not r["positionText"].isdigit() else r["position"]
        time_s = _result_time(r)
        if not (r["positionText"].isdigit()):
            time_s = red(trunc(time_s, 14).ljust(14))
        else:
            time_s = trunc(time_s, 14).ljust(14)
        out.append("  {}  {} {}  {}  {:>4}  {}  {:>3}".format(
            pos_style(pos), team_bar(cid),
            bold(trunc("{} {}".format(d["givenName"], d["familyName"]), 22).ljust(22)),
            team_color(cid, trunc(c["name"], 16).ljust(16)),
            grid, time_s, pts if pts != "0" else dim("0"),
        ))
    if fastest:
        d, fl = fastest
        out.append(dim("  Fastest lap: ") + "{} {} {}".format(
            d["givenName"], d["familyName"],
            dim("{} (lap {})".format(fl["Time"]["time"], fl.get("lap", "?")))))
    return out


def render_last_podium(season, refresh=False):
    try:
        last = mrdata(fetch("{}/last/results".format(season), refresh=refresh))
        r = last["RaceTable"]["Races"][0]
    except (ApiError, KeyError, IndexError):
        return []
    out = [header("Last race \u00b7 R{} {}".format(r["round"], r["raceName"]))]
    for p in r["Results"][:3]:
        d, c = p["Driver"], p["Constructor"]
        out.append("{}  {} {}  {}".format(
            pos_style(p["position"]), team_bar(c["constructorId"]),
            bold("{} {}".format(d["givenName"], d["familyName"]).ljust(22)),
            team_color(c["constructorId"], c["name"])))
    return out


def render_dashboard(season, refresh=False):
    rows = [
        [render_next_race(season, refresh), render_last_podium(season, refresh)],
        [render_driver_standings(season, limit=10, refresh=refresh),
         render_constructor_standings(season, refresh)],
    ]
    return layout(rows) + ["", dim(
        "  Try: live \u00b7 drivers \u00b7 teams \u00b7 schedule \u00b7 results "
        "\u00b7 quali \u00b7 next --watch")]


# ---------------------------------------------------------------------------
# Live timing (F1's public SignalR Core feed, read over Server-Sent Events)
# ---------------------------------------------------------------------------

LIVE_TOPICS = [
    "SessionInfo", "SessionStatus", "TrackStatus", "LapCount",
    "ExtrapolatedClock", "DriverList", "TimingData", "TimingAppData",
    "RaceControlMessages", "TimingStats",
]
LIVE_STATUSES = ("Started", "Aborted")  # Aborted = red-flag suspension

TRACK_STATUS = {
    "1": ("GREEN", "32"),
    "2": ("YELLOW", "1;33"),
    "4": ("SAFETY CAR", "1;33"),
    "5": ("RED FLAG", "1;31"),
    "6": ("VSC", "1;33"),
    "7": ("VSC ENDING", "33"),
}

COMPOUNDS = {
    "SOFT": ("S", "1;38;2;225;6;0"),
    "MEDIUM": ("M", "1;38;2;255;210;0"),
    "HARD": ("H", "1;38;2;240;240;240"),
    "INTERMEDIATE": ("I", "1;38;2;67;176;42"),
    "WET": ("W", "1;38;2;0;103;173"),
}

PURPLE = "1;38;2;180;90;255"


def merge(base, update):
    """Apply a SignalR partial update. Lists are patched with dicts whose
    keys are string indexes, e.g. {"Sectors": {"1": {...}}}."""
    if isinstance(base, dict) and isinstance(update, dict):
        for k, v in update.items():
            base[k] = merge(base[k], v) if k in base else v
        return base
    if isinstance(base, list) and isinstance(update, dict):
        for k, v in update.items():
            try:
                i = int(k)
            except ValueError:
                continue
            if i < len(base):
                base[i] = merge(base[i], v)
            else:
                base.extend([None] * (i - len(base)))
                base.append(v)
        return base
    return update


class LiveFeed(object):
    """Holds the live state and keeps it updated from a background thread."""

    def __init__(self):
        self.state = {}
        self.lock = threading.Lock()
        self.connected = False
        self.error = None
        self.ready = threading.Event()
        self.last_msg = 0.0

    def _connect(self):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
        headers = {"User-Agent": USER_AGENT}
        neg = json.load(opener.open(urllib.request.Request(
            LIVE_URL + "/negotiate?negotiateVersion=1", data=b"",
            method="POST", headers=headers), timeout=15))
        url = "{}?id={}".format(
            LIVE_URL, urllib.parse.quote(neg["connectionToken"]))
        stream = opener.open(urllib.request.Request(
            url, headers=dict(headers, Accept="text/event-stream")),
            timeout=60)

        def send(obj):
            body = (json.dumps(obj) + "\x1e").encode()
            opener.open(urllib.request.Request(
                url, data=body, method="POST", headers=headers),
                timeout=15).read()

        send({"protocol": "json", "version": 1})
        send({"type": 1, "target": "Subscribe",
              "arguments": [LIVE_TOPICS], "invocationId": "0"})
        return stream

    def _handle(self, msg):
        kind = msg.get("type")
        if kind == 3 and msg.get("invocationId") == "0":
            if msg.get("error"):
                raise ApiError("live feed refused subscription: "
                               + str(msg["error"]))
            with self.lock:
                self.state = msg.get("result") or {}
            self.ready.set()
        elif kind == 1 and msg.get("target") == "feed":
            topic, data = msg["arguments"][0], msg["arguments"][1]
            with self.lock:
                if topic in self.state:
                    self.state[topic] = merge(self.state[topic], data)
                else:
                    self.state[topic] = data
        elif kind == 7:  # server closed the connection
            raise ApiError("live feed closed: " + str(msg.get("error", "")))

    def run(self):
        while True:
            try:
                stream = self._connect()
                self.connected, self.error = True, None
                for raw in stream:
                    self.last_msg = time.time()
                    if not raw.startswith(b"data:"):
                        continue
                    for part in raw[5:].strip().split(b"\x1e"):
                        if part:
                            self._handle(json.loads(part.decode("utf-8")))
            except Exception as e:  # keep the screen up and reconnect
                self.error = str(e)
            self.connected = False
            self.ready.set()
            time.sleep(3)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))


def _fmt_rgb(hex_colour, text):
    try:
        r, g, b = (int(hex_colour[i:i + 2], 16) for i in (0, 2, 4))
    except (TypeError, ValueError):
        return text
    return _sgr("38;2;{};{};{}".format(r, g, b), text)


def _val(x):
    if isinstance(x, dict):
        return x.get("Value", "") or ""
    return x or ""


def _lap_seconds(t):
    try:
        parts = t.split(":")
        secs = float(parts[-1])
        if len(parts) > 1:
            secs += 60 * int(parts[-2])
        return secs
    except (ValueError, AttributeError):
        return None


def _best_lap(line):
    best = _val(line.get("BestLapTime"))
    if not best:  # qualifying keeps one best lap per segment
        laps = line.get("BestLapTimes") or []
        if isinstance(laps, dict):
            laps = [laps[k] for k in sorted(laps, key=int)]
        for lap in reversed(laps):
            if _val(lap):
                return _val(lap)
    return best


def session_clock(ec):
    """Time remaining, ticking down locally while the feed says it's running."""
    try:
        h, m, sec = (int(x) for x in ec["Remaining"].split(":"))
    except (KeyError, ValueError):
        return ""
    left = h * 3600 + m * 60 + sec
    if ec.get("Extrapolating") and ec.get("Utc"):
        stamp = datetime.fromisoformat(ec["Utc"].rstrip("Z")[:26]).replace(
            tzinfo=timezone.utc)
        left -= int((now_utc() - stamp).total_seconds())
    if left <= 0:
        return ""
    h, rem = divmod(left, 3600)
    return "{}:{:02d}:{:02d}".format(h, *divmod(rem, 60))


def _seconds(v):
    """'+12.345' -> 12.345; '1 L', 'LAP 32', '' -> None."""
    try:
        return float(str(v).lstrip("+"))
    except ValueError:
        return None


def _lap_progress(view, num, line, lap_time):
    """Fraction of the current lap completed, from the mini-sector lights,
    interpolated by the time since the last one lit up."""
    sectors = line.get("Sectors") or []
    if isinstance(sectors, dict):
        sectors = [sectors[k] for k in sorted(sectors, key=int)]
    segs = []
    for sec in sectors:
        sg = (sec or {}).get("Segments") or []
        if isinstance(sg, dict):
            sg = [sg[k] for k in sorted(sg, key=int)]
        segs.extend(sg)
    if not segs:
        return None
    done = sum(1 for sg in segs if sg and sg.get("Status"))
    now = time.time()
    prev = view.segments.get(num)
    if prev is None or prev[0] != done:
        view.segments[num] = prev = (done, now)
    seg_time = lap_time / len(segs) if lap_time else 5.0
    within = min(0.95, (now - prev[1]) / seg_time) if done < len(segs) else 0
    return min(0.999, (done + within) / len(segs))


def car_positions(state, view, is_race):
    """Return [(num, fraction 0..1)] for cars on track.

    Race: the leader's lap progress, minus each car's cumulative interval
    converted to a share of the lap - accurate to the timing gaps.
    Other sessions: each car's own mini-sector progress."""
    timing = state.get("TimingData", {}).get("Lines", {})
    order = sorted(timing, key=lambda n: _int(timing[n].get("Position"), 99))
    running = [n for n in order
               if not (timing[n].get("Retired") or timing[n].get("Stopped"))]

    if view.mode == "gap" or is_race:
        cum, gaps = 0.0, {}
        for i, n in enumerate(running):
            if i:
                iv = _seconds(_val(timing[n].get("IntervalToPositionAhead")))
                if iv is None:
                    iv = _seconds(_val(timing[n].get("TimeDiffToPositionAhead"))) or 0.0
                cum += iv
            gaps[n] = cum
        if view.mode == "gap":
            span = max(gaps.values() or [0]) or 1.0
            return [(n, gaps[n] / span) for n in running], span
        leader = running[0] if running else None
        lap = _lap_seconds(_val(timing[leader].get("LastLapTime"))) if leader else None
        lap = lap or _lap_seconds(_best_lap(timing[leader])) if leader else None
        lap = lap or 90.0
        lead = _lap_progress(view, leader, timing[leader], lap) if leader else 0
        if lead is None:
            return [], lap
        return [(n, (lead - gaps[n] / lap) % 1.0) for n in running], lap

    out = []
    for n in running:
        line = timing[n]
        if line.get("InPit") or line.get("KnockedOut"):
            continue
        lap = _lap_seconds(_val(line.get("LastLapTime"))) \
            or _lap_seconds(_best_lap(line)) or 90.0
        p = _lap_progress(view, n, line, lap)
        if p is not None:
            out.append((n, p))
    return out, None


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class LiveView(object):
    """Per-screen state for the live view: keyboard choices, recent events,
    and the history needed for interpolation and the driver panel."""

    def __init__(self):
        self.mode = "track"   # or "gap"
        self.focus = None     # racing number of the highlighted driver
        self.order = []       # racing numbers in running order
        self.segments = {}    # num -> (segments done this lap, when it changed)
        self.scale = None     # seconds spanned by the gap view
        self.prev = None      # last snapshot summary, for spotting events
        self.events = []      # (time, kind, text)
        self.history = {}     # num -> [(lap, lap seconds, interval seconds)]
        self.best = None      # (seconds, num, text) of the fastest lap
        self.rc_seen = None

    def cycle_focus(self, step):
        if not self.order:
            return
        if self.focus not in self.order:
            self.focus = self.order[0] if step > 0 else self.order[-1]
            return
        i = self.order.index(self.focus) + step
        self.focus = None if i < 0 or i >= len(self.order) else self.order[i]

    def add_event(self, kind, text):
        self.events.append((time.time(), kind, text))
        del self.events[:-30]

    def observe(self, state, is_race):
        """Compare with the previous frame to spot overtakes, pit stops,
        retirements, fastest laps and safety car / flag messages."""
        timing = state.get("TimingData", {}).get("Lines", {})
        drivers = state.get("DriverList", {})
        app = state.get("TimingAppData", {}).get("Lines", {})

        def tla(n):
            return drivers.get(n, {}).get("Tla", n)

        snap = {}
        for n, l in timing.items():
            snap[n] = {
                "pos": _int(l.get("Position"), 99),
                "pit": bool(l.get("InPit") or l.get("PitOut")),
                "inpit": bool(l.get("InPit")),
                "out": bool(l.get("Retired") or l.get("Stopped")),
            }
        prev = self.prev
        if prev:
            if is_race:
                for a in snap:
                    if a not in prev or snap[a]["pos"] >= prev[a]["pos"]:
                        continue
                    for b in snap:
                        if b == a or b not in prev:
                            continue
                        if not (prev[a]["pos"] > prev[b]["pos"]
                                and snap[a]["pos"] < snap[b]["pos"]):
                            continue
                        involved = (snap[a], snap[b], prev[a], prev[b])
                        if any(x["pit"] or x["out"] for x in involved):
                            continue  # places swapped in the pits, not on track
                        self.add_event("overtake", "{} passes {} for P{}".format(
                            bold(tla(a)), tla(b), snap[a]["pos"]))
            for n, s in snap.items():
                p = prev.get(n)
                if not p:
                    continue
                if s["inpit"] and not p["inpit"]:
                    self.add_event("pit", "{} into the pits".format(bold(tla(n))))
                elif p["inpit"] and not s["inpit"]:
                    stint = _last_stint(app.get(n))
                    on = " on " + tyre_badge(stint) if stint else ""
                    self.add_event("pit", "{} out of the pits{}".format(
                        bold(tla(n)), on))
                if s["out"] and not p["out"]:
                    self.add_event("out", "{} has stopped".format(bold(tla(n))))

        best = None
        for n, l in timing.items():
            t = _lap_seconds(_best_lap(l))
            if t and (best is None or t < best[0]):
                best = (t, n, _best_lap(l))
        if best and self.best and best[0] < self.best[0] - 1e-6:
            self.add_event("fastest", "{} {}".format(bold(tla(best[1])), best[2]))
        if best:
            self.best = best

        msgs = _as_list(state.get("RaceControlMessages", {}).get("Messages"))
        if self.rc_seen is not None:
            for m in msgs[self.rc_seen:]:
                text = (m or {}).get("Message", "")
                if (m and (m.get("Category") == "SafetyCar"
                           or m.get("Flag") in ("RED", "CHEQUERED")
                           or "SAFETY CAR" in text or "RED FLAG" in text)):
                    self.add_event("control", text)
        self.rc_seen = len(msgs)

        for n, l in timing.items():
            laps = l.get("NumberOfLaps")
            last = _lap_seconds(_val(l.get("LastLapTime")))
            hist = self.history.setdefault(n, [])
            if laps and last and (not hist or hist[-1][0] != laps):
                hist.append((laps, last, _seconds(
                    _val(l.get("IntervalToPositionAhead")))))
                del hist[:-30]
        self.prev = snap


def _as_list(x):
    """Feed lists sometimes arrive as {"0": ..., "1": ...}."""
    if isinstance(x, dict):
        return [x[k] for k in sorted(x, key=lambda k: _int(k, 0))]
    return x or []


def _last_stint(app_line):
    stints = _as_list((app_line or {}).get("Stints"))
    return stints[-1] if stints else None


def tyre_badge(stint):
    letter, code = COMPOUNDS.get(stint.get("Compound", ""), ("?", "0"))
    return _sgr(code, "(" + letter + ")")


def tyre_cell(stint):
    """(S) 12 : compound and laps since it was fitted."""
    if not stint:
        return " " * 6
    laps = _int(stint.get("TotalLaps"), 0) - _int(stint.get("StartLaps"), 0)
    return "{} {:>2}".format(tyre_badge(stint), laps)


SEG_COLOURS = {2048: (255, 200, 0), 2049: (0, 200, 80),
               2051: (180, 90, 255), 2064: (90, 160, 255)}


def mini_sectors(line):
    parts = []
    for sec in _as_list(line.get("Sectors")):
        chars = []
        for sg in _as_list((sec or {}).get("Segments")):
            status = (sg or {}).get("Status", 0)
            if not status:
                chars.append(dim("▱") if USE_COLOR else ".")
            elif USE_COLOR:
                chars.append(rgb_fg(SEG_COLOURS.get(status, (255, 200, 0)),
                                    "▰"))
            else:
                chars.append("#")
        parts.append("".join(chars))
    return " ".join(parts)


EVENT_STYLE = {
    "overtake": ("OVERTAKE", (0, 200, 120), (0, 0, 0)),
    "pit": ("PIT", (90, 160, 255), (0, 0, 0)),
    "fastest": ("FASTEST LAP", (150, 60, 230), (255, 255, 255)),
    "out": ("OUT", (225, 6, 0), (255, 255, 255)),
    "control": ("RACE CONTROL", (250, 200, 0), (0, 0, 0)),
}


def render_events(view, n=2):
    now = time.time()
    recent = [e for e in reversed(view.events) if now - e[0] < 30][:n]
    lines = []
    for t, kind, text in recent:
        label, rgb, fg = EVENT_STYLE[kind]
        age = int(now - t)
        if age >= 8:  # fade older events
            text = dim(ANSI_RE.sub("", text))
            label_s = dim("[" + label + "]")
        else:
            label_s = badge(label, rgb, fg)
        lines.append(" " + label_s + " " + text + dim("  {}s ago".format(age)))
    return lines + [""] * (n - len(lines))


FLAG_BANNER = {
    "1": ("GREEN FLAG", (0, 135, 60), (255, 255, 255)),
    "2": ("YELLOW FLAG", (250, 200, 0), (0, 0, 0)),
    "4": ("SAFETY CAR", (250, 200, 0), (0, 0, 0)),
    "5": ("RED FLAG", (200, 0, 0), (255, 255, 255)),
    "6": ("VIRTUAL SAFETY CAR", (250, 200, 0), (0, 0, 0)),
    "7": ("VSC ENDING", (250, 200, 0), (0, 0, 0)),
}


def status_banner(state, width):
    info = state.get("SessionInfo", {})
    status = state.get("SessionStatus", {}).get("Status", "")
    if status in ("Finished", "Finalised", "Ends"):
        label, rgb, fg = "CHEQUERED FLAG", (230, 230, 230), (0, 0, 0)
    elif status in LIVE_STATUSES:
        label, rgb, fg = FLAG_BANNER.get(
            state.get("TrackStatus", {}).get("Status"),
            ("TRACK STATUS ?", (90, 90, 90), (255, 255, 255)))
    else:
        label, rgb, fg = status.upper() or "WAITING", (90, 90, 90), (255, 255, 255)
    live = "● LIVE   " if status in LIVE_STATUSES else ""
    left = "  {}{}  ".format(live, label)

    right = [info.get("Meeting", {}).get("Name", ""), info.get("Name", "")]
    laps = state.get("LapCount", {})
    if laps.get("CurrentLap"):
        right.append("Lap {}/{}".format(laps["CurrentLap"],
                                       laps.get("TotalLaps", "?")))
    clock = session_clock(state.get("ExtrapolatedClock", {}))
    if clock:
        right.append(clock + " left")
    right = " · ".join(r for r in right if r) + "  "
    right = right[-max(0, width - len(left)):] if len(left) + len(right) > width \
        else right
    text = left + " " * max(0, width - len(left) - len(right)) + right
    if not USE_COLOR:
        return text
    return _sgr("1;38;2;{};{};{};48;2;{};{};{}".format(*(fg + rgb)), text)


HIGHLIGHT_BG = "48;2;55;55;80"


def highlight(row, width):
    if not USE_COLOR:
        return ">" + row[1:]
    on = "\033[" + HIGHLIGHT_BG + "m"
    return on + row.replace(RESET, RESET + on) + " " * max(0, width - vlen(row)) \
        + RESET


def tower_rows(state, view, is_race, width):
    drivers = state.get("DriverList", {})
    timing = state.get("TimingData", {}).get("Lines", {})
    app = state.get("TimingAppData", {}).get("Lines", {})
    order = sorted(timing, key=lambda n: _int(timing[n].get("Position")
                                              or timing[n].get("Line"), 99))
    bests = [_lap_seconds(_best_lap(timing[n])) for n in order]
    bests = [b for b in bests if b]
    overall_best = min(bests) if bests else None

    sample = vlen(mini_sectors(timing[order[0]])) if order else 0
    # pos, driver (TLA only), gap, int, last, tyre, pits, status badge
    core = 3 + 2 + 5 + 2 + 8 + 2 + 7 + 2 + 8 + 2 + 6 + 2 + 3 + 11
    if is_race:
        core += 5 + 2
    show_surname = width >= core + 12
    core += 12 if show_surname else 0
    show_best = width >= core + 10
    core += 10 if show_best else 0
    show_mini = bool(sample) and width >= core + sample + 2

    cols = ["POS", " +/- " if is_race else "", "DRIVER".ljust(17 if show_surname else 5),
            "GAP".rjust(8), ("INT" if is_race else "AHEAD").rjust(7), "LAST".ljust(8)]
    if show_best:
        cols.append("BEST".ljust(8))
    if show_mini:
        cols.append("MINI-SECTORS".ljust(sample))
    cols += ["TYRE".ljust(6), "PIT"]
    rows = [dim("  ".join(c for c in cols if c))]

    for num in order:
        line = timing[num]
        d = drivers.get(num, {})
        colour = d.get("TeamColour", "")
        is_out = line.get("Retired") or line.get("Stopped")
        cells = [" " + pos_style(line.get("Position", ""))]

        if is_race:
            grid = _int((app.get(num) or {}).get("GridPos"), 0)
            pos = _int(line.get("Position"), 0)
            delta = grid - pos if grid and pos else 0
            if delta > 0:
                cells.append(green(vrjust("▲{}".format(delta), 4)) + " ")
            elif delta < 0:
                cells.append(red(vrjust("▼{}".format(-delta), 4)) + " ")
            else:
                cells.append(dim("   · "))

        tla = d.get("Tla", num)
        name = _fmt_rgb(colour, "█") + " " + bold(_fmt_rgb(colour, tla))
        if show_surname:
            name += " " + trunc(d.get("LastName", ""), 11)
        cells.append(vpad(name, 17 if show_surname else 5))

        gap = _val(line.get("GapToLeader")) or _val(line.get("TimeDiffToFastest"))
        ahead = _val(line.get("IntervalToPositionAhead")) \
            or _val(line.get("TimeDiffToPositionAhead"))
        if str(line.get("Position")) == "1":
            gap, ahead = "Leader", ""
        ahead_s = trunc(ahead, 7).rjust(7)
        secs = _seconds(ahead)
        if is_race and secs is not None and 0 < secs < 1.0:
            ahead_s = _sgr("1;32", ahead_s)  # DRS range
        cells += [trunc(gap, 8).rjust(8), ahead_s]

        last = line.get("LastLapTime") or {}
        last_s = _val(last).ljust(8)
        if last.get("OverallFastest"):
            last_s = _sgr(PURPLE, last_s)
        elif last.get("PersonalFastest"):
            last_s = green(last_s)
        cells.append(last_s)
        if show_best:
            best = _best_lap(line)
            best_s = best.ljust(8)
            if best and overall_best and _lap_seconds(best) == overall_best:
                best_s = _sgr(PURPLE, best_s)
            cells.append(best_s)
        if show_mini:
            cells.append(vpad(mini_sectors(line), sample))
        cells.append(tyre_cell(_last_stint(app.get(num))))
        cells.append(str(line.get("NumberOfPitStops", 0)).rjust(3))

        if line.get("InPit"):
            cells.append(badge("IN PIT", (90, 160, 255)))
        elif line.get("PitOut"):
            cells.append(badge("PIT OUT", (90, 160, 255)))
        elif line.get("Retired"):
            cells.append(badge("OUT", (225, 6, 0), (255, 255, 255)))
        elif line.get("Stopped"):
            cells.append(badge("STOPPED", (225, 6, 0), (255, 255, 255)))
        elif line.get("KnockedOut"):
            cells.append(dim("KO"))

        row = "  ".join(cells)
        if is_out and USE_COLOR:
            row = dim(ANSI_RE.sub("", row))
        rows.append(row)

    width_used = max(vlen(r) for r in rows)
    if view.focus in order:
        i = order.index(view.focus) + 1
        rows[i] = highlight(rows[i], width_used)
    return rows


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(values):
    lo = min(values)
    hi = max(min(max(values), lo + 3.0), lo + 0.1)
    return "".join(SPARK[min(7, int((min(v, hi) - lo) / (hi - lo) * 7 + 0.5))]
                   for v in values)


def _trend(view, num, threat=False, short=False):
    """How a gap to the car ahead is moving over the last few laps. For the
    car behind (threat=True) closing is bad news, so the colours flip."""
    gaps = [h[2] for h in view.history.get(num, [])[-6:] if h[2] is not None]
    if len(gaps) < 2:
        return ""
    rate = (gaps[-1] - gaps[0]) / (len(gaps) - 1)
    if abs(rate) <= 0.05:
        return dim("holding")
    closing = rate < 0
    good = closing != threat
    if short:
        text = "{:+.1f}s/lap".format(rate)
    elif threat:
        text = ("catching {:.1f}s/lap" if closing else "falling back {:.1f}s/lap").format(abs(rate))
    else:
        text = ("closing {:.1f}s/lap" if closing else "dropping {:.1f}s/lap").format(abs(rate))
    return green(text) if good else red(text)


def _flagged(item, text):
    """Colour a timing value purple/green when it is a best."""
    if (item or {}).get("OverallFastest"):
        return _sgr(PURPLE, text)
    if (item or {}).get("PersonalFastest"):
        return green(text)
    return text


def _rank(item):
    pos = (item or {}).get("Position")
    return dim(" P{}".format(pos)) if pos else ""


def detail_lines(state, view, is_race, width):
    """Everything about the highlighted driver. Two columns of sections when
    there is room, otherwise one column."""
    if width >= 84:
        col = (width - 3) // 2
        head, secs = _detail_sections(state, view, is_race, col)
        if not head:
            return []
        left = head + [l for t, b in secs[:2] for l in [""] + _sec_head(t) + b]
        right = [l for t, b in secs[2:] for l in _sec_head(t) + b + [""]]
        return hjoin([[vpad(vtrunc(l, col), col) for l in left] or [""],
                      [vtrunc(l, col) for l in right] or [""]], gap=3)
    head, secs = _detail_sections(state, view, is_race, width)
    return head + [l for t, b in secs for l in _sec_head(t) + b]


def _sec_head(title):
    return [bold(dim("\u2500 " + title.upper()))]


def _wrap_parts(parts, width, sep="  "):
    """Join coloured pieces into lines no wider than width."""
    lines, cur = [], ""
    for p in parts:
        cand = cur + sep + p if cur else p
        if cur and vlen(cand) > width:
            lines.append(cur)
            cur = p
        else:
            cur = cand
    return lines + ([cur] if cur else [])


def _detail_sections(state, view, is_race, width):
    num = view.focus
    timing = state.get("TimingData", {}).get("Lines", {})
    if not num or num not in timing:
        return [], []
    drivers = state.get("DriverList", {})
    d = drivers.get(num, {})
    line = timing[num]
    app = state.get("TimingAppData", {}).get("Lines", {}).get(num) or {}
    stats = state.get("TimingStats", {}).get("Lines", {}).get(num) or {}
    colour = d.get("TeamColour", "")

    secs = []

    def section(title):
        secs.append((title, []))
        return secs[-1][1]

    def who(n):
        dd = drivers.get(n, {})
        tla = bold(_fmt_rgb(dd.get("TeamColour", ""), dd.get("Tla", n)))
        if width < 44:  # narrow column: the three-letter code is enough
            return tla
        return "{} {}".format(tla, trunc(dd.get("LastName", ""), 11).ljust(11))

    name = _fmt_rgb(colour, "\u2588 ") + bold(_fmt_rgb(colour, d.get("Tla", num))) \
        + "  " + bold("{} {}".format(d.get("FirstName", ""), d.get("LastName", "")))
    team = dim("#{} \u00b7 {}".format(num, d.get("TeamName", "")))
    out = _wrap_parts([name, team], width, "  ")

    pos = _int(line.get("Position"), 0)
    grid = _int(app.get("GridPos"), 0)
    where = bold("P{}".format(pos or "?"))
    if is_race and grid and pos:
        delta = grid - pos
        moved = green("▲{}".format(delta)) if delta > 0 else \
            red("▼{}".format(-delta)) if delta < 0 else dim("=")
        where += "  {} {}".format(moved, dim("from P{}".format(grid)))
    stops = _int(line.get("NumberOfPitStops"), 0)
    where += dim("  ·  {} stop{}".format(stops, "" if stops == 1 else "s"))
    out.append(where)

    # Battle: the car ahead and behind, and whether the gaps are moving.
    order = sorted(timing, key=lambda n: _int(timing[n].get("Position"), 99))
    i = order.index(num)
    ahead = order[i - 1] if i > 0 else None
    behind = order[i + 1] if i + 1 < len(order) else None
    battle = section("Battle")
    if ahead:
        iv = _val(line.get("IntervalToPositionAhead")) \
            or _val(line.get("TimeDiffToPositionAhead"))
        battle.append("P{} {} {:>8}  {}".format(
            i, who(ahead), iv, _trend(view, num, short=width < 44) if is_race else ""))
    battle.append(highlight("P{} {}".format(i + 1, who(num)), max(0, width - 2))
               if USE_COLOR else "P{} {}".format(i + 1, who(num)))
    if behind:
        bl = timing[behind]
        iv = _val(bl.get("IntervalToPositionAhead")) \
            or _val(bl.get("TimeDiffToPositionAhead"))
        trend = _trend(view, behind, threat=True, short=width < 44) if is_race else ""
        battle.append("P{} {} {:>8}  {}".format(i + 2, who(behind), iv, trend))

    # This lap: live sector times, mini-sectors, speed traps.
    lap = section("This lap \u00b7 km/h")
    sectors = _as_list(line.get("Sectors"))
    parts = []
    for k, sec in enumerate(sectors, 1):
        value = (sec or {}).get("Value") or ""
        shown = _flagged(sec, value) if value else dim((sec or {}).get("PreviousValue") or "-")
        parts.append(dim("S{} ".format(k)) + vpad(shown, 6))
    if parts:
        lap += _wrap_parts(parts, width)
        lap.append(mini_sectors(line))
    speeds = line.get("Speeds") or {}
    traps = [(k, label) for k, label in (("I1", "I1"), ("I2", "I2"),
                                         ("FL", "Finish"), ("ST", "Trap"))]
    speed_parts = [dim(label + " ") + _flagged(speeds.get(k), _val(speeds.get(k)) or "-")
                   for k, label in traps]
    lap += _wrap_parts(speed_parts, width)

    # Bests, with where they rank in the field.
    best = section("Best")
    pb = stats.get("PersonalBestLapTime") or {}
    if _val(pb):
        best.append(dim("Lap ") + _val(pb) + _rank(pb)
                   + dim("  (lap {})".format(pb["Lap"]) if pb.get("Lap") else ""))
    best_secs = _as_list(stats.get("BestSectors"))
    if best_secs:
        best += _wrap_parts([dim("S{} ".format(k)) + _val(b) + _rank(b)
                             for k, b in enumerate(best_secs, 1) if b], width)
    trap = (stats.get("BestSpeeds") or {}).get("ST")
    if _val(trap):
        best.append(dim("Top speed ") + _val(trap) + dim(" km/h") + _rank(trap))

    # Tyres and lap-time history.
    stints = _as_list(app.get("Stints"))
    if stints:
        tyres = section("Tyres")
        bits = []
        for k, st in enumerate(stints):
            laps = _int(st.get("TotalLaps"), 0) - _int(st.get("StartLaps"), 0)
            used = dim(" used") if st.get("New") == "false" else ""
            now = dim(" now") if k == len(stints) - 1 else ""
            bits.append("{} {}{}{}".format(tyre_badge(st), laps, used, now))
        tyres += _wrap_parts(bits, width, dim(" \u2192 "))

    laps_sec = section("Lap times \u00b7 taller = slower")
    hist = view.history.get(num, [])
    if len(hist) >= 2:
        times = [h[1] for h in hist[-(max(10, width - 2)):]]
        laps_sec.append(rgb_fg((0, 200, 80), sparkline(times))
                        + dim("  {} laps".format(len(times))))
    else:
        laps_sec.append(dim("Fills in while boxbox is running."))
    return out, secs


def rc_lines(state, width, limit):
    msgs = _as_list(state.get("RaceControlMessages", {}).get("Messages"))
    out = []
    for m in reversed(msgs):
        if not m or len(out) >= limit:
            continue
        when = "     "
        try:
            day, clock_t = m["Utc"].rstrip("Z").split("T")
            when = local(parse_dt(day, clock_t[:8] + "Z")).strftime("%H:%M")
        except (KeyError, ValueError):
            pass
        lap = "L{}".format(m["Lap"]) if m.get("Lap") else ""
        text = m.get("Message", "")
        flag = m.get("Flag", "")
        if flag == "RED" or "PENALTY" in text:
            paint = red
        elif flag in ("YELLOW", "DOUBLE YELLOW") or "SAFETY CAR" in text:
            paint = yellow
        elif flag in ("GREEN", "CLEAR"):
            paint = green
        else:
            paint = str
        for i, piece in enumerate(textwrap.wrap(text, max(10, width - 10)) or [""]):
            prefix = dim("{} {}".format(when, lap.ljust(3))) if i == 0 else " " * 9
            out.append(prefix + " " + paint(piece))
    return out[:limit]


def render_track_line(state, view, is_race, inner):
    """Cars as team-coloured dots on one line, with names tied to their dot
    by a short stalk when they have to sit further away."""
    drivers = state.get("DriverList", {})
    cars, view.scale = car_positions(state, view, is_race)
    length = max(20, inner - 8)
    if not cars:  # keep the panel's height while positions are unavailable
        blank = [""] * 3
        return blank + [dim("S/F " + "\u2500" * length + " S/F")] + blank

    def rank(num):
        return view.order.index(num) if num in view.order else 99

    placed = [(num, min(length - 1, int(frac * (length - 1) + 0.5)))
              for num, frac in cars]
    line = ["─"] * length
    line_owner = [None] * length
    # Back-markers first so the leader, then the focused car, end up on top.
    for num, col in sorted(placed, key=lambda p: (p[0] == view.focus, -rank(p[0]))):
        line[col] = "●"
        line_owner[col] = num

    rows = {("a", i): [" "] * length for i in range(3)}  # a2 sits on the line
    rows.update({("b", i): [" "] * length for i in range(3)})  # b0 under it
    owner = {}
    prefs = [("a", 2), ("b", 0), ("a", 1), ("b", 1), ("a", 0), ("b", 2)]
    for num, col in sorted(placed, key=lambda p: (p[0] != view.focus, rank(p[0]))):
        tla = drivers.get(num, {}).get("Tla", num)[:3]
        start = max(0, min(length - len(tla), col - 1))
        for side, r in prefs:
            row = rows[(side, r)]
            if any(row[i] != " " for i in range(max(0, start - 1),
                                                min(length, start + len(tla) + 1))):
                continue
            stalk = range(r + 1, 3) if side == "a" else range(0, r)
            if any(rows[(side, k)][col] != " " for k in stalk):
                continue
            for i, ch in enumerate(tla):
                row[start + i] = ch
                owner[(side, r, start + i)] = num
            for k in stalk:
                rows[(side, k)][col] = "│"
                owner[(side, k, col)] = num
            break

    def paint(chars, owner_at, base=None):
        out, i = [], 0
        while i < len(chars):
            num = owner_at(i)
            j = i + 1
            while j < len(chars) and owner_at(j) == num:
                j += 1
            text = "".join(chars[i:j])
            if num is not None:
                text = _fmt_rgb(drivers.get(num, {}).get("TeamColour", ""), text)
                if num == view.focus and USE_COLOR:
                    text = "\033[1;7m" + text + RESET
            elif base:
                text = base(text)
            out.append(text)
            i = j
        return "".join(out)

    if view.mode == "gap":
        left, right = dim("P1 "), dim("")
    else:
        left, right = dim("S/F"), dim("S/F")
    out = []
    for side, rng in (("a", range(3)), ("b", range(3))):
        if side == "b":
            out.append(left + " " + paint(line, lambda i: line_owner[i], dim)
                       + " " + right)
        for r in rng:
            row = rows[(side, r)]
            # Every label row is always drawn, even when empty, so the panel
            # keeps a fixed height however tightly the cars are bunched.
            out.append("    " + paint(row, lambda i, s=side, r=r: owner.get((s, r, i))))
    return out


def render_live(state, feed=None, view=None):
    view = view or LiveView()
    tw, th = term_size()
    info = state.get("SessionInfo", {})
    is_race = info.get("Type") in ("Race", "Sprint")
    timing = state.get("TimingData", {}).get("Lines", {})
    view.order = [n for n in sorted(timing, key=lambda n: _int(
        timing[n].get("Position"), 99))
        if not (timing[n].get("Retired") or timing[n].get("Stopped"))]
    view.observe(state, is_race)

    out = [status_banner(state, tw)]
    events = render_events(view, 2)
    if feed is not None and not feed.connected:  # reuse an event slot
        events[-1] = yellow("  reconnecting to live feed... ") + dim(feed.error or "")
    out += events

    track = render_track_line(state, view, is_race, tw - 4)
    if track:
        title = "Gaps" if view.mode == "gap" else "Track"
        out += panel(title, track, tw)

    tower = tower_rows(state, view, is_race, tw - 4)
    tower_w = max(vlen(l) for l in tower) + 4
    side_w = tw - tower_w - 1
    side_by_side = side_w >= 34
    detail = detail_lines(state, view, is_race,
                          (side_w if side_by_side else tw) - 4)
    if side_by_side:
        tower_box = panel("Timing", tower, tower_w)
        room = len(tower_box) - 2
        if detail:
            right = panel("Driver", detail[:room] + [""] * (room - len(detail)), side_w)
        else:
            right = panel("Race control", rc_lines(state, side_w - 4, room), side_w)
        out += hjoin([tower_box, right])
    else:
        out += panel("Timing", tower, tw)
        room = th - len(out) - 2 - 2
        if detail:
            out += panel("Driver", detail[:max(room, 8)], tw)
        elif room >= 2:
            out += panel("Race control", rc_lines(state, tw - 4, room), tw)
    return out


def next_session_line(season="current"):
    try:
        races = get_schedule(season)
    except ApiError:
        return ""
    now = now_utc()
    for race in races:
        for label, dt in sessions(race):
            if dt > now:
                return "  Next: {} {} · {} ({})".format(
                    race["raceName"], label, fmt_local(dt),
                    green("in " + fmt_delta((dt - now).total_seconds())))
    return ""


def _draw(lines):
    width, height = term_size()
    sys.stdout.write("\033[H" + "\n".join(
        vtrunc(l, width) + "\033[K" for l in lines[:height - 1]) + "\033[J")
    sys.stdout.flush()


def _redraw_forever(feed, view, footer, interval):
    sys.stdout.write("\033[?1049h\033[?25l")
    try:
        while True:
            _draw(render_live(feed.snapshot(), feed, view) + footer
                  + [dim("  Ctrl+C to quit")])
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
    finally:
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def run_live(interval=0.5):
    feed = LiveFeed()
    feed.start()
    feed.ready.wait(20)
    state = feed.snapshot()
    if not state:
        raise ApiError("could not connect to the F1 live timing feed"
                       + (": " + feed.error if feed.error else ""))

    status = state.get("SessionStatus", {}).get("Status", "")
    if status not in LIVE_STATUSES:
        info = state.get("SessionInfo", {})
        emit(["  " + bold("No live session right now."),
              dim("  Last session: {} {} ({})".format(
                  info.get("Meeting", {}).get("Name", "?"),
                  info.get("Name", "?"), status.lower() or "unknown")),
              next_session_line()])
        return

    interactive = sys.stdout.isatty() and sys.stdin.isatty()
    view = LiveView()
    footer = [
        "  " + bold("\u2190/\u2192") + dim(" highlight driver  ") + bold("g")
        + dim(" gap/track view  ") + bold("esc") + dim(" clear  ") + bold("q")
        + dim(" quit"),
        dim("  Mini-sectors: ") + _sgr(PURPLE, "\u25b0") + dim(" fastest overall  ")
        + green("\u25b0") + dim(" personal best  ") + yellow("\u25b0")
        + dim(" slower   Tyre: compound and laps since the stop"),
    ]
    if not interactive:  # piped: print one frame and stop
        view.observe(feed.snapshot(), False)
        print("\n".join(render_live(feed.snapshot(), feed, view) + footer))
        return

    try:
        import select
        import termios
        import tty
    except ImportError:  # Windows: no raw keyboard input, just keep redrawing
        _redraw_forever(feed, view, footer[1:], interval)
        return
    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)
    sys.stdout.write("\033[?1049h\033[?25l")  # alt screen, hide cursor
    try:
        next_draw = 0.0
        while True:
            if time.time() >= next_draw:
                _draw(render_live(feed.snapshot(), feed, view) + footer)
                # Redraw a few times a second so cars glide along the line.
                next_draw = time.time() + interval
            ready, _, _ = select.select([sys.stdin], [], [],
                                        max(0, next_draw - time.time()))
            if not ready:
                continue
            key = os.read(fd, 16).decode("utf-8", "ignore")
            if key in ("q", "Q"):
                break
            elif key in ("g", "G"):
                view.mode = "gap" if view.mode == "track" else "track"
            elif key in ("\033[C", "l", "j", "\033[B"):
                view.cycle_focus(1)
            elif key in ("\033[D", "h", "k", "\033[A"):
                view.cycle_focus(-1)
            elif key == "\033":
                view.focus = None
            next_draw = 0  # redraw straight away after a key press
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def latest_with_data(season, kind, refresh=False):
    """Most recent started round that actually has `kind` data published.
    Results land a little after the chequered flag, so step back if needed."""
    races = get_schedule(season, refresh)
    now = now_utc()
    started = [r for r in races if race_dt(r) - timedelta(days=3) <= now]
    if kind == "sprint":
        started = [r for r in started if "Sprint" in r]
    for r in reversed(started[-3:]):
        if get_session_results(season, r["round"], kind, refresh):
            return r["round"]
    return started[-1]["round"] if started else "1"


def banner():
    return " " + badge("BOX BOX", (225, 6, 0), (255, 255, 255)) \
        + dim("  F1 in your terminal")


def emit(lines):
    if any(l.startswith(SECTION) for l in lines):
        lines = boxed(lines)
    print("\n".join([banner(), ""] + lines))
    print()


def watch(render, interval):
    try:
        while True:
            lines = render()
            sys.stdout.write("\033[2J\033[H")
            emit(lines)
            print(dim("  Updating every {}s · Ctrl+C to quit".format(interval)))
            time.sleep(interval)
    except KeyboardInterrupt:
        print()


def season_arg(v):
    if v == "current" or (v.isdigit() and 1950 <= int(v) <= 2100):
        return v
    raise argparse.ArgumentTypeError("expected a year like 2024 or 'current'")


def round_arg(v):
    if v == "last" or v.isdigit():
        return v
    raise argparse.ArgumentTypeError("expected a round number")


def main(argv=None):
    global USE_COLOR
    p = argparse.ArgumentParser(
        prog="boxbox", description="Formula 1 tracker for your terminal.")
    p.add_argument("-s", "--season", default="current", type=season_arg,
                   help="season year (default: current)")
    p.add_argument("-r", "--refresh", action="store_true",
                   help="ignore the 5-minute cache and fetch fresh data")
    p.add_argument("--no-color", action="store_true", help="disable colours")
    p.add_argument("--version", action="version",
                   version="boxbox " + __version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("dashboard", help="overview (default)")
    sub.add_parser("drivers", help="driver championship standings")
    sub.add_parser("teams", help="constructor championship standings")
    sub.add_parser("schedule", help="season calendar")
    sub.add_parser("live", help="live timing during a session")
    n = sub.add_parser("next", help="next race weekend with session countdowns")
    n.add_argument("-w", "--watch", action="store_true",
                   help="keep the countdown ticking")
    for name, help_ in (("results", "race result"),
                        ("quali", "qualifying result"),
                        ("sprint", "sprint result")):
        sp = sub.add_parser(name, help=help_ + " (default: latest round)")
        sp.add_argument("round", nargs="?", default="last", type=round_arg,
                        help="round number (default: latest)")
    args = p.parse_args(argv)

    if args.no_color:
        USE_COLOR = False

    season, refresh = args.season, args.refresh
    cmd = args.cmd or "dashboard"
    try:
        if cmd == "dashboard":
            emit(render_dashboard(season, refresh))
        elif cmd == "drivers":
            emit(render_driver_standings(season, refresh=refresh))
        elif cmd == "teams":
            emit(render_constructor_standings(season, refresh))
        elif cmd == "schedule":
            emit(render_schedule(season, refresh))
        elif cmd == "live":
            run_live()
        elif cmd == "next":
            if args.watch:
                watch(lambda: render_next_race(season), 1)
            else:
                emit(render_next_race(season, refresh))
        elif cmd in ("results", "quali", "sprint"):
            kind = {"results": "results", "quali": "qualifying",
                    "sprint": "sprint"}[cmd]
            rnd = args.round
            if rnd == "last":
                rnd = latest_with_data(season, kind, refresh)
            emit(render_results(season, rnd, kind, refresh))
    except ApiError as e:
        print(red("error: ") + str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
