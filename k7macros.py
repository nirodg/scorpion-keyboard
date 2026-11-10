# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Brage Dorin
"""Macros for the Genius Scorpion K7: model, official-file import, and the
on-keyboard encoding (reverse-engineered from the official app; see
PROTOCOL-NOTES.md, "Macros").
"""
import json
import re
from dataclasses import asdict, dataclass, field

# ---------------------------------------------------------------- constants
MODE_TIMES = "times"      # play N times              (vendor mode 0)
MODE_TOGGLE = "toggle"    # repeat until pressed again (vendor mode 1)
MODE_HOLD = "hold"        # repeat while held — rapid fire (vendor mode 2)
MODES = {MODE_TIMES: 0, MODE_TOGGLE: 1, MODE_HOLD: 2}
MODE_NAMES = {MODE_TIMES: "Play N times", MODE_TOGGLE: "Repeat until pressed again",
              MODE_HOLD: "Repeat while held (rapid fire)"}

# Device mouse-button codes. The official app translates PC codes 240/241/242
# (left/right/middle) to 0xF1/0xF3/0xF2 (HID<->VK translator at 0x42c99c).
MOUSE_LEFT, MOUSE_MIDDLE, MOUSE_RIGHT = 0xF1, 0xF2, 0xF3
PC_MOUSE = {240: MOUSE_LEFT, 241: MOUSE_RIGHT, 242: MOUSE_MIDDLE}
MOUSE_NAMES = {MOUSE_LEFT: "Left", MOUSE_MIDDLE: "Middle", MOUSE_RIGHT: "Right", 0xF0: "0xF0"}
DEV_MOVE, DEV_WHEEL, DEV_MEDIA, DEV_NOP = 0xF6, 0xF7, 0xFA, 0xF8
SLOTS = 20
SLOT_TABLE = 0x1D8 - 0xE0                      # offset from profile base B
DATA_START = 0x240                             # first slot data address (factory layout)
# Device memory is 2 KB (0x800-0xFFF mirrors 0x000-0x7FF). 0x3FF holds an unknown
# marker byte and 0x7C0-0x7FF the keyboard's identity string, so macros stay below 0x3F8.
DATA_END = 0x3F8

# Windows virtual-key code -> USB HID usage (standard mapping)
VK_TO_HID = {
    **{0x41 + i: 0x04 + i for i in range(26)},                 # A..Z
    0x31: 0x1E, 0x32: 0x1F, 0x33: 0x20, 0x34: 0x21, 0x35: 0x22,
    0x36: 0x23, 0x37: 0x24, 0x38: 0x25, 0x39: 0x26, 0x30: 0x27,
    0x0D: 0x28, 0x1B: 0x29, 0x08: 0x2A, 0x09: 0x2B, 0x20: 0x2C,
    0xBD: 0x2D, 0xBB: 0x2E, 0xDB: 0x2F, 0xDD: 0x30, 0xDC: 0x31,
    0xBA: 0x33, 0xDE: 0x34, 0xC0: 0x35, 0xBC: 0x36, 0xBE: 0x37, 0xBF: 0x38, 0x14: 0x39,
    **{0x70 + i: 0x3A + i for i in range(12)},                 # F1..F12
    **{0x7C + i: 0x68 + i for i in range(12)},                 # F13..F24
    0x2C: 0x46, 0x91: 0x47, 0x13: 0x48, 0x2D: 0x49, 0x24: 0x4A, 0x21: 0x4B,
    0x2E: 0x4C, 0x23: 0x4D, 0x22: 0x4E, 0x27: 0x4F, 0x25: 0x50, 0x28: 0x51, 0x26: 0x52,
    0x90: 0x53, 0x6F: 0x54, 0x6A: 0x55, 0x6D: 0x56, 0x6B: 0x57,
    **{0x61 + i: 0x59 + i for i in range(9)}, 0x60: 0x62, 0x6E: 0x63,   # numpad
    0xE2: 0x64, 0x5D: 0x65,
    0x11: 0xE0, 0x10: 0xE1, 0x12: 0xE2,                        # generic Ctrl/Shift/Alt -> left
    0xA2: 0xE0, 0xA0: 0xE1, 0xA4: 0xE2, 0x5B: 0xE3,
    0xA3: 0xE4, 0xA1: 0xE5, 0xA5: 0xE6, 0x5C: 0xE7,
}


