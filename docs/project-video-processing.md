# Project video processing

Project Settings in the Vision sidebar now saves transcription, sound effects,
screenshot detail and background visual descriptions with the project. New
projects use Balanced and the local transcription provider. Gemini remains an
explicit selection and uses the existing account approval flow. Old projects
retain their previous import-preference behavior until Project Settings is saved.
Already accepted imports retain the options captured when submitted.

| Preset | Starting interval | Maximum frames | Five-minute video |
|---|---|---|---|
| Maximum | 0.25 seconds | 1,200 | 1,200 |
| Medium | 1 second | 600 | 300 |
| Balanced | 3 seconds | 300 | 100 |
| Minimal | 10 seconds | 120 | 30 |

The interval is `max(starting interval, duration / maximum frames)`. These are
samples: even Maximum can miss fast movement between frames. Automatic extra
scene-cut extraction is not part of this release.

## Storage and exports

Uploaded videos and YouTube imports retain individual timestamped JPEGs in
project-owned frame collections. Only a thumbnail and frame identifiers travel
in the result manifest and editable project. JPEG reads require the existing
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
signature. Qwen receives current pixels plus the previous observation for cautious
continuity. Exact decoded pixel duplicates reuse a labeled prior observation;
similar images are still independently inspected. Coarse similarity groups nearby
observations into ranges while retaining every changed description. This is a
heuristic, not object tracking or evidence of continuous action. Frames are
captioned after extraction completes, independently of transcription.

## Activity and rollout

The existing Activity button lives directly under `body`, outside the collapsible
header and workspace transforms. It respects mobile safe areas, remains clickable
in Vision and Venture, shows errors until resolved, and briefly shows a completion
checkmark. Video, transcription, document, export, visual analysis and current
Venture/board runs report there.

Update FUPCJ Server using the existing Setup-Vision-PC update procedure after
merging, then refresh Vision. New clients explicitly reject older processors that
lack frame-collection support. The portable build workflow regenerates Vision.html.

Validation includes a real FFmpeg 4-FPS extraction, ownership/access rejection,
checkpoint recovery with a mock visual runtime, duplicate provenance, presets,
project settings and stale response guards. Real Windows model throughput and OBS
coexistence require verification on the target server; they are not inferred from
these offline tests. The GitHub workflow also runs phone/desktop browser checks.
