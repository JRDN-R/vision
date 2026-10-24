#!/bin/bash
# Build and install the Vision companion using Apple's own tools.
# The script never asks for an Apple password, changes global Xcode selection,
# installs Homebrew, or discards changes in an existing checkout.
set -euo pipefail
umask 077

REPOSITORY_URL="https://github.com/JRDN-R/vision.git"
DEFAULT_REF="codex/vision-ios-companion-20261024"
ORIGINAL_ARGS=("$@")
REQUESTED_TEAM=""
REQUESTED_DEVICE=""
REQUESTED_BUNDLE=""
REQUESTED_SERVER=""
REQUESTED_WEB=""
REQUESTED_REF=""
REQUESTED_PUSH=""
INSTALL_DIRECTORY="${HOME}/Developer/Vision-iOS"
DO_UPDATE=0
GENERATE_ONLY=0
SIMULATOR_CHECK=0
SIMULATOR_TEST=0

usage() {
  cat <<'USAGE'
Vision iOS setup

  bash setup-ios.sh [options]

  --team TEAMID          Apple development Team ID (10 characters).
  --device ID            Paired iPhone UDID or devicectl identifier.
  --bundle-id ID         Stable bundle ID; defaults to com.jordanrapp.Vision.<team>.
  --server-url URL       FUPCJ HTTPS server; current Vision server is preconfigured.
  --web-url URL          Hosted Vision web app; current GitHub Pages URL is preconfigured.
  --push                Enable APNs: requires paid Apple Developer Program membership.
  --no-push             Free Personal Team configuration (the initial default).
  --generate-only       Create the Xcode project without building or opening Xcode.
  --simulator-check     Compile all three app targets plus XCTest for Simulator, unsigned.
  --simulator-test      Compile and run XCTest on an available iPhone Simulator, unsigned.
  --directory PATH      New managed checkout destination; default ~/Developer/Vision-iOS.
  --ref REF             Repository branch or exact commit to download.
  --update              Update only a clean checkout created by this installer.
  --help                Show this help.

First run: connect and unlock the iPhone, trust the Mac, add your Apple account in
Xcode Settings > Accounts, and enable Settings > Privacy & Security > Developer Mode
on the iPhone. Apple requires these confirmations; the script cannot supply them.

Rerun the same script to rebuild/reinstall after the free 7-day profile expires.
Keep the same team and bundle ID, and do not delete the app to preserve its data.
USAGE
}

die() { printf '\n%s\n' "$*" >&2; exit 1; }
need_value() { [ "$#" -ge 2 ] && [ -n "$2" ] || die "Missing value for $1"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --team) need_value "$@"; REQUESTED_TEAM="$2"; shift 2 ;;
    --device) need_value "$@"; REQUESTED_DEVICE="$2"; shift 2 ;;
    --bundle-id) need_value "$@"; REQUESTED_BUNDLE="$2"; shift 2 ;;
    --server-url) need_value "$@"; REQUESTED_SERVER="$2"; shift 2 ;;
    --web-url) need_value "$@"; REQUESTED_WEB="$2"; shift 2 ;;
    --directory) need_value "$@"; INSTALL_DIRECTORY="$2"; shift 2 ;;
    --ref) need_value "$@"; REQUESTED_REF="$2"; shift 2 ;;
    --push) REQUESTED_PUSH=1; shift ;;
    --no-push) REQUESTED_PUSH=0; shift ;;
    --update) DO_UPDATE=1; shift ;;
    --generate-only) GENERATE_ONLY=1; shift ;;
    --simulator-check) SIMULATOR_CHECK=1; shift ;;
    --simulator-test) SIMULATOR_CHECK=1; SIMULATOR_TEST=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1. Run with --help for options." ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || die "Run this script in Terminal on your Mac with Xcode installed."

# Respect an explicitly selected full Xcode. Otherwise locate Xcode without
# changing the user's system-wide xcode-select configuration.
SELECTED_DEVELOPER="${DEVELOPER_DIR:-}"
if [ -z "$SELECTED_DEVELOPER" ]; then
  SELECTED_DEVELOPER="$(/usr/bin/xcode-select -p 2>/dev/null || true)"
fi
if [ ! -x "$SELECTED_DEVELOPER/usr/bin/xcodebuild" ]; then
  SELECTED_DEVELOPER="/Applications/Xcode.app/Contents/Developer"