# ---------------------------------------------------------------- model
@dataclass
class Event:
    kind: str                 # key | mouse | move | wheel | media
    action: str = "press"     # press | release   (key, mouse, media)
    code: int = 0             # HID usage (key), MOUSE_* (mouse), consumer usage (media)
    dx: int = 0               # move
    dy: int = 0               # move (positive = down)
    amount: int = 0           # wheel (positive = up)
    delay: int = 10           # ms after this event


@dataclass
class Macro:
    name: str
    mode: str = MODE_TIMES
    repeat: int = 1
    events: list = field(default_factory=list)
    builtin: bool = False

    def to_dict(self):
        d = asdict(self)
        d.pop("builtin")
        return d

    @classmethod
    def from_dict(cls, d, builtin=False):
        return cls(name=d["name"], mode=d.get("mode", MODE_TIMES), repeat=d.get("repeat", 1),
                   events=[Event(**e) for e in d.get("events", [])], builtin=builtin)


def load_library(path, builtin=False):
    with open(path) as f:
        return [Macro.from_dict(d, builtin) for d in json.load(f)]


def save_library(path, macros):
    with open(path, "w") as f:
        json.dump([m.to_dict() for m in macros], f, indent=1)


# ---------------------------------------------------------------- official .dat import
def _records(path):
    txt = open(path, "rb").read().decode("latin1").replace("\r", "")
    v3 = re.search(r"DAT_VER=3", txt) is not None
    for m in re.finditer(r"^\d+=(\d+)$", txt, re.M):
        d = m.group(1)
        yield v3, bytes(int(d[i:i + 3]) for i in range(0, len(d) - len(d) % 3, 3))


def parse_dat(path):
    """Read an official macro file (defMacro.dat or a DAT_VER=3 export)."""
    out = []
    for v3, r in _records(path):
        name_len, m_off, ev_off = (60, 59, 81) if v3 else (20, 20, 35)
        name = r[:name_len].decode("utf-16-le", "replace").split("\0")[0]
        mode_b, repeat, count = r[m_off], r[m_off + 2 if v3 else m_off + 1] | (
            r[m_off + 3 if v3 else m_off + 2] << 8), r[m_off + 4 if v3 else m_off + 3]
        mode = {0: MODE_TIMES, 1: MODE_TOGGLE, 2: MODE_HOLD}.get(mode_b & 3, MODE_TIMES)
        evs = []
        for i in range(count):
            t, code, p0, p1, p2, p3, dl, dh = r[ev_off + 8 * i: ev_off + 8 * i + 8]
            delay = dl | dh << 8
            if t in (1, 2) and code in PC_MOUSE:
                evs.append(Event("mouse", "press" if t == 1 else "release", PC_MOUSE[code], delay=delay))
            elif t in (1, 2):
                if code in VK_TO_HID:
                    evs.append(Event("key", "press" if t == 1 else "release", VK_TO_HID[code], delay=delay))
            elif 3 <= t <= 6:
                x, y = p0 | p1 << 8, p2 | p3 << 8
                sx, sy = {3: (-1, -1), 4: (-1, 1), 5: (1, -1), 6: (1, 1)}[t]
                evs.append(Event("move", dx=sx * x, dy=sy * y, delay=delay))
            elif t in (7, 8):
                evs.append(Event("wheel", amount=(p0 if t == 7 else -p0), delay=delay))
            elif t in (0xB, 0xC):
                evs.append(Event("media", "press" if t == 0xC else "release", p0 | p1 << 8, delay=delay))
            # type 9 (code 248) and LED events are not supported -> skipped
        out.append(Macro(name, mode, max(repeat, 1), evs))
    return out


# ---------------------------------------------------------------- device encoding
def _delay_units(ms):
    """-> (units for the event pair, list of padding units) — avoids the vendor's
    multiple-of-127 bug."""
    u = max(ms >> 3, 1)
    pad = []
    while u > 127:
        pad.append(127)
        u -= 127
    return max(u, 1), pad


