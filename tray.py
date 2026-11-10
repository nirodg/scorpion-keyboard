# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Brage Dorin
"""Minimal tray icon (StatusNotifierItem + DBusMenu) using only Gio.

GTK4 has no tray API and libappindicator needs GTK3, so this speaks the
freedesktop/KDE StatusNotifierItem D-Bus protocol directly. Works with the
Ubuntu AppIndicator GNOME extension, KDE Plasma, and other SNI hosts.
"""
import os

from gi.repository import Gio, GLib

SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
  </interface>
</node>"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/><arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/><arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/><arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/><arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/><arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/><arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/><arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/><arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/><arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="LayoutUpdated"><arg type="u" name="revision"/><arg type="i" name="parent"/></signal>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/><arg type="a(ias)" name="removedProps"/>
    </signal>
  </interface>
</node>"""


def _once(fn):
    def run():
        fn()
        return False          # never repeat the idle callback
    return run


class Tray:
    """items: list of (label, callback) tuples; None = separator.
    on_activate: called on left click (hosts that support it)."""

    def __init__(self, app_id, title, icon_name, items, on_activate):
        self.app_id, self.title, self.icon = app_id, title, icon_name
        self.items = items
        self.on_activate = on_activate
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._regs = [
            self.bus.register_object("/StatusNotifierItem",
                                     Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0],
                                     self._sni_call, self._sni_prop, None),
            self.bus.register_object("/MenuBar",
                                     Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0],
                                     self._menu_call, self._menu_prop, None),
        ]
        self._owner = Gio.bus_own_name_on_connection(
            self.bus, self.name, Gio.BusNameOwnerFlags.NONE, self._register, None)
        # Re-register if the tray host (e.g. the GNOME extension) restarts.
        self._watch = Gio.bus_watch_name_on_connection(
            self.bus, "org.kde.StatusNotifierWatcher", Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._register(), None)

    def _register(self, *_):
        self.bus.call("org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
                      "org.kde.StatusNotifierWatcher", "RegisterStatusNotifierItem",
                      GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, -1, None,
                      lambda bus, res: self._ignore(bus, res))

    @staticmethod
    def _ignore(bus, res):
        try:
            bus.call_finish(res)
        except GLib.Error:
            pass                       # no tray host running; nothing to do

    def remove(self):
        for r in self._regs:
            self.bus.unregister_object(r)
        Gio.bus_unwatch_name(self._watch)
        Gio.bus_unown_name(self._owner)

    # --- StatusNotifierItem ---
    def _sni_prop(self, conn, sender, path, iface, prop):
        return {
            "Category": GLib.Variant("s", "Hardware"),
            "Id": GLib.Variant("s", self.app_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", self.icon),
            "IconThemePath": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.icon, [], self.title, "Click to open settings")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/MenuBar"),
        }.get(prop)

    def _sni_call(self, conn, sender, path, iface, method, params, inv):
        if method in ("Activate", "SecondaryActivate"):
            GLib.idle_add(_once(self.on_activate))
        inv.return_value(None)

    # --- DBusMenu ---
    def _props(self, idx):
        item = self.items[idx - 1]
        if item is None:
            return {"type": GLib.Variant("s", "separator")}
        return {"label": GLib.Variant("s", item[0]), "enabled": GLib.Variant("b", True),
                "visible": GLib.Variant("b", True)}

    def _layout(self):
        children = [GLib.Variant("(ia{sv}av)", (i, self._props(i), []))
                    for i in range(1, len(self.items) + 1)]
        return (0, {"children-display": GLib.Variant("s", "submenu")}, children)

    def _menu_prop(self, conn, sender, path, iface, prop):
        return {"Version": GLib.Variant("u", 3), "TextDirection": GLib.Variant("s", "ltr"),
                "Status": GLib.Variant("s", "normal"), "IconThemePath": GLib.Variant("as", [])}.get(prop)

    def _menu_call(self, conn, sender, path, iface, method, params, inv):
        if method == "GetLayout":
            inv.return_value(GLib.Variant("(u(ia{sv}av))", (1, self._layout())))
        elif method == "GetGroupProperties":
            ids = params.unpack()[0] or range(1, len(self.items) + 1)
            res = [(i, self._props(i)) for i in ids if 1 <= i <= len(self.items)]
            inv.return_value(GLib.Variant("(a(ia{sv}))", (res,)))
        elif method == "GetProperty":
            i, name = params.unpack()
            inv.return_value(GLib.Variant("(v)", (self._props(i).get(name, GLib.Variant("s", "")),)))
        elif method == "Event":
            i, event = params.unpack()[:2]
            self._fire(i, event)
            inv.return_value(None)
        elif method == "EventGroup":
            for i, event, _d, _t in params.unpack()[0]:
                self._fire(i, event)
            inv.return_value(GLib.Variant("(ai)", ([],)))
        elif method == "AboutToShow":
            inv.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            inv.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            inv.return_value(None)

    def _fire(self, i, event):
        if event == "clicked" and 1 <= i <= len(self.items) and self.items[i - 1]:
            GLib.idle_add(_once(self.items[i - 1][1]))
