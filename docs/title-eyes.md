# title-eyes

Open **https://jrdn-r.github.io/vision/title-eyes/** directly. There is no navigation button in Vision, Venture or Vortex. Sign in with the same Vision account. The page restores the existing session and also offers Google or email sign-in.

Drop a single media slide anywhere on the page, click the large drop area, or paste an image. The preview stays above an editable title field. Copy copies only the field’s text and shows a check mark for two seconds.

## Reading and casing

The existing, pinned **Qwen3.5-0.8B** visual worker identifies the main headline and transcribes its visible words. Its instruction excludes speaker names, scripture references, verse passages, church branding, service labels, dates and subtitles. It must not create a title for an image without one or invent unclear words. The response can indicate that manual review is needed. This is probabilistic image recognition, not a guarantee of perfect OCR, especially for distorted, overlapping, tiny or partly hidden lettering.

A deterministic English title-case pass keeps common articles, conjunctions and short prepositions lowercase in the middle, while capitalizing the first/last word and the first word after a colon. Recognized acronyms and mixed-case names are preserved. For example, `THE COST OF A WEDDING` becomes `The Cost of a Wedding`. This is a practical title-case convention, not a complete AP/Chicago linguistic parser; phrasal verbs and unfamiliar acronyms may need editing.

Supported input: one JPEG, PNG, WebP or still GIF, at most 10 MiB and 32 megapixels. The server orients and sanitizes it into a JPEG no larger than 1600 × 1600. Animated images and non-images are rejected. Images are not sent to a cloud model. The PC uses the same verified model/projector and shared inference lock as existing local visual work. No 9B/27B answering pass is needed. It waits while OBS is running or available memory is below 10 GiB.

Accepted jobs are owned by the signed-in account and persist through page/server restarts. This tab remembers the latest accepted job ID in session storage. The prepared image is discarded from the job after completion, failure, cancellation or expiry. Results expire after 24 hours. These jobs do not create Venture conversations or Vision projects. Local server administrators retain access to server data.

## One-time server update

The UI is hosted by GitHub Pages. The new authenticated API must be installed on FUPCJ Server before extraction works. This reuses the installed visual model and adds no new model or Python dependency.

Run the normal updater on the church PC:

```powershell
$ErrorActionPreference = 'Stop'
$VisionSetup = Join-Path $env:TEMP 'Setup-Vision-PC.ps1'
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/JRDN-R/vision/main/vision-pc/Setup-Vision-PC.ps1' -OutFile $VisionSetup
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $VisionSetup -Action Update
```

If the installed visual worker has never been enabled, follow the existing Local AI setup. The API reports this explicitly rather than silently using a cloud service.

## HTTP API

Backend: `https://desktop-vjt2br2.tail385c9d.ts.net`

Use `Authorization: Bearer <Vision Firebase ID token>` on every request. Tokens must be obtained by your authorized application, refreshed when needed, and kept out of URLs, source control, screenshots and logs. This API does not create a public unauthenticated OCR service. The installation connection token is accepted only under the server’s existing private-PC authorization policy; it is not a substitute for Firebase on a public installation. Existing origin restrictions apply.

| Method | Path | Action |
| --- | --- | --- |
| POST | `/api/title-eyes/jobs` | Multipart form with `image` (file) and `requestId` (8–80 letters, digits, `_` or `-`). Returns 202 and a job. |
| GET | `/api/title-eyes/jobs/{id}` | Read your job’s progress or result. Poll about every two seconds. |
| DELETE | `/api/title-eyes/jobs/{id}` | Cancel/remove your job and its retained data. Returns `{ "deleted": true }`. |

Reuse a requestId when retrying the same upload after a network error. A requestId identifies the original request, so use a fresh one for a different slide or a deliberate new attempt. Limits: three active jobs per account and twenty active jobs across the server. A full queue returns 429. Another account’s ID returns 404. The API returns 503 when the configured local visual worker is disabled.

Example completed response:

```json
{
  "id": "0123456789abcdef01234567",
  "status": "complete",
  "phase": "Ready",
  "error": null,
  "expiresAt": 1792447200,
  "model": "Qwen3.5-0.8B",
  "title": "The Cost of a Wedding",
  "rawTitle": "THE COST OF A WEDDING",
  "uncertain": false
}
```

`rawTitle` is the model’s transcription before the deterministic casing pass; the model may already have normalized capitalization. `title: null` means no title was reliably found; use `uncertain` to distinguish unclear title selection/lettering from an image reported to have no title. Treat model output as untrusted text, never HTML or instructions. Statuses are `queued`, `processing`, `complete` and `error`. DELETE removes the record, so later GET returns 404. Stop polling on completion, error, or 404.

### Reusable JavaScript client

The page uses this same client. It has no Firebase or DOM dependency; your application supplies its own authorized token getter.

```js
import { createTitleEyesClient } from
  'https://jrdn-r.github.io/vision/web/title-eyes/client.js';

const client = createTitleEyesClient({
  getToken: () => firebaseUser.getIdToken()
});

const result = await client.extract(slideFile, {
  onAccepted: job => saveJobId(job.id),
  onProgress: job => showStatus(job.phase)
});
console.log(result.title); // plain title text or null
```

`submit(file, {requestId, signal})`, `get(id, {signal})` and `remove(id, {signal})` are also available. Aborting a client request stops that client’s waiting; it does **not** cancel an already accepted server job. To cancel server work, call `remove(job.id)`. Keep the accepted ID if you want to resume polling later. GitHub Pages hosts the page/client; it does not execute the API.

## Verification

Offline tests cover account ownership, repeat uploads, malformed inputs/results, title casing, the shared model lock, OBS deferral, cancellation, restart recovery and cleanup. They use controlled model responses; they do not measure real OCR accuracy on sermon slides. Validate several representative slides against the church PC’s installed model after updating, especially mixed title/speaker/scripture layouts and decorative fonts.
