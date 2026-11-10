#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Brage Dorin
"""Diagnose the 'keyboard freezes after Apply' problem.

Keep tapping LEFT SHIFT for the whole run (it types nothing). The script
listens to the raw key reports on both keyboard interfaces (read-only copies;
typing is not affected), performs ONE lighting write + apply WITHOUT a
read-back, and records whether key reports keep arriving. If they stop, it
tries candidate recovery commands one at a time and reports which one brings
the reports back. Log: diag_freeze.log
"""
import glob
import os
import select
import sys
import threading
import time

import scorpion_k7 as k7

LOG = open("diag_freeze.log", "w")
events = []          # (t, node, bytes)
stop = False


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()


def keyboard_nodes():
    nodes = []
    for path in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        n = os.path.basename(path)
        if "00000458:00006013" in open(f"{path}/device/uevent").read():
            nodes.append(f"/dev/{n}")
    return nodes


def listener(nodes):
    fds = {os.open(n, os.O_RDONLY | os.O_NONBLOCK): n for n in nodes}
    while not stop and fds:
        r, _, _ = select.select(list(fds), [], [], 0.2)
        for fd in r:
            try:
                data = os.read(fd, 64)
            except BlockingIOError:
                continue
            except OSError:            # node vanished (USB reset/unplug)
                os.close(fd)
                del fds[fd]
                continue
            events.append((time.monotonic(), fds[fd], data))
    for fd in fds:
        os.close(fd)


def window(label, seconds):
    t0 = time.monotonic()
    log(f"--- {label}: tap LEFT SHIFT now ({seconds}s) ---")
    time.sleep(seconds)
    got = [e for e in events if e[0] >= t0]
    per = {}
    for _, n, d in got:
        per.setdefault(n, []).append(d.hex(" "))
    for n, lst in per.items():
        log(f"    {n}: {len(lst)} reports, e.g. {lst[:3]}")
    if not got:
        log("    NO key reports received")
    return len(got) > 0


def status(label):
    """GetFeature(7) only (pure read) - candidate freeze detector."""
    k = k7.K7(k7.find_node())
    try:
        st = k.get_feature().hex(" ")
    finally:
        k.close()
    log(f"    status [{label}]: {st}")
    return st


def send(raw, why):
    log(f"send {raw}  ({why})")
    k = k7.K7(k7.find_node())
    try:
        k.set_feature(bytes.fromhex(raw)[1:])
    finally:
        k.close()


def main():
    global stop
    nodes = keyboard_nodes()
    log(f"keyboard nodes: {nodes}; vendor node: {k7.find_node()}")
    threading.Thread(target=listener, args=(nodes,), daemon=True).start()

    if not window("baseline", 5):
        log("No reports even before the test: hidraw not readable or not tapping. Aborting, nothing sent.")
        return
    status("before")

    # One visible write: toggle brightness between 2 and 4. No read-back.
    k = k7.K7(k7.find_node())
    try:
        base = k.base()
        addr = base + k7.LED_OFF
        rec = k.read_block(addr, 8)
        cur = k7.decode_led(rec)["brightness"]
        new = k7.encode_led(rec, brightness=2 if cur >= 3 else 4)
        log(f"LED @0x{addr:x}: {rec.hex(' ')} -> {new.hex(' ')}")
        k.write_block(addr, new)
        log("write_block done (stage/commit/end)")
        if not window("after write, before apply", 4):
            log("FROZE ALREADY AFTER WRITE (before apply)")
        k.apply()
        log("apply done (07 18 11 / 07 18 00)")
        time.sleep(0.3)
        k.release()
        log("release done (07 20)")
    finally:
        k.close()

    status("after apply")
    alive = window("after write+apply+release", 6)
    status("after apply, +6s")
    if alive:
        log("RESULT: keyboard kept working — no read-back after apply avoids the freeze")
        stop = True
        return

    candidates = [
        ("07 20 00 00 00 00 00 00", "release again"),
        ("07 18 01 00 00 00 00 00", "memory session end"),
        ("07 18 11 00 00 00 00 00", "apply/reload again"),
        ("07 18 00 00 00 00 00 00", "apply second half"),
        ("07 09 c0 00 00 00 00 00", "restore lock/mode flags as stored on device (0xC0)"),
    ]
    for raw, why in candidates:
        send(raw, why)
        time.sleep(0.2)
        if window(f"after '{why}'", 5):
            log(f"RESULT: recovered by {raw} ({why})")
            stop = True
            return
    log("trying USB port reset (software unplug/replug)")
    try:
        node = k7.usb_reset()
        log(f"    keyboard re-enumerated as {node}")
        time.sleep(0.5)
        # listener fds point at the old nodes; restart it on the new ones
        threading.Thread(target=listener, args=(keyboard_nodes(),), daemon=True).start()
        if window("after USB reset", 6):
            status("after USB reset")
            log("RESULT: recovered by USB reset")
            stop = True
            return
    except Exception as e:  # noqa: BLE001
        log(f"    USB reset not possible: {e}")
    log("RESULT: nothing recovered it — please unplug/replug the keyboard")
    stop = True


if __name__ == "__main__":
    try:
        main()
    finally:
        stop = True
        LOG.close()
