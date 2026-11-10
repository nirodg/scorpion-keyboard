# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Brage Dorin
"""Macros tab: profiles, key assignments, macro library and editor."""
import copy
import json
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

import k7macros as m  # noqa: E402
import scorpion_k7 as k7  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CONF = os.path.join(GLib.get_user_config_dir(), "scorpion-k7")
CUSTOM_MACROS = os.path.join(CONF, "macros.json")
CUSTOM_PROFILES = os.path.join(CONF, "profiles.json")
MAX_KEYS = m.SLOTS - 1


def key_label(code):
    names = {0x2C: "Space", 0x28: "Enter", 0x58: "Numpad Enter", 0x47: "Scroll Lock", 0x46: "Print Screen",
             0x53: "Num Lock", 0x39: "Caps Lock"}
    if code in names:
        return names[code]
    n = k7.key_name(code)
    return n.replace("KP", "Numpad ") if n.startswith("KP") else n


class MacrosPage(Gtk.Box):
    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.official = m.load_library(os.path.join(HERE, "macros_default.json"), builtin=True)
        self.custom = self._load_custom_macros()
        self.profiles = self._load_profiles()
        self.current = "Gaming (PUBG)"
        self.assign = dict(self.profiles[self.current]["keys"])     # {factory_code: macro name}
        self.on_keyboard = None                                      # {cell: (slot, events)}
        self.bank = None

        page = Adw.PreferencesPage(vexpand=True)
        self.append(page)

        # ---- profile
        prof = Adw.PreferencesGroup(
            title="Profile",
            description="A profile is a set of macro keys (e.g. Gaming, Productivity). Edit it below, then "
                        "apply it — the keyboard stores the macros itself, so they work without this app.")
        page.add(prof)
        self.profile_model = Gtk.StringList()
        self.profile_row = Adw.ComboRow(title="Profile", model=self.profile_model)
        self.profile_row.connect("notify::selected", self._on_profile_selected)
        prof.add(self.profile_row)
        btns = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=10)
        for label, cb, style in (("Delete", self._delete_profile, "destructive-action"),
                                 ("Save as new…", self._save_profile_as, None),
                                 ("Save", self._save_profile, None),
                                 ("Apply to keyboard", self._apply, "suggested-action")):
            b = Gtk.Button(label=label)
            if style:
                b.add_css_class(style)
            b.connect("clicked", lambda *_, f=cb: f())
            btns.append(b)
            if label == "Delete":
                self.del_btn = b
            if label == "Save":
                self.save_btn = b
        prof.add(btns)

        # ---- key assignments
        self.keys_group = Adw.PreferencesGroup(title="Macro keys in this profile",
                                               description=f"Up to {MAX_KEYS} keys. Tip: the numpad and "
                                                           "Scroll Lock are good spare keys.")
        page.add(self.keys_group)
        self.key_rows = []
        add = Adw.ActionRow(title="Add a macro key")
        self.add_key = Gtk.DropDown(model=Gtk.StringList(), valign=Gtk.Align.CENTER, enable_search=True)
        self.add_macro = Gtk.DropDown(model=Gtk.StringList(), valign=Gtk.Align.CENTER, enable_search=True)
        ab = Gtk.Button(icon_name="list-add-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Add")
        ab.connect("clicked", self._add_assignment)
        add.add_suffix(self.add_key)
        add.add_suffix(Gtk.Label(label="plays"))
        add.add_suffix(self.add_macro)
        add.add_suffix(ab)
        self.add_row = add
        self.keys_group.add(add)

        # ---- on the keyboard now
        now = Adw.PreferencesGroup(title="On the keyboard now")
        page.add(now)
        self.now_row = Adw.ActionRow(title="Not read yet", subtitle="Click “Read” to see which keys play macros")
        rd = Gtk.Button(label="Read", valign=Gtk.Align.CENTER)
        rd.connect("clicked", lambda *_: self.read_keyboard())
        self.now_row.add_suffix(rd)
        now.add(self.now_row)

        # ---- library
        self.lib_group = Adw.PreferencesGroup(
            title="Macro library",
            description="“Official” macros come with the Genius software (PUBG presets). "
                        "Duplicate one to change it, or create your own.")
        nb = Gtk.Button(label="New macro", valign=Gtk.Align.CENTER)
        nb.add_css_class("flat")
        nb.connect("clicked", lambda *_: self._edit_macro(None))
        self.lib_group.set_header_suffix(nb)
        page.add(self.lib_group)
        self.lib_rows = []

        self._refresh_profiles()
        self._refresh_all()

    # ------------------------------------------------------------ storage
    def _load_custom_macros(self):
        try:
            return m.load_library(CUSTOM_MACROS)
        except (OSError, ValueError, KeyError):
            return []

    def _save_custom_macros(self):
        os.makedirs(CONF, exist_ok=True)
        m.save_library(CUSTOM_MACROS, self.custom)

    def _load_profiles(self):
        profs = {n: {"keys": dict(k), "builtin": True} for n, k in m.BUILTIN_PROFILES.items()}
        try:
            with open(CUSTOM_PROFILES) as f:
                for n, p in json.load(f).items():
                    profs[n] = {"keys": {int(c): v for c, v in p["keys"].items()}, "builtin": False}
        except (OSError, ValueError, KeyError):
            pass
        return profs

    def _save_profiles(self):
        os.makedirs(CONF, exist_ok=True)
        data = {n: {"keys": {str(c): v for c, v in p["keys"].items()}}
                for n, p in self.profiles.items() if not p["builtin"]}
        with open(CUSTOM_PROFILES, "w") as f:
            json.dump(data, f, indent=1)

    def library(self):
        return self.official + m.PRODUCTIVITY_MACROS + self.custom

    def macro_by_name(self, name):
        return next((x for x in self.library() if x.name == name), None)

    # ------------------------------------------------------------ profile UI
    def _refresh_profiles(self):
        names = list(self.profiles)
        self._updating = True
        self.profile_model.splice(0, self.profile_model.get_n_items(), names)
        self.profile_row.set_selected(names.index(self.current) if self.current in names else 0)
        self._updating = False
        builtin = self.profiles[self.current]["builtin"]
        self.del_btn.set_sensitive(not builtin)
        self.save_btn.set_sensitive(not builtin)
        self.save_btn.set_tooltip_text("Built-in profiles can't be overwritten — use “Save as new…”" if builtin else None)

    def _on_profile_selected(self, *_):
        if getattr(self, "_updating", False):
            return
        idx = self.profile_row.get_selected()
        names = list(self.profiles)
        if 0 <= idx < len(names):
            self.current = names[idx]
            self.assign = dict(self.profiles[self.current]["keys"])
            self._refresh_profiles()
            self._refresh_assignments()

    def _save_profile(self):
        self.profiles[self.current]["keys"] = dict(self.assign)
        self._save_profiles()
        self.win.toast(f"Profile “{self.current}” saved")

    def _save_profile_as(self):
        self._ask_text("Save profile as", "Profile name", f"{self.current} (custom)", self._do_save_as)

    def _do_save_as(self, name):
        if not name:
            return
        self.profiles[name] = {"keys": dict(self.assign), "builtin": False}
        self.current = name
        self._save_profiles()
        self._refresh_profiles()
        self.win.toast(f"Profile “{name}” saved")

    def _delete_profile(self):
        if self.profiles[self.current]["builtin"]:
            return
        del self.profiles[self.current]
        self._save_profiles()
        self.current = "No macros"
        self.assign = {}
        self._refresh_profiles()
        self._refresh_assignments()

    # ------------------------------------------------------------ assignments UI
    def _refresh_all(self):
        self._refresh_assignments()
        self._refresh_library()

    def _refresh_assignments(self):
        for r in self.key_rows:
            self.keys_group.remove(r)
        self.key_rows = []
        names = [x.name for x in self.library()]
        for code, mname in sorted(self.assign.items(), key=lambda kv: key_label(kv[0])):
            row = Adw.ActionRow(title=key_label(code))
            dd = Gtk.DropDown(model=Gtk.StringList.new(names), valign=Gtk.Align.CENTER, enable_search=True)
            if mname in names:
                dd.set_selected(names.index(mname))
            else:
                row.set_subtitle(f"⚠ macro “{mname}” not found")
            dd.connect("notify::selected", lambda d, _p, c=code: self._set_assignment(c, names[d.get_selected()]))
            rm = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
            rm.add_css_class("flat")
            rm.connect("clicked", lambda *_, c=code: self._remove_assignment(c))
            row.add_suffix(dd)
            row.add_suffix(rm)
            self.keys_group.remove(self.add_row)
            self.keys_group.add(row)
            self.keys_group.add(self.add_row)
            self.key_rows.append(row)
        free = [c for _, c in m.assignable_keys() if c not in self.assign]
        self._free_codes = sorted(set(free), key=key_label)
        self.add_key.set_model(Gtk.StringList.new([key_label(c) for c in self._free_codes]))
        self.add_macro.set_model(Gtk.StringList.new(names))
        self.add_row.set_sensitive(len(self.assign) < MAX_KEYS)
        self.keys_group.set_description(
            f"{len(self.assign)} of {MAX_KEYS} keys used. Tip: the numpad and Scroll Lock make good macro keys.")

    def _set_assignment(self, code, name):
        self.assign[code] = name

    def _remove_assignment(self, code):
        self.assign.pop(code, None)
        self._refresh_assignments()

    def _add_assignment(self, *_):
        ki, mi = self.add_key.get_selected(), self.add_macro.get_selected()
        names = [x.name for x in self.library()]
        if ki == Gtk.INVALID_LIST_POSITION or mi == Gtk.INVALID_LIST_POSITION or not self._free_codes:
            return
        self.assign[self._free_codes[ki]] = names[mi]
        self._refresh_assignments()

    # ------------------------------------------------------------ keyboard I/O
    def read_keyboard(self):
        def fn(k):
            base = k.base()
            return m.read_bank(k, base)
        self.win.run_io(fn, None, self._show_keyboard)

    def _show_keyboard(self, bank):
        self.bank = bank
        self.on_keyboard = m.key_assignments(bank)
        if not self.on_keyboard:
            self.now_row.set_title("No macro keys (except Copilot)")
            self.now_row.set_subtitle("Apply a profile to add some")
            return
        parts = []
        for cell, (_, events) in sorted(self.on_keyboard.items()):
            name = self._match_macro(events)
            parts.append(f"{key_label(m.FACTORY_KEYMAP[cell])} → {name}")
        self.now_row.set_title(f"{len(parts)} macro key{'s' if len(parts) != 1 else ''}")
        self.now_row.set_subtitle(GLib.markup_escape_text("  ·  ".join(parts)))

    def _match_macro(self, events):
        for x in self.library():
            if [(e.kind, e.action, e.code, e.dx, e.dy) for e in m.decode_device(*self._enc(x))] == \
                    [(e.kind, e.action, e.code, e.dx, e.dy) for e in events]:
                return x.name
        return "custom (" + ", ".join(m.describe(e) for e in events[:3]) + ("…" if len(events) > 3 else "") + ")"

    @staticmethod
    def _enc(macro):
        data, count, _ = m.encode_device(macro)
        return data, count

    def _apply(self):
        missing = [n for n in self.assign.values() if not self.macro_by_name(n)]
        if missing:
            self.win.toast(f"Unknown macro: {missing[0]}")
            return
        assignments = {m.cell_for_code(c): self.macro_by_name(n) for c, n in self.assign.items()}

        def fn(k):
            base = k.base()
            bank = m.read_bank(k, base)
            m.write_bank(k, base, bank, assignments)
            return m.read_bank(k, base)

        def done(bank):
            self._show_keyboard(bank)
            n = len(self.assign)
            self.win.toast(f"“{self.current}” applied — {n} macro key{'s' if n != 1 else ''}")
        self.win.run_io(fn, None, done)

    # ------------------------------------------------------------ library UI
    def _refresh_library(self):
        for r in self.lib_rows:
            self.lib_group.remove(r)
        self.lib_rows = []
        for x in self.library():
            kind = "Official" if x in self.official else "Built-in" if x.builtin else "Custom"
            rep = (f"{x.repeat}× " if x.mode == m.MODE_TIMES and x.repeat > 1 else "")
            row = Adw.ActionRow(title=GLib.markup_escape_text(x.name),
                                subtitle=f"{kind} · {rep}{m.MODE_NAMES[x.mode]} · {len(x.events)} steps")
            if x.builtin:
                b = Gtk.Button(label="View / duplicate", valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda *_, mm=x: self._edit_macro(mm, duplicate=True))
            else:
                b = Gtk.Button(label="Edit", valign=Gtk.Align.CENTER)
                b.connect("clicked", lambda *_, mm=x: self._edit_macro(mm))
                d = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Delete")
                d.add_css_class("flat")
                d.connect("clicked", lambda *_, mm=x: self._delete_macro(mm))
                row.add_suffix(d)
            row.add_suffix(b)
            self.lib_group.add(row)
            self.lib_rows.append(row)

    def _delete_macro(self, macro):
        self.custom.remove(macro)
        self._save_custom_macros()
        self._refresh_all()

    def _edit_macro(self, macro, duplicate=False):
        if macro is None:
            work = m.Macro("My macro")
        else:
            work = copy.deepcopy(macro)
            work.builtin = False
            if duplicate:
                work.name = f"{macro.name} (copy)"
        MacroEditor(self.win, work, on_save=lambda mm: self._store_macro(macro if not duplicate else None, mm))

    def _store_macro(self, original, macro):
        names = {x.name for x in self.library() if x is not original}
        if macro.name in names:
            self.win.toast(f"A macro named “{macro.name}” already exists")
            return False
        if original is not None and original in self.custom:
            old = original.name
            self.custom[self.custom.index(original)] = macro
            for p in self.profiles.values():                       # follow renames
                for c, n in p["keys"].items():
                    if n == old:
                        p["keys"][c] = macro.name
            self.assign = {c: (macro.name if n == old else n) for c, n in self.assign.items()}
            self._save_profiles()
        else:
            self.custom.append(macro)
        self._save_custom_macros()
        self._refresh_all()
        return True

    # ------------------------------------------------------------ helpers
    def _ask_text(self, heading, label, default, cb):
        dlg = Adw.MessageDialog(transient_for=self.win, heading=heading)
        entry = Gtk.Entry(text=default, activates_default=True)
        dlg.set_extra_child(entry)
        dlg.add_response("cancel", "Cancel")
        dlg.add_response("ok", "Save")
        dlg.set_default_response("ok")
        dlg.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dlg.connect("response", lambda d, r: r == "ok" and cb(entry.get_text().strip()))
        dlg.present()


