# KeyOverlay

A Windows tray app that shows always-on-top, transparent screen overlays when you press a hotkey: an image, a colored border, a screen tint, or large text.

## Features

- Global hotkeys that work in any app
- Four overlay types: image, border, tint and text
- Fade in/out animation and hold duration
- Multi-monitor support, with a display picker
- Live preview while you edit
- Hotkeys come back after you lock and unlock Windows
- Settings saved to `%APPDATA%\KeyOverlay\settings.json`

## Run from source

Requires Python 3.10+ on Windows.

```
run.bat
```

or `python overlay.py`. Missing packages (Pillow, pystray, keyboard) are installed automatically.

## Build the exe

Copy the folder to a normal location (for example `C:\KeyOverlay`), then run:

```
build.bat
```

The exe is written to `dist\KeyOverlay.exe`. If the app hits an error, details are written to `%APPDATA%\KeyOverlay\error.log`.
