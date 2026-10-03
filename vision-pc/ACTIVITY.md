# Vision Activity

Open **https://jrdn-r.github.io/vision/activity.html** on a phone or computer. The HTML is hosted on GitHub Pages; private activity stays on the existing FUPCJ processing PC. A Google login alone does not grant access.

## Enable once on the PC

While processing jobs are idle, open Windows PowerShell **as Administrator** on the standard Vision PC and run:

```powershell
$p="$env:TEMP\Enable-Vision-Activity.ps1"
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Enable-Vision-Activity.ps1' -OutFile $p
& powershell -NoProfile -ExecutionPolicy Bypass -File $p
```

Enter the exact Google email of an account that has already used Vision. Check the displayed account and type `YES`. The installer binds permission to that account's Firebase UID, not its name or email. Repeat deliberately to authorize another account. No accounts are authorized by default.

The installer adds the read-only extension and a small optional startup hook, keeps a protected backup, briefly restarts the existing `Vision Private PC` task, and checks startup. Existing projects, models, text logs, Firebase configuration, and processing settings are preserved. It does not install another Python runtime or change the Tailscale route. A customized installation is rejected rather than guessed.

Sign in on the hosted page using the account selected above. Keep the PC and its existing Tailscale/Funnel connection online. Nothing needs installing on the viewing device. A `file:` copy directs users to the hosted sign-in page.

## Behavior and limits

- First names in the people list; full names and emails in the selected profile. Accounts with the same first name remain distinct.
- Plain-language event titles, expandable explanations and available timings/sizes/results, local time, search, filters, and older-event loading.
- Polls about every three seconds while the page is visible; this is not instant server push. Unchanged revisions do not redraw the timeline. It pauses when hidden and reconnects on return.
- Reads the existing SQLite audit tables, not the desktop text files. Existing retained history appears immediately after activation. Only events actually recorded by the existing logger are available.
- Up to 2,000 recent events retained per user by the existing logger. The combined feed shows at most 2,000 at a time; select a user for their history. Filters apply to the loaded records.
- Sign-in sightings are distinct verified authentication sessions observed by the PC. Last seen is an observed request, not a claim that the user is currently online. Durations are elapsed wall time, not CPU utilization.
- An unavailable PC shows a disconnected/stale-data warning. Authorization loss or sign-out clears the private view.

`?demo=1` opens a prominently labeled sample preview with fictitious example.test addresses. It makes no backend or Firebase requests and never displays real records.

## Access and privacy

`GET /api/admin/activity` first passes the server's existing Firebase token verification, then checks `DATA_DIR/activity-admins.json` for an explicitly allowed UID. The API is read-only and has no remote grant/administration endpoint. Installation and diagnostic tokens cannot read it. The HTML contains only the existing public Firebase web configuration, never an admin credential or user log.

The allowlist is reread on every request. To revoke access, an administrator can edit that protected local JSON file to remove the account's UID. To disable all access, remove the file or set `uids` to an empty array. This affects subsequent requests immediately; previously viewed or copied information cannot be recalled. Do not commit this file, the database, or text logs to GitHub. The default location is `C:\ProgramData\VisionPC\data\activity-admins.json`.

The endpoint returns only names, emails, timestamps, counts, and allowlisted usage metrics. It never returns prompts, file contents, filenames, passwords, or API keys. User values are rendered as text, not HTML. Tokens travel in the Authorization header, not URLs. The page keeps activity data in memory only and the API disables caching.

Existing Firebase verification behavior is unchanged: signature, audience, issuer, expiry and Google provider are verified; server-side Firebase revocation checking is not added by this feature. Local dashboard-allowlist revocation is checked on every request.

## Maintenance and tests

The optional startup hook in server.py preserves the integration through ordinary source updates. New installations without the optional module still start normally. Run the enable command again to update the optional dashboard module, or to reapply the hook after intentionally installing an older server version.

```sh
python -m unittest discover -s tests -p test_activity_dashboard.py -v
python tests/activity_browser_smoke.py
```

The first command uses real temporary SQLite databases and standard-library tests. The browser test requires Playwright and Chromium (or CHROMIUM_PATH). It exercises the actual HTML with test-only Firebase/API stubs. Neither test proves live Google sign-in, Windows task execution, or a completed PC rollout; those require the installed PC and the owner's account. No production credentials or real records are test fixtures.
