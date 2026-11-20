#!/usr/bin/env python3
"""DreamON — companion app for the DreamON GNOME Shell extension.

The ensō is the measure: a sitting's ensō draws itself as the sitting goes on,
and each day's ensō closes when the day's practice reaches the daily aim.

Begins sittings and declares days through the extension's D-Bus interface; it has
no way to end a sitting (leaving early opens the extension's own dialog).
Fully local: reads ~/.local/share/dreamon/ (written by the extension) and, for
paper titles, Zotero's local API on 127.0.0.1. Writes nothing but a title cache.
"""

from __future__ import annotations

import json
import math
import random
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enso  # noqa: E402
import stats  # noqa: E402
import zotero  # noqa: E402

APP_ID = "io.github.sgakhil.DreamON"
EXT_UUID = "dreamon@sgakhil.github.io"
DBUS_NAME = "org.gnome.Shell"
DBUS_PATH = "/io/github/sgakhil/DreamON"
DBUS_IFACE = "io.github.sgakhil.DreamON"
DAILY_AIM = 120   # minutes; a day's ensō closes here

# Practice inks: sage, slate, ochre, clay, plum
PALETTE = [
    (0.541, 0.604, 0.482),
    (0.420, 0.498, 0.600),
    (0.722, 0.584, 0.353),
    (0.690, 0.443, 0.380),
    (0.537, 0.463, 0.600),
]

THEMES = {
    False: dict(bg="#f3efe6", fg="#2b2925", row="#faf7f1", line="rgba(43,41,37,0.09)",
                ink=(0.17, 0.16, 0.145), accent="#6f8460", accent_fg="#fbf9f4", accent_text="#5b6e4d"),
    True: dict(bg="#191816", fg="#e8e2d5", row="#211f1c", line="rgba(232,226,213,0.08)",
               ink=(0.91, 0.886, 0.835), accent="#7d9170", accent_fg="#191816", accent_text="#a9bb98"),
}

SERIF = '"Noto Serif", "Source Serif 4", "Source Serif Pro", "Libertinus Serif", "EB Garamond", serif'

LINES = [
    "One thing, fully.",
    "Sit down. Begin. The rest can wait.",
    "The page is patient. So can you be.",
    "Nothing to chase — only the next sentence.",
    "Attention is the whole practice.",
    "Small, unbroken, every day.",
    "Return, and return again.",
]


def load_lines() -> list[dict]:
    """Your private corpus, ~/.local/share/dreamon/lines.json: [{"text", "author", "source"}, …].
    Kept outside the repository. Falls back to a few plain lines if it's missing or unreadable."""
    try:
        data = json.loads((stats.DATA_DIR / "lines.json").read_text(encoding="utf-8"))
        lines = [d for d in data if isinstance(d, dict) and str(d.get("text", "")).strip()]
        if lines:
            return lines
    except (OSError, ValueError, TypeError):
        pass
    return [{"text": t} for t in LINES]


def css_for(dark: bool) -> str:
    t = THEMES[dark]
    dots = "".join(
        f".dreamon-c{i} {{ background-color: rgb({round(r*255)},{round(g*255)},{round(b*255)}); }}\n"
        for i, (r, g, b) in enumerate(PALETTE))
    return f"""
:root {{
  --accent-bg-color: {t['accent']};
  --accent-fg-color: {t['accent_fg']};
  --accent-color: {t['accent_text']};
}}
@define-color accent_bg_color {t['accent']};
@define-color accent_fg_color {t['accent_fg']};
@define-color accent_color {t['accent_text']};

window.dreamon,
window.dreamon headerbar,
window.dreamon .top-bar {{ background-color: {t['bg']}; color: {t['fg']}; }}
window.dreamon headerbar {{ box-shadow: none; border: none; }}
window.dreamon scrolledwindow,
window.dreamon viewport {{ background-color: transparent; }}
window.dreamon .boxed-list {{
  background-color: {t['row']};
  border-radius: 14px;
  box-shadow: 0 0 0 1px {t['line']};
}}
window.dreamon .boxed-list > row {{ background-color: transparent; color: {t['fg']}; }}
window.dreamon .boxed-list > row:not(:last-child) {{ border-bottom: 1px solid {t['line']}; }}
window.dreamon .boxed-list > row.activatable:hover {{ background-color: alpha(currentColor, 0.04); }}

.dreamon-enso-number {{ font-family: {SERIF}; font-size: 40pt; font-weight: 300; font-feature-settings: "tnum"; }}
.dreamon-hero {{ font-family: {SERIF}; font-size: 24pt; font-weight: 400; }}
.dreamon-subhead {{ font-family: {SERIF}; font-size: 15pt; font-weight: 400; }}
.dreamon-quote {{ font-family: {SERIF}; font-style: italic; font-size: 17px; line-height: 1.45; color: alpha({t['fg']}, 0.72); }}
.dreamon-section {{ font-family: {SERIF}; font-size: 15pt; margin-top: 18px; }}
.dreamon-stat {{ font-family: {SERIF}; font-size: 22pt; font-weight: 400; }}
.dreamon-caption {{ font-size: 13px; line-height: 1.5; color: alpha({t['fg']}, 0.6); }}
.dreamon-muted {{ line-height: 1.4; color: alpha({t['fg']}, 0.64); }}
.dreamon-day {{ font-size: 13px; line-height: 1.5; color: alpha({t['fg']}, 0.6); margin-top: 10px; }}
.dreamon-rule {{ background-color: {t['line']}; min-height: 1px; margin: 10px 0; }}
.dreamon-begin {{ background-color: {t['accent']}; color: {t['accent_fg']}; padding-left: 26px; padding-right: 26px; }}
.dreamon-dot {{ border-radius: 99px; min-width: 9px; min-height: 9px; }}
{dots}"""


