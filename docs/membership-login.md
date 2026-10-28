# Vision welcome and membership approval

The welcome page uses the supplied 36-second black/lime/olive fluid animation,
with the supplied Vision logo at upper left and a purple question mark at upper
right. The question mark opens the existing Why use Vision information. Google
sign-in and a collapsible email sign-in/create-account form are the entry choices.
The animation stops when hidden and uses a static composition for reduced motion.

New Firebase accounts are real accounts immediately, but cannot enter Vision,
Venture, Vortex, processing, saved keys, or Account Upgrade until approved on
FUPCJ Server. The pending page checks every 15 seconds while active and provides
manual refresh and sign out. Network failures never grant access. Guest trial
start and previously issued guest tokens are no longer accepted.

Membership and Account Upgrade are independent. Approving membership does not
change Gemini/Luna permissions, sponsors, billing, or saved preferences.

## Existing accounts and owner

On the first server startup with this change, a transaction snapshots existing
`audit_users` into approved memberships and records a persistent cutoff. New
requests are observed only after this migration. The migration does not repeat
on restart. Previously unseen Firebase users are checked using Firebase's
server-owned `createdAt` from the authenticated `accounts:lookup` REST endpoint.
Accounts created before the cutoff are approved automatically; later accounts
are pending. This also preserves Firebase-only accounts that never used FUPCJ.
If Firebase cannot verify creation time, the request fails closed and can retry.
No Firebase Admin service account is required for membership approval.

Sign in with **Google as rappsongs@gmail.com** to establish the designated owner.
The server requires a verified Firebase email and Google provider and permanently
binds that account's UID. An unverified email, password-provider email match, or
another UID claiming that address cannot become owner. Existing private UID
administrators remain authorized. The owner is protected by account deletion
administration. Decisions have an actor/time journal and are server-only.

Open https://jrdn-r.github.io/vision-status/ to use **Vision account requests** at
the top of the dashboard. The same queue is present in Vision's `activity.html`
alias. The Account menu links to new-account administration. The existing
Account Upgrade section remains separate. Status refreshes its queue while open.

## iPhone and Mac

The native iPhone app embeds the same welcome page in WKWebView. Google still
uses the existing system-browser broker, whose identity handoff is allowed for
pending users; all workspace API access remains gated. Email signup stays inside
the existing web interface. Direct Vortex entry checks membership first.

This change uses the existing native cache manifest/update mechanism and does
not change Swift or signing configuration. Existing installations can receive
the new login after the hosted update downloads and the workspace reloads.
An installed binary is not automatically rebuilt by a GitHub merge.

**New-request background push notifications are not added by this change.** The
requested admin workflow is implemented through Vision Status. Existing iOS
upgrade-approved alerts are preserved. Background APNs still requires a paid
Apple Developer build, `--push`, and the private server configuration documented
in `vision-pc/IOS-APNS.md`; a free `--no-push` installation cannot receive APNs.

## Rollout

1. Merge the Vision and vision-status pull requests after review/checks.
2. Wait for Vision's portable build and GitHub Pages publication. Generated
   portable HTML, standalone manifest, and iPhone cache are produced by CI.
3. On FUPCJ Server, run in **Administrator PowerShell**:

```powershell
$VisionInstaller = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionInstaller
& $VisionInstaller -Action Update
```

4. Sign in to Vision Status with the designated owner's Google account.
5. Reload the browser app / reopen or reload the iPhone workspace after its
   manifest update. For a source rebuild of the existing free-signing Mac setup:

```bash
curl --fail --location --proto '=https' \
  'https://raw.githubusercontent.com/JRDN-R/vision/main/ios/setup-ios.sh' \
  --output "$HOME/Downloads/Setup-Vision-iOS.command"
bash "$HOME/Downloads/Setup-Vision-iOS.command" --update --ref main --no-push
```

The managed checkout is `~/Developer/Vision-iOS`; keep the same signing team and
bundle identifier to preserve the installed app's data. Do not delete the app.
Paid push installations should retain their existing `--push` configuration.

Production Firebase, Windows FUPCJ Server, the Mac, and a physical iPhone are not
modified or device-tested from this development environment. Before rollout,
verify a current account opens normally, a newly created Google/email account
waits, the owner can approve it, and paid features still require their separate
upgrade approval.