fi
if [ ! -x "$SELECTED_DEVELOPER/usr/bin/xcodebuild" ]; then
  FOUND_XCODE="$(/usr/bin/mdfind 'kMDItemCFBundleIdentifier == "com.apple.dt.Xcode"' 2>/dev/null | head -n 1 || true)"
  if [ -n "$FOUND_XCODE" ]; then SELECTED_DEVELOPER="$FOUND_XCODE/Contents/Developer"; fi
fi
[ -x "$SELECTED_DEVELOPER/usr/bin/xcodebuild" ] || die "Full Xcode was not found. Open Xcode once and finish its setup, then rerun. Command Line Tools alone cannot build iOS apps."
export DEVELOPER_DIR="$SELECTED_DEVELOPER"
XCODEBUILD="$DEVELOPER_DIR/usr/bin/xcodebuild"
if ! "$XCODEBUILD" -version >/dev/null 2>&1; then
  die "Open Xcode, accept Apple's license and complete first-launch setup, then rerun this script."
fi
if ! "$XCODEBUILD" -checkFirstLaunchStatus >/dev/null 2>&1; then
  die "Xcode still needs its first-launch components. Open Xcode and finish the installation, then rerun."
fi
if ! xcrun --sdk iphoneos --show-sdk-path >/dev/null 2>&1; then
  die "Install iOS platform support in Xcode Settings > Components, then rerun."
fi
PYTHON="$(xcrun --find python3 2>/dev/null || command -v python3 || true)"
[ -n "$PYTHON" ] || die "Python 3 was not found. Finish installing Xcode's Command Line Tools, then rerun."
"$PYTHON" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9 or newer is required"' || die "Xcode's Python must be version 3.9 or newer."
SCRIPT_DIRECTORY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

valid_ref() {
  case "$1" in ""|-*|*$'\n'*|*$'\r'*) return 1 ;; esac
  git check-ref-format --branch "$1" >/dev/null 2>&1 || [[ "$1" =~ ^[a-fA-F0-9]{40}$ ]]
}

if [ -f "$SCRIPT_DIRECTORY/generate_project.py" ] && [ -d "$SCRIPT_DIRECTORY/App" ]; then
  REPO_ROOT="$(cd "$SCRIPT_DIRECTORY/.." && pwd -P)"
