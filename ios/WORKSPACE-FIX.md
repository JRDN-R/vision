# Original workspace and startup fix

The first companion build rejected its own launch URL: Foundation's `URL.path`
removes a directory's trailing slash, but the native page allowlist expected it.
That cancelled navigation before the workspace loaded, left `loading` true,
and made the account button incorrectly suggest merging the already-merged PR.

The corrected check compares `URLComponents.percentEncodedPath` against exact
approved directory and index forms. HTTPS, host, port, credentials, and the
main-frame checks still protect the native credential bridge. Unrelated pages
on the same GitHub Pages origin are not added to the allowlist.

The app now opens the actual `Vision.html` workspace directly and removes the
replacement native top bar and bottom tab strip. Vision's original logo,
project actions, left-side menus, and Venture/Vortex navigation remain the web
app's own UI. Native-only iPhone downloads, settings, and reload controls are
added to the existing Vision, Venture, and Vortex menus. The normal website is
not modified. The download panel has a Back to workspace button and leaves the
same web view mounted so returning does not reload the current project.

Failed initial or committed loads, rejected startup navigation, HTTP failures,
and a terminated WebKit process show a recovery screen. A 60-second watchdog
bounds page loading. Retrying reloads web resources, not saved account data.

## Install this correction

Quit Xcode before regenerating the project, then run on the Mac:

```bash
bash "$HOME/Developer/Vision-iOS/ios/setup-ios.sh" --update --ref codex/ios-original-workspace-fix-20261024
```

This selects the fix branch even before its PR is merged, rebuilds the app and
extensions, and attempts installation on the paired iPhone. Keep the existing
app installed. The installer keeps the saved signing team and bundle identifier
and refuses to discard source changes. The generated Xcode project and local
signing settings are ignored by Git. An Apple trust or permission prompt still
requires the user's approval.

This correction does not change FUPCJ Server or Firebase configuration. The
original companion's published sign-in page and server auth endpoints are still
required for Google login and background transfers.

## Verification

`WorkspaceNavigationTests` adds the actual launch/directory/index URLs, query and
fragment forms, adversarial URL rejection, and navigation failure recovery to
the native XCTest suite. `tests/ios-workspace.test.cjs` exercises native sidebar
injection, original-control preservation, idempotence, delayed menu creation,
subframe exclusion, the bridge API, and shell/recovery invariants.

A signed-device run is still necessary to verify the visual layout, real Google
login, Photos, background transfers, and Live Activities on the user's iPhone.