class MacroEditor(Adw.Window):
    """Edit a macro: name, repeat mode, steps (record keys, clicks, mouse moves, delays)."""

    def __init__(self, parent, macro, on_save):
        super().__init__(transient_for=parent, modal=True, title="Edit macro",
                         default_width=560, default_height=640)
        self.macro, self.on_save = macro, on_save
        self.recording = False
        self.last_t = None

        tb = Adw.ToolbarView()
        hb = Adw.HeaderBar()
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        save = Gtk.Button(label="Save")
        save.add_css_class("suggested-action")
        save.connect("clicked", self._save)
        hb.pack_start(cancel)
        hb.pack_end(save)
        tb.add_top_bar(hb)
        page = Adw.PreferencesPage()
        tb.set_content(page)
        self.set_content(tb)

        g = Adw.PreferencesGroup()
        page.add(g)
        self.name_row = Adw.EntryRow(title="Name", text=macro.name)
        g.add(self.name_row)
        modes = list(m.MODE_NAMES)
        self.mode_row = Adw.ComboRow(title="When the key is pressed",
                                     model=Gtk.StringList.new([m.MODE_NAMES[x] for x in modes]))
        self.mode_row.set_selected(modes.index(macro.mode))
        self.mode_row.connect("notify::selected", lambda *_: self._mode_changed())
        g.add(self.mode_row)
        self.rep_row = Adw.SpinRow.new_with_range(1, 500, 1)
        self.rep_row.set_title("Times")
        self.rep_row.set_value(macro.repeat)
        g.add(self.rep_row)
        self._modes = modes
        self._mode_changed()

        self.steps = Adw.PreferencesGroup(title="Steps", description="Delay = pause after the step, in ms.")
        page.add(self.steps)
        bar = Gtk.Box(spacing=6)
        self.rec_btn = Gtk.ToggleButton(label="● Record keys")
        self.rec_btn.connect("toggled", self._toggle_record)
        bar.append(self.rec_btn)
        for label, ev in (("+ Left click", [m.Event("mouse", "press", m.MOUSE_LEFT, delay=20),
                                            m.Event("mouse", "release", m.MOUSE_LEFT, delay=20)]),
                          ("+ Right click", [m.Event("mouse", "press", m.MOUSE_RIGHT, delay=20),
                                             m.Event("mouse", "release", m.MOUSE_RIGHT, delay=20)]),
                          ("+ Mouse move", [m.Event("move", dx=0, dy=10, delay=10)])):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda *_, e=ev: self._add(copy.deepcopy(e)))
            bar.append(b)
        clr = Gtk.Button(label="Clear")
        clr.connect("clicked", lambda *_: (self.macro.events.clear(), self._refresh()))
        bar.append(clr)
        self.steps.set_header_suffix(bar)
        self.step_rows = []
        self.hint = Adw.ActionRow(title="No steps yet",
                                  subtitle="Click “Record keys” and type, or add clicks and moves")
        self._refresh()

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)   # before buttons see Space/Enter
        keys.connect("key-pressed", self._key, "press")
        keys.connect("key-released", self._key, "release")
        self.add_controller(keys)
        self.present()

    def _mode_changed(self):
        self.rep_row.set_visible(self._modes[self.mode_row.get_selected()] == m.MODE_TIMES)

    def _toggle_record(self, btn):
        self.recording = btn.get_active()
        self.last_t = None
        btn.set_label("■ Stop recording" if self.recording else "● Record keys")

    def _key(self, ctl, keyval, keycode, state, action):
        if not self.recording:
            return False
        ok, base_kv, *_ = Gdk.Display.get_default().translate_key(keycode, 0, 0)
        name = Gdk.keyval_name(base_kv if ok else keyval) or ""
        name = name.lower() if len(name) == 1 else name
        hid = m.KEYVAL_HID.get(name)
        if hid is None:
            return True
        t = ctl.get_current_event_time()
        if self.last_t is not None and self.macro.events:
            self.macro.events[-1].delay = max(0, min(t - self.last_t, 60000))
        self.last_t = t
        ev = self.macro.events
        if action == "press" and ev and ev[-1].kind == "key" and ev[-1].code == hid and ev[-1].action == "press":
            return True                                   # ignore auto-repeat
        ev.append(m.Event("key", action, hid, delay=20))
        self._refresh()
        return True

    def _add(self, events):
        self.macro.events.extend(events)
        self._refresh()

    def _refresh(self):
        for r in self.step_rows:
            self.steps.remove(r)
        self.step_rows = []
        if not self.macro.events:
            self.steps.add(self.hint)
            self.step_rows.append(self.hint)
            return
        for i, e in enumerate(self.macro.events):
            row = Adw.ActionRow(title=f"{i + 1}. {m.describe(e)}")
            if e.kind == "move":
                for attr in ("dx", "dy"):
                    sp = Gtk.SpinButton.new_with_range(-500, 500, 1)
                    sp.set_value(getattr(e, attr))
                    sp.set_valign(Gtk.Align.CENTER)
                    sp.set_tooltip_text("Right (+) / left (−)" if attr == "dx" else "Down (+) / up (−)")
                    sp.connect("value-changed", lambda s, ev=e, a=attr: setattr(ev, a, int(s.get_value())))
                    row.add_suffix(sp)
            d = Gtk.SpinButton.new_with_range(0, 10000, 10)
            d.set_value(e.delay)
            d.set_valign(Gtk.Align.CENTER)
            d.set_tooltip_text("Delay after this step (ms)")
            d.connect("value-changed", lambda s, ev=e: setattr(ev, "delay", int(s.get_value())))
            row.add_suffix(d)
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER)
            rm.add_css_class("flat")
            rm.connect("clicked", lambda *_, ev=e: (self.macro.events.remove(ev), self._refresh()))
            row.add_suffix(rm)
            self.steps.add(row)
            self.step_rows.append(row)

    def _save(self, *_):
        self.macro.name = self.name_row.get_text().strip() or "Untitled macro"
        self.macro.mode = self._modes[self.mode_row.get_selected()]
        self.macro.repeat = int(self.rep_row.get_value())
        if not self.macro.events:
            return
        data, _, _ = m.encode_device(self.macro)
        if len(data) > 256:
            self.set_title("Too long — remove some steps")
            return
        if self.on_save(self.macro) is not False:
            self.close()
