#!/usr/bin/env bash
# Temporary iOS Firebase Auth regression pin. Track firebase/firebase-js-sdk#10428.
# Bundled runtime is shared by Vision, Venture and Vortex.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
npm install --prefix "$TMP" --no-audit --no-fund --save-exact firebase@12.8.0 esbuild@0.25.10
printf "import * as app from 'firebase/app'; import * as auth from 'firebase/auth'; window.VisionFirebaseSDK={app,auth};\n" > "$TMP/firebase-entry.js"
"$TMP/node_modules/.bin/esbuild" "$TMP/firebase-entry.js" --bundle --format=iife --minify --legal-comments=inline --outfile="$ROOT/web/vendor/firebase.js"
