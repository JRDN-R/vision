#!/usr/bin/env python3
"""Update the portable Vision bundle to expose its existing Lottie switch on desktop.

Vision.html is a generated ~20 MB file. Keeping this patch small and repeatable
lets a GitHub runner apply it without uploading or rewriting its bundled assets
through a size-limited API.
"""
from pathlib import Path

app = Path(__file__).resolve().parents[1] / "Vision.html"
html = app.read_text(encoding="utf-8")
old_marker = "/* A single viewport anchor for both workspaces, outside the sliding content. */"
new_marker = "/* The shared Lottie switch flows with desktop headers and stays fixed on mobile. */"

old_visible = """ const visible=accountSignedIn()&&matchMedia('(max-width:760px)').matches;
 button.hidden=!visible;"""
new_visible = """ const mobile=matchMedia('(max-width:760px)').matches;
 // Keep one Lottie instance; dock it in the active desktop header, or use
 // the original fixed viewport position on mobile.
 const target=mobile?document.body:(venture.open?$('visionVenture')?.querySelector('.venture-header'):$('appHeader'));
 if(target&&button.parentElement!==target)target.append(button);
 const visible=accountSignedIn();
 button.hidden=!visible;"""

if new_marker in html:
    assert new_visible in html, "CSS and JavaScript switch patches are out of sync"
    print("Responsive workspace switch already applied")
    raise SystemExit(0)

assert html.count(old_marker) == 1, "Could not find original Lottie switch styles"
begin = html.index(old_marker)
end = html.index(" .venture-root .venture-header", begin)
assert 100 < end - begin < 1800, "Unexpected style block; stop without writing"
css = """/* The shared Lottie switch flows with desktop headers and stays fixed on mobile. */
.workspace-switch-slot{display:none}
.workspace-switch[hidden],.workspace-chevron[hidden]{display:none!important}
.workspace-switch{display:grid;place-items:center;flex:0 0 44px;width:44px;min-width:44px;height:44px;min-height:44px;padding:8px;border:0;border-radius:12px;background:transparent;touch-action:manipulation;-webkit-tap-highlight-color:transparent}
.workspace-switch:hover{background:#c4f48a0b}.workspace-switch:active{background:#c4f48a18}
.workspace-switch:focus-visible{outline:2px solid #c4f48a;outline-offset:2px}
.workspace-chevron{display:block;width:28px;height:28px;pointer-events:none}
.workspace-chevron>svg{display:block;width:100%!important;height:100%!important}
@media(max-width:760px){
 .workspace-switch{position:fixed;z-index:86;top:calc(10px + env(safe-area-inset-top,0px));right:calc(40px + max(12px,env(safe-area-inset-right,0px)))}
 .workspace-switch-slot{display:block;flex:0 0 44px;width:44px;height:44px}
"""
html = html[:begin] + css + html[end:]

replacements = (
    (old_visible, new_visible),
    ("if(panel&&board){const parent=matchMedia('(max-width:760px)').matches?document.body:board;if(panel.parentElement!==parent)parent.append(panel);panel.inert=venture.open;}",
     "if(panel&&board){const parent=mobile?document.body:board;if(panel.parentElement!==parent)parent.append(panel);panel.inert=venture.open;}"),
    ("if(header)header.inert=venture.open||(matchMedia('(max-width:760px)').matches&&!document.documentElement.classList.contains('header-open'));",
     "if(header)header.inert=venture.open||(mobile&&!document.documentElement.classList.contains('header-open'));"),
)
for old, new in replacements:
    assert html.count(old) == 1, f"Unexpected source for switch replacement: {old[:75]}"
    html = html.replace(old, new, 1)

assert html.count("function workspaceSyncSwitch()") == 1
assert "window.VisionChevron.create(host,direction)" in html
assert "button.onclick=()=>workspaceSwitch(venture.open?'vision':'venture')" in html
app.write_text(html, encoding="utf-8")
print(f"Updated {app.name} ({len(html):,} chars), preserving mobile and both Lottie animations")
