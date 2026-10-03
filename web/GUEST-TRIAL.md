# Launch page and five-minute guest trial

Google authentication and account storage are unchanged. The launch page uses the supplied small Vision wordmark, a Why use Vision dialog, and a separate one-time trial button. This change uses server-issued guest leases, not Firebase anonymous users: **do not enable another Firebase sign-in provider**.

## Activate on FUPCJ Server

Update the installed processor using the existing installer (`Setup-Vision-PC.ps1 -Action Update`). The installer now includes `trials.py`. Until that update finishes, normal Google sign-in continues working and the trial button explains that a processor update is needed. Keep the PC awake for processing.

## Trial behavior

The server starts one uninterrupted 300-second lease only when the visitor clicks the trial button. Loading the launch page or opening Why use Vision does not start it. Reloads resume the original deadline, never extend it; closing a tab does not pause the timer. Processing/queue time counts toward the five minutes. Long jobs might not finish before expiry.

Trial boards, files and API keys are not put into browser recovery or saved account projects. A reload loses the board even when time remains. The browser stores only a random trial-device receipt. Temporary server job inputs and outputs are necessary for Whisper, optional sound recognition, uploaded videos, direct YouTube links and AI runs with the visitor's own API key. They use an isolated temporary processing namespace, not the saved projects table. Google Drive consent, YouTube search and account features still require Google sign-in.

At expiry the interface locks and clears the trial board. Server authorization independently rejects further requests and cuts off streaming output. A cleanup worker requests cancellation and removes temporary local files, job records and encrypted run credentials once their writers have stopped. Cancellation and deletion at an external AI provider are not guaranteed; provider retention/billing rules still apply. Signing in ends and discards the temporary workspace rather than silently saving it into the account.

## Repeat-use controls and privacy limits

A permanent SQLite receipt keeps an HMAC of the random browser identifier and an HMAC of the initial network address, plus the lease identifier and timestamps. Raw addresses, project contents and bearer tokens are not in this receipt. IPv6 is grouped by /64. Keep the server database and its trial secret across updates; deleting this database resets the receipts.

One trial per browser/network is best-effort abuse prevention, **not lifetime identification of a person**. Shared Wi-Fi or carrier networks can share a consumed trial. A different browser/device plus a different network can get around it. VPNs, IP reassignment and cleared storage prevent perfect identification without an account. The launch page discloses the shared-network limitation. No browser fingerprinting is used. Google users are never limited by a network's trial receipt.

Tailscale's loopback reverse proxy replaces X-Forwarded-For. Waitress trusts only 127.0.0.1 and one proxy hop, accepting only that forwarding address and scheme. Trial issuance reads the resulting REMOTE_ADDR, never arbitrary untrusted forwarded headers; missing real client addresses fail closed instead of consuming one global loopback trial. Do not expose the loopback processor directly or place an untrusted proxy in front of it. Guest issuance is capped at five concurrent leases and existing processing-queue/upload limits remain in place.

The Why dialog intentionally does not claim that the developer cannot access server data, that every stored byte is end-to-end encrypted, or that every provider deletes data immediately. Those are not properties of the current storage architecture. An administrator can turn off guest issuance using `"anonymousTrialEnabled": false` in the existing server config; Google sign-in is unaffected.

## Validation

`vision-pc/test_trials.py` tests server clock/resume/restart, hashed receipts, network reuse and spoofing rejection, namespace/key isolation, all guest processing types, no saved project/key records, cancellation/cleanup and concurrency limits. `tests/launch-trial-smoke.cjs` tests the built mobile page with mocked Firebase/server responses, including Why keyboard behavior, storage isolation, refresh, expiry and Google sign-in. Existing account and processing regressions should also pass. These tests do not initiate real paid API, Whisper or YouTube work on FUPCJ Server.
