#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Brage Dorin
"""Scorpion K7 — native Linux configurator (GTK4 / libadwaita).

Every keyboard operation runs in a short session that ALWAYS ends with the
release command (07 20), so the keyboard goes back to normal typing.
"""
import json
import os
import subprocess
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

import scorpion_k7 as k7  # noqa: E402

APP_ID = "io.github.scorpion_k7"
VERSION = "0.1.0"
AUTHOR = "Brage Dorin"
AUTHOR_URL = "https://github.com/nirodg"
REPO_URL = "https://github.com/nirodg/scorpion-keyboard"
REA_URL = "https://github.com/morluto/rea"
COPILOT_ACCEL = "<Shift><Super>XF86TouchpadOff"   # Win+Shift+F23 as X11 sees it
KB_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KB_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/"
KB_SLOT = KB_PATH + "scorpion-k7-copilot/"


def gsettings(*args):
    return subprocess.run(["gsettings", *args], capture_output=True, text=True, check=True).stdout.strip()


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Scorpion K7")
        self.set_default_size(820, 620)
        self.base = None
        self.led = None
        self.busy = False
        self._flush_id = 0
        self._queued = False

        self.toasts = Adw.ToastOverlay()
        stack = Adw.ViewStack()
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE))
        recover = Gio.SimpleAction.new("recover", None)
        recover.connect("activate", lambda *_: self.recover())
        self.add_action(recover)
        menu = Gio.Menu()
        menu.append("Start at login (in the top bar)", "app.autostart")
        menu.append("Recover keyboard (if it stops typing)", "win.recover")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))
        self.spinner = Gtk.Spinner()
        header.pack_start(self.spinner)

        stack.add_titled_with_icon(self.build_lighting(), "light", "Lighting", "display-brightness-symbolic")
        stack.add_titled_with_icon(self.build_copilot(), "copilot", "Copilot key", "input-keyboard-symbolic")
        stack.add_titled_with_icon(self.build_about(), "about", "About", "help-about-symbolic")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(stack)
        self.toasts.set_child(box)
        self.set_content(self.toasts)
        # Load current settings at start-up (reads have not caused freezes).
        GLib.idle_add(lambda: self.load_led() and False)

    # ---------- device I/O (background thread, always released) ----------
    def run_io(self, fn, done_msg=None, on_done=None, after=None):
        """Run fn(k) in a released session; then optionally after() (e.g. a
        USB soft restart) once the session is closed."""
        if self.busy:
            self.toast("Keyboard is busy, wait a moment")
            return False
        self.busy = True
        self.spinner.start()

        def worker():
            try:
                with k7.session() as k:
                    res = fn(k)
                if after:
                    after()
                GLib.idle_add(self._io_ok, res, done_msg, on_done)
            except Exception as e:  # noqa: BLE001 - shown to the user
                GLib.idle_add(self._io_err, e)

        threading.Thread(target=worker, daemon=True).start()

    def _io_ok(self, res, msg, on_done):
        self.busy = False
        self.spinner.stop()
        if on_done:
            on_done(res)
        if msg:
            self.toast(msg)

    def _io_err(self, e):
        self.busy = False
        self.spinner.stop()
        self.toast(f"Error: {e}")

    @staticmethod
    def can_soft_restart():
        node = k7.usb_device_node()
        return bool(node) and os.access(node, os.W_OK)

    def soft_restart(self):
        if self.can_soft_restart():
            k7.usb_reset()

    def recover(self):
        """1st click: apply + release (what un-freezes it). Clicked again
        within 15 s: USB soft restart as a fallback."""
        now = time.monotonic()
        if now - getattr(self, "_last_recover", 0) > 15:
            self._last_recover = now
            self.run_io(lambda k: k.apply(), "Keyboard released — still stuck? Click Recover again")
        elif self.can_soft_restart():
            self._last_recover = 0
            self.run_io(lambda k: None, "Keyboard restarted over USB", after=k7.usb_reset)
        else:
            self.toast("USB restart needs the one-time setup (see the banner). Unplug/replug for now.")

    def toast(self, msg):
        self.toasts.add_toast(Adw.Toast(title=msg, timeout=4))

    # ---------- Lighting ----------
    def build_lighting(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.banner = Adw.Banner(
            title="One-time setup needed so the app can soft-restart the keyboard if it freezes.",
            button_label="Show me how", revealed=not self.can_soft_restart())
        self.banner.connect("button-clicked", self._show_setup)
        outer.append(self.banner)
        page = Adw.PreferencesPage(vexpand=True)
        outer.append(page)

        # --- Effect ---
        eff = Adw.PreferencesGroup(title="Effect",
                                   description="Pick the lighting effect (names as in the official app).")
        page.add(eff)
        self.mode_ids = sorted(k7.MODES)
        self.mode_row = Adw.ComboRow(
            title="Lighting mode",
            model=Gtk.StringList.new([k7.MODES[i][0] for i in self.mode_ids]))
        self.mode_row.connect("notify::selected", self._on_mode_changed)
        eff.add(self.mode_row)
        self.mode_desc = Adw.ActionRow(title="What it does", subtitle="—")
        self.mode_desc.add_prefix(Gtk.Image.new_from_icon_name("dialog-information-symbolic"))
        eff.add(self.mode_desc)

        # --- Look & motion ---
        look = Adw.PreferencesGroup(title="Look and motion")
        page.add(look)
        self.scales = {}
        for key, title, sub, lo_lbl, hi_lbl, mx in (
                ("brightness", "Brightness", "How bright the backlight is", "Dim", "Max", k7.BRIGHTNESS_MAX),
                ("speed", "Speed", "How fast animated effects move", "1", "5", k7.SPEED_MAX)):
            row = Adw.ActionRow(title=title, subtitle=sub)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, mx, 1)
            sc.set_size_request(260, -1)
            sc.set_draw_value(False)
            sc.set_round_digits(0)
            for v in range(mx + 1):
                label = lo_lbl if v == 0 else hi_lbl if v == mx else None
                sc.add_mark(v, Gtk.PositionType.BOTTOM, label)
            sc.connect("value-changed", lambda *_: self._mark_dirty())
            row.add_suffix(sc)
            look.add(row)
            self.scales[key] = sc

        self.dir_row = Adw.ComboRow(
            title="Direction", subtitle="Which way moving effects travel (wave, comet, flow)",
            model=Gtk.StringList.new(["Direction 1", "Direction 2", "Direction 3", "Direction 4"]))
        self.dir_row.connect("notify::selected", lambda *_: self._mark_dirty())
        look.add(self.dir_row)
        self.sym_row = Adw.SwitchRow(title="Mirror from the centre",
                                     subtitle="Effects start in the middle and spread to both sides")
        self.sym_row.connect("notify::active", lambda *_: self._mark_dirty())
        look.add(self.sym_row)

        # --- Behaviour ---
        beh = Adw.PreferencesGroup(title="Behaviour")
        page.add(beh)
        self.autorestart_row = Adw.SwitchRow(
            title="Soft-restart keyboard after each change",
            subtitle="Fallback only — normally not needed. Resets the keyboard over USB after each change (~2 s pause).",
            active=False)
        beh.add(self.autorestart_row)

        # --- Advanced ---
        adv = Adw.PreferencesGroup()
        page.add(adv)
        exp = Adw.ExpanderRow(title="Advanced", subtitle="Raw data stored on the keyboard")
        self.raw_row = Adw.ActionRow(title="Lighting record", subtitle="not read yet")
        self.raw_row.add_css_class("monospace")
        exp.add_row(self.raw_row)
        adv.add(exp)

        # --- Actions ---
        bar = Gtk.Box(spacing=12, margin_top=6, margin_bottom=12, margin_start=12, margin_end=12)
        self.state_lbl = Gtk.Label(label="Click “Read from keyboard” to load your current settings.",
                                   xalign=0, hexpand=True, wrap=True)
        self.state_lbl.add_css_class("dim-label")
        reload_btn = Gtk.Button(label="Read from keyboard")
        reload_btn.connect("clicked", lambda *_: self.load_led())
        bar.append(self.state_lbl)
        bar.append(reload_btn)
        outer.append(bar)
        self._loading = False
        return outer

    def _on_mode_changed(self, *_):
        idx = self.mode_row.get_selected()
        if 0 <= idx < len(self.mode_ids):
            name, desc = k7.MODES[self.mode_ids[idx]]
            self.mode_desc.set_subtitle(f"{desc}  (mode value {self.mode_ids[idx]})")
        self._mark_dirty()

    def _pending(self):
        if self.led is None:
            return None
        return k7.encode_led(
            self.led,
            mode=self.mode_ids[self.mode_row.get_selected()],
            brightness=k7.brightness_from_ui(int(self.scales["brightness"].get_value())),
            speed=int(self.scales["speed"].get_value()),
            direction=self.dir_row.get_selected(),
            symmetry=int(self.sym_row.get_active()),
        )

    def _mark_dirty(self):
        """Live apply: send changes ~0.5 s after the user stops adjusting."""
        if self._loading or self.led is None:
            return
        if self._pending() == self.led:
            self.state_lbl.set_text("Settings on your keyboard ✓")
            return
        self.state_lbl.set_text("Applying…")
        if self._flush_id:
            GLib.source_remove(self._flush_id)
        self._flush_id = GLib.timeout_add(500, self._flush)

    def _flush(self):
        self._flush_id = 0
        new = self._pending()
        if new is None or new == self.led:
            return False
        if self.busy:                      # a write is in progress: try again shortly
            self._flush_id = GLib.timeout_add(300, self._flush)
            return False
        self.do_write(new)
        return False

    def load_led(self):
        def fn(k):
            base = k.base()
            led = k.read_block(base + k7.LED_OFF, 8)
            return base, led, k.macro_slot(base, k7.COPILOT_SLOT)
        self.run_io(fn, None, lambda r: (self.show_led(r[:2]), self.show_copilot(r[2])))

    def show_led(self, res):
        self.base, self.led = res
        d = k7.decode_led(self.led)
        self._loading = True
        if d["mode"] in self.mode_ids:
            self.mode_row.set_selected(self.mode_ids.index(d["mode"]))
        self.scales["brightness"].set_value(k7.brightness_to_ui(d["brightness"]))
        self.scales["speed"].set_value(min(d["speed"], k7.SPEED_MAX))
        self.dir_row.set_selected(d["direction"])
        self.sym_row.set_active(bool(d["symmetry"]))
        self._loading = False
        self._on_mode_changed()
        self.raw_row.set_subtitle(f"0x{self.base + k7.LED_OFF:04X}: {self.led.hex(' ')}")
        self._mark_dirty()

    def do_write(self, new):
        addr = self.base + k7.LED_OFF
        restart = self.autorestart_row.get_active() and self.can_soft_restart()

        def fn(k):
            # Same as the vendor app: write, apply, no read-back afterwards.
            k.write_block(addr, new)
            k.apply()
            return self.base, new

        def after():
            if restart:
                time.sleep(0.2)
                k7.usb_reset()

        def done(res):
            self.base, self.led = res
            self.raw_row.set_subtitle(f"0x{addr:04X}: {new.hex(' ')}")
            self._mark_dirty()   # picks up any change made while we were writing
            if self._pending() == self.led:
                self.state_lbl.set_text("Settings on your keyboard ✓" +
                                        ("  (keyboard soft-restarted)" if restart else ""))
        self.run_io(fn, None, done, after=after)

    def _show_setup(self, *_):
        here = os.path.dirname(os.path.abspath(__file__))
        cmd = (f"sudo cp '{here}/70-scorpion-k7.rules' /etc/udev/rules.d/ && "
               "sudo udevadm control --reload && sudo udevadm trigger")
        dlg = Adw.MessageDialog(
            transient_for=self, heading="Allow soft restart of the keyboard",
            body=("Run this once in a terminal, then unplug and replug the keyboard. It lets "
                  "this app reset only this keyboard over USB, without root:\n\n" + cmd))
        dlg.add_response("copy", "Copy command")
        dlg.add_response("close", "Close")
        dlg.connect("response", lambda d, r: r == "copy" and self.get_clipboard().set(cmd))
        dlg.present()

    # ---------- Copilot key ----------
    def build_copilot(self):
        page = Adw.PreferencesPage()
        grp = Adw.PreferencesGroup(
            title="What should the Copilot key do?",
            description="Choose one. “Send a shortcut” is stored on the keyboard itself, so it works in every "
                        "app (and even on other computers). “Run a command” uses a GNOME shortcut.")
        page.add(grp)

        self.cop_checks = {}
        first = None

        def choice(key, title, subtitle):
            nonlocal first
            row = Adw.ActionRow(title=title, subtitle=subtitle)
            chk = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            if first:
                chk.set_group(first)
            else:
                first = chk
            chk.connect("toggled", lambda *_: self._cop_update())
            row.add_prefix(chk)
            row.set_activatable_widget(chk)
            grp.add(row)
            self.cop_checks[key] = chk
            return row

        choice("default", "Windows default", "Sends Win+Shift+F23 (opens Copilot on Windows 11)")
        sc_row = choice("shortcut", "Send a shortcut", "e.g. Ctrl+Space to start your voice-recording app")
        self.sc_label = Gtk.ShortcutLabel(accelerator="", disabled_text="none", valign=Gtk.Align.CENTER)
        self.sc_btn = Gtk.Button(label="Record…", valign=Gtk.Align.CENTER)
        self.sc_btn.connect("clicked", self._record_shortcut)
        sc_row.add_suffix(self.sc_label)
        sc_row.add_suffix(self.sc_btn)
        choice("command", "Run a command", "Launch any app or script when the key is pressed")
        self.cmd_row = Adw.EntryRow(title="Command (e.g. firefox https://claude.ai)")
        self.cmd_row.connect("changed", lambda *_: self._cop_update())
        grp.add(self.cmd_row)

        act = Adw.PreferencesGroup()
        page.add(act)
        self.cop_state = Adw.ActionRow(title="Currently on the keyboard", subtitle="reading…")
        act.add(self.cop_state)
        self.cop_save = Gtk.Button(label="Save", halign=Gtk.Align.END, margin_top=12, sensitive=False)
        self.cop_save.add_css_class("suggested-action")
        self.cop_save.connect("clicked", self._cop_save)
        act.add(self.cop_save)

        self.cop_keys = []          # HID codes chosen for "Send a shortcut"
        self.cop_slot = None
        self.cop_checks["default"].set_active(True)
        return page

    # HID code <-> GTK accelerator helpers
    MOD_HID = {"<Control>": 0xE0, "<Shift>": 0xE1, "<Alt>": 0xE2, "<Super>": 0xE3}
    KEYVAL_HID = {
        "space": 0x2C, "Return": 0x28, "Tab": 0x2B, "BackSpace": 0x2A, "Delete": 0x4C, "Insert": 0x49,
        "Home": 0x4A, "End": 0x4D, "Page_Up": 0x4B, "Page_Down": 0x4E, "Left": 0x50, "Right": 0x4F,
        "Up": 0x52, "Down": 0x51, "Print": 0x46, "Pause": 0x48, "Menu": 0x65, "minus": 0x2D,
        "equal": 0x2E, "bracketleft": 0x2F, "bracketright": 0x30, "backslash": 0x31, "semicolon": 0x33,
        "apostrophe": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37, "slash": 0x38,
        **{chr(ord("a") + i): 0x04 + i for i in range(26)},
        **{str((i + 1) % 10): 0x1E + i for i in range(10)},
        **{f"F{i + 1}": 0x3A + i for i in range(12)},
        **{f"F{i + 13}": 0x68 + i for i in range(12)},
    }

    def _keys_to_accel(self, keys):
        mods = {v: k for k, v in self.MOD_HID.items()}
        names = {v: k for k, v in self.KEYVAL_HID.items()}
        acc = ""
        for h in keys:
            if h in mods:
                acc += mods[h]
            elif h in (0xE4, 0xE5, 0xE6, 0xE7):
                acc += mods[h - 4]
            elif h in names:
                acc += names[h]
            else:
                return None
        return acc

    def _record_shortcut(self, *_):
        win = Adw.Window(transient_for=self, modal=True, default_width=380, default_height=160,
                         title="Record shortcut")
        lbl = Gtk.Label(label="Press the shortcut you want the Copilot key to send\n(e.g. Ctrl+Space).\n\n"
                              "Esc to cancel", justify=Gtk.Justification.CENTER,
                        margin_top=24, margin_bottom=24, margin_start=24, margin_end=24)
        win.set_content(lbl)
        ctl = Gtk.EventControllerKey()

        def pressed(_c, keyval, keycode, state):
            name = Gdk.keyval_name(keyval) or ""
            if name == "Escape" and not state & Gtk.accelerator_get_default_mod_mask():
                win.close()
                return True
            if name.startswith(("Control", "Shift", "Alt", "Super", "Meta", "ISO_")):
                return True                           # wait for the non-modifier key
            ok, base_kv, *_ = Gdk.Display.get_default().translate_key(keycode, 0, 0)
            base = Gdk.keyval_name(base_kv if ok else keyval) or name
            base = base.lower() if len(base) == 1 else base
            if base not in self.KEYVAL_HID:
                lbl.set_text(f"“{name}” can't be sent by the keyboard — try another key")
                return True
            keys = [h for m, h in (("CONTROL", 0xE0), ("SHIFT", 0xE1), ("ALT", 0xE2), ("SUPER", 0xE3))
                    if state & getattr(Gdk.ModifierType, f"{m}_MASK")]
            keys.append(self.KEYVAL_HID[base])
            if len(keys) > 3:
                lbl.set_text("Up to 3 keys (e.g. Ctrl+Shift+R) — try again")
                return True
            self.cop_keys = keys
            self.sc_label.set_accelerator(self._keys_to_accel(keys) or "")
            self.cop_checks["shortcut"].set_active(True)
            self._cop_update()
            win.close()
            return True
        ctl.connect("key-pressed", pressed)
        win.add_controller(ctl)
        win.present()

    def show_copilot(self, slot):
        self.cop_slot = slot
        _, count, _, _, data = slot
        used = data[:2 + 2 * count + 2]
        bound = self._gnome_command()
        if used == k7.COPILOT_DEFAULT[:len(used)] and count == 6:
            self.cop_state.set_subtitle("Windows default (Win+Shift+F23)" +
                                        (f" → runs: {bound}" if bound else ""))
            self.cop_checks["command" if bound else "default"].set_active(True)
            if bound:
                self.cmd_row.set_text(bound)
        else:
            keys = [k7.HID_BY_NAME.get(n.lower(), 0) for n in k7.macro_keys(data)]
            self.cop_keys = keys
            acc = self._keys_to_accel(keys)
            self.sc_label.set_accelerator(acc or "")
            self.cop_state.set_subtitle("Sends " + " + ".join(k7.macro_keys(data)))
            self.cop_checks["shortcut"].set_active(True)
        self._cop_update()

    def _cop_choice(self):
        return next((k for k, c in self.cop_checks.items() if c.get_active()), None)

    def _cop_update(self):
        ch = self._cop_choice()
        if not hasattr(self, "cop_save") or ch is None:   # mid radio switch
            return
        self.cmd_row.set_sensitive(ch == "command")
        ok = self.cop_slot is not None and (
            ch == "default" or (ch == "shortcut" and self.cop_keys) or
            (ch == "command" and self.cmd_row.get_text().strip()))
        self.cop_save.set_sensitive(bool(ok))

    def _cop_save(self, *_):
        ch = self._cop_choice()
        if ch == "shortcut":
            data, count = k7.encode_macro(self.cop_keys)
        else:
            data, count = k7.COPILOT_DEFAULT, 6
        try:
            if ch == "command":
                self._gnome_bind(self.cmd_row.get_text().strip())
            else:
                self._gnome_unbind()
        except Exception as e:  # noqa: BLE001
            self.toast(f"GNOME shortcut failed: {e}")
            return

        def fn(k):
            base = k.base()
            k.write_macro_slot(base, k7.COPILOT_SLOT, data, count)
            return k.macro_slot(base, k7.COPILOT_SLOT)
        self.run_io(fn, "Copilot key saved — press it to test", self.show_copilot)

    # GNOME custom shortcut (only used for "Run a command")
    def _bindings(self):
        raw = gsettings("get", KB_SCHEMA, "custom-keybindings").replace("@as ", "")
        return json.loads(raw.replace("'", '"'))

    def _gnome_command(self):
        try:
            if KB_SLOT in self._bindings():
                return gsettings("get", f"{KB_SCHEMA}.custom-keybinding:{KB_SLOT}", "command").strip("'")
        except Exception:  # noqa: BLE001
            pass
        return None

    def _gnome_bind(self, cmd):
        lst = self._bindings()
        if KB_SLOT not in lst:
            lst.append(KB_SLOT)
            gsettings("set", KB_SCHEMA, "custom-keybindings", json.dumps(lst).replace('"', "'"))
        rel = f"{KB_SCHEMA}.custom-keybinding:{KB_SLOT}"
        gsettings("set", rel, "name", "Scorpion K7 Copilot key")
        gsettings("set", rel, "command", cmd)
        gsettings("set", rel, "binding", COPILOT_ACCEL)

    def _gnome_unbind(self):
        lst = self._bindings()
        if KB_SLOT in lst:
            lst.remove(KB_SLOT)
            gsettings("set", KB_SCHEMA, "custom-keybindings", json.dumps(lst).replace('"', "'"))
            gsettings("reset-recursively", f"{KB_SCHEMA}.custom-keybinding:{KB_SLOT}")

    # ---------- About ----------
    def build_about(self):
        page = Adw.PreferencesPage()
        head = Adw.PreferencesGroup()
        page.add(head)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=12, margin_bottom=12)
        title = Gtk.Label(label="Scorpion K7 for Linux")
        title.add_css_class("title-1")
        sub = Gtk.Label(label=f"Version {VERSION} · Customize the Genius Scorpion K7 on Linux, "
                              "just like the official Windows and macOS software", wrap=True,
                        justify=Gtk.Justification.CENTER)
        sub.add_css_class("dim-label")
        box.append(title)
        box.append(sub)
        head.add(box)

        links = Adw.PreferencesGroup(title="Project")
        page.add(links)
        links.add(self._link_row("Source code", "Report issues, contribute or star the project", REPO_URL))
        lic = Adw.ActionRow(title="License", subtitle="MIT — free to use, modify and share, including in "
                                                       "your own projects")
        lic.add_prefix(Gtk.Image.new_from_icon_name("emblem-documents-symbolic"))
        links.add(lic)

        thanks = Adw.PreferencesGroup(title="Thanks")
        page.add(thanks)
        thanks.add(self._link_row(f"{AUTHOR}", "Author — built, tested and drove this project", AUTHOR_URL,
                                  icon="avatar-default-symbolic"))
        thanks.add(self._link_row("REA", "Reverse-engineering toolkit used to decode the keyboard protocol "
                                         "from the official app", REA_URL, icon="system-search-symbolic"))
        return page

    @staticmethod
    def _link_row(title, subtitle, url, icon="web-browser-symbolic"):
        row = Adw.ActionRow(title=title, subtitle=f"{subtitle}\n{url}", activatable=True)
        row.add_prefix(Gtk.Image.new_from_icon_name(icon))
        row.add_suffix(Gtk.Image.new_from_icon_name("adw-external-link-symbolic"))
        row.connect("activated", lambda *_: Gtk.UriLauncher.new(url).launch(None, None, None, None))
        return row

