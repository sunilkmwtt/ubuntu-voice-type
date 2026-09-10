# Voice Type

Local, offline, system-wide voice dictation for Ubuntu (X11).

Press a keyboard shortcut, speak, press it again, and the transcribed text
is pasted into whichever application currently has focus -- Chrome, VS
Code, GNOME Text Editor, Slack, a terminal, or anything else that accepts a
normal clipboard paste. Speech recognition runs entirely on your machine
via [OpenAI Whisper](https://github.com/openai/whisper); no audio or text
ever leaves the computer.

## How it works

```
Ctrl+Alt+Space -> arecord starts -> [sound + "Listening...." bubble at caret]
   ... you speak ...
Ctrl+Alt+Space -> arecord stops -> [sound, bubble -> "Processing...."]
   -> Whisper transcribes the recording locally
   -> text copied to the clipboard (xclip)
   -> Ctrl+V simulated into the focused window (xdotool)
   -> bubble hidden (or "Could not load" briefly, on failure)
```

This is the same flow as the original single-machine bash prototype
(`Ubuntu_Voice_Dictation_Setup`), reorganized into an installable package:

```
voice_type/
├── __init__.py
├── main.py         # entry point: toggles start/stop
├── recorder.py      # arecord start/stop via a PID file
├── transcriber.py    # loads Whisper and transcribes
├── paste.py         # xclip copy + xdotool paste
├── indicator.py      # status bubble: AT-SPI caret position + color, overlay window
└── sounds.py         # start/stop audio cues
install.sh
uninstall.sh
requirements.txt
pyproject.toml
```

## Requirements

- Ubuntu (tested on 22.04 LTS), **X11 session only** (not Wayland -- v1
  does not support it, since `xdotool` cannot simulate keystrokes on
  Wayland)
- GNOME Shell, for the automatic keyboard shortcut (other desktops work,
  just bind the shortcut manually -- see below)
- A working microphone

## Install

```bash
git clone https://github.com/<your-username>/ubuntu-voice-type.git
cd ubuntu-voice-type
./install.sh
```

This will (you'll be prompted for your sudo password once, for apt):

1. Install system packages: `python3-venv`, `python3-tk`, `alsa-utils`,
   `ffmpeg`, `xclip`, `xdotool`, `pulseaudio-utils`.
2. Create a virtual environment and install Whisper into it at
   `~/.local/share/voice-type/venv` (independent of this repo -- you can
   delete the cloned folder afterwards).
3. Symlink the `voice-type` command into `~/.local/bin`.
4. Register a GNOME custom keyboard shortcut, **Ctrl+Alt+Space**, without
   touching any custom shortcuts you already have.

The first real dictation will be slower than later ones: Whisper downloads
the `base.en` model (~140 MB) to `~/.cache/whisper` on first use.

### If the automatic shortcut doesn't work

Not on GNOME, or `gsettings` isn't available? Bind it manually:

**Settings → Keyboard → View and Customize Shortcuts → Custom Shortcuts →
Add**, with:

| Field    | Value                  |
|----------|------------------------|
| Name     | Voice Type             |
| Command  | `voice-type`           |
| Shortcut | `Ctrl+Alt+Space`       |

## Using it

1. Click into the text field where you want to dictate.
2. Press `Ctrl+Alt+Space`. You'll hear a short beep and see a small dashed
   "Listening...." bubble appear just above your text cursor.
3. Speak. The bubble updates in place to "Processing...." the moment you
   stop.
4. Press `Ctrl+Alt+Space` again. You'll hear a second, lower beep, and
   after a few seconds (Whisper is transcribing) the text is typed in at
   your cursor and the bubble disappears. If something goes wrong, it
   briefly shows "Could not load" instead before disappearing.

The bubble positions itself using the real text-caret location (via the
GNOME accessibility API) in GTK apps -- GNOME Text Editor, gedit, GNOME
Terminal. **VS Code and Chrome/Google Docs don't expose caret position to
that API**, so there the bubble falls back to appearing near the top of
the focused window instead of the exact cursor position. See
[Limitations](#limitations-v1).

## Uninstall

```bash
./uninstall.sh
```

Removes the `voice-type` command, the installed venv/app data under
`~/.local/share/voice-type`, and the Ctrl+Alt+Space shortcut this project
created. It does not remove the apt packages (`xclip`, `xdotool`, etc.), or
any other keyboard shortcuts you have configured.

## Performance notes

Each `voice-type` invocation is a short-lived process, so **the Whisper
model is loaded from disk fresh every time you stop a recording** -- there
is no background process keeping it warm. On CPU, loading `base.en` and
transcribing a short clip typically adds a few seconds of latency before
the text appears.

This is intentional for v1: it keeps the implementation to "one script, no
daemon, no IPC," which is much easier to install, debug, and uninstall
cleanly. If that latency becomes a real problem, the natural next step is
a small persistent background process (started once, e.g. at login) that
loads the Whisper model once and listens on a local socket or FIFO for
start/stop/transcribe requests -- `voice-type` would then become a thin
client that just sends a message to it. That is a deliberate v2 change,
not something this version does.

## Limitations (v1)

- **X11 only.** Wayland is not supported (`xdotool` can't synthesize
  keystrokes there). Check with `echo $XDG_SESSION_TYPE`.
- **English only** (`base.en` model). Swap the model name in
  `voice_type/transcriber.py` for multilingual support at the cost of
  accuracy/speed.
- **GNOME-only automatic shortcut setup.** Other desktops need the manual
  step above.
- **A few seconds of latency per dictation** (see Performance notes).
- Pasting relies on the target application supporting a normal `Ctrl+V`
  paste; apps with non-standard paste handling may not work.
- No background/daemon mode, no system tray icon, no configuration UI --
  everything is a single command triggered by the keyboard shortcut.
- **Caret-accurate bubble positioning only works in apps that expose their
  accessibility tree** -- confirmed working in GNOME Text Editor, gedit,
  and GNOME Terminal. **VS Code and Chrome/Google Docs do not** (they
  register with the system accessibility bus but don't populate real
  caret info for it), so the bubble falls back to a position near the top
  of the focused window there instead of the real cursor. This also
  requires `org.gnome.desktop.interface toolkit-accessibility` to be on
  (`install.sh` enables it) and apps that were already running before
  install need to be restarted once to pick it up.
- **The status bubble is semi-transparent, not fully see-through.** Real
  pixel-level transparency (punching an actual hole in the window) was
  tried and worked only about half the time on GNOME/Mutter, with visible
  flicker on retries -- a translucent whole-window blend was used instead
  for 100% reliability. The bubble's border/text color still matches the
  real caret color where available.

## Troubleshooting

- **Nothing happens / no transcription:** check
  `~/.local/share/voice-type/voice-type.log` for errors, and confirm
  `~/.local/share/voice-type/dictation.wav` was created and is non-empty.
- **Recording seems stuck:** `pgrep -a arecord`, and if needed
  `pkill arecord` then remove `~/.local/share/voice-type/recording.pid`.
- **Only near-silence / garbage is transcribed:** you likely spoke too
  briefly after starting, or the mic captured background noise. Try again
  and start speaking as soon as you see/hear the start cue.
- **Text pastes into the wrong window:** `xdotool` pastes into whatever
  window has X11 focus at the moment you press stop -- make sure your
  cursor is still in the intended field.
- **No indicator window appears:** confirm `python3-tk` is installed
  (`python3 -c "import tkinter"` inside the venv should not error).
- **Bubble doesn't line up with the cursor:** check
  `~/.local/share/voice-type/voice-type.log` for a line like "caret
  position unavailable" -- this is expected in VS Code/Chrome (see
  Limitations) and falls back to positioning near the focused window.
- **`arecord: command not found` or similar:** re-run `./install.sh`, or
  install the missing apt package it lists.

## License

MIT
