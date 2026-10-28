
#!/usr/bin/env bash
set -Eeuo pipefail

API="https://api.github.com/repos/Genymobile/scrcpy/releases/latest"
INSTALL_BIN="/usr/local/bin/scrcpy"
SERVER_DIR="/usr/local/share/scrcpy"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "========================================"
echo " scrcpy verzióellenőrzés és frissítés"
echo "========================================"
echo

for cmd in curl python3 tar sha256sum sudo; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        echo "[HIBA] Hiányzó szükséges parancs: $cmd"
        exit 1
    fi
done

# Ez a hivatalos előre fordított csomag x86-64 architektúrához való.
ARCH="$(uname -m)"
if [[ "$ARCH" != "x86_64" ]]; then
    echo "[HIBA] Ez a telepítő x86_64 architektúrához készült."
    echo "Észlelt architektúra: $ARCH"
    exit 1
fi

# A jelenleg használt program felderítése.
CURRENT_BIN="$(command -v scrcpy || true)"
LOCAL_VERSION="nincs telepítve"

if [[ -n "$CURRENT_BIN" ]]; then
    LOCAL_OUTPUT="$("$CURRENT_BIN" --version 2>&1 | head -n 1 || true)"
    if [[ "$LOCAL_OUTPUT" =~ ([0-9]+\.[0-9]+(\.[0-9]+)?) ]]; then
        LOCAL_VERSION="${BASH_REMATCH[1]}"
    fi
fi

echo "Helyi program: ${CURRENT_BIN:-nem található}"
echo "Helyi verzió:  $LOCAL_VERSION"
echo
echo "A hivatalos GitHub-kiadás lekérdezése..."

if ! curl -fsSL --connect-timeout 10 --max-time 40 \
    -H 'Accept: application/vnd.github+json' \
    "$API" -o "$TMP/release.json"; then
    echo "[HIBA] Nem sikerült lekérni a GitHub-kiadást."
    exit 1
fi

# A hivatalos kiadásból kiválasztjuk a megfelelő archívumot és annak
# GitHub által közölt SHA-256 ellenőrzőösszegét.
python3 - "$TMP/release.json" "$TMP/release.env" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    release = json.load(f)

if release.get("draft") or release.get("prerelease"):
    raise SystemExit("A GitHub nem stabil kiadást adott vissza.")

tag = release.get("tag_name", "")
version = tag.removeprefix("v")

if not re.fullmatch(r"\d+(?:\.\d+)+", version):
    raise SystemExit(f"Érvénytelen verziószám: {tag!r}")

wanted = f"scrcpy-linux-x86_64-v{version}.tar.gz"
asset = next(
    (a for a in release.get("assets", []) if a.get("name") == wanted),
    None,
)

if asset is None:
    raise SystemExit(f"A hivatalos kiadásban nem található: {wanted}")

digest = asset.get("digest") or ""
if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
    raise SystemExit(
        "A GitHub nem adott ellenőrizhető SHA-256 értéket ehhez az archívumhoz. "
        "Biztonsági okból a telepítés leáll."
    )

url = asset.get("browser_download_url", "")
if not url.startswith("https://github.com/Genymobile/scrcpy/releases/download/"):
    raise SystemExit("Váratlan letöltési URL; a telepítés leáll.")

with open(sys.argv[2], "w", encoding="utf-8") as f:
    f.write(f"VERSION={version}\n")
    f.write(f"ASSET={wanted}\n")
    f.write(f"URL={url}\n")
    f.write(f"SHA256={digest.split(':', 1)[1].lower()}\n")
PY

# A fájl a GitHub hivatalos kiadásából származó adatokat tartalmazza.
# shellcheck disable=SC1090
source "$TMP/release.env"

echo "Legújabb stabil verzió: $VERSION"
echo

if [[ "$LOCAL_VERSION" == "$VERSION" ]]; then
    echo "[NAPRAKÉSZ] Már a legújabb verzió van telepítve."
    exit 0
fi

if [[ "$LOCAL_VERSION" != "nincs telepítve" ]]; then
    # Numerikus összehasonlítás: az 5.0 és az 5.0.0 azonosnak számít.
    if python3 - "$LOCAL_VERSION" "$VERSION" <<'PY'
import sys

def parts(s):
    return tuple(map(int, s.split(".")))

