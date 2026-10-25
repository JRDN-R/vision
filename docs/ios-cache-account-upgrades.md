# iPhone cache, navigation, and Account Upgrade

Prepared on `codex/ios-cache-account-upgrades-20261025` for `JRDN-R/vision`.
The pull request requires CI validation and publication before installation. GitHub Actions generates the portable HTML and cache manifest from this source.

## Result

- The native iPhone app keeps verified copies of Vision/Venture and Vortex app files on disk. The current bundle is about 27 MB. After its first complete download, reopening uses the saved app files. Account data, projects, media and paid jobs still use their existing server connections.
- The app checks a small manifest on opening/resuming and every five minutes while active. Only changed app documents download. An interrupted or invalid update leaves the previous complete release available. Updates apply on the next launch or workspace reload, preserving the current editing session. This does not schedule periodic downloads while iOS has suspended or closed the app.
- Vision/Venture and Vortex keep separate, mounted web views. Switching back preserves the existing board/chat session instead of navigating the same web view away and rebuilding it.
- The title stays visible. Vision's menu starts with Account, Account Upgrade, New Project, and Save Project. Venture/Vortex shortcuts share the bottom row. Short screens use a compact two-column action layout. Venture history is paginated; Vortex history opens in its own dialog.
- Account Upgrade uses the existing audited admin access decisions. The gold request option disappears after approval. Admins open **Account → Manage account upgrades** to approve, deny, or revoke access. Existing approved Gemini accounts count as upgraded.
- Approval is enforced on the server for Gemini transcription and Luna descriptions/naming. An approved account uses its own saved OpenAI key, or the approving admin's saved key while that sponsor remains an authorized admin. Approval therefore authorizes paid usage, including sponsor billing where applicable. Credentials stay on FUPCJ Server.
- Upgraded defaults are Gemini transcription, background descriptions enabled, and automatic descriptions starting with Luna. A user's explicit Whisper/local/disabled choice takes precedence. Transcription, sound effects and description preferences are global, saved per account on the server, and applied to existing/new projects. Screenshot detail and automatic naming remain project options.
- Upgraded automatic titles use GPT Luna. Other accounts keep local Qwen. Typed titles remain protected. Uncertain paid naming requests pause instead of automatically replaying after a server restart.

## Validation

- 78 backend checks cover authorization, account isolation, revocation, saved preferences, Luna naming, frame processing, and existing iOS endpoints.
- 148 JavaScript checks pass; one pre-existing test is skipped.
- Browser fixtures cover request/approval, global settings, quick import selectors, recovery of unsynced choices, fixed titles, keyboard navigation, and menus at phone/desktop sizes. They also verify cached Vortex boots without separate static-asset requests and that history remains usable.
- The Xcode project generator validates target references. The new Swift cache tests cover manifest validation, persistent cache recovery, corruption detection, and HTTPS-origin preservation.
- A native Xcode build and simulator/device run remain required. This Linux environment cannot execute them. The existing iOS pull-request workflow will run them once the branch is authorized for upload and a PR is opened. Real iPhone reopen/switch timings are not yet measured.

## Rollout after review

1. Push the branch, open the PR, and require the iOS simulator and web/backend checks to pass.
2. Merge and let the portable build / GitHub Pages deployment publish the app documents and manifest together.
3. Update FUPCJ Server using its normal installer so account preference routes and paid access checks are available.
4. Update/build/install the iPhone app using the existing Mac installer. A website-only deployment cannot add the native disk cache or retained web views to an already installed binary.
5. On an iPhone, allow the first cache download to finish, reopen, and test Vision ↔ Venture ↔ Vortex with an active project. Verify that an unapproved account cannot use paid features, approval enables defaults, manual preferences persist, and revocation blocks subsequent paid requests.