def encode_device(macro):
    """-> (data bytes, entry_count, mode_value). data is padded to a multiple of 8."""
    mode = MODES[macro.mode]
    data = bytearray(int(macro.repeat).to_bytes(2, "little") if mode == 0 else b"\x01\x00")
    count = 0
    for e in macro.events:
        d, pad = _delay_units(e.delay)
        press = 0x80 if e.action == "press" else 0
        if e.kind in ("key", "mouse"):
            data += bytes([press | d, e.code & 0xFF])
        elif e.kind == "move":
            data += bytes([d, DEV_MOVE]) + int(e.dx).to_bytes(2, "little", signed=True) \
                + int(e.dy).to_bytes(2, "little", signed=True)
        elif e.kind == "wheel":
            amt = e.amount if e.amount >= 0 else 0x100 + e.amount
            data += bytes([d, DEV_WHEEL, amt & 0xFF, 0])
        elif e.kind == "media":
            data += bytes([press | d, DEV_MEDIA]) + int(e.code).to_bytes(2, "little")
        else:
            raise ValueError(f"unknown event kind {e.kind}")
        count += 1
        for p in pad:
            data += bytes([p, 0])
            count += 1
    data += b"\x00\x00"
    if len(data) % 8:
        data += bytes(8 - len(data) % 8)
    return bytes(data), count, mode


def decode_device(data, count):
    """Best-effort reverse of encode_device (for showing what is on the keyboard)."""
    evs, i = [], 2
    for _ in range(count):
        if i + 1 >= len(data):
            break
        b0, code = data[i], data[i + 1]
        i += 2
        delay = (b0 & 0x7F) * 8
        if code == 0 and b0 & 0x7F == 127 and evs:          # padding
            evs[-1].delay += 127 * 8
            continue
        act = "press" if b0 & 0x80 else "release"
        if code == DEV_MOVE:
            dx = int.from_bytes(data[i:i + 2], "little", signed=True)
            dy = int.from_bytes(data[i + 2:i + 4], "little", signed=True)
            i += 4
            evs.append(Event("move", dx=dx, dy=dy, delay=delay))
        elif code == DEV_WHEEL:
            a = data[i]
            i += 2
            evs.append(Event("wheel", amount=a if a < 0x80 else a - 0x100, delay=delay))
        elif code == DEV_MEDIA:
            evs.append(Event("media", act, int.from_bytes(data[i:i + 2], "little"), delay=delay))
            i += 2
        elif code in MOUSE_NAMES:
            evs.append(Event("mouse", act, code, delay=delay))
        else:
            evs.append(Event("key", act, code, delay=delay))
    return evs


def encode_entry(addr, count, mode):
    return bytes([0x07, (count & 0x7F) | (0x80 if mode == 2 else 0), addr & 0xFF,
                  (0x80 if mode == 1 else 0) | ((addr >> 8) & 0x3F), count >> 7])


def decode_entry(e):
    """-> (addr, count, mode_value)"""
    mode = 2 if e[1] & 0x80 else 1 if e[3] & 0x80 else 0
    return e[2] | (e[3] & 0x3F) << 8, (e[1] & 0x7F) | e[4] << 7, mode


def slot_code(slot):
    """Key-map code that makes a key play macro slot `slot`."""
    return 0xA5 + slot if slot < 11 else 0xAD + slot


def code_slot(code):
    if 0xA5 <= code <= 0xAF:
        return code - 0xA5
    if 0xB8 <= code <= 0xC0:
        return code - 0xAD
    return None


def plan_layout(slot_data):
    """slot_data: list of 20 byte strings (multiple of 8). -> list of addresses
    (contiguous from DATA_START), or raise if it doesn't fit."""
    addrs, a = [], DATA_START
    for d in slot_data:
        addrs.append(a)
        a += max(len(d), 8)
    if a > DATA_END:
        raise ValueError(f"macros need {a - DATA_START} bytes, only {DATA_END - DATA_START} available")
    return addrs


def describe(e):
    """Short human text for an event."""
    import scorpion_k7 as k7
    arrow = "↓" if e.action == "press" else "↑"
    if e.kind == "key":
        return f"{arrow} {k7.key_name(e.code)}"
    if e.kind == "mouse":
        return f"{arrow} {MOUSE_NAMES.get(e.code, hex(e.code))} click"
    if e.kind == "move":
        return f"Move mouse {e.dx:+d}, {e.dy:+d}"
    if e.kind == "wheel":
        return f"Scroll {'up' if e.amount > 0 else 'down'} {abs(e.amount)}"
    if e.kind == "media":
        return f"{arrow} Media 0x{e.code:03X}"
    return e.kind


# ---------------------------------------------------------------- key map
KEYMAP_OFF = 0x48             # from base B; 20 columns x 8 rows, column-major
COPILOT_CELL = 9 * 8 + 7      # col 9, row 7 (factory value 0xA6 = slot 1)
# Factory key map (identical to the official KBconfig.ini; cell = col*8 + row)
FACTORY_KEYMAP = bytes.fromhex(
    "1d1eb0cde6ccb1b20620b4cbcad3e4d519223ed2e2d0d1d4093cf0ecede8e0cf"
    "87212ce3da8b8a88893d04d9f69091f14035161c1324e9e7172a4f5657855839"
    "433b141231273236422e070c302634a6443a1a464a4c4937450f08474d4b4e38"
    "653352535f5c5964290e5054605d5a62150d5155615e5b63412d2b182f250b11"
    "1b1fd7c9cee5d6d80af3eaebeeff051028233fb7b6e1b5b3ffffffffffffffff"
)


