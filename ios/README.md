# Vision for iPhone

An iOS companion for the existing Vision, Venture, and Vortex services. It uses
the same Firebase account and FUPCJ Server projects. Native components provide
the Share extension, background file transfers, Photos saves, Files export, and
the Lock Screen / Dynamic Island Live Activity. The board and chat use the
existing hosted interfaces in a persistent WKWebView.

The project supports iOS 17 or later. A compatible full Xcode installation is
required; an older Xcode may need an update to support the iPhone's installed
iOS version. The source contains public service addresses and Firebase web
configuration, never your passwords, saved OpenAI keys, or Apple private keys.

## Install in three steps

### 1. Publish the matching web changes

Merge the iOS companion pull request into `main` and allow the existing portable
build / GitHub Pages publication to finish. The new system-browser login page is
`https://jrdn-r.github.io/vision/ios-signin.html`. The native app also needs the
updated `web/auth.js`, `web/vortex/auth.js`, and `web/vortex/app.js` bridges.

A native build alone cannot publish these hosted pages. Until they are deployed,
the iOS sign-in flow cannot finish. The existing website remains usable in its
normal browser while these updates are being prepared.

### 2. Update FUPCJ Server once

On FUPCJ Server, run the following in **Administrator PowerShell** after merge:

```powershell
$VisionInstaller = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionInstaller
& $VisionInstaller -Action Update
```

The normal update adds the short-lived login handoff and the owned background
transfer endpoint. It preserves the existing configuration, projects, accounts,
and installed model setup. Keep the server awake and connected as usual.
Optional Apple push configuration is separate and is not required for downloads.

### 3. Run the Mac installer

For the latest merged source:

```bash
curl --fail --location --proto '=https' \
  'https://raw.githubusercontent.com/JRDN-R/vision/main/ios/setup-ios.sh' \
  --output "$HOME/Downloads/Setup-Vision-iOS.command"
bash "$HOME/Downloads/Setup-Vision-iOS.command" --ref main
```

The pull request's installation instructions may pin an exact tested commit.
Use that commit in both the download URL and `--ref` to reproduce that build.

The script downloads a managed checkout to `~/Developer/Vision-iOS`, finds full
Xcode, creates the Xcode project and three app targets, prepares the existing
Vision icon, finds an available signing team, builds, and attempts to install
on a paired iPhone. It does not install Homebrew or a third-party project
generator. Python's standard library generates the project.

Apple requires a few first-use actions that a shell script cannot approve:

1. Open Xcode, accept its license, and finish installing its iOS components.
2. Add your Apple account in **Xcode → Settings → Accounts**.
3. Connect and unlock the iPhone, select **Trust** if asked, and enable
   **Settings → Privacy & Security → Developer Mode** on the iPhone.
4. If the installer cannot identify one signing team, select the same team on
   **Vision**, **VortexShare**, and **VortexActivity** in Xcode's Signing &
   Capabilities panels, select the iPhone, and press Run. Alternatively rerun
   with `--team YOURTEAMID` and optionally `--device DEVICE_ID`.

If an iPhone is not paired or development signing has not been set up, the
installer opens the prepared project and explains the remaining step. It never
asks for your Apple password or attempts to bypass device trust or signing.

## Seven-day free signing

A free Apple Personal Team can install a development build. Apple says its
provisioning profiles expire seven days after issuance. Rebuild and reinstall
using the **same team and bundle identifier**; do not delete the app first:

```bash
bash "$HOME/Developer/Vision-iOS/ios/setup-ios.sh"
```

To update a clean checkout created by this installer after future merges:

```bash
bash "$HOME/Developer/Vision-iOS/ios/setup-ios.sh" --update --ref main
```

The script keeps your team and bundle identifier in a local ignored settings
file. It refuses to discard local changes. Automatic silent renewal is not
provided: Apple signing and device availability still apply. App, share, and
widget use separate App IDs. Apple's free-account limits also apply to any
other development apps already installed.

## First launch and everyday use

Open Vision, sign in to your existing account, and enable Photos access from
Downloads or settings if you want video saved automatically. Google sign-in
uses Apple's system authentication browser; email/password sign-in remains
available in the existing Vision interface. The sign-in session is shared with
the extension through the Keychain. Your Safari or desktop login is not copied
into the new app; the first native sign-in is required.

In another app, choose **Share → Vortex**. If Vortex is not visible, use the
share sheet's **More / Edit** controls to enable it. The host app must expose an
HTTP(S) URL or text containing one. The extension extracts up to ten links and
starts accepted server jobs. Raw files, photos, and text without a URL do not
become a downloadable link; unsupported media sources report Vortex's error.

