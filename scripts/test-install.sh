#!/bin/sh
# Teste install.sh de bout en bout dans un conteneur Linux propre : Python installé par le gestionnaire de paquets
# de la distribution, téléchargement depuis un serveur local (livequill.pyz + .sha256 construits ici), puis
# lancement réel du serveur LiveQuill.
#
#   scripts/test-install.sh IMAGE 'COMMANDE QUI INSTALLE PYTHON'
#   scripts/test-install.sh IMAGE --no-python 'TEXTE ATTENDU DANS LE CONSEIL'   # sans Python : refus clair attendu
#   scripts/test-install.sh --host                                               # sans conteneur (macOS)
set -eu

repo=$(cd "$(dirname "$0")/.." && pwd)
dl=$(mktemp -d)
python3 "$repo/scripts/build-pyz.py" --out "$dl/livequill.pyz" >/dev/null
(cd "$dl" && python3 -c 'import hashlib; print(hashlib.sha256(open("livequill.pyz", "rb").read()).hexdigest() + "  livequill.pyz")' >livequill.pyz.sha256)
port=$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
python3 -m http.server "$port" -b 127.0.0.1 -d "$dl" >/dev/null 2>&1 &
srv=$!
trap 'kill $srv 2>/dev/null; rm -rf "$dl"' EXIT
sleep 1

# shellcheck disable=SC2016  # développé dans le conteneur, pas ici
# Ce qui tourne « chez l'utilisateur » : installation, commande, serveur qui répond avec la barre.
check='
set -eu
sh /tmp/lq-install.sh
"$HOME/.local/bin/livequill" --version
mkdir -p /tmp/lq-site && echo "<h1>hi</h1>" >/tmp/lq-site/index.html
"$HOME/.local/bin/livequill" /tmp/lq-site --no-open --port 18777 --config /tmp/lq-config.json >/tmp/lq.log 2>&1 &
for p in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  command -v $p >/dev/null 2>&1 && $p -c "import sys; sys.exit(sys.version_info < (3, 10))" && break
done
$p - <<EOF
import time, urllib.request
for _ in range(40):
    try:
        html = urllib.request.urlopen("http://127.0.0.1:18777/__setup", timeout=2).read()
        break
    except OSError:
        time.sleep(0.25)
else:
    raise SystemExit("le serveur ne répond pas : " + open("/tmp/lq.log").read())
assert b"lq-token" in html, html[:200]
assert b"livequill" in urllib.request.urlopen("http://127.0.0.1:18777/__lq/overlay.js").read()
print("serveur OK")
EOF
sh /tmp/lq-install.sh --uninstall
test ! -e "$HOME/.local/bin/livequill"
'

if [ "${1:-}" = --host ]; then
  home=$(mktemp -d)
  cp "$repo/install.sh" /tmp/lq-install.sh
  env HOME="$home" LIVEQUILL_BASE_URL="http://127.0.0.1:$port" sh -c "$check"
  rm -rf "$home"
  echo "✓ install.sh OK sur $(uname -s)"
  exit 0
fi

image=$1
if [ "${2:-}" = --no-python ]; then
  out=$(docker run --rm --network host -v "$repo/install.sh:/tmp/lq-install.sh:ro" -e LIVEQUILL_BASE_URL="http://127.0.0.1:$port" \
    "$image" sh /tmp/lq-install.sh 2>&1) && { echo "✗ l'installation aurait dû échouer sans Python"; exit 1; }
  echo "$out"
  case "$out" in *"$3"*) echo "✓ $image : refus clair sans Python" ;; *) echo "✗ conseil attendu : $3"; exit 1 ;; esac
  exit 0
fi
docker run --rm --network host -v "$repo/install.sh:/tmp/lq-install.sh:ro" -e LIVEQUILL_BASE_URL="http://127.0.0.1:$port" \
  "$image" sh -c "$2 >/dev/null && $check"
echo "✓ install.sh OK sur $image"