AUTOSTART = os.path.join(GLib.get_user_config_dir(), "autostart", f"{APP_ID}.desktop")


def launch_command():
    here = os.path.abspath(__file__)
    if here.startswith("/usr/share/scorpion-k7/"):
        return "scorpion-k7"                       # installed from the .deb
    return f"python3 '{here}'"                     # running from source


class App(Adw.Application):
    """Single instance; lives in the top bar (tray) while the window is closed."""

    def __init__(self, background=False):
        super().__init__(application_id=APP_ID)
        self.start_hidden = background
        self.win = None
        self.tray = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()                                 # keep running with no window
        quit_ = Gio.SimpleAction.new("quit", None)
        quit_.connect("activate", lambda *_: self.quit_app())
        self.add_action(quit_)
        self.set_accels_for_action("app.quit", ["<Control>q"])
        auto = Gio.SimpleAction.new_stateful("autostart", None,
                                             GLib.Variant("b", os.path.exists(AUTOSTART)))
        auto.connect("change-state", self.on_autostart)
        self.add_action(auto)
        try:
            from tray import Tray
            self.tray = Tray(APP_ID, "Scorpion K7", "input-keyboard-symbolic", [
                ("Open Scorpion K7", self.show_window),
                ("Recover keyboard", lambda: self.show_window() or self.win.recover()),
                None,
                ("Quit", self.quit_app),
            ], on_activate=self.show_window)
        except Exception as e:  # noqa: BLE001 - tray is optional
            print(f"tray icon unavailable: {e}")

    def do_activate(self):
        if self.start_hidden:                       # login autostart: tray only
            self.start_hidden = False
            return
        self.show_window()

    def show_window(self):
        if self.win is None:
            self.win = Window(self)
            self.win.connect("close-request", self._on_close)
        else:
            self.win.load_led()                     # refresh (Fn keys may have changed it)
        self.win.present()

    def _on_close(self, win):
        if self.tray is None:
            return False                            # no tray: closing quits as usual
        win.set_visible(False)
        return True

    def quit_app(self):
        if self.tray:
            self.tray.remove()
        self.release()
        self.quit()

    def on_autostart(self, action, value):
        try:
            if value.get_boolean():
                os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
                with open(AUTOSTART, "w") as f:
                    f.write("[Desktop Entry]\nType=Application\nName=Scorpion K7\n"
                            "Comment=Keyboard settings in the top bar\n"
                            f"Exec={launch_command()} --background\n"
                            "Icon=input-keyboard\nX-GNOME-Autostart-enabled=true\n")
            elif os.path.exists(AUTOSTART):
                os.remove(AUTOSTART)
            action.set_state(value)
        except OSError as e:
            if self.win:
                self.win.toast(f"Could not change autostart: {e}")


if __name__ == "__main__":
    import sys
    bg = "--background" in sys.argv
    App(background=bg).run([a for a in sys.argv if a != "--background"])