a, b = parts(sys.argv[1]), parts(sys.argv[2])
n = max(len(a), len(b))
a += (0,) * (n - len(a))
b += (0,) * (n - len(b))
sys.exit(0 if a > b else 1)
PY
    then
        echo "[FIGYELEM] A helyi verzió újabb a legfrissebb stabil kiadásnál."
        echo "Automatikus visszaminősítés helyett leállok."
        exit 0
    fi
fi

echo "Letöltés: $ASSET"
if ! curl -fL --retry 2 --connect-timeout 10 --max-time 300 \
    "$URL" -o "$TMP/$ASSET"; then
    echo "[HIBA] A letöltés sikertelen."
    exit 1
fi

echo "SHA-256 ellenőrzés..."
ACTUAL_SHA="$(sha256sum "$TMP/$ASSET" | awk '{print $1}')"
if [[ "$ACTUAL_SHA" != "$SHA256" ]]; then
    echo "[HIBA] Az ellenőrzőösszeg nem egyezik."
    echo "Elvárt: $SHA256"
    echo "Kapott: $ACTUAL_SHA"
    exit 1
fi
echo "[OK] A letöltés ellenőrzése sikeres."

mkdir "$TMP/extracted"
tar -xzf "$TMP/$ASSET" -C "$TMP/extracted"

# Megkeressük a binárist és a hozzá tartozó szerverfájlt.
NEW_BIN="$(find "$TMP/extracted" -type f -name scrcpy -perm /111 -print -quit)"
NEW_SERVER="$(find "$TMP/extracted" -type f -name scrcpy-server -print -quit)"

if [[ -z "$NEW_BIN" || -z "$NEW_SERVER" ]]; then
    echo "[HIBA] Az archívumban nem található a scrcpy és a scrcpy-server."
    echo "A meglévő telepítés változatlan maradt."
    exit 1
fi

# Telepítés előtt ellenőrizzük, hogy a letöltött bináris elindul-e.
NEW_OUTPUT="$("$NEW_BIN" --version 2>&1 | head -n 1 || true)"
if [[ "$NEW_OUTPUT" != *"$VERSION"* ]]; then
    echo "[HIBA] A letöltött bináris verziója nem egyezik a kiadással:"
    echo "$NEW_OUTPUT"
    exit 1
fi

echo
echo "Telepítendő verzió: $VERSION"
echo "A meglévő fájlokról biztonsági mentés készül."
read -r -p "Folytatod a telepítést? [i/N] " ANSWER
if [[ ! "$ANSWER" =~ ^[iIyY]$ ]]; then
    echo "Telepítés megszakítva."
    exit 0
fi

BACKUP="/usr/local/share/scrcpy-backup-$(date +%Y%m%d-%H%M%S)"
echo
echo "Rendszergazdai jogosultság szükséges; add meg a jelszavad, ha kéri."

sudo mkdir -p "$SERVER_DIR"

# A régi bináris és szerver mentése, ha léteznek.
if [[ -e "$INSTALL_BIN" || -e "$SERVER_DIR/scrcpy-server" ]]; then
    sudo mkdir -p "$BACKUP"
    if [[ -e "$INSTALL_BIN" ]]; then
        sudo cp -a "$INSTALL_BIN" "$BACKUP/scrcpy"
    fi
    if [[ -e "$SERVER_DIR/scrcpy-server" ]]; then
        sudo cp -a "$SERVER_DIR/scrcpy-server" "$BACKUP/scrcpy-server"
    fi
    echo "Biztonsági mentés: $BACKUP"
fi

# Előbb ideiglenes fájlok települnek, majd átnevezéssel kerülnek a helyükre.
sudo install -m 0755 "$NEW_BIN" "$INSTALL_BIN.new"
sudo install -m 0644 "$NEW_SERVER" "$SERVER_DIR/scrcpy-server.new"
sudo mv -f "$INSTALL_BIN.new" "$INSTALL_BIN"
sudo mv -f "$SERVER_DIR/scrcpy-server.new" "$SERVER_DIR/scrcpy-server"

echo
echo "========================================"
echo " Telepítés befejezve"
echo "========================================"

"$INSTALL_BIN" --version
echo
echo "Új bináris: $INSTALL_BIN"
echo "Új szerver: $SERVER_DIR/scrcpy-server"
echo
echo "A telefon csatlakoztatásával ellenőrizd a működést."