# ---------- small helpers ----------

def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def clock(seconds: float) -> str:
    s = max(0, math.ceil(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def short_minutes(m: int) -> str:
    """'1 h 20', '45 min' — for inside an ensō."""
    m = int(m)
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h" if r == 0 else f"{h} h {r:02d}"


def day_name(d: date, today: date) -> str:
    if d == today:
        return "Today"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return f"{d:%a} {d.day} {d:%b}"


def clear(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def label(text: str, css=(), wrap: bool = False, xalign: float = 0.0, **kw) -> Gtk.Label:
    lab = Gtk.Label(label=text, xalign=xalign, wrap=wrap, **kw)
    for c in css:
        lab.add_css_class(c)
    return lab


def stat_row(*items: tuple[str, str]) -> Gtk.Box:
    row = Gtk.Box(spacing=24, homogeneous=True)
    for value, caption in items:
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        b.append(label(value, ["dreamon-stat"]))
        b.append(label(caption, ["dreamon-caption"], wrap=True))
        row.append(b)
    return row


def rule() -> Gtk.Box:
    r = Gtk.Box()
    r.add_css_class("dreamon-rule")
    return r


def listbox() -> Gtk.ListBox:
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("boxed-list")
    return lb


def action_row(title: str, subtitle: str = "", suffix: str = "") -> Adw.ActionRow:
    r = Adw.ActionRow(title=title, subtitle=subtitle)
    r.set_use_markup(False)
    if suffix:
        r.add_suffix(label(suffix, ["dreamon-muted"]))
    return r


def page() -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                  margin_top=28, margin_bottom=40, margin_start=28, margin_end=28)
    clamp = Adw.Clamp(maximum_size=820, child=box)
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True)
    return sw, box


def paint(cr, stamps_, rgb, alpha: float = 1.0) -> None:
    """Fill brush stamps as one path, so overlaps don't darken."""
    if not stamps_:
        return
    for x, y, r, _t in stamps_:
        cr.new_sub_path()
        cr.arc(x, y, r, 0, math.tau)
    cr.set_source_rgba(*rgb, alpha)
    cr.fill()


# ---------- ensō widgets ----------

class EnsoView(Gtk.Overlay):
    """A large brushed ensō with words inside. `progress` 0..1 is how much of it is drawn."""

    def __init__(self, win: "Window", size: int = 330) -> None:
        super().__init__(halign=Gtk.Align.CENTER)
        self.win = win
        self.size = size
        self.progress = 0.0
        self.seed = 0
        self._cache: tuple | None = None
        self.area = Gtk.DrawingArea(content_width=size, content_height=size)
        self.area.set_draw_func(self._draw)
        self.set_child(self.area)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.big = label("", ["dreamon-enso-number"], xalign=0.5)
        self.small = label("", ["dreamon-caption"], xalign=0.5)
        inner.append(self.big)
        inner.append(self.small)
        self.add_overlay(inner)

    def set_view(self, progress: float, seed: int, big: str, small: str) -> None:
        self.progress, self.seed = max(0.0, min(1.0, progress)), seed
        self.big.set_label(big)
        self.small.set_label(small)
        self.area.queue_draw()

    def _stroke(self, progress: float):
        key = (self.seed, round(progress, 3))
        if self._cache is None or self._cache[0] != key:
            s = self.size
            self._cache = (key, enso.stamps(s / 2, s / 2, s * 0.4, progress, seed=self.seed, width=0.11))
        return self._cache[1]

    def _draw(self, _area, cr, w, h) -> None:
        cr.translate((w - self.size) / 2, (h - self.size) / 2)
        ink = self.win.ink()
        if self.progress <= 0:
            paint(cr, self._stroke(1.0), ink, 0.07)      # an invitation, not a measure
            return
        paint(cr, self._stroke(self.progress), ink, 0.92)


class DayEnso(Gtk.DrawingArea):
    """A small ensō for one day: practice inks in order, closing at the daily aim."""

    def __init__(self, win: "Window", day: date, minutes_by_practice: dict[str, int], size: int = 64) -> None:
        super().__init__(content_width=size, content_height=size, halign=Gtk.Align.CENTER)
        self.win, self.day, self.mins, self.size = win, day, minutes_by_practice, size
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, w, h) -> None:
        cx, cy, r = w / 2, h / 2, self.size * 0.36
        total = sum(self.mins.values())
        if total == 0:
            paint(cr, enso.dot(cx, cy, 2.2), self.win.ink(), 0.22)
            return
        whole = max(total, DAILY_AIM)
        stroke = enso.stamps(cx, cy, r, min(1.0, total / DAILY_AIM), seed=self.day.toordinal(),
                             width=0.17, lift=0.08)
        order = self.win.practice_order()
        shares = [(n, self.mins[n] / whole) for n in order + [k for k in self.mins if k not in order]
                  if self.mins.get(n)]
        for name, run in enso.split_by(stroke, shares):
            paint(cr, run, self.win.color(name), 0.95)


