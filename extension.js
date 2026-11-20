// DreamON — a quiet room for deep work: only one practice's apps, for one sitting.
// GNOME Shell 50 (ES modules).
//
// Writes (and only reads its own files from) ~/.local/share/dreamon/:
//   sittings.jsonl       one line per finished sitting
//   days.json            No-<site> days and how often the site was reached for
//   progress-start.json  reading-progress snapshot taken when a sitting begins
// Exports io.github.sgakhil.DreamON on the session bus at /io/github/sgakhil/DreamON
// (on org.gnome.Shell) for the DreamON app. There is deliberately no method to end a sitting.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as ModalDialog from 'resource:///org/gnome/shell/ui/modalDialog.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const COVER_DELAY_MS = 1500;   // a tab must be off-task this long before it is covered
const NEW_WINDOW_DELAY_MS = 200;
const PAGE_READ_SECONDS = 30;  // same rule as Marginalia
const APP_ID = 'io.github.sgakhil.DreamON';
const DBUS_PATH = '/io/github/sgakhil/DreamON';
const DBUS_XML = `
<node>
  <interface name="io.github.sgakhil.DreamON">
    <method name="GetState">
      <arg type="s" direction="out" name="state"/>
    </method>
    <method name="Begin">
      <arg type="s" direction="in" name="practice"/>
      <arg type="u" direction="in" name="minutes"/>
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="DeclareSiteDay">
      <arg type="b" direction="out" name="ok"/>
    </method>
    <method name="OpenLeaveDialog"/>
    <signal name="StateChanged">
      <arg type="s" name="state"/>
    </signal>
  </interface>
</node>`;

const norm = s => String(s ?? '').trim().toLowerCase();
const squash = s => norm(s).replace(/\s+/g, ' ');
const today = () => GLib.DateTime.new_now_local().format('%Y-%m-%d');
const emptyCounters = () => ({minimized: 0, covered: 0, apps: {}, sites: {}});

function formatDuration(minutes) {
    if (minutes < 60)
        return `${minutes} min`;
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    if (m === 0)
        return h === 1 ? '1 hour' : `${h} hours`;
    return `${h} h ${m} min`;
}

function formatMinutes(secondsLeft) {
    const m = Math.ceil(Math.max(0, secondsLeft) / 60);
    return m <= 1 && secondsLeft < 60 ? '<1m' : `${m}m`;
}

