# iOS Home Screen sign-in recovery

## Native iPhone reconnect fix (workspace 1.1.0.4)

The native system-browser broker now explicitly requests Google's account
chooser on each **Continue with Google** tap. Normal browser login keeps its
existing remembered-account behavior. Previously `prompt: ''` could immediately
reuse the browser's Google account, so its popup appeared to flash and close.

The observed "Your sign-in has expired or is invalid" text originates in
FUPCJ's Firebase verification middleware, after Google and Firebase have
returned a credential. The recording does not identify the failed claim.
Two reproducible fresh-login failures are corrected: a PC slightly behind
Google's clock, and a new Google signing key absent from the still-cached
certificate set. Issue/authentication times allow at most 60 seconds of clock
skew; token expiry, signature, issuer, audience, provider and account checks
remain enforced. Unknown signing keys trigger a certificate refresh at most
once per minute while the existing cache is valid. No account tokens are logged.

The broker refreshes a rejected Firebase token once without opening Google a
second time, clears its temporary Firebase session on success/failure, and
distinguishes server clock/key errors from cancelled Google login. The PKCE
verifier, state, same-Google-account binding and single-use callback code are
unchanged. The page versions its two broker scripts to avoid mixing old and
new JavaScript after publication.

After merging and publishing, **update FUPCJ Server on the PC** to install the
verifier changes. No macOS installer run or iPhone app rebuild is required;
the existing app opens the hosted broker. Close an already-open sign-in sheet
and start a new attempt after publication. Workspace version is **1.1.0.4**;
the native app's version/build is unchanged. Run a PC update after a livestream
if restarting FUPCJ Server would interrupt its work.

Validation: `tests/ios-signin-browser.py` drives the actual broker, bundled
Firebase runtime, signed test JWT verification, Flask approval and one-use
PKCE exchange. It covers cancellation/retry, Alice-to-Bob reconnect, fresh
tokens with clock skew, one token-refresh retry, large clock errors and account
mismatch. Google UI/provider HTTP are fixtures, so a real Google account on a
physical iPhone still needs a post-publication check.

References:
- https://developers.google.com/identity/oauth2/web/reference/js-reference#TokenClientConfig
- https://firebase.google.com/docs/reference/admin/python/firebase_admin.auth#verify_id_token

## Shared web account behavior

Vision, Venture and Vortex share the Firebase project `visionboard-api`, account UID,
and FUPCJ processing server. Google login is started only in response to a user
click. Saved Firebase identities should be restored from IndexedDB or localStorage;
the apps should not silently downgrade to session-only persistence or open a
Google popup during startup.

## Direct Google login change (pending OAuth origin verification)

The hosted shared login now uses Google Identity Services (GIS) to obtain a
short-lived Google credential, then `signInWithCredential` verifies it through
the existing Firebase project. It does not visit Firebase's `/__/auth/handler`
or depend on that handler's cross-site sessionStorage. Vortex still enters via
Vision's allowlisted `?continue=vortex` gateway; Venture shares Vision's login.
The Firebase app name, API key, account UIDs, durable persistence and backend
token verification remain the same. No project or account migration is needed.

`web/google-login.js` uses the project's existing public Google web client ID:
`150865729216-1f7654bomu7se549rkofjku2fmjg98hu.apps.googleusercontent.com`.
It was read from the authorization URI returned by the public Firebase
`accounts:createAuthUri` endpoint for this project, without signing in a user.
No client secret belongs in this code.

Before merging/publishing this change, verify in Google Cloud Console →
Google Auth Platform → Clients → that exact web client → **Authorized JavaScript
origins** that `https://jrdn-r.github.io` is present. Add it if absent, without
a path or trailing slash. Retain the existing authorized redirect URIs for
Drive/local-file connections. Firebase's Authorized domains list is a separate
setting and does not establish that this OAuth JavaScript origin is configured.
Console settings could not be inspected here; a live GIS request returned 403
in the execution environment. Do not treat mocked browser tests as confirmation
of the OAuth client's live origin configuration or an actual iPhone login.

GIS loads only for signed-out hosted users, without opening a popup. A click
starts the Google popup synchronously; if its script is still loading, the UI
asks for another click after loading, preserving browser user activation.
Duplicate requests, denial, closure, blocked popups and expired attempts recover
without accepting late callbacks. Only `openid email profile` is requested;
previous Drive grants are not included. The temporary Google token stays in
memory and is exchanged for the ordinary persistent Firebase session.

This change targets hosted account login. The separate local-file bridge and
Google Drive reauthentication retain their existing Firebase popup mechanism;
the bridge and unbundled imports now match the existing 12.8.0 SDK pin.

Verification: `tests/google-login.test.cjs`, `tests/account-auth.test.cjs`,
`tests/google-login-browser.py`, and `tests/vision-session-return.py` cover
failure/retry and all three entry routes. Browser checks use the real bundled
Firebase credential exchange and persistence with provider/backend HTTP fixtures.
Google's GIS response is mocked. After configuring the origin and publishing,
complete the physical-iPhone checks below using an actual Google account.

References:
- https://firebase.google.com/docs/auth/web/google-signin
- https://developers.google.com/identity/oauth2/web/guides/use-token-model
- https://developers.google.com/identity/oauth2/web/reference/js-reference
- https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid

## October 2026 mitigation

The Google popup/redirect handler showed "Unable to process request due to
missing initial state" on iOS 27. Firebase SDK 12.19.0 has an open iOS
regression report (firebase/firebase-js-sdk#10428); that report is about
`signInWithRedirect`, so it does not prove this popup incident has the same cause.
For now `web/vendor/build-firebase.sh` pins the shared Firebase bundle to
12.8.0 (the known working SDK from that report). The build and browser CI
regenerate this bundle from the pinned package; `build-portable.yml` commits
the compiled vendor asset and rebuilt `Vision.html` after a main-branch change.

## Testing and limitations

Automated tests cover restoration across Vision/Vortex, a new browser page,
the use of persistent storage, no forced Google account selection, and
interactive sign-in after a failed restore. These cannot simulate Safari's
standalone web-app storage isolation or live Google OAuth. Complete validation
requires an iPhone with the installed Home Screen app:

1. In the existing Vision Home Screen app, tap Google sign-in and finish login.
2. Close Vision fully, reopen it, and verify the account is restored.
3. Move among Vision, Venture and Vortex, then return to the Home Screen.
4. Restart the iPhone and reopen Vision.
5. Test a denied/cancelled popup and verify the login form recovers.

Do not delete or reinstall the Home Screen web app as part of testing; that
can clear its origin-specific local data. Avoid deploying a changed
`authDomain` without also deploying a compatible same-origin Firebase auth
helper and updating authorized OAuth redirect URIs.

If iOS still loses the helper's state, the permanent solution is to host
authentication through the same origin as the app (Firebase Hosting custom
domain or a reverse-proxied `/__/auth/` helper) or implement an independent
Google credential flow. GitHub Pages cannot reverse proxy OAuth helpers;
pointing `authDomain` at `jrdn-r.github.io` without supplying the helper and
configuring the Google console would break Google login. Refer to:
https://firebase.google.com/docs/auth/web/redirect-best-practices
