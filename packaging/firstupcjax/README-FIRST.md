# FirstUPCJax Admins on FUPCJ Server

Server installer package 1.0.1 fixes the "Invalid source revision" error in 1.0.0.
If that error stopped your first attempt, extract this complete corrected package
into a new folder and run `INSTALL-FIRSTUPCJAX-ADMINS.cmd` again. No uninstall or
credential reset is needed. The plugin and server feature version remains 1.0.0;
this is an installer correction.

This extension runs inside the existing always-on Vision PC service. It reuses the
working Planning Center Services scheduling engine and adds an official Scheduling
Email browser worker. It needs no new Docker container and no user laptop running.
The existing Windows service and Tailscale HTTPS connection remain in use.

## Install and connect

1. On FUPCJ Server, extract the complete private setup ZIP. Run
   `INSTALL-FIRSTUPCJAX-ADMINS.cmd` and approve administrator elevation.
2. Setup verifies checksums, updates the existing Vision service with a cold backup,
   installs its private pinned Node and Playwright runtimes, protects the supplied
   Planning Center credentials under SYSTEM DPAPI, and checks the public MCP
   authorization metadata. Internet access is needed for Python/Chromium packages.
3. Open the new desktop shortcut **FirstUPCJax Admins - Planning Center Sign In**.
   Sign in normally in the opened browser and select FirstUPCJax Services, then
   press Enter in the helper. It saves only Planning Center session state. The
   background SYSTEM worker imports it into DPAPI storage and removes the transfer.
4. Open https://jrdn-r.github.io/vision/admins/ and sign in with the configured owner
   account. This page must be published from the matching Vision source revision.
5. Install the inner `FirstUPCJax-Admins-Plugin.zip` using ChatGPT's custom plugin
   upload, or open the saved private plugin. Connect, sign into Vision, and approve.
6. Ask for a read-only upcoming-plan lookup. For the first deliberately requested
   notification, check the exact recipient, plan and resulting `sent` job. Live
   browser selectors and inbox delivery need acceptance on the actual FUPCJ PC.

Do not remove the existing working Sites plugin until the new connection passes
these checks. They are separate integrations. The new packaged plugin uses
`https://desktop-vjt2br2.tail385c9d.ts.net/firstupcjax/mcp` directly. It does not
need the old Site's bridge secret, pairing code, or hosting configuration.

## Private access and branding

The owner adds an email and role in Vision. Each person signs into their own
verified Vision account and links ChatGPT through OAuth. Viewer permits reads;
scheduler permits scheduling and requested emails. Owner also manages access.
Invitation records expire after 14 days if not accepted. Adding an email does not
send an invitation email; share the admin page or plugin with the invited person.
A ChatGPT email is not used as identity proof. Sharing the plugin never bypasses
the invite list. Revoke removes tokens and cancels unsent queued jobs.

The plugin package includes the supplied church logo as both `logo` and
`composerIcon`. The host controls where those assets render. The old manually
added/Sites app's icon is not changed by this new package. Logo metadata is not a
website favicon workaround.

The outer setup ZIP includes the supplied provider credentials in its private PC
configuration and is intended for the server owner. Share only the inner plugin
ZIP with invited users. That plugin contains no Planning Center credential.
Nothing is placed in public GitHub source or frontend JavaScript.

## Notification behavior

The worker verifies the exact person, assignment, plan, service type, future service
time and prior notification timestamp. It opens the individual Scheduling Email
composer, requires exactly one recipient and **Send 1**, preserves the selected
provider template, rechecks permission immediately before sending, and reads
`notification_sent_at` afterward. Planning Center supplies the response buttons.

Queued is not sent. `sent` means a new provider send timestamp was verified;
`already_sent` means no new email was sent. It does not prove inbox delivery.
A login wall becomes `waiting_for_login`; run the desktop sign-in helper again.
Unknown page layouts fail safely. A crash or timeout after the possible click
becomes `needs_review`; it never automatically clicks Send again. Check the exact
assignment in Planning Center before resolving or explicitly requesting a resend.

The browser adapter uses semantic controls. Actual Planning Center markup may
require adjustment during acceptance; mocked tests cannot prove the live composer.
No generic remote shell, arbitrary browser URL tool, or undocumented dispatch API
is exposed. Existing scheduling tools remain separate from notification results.

## Persistence, upgrades and recovery

Config, database and logs stay under `%ProgramData%\VisionPC`. Church membership,
OAuth grants and email jobs live in the existing `data\vision.sqlite3`. The local
scheduling journal and DPAPI-protected credentials/browser state live in
`data\church-admin` (or the configured data directory). Never move encrypted files
to another computer/account expecting DPAPI to decrypt them.

The existing updater creates a cold application/data backup. Extension setup uses
new dependency folders and restores the prior configuration if activation checks
fail. It preserves existing protected credentials and other Vision settings.
Rollback uses the retained processor backup with `Setup-Vision-PC.ps1 -Action
Rollback -BackupDirectory <backup path>`. User data is preserved during rollback.
To disable this extension, set `churchAdmins.enabled` to false in the protected
config and restart the **Vision Private PC** task; no deletion is required.

## Developer validation

Run `python -m pytest tests/test_church_admin.py tests/test_church_oauth.py
 tests/test_church_package.py -q`, `node --test tests/church-scheduler.test.mjs`,
`node tests/account-auth.test.cjs`, and `python web/build.py` from the repository.
The GitHub workflow also parses Windows PowerShell scripts. Full server regression
checks use `python -m pytest vision-pc/test_server.py vision-pc/test_firebase_auth.py
 vision-pc/test_account_administration.py -q` with base server dependencies installed.

`packaging/firstupcjax/build_release.py --private-config <owner-json>
 --node-archive <verified-node-zip> --output <directory>` creates the deliverables.
The owner JSON requires `ownerEmail`, `backendUrl`, and `pco` containing nonempty
`client_id` and `secret`. These values never belong in source control. Node's exact
version and SHA-256 are pinned in the installer. Build validates both ZIPs and
re-extracts the outer package; the release report records tests actually run.

Native OAuth uses exact redirect/resource binding, S256 PKCE, short-lived opaque
access tokens, hashed storage, rotating refresh tokens and replay revocation.
The current stable ChatGPT callback is allowlisted. If ChatGPT changes its callback,
add only its verified exact HTTPS URI to `churchAdmins.oauthRedirectUris` locally.
Never open redirect wildcards. A provider PAT is never an OAuth access token.
