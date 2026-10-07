# Venture workspace update

## Current implementation and rollout status

The workspace implementation has been built and tested locally. At the time this review was written, the source changes were not deployed. The full local server suite passed 192 tests. Browser tests passed at 390px and 1280px for navigation, Sources, settings and dictation recovery. Paid Gemini transport and local model inference were mocked; no funding or live inference diagnosis is established by those tests.

## Navigation and files

Vision's five navigation controls use the requested Material Symbols Outlined icons with accessible names and temporary labels. Mobile-only swipes are restricted to the top navigation region, including the collapsed controls. A required, one-time acknowledgement is scoped to each signed-in user. The account menu retains an accessible alternative to swiping.

Each conversation has an authenticated Sources inventory with pagination, uploads, generated versions, previews and authorized downloads. The open inventory refreshes while work completes. Repeated generated-file grids no longer appear after every response; existing inline links still work.

Startup accepts ?view=venture, ?view=vision, #venture and #vision. Explicit URLs override the saved startup preference for that launch. Login and account settings can save a default; saved projects can override it. Opening a workspace does not automatically send a board or invoke an AI model.

## Model controls and cost routing

The web build and PC share an exact-ID capability catalog and fingerprint. Astra's documented output maximum is 128,000 tokens, not 64,000. Context capacity and output ceilings are distinct. This implementation reviews 13 model IDs, not every ID that an account might return. Unreviewed or specialized models are not assigned guessed controls.

Vision's 512-token slider minimum and 16,000-token initial preference are app policies, not claimed API defaults. Unsupported reasoning, verbosity and Pro controls are hidden. A stale PC catalog is detected before a new Venture model request.

Optional cost routing is off by default. Free local rules can temporarily use available Luna for a short, self-contained text edit, with a smaller output ceiling. Attachments, board context, history, memory, web search, retries and recognized technical or higher-stakes requests preserve the manual model. This is not an all-purpose AI prompt classifier or automatic history compaction engine.

## Dictation

Only Venture composer dictation changes. Failed requests retain the recording in the current page for an explicit new attempt. Unknown network outcomes first check the original receipt instead of blindly issuing another paid POST. Text is appended once and never automatically sent.

Explicit PC Whisper fallback uses the same recording, the installed local CPU model and the existing worker lock. It does not download weights or call Gemini. The PC and local transcription service must be online and enabled. Closing or discarding the browser recording does not create a permanent audio backup; a PC restart interrupts unfinished transcription.

Safe error categories distinguish authentication, billing, permissions, request/model, quota and service failures. A configured key does not prove available funding. Project Gemini approval is unchanged.

## Remaining scope

The Sora models and Videos API retired on September 24, 2026. A general multimedia adapter and in-chat model-to-model video handoff are not implemented. They must not be represented as functioning controls.

The app is a reasonable foundation for a private iOS prototype, not yet a finished App Store submission. Native audio interruptions, OAuth return flows, secure credentials, files/share-sheet integration, offline/reconnection behavior, dependable backend availability, account deletion, third-party AI consent and physical-device accessibility/backgrounding tests remain release work.

## References reviewed October 7, 2026

Exact model-card URLs are stored in the capability catalog. Primary references:

- https://developers.openai.com/api/docs/models/gpt-6-astra
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/docs/deprecations
- https://ai.google.dev/gemini-api/docs/transcribe
- https://ai.google.dev/gemini-api/docs/troubleshooting
- https://developer.apple.com/app-store/review/guidelines/
