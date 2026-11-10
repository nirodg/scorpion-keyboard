# Scorpion K7 (Genius) — Linux notes

Source analysed: `Scorpion K7.[20260714](1).exe` (Inno Setup 5.5.7, sha256 868e2616…fc100e)
→ extracted to `extracted/installed/app/` (`Gaming Keyboard.exe`, sha256 48333a9c…22e6, PE32 x86 MFC app).

## Driver situation
- The installer ships **no kernel driver** (.sys/.inf). Only a userland app + INI/skin files.
- The app uses the stock Windows HID stack only: `HidD_SetFeature`, `HidD_GetFeature`,
  `HidD_GetAttributes`, `HidP_GetCaps`, `SetupDi*`, `CreateFileW`.
- Linux equivalent: the in-kernel `usbhid` driver (already bound) + `/dev/hidraw*`
  with `HIDIOCSFEATURE` / `HIDIOCGFEATURE` ioctls. Typing works without anything extra.

## Device identification (observed on this machine + in the exe)
- USB `0458:6013` "INSTANT USB Keyboard" (KYE/Genius). Exe also accepts `2130:30FA`.
- Interface 0 → `hidraw2`: boot keyboard.
- Interface 1 → `hidraw3`: consumer (ID1), system (ID2), keyboard/NKRO (ID3,4,5,8), mouse (ID9), plus vendor:
  - **Report ID 6**: Input, usage page `0xFF00` usage 1, 2 bytes — app opens it read-only.
  - **Report ID 7**: Feature, usage page `0xFF01` usage 0x20, 7 data bytes (8 with ID) — **config channel**.
- The exe selects collections by usage page (`0xFF00` read handle, `0xFF01` write handle), see 0x407c97–0x407d86.

## Transport (Feature report 7, 8 bytes)
```
[0]=0x07 report id
[1]=opcode   0x18 = memory/config, 0x09 = mode bits, 0x20 = ?, 0x41/0x81 sub-ops seen
[2]=cmd      (for 0x18) bit-packed flags; seen 0x01,0x03,0x05,0x09,0x11,0x81
[3]=index
[4]=addr lo
[5]=addr hi
[6]=data
[7]=len-1  (or len+0x5F when global flag 0x66a7a8 is set — model variant)
```
Block write `0x407270(addr, buf, n)`:
1. for i in 0..n-1: `07 18 03 i aLo aHi buf[i] L`
2. sleep 2 ms; `07 18 09 00 aLo aHi 00 L`   (commit)
3. sleep 10 ms; `07 18 01 00 aLo aHi 00 00` (end)

Block read `0x407070(addr, buf, n)`: `07 18 03 …` setup, then per byte
`07 18 05 i aLo aHi 00 L` → sleep 1 → GetFeature(7) → data = report[1]; end with `07 18 01 00 00 00 00 00`.

Generic command helper `0x4135f0(a1..a6, addr, idx, last, data)`: cmd = a1<<5|a2<<4|a4<<3|a3<<2|a5<<1|a6.

Init (0x4107a0): `07 09 41 00 00 00 00 00`, sleep 200 ms; optional erase loop `07 18 81 00 aLo aHi 00 60`
for addr 0..0x3FF step 0x20; then GetFeature; then writes 8 bytes to addr `0xD0` (`E1 1E vLo vHi F0 0F 00 00`).

## Opcode 0x09 — lock / mode flags (0x4293f0)
`07 09 FF 00 00 00 00 00` where FF = KEYSMODE<<7 | BALTF4<<6 | ?(0x66a7e0)<<4 | BWINLOCK<<2 | BALLKEYSLOCK.
Init sends `07 09 41 …` (Alt+F4 lock + all-keys lock) before programming.

## INI key → global (loader 0x41c6d1–0x41d7ec)
KEYSMODE 0x649104, BALTF4 0x66a7d8, BWINLOCK 0x66a7e8, BALLKEYSLOCK 0x66a7f0, BMODELED 0x66a7f4,
SINGLECOL 0x673ed4, HR_DIRECTION 0x673ec8, HR_BRIGHT 0x673ecc, HR_SPEED 0x673ed0, HR_SYMMETRY 0x673ec4,
HR_COLOR_SEL 0x66a740, RGB_HR_COLOR_EN0..6 0x673f9c.., HR_LED_MODE 0x673ff8, HR_LED_MODE_EN0..8 0x673fc4..,
HR_REACT_TYPE 0x673fb8. Variant flag 0x66a7a8 is never written (=0) → byte7 = len-1, no erase loop.

