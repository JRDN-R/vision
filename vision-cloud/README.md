# Vision Cloud setup

This is the shared cloud connection for Vision on iPhone, desktop browsers, and a downloaded `Vision.html`. Nothing needs to be installed or left running on your computer. Video processing runs in Google Cloud; this folder is deployed once using **Google Cloud Shell in your browser**.

The service is prepared but is not live until you deploy it to your Firebase project. A Firebase **Blaze billing account** is required. OpenAI and Gemini API charges are separate from Google Cloud hosting.

## 1. Prepare Firebase

1. Open [Firebase Console](https://console.firebase.google.com/). Select your project, or create a dedicated Vision project. Upgrade its billing plan to **Blaze** if necessary.
2. Open **Project settings → General**. Copy the **Project ID**.
3. Under **Your apps**, add a **Web app** if you do not already have one. Copy its `apiKey` from the Firebase configuration. This is public application configuration, not a private service-account key.
4. Open **Build → Authentication → Get started → Sign-in method**. Enable **Email/Password**.
5. Under **Authentication → Users**, choose **Add user**, create your account, and copy its **User UID**. Only this UID will be allowed to use your paid cloud service. Sign in using this email/password in Vision; do not embed the password in the HTML.
6. Open **Firestore Database**. Create the default database in **production mode**, preferably `us-central1`. If it already exists, keep its location. Its browser security rules must deny access to the `visionJobs`, `visionQuotas`, and `visionResponses` collections. On a new dedicated project, leave production mode's default deny-all rules in place. `firestore.rules` documents the intended rules; do not leave broad test-mode access enabled.
7. In [Google Cloud billing budgets](https://console.cloud.google.com/billing), add a small budget alert, for example **$5/month**. A budget sends alerts; it does not automatically stop charges.

## 2. Deploy entirely in Cloud Shell

Open [Google Cloud Shell](https://shell.cloud.google.com/) and select the same project. Paste these commands, replacing the three values with the details above:

```bash
git clone https://github.com/JRDN-R/vision.git
cd vision/vision-cloud
export VISION_PROJECT='your-firebase-project-id'
export VISION_OWNER_UID='your-authentication-user-uid'
export VISION_FIREBASE_API_KEY='your-public-firebase-web-api-key'
bash deploy-cloud-shell.sh
```

If you already cloned the repository in Cloud Shell, use `cd ~/vision && git pull && cd vision-cloud` instead of cloning it again. Only the Google Cloud Shell session uses the development tools; your computer and phone do not.

Approve Google's Cloud Shell authorization when prompted. You need project Owner access, or equivalent deployment and IAM permissions. Deployment typically takes several minutes. The script creates:

- One Cloud Run service, with zero idle instances, at most one active instance, 1 CPU and 2 GiB memory.
- A durable media queue that processes one video at a time.
- Private temporary result storage, automatic cleanup eligibility after one day, and per-user job records.
- Separate runtime, build, and queue identities. No downloadable private credential file is created.

At the end it prints an HTTPS service address and writes **`vision-cloud-config.json`** with:

```json
{
  "backendUrl": "https://vision-cloud-EXAMPLE.us-central1.run.app",
  "projectId": "your-firebase-project-id",
  "firebaseApiKey": "your-public-firebase-web-api-key"
}
```

If the first deployment reports a newly granted permission has not propagated yet, rerun the script after a minute. It reuses the created resources.

## 3. Connect Vision

1. Open [Vision](https://jrdn-r.github.io/vision/) and open its **Cloud connection/settings** form.
2. Paste the complete JSON from `vision-cloud-config.json` into the configuration field, then choose **Save cloud settings**.
3. Sign in with the account created in step 1.
4. Paste a short public YouTube link for your first test. Check the activity panel for progress.
5. Use **Download App** on desktop after configuring the connection. The public connection settings travel with the downloaded HTML. Sign in on any additional device when requested.

The same cloud service accepts the hosted website and a locally opened HTML file. An internet connection is required for YouTube retrieval, Gemini transcription, and OpenAI sessions. Editing, saving, and exporting existing local project content still work offline.

Do not place a service-account JSON key, Firebase password, or an OpenAI secret in the public HTML or repository. Firebase's public configuration can be embedded; paid endpoints verify a signed login token and the configured owner UID. OpenAI API keys are sent transiently over HTTPS for the requested session and are not saved by this service.

## What happens to a YouTube link

- Standard, shortened `youtu.be`, Shorts, and live-recording links are normalized to one video URL.
- Existing manual or automatic timestamped captions are preferred.
- Screenshots use the same timing as Vision: up to 5 seconds → every second; under 5 minutes → every 5 seconds; under 10 minutes → every 30 seconds; 10 minutes or more → every 60 seconds.
- When captions are unavailable, compressed audio is returned to Vision's Gemini queue. Vision handles pause-aware sections and the existing 60-second gaps. Keep Vision open for browser-based Gemini processing; saved projects retain unfinished audio jobs.
- Original video files are deleted when processing finishes. Cloud results are deleted immediately when Vision acknowledges successful import, or become inaccessible after 24 hours and are removed by storage lifecycle cleanup. Lifecycle deletion is asynchronous and may occur later than the 24-hour access cutoff.
- Imported audio is deleted from the project once its full transcript succeeds.
- Individual screenshots or optional nine-frame contact sheets are selected when exporting from Vision.

The initial limits are **two hours per video**, about **25 minutes processing time**, and **20 YouTube starts per user per UTC day**. There is a separate limit of 20 OpenAI starts per day. Change `DAILY_IMPORT_LIMIT` in Cloud Run if needed. These limits and a one-instance cap reduce surprise usage, but are not a billing hard cap.

Some YouTube videos require account verification or refuse requests from cloud addresses. The service reports that failure rather than repeatedly charging for retries. A first live test is required after deployment; cloud hosting cannot guarantee access to every video. Uploading an available video file directly to Vision remains an alternative.

## OpenAI sessions

Vision sends the project ZIP to the OpenAI Files API and attaches it directly to a Code Interpreter container in the Responses API. Code Interpreter is required; other tools are not enabled. The uploaded file expires at OpenAI after 24 hours. OpenAI's own API data policies apply to requests and generated artifacts.

The app streams public status events, available reasoning summaries, and the final Markdown response. It does not expose private chain of thought. OpenAI background responses can continue after a browser disconnect; Vision can reconnect using the saved response ID and the user's API key. Generated files should be downloaded or added to the project promptly because OpenAI containers expire.

The first version accepts **up to 25 MB per session ZIP**, including request overhead. Larger projects should use smaller exports. OpenAI model and Code Interpreter charges are additional to cloud hosting. Login with a ChatGPT subscription is not implemented; use an OpenAI Platform API key.

## Updating or stopping

To update the service, pull the new repository version in Cloud Shell and rerun the same deployment command with your three environment variables. To stop all new requests, set `OWNER_UIDS` to an empty value in Cloud Run. To remove the backend, delete the `vision-cloud` Cloud Run service and `vision-media` Cloud Tasks queue; remove its result bucket and stored job records if no longer needed. Artifact Registry images may continue to use a small amount of billed storage until deleted.

## Developer checks

`python -m unittest discover -s vision-cloud -p 'test_*.py'` exercises URL normalization, screenshot timing, caption conversion, auth/CORS rejection, and OpenAI request construction without live cloud or provider calls. Deployment and live YouTube/OpenAI requests need the real project and account credentials and are intentionally not part of these checks.
