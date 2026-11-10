#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Brage Dorin
"""Native Linux control for the Genius Scorpion K7 keyboard (USB 0458:6013).

Protocol recovered from the vendor Windows app ("Gaming Keyboard.exe",
sha256 48333a9c...22e6); see PROTOCOL-NOTES.md for addresses and evidence.

All traffic is HID Feature report 7 (8 bytes) on the vendor collection with
usage page 0xFF01, exposed by Linux as a /dev/hidraw node. No kernel driver
is needed.

Commands:
  find                       show the matching hidraw node
  dump ADDR LEN [-o FILE]    read device memory (read-only sequence)
  raw-get                    one GetFeature(7), print bytes
  release                    return keyboard to normal typing (07 20)
  led / set-led ...           show or change lighting (writes need --yes)
Every command ends with 07 20 so the keyboard returns to normal typing.
"""
import argparse
import contextlib
import fcntl
import glob
import os
import sys
import time

VID, PIDS = 0x0458, {0x6013}
ALT_IDS = {(0x2130, 0x30FA)}
REPORT_ID = 7
REPORT_LEN = 8
OP_MEM = 0x18

# <linux/hidraw.h>: _IOC(_IOC_WRITE|_IOC_READ, 'H', nr, len)
def _ioc_rw(nr, size):
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr

def HIDIOCSFEATURE(n): return _ioc_rw(0x06, n)
def HIDIOCGFEATURE(n): return _ioc_rw(0x07, n)


def _uevent(node):
    d = {}
    with open(f"/sys/class/hidraw/{node}/device/uevent") as f:
        for line in f:
            k, _, v = line.strip().partition("=")
            d[k] = v
    return d


def _has_vendor_ff01(node):
    """True if the report descriptor declares Usage Page 0xFF01 + Report ID 7."""
    with open(f"/sys/class/hidraw/{node}/device/report_descriptor", "rb") as f:
        rd = f.read()
    return b"\x06\x01\xff" in rd and b"\x85\x07" in rd


def find_node():
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        node = os.path.basename(path)
        bus, vid, pid = _uevent(node)["HID_ID"].split(":")
        vid, pid = int(vid, 16), int(pid, 16)
        if ((vid == VID and pid in PIDS) or (vid, pid) in ALT_IDS) and _has_vendor_ff01(node):
            return f"/dev/{node}"
    sys.exit("Scorpion K7 vendor interface (usage page 0xFF01) not found")