class EnsoCalendar(Gtk.Box):
    """Weeks of small ensō, each closing when that day reached the daily aim."""

    def __init__(self, win: "Window", cols) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.win, self.cols = win, cols
        self.cell, self.left, self.top = 22, 32, 18
        area = Gtk.DrawingArea(content_width=self.left + len(cols) * self.cell,
                               content_height=self.top + 7 * self.cell, halign=Gtk.Align.START)
        area.set_draw_func(self._draw)
        self.append(area)
        self.append(label(f"Each day's ensō closes at {stats.fmt_minutes(DAILY_AIM)} of practice.",
                          ["dreamon-caption"]))

    def _draw(self, _area, cr, _w, _h) -> None:
        ink = self.win.ink()
        c = self.cell
        cr.select_font_face("Sans")
        cr.set_font_size(9.5)
        cr.set_source_rgba(*ink, 0.5)
        for row, name in ((0, "Mon"), (2, "Wed"), (4, "Fri"), (6, "Sun")):
            cr.move_to(0, self.top + row * c + c / 2 + 3.5)
            cr.show_text(name)
        last_month = None
        for i, col in enumerate(self.cols):
            x = self.left + i * c
            month = col[0][0].month
            if month != last_month:
                cr.set_source_rgba(*ink, 0.5)
                cr.move_to(x + 2, 11)
                cr.show_text(col[0][0].strftime("%b"))
                last_month = month
            for row, (d, m) in enumerate(col):
                if m < 0:
                    continue
                cx, cy = x + c / 2, self.top + row * c + c / 2
                if m == 0:
                    paint(cr, enso.dot(cx, cy, 1.4), ink, 0.25)
                else:
                    paint(cr, enso.stamps(cx, cy, 7.0, min(1.0, m / DAILY_AIM), seed=d.toordinal(),
                                          width=0.22, bristles=False, lift=0.1), PALETTE[0], 0.95)


# ---------- link to the extension ----------