| Item | Default behavior |
| --- | --- |
| Video link | Balanced quality, MP4; MOV can be selected in Vision settings. |
| Audio link | M4A; once ready, Vision opens a Files destination picker while visible. |
| Source discovered to be audio-only | The finished audio payload is recognized even if the initial URL did not identify it. |
| Spotify track link | Existing spotDL path: Spotify metadata is matched to audio from another public service. This is not export of Spotify's protected stream. |
| Image or gallery returned by a supported source | Kept as a supported image/ZIP for manual Files export. |
| Several links | Up to three accepted Vortex operations at once; extra links remain queued locally. Open Vision to send waiting links when server slots free up. |

Files are retained locally until removed from Downloads. Removing a local copy
does not erase an already-saved Photos asset or the independent server history.
FUPCJ's existing Vortex retention policy still applies to server copies.

## What works in the background

The share extension starts a real background `URLSession` download before it
closes. FUPCJ streams a bounded transfer envelope while its durable Vortex job
downloads/converts the source. When media is ready, the same response carries
the file. Native code checks the envelope, file type, filename and byte count,
then copies the media with bounded memory use.

The server job continues even if the phone disconnects. iOS schedules the phone
transfer and may delay it. Force-quitting Vision, a network/proxy interruption,
or an expired development install can stop phone-side delivery; reopen Vision
and retry the same job. There is no claim of unlimited background execution.
Transfer waiting is capped at two hours, individual payloads at 2 GiB, and
retries restart the phone transfer rather than resuming a byte range. Two server
streams globally and one per account keep the normal HTTP workers available.

Videos save automatically to Photos when add-only permission was already
granted and iOS gives the completion handler enough execution time. If Photos
cannot import the file, it remains in Downloads with Save to Files / retry
options. Audio stays private until a Files destination is chosen. The system
cannot show a folder picker over another app while Vision is suspended.

## Dynamic Island and Live Activities

| Build | Behavior |
| --- | --- |
| Free Personal Team | Open Vision to start a Live Activity for an active job. It displays on supported Dynamic Island hardware and the Lock Screen. Local updates occur when the app has execution time; stale progress is labelled. |
| Paid Apple Developer build with APNs configured | FUPCJ can update an already-started Live Activity while the app is suspended, within Apple's delivery budgets. |

**Neither build starts a new Live Activity directly from the share extension.**
This version does not implement ActivityKit push-to-start. Open Vision after
sharing if you want a new Dynamic Island activity. A Live Activity is a progress
display, not a process that keeps the app alive. It does not make transfer or
Photos-save execution unlimited.

For the optional paid push variant, run the installer with `--push` and follow
[the server APNs guide](../vision-pc/IOS-APNS.md). Apple team/key IDs, a private
`.p8` key, and a matching app topic are configured on FUPCJ Server. They are not
embedded in the app. The free configuration omits the APNs entitlement.

## Project structure

- `App/`: SwiftUI shell, trusted WKWebView bridge, downloads, system sign-in,
  Photos and Files integration.
- `ShareExtension/`: extracts links, queues server jobs and schedules transfers.
- `Shared/`: Firebase Keychain session, App Group journal and file locks,
  Vortex client, bounded transfer parser, shared completion handling.
- `ActivityWidget/`: Lock Screen and Dynamic Island presentation.
- `Tests/`: native URL and binary-transfer regression tests.
- `generate_project.py`: reproducible Xcode project, plists, entitlements,
  app icon, privacy manifests, and schemes.
- `setup-ios.sh`: Mac preparation, signing/build/install and renewal entrypoint.

`Generated/`, `Local/`, and `Vision.xcodeproj/` are reproducible and git-ignored.
The generator does not fetch signing secrets or change your Apple account.

## Verification

The GitHub **Test Vision iOS** workflow builds the app and both extensions on a
Mac runner and runs the hosted XCTest suite. A separate job checks the Python
login/transfer/push endpoints and JavaScript account bridge. APNs HTTP calls are
mocked in automated tests; real push delivery requires your private Apple
configuration and a device test.

On a Mac, verify without device signing:

```bash
bash ios/setup-ios.sh --simulator-test
```

Physical-device acceptance: sign in, share one short video with Vision closed,
check Photos after delivery, share audio and choose a Files destination, lock
the phone during a transfer, reopen after interruption, and rebuild the same
bundle after the signing period. Confirm the currently signed-in account sees
only its own downloads. Simulator compilation cannot establish physical-device
background timing, provisioning eligibility, or Live Activity delivery.

## Apple references

- [Personal Team limits and seven-day profiles](https://developer.apple.com/help/account/basics/about-your-developer-account/)
- [Capabilities by membership](https://developer.apple.com/help/account/reference/supported-capabilities-ios/)
- [Extension background transfers and shared containers](https://developer.apple.com/library/archive/documentation/General/Conceptual/ExtensibilityPG/ExtensionScenarios.html)
- [ActivityKit Live Activities](https://developer.apple.com/documentation/activitykit/displaying-live-data-with-live-activities)
- [ActivityKit push updates](https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications)