## Profile image (writer 0x410d9a–0x411560, records of 8 bytes at base B)
B = bytes 14..15 of a 16-byte GetFeature(7) at init (0x410893) — unresolved for 8-byte reports.
- B+0x00 rec0: byte0 = flags (bit7 KEYSMODE, 6 ALTF4, 5/4 preserved from device, 3 ?, 2 WINLOCK, 1 ?, 0 ALLKEYSLOCK); bytes1..7 = nibble pairs
- B+0x08 rec1, B+0x10 rec2: per-mode LED parameter records (same packing as LED record)
- B+0x18 LED record:
  - byte0 = dir<<6 | (bright&7)<<3 | (speed&7)
  - byte1 = symmetry<<7 | (colorSel&7)<<4 | (mode&0xF)
  - byte2 = 0x80 | colour-enable mask (EN0 = bit0)
  - byte3 = mode-enable mask (EN0 = bit0)
  - byte4..5 = B+0x48 (colour table ptr), byte6..7 = B+0xF8 (second table ptr)
- B+0x20 1 byte from word 0x66a130: (lo<<4)|(hi&0xF)
- Header at absolute 0xD0: `E1 1E <B lo> <B hi> F0 0F 00 00`

## Confirmed on device (2026-11-10)
- Header @0xD0 = `e1 1e e0 00 f0 0f 00 00` → base B = 0x00E0. Full 4 KB backup: `dumps/backup_full_0000_1000.bin`.
- LED record @0xF8 read `51 05 7f ff 28 01 d8 01`; wrote brightness 2→4 (`61 …`), read-back OK, backlight got brighter.
- Key map @0x128 (col-major 20×8) = KBconfig.ini values; Fn table @0x1C8; macro slot table @0x1D8.
- Copilot macro @0x250: `01 00 88 e3 a8 e1 93 72 01 e1 01 e3 01 72 00 00` = ↓LWin ↓LShift ↓F23 ↑LShift ↑LWin ↑F23.
  On X11, F23 = keycode 201 = XF86TouchpadOff → GNOME accel `<Shift><Super>XF86TouchpadOff`.

## Session end — `07 20 00 00 00 00 00 00`
After the first write (no 07 20 sent) the keyboard stopped typing until replugged; no USB reset in the
kernel log. The vendor app sends `07 20` only on exit (0x412dc0, 0x414fc1, 0x415ba6), so the tool now
ends every session with it.

## Freeze — root cause (diag_freeze.py, 2026-11-10 13:44)
Any 07 18 memory access suspends key scanning (no input reports on hidraw2) until apply
`07 18 11 …` + `07 18 00 …` is sent; then typing resumes immediately. The earlier freezes came from a
read-back *after* apply, which re-suspended it. The vendor app always ends memory access with apply.
Rule in the library: a session that touched memory ends with apply, then 07 20.
Status reply byte 1 is NOT a freeze flag (was 01 while typing worked).

## Tool
`scorpion_k7.py` — `find`, `raw-get`, `dump ADDR LEN` (read-only sequence copied from 0x407070).

## Unknown / not yet mapped
- Address map for LED mode/colour/brightness/speed, key remap table, macros.
- Meaning of opcodes 0x09 (bits from 5 mode globals) and 0x20.
- These are best recovered by capturing real traffic (Wine + hidraw, or usbmon) while changing settings.

## Macros (v0.2)
Memory is **2 KB** (0x800–0xFFF mirrors 0x000–0x7FF); 0x7C0–0x7FF holds the identity string
`B009 KAI LE K783025B -AI-FN`. Macro data lives in 0x240–0x3F8.

**Slot table** @0x1D8 (B+0xF8): 20 entries × 5 bytes
`07, (count&0x7F) | hold<<7, addr_lo, toggle<<7 | (addr_hi&0x3F), count>>7`
— count = events + long-delay padding pairs; hold = repeat while held (rapid fire),
toggle = repeat until pressed again; neither = play N times.

**Macro data**: 2-byte header (repeat count u16 LE, or `01 00` for hold/toggle), then per event
`b0, code` with b0 = (0x80 if press) | delay (ms>>3, 1..127; longer delays add `7f 00` pairs),
terminated by `00 00`, padded to 8 bytes. Codes: HID usages for keys; mouse buttons
**0xF1 left, 0xF3 right, 0xF2 middle** (device-verified); `0xF6` mouse move + dx,dy (i16 LE);
`0xF7` wheel + amount; `0xFA` consumer/media + 16-bit usage.

**Key → macro**: a key-map cell holding `0xA5+slot` (slot 0–10) or `0xAD+slot` (slot 11–19)
plays that slot. The Copilot key is cell (col 9, row 7) = `0xA6` → slot 1.

Verified: encoding the official Copilot macro reproduces the factory bytes exactly.
