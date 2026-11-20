"""DreamON stats: pure functions over the extension's local logs.

Reads (never writes) ~/.local/share/dreamon/sittings.jsonl and days.json.
No GTK here, so it can be tested on its own.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "dreamon"
SITTINGS_PATH = DATA_DIR / "sittings.jsonl"
DAYS_PATH = DATA_DIR / "days.json"


# ---------- loading ----------

def load_sittings(path: Path = SITTINGS_PATH) -> list[dict]:
    """Every logged sitting, oldest first. Bad lines are skipped."""
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    s = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(s, dict) and isinstance(s.get("start"), (int, float)):
                    out.append(s)
    except FileNotFoundError:
        pass
    out.sort(key=lambda s: s["start"])
    return out


def load_days(path: Path = DAYS_PATH) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------- helpers ----------

def day_of(sitting: dict) -> date:
    return datetime.fromtimestamp(sitting["start"]).date()


def minutes(sitting: dict) -> int:
    m = sitting.get("minutes")
    if isinstance(m, (int, float)):
        return max(0, int(m))
    end = sitting.get("end", sitting["start"])
    return max(0, round((end - sitting["start"]) / 60))


def since(sittings: list[dict], start_day: date) -> list[dict]:
    return [s for s in sittings if day_of(s) >= start_day]


def on_day(sittings: list[dict], d: date) -> list[dict]:
    return [s for s in sittings if day_of(s) == d]


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())   # Monday


# ---------- time and streaks ----------

def totals(sittings: list[dict]) -> dict:
    """Minutes, sittings, completed, pages, reading time and distractions for a set of sittings."""
    done = [s for s in sittings if s.get("completed")]
    return {
        "minutes": sum(minutes(s) for s in sittings),
        "sittings": len(sittings),
        "completed": len(done),
        "left_early": len(sittings) - len(done),
        "pages": sum(s.get("pages") or 0 for s in sittings),
        "reading_seconds": sum(s.get("readingSeconds") or 0 for s in sittings),
        "minimized": sum(s.get("minimized") or 0 for s in sittings),
        "covered": sum(s.get("covered") or 0 for s in sittings),
    }


def minutes_by_day(sittings: list[dict], end: date, days: int = 7) -> list[tuple[date, dict[str, int]]]:
    """[(day, {practice: minutes})] for the `days` days ending on `end`, oldest first."""
    first = end - timedelta(days=days - 1)
    table: dict[date, dict[str, int]] = {first + timedelta(days=i): defaultdict(int) for i in range(days)}
    for s in sittings:
        d = day_of(s)
        if d in table:
            table[d][s.get("practice", "Other")] += minutes(s)
    return [(d, dict(v)) for d, v in table.items()]


def minutes_by_practice(sittings: list[dict]) -> list[tuple[str, int]]:
    c: Counter = Counter()
    for s in sittings:
        c[s.get("practice", "Other")] += minutes(s)
    return c.most_common()


def practice_days(sittings: list[dict], min_minutes: int = 1) -> set[date]:
    per_day: Counter = Counter()
    for s in sittings:
        per_day[day_of(s)] += minutes(s)
    return {d for d, m in per_day.items() if m >= min_minutes}


def streaks(sittings: list[dict], today: date) -> tuple[int, int]:
    """(current, longest) runs of consecutive days with practice.

    The current streak stays alive through today until midnight: if today has no
    sitting yet, it counts back from yesterday."""
    days = practice_days(sittings)
    if not days:
        return 0, 0
    longest = run = 0
    prev = None
    for d in sorted(days):
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        longest = max(longest, run)
        prev = d
    cur = 0
    d = today if today in days else today - timedelta(days=1)
    while d in days:
        cur += 1
        d -= timedelta(days=1)
    return cur, longest


def heat(sittings: list[dict], today: date, weeks: int = 18) -> list[list[tuple[date, int]]]:
    """Columns of Monday..Sunday (date, minutes) for the last `weeks` weeks; future days get -1."""
    per_day: Counter = Counter()
    for s in sittings:
        per_day[day_of(s)] += minutes(s)
    first = week_start(today) - timedelta(weeks=weeks - 1)
    cols = []
    for w in range(weeks):
        col = []
        for i in range(7):
            d = first + timedelta(weeks=w, days=i)
            col.append((d, -1 if d > today else per_day.get(d, 0)))
        cols.append(col)
    return cols


# ---------- reading ----------

def papers(sittings: list[dict]) -> list[dict]:
    """Zotero keys read across these sittings: pages, seconds, sittings, last read; most time first."""
    agg: dict[str, dict] = {}
    for s in sittings:
        for p in s.get("papers") or []:
            key = p.get("key")
            if not key:
                continue
            a = agg.setdefault(key, {"key": key, "pages": 0, "seconds": 0, "sittings": 0, "last": 0})
            a["pages"] += p.get("pages") or 0
            a["seconds"] += p.get("seconds") or 0
            a["sittings"] += 1
            a["last"] = max(a["last"], s.get("end", s["start"]))
    return sorted(agg.values(), key=lambda a: (a["seconds"], a["pages"]), reverse=True)


# ---------- distractions ----------

def top(sittings: list[dict], field: str, n: int = 8) -> list[tuple[str, int]]:
    """Most frequent apps (field='apps') or sites (field='sites') across sittings."""
    c: Counter = Counter()
    for s in sittings:
        for name, count in (s.get(field) or {}).items():
            if isinstance(count, (int, float)):
                c[name] += int(count)
    return c.most_common(n)


def site_days(days: dict[str, dict], today: date) -> dict:
    """Summary of No-<site> days: how many, how many with no reaching, the current run, recent list."""
    parsed = []
    for k, v in days.items():
        try:
            parsed.append((date.fromisoformat(k), v if isinstance(v, dict) else {}))
        except ValueError:
            continue
    parsed.sort(key=lambda x: x[0])
    kept = {d for d, v in parsed}
    run = 0
    d = today if today in kept else today - timedelta(days=1)
    while d in kept:
        run += 1
        d -= timedelta(days=1)
    return {
        "days": len(parsed),
        "clean": sum(1 for _, v in parsed if not v.get("attempts")),
        "attempts": sum(v.get("attempts") or 0 for _, v in parsed),
        "run": run,
        "recent": [(d, v) for d, v in reversed(parsed)][:14],
    }


# ---------- formatting ----------

def fmt_minutes(m: int) -> str:
    m = int(m)
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h" if r == 0 else f"{h} h {r} min"
