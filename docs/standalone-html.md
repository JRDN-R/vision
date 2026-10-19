# Vision Standalone HTML

`Vision-Standalone.html` contains the complete Vision/Venture interface, CSS,
fonts, images, animations, Firebase SDK, and media runtimes. It makes no CDN
requests to load those assets and does not check GitHub for updates.

Open the HTML on your computer and press **Open Vision**. A small launch page
opens at FUPCJ Server's `/standalone/` HTTPS address. The two browser tabs pass
the embedded app through `postMessage` entirely inside the browser. The launch
page checks its exact UTF-8 byte count and SHA-256 against the installed release
before rendering it. It accepts one message from its opener with a random nonce;
the desktop launcher also checks the source window and exact server origin.
Arbitrary or edited HTML is never executed at the server origin. No project,
password, or Firebase token is sent to the launcher or embedded in downloads.
After verification, the browser keeps an optional local copy of the app for
refreshing or reopening the HTTPS tab. The same release hash is checked again
on every load. If browser storage is unavailable, reopen the original HTML.

The running app now has FUPCJ Server's HTTPS origin. Authenticated API requests
are same-origin, so they do not require the cross-origin `OPTIONS` request that
failed from the desktop `file://` version. Existing API authentication, account
ownership, authorization, and jobs are unchanged. This is not a promise that a
workplace network will allow the connection; its policy still applies.

The HTML **still needs FUPCJ Server and Firebase online**. The server continues
using whatever providers a selected feature normally needs. Firebase login can
contact Google-operated authentication helpers. Those are authentication service
requests, not missing application assets. Sign-in on the new origin does not
share the GitHub Pages browser session; sign in again with the same account to
open your server projects. Local unsynced recovery copies do not migrate between
origins; export/import those projects if needed.

## One-time server update

Use the setup command supplied with the same standalone release. The regular
`Setup-Vision-PC.ps1 -Action Update` installer stages `standalone.py`,
`standalone-bridge.html`, and `standalone-manifest.json` with the other processor
files, including its existing backup/rollback flow. No new Python dependency,
model installation, listening port, or change to Tailscale is required.

The HTML must match the installed manifest. An altered file or another release
is rejected. Updating either side requires the matching file/release on the
other side. The app's **Download app** preserves the verified original payload,
without exporting current projects or credentials.

For **Google sign-in**, add `desktop-vjt2br2.tail385c9d.ts.net` in Firebase Console
→ Authentication → Settings → Authorized domains if it is not already present.
The standalone edition uses the bundled Firebase popup sign-in flow. Existing
Vision email/password sign-in remains available without the Google popup. Do
not enter your Google password in the Vision password field.

## Included and excluded services

The board, editable projects, uploads, document processing, video previews,
transcription, pasted YouTube links, Venture, and exports keep using FUPCJ Server.
Direct Google Drive picking, browser-side YouTube search, browser-provider
dictation, and links to separate hosted applications are excluded from this
edition so they do not introduce other runtime service dependencies. Add local
files, paste video links, and use Venture's FUPCJ transcription microphone.
Explicit external links in exported answers or help are still user-directed
navigation, not application dependencies.

## Rebuild and checks

```sh
python3 web/build_standalone.py
node --test tests/standalone.test.cjs
python3 tests/standalone-bundle.py
python3 tests/test_standalone.py
```

The normal portable build runs first and remains unchanged. The standalone
builder creates the large desktop HTML and its small server trust manifest.
CI uploads the HTML as `Vision-Standalone` and commits the matching manifest
alongside the normal portable build. Generated desktop HTML is not tracked.

Checks exercise altered-payload rejection, nonce/window/origin checks, duplicate
messages, Unicode bytes, workspace routing, download reconstruction, the actual
embedded runtimes and asset URLs, executable JavaScript syntax, and server route
behavior. Live Firebase sign-in, the installed Windows server, and the workplace
proxy still require an end-to-end check in the user's environment.
