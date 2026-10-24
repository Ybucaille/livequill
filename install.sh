#!/bin/sh
# LiveQuill installer for Linux and macOS. No root, no git, no pip: one file in your home directory.
#
#   curl -fsSL https://ybucaille.github.io/livequill/install.sh | sh
#   curl -fsSL https://ybucaille.github.io/livequill/install.sh | sh -s -- --uninstall
#
# It downloads livequill.pyz from the latest GitHub release, checks its SHA-256, stores it in
# ~/.local/share/livequill and creates the `livequill` command in ~/.local/bin.
#
# Environment overrides:
#   LIVEQUILL_VERSION   release tag to install, e.g. v0.2.0 (default: latest)
#   LIVEQUILL_BIN_DIR   where the command goes (default: ~/.local/bin)
#   LIVEQUILL_HOME      where the program goes (default: ${XDG_DATA_HOME:-~/.local/share}/livequill)
#   LIVEQUILL_BASE_URL  download location of livequill.pyz and livequill.pyz.sha256 (mirrors, tests)
set -eu

REPO=https://github.com/Ybucaille/livequill
BIN_DIR=${LIVEQUILL_BIN_DIR:-$HOME/.local/bin}
APP_DIR=${LIVEQUILL_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/livequill}
if [ -n "${LIVEQUILL_BASE_URL:-}" ]; then
  BASE=$LIVEQUILL_BASE_URL
elif [ -n "${LIVEQUILL_VERSION:-}" ]; then
  BASE=$REPO/releases/download/$LIVEQUILL_VERSION
else
  BASE=$REPO/releases/latest/download
fi

say() { printf '%s\n' "$*"; }
die() { printf 'livequill: %s\n' "$*" >&2; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
  rm -f "$BIN_DIR/livequill"
  rm -rf "$APP_DIR"
  say "LiveQuill removed. Your settings and site list stay in ~/.config/livequill (delete it to forget them)."
  exit 0
fi

case "$(uname -s)" in
  Linux | Darwin) ;;
  *) die "only Linux and macOS are supported" ;;
esac

# Same search order as the generated command, so both agree on the interpreter.
PYTHONS="python3.14 python3.13 python3.12 python3.11 python3.10 python3"
find_python() {
  for p in $PYTHONS; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      command -v "$p"
      return 0
    fi
  done
  return 1
}

python_hint() {
  if [ "$(uname -s)" = Darwin ]; then
    say "  brew install python      (or the installer from https://www.python.org/downloads/)"
    return
  fi
  ids=
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    ids=$(. /etc/os-release && printf '%s %s' "${ID:-}" "${ID_LIKE:-}")
  fi
  case " $ids " in
    *" opensuse"* | *" suse "* | *" sles "*) say "  sudo zypper install python311" ;;
    *" rhel "* | *" centos "* | *" rocky "* | *" almalinux "*) say "  sudo dnf install python3.12" ;;
    *" debian "* | *" ubuntu "*) say "  sudo apt install python3" ;;
    *" fedora "*) say "  sudo dnf install python3" ;;
    *" arch "*) say "  sudo pacman -S python" ;;
    *" alpine "*) say "  sudo apk add python3" ;;
    *" void "*) say "  sudo xbps-install python3" ;;
    *" gentoo "*) say "  sudo emerge dev-lang/python" ;;
    *" nixos "*) say "  nix-env -iA nixpkgs.python3   (or add python3 to your configuration)" ;;
    *) say "  install Python 3.10 or newer with your package manager" ;;
  esac
}

PY=$(find_python) || {
  say "LiveQuill needs Python 3.10 or newer, which was not found. Install it, then run this script again:" >&2
  python_hint >&2
  exit 1
}

fetch() { # url dest
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --retry 3 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$2" "$1"
  else
    "$PY" -c 'import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])' "$1" "$2"
  fi
}

TMP=$(mktemp -d "${TMPDIR:-/tmp}/livequill.XXXXXX")
trap 'rm -rf "$TMP"' EXIT INT TERM

say "Downloading LiveQuill from $BASE"
fetch "$BASE/livequill.pyz" "$TMP/livequill.pyz" || die "download failed: $BASE/livequill.pyz"
fetch "$BASE/livequill.pyz.sha256" "$TMP/livequill.pyz.sha256" || die "download failed: $BASE/livequill.pyz.sha256"
"$PY" - "$TMP/livequill.pyz" "$TMP/livequill.pyz.sha256" <<'EOF' || die "checksum mismatch: the download is corrupted or was tampered with"
import hashlib, sys
with open(sys.argv[1], 'rb') as f:
    got = hashlib.sha256(f.read()).hexdigest()
with open(sys.argv[2]) as f:
    want = f.read().split()[0].lower()
sys.exit(got != want)
EOF

mkdir -p "$APP_DIR" "$BIN_DIR"
mv -f "$TMP/livequill.pyz" "$APP_DIR/livequill.pyz"
cat >"$TMP/livequill" <<EOF
#!/bin/sh
# LiveQuill command, created by install.sh. Upgrade: run install.sh again.
for p in $PYTHONS; do
  if command -v "\$p" >/dev/null 2>&1 && "\$p" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "\$p" "$APP_DIR/livequill.pyz" "\$@"
  fi
done
echo "livequill: Python 3.10 or newer is required" >&2
exit 1
EOF
chmod 755 "$TMP/livequill"
mv -f "$TMP/livequill" "$BIN_DIR/livequill"

"$BIN_DIR/livequill" --version >/dev/null || die "the installed command does not start"
say "Installed $("$BIN_DIR/livequill" --version) (Python: $PY)"
case ":$PATH:" in
  *":$BIN_DIR:"*) say "Run: livequill" ;;
  *)
    say "Run: $BIN_DIR/livequill"
    say "To type just \`livequill\`, add $BIN_DIR to your PATH, for example:"
    case "${SHELL:-}" in
      */zsh) say "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.zshrc" ;;
      */fish) say "  fish_add_path $BIN_DIR" ;;
      *) say "  echo 'export PATH=\"$BIN_DIR:\$PATH\"' >> ~/.bashrc" ;;
    esac
    ;;
esac
