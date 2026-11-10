#!/bin/sh
# Build dist/scorpion-k7_<version>_all.deb — attach it to a GitHub release.
# Usage: packaging/build-deb.sh
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/scorpion_k7_gui.py")
PKG=scorpion-k7
STAGE="$ROOT/build/${PKG}_${VERSION}_all"
OUT="$ROOT/dist/${PKG}_${VERSION}_all.deb"

[ -n "$VERSION" ] || { echo "VERSION not found in scorpion_k7_gui.py" >&2; exit 1; }
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/$PKG" \
         "$STAGE/usr/share/applications" "$STAGE/usr/lib/udev/rules.d" \
         "$STAGE/usr/share/doc/$PKG" "$ROOT/dist"

# Application
install -m 644 "$ROOT/scorpion_k7.py" "$ROOT/scorpion_k7_gui.py" "$ROOT/tray.py" "$STAGE/usr/share/$PKG/"
install -m 644 "$ROOT/70-scorpion-k7.rules" "$STAGE/usr/share/$PKG/"
cat > "$STAGE/usr/bin/scorpion-k7" <<'EOF'
#!/bin/sh
exec python3 /usr/share/scorpion-k7/scorpion_k7_gui.py "$@"
EOF
cat > "$STAGE/usr/bin/scorpion-k7-cli" <<'EOF'
#!/bin/sh
exec python3 /usr/share/scorpion-k7/scorpion_k7.py "$@"
EOF
chmod 755 "$STAGE/usr/bin/scorpion-k7" "$STAGE/usr/bin/scorpion-k7-cli"

# Desktop integration, udev rule, docs
install -m 644 "$ROOT/packaging/io.github.scorpion_k7.desktop" "$STAGE/usr/share/applications/"
install -m 644 "$ROOT/70-scorpion-k7.rules" "$STAGE/usr/lib/udev/rules.d/"
install -m 644 "$ROOT/LICENSE" "$STAGE/usr/share/doc/$PKG/copyright"
install -m 644 "$ROOT/README.md" "$ROOT/PROTOCOL-NOTES.md" "$STAGE/usr/share/doc/$PKG/"

# Package metadata
install -m 755 "$ROOT/packaging/debian/postinst" "$ROOT/packaging/debian/postrm" "$STAGE/DEBIAN/"
SIZE=$(du -sk "$STAGE/usr" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: all
Maintainer: Brage Dorin <https://github.com/nirodg>
Installed-Size: $SIZE
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-4.0 (>= 4.10), gir1.2-adw-1 (>= 1.4), libglib2.0-bin, udev
Section: utils
Priority: optional
Homepage: https://github.com/nirodg/scorpion-keyboard
Description: Linux configurator for the Genius Scorpion K7 keyboard
 Open-source alternative to the official Windows app: change the RGB
 backlight (mode, brightness, speed, direction) and remap the Copilot key
 to a shortcut or a command. Settings are stored on the keyboard.
 Includes a udev rule so the logged-in user can configure it without root.
EOF

find "$STAGE" -type d -exec chmod 755 {} +
dpkg-deb --root-owner-group --build "$STAGE" "$OUT" >/dev/null
echo "$OUT"
