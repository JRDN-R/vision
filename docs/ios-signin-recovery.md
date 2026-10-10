# iOS Home Screen sign-in recovery

Vision, Venture and Vortex share the Firebase project `visionboard-api`, account UID,
and FUPCJ processing server. Google login is started only in response to a user
click. Saved Firebase identities should be restored from IndexedDB or localStorage;
the apps should not silently downgrade to session-only persistence or open a
Google popup during startup.

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
