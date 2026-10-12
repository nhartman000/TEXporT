<p align="center"><img src="snap/gui/texport.png" width="96" alt="TExporT icon"></p>

<h1 align="center">TExporT for Linux</h1>

<p align="center">
A see-through panel that scroll-captures text from <b>any</b> window by OCR, lets you mark the lines that matter,<br>
and exports a handoff document the next AI can't quietly lose. MG8-ready.
</p>

<p align="center">Ubuntu (GNOME, X11 or Wayland) · Arch / <b>Omarchy</b> (Hyprland) · © 2026 American Milestone Inc</p>

---

## Contents
- [What it does](#what-it-does)
- [Install](#install)
- [Omarchy / Hyprland](#omarchy--hyprland)
- [Use](#use)
- [Auto-scroll](#auto-scroll)
- [Markers and export](#markers-and-export)
- [The MG8 unit and Verify](#the-mg8-unit-and-verify)
- [NYCH encoder / mg8-engine handoff](#nych-encoder--mg8-engine-handoff)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Project structure](#project-structure)
- [The TExporT family](#the-texport-family)
- [License](#license)

## What it does
1. You put a translucent TExporT panel over a conversation in any app: a desktop chat app, a browser, a terminal or a PDF.
2. TExporT reads the text with OCR as the window scrolls, either as you scroll or on its own with **auto-scroll**. It stitches the screens into one clean transcript with no duplicated lines.
3. You mark lines (**M**) or paragraphs (**P**), add notes (**N**), and fix any OCR slips by double-clicking a line.
4. **Export** writes a Markdown and PDF handoff:
   - reader instructions
   - a register of every marked passage, at the start and repeated at the end
   - inline `[[MARK Mn START]] … [[MARK Mn END]]` tags
   - optionally an **MG8 unit**, whose three retention gates **Verify** checks against any AI's answer

Everything runs on your machine. Nothing is uploaded.

## Install
```bash
git clone https://github.com/nhartman000/TEXporT.git
cd TEXporT
bash install.sh
texport            # or open "TExporT" from your app launcher
```
`install.sh` detects your system and installs what TExporT needs.

| System | Packages it installs |
|---|---|
| Ubuntu 22.04+ (apt) | tesseract + English data, PyGObject, GStreamer + PipeWire plugin, PySide6, xdotool |
| Arch / Omarchy (pacman) | tesseract + English data, python-gobject, pyside6, GStreamer + PipeWire plugin, **grim**, **ydotool**, xdotool |

It installs the app to `~/.local/share/texport/app`, a `texport` launcher in `~/.local/bin`, and a desktop entry. Run it again to update. Your captures are kept in `~/.local/share/texport/sessions`.

## Omarchy / Hyprland
**Install on Omarchy:** run `omarchy update` once first. Omarchy blocks direct `pacman -Syu`, and a fresh install has no package database yet. Then run `bash install.sh`.

Omarchy runs Hyprland, which behaves differently from Ubuntu's GNOME, so TExporT has a Hyprland mode. It switches on automatically when it detects Hyprland:

| | Ubuntu GNOME | Omarchy / Hyprland |
|---|---|---|
| Reading the screen | Screen-share portal (pick a window) | `grim`, focused monitor |
| Auto-scroll | Portal "remote interaction" | `hyprctl dispatch movecursor` + `ydotool` wheel |
| Keeping the panel floating on top | X11 always-on-top | Hyprland window rules set at launch (float + pin) |
| Hiding the panel while reading | Window opacity | Briefly unmaps the panel |

**One-time setup for auto-scroll.** `ydotool` sends the mouse-wheel events, which needs its service running and access to `/dev/uinput`:
```bash
systemctl --user enable --now ydotool
# If auto-scroll says ydotool can't scroll, allow /dev/uinput for your user, then log out and in:
echo 'KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"' | sudo tee /etc/udev/rules.d/80-uinput.rules
sudo usermod -aG input "$USER"
```
Manual capture, where you scroll yourself, works without ydotool.

The window rules are applied at runtime with `hyprctl` and never touch your Hyprland config. If you'd rather make them permanent, add to `~/.config/hypr/hyprland.conf`:
```
windowrule = float, title:^(TExporT)$
windowrule = pin, title:^(TExporT)$
```
On older Hyprland versions, write `windowrulev2` instead of `windowrule`.

> Hyprland support is new and has not yet been tested on a real Omarchy machine. If something is off, send `~/.local/share/texport/texport.log`.

## Use
1. Click **Share…**:
   - **Ubuntu:** pick the window in the screen-share dialog. Share the Claude/ChatGPT window, not the whole screen, and switch on "Allow Remote Interaction" if you want auto-scroll.
   - **Hyprland:** the focused monitor is used, with no dialog.
2. Click **Region…** and draw a box over the whole message column, including right-aligned messages. If lines touch the right edge, TExporT warns you and asks you to redraw.
3. Click **● Capture** and scroll the conversation from top to bottom, or tick **Auto-scroll** first.
4. Mark, note, fix, then **Export…**.

The panel's transparency slider lets you see the conversation through it.

## Auto-scroll
1. Tick **Auto-scroll** and click **● Capture**. The panel shrinks.
2. Move the pointer onto the conversation. That starts it.
3. The panel resizes to cover the conversation, leaving a strip at the edge where your pointer is. The wheel events go to the window under your pointer, so keep it in that strip. Moving onto TExporT pauses the run; moving back continues it.
4. It scrolls a few notches at a time and adjusts the speed so each screen overlaps the last. If a screen doesn't overlap, it backs up and re-reads. It only stops at the real end: the page has to stop moving, and one large confirmation scroll has to confirm it.
5. **■ Stop** or **Esc** ends the run, and the panel returns to its size and place.

## Markers and export
| Key / button | Action |
|---|---|
| **M** | Mark the selected line(s) |
| **P** | Mark the whole paragraph |
| **N** | Add a note to a marker |
| Double-click a line | Fix OCR text |
| **New** | Start a new capture (old ones are kept and can be combined at export) |
| **Export…** | `.md`, `.pdf` and, optionally, a `<name>.mg8/` unit |
| **Verify…** | Paste an AI's answer and check every marker survived |

The export format is the same in every TExporT tool, so a handoff from the desktop, the browser or the phone reads the same.

## The MG8 unit and Verify
With **MG8 unit** ticked, Export also writes:
```
<name>.mg8/
  unit.mg8               manifest (mg8_v1)
  flow.ork               gate order (mg8-engine reference profile)
  state/capture.gst      markers, notes, anchors, intent, constraints (gst_v1)
  gates/retention.g8son  3 retention gates (g8son_v1)
  canon.1.1.gitson       profile semantics + canonical MG8 repo references (gitson_v1)
  handoff.md             the marked handoff document
  trace/verify-*.qson    one append-only, hash-chained trace per Verify run (qson_v1)
```
**Verify** applies three gates using fixed rules, with no AI model involved:
1. **marker_coverage:** every marker ID (M1…Mn) is cited.
2. **anchor_fidelity:** key values from each marked passage appear verbatim. That means numbers, dates, file names, identifiers, quoted terms and inline code.
3. **no_phantom_ids:** no marker that doesn't exist is cited.

## NYCH encoder / mg8-engine handoff
If you have [nych](https://github.com/nhartman000/nych) and [mg8-engine](https://github.com/nhartman000/mg8-engine) installed in `~/nych-workspace`, click **⚙** → **Check connection**. Then tick "NYCH encoder → mg8-engine handoff" in Export.
- **NYCH format:** the nych `.gst` pretext with the symbolic layer, `nych.ork`, `nych.bonit`, and the step-2 mapping prompt.
- **Text format:** the same `.gst` schema without the symbolic layer, `mg8.ork`, `mg8.bonit`, and the step-3 congruence prompt.
- **Run it through mg8-engine now:** runs the pipeline on each marker with your configured model, carrying pinned symbols between markers.

Setup:
```bash
mkdir -p ~/nych-workspace && cd ~/nych-workspace
git clone https://github.com/nhartman000/nych.git
git clone https://github.com/nhartman000/mg8-engine.git
cd mg8-engine && python3 -m venv .venv && . .venv/bin/activate
pip install -e . -e ../nych
```

## Troubleshooting
| Problem | Fix |
|---|---|
| Every line is cut off on the right | The Region is narrower than the text. Redraw it over the whole column. |
| Duplicate or garbled lines from the panel itself | On a whole-screen capture the panel is in the picture. Share just the conversation window (Ubuntu), or keep the panel off the text. |
| "Scrolling permission (pointer) was not granted" (Ubuntu) | Click Share… again and switch on **Allow Remote Interaction** in the dialog. |
| Auto-scroll does nothing (Hyprland) | Check `systemctl --user status ydotool` and the `/dev/uinput` setup in the Omarchy section. Keep the pointer on the conversation. |
| The panel tiles instead of floating (Hyprland) | Add the two `windowrule` lines from the Omarchy section to your config. |
| Something else | Send `~/.local/share/texport/texport.log`. |

## Development
```bash
python3 tests/test_autoscroll.py      # auto-scroll pacing, back-up and end-detection simulation
python3 tests/sim_chat.py             # renders a chat with Playwright, then OCR + stitch end to end
texport                                # run from the installed copy (re-run install.sh after edits)
```
Publishing to the Snap Store is covered in [PUBLISHING.md](PUBLISHING.md).

## Project structure
```
texport/
  app.py           panel UI, region picker, auto-scroll controller, export/verify dialogs
  capture.py       frame sources: X11, GNOME portal (PipeWire), Hyprland (grim)
  ocr.py           tesseract TSV → lines
  stitch.py        aligns overlapping screens into one transcript
  autoscroll.py    pacing and end detection (pure logic, tested)
  export.py        Markdown / PDF handoff
  mg8.py           MG8 unit writer + Verify gates
  pipeline.py      nych / mg8-engine connection
  bridge_payload.py  .gst / prompt preparation for the NYCH handoff
install.sh         Ubuntu + Arch/Omarchy installer
snap/              snapcraft packaging
tests/             simulations
```

## The TExporT family
- **TExporT for Linux** (this repo): any window, by OCR.
- **[TExporT Markers](https://github.com/nhartman000/Texport-browser-extension)** for Firefox and Chrome: mark straight on Claude, ChatGPT and Gemini pages.
- **[TExporT for Android](https://github.com/nhartman000/TEXporT_android)**: "Mark with TExporT" in every app's text-selection menu.

## License
Proprietary. © 2026 American Milestone Inc. All rights reserved. Free to install and use; see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
