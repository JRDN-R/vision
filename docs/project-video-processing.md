# Project video processing

Project Settings in the Vision sidebar now saves transcription, sound effects,
screenshot detail and background visual descriptions with the project. New
projects use Balanced and the local transcription provider. Gemini remains an
explicit selection and uses the existing account approval flow. Old projects
retain their previous import-preference behavior until Project Settings is saved.
Already accepted imports retain the options captured when submitted.

| Preset | Starting interval | Maximum screenshots | Five-minute video | Descriptions |
|---|---|---|---|---|
| Maximum | 0.25 seconds | 1,200 | 1,200 | Every other screenshot (600) |
| Medium | 1 second | 600 | 300 | Every other screenshot (150) |
| Balanced | 3 seconds | 300 | 100 | Every screenshot (100) |
| Minimal | 10 seconds | 120 | 30 | Every screenshot (30) |

The interval is `max(starting interval, duration / maximum frames)`. These are
samples: even Maximum can miss fast movement between frames. Automatic extra
scene-cut extraction is not part of this release. Description sampling starts at
the first screenshot (1, 3, 5, ...). Skipped screenshots remain saved and are
explicitly marked not analyzed in TXT and JSON exports. Old collections retain
their original cadence. Progress counts screenshots processed, including skips;
reports also include the number described and the planned description count.

## Storage and exports

Uploaded videos and YouTube imports retain individual timestamped JPEGs in
project-owned frame collections. Only a thumbnail and frame identifiers travel
in the result manifest and editable project. New uploaded-video collections use
up to 1280-pixel screenshots, matching YouTube, instead of reusing the 640-pixel
preview thumbnail. JPEG quality steps down only if needed for the file limit. JPEG reads require the existing
project authorization. No credential, arbitrary URL or local path is stored in a
frame reference. Imports submitted by older clients keep the inline result format.

AI ZIP exports fetch frames sequentially (contact sheets render nine at a time),
then include current visual timeline TXT and JSON files. Incomplete or paused
analysis is explicitly labeled. Editable projects containing frame references need
their original project/server to fetch screenshots; downloaded AI ZIPs contain
the actual screenshots. A project fork does not duplicate server frame collections.

Collections have a 1,200-frame/256-MiB limit, each JPEG is bounded to 512 KiB,
and all collections share a 4-GiB storage limit. Project Settings provides an
inventory and explicit deletion. Deleting a preview leaves its separate frame
collection intact. Removing a collection makes those frame references unavailable.
Account deletion and expired trial cleanup include frame collections.

## Background visual worker

The existing verified Qwen3.5-0.8B runtime supplies descriptions. This feature does
not download weights, call paid providers, or assume an unverified 2B model is
installed. Run the existing local visual setup/acceptance process on the Windows
server before resuming a paused analysis. Missing or disabled models pause the
queue without discarding screenshots.

One worker uses the existing cross-process inference lock and below-normal CPU
runtime. Foreground local runs have priority; batches release the runtime after
at most 16 images. New batches defer while OBS is running or free RAM is under
10 GiB. This conservative OBS check includes an idle OBS process. Restart resumes
from the last saved frame. Pause/resume controls are available in Activity.

Pillow records image dimensions, aspect ratio, dominant colors and a coarse visual
signature. Qwen describes current pixels independently with conservative
sampling and an explicit instruction to omit uncertain claims. Previous captions
are not fed back into the model, avoiding propagation of earlier hallucinations. Exact decoded pixel duplicates reuse a labeled prior observation;
similar images are still independently inspected. Coarse similarity groups nearby
observations into ranges while retaining every changed description. This is a
heuristic, not object tracking or evidence of continuous action. Frames are
captioned after extraction completes, independently of transcription.

## Activity and rollout

Each node with source work has its own pill immediately above its top-left edge.
It follows the node through movement and zoom. Running jobs spin; awaiting jobs
have a stationary indicator; paused/error jobs remain visible. Clicking opens
Activity with that node identified. Completed node badges disappear. Work without
a source node (such as project-wide export or Venture chat) keeps the global
Activity entry point at the upper-left workspace edge, clear of the add button.

## Automatic project naming

Project Settings has an opt-in toggle, off by default. After the first file,
naming waits for source preparation and requests a durable local suggestion.
It runs once for an untitled saved project. Manual names, disabling the toggle,
account changes, and project switches prevent a late suggestion from renaming
anything. The title is applied through ordinary revision-checked project saving;
the worker never directly overwrites the saved title. Resume/reopen retrieves the
same request. Activity provides retry if the model is unavailable.

The same installed Qwen3.5-0.8B worker reads a bounded overview of all nodes
(titles, captions, prompts, filenames, and text excerpts) and up to six image
previews. It does not claim exhaustive inspection of all attachment contents.
It shares the inference lock and OBS/RAM guards, yields to foreground Qwen work,
and does not download weights or call a paid provider. Temporary trial projects
are excluded. Naming jobs are cleaned up with account deletion.

## Deployment and validation

Update FUPCJ Server using the existing Setup-Vision-PC update procedure after
merging, then refresh Vision. New clients explicitly reject older processors that
lack frame-collection support. The portable build workflow regenerates Vision.html.

Validation includes a real FFmpeg 4-FPS extraction, ownership/access rejection,
checkpoint recovery with a mock visual runtime, duplicate provenance, presets,
project settings and stale response guards. Real Windows model throughput and OBS
coexistence require verification on the target server; they are not inferred from
these offline tests. The GitHub workflow also runs phone/desktop browser checks.

These changes tune inference and image preparation; they do not fine-tune model
weights or establish a measured accuracy gain. A larger model remains a separate
installation/benchmark decision. The present runtime remains pinned to the
verified Qwen3.5-0.8B model and projector.