class K7:
    def __init__(self, dev):
        self.fd = os.open(dev, os.O_RDWR)
        # Any 07 18 memory access suspends typing until apply (07 18 11/00)
        # is sent (observed: no key reports after a write until apply).
        self.mem_used = False

    def close(self):
        os.close(self.fd)

    # The vendor app sends this only when it exits (0x412dc0, 0x415ba0,
    # 0x414fc1). Without it the keyboard stayed in software-control mode and
    # stopped typing after a write (observed 2026-11-10). Always end with it.
    def release(self):
        self.set_feature([0x20, 0, 0, 0, 0, 0, 0])

    def set_feature(self, payload):
        buf = bytearray([REPORT_ID]) + bytearray(payload)
        buf = buf.ljust(REPORT_LEN, b"\0")
        assert len(buf) == REPORT_LEN, buf
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(REPORT_LEN), buf)

    def get_feature(self):
        buf = bytearray([REPORT_ID]) + bytearray(REPORT_LEN - 1)
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(REPORT_LEN), buf)
        return bytes(buf)

    # Mirrors 0x407070 read(addr, buf, n) in the vendor app, step for step.
    def read_block(self, addr, n):
        if not 1 <= n <= 256:
            raise ValueError("block length must be 1..256")
        lo, hi, last = addr & 0xFF, (addr >> 8) & 0xFF, (n - 1) & 0xFF
        self.mem_used = True
        for i in range(n):                       # stage n bytes (no commit)
            self.set_feature([OP_MEM, 0x03, i, lo, hi, 0x00, last])
        time.sleep(0.001)
        self.set_feature([OP_MEM, 0x00, 0x00, lo, hi, 0x00, 0x00])
        time.sleep(0.001)
        out = bytearray()
        for i in range(n):
            self.set_feature([OP_MEM, 0x05, i, lo, hi, 0x00, last])
            time.sleep(0.001)
            out.append(self.get_feature()[1])
        time.sleep(0.001)
        self.set_feature([OP_MEM, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00])
        return bytes(out)

    # Mirrors 0x407270 write(addr, buf, n): stage bytes, commit (09), end (01).
    def write_block(self, addr, data):
        n = len(data)
        if not 1 <= n <= 256:
            raise ValueError("block length must be 1..256")
        lo, hi, last = addr & 0xFF, (addr >> 8) & 0xFF, (n - 1) & 0xFF
        self.mem_used = True
        for i, b in enumerate(data):
            self.set_feature([OP_MEM, 0x03, i, lo, hi, b, last])
        time.sleep(0.002)
        self.set_feature([OP_MEM, 0x09, 0x00, lo, hi, 0x00, last])
        time.sleep(0.010)
        self.set_feature([OP_MEM, 0x01, 0x00, lo, hi, 0x00, 0x00])

    # Mirrors 0x412a02: reload settings after a profile write.
    def apply(self):
        self.set_feature([OP_MEM, 0x11, 0x00, 0x00, 0x00, 0x00, 0x00])
        time.sleep(0.010)
        self.set_feature([OP_MEM, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        self.mem_used = False

    def read_range(self, addr, n, chunk=8):
        """Read n bytes in the same 8-byte transactions the vendor app uses."""
        out = bytearray()
        while len(out) < n:
            m = min(chunk, n - len(out))
            out += self.read_block(addr + len(out), m)
        return bytes(out)

    def macro_slot(self, base, slot):
        """-> (entry_addr, count, data_addr, capacity_bytes, data)"""
        tbl = base + MACRO_TABLE_OFF
        entry_addr = tbl + 5 * slot
        e = self.read_range(entry_addr, 10)          # this entry + next one
        if e[0] != 0x07:
            raise RuntimeError(f"unexpected macro slot entry: {e[:5].hex(' ')}")
        data_addr = e[2] | e[3] << 8
        nxt = e[7] | e[8] << 8 if e[5] == 0x07 else data_addr + 16
        cap = nxt - data_addr
        data = self.read_range(data_addr, min(cap, 16))
        return entry_addr, e[1], data_addr, cap, data

    def write_macro_slot(self, base, slot, data, count):
        entry_addr, _, data_addr, cap, _ = self.macro_slot(base, slot)
        if len(data) > cap:
            raise ValueError(f"macro needs {len(data)} bytes, slot holds {cap}")
        for off in range(0, len(data), 8):
            self.write_block(data_addr + off, data[off:off + 8])
        self.write_block(entry_addr + 1, bytes([count]))
        self.apply()

    def base(self):
        hdr = self.read_block(HEADER_ADDR, 8)
        if hdr[:2] != b"\xe1\x1e":
            raise RuntimeError(f"unexpected header at 0xD0: {hdr.hex(' ')}")
        return hdr[2] | hdr[3] << 8


def usb_device_node():
    """/dev/bus/usb/BBB/DDD of the keyboard, found via sysfs."""
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            vid = int(open(f"{d}/idVendor").read(), 16)
            pid = int(open(f"{d}/idProduct").read(), 16)
        except OSError:
            continue
        if (vid == VID and pid in PIDS) or (vid, pid) in ALT_IDS:
            bus = int(open(f"{d}/busnum").read())
            dev = int(open(f"{d}/devnum").read())
            return f"/dev/bus/usb/{bus:03d}/{dev:03d}"
    return None


USBDEVFS_RESET = (ord("U") << 8) | 20   # _IO('U', 20)


def usb_reset(wait=3.0):
    """Software 'unplug/replug': USB port reset, then wait for the vendor
    hidraw node to come back. Needs write access to the USB device node
    (see 70-scorpion-k7.rules). Returns the new hidraw path or raises."""
    node = usb_device_node()
    if node is None:
        raise RuntimeError("keyboard not found on USB")
    if not os.access(node, os.W_OK):
        raise PermissionError(f"no write access to {node}; install 70-scorpion-k7.rules")
    fd = os.open(node, os.O_WRONLY)
    try:
        fcntl.ioctl(fd, USBDEVFS_RESET, 0)
    finally:
        os.close(fd)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.2)
        try:
            return find_node()
        except SystemExit:
            continue
    raise RuntimeError("keyboard did not come back after USB reset")


@contextlib.contextmanager
def session(dev=None):
    """Open the keyboard and always hand it back typing: apply (07 18 11/00)
    after any memory access, then release (07 20)."""
    k = K7(dev or find_node())
    try:
        yield k
    finally:
        try:
            if k.mem_used:          # never leave typing suspended (reads too)
                k.apply()
            k.release()
        finally:
            k.close()


HEADER_ADDR = 0xD0
LED_OFF = 0x18          # LED record offset from profile base

KEYMAP_OFF = 0x48       # 20 columns x 8 rows, column-major (matches KBconfig.ini)
KEYMAP_COLS, KEYMAP_ROWS = 20, 8
FN_OFF = 0xE8           # Fn+F1..F12 table (12 bytes) right after the key map
MACRO_TABLE_OFF = 0xF8  # 5-byte slots: 07 xx addrLo addrHi 00

# Mode nibble -> (name, description). Names are the vendor's (KbIndexText.ini
# ids 30-42); assuming nibble = id - 30. Not yet confirmed on the device.
MODES = {
    0: ("Backlight off", "All LEDs off"),
    1: ("Breathing", "Slowly fades in and out"),
    2: ("Constant", "Steady, always on"),
    3: ("Response", "Keys light up when pressed"),
    4: ("Neon", "Whole keyboard shifts through colours"),
    5: ("Ambilight", "Soft ambient colour flow"),
    6: ("Comet tail", "A light streak runs across the keys"),
    7: ("Single water", "Single ripple from the pressed key"),
    8: ("Flow water", "Continuous ripples across the board"),
    9: ("Flashing", "Blinks on and off"),
    10: ("Monochromatic wave", "One colour sweeping across"),
    11: ("Partition", "Zones in different colours"),
    12: ("Combination", "Mix of effects"),
}
BRIGHTNESS_MAX = 4      # observed: 5..7 turn the backlight off
SPEED_MAX = 4           # assumed same scale as brightness; to confirm

HID_NAMES = {
    **{0x04 + i: chr(ord("A") + i) for i in range(26)},
    **{0x1E + i: str((i + 1) % 10) for i in range(10)},
    0x28: "Enter", 0x29: "Esc", 0x2A: "Backspace", 0x2B: "Tab", 0x2C: "Space",
    0x2D: "-", 0x2E: "=", 0x2F: "[", 0x30: "]", 0x31: "\\", 0x32: "#", 0x33: ";",
    0x34: "'", 0x35: "`", 0x36: ",", 0x37: ".", 0x38: "/", 0x39: "CapsLock",
    **{0x3A + i: f"F{i + 1}" for i in range(12)},
    0x46: "PrtSc", 0x47: "ScrLk", 0x48: "Pause", 0x49: "Ins", 0x4A: "Home",
    0x4B: "PgUp", 0x4C: "Del", 0x4D: "End", 0x4E: "PgDn", 0x4F: "Right",
    0x50: "Left", 0x51: "Down", 0x52: "Up", 0x53: "NumLk", 0x54: "KP/",
    0x55: "KP*", 0x56: "KP-", 0x57: "KP+", 0x58: "KPEnter",
    **{0x59 + i: f"KP{(i + 1) % 10}" for i in range(10)},
    0x63: "KP.", 0x64: "\\|(ISO)", 0x65: "Menu", 0x72: "F23",
    0xE0: "LCtrl", 0xE1: "LShift", 0xE2: "LAlt", 0xE3: "LWin",
    0xE4: "RCtrl", 0xE5: "RShift", 0xE6: "RAlt", 0xE7: "RWin", 0xFF: "—",
}


def key_name(code):
    return HID_NAMES.get(code, f"0x{code:02X}")


def decode_macro(data):
    """Inferred format: 2-byte header, then (flags, key) pairs; flags bit7 =
    press, low 7 bits = delay; 00 00 ends. Seen: Copilot = Win+Shift+F23."""
    steps = []
    for i in range(2, len(data) - 1, 2):
        f, key = data[i], data[i + 1]
        if f == 0 and key == 0:
            break
        steps.append(("press" if f & 0x80 else "release", key_name(key), f & 0x7F))
    return steps


COPILOT_SLOT = 1        # macro slot holding Win+Shift+F23 (verified in backup)
MOD_KEYS = (0xE0, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6, 0xE7)
COPILOT_DEFAULT = bytes.fromhex("0100 88e3 a8e1 9372 01e1 01e3 0172 0000")
HID_BY_NAME = {v.lower(): k for k, v in HID_NAMES.items()}


def encode_macro(keys):
    """keys: HID codes, e.g. [0xE0, 0x2C] for Ctrl+Space. Press in order,
    release in reverse (same style as the factory macros). Returns (bytes, count)."""
    keys = list(keys)
    if not 1 <= len(keys) <= 3:
        raise ValueError("a shortcut needs 1-3 keys")
    body = bytearray(b"\x01\x00")
    for k in keys:
        body += bytes([0x88, k])
    for k in reversed(keys):
        body += bytes([0x01, k])
    body += b"\x00\x00"
    return bytes(body), 2 * len(keys)


def macro_keys(data):
    """Keys pressed by a macro, in press order (for display)."""
    return [n for a, n, _ in decode_macro(data) if a == "press"]


def brightness_to_ui(dev):
    """Device 0 = brightest .. 4 = dimmest (observed); UI 0 = dim .. 4 = max."""
    return BRIGHTNESS_MAX - min(dev, BRIGHTNESS_MAX)


def brightness_from_ui(ui):
    return BRIGHTNESS_MAX - ui


def decode_led(rec):
    return {
        "direction": rec[0] >> 6,
        "brightness": (rec[0] >> 3) & 7,
        "speed": rec[0] & 7,
        "symmetry": rec[1] >> 7,
        "color_sel": (rec[1] >> 4) & 7,
        "mode": rec[1] & 0xF,
        "color_mask": rec[2],
        "mode_mask": rec[3],
        "tables": (rec[4] | rec[5] << 8, rec[6] | rec[7] << 8),
    }


def encode_led(rec, **kw):
    r = bytearray(rec)
    d = decode_led(rec)
    d.update({k: v for k, v in kw.items() if v is not None})
    r[0] = (d["direction"] & 3) << 6 | (d["brightness"] & 7) << 3 | (d["speed"] & 7)
    r[1] = (d["symmetry"] & 1) << 7 | (d["color_sel"] & 7) << 4 | (d["mode"] & 0xF)
    return bytes(r)


def hexdump(base, data):
    for off in range(0, len(data), 16):
        chunk = data[off:off + 16]
        print(f"{base + off:04x}  {chunk.hex(' ')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find")
    d = sub.add_parser("dump")
    d.add_argument("addr", type=lambda s: int(s, 0))
    d.add_argument("length", type=lambda s: int(s, 0))
    d.add_argument("-o", "--output")
    d.add_argument("--block", type=int, default=8, help="bytes per read transaction (app uses 8)")
    r = sub.add_parser("restore", help="write a backup image (from 'dump 0x0 0x1000') back")
    r.add_argument("file")
    r.add_argument("--start", type=lambda s: int(s, 0), default=0xD0)
    r.add_argument("--end", type=lambda s: int(s, 0), default=0x400)
    r.add_argument("--yes", action="store_true")
    sub.add_parser("raw-get")
    sub.add_parser("release", help="return keyboard to normal mode (07 20)")
    sub.add_parser("led", help="show current lighting record")
    s = sub.add_parser("set-led", help="change lighting (writes to keyboard)")
    for f in ("mode", "brightness", "speed", "direction", "symmetry", "color_sel"):
        s.add_argument(f"--{f.replace('_', '-')}", dest=f, type=int)
    s.add_argument("--yes", action="store_true", help="really write")
    a = ap.parse_args()

    dev = find_node()
    if a.cmd == "find":
        print(dev)
        return
    with session(dev) as k:
        if a.cmd == "restore":
            img = open(a.file, "rb").read()
            if len(img) < a.end or img[0xD0:0xD2] != b"\xe1\x1e":
                sys.exit("not a full backup image (expected 4 KB from 'dump 0x0 0x1000')")
            print(f"restore 0x{a.start:x}..0x{a.end:x} ({a.end - a.start} bytes) from {a.file}")
            if not a.yes:
                print("dry run; add --yes to write")
            else:
                for off in range(a.start, a.end, 8):
                    k.write_block(off, img[off:min(off + 8, a.end)])
                k.apply()
                print("restored")
        elif a.cmd == "release":
            print("release sent (07 20)")
        elif a.cmd == "raw-get":
            print(k.get_feature().hex(" "))
        elif a.cmd in ("led", "set-led"):
            addr = k.base() + LED_OFF
            rec = k.read_block(addr, 8)
            print(f"LED record @0x{addr:x}: {rec.hex(' ')}  {decode_led(rec)}")
            if a.cmd == "set-led":
                kw = {f: getattr(a, f) for f in ("mode", "brightness", "speed", "direction", "symmetry", "color_sel")}
                new = encode_led(rec, **kw)
                print(f"new record          : {new.hex(' ')}  {decode_led(new)}")
                if new == rec:
                    print("no change")
                elif not a.yes:
                    print("dry run; add --yes to write")
                else:
                    k.write_block(addr, new)
                    k.apply()
                    back = k.read_block(addr, 8)
                    print(f"read back           : {back.hex(' ')}  {'OK' if back == new else 'MISMATCH'}")
        elif a.cmd == "dump":
            data = bytearray()
            addr = a.addr
            while len(data) < a.length:
                n = min(a.block, a.length - len(data))
                data += k.read_block(addr, n)
                addr += n
            hexdump(a.addr, data)
            if a.output:
                with open(a.output, "wb") as f:
                    f.write(data)


if __name__ == "__main__":
    main()