class ShellLink:
    """Talks to the extension over D-Bus. Every call fails soft (returns None/False)."""

    def __init__(self, on_change) -> None:
        self.proxy = None
        try:
            self.proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES | Gio.DBusProxyFlags.DO_NOT_AUTO_START,
                None, DBUS_NAME, DBUS_PATH, DBUS_IFACE, None)
            self.proxy.connect("g-signal", self._on_signal, on_change)
        except GLib.Error:
            self.proxy = None

    @staticmethod
    def _on_signal(_proxy, _sender, signal, params, on_change) -> None:
        if signal == "StateChanged":
            try:
                on_change(json.loads(params.unpack()[0]))
            except (ValueError, IndexError):
                pass

    def _call(self, method, params=None):
        if self.proxy is None:
            return None
        try:
            v = self.proxy.call_sync(method, params, Gio.DBusCallFlags.NONE, 3000, None)
            return v.unpack() if v is not None else ()
        except GLib.Error:
            return None

    def state(self) -> dict | None:
        r = self._call("GetState")
        try:
            return json.loads(r[0]) if r else None
        except ValueError:
            return None

    def begin(self, practice: str, minutes: int) -> bool:
        r = self._call("Begin", GLib.Variant("(su)", (practice, minutes)))
        return bool(r and r[0])

    def declare_site_day(self) -> bool:
        r = self._call("DeclareSiteDay")
        return bool(r and r[0])

    def leave(self) -> None:
        self._call("OpenLeaveDialog")


# ---------- window ----------