def assignable_keys():
    """Cells that can hold a macro: ordinary keys (letters, digits, F-keys,
    navigation, numpad, punctuation). -> list of (cell, factory_code)."""
    out = []
    for cell, code in enumerate(FACTORY_KEYMAP):
        if 0x04 <= code <= 0x63 and code != 0x32 and cell != COPILOT_CELL:   # no ISO/#/Menu keys on the K7
            out.append((cell, code))
    return out


def cell_for_code(code):
    """Cell whose factory function is HID `code` (e.g. 0x59 = Numpad 1)."""
    for cell, c in enumerate(FACTORY_KEYMAP):
        if c == code and cell != COPILOT_CELL:
            return cell
    return None


# ---------------------------------------------------------------- keyboard I/O
COPILOT_SLOT = 1


def read_bank(k, base):
    """Read slot table, all slot data and the key map.
    -> dict(entries=[(addr, count, mode)], data=[bytes], keymap=bytes)"""
    raw = k.read_range(base + SLOT_TABLE, 5 * SLOTS)
    entries = [decode_entry(raw[5 * s:5 * s + 5]) for s in range(SLOTS)]
    data = []
    for s, (addr, count, mode) in enumerate(entries):
        nxt = entries[s + 1][0] if s + 1 < SLOTS else addr + 8
        cap = nxt - addr if nxt > addr else 8
        data.append(k.read_range(addr, min(cap, 256)))
    keymap = k.read_range(base + KEYMAP_OFF, 160)
    return {"entries": entries, "data": data, "keymap": keymap}


def key_assignments(bank):
    """-> {cell: (slot, events)} for every key (except Copilot) playing a macro."""
    out = {}
    for cell, code in enumerate(bank["keymap"]):
        s = code_slot(code)
        if s is not None and cell != COPILOT_CELL:
            _, count, _ = bank["entries"][s]
            out[cell] = (s, decode_device(bank["data"][s], count))
    return out


def build_bank(bank, assignments):
    """Plan a new macro bank. assignments: {cell: Macro} (max 19).
    Keeps the Copilot slot's bytes unchanged. -> (slot_data, entries, keymap)"""
    if len(assignments) > SLOTS - 1:
        raise ValueError(f"at most {SLOTS - 1} macro keys")
    slot_data, meta = [None] * SLOTS, [None] * SLOTS
    _, c_count, c_mode = bank["entries"][COPILOT_SLOT]
    c_len = len(encode_from_count(bank["data"][COPILOT_SLOT], c_count))
    slot_data[COPILOT_SLOT] = bank["data"][COPILOT_SLOT][:c_len]
    meta[COPILOT_SLOT] = (c_count, c_mode)
    free = [s for s in range(SLOTS) if s != COPILOT_SLOT]
    cell_slot = {}
    for (cell, macro), s in zip(sorted(assignments.items()), free):
        data, count, mode = encode_device(macro)
        slot_data[s], meta[s], cell_slot[cell] = data, (count, mode), s
    for s in range(SLOTS):
        if slot_data[s] is None:
            slot_data[s], meta[s] = b"\x01\x00" + bytes(6), (0, 0)
    addrs = plan_layout(slot_data)
    entries = b"".join(encode_entry(a, c, m) for a, (c, m) in zip(addrs, meta))
    keymap = bytearray(bank["keymap"])
    for cell, code in enumerate(keymap):
        if cell != COPILOT_CELL and code_slot(code) is not None:
            keymap[cell] = FACTORY_KEYMAP[cell]          # free keys no longer assigned
    for cell, s in cell_slot.items():
        keymap[cell] = slot_code(s)
    return slot_data, addrs, entries, bytes(keymap)


def encode_from_count(data, count):
    """Length of an existing device macro (header + pairs + payloads), rounded to 8."""
    i = 2
    for _ in range(count):
        code = data[i + 1] if i + 1 < len(data) else 0
        i += 2 + {DEV_MOVE: 4, DEV_WHEEL: 2, DEV_MEDIA: 2}.get(code, 0)
    i += 2
    return data[:i + (-i % 8)]


