# Workspace chevrons

The two JSON assets preserve the supplied Circle Chevron Right Animated Icon's
24×24 paths, 2-unit rounded stroke, opacity/scale easing, and trim-path reveal.
Only the left chevron is mirrored; the circle and its drawing direction stay
unchanged. No raster layers or background are included.

The native linear gradient runs diagonally from `[2,22]` to `[22,2]`. Soft green
occupies 60% of the moving color ramp, followed by feathered lavender and coral.
Frame 588 matches frame 108 exactly. Geometry stops changing after frame 38.

## Playback contract

At 60 fps, play `[0,108]` once with looping off, then loop `[108,588]` forever.
The JSON markers are `entrance` and `gradient-loop`. Do not loop the whole file:
standard Lottie JSON does not encode separate loop policies for individual
properties. A native player must implement the same segment handoff.

`web/workspace-chevron.js` handles this contract in the app and preview with the
vendored Lottie SVG player, including reduced motion and background pausing.
Call `VisionChevron.create(element, 'right' | 'left').start()` after bundling its
asset placeholders through `web/build.py`. Each `start()` explicitly replays the
entrance; idle loops never call it. Reduced motion shows the final icon without
animation. Inactive workspaces pause their animation.

Open `circle-chevron-preview.html` directly for the self-contained side-by-side
preview. It requires no server or CDN. Rebuild it after asset/controller edits:

```sh
python3 web/build-chevron-preview.py
```

Lottie-web 5.13.0 is vendored under the MIT license in `web/vendor/lottie-LICENSE.md`.

# Floating menu animation

`menu-in-out.json` comes from the user-supplied `menu - in  out.lottie.zip`.
The original line geometry and timing are preserved, with a pastel white stroke.
Frames 0–51 draw the menu, frame 60 holds it, and frames 80–120 remove it.
`navigation-animation.js` plays each segment once and handles interrupted
toggles and reduced motion. The glyph is unboxed with a transparent 44px target.

Lilita One is bundled under its SIL Open Font License in `web/vendor/` and
embedded into the portable HTML. The existing transparent head is shared by both
menus and receives the same brief entrance motion without a tile or background.