function formatClock(secondsLeft) {
    const s = Math.max(0, Math.ceil(secondsLeft));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = String(s % 60).padStart(2, '0');
    return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

// "Some video - YouTube — Mozilla Firefox" -> "YouTube"
function siteFromTitle(title) {
    let t = String(title ?? '').trim();
    t = t.replace(/\s+[—–-]\s+(Mozilla Firefox|Firefox|Google Chrome|Chromium|Brave|Vivaldi|LibreWolf|Web)\s*$/i, '');
    const parts = t.split(/\s+[-–—|·]\s+/).map(p => p.trim()).filter(Boolean);
    const site = parts.length > 1 ? parts[parts.length - 1] : t;
    return site.slice(0, 60) || 'Untitled tab';
}

export default class DreamOnExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._tracker = Shell.WindowTracker.get_default();
        this._session = null;
        this._signals = [];
        this._covers = new Map();
        this._timeouts = new Set();
        this._tickId = 0;
        this._focusIdleId = 0;
        this._overviewIdleId = 0;
        this._menuIdleId = 0;
        this._dialog = null;
        this._dialogTimerId = 0;
        this._lastAllowed = null;
        this._dayRecorded = '';
        this._siteDayWasOn = false;
        this._bellId = 0;

        this._button = new PanelMenu.Button(0.0, 'DreamON', false);
        const box = new St.BoxLayout({style_class: 'panel-status-menu-box'});
        this._icon = new St.Icon({
            gicon: Gio.FileIcon.new(Gio.File.new_for_path(`${this.path}/icons/dreamon-symbolic.svg`)),
            style_class: 'system-status-icon',
        });
        this._label = new St.Label({
            text: '',
            y_align: Clutter.ActorAlign.CENTER,
            style_class: 'dreamon-label',
            visible: false,
        });
        box.add_child(this._icon);
        box.add_child(this._label);
        this._button.add_child(box);
        Main.panel.addToStatusArea(this.uuid, this._button);

        const siteRulesChanged = () => this._onSiteRulesChanged();
        this._settingsIds = [
            this._settings.connect('changed::modes', () => this._queueMenuRebuild()),
            this._settings.connect('changed::durations', () => this._queueMenuRebuild()),
            this._settings.connect('changed::panel-display', () => this._updateLabel()),
            this._settings.connect('changed::site-day', siteRulesChanged),
            this._settings.connect('changed::every-day-site-block', siteRulesChanged),
            this._settings.connect('changed::blocked-sites', siteRulesChanged),
            this._settings.connect('changed::browsers', siteRulesChanged),
        ];

        try {
            this._dbus = Gio.DBusExportedObject.wrapJSObject(DBUS_XML, this);
            this._dbus.export(Gio.DBus.session, DBUS_PATH);
        } catch (e) {
            console.warn(`DreamON: could not export D-Bus interface: ${e.message}`);
            this._dbus = null;
        }

        this._buildMenu();
        this._resume();
        this._siteDayWasOn = this._siteDayOn();
        this._ensureDayRecord();
        this._updateCovers();
        this._syncTick();
    }

    disable() {
        // Tear down the running sitting but keep its state in settings,
        // so it resumes after the screen is unlocked or after logging back in.
        this._teardownSession();
        this._stopTick();
        this._disarmBell();
        for (const cover of this._covers.values())
            this._destroyCover(cover);
        this._covers.clear();
        if (this._menuIdleId) {
            GLib.source_remove(this._menuIdleId);
            this._menuIdleId = 0;
        }
        for (const id of this._settingsIds ?? [])
            this._settings.disconnect(id);
        this._settingsIds = null;
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._button?.destroy();
        this._button = null;
        this._icon = null;
        this._label = null;
        this._statusItem = null;
        this._settings = null;
        this._tracker = null;
    }

    // ---------- D-Bus interface for the DreamON app ----------

    GetState() {
        const s = this._session;
        const sites = this._blockedSites();
        const days = this._readJson('days.json') ?? {};
        return JSON.stringify({
            version: 2,
            sitting: s ? {
                practice: s.mode.name,
                start: s.start,
                end: s.end,
                exitMode: s.exitMode,
                minimized: s.counters.minimized,
                covered: s.counters.covered,
            } : null,
            practices: this._modes().map(m => m.name),
            durations: this._durations(),
            siteDay: {
                sites,
                name: this._siteDayName(),
                active: this._siteDayOn(),
                everyDay: this._settings.get_boolean('every-day-site-block'),
                attempts: days[today()]?.attempts ?? 0,
            },
        });
    }

    Begin(practice, minutes) {
        if (this._session || minutes < 1 || minutes > 600)
            return false;
        const mode = this._modes().find(m => m.name === practice);
        if (!mode)
            return false;
        this._start(mode, minutes);
        return true;
    }

    DeclareSiteDay() {
        return this._declareSiteDay();
    }

    OpenLeaveDialog() {
        this._openStopDialog();
    }

    _emitState() {
        try {
            this._dbus?.emit_signal('StateChanged', new GLib.Variant('(s)', [this.GetState()]));
        } catch (e) {}
    }

    // ---------- own data files ----------

    _dataPath(name) {
        const dir = GLib.build_filenamev([GLib.get_user_data_dir(), 'dreamon']);
        GLib.mkdir_with_parents(dir, 0o755);
        return GLib.build_filenamev([dir, name]);
    }

    _readJson(name) {
        try {
            const [, bytes] = Gio.File.new_for_path(this._dataPath(name)).load_contents(null);
            return JSON.parse(new TextDecoder().decode(bytes));
        } catch (e) {
            return null;
        }
    }

    _writeJson(name, value) {
        try {
            Gio.File.new_for_path(this._dataPath(name)).replace_contents(
                new TextEncoder().encode(JSON.stringify(value)), null, false,
                Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        } catch (e) {
            console.warn(`DreamON: could not write ${name}: ${e.message}`);
        }
    }

    _appendLine(name, value) {
        try {
            const stream = Gio.File.new_for_path(this._dataPath(name))
                .append_to(Gio.FileCreateFlags.NONE, null);
            stream.write_all(new TextEncoder().encode(`${JSON.stringify(value)}\n`), null);
            stream.close(null);
        } catch (e) {
            console.warn(`DreamON: could not append to ${name}: ${e.message}`);
        }
    }

    // ---------- modes and menu ----------

    _modes() {
        try {
            const modes = JSON.parse(this._settings.get_string('modes'));
            if (Array.isArray(modes))
                return modes.filter(m => m && m.name);
        } catch (e) {
            console.warn(`DreamON: could not read practices: ${e.message}`);
        }
        return [];
    }

    _durations() {
        const list = this._settings.get_value('durations').deepUnpack();
        const clean = list.filter(n => Number.isInteger(n) && n > 0);
        return clean.length ? clean : [60];
    }

    _queueMenuRebuild() {
        if (this._menuIdleId)
            return;
        this._menuIdleId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._menuIdleId = 0;
            this._buildMenu();
            return GLib.SOURCE_REMOVE;
        });
    }

    _buildMenu() {
        if (!this._button)
            return;
        const menu = this._button.menu;
        menu.removeAll();
        this._statusItem = null;

        if (this._session) {
            const end = GLib.DateTime.new_from_unix_local(this._session.end).format('%H:%M');
            this._statusItem = new PopupMenu.PopupMenuItem(
                `${this._session.mode.name} · until ${end}`, {reactive: false});
            menu.addMenuItem(this._statusItem);
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            if (this._session.exitMode === 'none') {
                menu.addMenuItem(new PopupMenu.PopupMenuItem(
                    'This sitting ends with the bell', {reactive: false}));
            } else {
                const stop = new PopupMenu.PopupMenuItem('Leave early…');
                stop.connect('activate', () => this._openStopDialog());
                menu.addMenuItem(stop);
            }
        } else {
            const durations = this._durations();
            const modes = this._modes();
            const header = new PopupMenu.PopupMenuItem('Begin a sitting', {reactive: false});
            header.add_style_class_name('dreamon-menu-header');
            menu.addMenuItem(header);
            if (modes.length === 0) {
                menu.addMenuItem(new PopupMenu.PopupMenuItem(
                    'No practices yet — open Settings', {reactive: false}));
            }
            for (const mode of modes) {
                const sub = new PopupMenu.PopupSubMenuMenuItem(mode.name);
                for (const minutes of durations) {
                    const item = new PopupMenu.PopupMenuItem(formatDuration(minutes));
                    item.connect('activate', () => this._start(mode, minutes));
                    sub.menu.addMenuItem(item);
                }
                menu.addMenuItem(sub);
            }
        }

        if (this._blockedSites().length) {
            menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
            if (this._siteDayOn()) {
                const when = this._settings.get_boolean('every-day-site-block') ? 'every day' : 'until midnight';
                menu.addMenuItem(new PopupMenu.PopupMenuItem(
                    `${this._siteDayName()} · ${when}`, {reactive: false}));
            } else {
                const declare = new PopupMenu.PopupMenuItem(`Make today a ${this._siteDayName()}`);
                declare.connect('activate', () => this._declareSiteDay());
                menu.addMenuItem(declare);
            }
        }

        menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const app = Shell.AppSystem.get_default().lookup_app(`${APP_ID}.desktop`);
        if (app) {
            const open = new PopupMenu.PopupMenuItem('Open DreamON');
            open.connect('activate', () => app.activate());
            menu.addMenuItem(open);
        }
        if (!this._session) {
            const prefs = new PopupMenu.PopupMenuItem('Settings');
            prefs.connect('activate', () => this.openPreferences());
            menu.addMenuItem(prefs);
        }
    }

    _updateLabel() {
        if (!this._label)
            return;
        if (!this._session) {
            this._label.text = '';
            this._label.visible = false;
            return;
        }
        const left = this._session.end - Date.now() / 1000;
        const style = this._settings.get_string('panel-display');
        if (style === 'icon') {
            this._label.visible = false;
        } else {
            const time = style === 'full' ? formatClock(left) : formatMinutes(left);
            this._label.text = style === 'full' ? `${this._session.mode.name} ${time}` : time;
            this._label.visible = true;
        }
        if (this._statusItem) {
            this._statusItem.label.text =
                `${this._session.mode.name} · ${formatClock(left)} remain`;
        }
    }

    // ---------- ticking ----------

    _tickNeeded() {
        return !!this._session || this._siteDayOn();
    }

    _syncTick() {
        if (this._tickNeeded() && !this._tickId)
            this._tickId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => this._tick());
    }

    _stopTick() {
        if (this._tickId) {
            GLib.source_remove(this._tickId);
            this._tickId = 0;
        }
    }

    // Each step is guarded: in GJS an error thrown inside a timeout callback
    // silently removes the timeout, which used to stop the bell for good.
    _safely(what, fn) {
        try {
            fn();
        } catch (e) {
            console.error(`DreamON: ${what} failed: ${e.message}\n${e.stack}`);
        }
    }

    _ringIfDue() {
        if (this._session && Date.now() / 1000 >= this._session.end)
            this._safely('finishing the sitting', () => this._finish(true));
    }

    // A second, independent path to the bell: one timeout set for the end itself.
    _armBell() {
        this._disarmBell();
        if (!this._session)
            return;
        const wait = Math.max(1, Math.ceil(this._session.end - Date.now() / 1000));
        this._bellId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, wait, () => {
            this._bellId = 0;
            this._ringIfDue();
            if (this._session)          // woke early (clock change); try again
                this._armBell();
            return GLib.SOURCE_REMOVE;
        });
    }

    _disarmBell() {
        if (this._bellId) {
            GLib.source_remove(this._bellId);
            this._bellId = 0;
        }
    }

    _tick() {
        this._ringIfDue();
        if (this._session)
            this._safely('setting windows aside', () => this._enforce(true));

        this._safely('checking the day', () => {
            const siteOn = this._siteDayOn();
            if (siteOn !== this._siteDayWasOn) {      // midnight passed, or a new day began
                this._siteDayWasOn = siteOn;
                this._queueMenuRebuild();
                this._emitState();
            }
            this._ensureDayRecord();
        });
        this._safely('covering tabs', () => this._updateCovers());
        this._safely('updating the label', () => this._updateLabel());

        if (!this._tickNeeded()) {
            this._tickId = 0;
            return GLib.SOURCE_REMOVE;
        }
        return GLib.SOURCE_CONTINUE;
    }

    // ---------- sitting lifecycle ----------

    _start(mode, minutes) {
        if (this._session)
            return;
        const s = this._settings;
        const now = Math.floor(Date.now() / 1000);
        s.set_string('active-mode', JSON.stringify(mode));
        s.set_int64('session-start', now);
        s.set_int64('session-end', now + minutes * 60);
        s.set_string('session-exit-mode', s.get_string('exit-mode'));
        s.set_string('session-counters', JSON.stringify(emptyCounters()));

        const progress = s.get_boolean('track-reading-progress') ? this._readProgress() : null;
        this._writeJson('progress-start.json', progress ? progress.byKey : null);

        if (s.get_boolean('mute-notifications'))
            this._mute();

        this._beginSession();
    }

    _resume() {
        const raw = this._settings.get_string('active-mode');
        if (!raw)
            return;
        if (Date.now() / 1000 < this._settings.get_int64('session-end')) {
            this._beginSession();
            return;
        }
        // Ended while the screen was locked or the user was logged out.
        let name = 'Your';
        try {
            name = JSON.parse(raw).name || name;
        } catch (e) {}
        const s = this._settings;
        const start = s.get_int64('session-start');
        const end = s.get_int64('session-end');
        const delta = this._readingDelta();
        this._logSitting({
            practice: name, start, plannedEnd: end, end, completed: true,
            counters: this._storedCounters(), delta,
        });
        this._clearSessionKeys();
        this._unmute();
        Main.notify(`The bell — ${name} sitting complete`,
            `It ended while you were away.${this._readingText(delta)}`);
    }

    _storedCounters() {
        try {
            const c = JSON.parse(this._settings.get_string('session-counters'));
            if (c && typeof c === 'object')
                return {...emptyCounters(), ...c};
        } catch (e) {}
        return emptyCounters();
    }

    _beginSession() {
        const s = this._settings;
        let mode = null;
        try {
            mode = JSON.parse(s.get_string('active-mode'));
        } catch (e) {}
        if (!mode || !mode.name) {
            this._clearSessionKeys();
            this._unmute();
            return;
        }

        const exitMode = s.get_string('session-exit-mode');
        this._session = {
            mode,
            start: s.get_int64('session-start'),
            end: s.get_int64('session-end'),
            exitMode: ['sentence', 'wait', 'none'].includes(exitMode) ? exitMode : 'sentence',
            apps: (mode.apps ?? []).map(norm).filter(Boolean),
            always: [...s.get_strv('always-allowed'), APP_ID].map(norm).filter(Boolean),
            browser: norm(mode.browser),
            title: norm(mode.titleMustContain),
            titleShown: String(mode.titleMustContain ?? '').trim(),
            counters: this._storedCounters(),
        };

        const display = global.display;
        this._connect(display, 'notify::focus-window', () => this._onFocus());
        this._connect(display, 'window-created', (_d, win) => this._onWindowCreated(win));
        this._connect(Main.overview, 'showing', () => this._blockOverview());
        if (Main.overview.visible)
            this._blockOverview();

        this._enforce(false);   // clearing the desk at the start isn't a distraction
        this._updateCovers();
        this._updateLabel();
        this._buildMenu();
        this._syncTick();
        this._armBell();
        this._emitState();
    }

    _finish(completed) {
        const session = this._session;
        if (!session)
            return;
        const now = Math.floor(Date.now() / 1000);
        const end = completed ? Math.min(now, session.end) : now;
        const minutes = Math.max(0, Math.round((end - session.start) / 60));
        const delta = this._readingDelta();
        this._logSitting({
            practice: session.mode.name, start: session.start, plannedEnd: session.end,
            end, completed, counters: session.counters, delta,
        });

        this._teardownSession();
        this._clearSessionKeys();
        this._unmute();

        const name = session.mode.name;
        Main.notify(
            completed ? `The bell — ${name} sitting complete` : `${name} set down early`,
            `${formatDuration(minutes)} of practice.${this._readingText(delta)}`);
        this._updateCovers();
        this._updateLabel();
        this._queueMenuRebuild();
        this._emitState();
    }

    _logSitting({practice, start, plannedEnd, end, completed, counters, delta}) {
        this._appendLine('sittings.jsonl', {
            v: 1,
            practice,
            start,
            end,
            plannedEnd,
            plannedMinutes: Math.round((plannedEnd - start) / 60),
            minutes: Math.max(0, Math.round((end - start) / 60)),
            completed,
            pages: delta?.pages ?? null,
            readingSeconds: delta?.seconds ?? null,
            papers: delta?.papers ?? [],
            minimized: counters.minimized,
            covered: counters.covered,
            apps: counters.apps,
            sites: counters.sites,
        });
        this._writeJson('progress-start.json', null);
    }

    _teardownSession() {
        for (const [obj, id] of this._signals ?? [])
            obj.disconnect(id);
        this._signals = [];

        for (const id of [this._focusIdleId, this._overviewIdleId]) {
            if (id)
                GLib.source_remove(id);
        }
        this._focusIdleId = this._overviewIdleId = 0;

        for (const id of this._timeouts ?? [])
            GLib.source_remove(id);
        this._timeouts?.clear();

        this._closeDialog();
        this._disarmBell();
        this._lastAllowed = null;
        this._session = null;
    }

    _clearSessionKeys() {
        const s = this._settings;
        s.set_string('active-mode', '');
        s.set_int64('session-start', 0);
        s.set_int64('session-end', 0);
        s.set_string('session-exit-mode', '');
        s.set_string('session-counters', '');
    }

    _connect(obj, signal, fn) {
        this._signals.push([obj, obj.connect(signal, fn)]);
    }

    _bump(kind, name) {
        const c = this._session?.counters;
        if (!c)
            return;
        const key = kind === 'apps' ? 'minimized' : 'covered';
        c[key]++;
        c[kind][name] = (c[kind][name] ?? 0) + 1;
        this._settings.set_string('session-counters', JSON.stringify(c));
    }

    // ---------- notifications ----------

    _mute() {
        const n = new Gio.Settings({schema_id: 'org.gnome.desktop.notifications'});
        if (n.get_boolean('show-banners')) {
            n.set_boolean('show-banners', false);
            this._settings.set_boolean('muted-banners', true);
        }
    }

    _unmute() {
        if (!this._settings.get_boolean('muted-banners'))
            return;
        const n = new Gio.Settings({schema_id: 'org.gnome.desktop.notifications'});
        n.set_boolean('show-banners', true);
        this._settings.set_boolean('muted-banners', false);
    }

    // ---------- reading progress (Ethereal Style; read-only) ----------

    _progressPath() {
        const home = GLib.get_home_dir();
        let path = this._settings.get_string('reading-progress-path').trim();
        if (!path)
            return GLib.build_filenamev([home, 'Zotero', 'reading-progress.json']);
        if (path === '~' || path.startsWith('~/'))
            path = home + path.slice(1);
        return path;
    }

    _readProgress() {
        try {
            const [, bytes] = Gio.File.new_for_path(this._progressPath()).load_contents(null);
            const data = JSON.parse(new TextDecoder().decode(bytes));
            const byKey = {};
            for (const [key, entry] of Object.entries(data ?? {})) {
                if (key === 'undefined')
                    continue;
                const perPage = entry?.readingTime?.data;
                if (!perPage || typeof perPage !== 'object')
                    continue;
                let p = 0;
                let s = 0;
                for (const value of Object.values(perPage)) {
                    const n = Number(value);
                    if (!Number.isFinite(n) || n <= 0)
                        continue;
                    s += n;
                    if (n >= PAGE_READ_SECONDS)
                        p++;
                }
                byKey[key] = {p, s: Math.round(s)};
            }
            return {byKey};
        } catch (e) {
            return null;
        }
    }

    _readingDelta() {
        const start = this._readJson('progress-start.json');
        if (!start || typeof start !== 'object')
            return null;
        const now = this._readProgress();
        if (!now)
            return null;
        let pages = 0;
        let seconds = 0;
        const papers = [];
        for (const [key, v] of Object.entries(now.byKey)) {
            const before = start[key] ?? {p: 0, s: 0};
            const dp = Math.max(0, v.p - (before.p ?? 0));
            const ds = Math.max(0, v.s - (before.s ?? 0));
            if (dp > 0 || ds >= PAGE_READ_SECONDS)
                papers.push({key, pages: dp, seconds: ds});
            pages += dp;
            seconds += ds;
        }
        papers.sort((a, b) => b.seconds - a.seconds);
        return {pages, seconds, papers};
    }

    _readingText(delta) {
        if (!delta)
            return '';
        const minutes = Math.round(delta.seconds / 60);
        if (delta.pages === 0 && minutes === 0)
            return '';
        const pagesText = delta.pages === 1 ? '1 new page read' : `${delta.pages} new pages read`;
        return ` ${pagesText} in Zotero, ${formatDuration(minutes)} with the page.`;
    }

    // ---------- No-<site> day ----------

    _blockedSites() {
        return this._settings.get_strv('blocked-sites').map(s => s.trim()).filter(Boolean);
    }

    _browserPatterns() {
        return this._settings.get_strv('browsers').map(norm).filter(Boolean);
    }

    _siteDayName() {
        const sites = this._blockedSites();
        return sites.length ? `No ${sites.join(' / ')} day` : 'Quiet web day';
    }

    _siteDayOn() {
        if (!this._blockedSites().length)
            return false;
        return this._settings.get_boolean('every-day-site-block') ||
            this._settings.get_string('site-day') === today();
    }

    _declareSiteDay() {
        if (!this._blockedSites().length)
            return false;
        if (!this._siteDayOn())
            this._settings.set_string('site-day', today());   // handled in _onSiteRulesChanged
        return true;
    }

    _onSiteRulesChanged() {
        this._siteDayWasOn = this._siteDayOn();
        this._dayRecorded = '';
        this._ensureDayRecord();
        this._updateCovers();
        this._syncTick();
        this._queueMenuRebuild();
        this._emitState();
    }

    _ensureDayRecord() {
        const t = today();
        if (this._dayRecorded === t || !this._siteDayOn())
            return;
        const days = this._readJson('days.json') ?? {};
        if (!days[t]) {
            days[t] = {
                sites: this._blockedSites(),
                how: this._settings.get_boolean('every-day-site-block') ? 'every-day' : 'declared',
                attempts: 0,
            };
            this._writeJson('days.json', days);
        }
        this._dayRecorded = t;
    }

    _bumpDayAttempt(site) {
        const t = today();
        const days = this._readJson('days.json') ?? {};
        const day = days[t] ?? {sites: this._blockedSites(), how: 'declared', attempts: 0};
        day.attempts = (day.attempts ?? 0) + 1;
        day.bySite = day.bySite ?? {};
        day.bySite[site] = (day.bySite[site] ?? 0) + 1;
        days[t] = day;
        this._writeJson('days.json', days);
        this._emitState();
    }

    // ---------- window policing ----------

    _windows() {
        return global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(win => win && win.get_compositor_private());
    }

    _root(win) {
        let root = win;
        for (let i = 0; i < 10 && root.get_transient_for(); i++)
            root = root.get_transient_for();
        return root;
    }

    _ids(win) {
        const ids = [];
        for (const fn of ['get_wm_class', 'get_wm_class_instance',
            'get_gtk_application_id', 'get_sandboxed_app_id']) {
            try {
                const value = win[fn]?.();
                if (value)
                    ids.push(value.toLowerCase());
            } catch (e) {}
        }
        const app = this._tracker.get_window_app(win);
        if (app?.get_id())
            ids.push(app.get_id().toLowerCase());
        return ids;
    }

    _matches(win, patterns) {
        if (!patterns.length)
            return false;
        const ids = this._ids(win);
        return patterns.some(p => ids.some(id => id.includes(p)));
    }

    _appName(win) {
        const root = this._root(win);
        const app = this._tracker.get_window_app(root);
        return app?.get_name() || root.get_wm_class() || 'Unknown app';
    }

    // Only ordinary app windows (and their dialogs) are policed; menus,
    // tooltips, the desktop and shell UI are left alone.
    _isPoliced(win) {
        const root = this._root(win);
        return root.get_window_type() === Meta.WindowType.NORMAL && !root.is_skip_taskbar();
    }

    _isAllowed(win) {
        const root = this._root(win);
        return this._matches(root, this._session.apps) ||
            this._matches(root, this._session.always);
    }

    _minimize(win, count) {
        if (win.minimized || !win.can_minimize())
            return;
        win.minimize();
        if (count)
            this._bump('apps', this._appName(win));
    }

    _enforce(count) {
        if (!this._session)
            return;
        for (const win of this._windows()) {
            if (this._isPoliced(win) && !this._isAllowed(win))
                this._minimize(win, count);
        }
    }

    _onFocus() {
        if (!this._session || this._focusIdleId)
            return;
        this._focusIdleId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._focusIdleId = 0;
            this._checkFocus();
            return GLib.SOURCE_REMOVE;
        });
    }

    _checkFocus() {
        if (!this._session)
            return;
        const win = global.display.focus_window;
        if (!win || !this._isPoliced(win))
            return;
        if (this._isAllowed(win)) {
            this._lastAllowed = win;
            return;
        }
        this._minimize(win, true);
        const back = this._pickAllowed();
        back?.activate(global.get_current_time());
    }

    _pickAllowed() {
        const last = this._lastAllowed;
        if (last && last.get_compositor_private())
            return last;
        return this._windows().find(w => this._isPoliced(w) && this._isAllowed(w) && !w.minimized) ??
            null;
    }

    _onWindowCreated(win) {
        const id = GLib.timeout_add(GLib.PRIORITY_DEFAULT, NEW_WINDOW_DELAY_MS, () => {
            this._timeouts.delete(id);
            if (this._session && win.get_compositor_private() &&
                this._isPoliced(win) && !this._isAllowed(win))
                this._minimize(win, true);
            return GLib.SOURCE_REMOVE;
        });
        this._timeouts.add(id);
    }

    _blockOverview() {
        if (this._overviewIdleId)
            return;
        this._overviewIdleId = GLib.idle_add(GLib.PRIORITY_DEFAULT, () => {
            this._overviewIdleId = 0;
            if (this._session)
                Main.overview.hide();
            return GLib.SOURCE_REMOVE;
        });
    }

    // ---------- browser tab covers ----------

    _coverReason(win, siteOn) {
        const title = win.get_title() ?? '';
        const lower = title.toLowerCase();
        if (siteOn && this._matches(win, this._browserPatterns())) {
            const hit = this._blockedSites().find(site => lower.includes(site.toLowerCase()));
            if (hit)
                return {kind: 'site', name: hit};
        }
        const s = this._session;
        if (s && s.browser && s.title && this._matches(win, [s.browser]) && !lower.includes(s.title))
            return {kind: 'task', name: siteFromTitle(title)};
        return null;
    }

    _updateCovers() {
        if (!this._settings)
            return;
        const s = this._session;
        const siteOn = this._siteDayOn();
        const patterns = [
            ...(siteOn ? this._browserPatterns() : []),
            ...(s && s.browser && s.title ? [s.browser] : []),
        ];
        const seen = new Set();
        if (patterns.length) {
            const workspace = global.workspace_manager.get_active_workspace();
            const now = Date.now();
            for (const win of this._windows()) {
                if (win.get_window_type() !== Meta.WindowType.NORMAL || !this._matches(win, patterns))
                    continue;
                seen.add(win);
                let cover = this._covers.get(win);
                if (!cover) {
                    cover = this._newCover(win);
                    this._covers.set(win, cover);
                }
                const reason = this._coverReason(win, siteOn);
                if (!reason) {
                    cover.reasonKey = null;
                    cover.actor?.hide();
                    continue;
                }
                const key = `${reason.kind}:${reason.name}`;
                if (cover.reasonKey !== key) {
                    cover.reasonKey = key;
                    cover.offSince = now;
                    cover.counted = false;
                }
                const visible = !win.minimized && win.located_on_workspace(workspace);
                if (visible && now - cover.offSince >= COVER_DELAY_MS) {
                    this._showCover(win, cover, reason);
                    if (!cover.counted) {
                        cover.counted = true;
                        this._countCover(reason);
                    }
                } else {
                    cover.actor?.hide();
                }
            }
        }
        for (const [win, cover] of this._covers) {
            if (!seen.has(win)) {
                this._destroyCover(cover);
                this._covers.delete(win);
            }
        }
    }

    _countCover(reason) {
        if (reason.kind === 'site')
            this._bumpDayAttempt(reason.name);
        if (this._session)
            this._bump('sites', reason.name);
    }

    _newCover(win) {
        const cover = {win, actor: null, offSince: 0, reasonKey: null, counted: false, ids: []};
        const refresh = () => this._updateCovers();
        for (const signal of ['notify::title', 'position-changed', 'size-changed', 'notify::minimized'])
            cover.ids.push(win.connect(signal, refresh));
        cover.ids.push(win.connect('unmanaged', () => {
            this._destroyCover(cover);
            this._covers.delete(win);
        }));
        return cover;
    }

    _showCover(win, cover, reason) {
        if (!cover.actor) {
            const actor = new St.BoxLayout({
                style_class: 'dreamon-cover',
                orientation: Clutter.Orientation.VERTICAL,
                reactive: true,
            });
            const inner = new St.BoxLayout({
                orientation: Clutter.Orientation.VERTICAL,
                x_align: Clutter.ActorAlign.CENTER,
                y_align: Clutter.ActorAlign.CENTER,
                x_expand: true,
                y_expand: true,
            });
            inner.add_child(new St.Icon({
                gicon: Gio.FileIcon.new(Gio.File.new_for_path(`${this.path}/icons/dreamon-symbolic.svg`)),
                icon_size: 96,
                style_class: 'dreamon-cover-enso',
                x_align: Clutter.ActorAlign.CENTER,
            }));
            cover.titleLabel = new St.Label({
                style_class: 'dreamon-cover-title',
                x_align: Clutter.ActorAlign.CENTER,
            });
            cover.textLabel = new St.Label({
                style_class: 'dreamon-cover-text',
                x_align: Clutter.ActorAlign.CENTER,
            });
            cover.textLabel.clutter_text.line_wrap = true;
            inner.add_child(cover.titleLabel);
            inner.add_child(cover.textLabel);
            actor.add_child(inner);
            Main.layoutManager.addChrome(actor, {affectsInputRegion: true});
            cover.actor = actor;
        }
        if (reason.kind === 'site') {
            cover.titleLabel.text = 'Not today';
            cover.textLabel.text = `Today is a ${this._siteDayName()}.\n` +
                'Let this tab go with Ctrl+W, or switch with Ctrl+Tab.';
        } else {
            cover.titleLabel.text = 'Return to the page';
            cover.textLabel.text =
                `The mind wandered to a tab that isn't ${this._session.titleShown}. That's all right.\n` +
                'Breathe, then come back: click your document\'s tab above, Ctrl+Tab to switch, or Ctrl+W to let this one go.';
        }
        const rect = win.get_frame_rect();
        const strip = win.is_fullscreen() ? 0 : this._settings.get_int('tab-bar-height');
        cover.actor.set_position(rect.x, rect.y + strip);
        cover.actor.set_size(rect.width, Math.max(0, rect.height - strip));
        cover.actor.show();
    }

    _destroyCover(cover) {
        for (const id of cover.ids) {
            try {
                cover.win.disconnect(id);
            } catch (e) {}
        }
        cover.ids = [];
        if (cover.actor) {
            Main.layoutManager.removeChrome(cover.actor);
            cover.actor.destroy();
            cover.actor = null;
        }
    }

    // ---------- leaving early ----------

    _sentence() {
        const template = this._settings.get_string('exit-sentence').trim() ||
            'I choose to set down my {task} before the bell.';
        return template.replaceAll('{task}', this._session.mode.name.toLowerCase());
    }

    _openStopDialog() {
        if (this._dialog || !this._session || this._session.exitMode === 'none')
            return;
        const session = this._session;
        const dialog = new ModalDialog.ModalDialog({styleClass: 'dreamon-dialog'});
        this._dialog = dialog;
        dialog.connect('destroy', () => {
            if (this._dialogTimerId) {
                GLib.source_remove(this._dialogTimerId);
                this._dialogTimerId = 0;
            }
            if (this._dialog === dialog)
                this._dialog = null;
        });

        const content = new St.BoxLayout({
            orientation: Clutter.Orientation.VERTICAL,
            style_class: 'dreamon-dialog-content',
        });
        dialog.contentLayout.add_child(content);
        content.add_child(new St.Icon({
            gicon: Gio.FileIcon.new(Gio.File.new_for_path(`${this.path}/icons/dreamon-symbolic.svg`)),
            icon_size: 56,
            style_class: 'dreamon-dialog-enso',
            x_align: Clutter.ActorAlign.CENTER,
        }));
        content.add_child(new St.Label({
            text: `Leave this ${session.mode.name.toLowerCase()} sitting?`,
            style_class: 'dreamon-dialog-title',
        }));
        const left = formatClock(session.end - Date.now() / 1000);
        content.add_child(new St.Label({
            text: `${left} remain before the bell.`,
            style_class: 'dreamon-dialog-text',
        }));

        const stopNow = () => {
            this._dialog = null;
            dialog.close();
            this._finish(false);
        };
        dialog.addButton({
            label: 'Stay',
            action: () => dialog.close(),
            key: Clutter.KEY_Escape,
            default: true,
        });
        const stopButton = dialog.addButton({label: 'Leave', action: () => {
            if (stopButton.reactive)
                stopNow();
        }});
        stopButton.reactive = false;
        stopButton.can_focus = false;
        const enableStop = on => {
            stopButton.reactive = on;
            stopButton.can_focus = on;
        };

        if (session.exitMode === 'sentence') {
            const sentence = this._sentence();
            content.add_child(new St.Label({
                text: 'If you truly wish to leave, write:',
                style_class: 'dreamon-dialog-text',
            }));
            const shown = new St.Label({text: sentence, style_class: 'dreamon-sentence'});
            shown.clutter_text.line_wrap = true;
            content.add_child(shown);
            const entry = new St.Entry({
                style_class: 'dreamon-entry',
                can_focus: true,
                hint_text: 'Write slowly…',
            });
            content.add_child(entry);
            entry.clutter_text.connect('text-changed', () =>
                enableStop(squash(entry.get_text()) === squash(sentence)));
            entry.clutter_text.connect('activate', () => {
                if (stopButton.reactive)
                    stopNow();
            });
            dialog.setInitialKeyFocus(entry);
        } else {
            let remaining = this._settings.get_int('exit-wait-seconds');
            const waitLabel = new St.Label({style_class: 'dreamon-dialog-text'});
            content.add_child(waitLabel);
            const update = () => {
                waitLabel.text = remaining > 0
                    ? `Sit with the choice for ${remaining} s.`
                    : 'You may leave, or stay.';
                enableStop(remaining <= 0);
            };
            update();
            this._dialogTimerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 1, () => {
                remaining--;
                update();
                if (remaining <= 0) {
                    this._dialogTimerId = 0;
                    return GLib.SOURCE_REMOVE;
                }
                return GLib.SOURCE_CONTINUE;
            });
        }

        dialog.open();
    }

    _closeDialog() {
        if (this._dialogTimerId) {
            GLib.source_remove(this._dialogTimerId);
            this._dialogTimerId = 0;
        }
        if (this._dialog) {
            const dialog = this._dialog;
            this._dialog = null;
            dialog.close();
        }
    }
}