else
  REF="${REQUESTED_REF:-$DEFAULT_REF}"
  valid_ref "$REF" || die "Invalid repository branch or commit: $REF"
  case "$INSTALL_DIRECTORY" in /*) ;; *) INSTALL_DIRECTORY="$PWD/$INSTALL_DIRECTORY" ;; esac
  if [ -e "$INSTALL_DIRECTORY" ]; then
    [ -f "$INSTALL_DIRECTORY/.git/vision-ios-managed.json" ] || die "The destination already exists and was not created by this installer: $INSTALL_DIRECTORY. Choose an empty --directory or run ios/setup-ios.sh from your existing checkout."
    [ -f "$INSTALL_DIRECTORY/ios/setup-ios.sh" ] || die "The existing managed checkout is incomplete. Choose a new --directory; no files were removed."
  else
    mkdir -p "$(dirname "$INSTALL_DIRECTORY")"
    printf 'Downloading Vision into %s\n' "$INSTALL_DIRECTORY"
    git clone --filter=blob:none --no-checkout "$REPOSITORY_URL" "$INSTALL_DIRECTORY"
    git -C "$INSTALL_DIRECTORY" fetch --depth 1 origin "$REF"
    git -C "$INSTALL_DIRECTORY" checkout --detach FETCH_HEAD
    "$PYTHON" - "$INSTALL_DIRECTORY/.git/vision-ios-managed.json" "$REF" "$REPOSITORY_URL" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"ref": sys.argv[2], "repository": sys.argv[3]}, indent=2) + "\n")
PY
    [ -f "$INSTALL_DIRECTORY/ios/setup-ios.sh" ] || die "The selected revision does not contain the iOS companion. Use the companion branch or its merged revision with --ref."
  fi
  exec /bin/bash "$INSTALL_DIRECTORY/ios/setup-ios.sh" "${ORIGINAL_ARGS[@]}"
fi

MARKER="$REPO_ROOT/.git/vision-ios-managed.json"
if [ "$DO_UPDATE" -eq 1 ] && [ "${VISION_IOS_UPDATED_THIS_RUN:-0}" != 1 ]; then
  [ -f "$MARKER" ] || die "Automatic --update only changes a checkout created by this installer. Update this existing repository using your normal Git workflow, then rerun without --update."
  [ "$(git -C "$REPO_ROOT" remote get-url origin)" = "$REPOSITORY_URL" ] || die "The managed checkout's origin changed. Update it manually; no files were modified."
  [ -z "$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)" ] || die "This checkout has local changes. Commit or move those changes before --update. The installer will not discard them."
  SAVED_REF="$("$PYTHON" - "$MARKER" <<'PY'
import json, sys
print(json.load(open(sys.argv[1])).get("ref", ""))
PY
)"
  REF="${REQUESTED_REF:-${SAVED_REF:-$DEFAULT_REF}}"
  valid_ref "$REF" || die "Invalid repository branch or commit: $REF"
  git -C "$REPO_ROOT" fetch --depth 1 origin "$REF"
  git -C "$REPO_ROOT" checkout --detach FETCH_HEAD
  "$PYTHON" - "$MARKER" "$REF" "$REPOSITORY_URL" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"ref": sys.argv[2], "repository": sys.argv[3]}, indent=2) + "\n")
PY
  export VISION_IOS_UPDATED_THIS_RUN=1
  exec /bin/bash "$REPO_ROOT/ios/setup-ios.sh" "${ORIGINAL_ARGS[@]}"
fi

LOCAL="$REPO_ROOT/ios/Local"
mkdir -p "$LOCAL"
SETTINGS="$LOCAL/installer-settings.json"
saved_value() {
  "$PYTHON" - "$SETTINGS" "$1" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
value = json.loads(p.read_text()).get(sys.argv[2], "") if p.exists() else ""
print(str(value) if value is not None else "")
PY
}
SAVED_TEAM="$(saved_value team)"
TEAM="${REQUESTED_TEAM:-$SAVED_TEAM}"
PUSH="${REQUESTED_PUSH:-$(saved_value push)}"
PUSH="${PUSH:-0}"
SERVER="${REQUESTED_SERVER:-$(saved_value server)}"
WEB="${REQUESTED_WEB:-$(saved_value web)}"
BUNDLE="${REQUESTED_BUNDLE:-$(saved_value bundle)}"

if [ -z "$TEAM" ] && [ "$SIMULATOR_CHECK" -eq 0 ]; then
  # Extract the actual certificate OU (Team ID), not the user's certificate CN.
  "$PYTHON" - > "$LOCAL/discovered-teams.txt" <<'PY'
import re, subprocess
identities = subprocess.run(["security", "find-identity", "-v", "-p", "codesigning"], capture_output=True, text=True).stdout.upper()
pem = subprocess.run(["security", "find-certificate", "-a", "-c", "Apple Development", "-p"], capture_output=True, text=True).stdout
teams = set()
for certificate in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S):
    result = subprocess.run(["/usr/bin/openssl", "x509", "-noout", "-subject", "-fingerprint", "-sha1", "-nameopt", "RFC2253"], input=certificate, capture_output=True, text=True)
    team = re.search(r"(?:^|,)OU=([A-Z0-9]{10})(?:,|$)", result.stdout, re.M)
    fingerprint = re.search(r"Fingerprint=([A-Fa-f0-9:]+)", result.stdout)
    if team and fingerprint and fingerprint.group(1).replace(":", "").upper() in identities:
        teams.add(team.group(1))
print("\n".join(sorted(teams)))
PY
  TEAM_COUNT="$(awk 'NF {n++} END {print n+0}' "$LOCAL/discovered-teams.txt")"
  if [ "$TEAM_COUNT" -eq 1 ]; then TEAM="$(awk 'NF {print; exit}' "$LOCAL/discovered-teams.txt")"; fi
fi
if [ -n "$REQUESTED_TEAM" ] && [ -n "$SAVED_TEAM" ] && [ "$REQUESTED_TEAM" != "$SAVED_TEAM" ] && [ -z "$REQUESTED_BUNDLE" ]; then
  # A new team cannot reuse the previous team's registered App Group.
  BUNDLE=""
fi
if [ -n "$TEAM" ] && [ -z "$SAVED_TEAM" ] && [ "$BUNDLE" = "com.jordanrapp.Vision" ] && [ -z "$REQUESTED_BUNDLE" ]; then
  # Replace the unsigned first-run placeholder with this team's unique ID.
  BUNDLE=""
fi
GENERATOR_ARGS=(--self-check)
[ -z "$TEAM" ] || GENERATOR_ARGS+=(--team "$TEAM")
[ -z "$BUNDLE" ] || GENERATOR_ARGS+=(--bundle-id "$BUNDLE")
[ -z "$SERVER" ] || GENERATOR_ARGS+=(--server-url "$SERVER")
[ -z "$WEB" ] || GENERATOR_ARGS+=(--web-url "$WEB")
if [ "$PUSH" = 1 ]; then GENERATOR_ARGS+=(--push); fi
"$PYTHON" "$REPO_ROOT/ios/generate_project.py" "${GENERATOR_ARGS[@]}"
"$PYTHON" - "$REPO_ROOT/ios/Generated/project-settings.json" "$SETTINGS" <<'PY'
import json, os, pathlib, sys
source = json.loads(pathlib.Path(sys.argv[1]).read_text())
path = pathlib.Path(sys.argv[2])
values = {"team": source["teamID"], "bundle": source["bundleID"], "push": int(source["pushEnabled"]),
          "server": source["serverURL"], "web": source["webURL"]}
temporary = path.with_suffix(".tmp")
temporary.write_text(json.dumps(values, indent=2) + "\n")
os.replace(temporary, path)
PY
BUNDLE="$(saved_value bundle)"
PROJECT="$REPO_ROOT/ios/Vision.xcodeproj"
DERIVED="$LOCAL/DerivedData"

if [ "$GENERATE_ONLY" -eq 1 ]; then
  printf '\nProject ready. Open %s when you want to build it.\n' "$PROJECT"
  exit 0
fi

if [ "$SIMULATOR_CHECK" -eq 1 ]; then
  DESTINATION="generic/platform=iOS Simulator"
  BUILD_ACTION="build-for-testing"
  if [ "$SIMULATOR_TEST" -eq 1 ]; then
    SIMULATOR_ID="$(xcrun simctl list devices available --json | "$PYTHON" -c 'import json,sys; groups=json.load(sys.stdin).get("devices",{}); devices=[d for key,values in groups.items() if "iOS" in key for d in values if d.get("isAvailable") and d.get("name", "").startswith("iPhone")]; devices.sort(key=lambda d: d.get("state") != "Booted"); print(devices[0]["udid"] if devices else "")')"
    [ -n "$SIMULATOR_ID" ] || die "No iPhone Simulator is installed. In Xcode Settings > Components install an iOS Simulator, then rerun --simulator-test."
    DESTINATION="platform=iOS Simulator,id=$SIMULATOR_ID"
    BUILD_ACTION="test"
  fi
  printf '\nChecking the unsigned Simulator build...\n'
  "$XCODEBUILD" -quiet -project "$PROJECT" -scheme Vision -configuration Debug -sdk iphonesimulator \
    -destination "$DESTINATION" -derivedDataPath "$DERIVED" CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO \
    "$BUILD_ACTION" 2>&1 | tee "$LOCAL/simulator-build.log"
  printf '\nSimulator check passed. Physical-device signing, Photos and Share behavior still need an iPhone check.\n'
  exit 0
fi

if [ -z "$TEAM" ]; then
  open "$PROJECT"
  printf '\nThe project is ready; Apple account setup is the remaining step.\n'
  printf 'In Xcode Settings > Accounts, add your Apple account. Then choose your team under Vision > Signing & Capabilities.\n'
  printf 'Select the same team for VortexShare and VortexActivity, choose your connected iPhone, and press Run.\n'
  printf 'For terminal installation, rerun with --team YOURTEAMID (10 characters).\n'
  if [ -f "$LOCAL/discovered-teams.txt" ] && [ "$(awk 'NF {n++} END {print n+0}' "$LOCAL/discovered-teams.txt")" -gt 1 ]; then
    printf 'More than one signing team is available; select one with --team:\n'
    cat "$LOCAL/discovered-teams.txt"
  fi
  exit 0
fi

DEVICE_JSON="$LOCAL/devices.json"
if ! xcrun devicectl list devices --json-output "$DEVICE_JSON" >/dev/null 2>&1; then
  open "$PROJECT"
  printf '\nThis Xcode did not return connected devices. Choose your iPhone in Xcode and press Run.\n'
  exit 0
fi
"$PYTHON" - "$DEVICE_JSON" "$REQUESTED_DEVICE" "$LOCAL/device-selection.tsv" <<'PY'
import json, pathlib, sys
data = json.load(open(sys.argv[1]))
requested = sys.argv[2]
devices = data.get("result", {}).get("devices", [])
valid = []
for device in devices:
    hardware = device.get("hardwareProperties", {})
    props = device.get("deviceProperties", {})
    connection = device.get("connectionProperties", {})
    if hardware.get("platform", props.get("platform")) not in ("iOS", "iPadOS"):
        continue
    ident = device.get("identifier", "")
    udid = hardware.get("udid", ident)
    if requested:
        if requested not in (ident, udid):
            continue
    elif connection.get("tunnelState") != "connected" and connection.get("transportType") != "wired":
        continue
    valid.append((ident, udid, props.get("name", "iPhone"), props.get("developerModeStatus", "unknown")))
selection = pathlib.Path(sys.argv[3])
selection.write_text("")
if len(valid) == 1:
    selection.write_text("\t".join(str(value).replace("\t", " ").replace("\n", " ") for value in valid[0]) + "\n")
elif len(valid) > 1:
    print("Several iPhones/iPads are connected. Rerun with --device and one of these IDs:")
    for ident, udid, name, mode in valid:
        print(f"  {name}: {udid}")
elif requested:
    print("The requested iPhone was not found. Connect/unlock it and accept Trust on both devices, then rerun.")
else:
    print("Connect and unlock your iPhone, accept Trust, and enable Developer Mode; then rerun.")
PY
if [ ! -s "$LOCAL/device-selection.tsv" ]; then
  open "$PROJECT"
  printf 'The Xcode project is ready. You can also select your iPhone in Xcode and press Run.\n'
  exit 0
fi
IFS=$'\t' read -r DEVICE_ID DEVICE_UDID DEVICE_NAME DEVELOPER_MODE < "$LOCAL/device-selection.tsv"
if [ "$DEVELOPER_MODE" = "disabled" ]; then
  printf '\nEnable Developer Mode on %s at Settings > Privacy & Security > Developer Mode, restart when asked, and rerun.\n' "$DEVICE_NAME"
  open "$PROJECT"
  exit 0
fi

printf '\nBuilding Vision for %s with team %s...\n' "$DEVICE_NAME" "$TEAM"
if ! "$XCODEBUILD" -quiet -project "$PROJECT" -scheme Vision -configuration Debug -sdk iphoneos \
  -destination "id=$DEVICE_UDID" -destination-timeout 60 -derivedDataPath "$DERIVED" \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration build 2>&1 | tee "$LOCAL/device-build.log"; then
  open "$PROJECT"
  die "Xcode could not finish the build. See ios/Local/device-build.log. For a signing error, sign in under Xcode Settings > Accounts and check the same team on all three app targets. If the iPhone OS is newer than this Xcode supports, update Xcode and its iOS platform support. No Apple password is read by this script."
fi
APP="$DERIVED/Build/Products/Debug-iphoneos/Vision.app"
[ -d "$APP" ] || die "Xcode finished without producing Vision.app; inspect ios/Local/device-build.log."
printf '\nInstalling Vision on %s...\n' "$DEVICE_NAME"
xcrun devicectl device install app --device "$DEVICE_ID" "$APP"
if ! xcrun devicectl device process launch --device "$DEVICE_ID" "$BUNDLE"; then
  printf '\nVision is installed. Unlock the iPhone and open Vision. If asked, trust your developer at Settings > General > VPN & Device Management.\n'
fi
printf '\nVision is installed. Sign in once, allow Photos access, then enable Vortex in Share > More.\n'
printf 'To renew the free 7-day installation, rerun: bash %q\n' "$REPO_ROOT/ios/setup-ios.sh"
printf 'Keep the same signing team and bundle ID; reinstalling over the app preserves its container.\n'
if [ -f "$MARKER" ]; then printf 'To also fetch app updates, add --update.\n'; fi
