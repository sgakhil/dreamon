// DreamON — settings window (GNOME Shell 50, libadwaita).

import Adw from 'gi://Adw';
import Gtk from 'gi://Gtk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

const EXIT_MODES = [
    ['sentence', 'Write a sentence'],
    ['wait', 'Sit with it, then leave'],
    ['none', 'Stay until the bell'],
];

const splitList = text => text.split(',').map(s => s.trim()).filter(Boolean);

export default class DreamOnPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        window._settings = settings;
        window.set_default_size(640, 760);

        const page = new Adw.PreferencesPage({title: 'DreamON', icon_name: 'weather-clear-night-symbolic'});
        window.add(page);

        if (settings.get_string('active-mode')) {
            const banner = new Adw.PreferencesGroup({
                description: 'A sitting is in progress. Changes apply from the next one.',
            });
            page.add(banner);
        }

        page.add(this._exitGroup(settings));
        page.add(this._modesGroup(settings));
        page.add(this._sessionGroup(settings));
        page.add(this._siteDayGroup(settings));
        page.add(this._readingGroup(settings));
    }

    _siteDayGroup(settings) {
        const group = new Adw.PreferencesGroup({
            title: 'A day without',
            description: 'Declare a day from the ensō menu or the DreamON app, and tabs whose title names one of these sites ' +
                'stay covered until midnight, during sittings and between them. A declared day can\'t be taken back.',
        });

        const sites = new Adw.EntryRow({
            title: 'Sites, comma-separated (matched in the tab title)',
            text: settings.get_strv('blocked-sites').join(', '),
        });
        sites.connect('changed', () => settings.set_strv('blocked-sites', splitList(sites.text)));
        group.add(sites);

        const browsers = new Adw.EntryRow({
            title: 'Browsers watched, comma-separated',
            text: settings.get_strv('browsers').join(', '),
        });
        browsers.connect('changed', () => settings.set_strv('browsers', splitList(browsers.text)));
        group.add(browsers);

        const everyDay = new Adw.SwitchRow({
            title: 'Every day',
            subtitle: 'Keep these sites covered every day, without declaring',
        });
        settings.bind('every-day-site-block', everyDay, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(everyDay);
        return group;
    }

    _exitGroup(settings) {
        const group = new Adw.PreferencesGroup({
            title: 'Leaving early',
            description: 'Fixed when a sitting begins, so it can\'t be loosened halfway.',
        });

        const combo = new Adw.ComboRow({
            title: 'To leave early',
            model: Gtk.StringList.new(EXIT_MODES.map(m => m[1])),
        });
        combo.selected = Math.max(0, EXIT_MODES.findIndex(m => m[0] === settings.get_string('exit-mode')));
        group.add(combo);

        const sentence = new Adw.EntryRow({title: 'Sentence to write — {task} becomes the practice name'});
        settings.bind('exit-sentence', sentence, 'text', Gio.SettingsBindFlags.DEFAULT);
        group.add(sentence);

        const wait = Adw.SpinRow.new_with_range(5, 600, 5);
        wait.title = 'Time to sit with the choice';
        wait.subtitle = 'Seconds';
        settings.bind('exit-wait-seconds', wait, 'value', Gio.SettingsBindFlags.DEFAULT);
        group.add(wait);

        const sync = () => {
            sentence.sensitive = combo.selected === 0;
            wait.sensitive = combo.selected === 1;
        };
        combo.connect('notify::selected', () => {
            settings.set_string('exit-mode', EXIT_MODES[combo.selected][0]);
            sync();
        });
        sync();
        return group;
    }

    _modesGroup(settings) {
        const group = new Adw.PreferencesGroup({
            title: 'Practices',
            description: 'Apps match by window class or app ID, so "zotero" matches Zotero however it is installed. ' +
                'If a browser and tab title are set, that browser\'s wandering tabs are gently covered.',
        });

        let modes;
        try {
            modes = JSON.parse(settings.get_string('modes'));
            if (!Array.isArray(modes))
                modes = [];
        } catch (e) {
            modes = [];
        }
        const save = () => settings.set_string('modes', JSON.stringify(modes));

        let rows = [];
        const render = () => {
            for (const row of rows)
                group.remove(row);
            rows = [];
            modes.forEach((mode, index) => {
                const expander = new Adw.ExpanderRow({
                    title: mode.name || 'Untitled',
                    subtitle: (mode.apps ?? []).join(' + '),
                    use_markup: false,
                });

                const name = new Adw.EntryRow({title: 'Name', text: mode.name ?? ''});
                name.connect('changed', () => {
                    mode.name = name.text.trim();
                    expander.title = mode.name || 'Untitled';
                    save();
                });

                const apps = new Adw.EntryRow({
                    title: 'Allowed apps, comma-separated',
                    text: (mode.apps ?? []).join(', '),
                });
                apps.connect('changed', () => {
                    mode.apps = splitList(apps.text);
                    expander.subtitle = mode.apps.join(' + ');
                    save();
                });

                const browser = new Adw.EntryRow({
                    title: 'Browser to watch (optional)',
                    text: mode.browser ?? '',
                });
                browser.connect('changed', () => {
                    mode.browser = browser.text.trim();
                    save();
                });

                const title = new Adw.EntryRow({
                    title: 'Tab title must contain',
                    text: mode.titleMustContain ?? '',
                });
                title.connect('changed', () => {
                    mode.titleMustContain = title.text.trim();
                    save();
                });

                const remove = new Gtk.Button({
                    icon_name: 'user-trash-symbolic',
                    tooltip_text: 'Remove practice',
                    valign: Gtk.Align.CENTER,
                    css_classes: ['flat'],
                });
                remove.connect('clicked', () => {
                    modes.splice(index, 1);
                    save();
                    render();
                });

                expander.add_suffix(remove);
                expander.add_row(name);
                expander.add_row(apps);
                expander.add_row(browser);
                expander.add_row(title);
                group.add(expander);
                rows.push(expander);
            });
        };

        const add = new Gtk.Button({label: 'Add practice', valign: Gtk.Align.CENTER, css_classes: ['flat']});
        add.connect('clicked', () => {
            modes.push({name: 'New practice', apps: [], browser: '', titleMustContain: ''});
            save();
            render();
        });
        group.set_header_suffix(add);

        render();
        return group;
    }

    _sessionGroup(settings) {
        const group = new Adw.PreferencesGroup({title: 'Sittings'});

        const durations = new Adw.EntryRow({
            title: 'Lengths offered in the menu (minutes, comma-separated)',
            text: settings.get_value('durations').deepUnpack().join(', '),
        });
        durations.connect('changed', () => {
            const values = splitList(durations.text)
                .map(Number)
                .filter(n => Number.isInteger(n) && n > 0 && n <= 600);
            if (values.length)
                settings.set_value('durations', new GLib.Variant('ai', values));
        });
        group.add(durations);

        const always = new Adw.EntryRow({
            title: 'Always allowed apps, comma-separated',
            text: settings.get_strv('always-allowed').join(', '),
        });
        always.connect('changed', () => settings.set_strv('always-allowed', splitList(always.text)));
        group.add(always);

        const PANEL = [['icon', 'Ensō only'], ['minutes', 'Ensō and minutes left'], ['full', 'Practice and full countdown']];
        const panel = new Adw.ComboRow({
            title: 'Top bar during a sitting',
            model: Gtk.StringList.new(PANEL.map(p => p[1])),
        });
        panel.selected = Math.max(0, PANEL.findIndex(p => p[0] === settings.get_string('panel-display')));
        panel.connect('notify::selected', () => settings.set_string('panel-display', PANEL[panel.selected][0]));
        group.add(panel);

        const mute = new Adw.SwitchRow({
            title: 'Quiet notification banners during a sitting',
        });
        settings.bind('mute-notifications', mute, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(mute);

        const strip = Adw.SpinRow.new_with_range(0, 300, 5);
        strip.title = 'Browser tab bar height';
        strip.subtitle = 'Pixels left uncovered at the top of a covered browser window, so you can click back to your tab';
        settings.bind('tab-bar-height', strip, 'value', Gio.SettingsBindFlags.DEFAULT);
        group.add(strip);
        return group;
    }

    _readingGroup(settings) {
        const group = new Adw.PreferencesGroup({
            title: 'Reading progress',
            description: 'Reads the Ethereal Style plugin\'s reading-progress.json (never writes it) and reports pages read when the bell rings. A page counts after 30 s.',
        });

        const track = new Adw.SwitchRow({title: 'Report pages read'});
        settings.bind('track-reading-progress', track, 'active', Gio.SettingsBindFlags.DEFAULT);
        group.add(track);

        const path = new Adw.EntryRow({title: 'File (empty = ~/Zotero/reading-progress.json)'});
        settings.bind('reading-progress-path', path, 'text', Gio.SettingsBindFlags.DEFAULT);
        group.add(path);
        return group;
    }
}