def write_bank(k, base, bank, assignments):
    """Write macros + slot table + changed key-map columns; caller's session applies."""
    slot_data, addrs, entries, keymap = build_bank(bank, assignments)
    for data, addr in zip(slot_data, addrs):
        for off in range(0, len(data), 8):
            k.write_block(addr + off, data[off:off + 8])
    for off in range(0, len(entries), 8):
        k.write_block(base + SLOT_TABLE + off, entries[off:off + 8])
    for col in range(20):
        a, b = bank["keymap"][col * 8:col * 8 + 8], keymap[col * 8:col * 8 + 8]
        if a != b:
            k.write_block(base + KEYMAP_OFF + col * 8, b)
    k.apply()
    return keymap


# ---------------------------------------------------------------- recording + presets
# GDK keyval name -> HID usage (unshifted names; used by the macro recorder)
KEYVAL_HID = {
    **{chr(ord("a") + i): 0x04 + i for i in range(26)},
    **{str((i + 1) % 10): 0x1E + i for i in range(10)},
    "Return": 0x28, "Escape": 0x29, "BackSpace": 0x2A, "Tab": 0x2B, "ISO_Left_Tab": 0x2B, "space": 0x2C,
    "minus": 0x2D, "equal": 0x2E, "bracketleft": 0x2F, "bracketright": 0x30, "backslash": 0x31,
    "semicolon": 0x33, "apostrophe": 0x34, "grave": 0x35, "comma": 0x36, "period": 0x37, "slash": 0x38,
    "Caps_Lock": 0x39, **{f"F{i + 1}": 0x3A + i for i in range(12)}, **{f"F{i + 13}": 0x68 + i for i in range(12)},
    "Print": 0x46, "Scroll_Lock": 0x47, "Pause": 0x48, "Insert": 0x49, "Home": 0x4A, "Page_Up": 0x4B,
    "Delete": 0x4C, "End": 0x4D, "Page_Down": 0x4E, "Right": 0x4F, "Left": 0x50, "Down": 0x51, "Up": 0x52,
    "Num_Lock": 0x53, "KP_Divide": 0x54, "KP_Multiply": 0x55, "KP_Subtract": 0x56, "KP_Add": 0x57,
    "KP_Enter": 0x58, **{f"KP_{(i + 1) % 10}": 0x59 + i for i in range(10)}, "KP_Decimal": 0x63,
    "less": 0x64, "Menu": 0x65,
    "Control_L": 0xE0, "Shift_L": 0xE1, "Alt_L": 0xE2, "Super_L": 0xE3, "Meta_L": 0xE3,
    "Control_R": 0xE4, "Shift_R": 0xE5, "Alt_R": 0xE6, "ISO_Level3_Shift": 0xE6, "Super_R": 0xE7,
}


def combo(*keys, name, mode=MODE_TIMES):
    """Helper: press keys in order, release in reverse."""
    ev = [Event("key", "press", k, delay=20) for k in keys]
    ev += [Event("key", "release", k, delay=20) for k in reversed(keys)]
    return Macro(name, mode, 1, ev, builtin=True)


PRODUCTIVITY_MACROS = [
    combo(0xE3, 0x0F, name="Lock screen (Super+L)"),
    combo(0xE3, 0x07, name="Show desktop (Super+D)"),
    Macro("Copy everything (Ctrl+A, Ctrl+C)", MODE_TIMES, 1,
          combo(0xE0, 0x04, name="").events + combo(0xE0, 0x06, name="").events, builtin=True),
    combo(0xE0, 0xE1, 0x19, name="Paste as plain text (Ctrl+Shift+V)"),
    combo(0xE2, 0x3D, name="Close window (Alt+F4)"),
]

# Built-in profiles: key = factory HID code of the physical key, value = macro name
BUILTIN_PROFILES = {
    "No macros": {},
    "Gaming (PUBG)": {0x59: "AKM recoil spray", 0x5A: "M4 recoil spray", 0x5B: "UMP9 recoil spray",
                      0x5C: "Auto sprint", 0x5D: "Open map", 0x5E: "First-aid kit",
                      0x5F: "Quick scope (right click)", 0x60: "Throw grenade", 0x61: "Melee strike"},
    "Productivity": {0x47: "Lock screen (Super+L)", 0x54: "Show desktop (Super+D)",
                     0x56: "Copy everything (Ctrl+A, Ctrl+C)", 0x57: "Paste as plain text (Ctrl+Shift+V)"},
}
