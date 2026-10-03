# Google Drive and YouTube search

These browser features use the existing Google sign-in for Vision. No update to
FUPCJ Server is needed; its normal media processing and project saving still apply.

## Enable the Google services

Use the existing **visionboard-api** Google Cloud project (number **150865729216**).

1. Enable [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com?project=visionboard-api),
   [Google Picker API](https://console.cloud.google.com/apis/library/picker.googleapis.com?project=visionboard-api),
   and [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com?project=visionboard-api).
2. In [Credentials](https://console.cloud.google.com/apis/credentials?project=visionboard-api),
   edit the browser API key already used in `web/auth.js`. If it has API restrictions,
   add these three APIs **while retaining the existing Firebase/auth services**.
   If it has website restrictions, retain the existing entries and include
   `https://jrdn-r.github.io/*` and `https://docs.google.com/*` (Picker's iframe).
   Never put an OAuth client secret or service account key in the web app.
3. In Google Auth Platform → Data Access, include
   `https://www.googleapis.com/auth/drive.file` in the app's requested scopes.
   Keep the Firebase-created web OAuth client for the working Google login. The
   OAuth client, Picker app ID, and enabled APIs must belong to this same project.
   If the OAuth app is in Testing, add the Google accounts that will test Drive to
   its test users. Review the consent screen/audience before allowing other users.
4. Reload Vision, sign in, and use **Add to board → Google Drive → Choose files from
   Drive**. Approve the selected-file permission using the same Google account.
   Test an ordinary file and a Google Doc. In the YouTube dialog, type a query and
   press **Search**; select a result, then **Import video**.

On October 3, 2026, a live YouTube API request returned `accessNotConfigured` for
this project. The search UI explains the missing setup and still accepts pasted
YouTube links. Enabling Google sign-in alone does not enable these additional APIs.

## Behavior and data

- Drive uses Firebase `reauthenticateWithPopup` with the current user, so it cannot
  silently replace the Vision account. Google Picker uses the `drive.file` scope:
  users choose which files to share with Vision. The app downloads those selected
  files; it does not modify or delete the originals.
- The Google access token is kept only in memory, reused for at most 50 minutes,
  and cleared on account changes/sign-out. It is not placed in a project, sent to
  FUPCJ Server, or saved in browser storage by Vision's Drive integration.
- Up to 20 selected files, 100 MiB combined, enter the normal board import flow.
  Docs export to Word, Sheets to Excel, Slides to PowerPoint, and Drawings to PDF.
  Google limits native document exports to 10 MB. Folder/shortcut selection and
  files whose owners prohibit downloads are rejected with a readable message.
- Selected file contents become part of the user's project and follow Vision's
  existing save/processing rules on FUPCJ Server. Audio processing uses the
  transcription provider and sound-effects setting captured when import begins.
- YouTube queries go directly to Google's Data API only after Search/Enter.
  Search returns 10 videos per page, with a five-minute memory cache. Duration and
  view counts come from `videos.list`; search cards still work if that optional
  metadata request fails. Selecting a card fills the existing URL field. Import
  starts only after the user presses Import video, using the existing server flow.
- Canceling, changing accounts, or switching projects aborts pending source
  requests. Delayed responses cannot attach files to a different project/account.

## Verification

Run `node tests/google-sources.test.cjs` for mocked OAuth, Picker, API and account
race coverage. Live consent and downloads also require the owner setup above.

Primary references: [Firebase Google sign-in](https://firebase.google.com/docs/auth/web/google-signin),
[Google Picker setup](https://developers.google.com/workspace/drive/picker/guides/web-picker-sample),
[Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth),
[Drive download/export](https://developers.google.com/workspace/drive/api/guides/manage-downloads),
[YouTube search](https://developers.google.com/youtube/v3/docs/search/list).