class Window(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application) -> None:
        super().__init__(application=app, title="DreamON", default_width=940, default_height=860)
        self.add_css_class("dreamon")
        self.closed = False
        self.state: dict | None = None
        self.state_json = ""
        self.sittings: list[dict] = []
        self.days: dict = {}
        self.resolver = zotero.Resolver()
        self.paper_rows: dict[str, tuple] = {}
        self.hero: EnsoView | None = None
        self.sel_practice = ""
        self.sel_minutes = 60
        self.reload_id = 0
        self.lines: list[dict] = []
        self.line_step = 0
        self.style = Adw.StyleManager.get_default()
        self.link = ShellLink(self._on_state_signal)

        self.stack = Adw.ViewStack()
        self.pages: dict[str, Gtk.Box] = {}
        for name, title, icon in [
            ("today", "Today", "weather-clear-night-symbolic"),
            ("practice", "Practice", "x-office-calendar-symbolic"),
            ("reading", "Reading", "x-office-document-symbolic"),
            ("distractions", "Distractions", "view-conceal-symbolic"),
            ("history", "History", "document-open-recent-symbolic"),
        ]:
            sw, box = page()
            self.pages[name] = box
            self.stack.add_titled_with_icon(sw, name, title, icon)

        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        settings_btn = Gtk.Button(icon_name="emblem-system-symbolic", tooltip_text="Extension settings")
        settings_btn.add_css_class("flat")
        settings_btn.connect("clicked", self._open_settings)
        header.pack_end(settings_btn)

        self.toasts = Adw.ToastOverlay(child=self.stack)
        toolbar = Adw.ToolbarView(content=self.toasts)
        toolbar.add_top_bar(header)
        self.set_content(toolbar)

        stats.DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.monitor = Gio.File.new_for_path(str(stats.DATA_DIR)).monitor_directory(
            Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self._on_files_changed)
        self.style.connect("notify::dark", lambda *_: self.render())
        self.connect("close-request", self._on_close)

        self.state = self.link.state()
        self.state_json = json.dumps(self.state, sort_keys=True)
        self.reload()
        GLib.timeout_add_seconds(1, self._tick)
        GLib.timeout_add_seconds(15, self._poll)

    # ----- data -----

    def reload(self) -> None:
        self.lines = load_lines()
        self.sittings = stats.load_sittings()
        self.days = stats.load_days()
        self.render()

    def _on_files_changed(self, _mon, f, _other, _event) -> None:
        if f.get_basename() not in ("sittings.jsonl", "days.json", "lines.json") or self.reload_id:
            return

        def go():
            self.reload_id = 0
            if not self.closed:
                self.reload()
            return GLib.SOURCE_REMOVE
        self.reload_id = GLib.timeout_add(400, go)

    def _set_state(self, state: dict | None) -> None:
        j = json.dumps(state, sort_keys=True)
        if j == self.state_json:
            return
        self.state, self.state_json = state, j
        self.render_today()

    def _on_state_signal(self, state: dict) -> None:
        if not self.closed:
            self._set_state(state)

    def _poll(self) -> bool:
        if self.closed:
            return GLib.SOURCE_REMOVE
        self._set_state(self.link.state())
        return GLib.SOURCE_CONTINUE

    def _tick(self) -> bool:
        if self.closed:
            return GLib.SOURCE_REMOVE
        sitting = (self.state or {}).get("sitting")
        if self.hero is not None and sitting:
            now = datetime.now().timestamp()
            span = max(1, sitting["end"] - sitting["start"])
            end = datetime.fromtimestamp(sitting["end"]).strftime("%H:%M")
            self.hero.set_view((now - sitting["start"]) / span, int(sitting["start"]),
                           clock(sitting["end"] - now), f"{sitting['practice']} · until {end}")
            if now - sitting["end"] > 3:   # the bell has rung; ask for fresh state
                self._set_state(self.link.state())
        return GLib.SOURCE_CONTINUE

    def _on_close(self, *_args) -> bool:
        self.closed = True
        self.resolver.save()
        return False

    # ----- colours -----

    def ink(self) -> tuple[float, float, float]:
        return THEMES[self.style.get_dark()]["ink"]

    def practice_order(self) -> list[str]:
        names = list((self.state or {}).get("practices") or [])
        for s in self.sittings:
            p = s.get("practice", "Other")
            if p not in names:
                names.append(p)
        return names

    def color_index(self, practice: str) -> int:
        order = self.practice_order()
        return (order.index(practice) if practice in order else len(order)) % len(PALETTE)

    def color(self, practice: str) -> tuple[float, float, float]:
        return PALETTE[self.color_index(practice)]

    def dot(self, practice: str) -> Gtk.Box:
        d = Gtk.Box(valign=Gtk.Align.CENTER)
        d.add_css_class("dreamon-dot")
        d.add_css_class(f"dreamon-c{self.color_index(practice)}")
        return d

    # ----- actions -----

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=3))

    def _begin(self, practice: str, minutes: int) -> None:
        self.sel_practice, self.sel_minutes = practice, minutes
        if self.link.begin(practice, minutes):
            self._set_state(self.link.state())
        else:
            self.toast("Couldn't begin — is a sitting already running, or the extension off?")

    def _declare(self, *_args) -> None:
        if self.link.declare_site_day():
            self._set_state(self.link.state())
        else:
            self.toast("Couldn't reach the extension.")

    def _open_settings(self, *_args) -> None:
        try:
            Gio.Subprocess.new(["gnome-extensions", "prefs", EXT_UUID], Gio.SubprocessFlags.NONE)
        except GLib.Error:
            self.toast("Couldn't open the extension's settings.")

    def _open_zotero(self, _row, key: str) -> None:
        info = self.resolver.cached(key) or {}
        item = info.get("item", key)
        try:
            Gio.AppInfo.launch_default_for_uri(f"zotero://select/library/items/{item}", None)
        except GLib.Error:
            self.toast("Couldn't open Zotero.")

    # ----- rendering -----

    def render(self) -> None:
        self.render_today()
        self.render_practice()
        self.render_reading()
        self.render_distractions()
        self.render_history()

    def sitting_row(self, s: dict) -> Adw.ActionRow:
        start = datetime.fromtimestamp(s["start"])
        end = datetime.fromtimestamp(s.get("end", s["start"]))
        practice = s.get("practice", "Sitting")
        bits = [f"{start:%H:%M}–{end:%H:%M}"]
        bits.append("to the bell" if s.get("completed")
                    else f"left early, of {stats.fmt_minutes(s.get('plannedMinutes') or 0)}")
        if s.get("pages"):
            bits.append(plural(s["pages"], "page"))
        pulled = (s.get("minimized") or 0) + (s.get("covered") or 0)
        if pulled:
            bits.append(f"{plural(pulled, 'distraction')} set aside")
        r = action_row(f"{practice} · {stats.fmt_minutes(stats.minutes(s))}", " · ".join(bits))
        r.add_prefix(self.dot(practice))
        return r

    # Today

    def line_block(self, today: date) -> Gtk.Box:
        """The day's line: every line once, in a shuffled order, before any comes back.
        Click it for another."""
        n = len(self.lines)
        order = list(range(n))
        random.Random(f"dreamon-{n}").shuffle(order)
        line = self.lines[order[(today.toordinal() + self.line_step) % n]]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, halign=Gtk.Align.CENTER,
                      tooltip_text="Another line")
        box.append(label(line["text"], ["dreamon-quote"], wrap=True, xalign=0.5,
                         max_width_chars=62, justify=Gtk.Justification.CENTER))
        who = " · ".join(str(x) for x in (line.get("author"), line.get("source")) if x)
        if who:
            cap = label(who, ["dreamon-caption"], xalign=0.5)
            cap.set_ellipsize(Pango.EllipsizeMode.END)
            cap.set_max_width_chars(80)
            box.append(cap)
        click = Gtk.GestureClick()
        click.connect("released", self._next_line)
        box.add_controller(click)
        return box

    def _next_line(self, *_args) -> None:
        self.line_step += 1
        self.render_today()

    def render_today(self) -> None:
        box = self.pages["today"]
        clear(box)
        st = self.state
        today = date.today()
        todays = stats.on_day(self.sittings, today)
        t = stats.totals(todays)

        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER)
        box.append(center)
        sitting = (st or {}).get("sitting")

        if not sitting:
            center.append(self.line_block(today))
        self.hero = EnsoView(self)
        center.append(self.hero)

        if st is None:
            self.hero.set_view(t["minutes"] / DAILY_AIM, today.toordinal(),
                           short_minutes(t["minutes"]), "today")
            center.append(label("The top-bar extension isn't answering.", ["dreamon-hero"], xalign=0.5))
            center.append(label("Turn on DreamON in the Extensions app (after installing, log out and back "
                                "in first). Your history still works.", ["dreamon-muted"], wrap=True,
                                xalign=0.5, max_width_chars=60, justify=Gtk.Justification.CENTER))
        elif sitting:
            self._tick()
            pulled = (sitting.get("minimized") or 0) + (sitting.get("covered") or 0)
            center.append(label("Nothing has pulled at you yet." if pulled == 0
                                else f"{plural(pulled, 'distraction')} set aside so far.",
                                ["dreamon-muted"], xalign=0.5))
            if sitting.get("exitMode") != "none":
                leave = Gtk.Button(label="Leave early…", halign=Gtk.Align.CENTER)
                leave.add_css_class("flat")
                leave.connect("clicked", lambda *_: self.link.leave())
                center.append(leave)
            else:
                center.append(label("This sitting ends with the bell.", ["dreamon-quote"], xalign=0.5))
        else:
            self.hero.set_view(t["minutes"] / DAILY_AIM, today.toordinal(),
                           short_minutes(t["minutes"]),
                           f"of {stats.fmt_minutes(DAILY_AIM)} today" if t["minutes"] < DAILY_AIM
                           else "the circle is closed")
            practices = st.get("practices") or []
            durations = st.get("durations") or [60]
            if not practices:
                center.append(label("No practices yet — add one in Settings.", ["dreamon-muted"], xalign=0.5))
            else:
                row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
                pick_p = Gtk.DropDown.new_from_strings(practices)
                pick_m = Gtk.DropDown.new_from_strings([stats.fmt_minutes(d) for d in durations])
                if self.sel_practice in practices:
                    pick_p.set_selected(practices.index(self.sel_practice))
                if self.sel_minutes in durations:
                    pick_m.set_selected(durations.index(self.sel_minutes))
                begin = Gtk.Button(label="Begin")
                begin.add_css_class("pill")
                begin.add_css_class("dreamon-begin")
                begin.connect("clicked", lambda *_: self._begin(
                    practices[pick_p.get_selected()], durations[pick_m.get_selected()]))
                row.append(pick_p)
                row.append(pick_m)
                row.append(begin)
                center.append(row)
                center.append(label("Set up your displays first — only this practice's apps stay on screen.",
                                    ["dreamon-caption"], xalign=0.5))

        box.append(rule())
        box.append(stat_row(
            (str(t["sittings"]), "sitting today" if t["sittings"] == 1 else "sittings today"),
            (str(t["pages"]), "pages read"),
            (str(t["minimized"] + t["covered"]), "distractions set aside"),
            (str(stats.streaks(self.sittings, today)[0]), "days in a row"),
        ))

        site = (st or {}).get("siteDay") or {}
        if site.get("sites"):
            box.append(rule())
            box.append(self.site_day_block(site))

        if todays:
            box.append(label("Today's sittings", ["dreamon-section"]))
            lb = listbox()
            for s in reversed(todays):
                lb.append(self.sitting_row(s))
            box.append(lb)

    def site_day_block(self, site: dict) -> Gtk.Box:
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        name = site.get("name", "No YouTube day")
        if site.get("active"):
            b.append(label(f"Today is a {name}", ["dreamon-subhead"]))
            a = site.get("attempts") or 0
            b.append(label("Not once so far." if a == 0
                           else f"You reached for it {plural(a, 'time')}, and let it go.", ["dreamon-muted"]))
            if site.get("everyDay"):
                b.append(label("Every day is one, by your settings.", ["dreamon-caption"]))
        else:
            row = Gtk.Box(spacing=16)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            text.append(label(f"A {name}?", ["dreamon-subhead"]))
            text.append(label("Its tabs stay covered until midnight, in sittings and between them. "
                              "Once begun, the day can't be taken back.", ["dreamon-muted"], wrap=True))
            btn = Gtk.Button(label="Begin the day", valign=Gtk.Align.CENTER)
            btn.add_css_class("pill")
            btn.connect("clicked", self._declare)
            row.append(text)
            row.append(btn)
            b.append(row)
        return b

    # Practice

    def render_practice(self) -> None:
        box = self.pages["practice"]
        clear(box)
        today = date.today()
        S = self.sittings
        if not S:
            box.append(label("Your practice will gather here", ["dreamon-hero"]))
            box.append(label("Each sitting adds to the day's ensō. The days fill in, week by week.",
                             ["dreamon-muted"], wrap=True))
            return
        week = stats.since(S, today - timedelta(days=6))
        month = stats.since(S, today - timedelta(days=29))
        cur, longest = stats.streaks(S, today)
        tm = stats.totals(month)
        kept = f"{round(100 * tm['completed'] / tm['sittings'])}%" if tm["sittings"] else "—"
        box.append(stat_row(
            (short_minutes(stats.totals(week)["minutes"]), "last 7 days"),
            (str(cur), "days in a row"),
            (str(longest), "longest run"),
            (kept, "kept to the bell, 30 days"),
        ))

        box.append(label("Last 7 days", ["dreamon-section"]))
        days_row = Gtk.Box(homogeneous=True, spacing=4)
        for d, mins in stats.minutes_by_day(S, today, 7):
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.append(DayEnso(self, d, mins, 72))
            col.append(label("Today" if d == today else d.strftime("%a"), ["dreamon-caption"], xalign=0.5))
            total = sum(mins.values())
            col.append(label(short_minutes(total) if total else "·", ["dreamon-muted"], xalign=0.5))
            days_row.append(col)
        box.append(days_row)
        legend = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER, margin_top=6)
        for name in self.practice_order():
            item = Gtk.Box(spacing=6)
            item.append(self.dot(name))
            item.append(label(name, ["dreamon-caption"]))
            legend.append(item)
        box.append(legend)

        box.append(label("Eighteen weeks", ["dreamon-section"]))
        box.append(EnsoCalendar(self, stats.heat(S, today, 18)))

        box.append(label("By practice · 30 days", ["dreamon-section"]))
        lb = listbox()
        counts: dict[str, int] = {}
        for s in month:
            counts[s.get("practice", "Other")] = counts.get(s.get("practice", "Other"), 0) + 1
        for name, mins in stats.minutes_by_practice(month):
            r = action_row(name, plural(counts.get(name, 0), "sitting"), stats.fmt_minutes(mins))
            r.add_prefix(self.dot(name))
            lb.append(r)
        box.append(lb)

    # Reading

    def render_reading(self) -> None:
        box = self.pages["reading"]
        clear(box)
        self.paper_rows = {}
        today = date.today()
        week = stats.since(self.sittings, today - timedelta(days=6))
        month = stats.since(self.sittings, today - timedelta(days=29))
        tw, tm = stats.totals(week), stats.totals(month)
        papers = stats.papers(month)
        pace = (f"{tm['pages'] / (tm['reading_seconds'] / 3600):.0f}"
                if tm["reading_seconds"] >= 600 else "—")
        box.append(stat_row(
            (str(tw["pages"]), "pages, last 7 days"),
            (short_minutes(round(tw["reading_seconds"] / 60)), "with the page, 7 days"),
            (str(len(papers)), "papers, 30 days"),
            (pace, "pages an hour"),
        ))

        box.append(label("Papers you sat with", ["dreamon-section"]))
        if not papers:
            box.append(label(
                "Pages are counted from the Ethereal Style plugin's reading-progress.json "
                "(a page counts after 30 s). Read in Zotero during a sitting and the papers gather here.",
                ["dreamon-muted"], wrap=True))
            return
        lb = listbox()
        missing = []
        for p in papers[:40]:
            info = self.resolver.cached(p["key"])
            if info is None:
                missing.append(p["key"])
            r = action_row(*self.paper_text(p, info))
            r.set_activatable(True)
            r.connect("activated", self._open_zotero, p["key"])
            r.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            self.paper_rows[p["key"]] = (r, p)
            lb.append(r)
        box.append(lb)
        box.append(label("Last 30 days. Open a paper to find it in Zotero.", ["dreamon-caption"]))
        if missing:
            threading.Thread(target=self._resolve, args=(missing,), daemon=True).start()

    @staticmethod
    def paper_text(p: dict, info: dict | None) -> tuple[str, str]:
        nums = (f"{plural(p['pages'], 'page')} · {stats.fmt_minutes(round(p['seconds'] / 60))}"
                f" · {plural(p['sittings'], 'sitting')}")
        if not info:
            return "Looking up in Zotero…", nums
        who = " ".join(x for x in (info.get("authors"), info.get("year")) if x)
        return info["title"], f"{who} · {nums}" if who else nums

    def _resolve(self, keys: list[str]) -> None:
        found = {k: self.resolver.resolve(k) for k in keys}
        self.resolver.save()
        GLib.idle_add(self._resolved, found)

    def _resolved(self, found: dict) -> bool:
        for key, info in found.items():
            entry = self.paper_rows.get(key)
            if entry is None:
                continue
            row, p = entry
            if info is None:
                row.set_title(f"Zotero item {key}")
                row.set_subtitle(self.paper_text(p, {"title": "", "authors": "Zotero isn't answering"})[1])
            else:
                title, sub = self.paper_text(p, info)
                row.set_title(title)
                row.set_subtitle(sub)
        return GLib.SOURCE_REMOVE

    # Distractions

    def render_distractions(self) -> None:
        box = self.pages["distractions"]
        clear(box)
        today = date.today()
        week = stats.since(self.sittings, today - timedelta(days=6))
        month = stats.since(self.sittings, today - timedelta(days=29))
        tw = stats.totals(week)
        sd = stats.site_days(self.days, today)
        site_name = ((self.state or {}).get("siteDay") or {}).get("name", "No YouTube day")
        box.append(stat_row(
            (str(tw["minimized"]), "windows set aside, 7 days"),
            (str(tw["covered"]), "tabs covered, 7 days"),
            (str(sd["days"]), f"{site_name}s"),
            (str(sd["clean"]), "of them untouched"),
        ))

        box.append(label("What pulled at you", ["dreamon-section"]))
        apps = stats.top(month, "apps")
        sites = stats.top(month, "sites")
        if not apps and not sites:
            box.append(label("Nothing yet. Quiet sittings leave no trace here.", ["dreamon-muted"], wrap=True))
        for title, items in (("Apps", apps), ("Tabs", sites)):
            if items:
                box.append(label(f"{title} · 30 days", ["dreamon-day"]))
                lb = listbox()
                for name, n in items:
                    lb.append(action_row(name, "", plural(n, "time")))
                box.append(lb)

        box.append(label(f"{site_name}s", ["dreamon-section"]))
        if not sd["recent"]:
            box.append(label("None yet. Begin one from the Today page or the ensō menu.",
                             ["dreamon-muted"], wrap=True))
            return
        box.append(label(f"{plural(sd['days'], 'day')} in all, {sd['run']} in a row to today · "
                         f"reached for it {plural(sd['attempts'], 'time')} altogether.",
                         ["dreamon-muted"], wrap=True))
        lb = listbox()
        for d, v in sd["recent"]:
            a = v.get("attempts") or 0
            lb.append(action_row(day_name(d, today),
                                 "every day" if v.get("how") == "every-day" else "begun by you",
                                 "untouched" if a == 0 else f"reached {plural(a, 'time')}"))
        box.append(lb)

    # History

    def render_history(self) -> None:
        box = self.pages["history"]
        clear(box)
        today = date.today()
        if not self.sittings:
            box.append(label("No sittings yet", ["dreamon-hero"]))
            box.append(label("Every sitting, kept or left, is noted here.", ["dreamon-muted"]))
            return
        current: date | None = None
        lb = None
        for s in list(reversed(self.sittings))[:200]:
            d = stats.day_of(s)
            if d != current:
                current = d
                day_total = stats.totals(stats.on_day(self.sittings, d))["minutes"]
                box.append(label(f"{day_name(d, today)} · {stats.fmt_minutes(day_total)}", ["dreamon-day"]))
                lb = listbox()
                box.append(lb)
            lb.append(self.sitting_row(s))


class App(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.provider: Gtk.CssProvider | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.provider = Gtk.CssProvider()
        style = Adw.StyleManager.get_default()
        # Load the light or dark sheet directly, and swap it when GNOME switches.
        self.provider.load_from_string(css_for(style.get_dark()))
        style.connect("notify::dark", lambda sm, _p: self.provider.load_from_string(css_for(sm.get_dark())))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self.provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

    def do_activate(self) -> None:
        win = self.props.active_window or Window(self)
        win.present()


def main() -> int:
    return App().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
