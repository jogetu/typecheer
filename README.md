# desktop mascot app

Desktop mascot app that:
- reacts to global keyboard input
- alternates `left` / `right` hit images while typing
- returns to an `idle` image when typing stops
- lets users add/edit/delete characters from right-click menu
- lets users switch characters directly from right-click menu

## Setup (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python mascot.py
```

## Optional args

- `--character-name` (default: `Character 1`)
- `--idle-image`
- `--left-image`
- `--right-image`
- `--x` (default: `1200`)
- `--y` (default: `520`)
- `--scale` (default: `0.35`)
- `--config` (default: `mascot_settings.json`)

`--idle-image`, `--left-image`, `--right-image` are optional and must be passed together.  
If omitted, the app starts with a placeholder character and you can configure images from the menu.

## Controls

- `Esc` or `Ctrl+Q`: quit
- left mouse drag on center area: move window
- left mouse drag on edge: resize like a normal window edge
- right click: open menu
- menu `キャラクター編集...`: open editor UI
  - edit existing character settings
  - add new characters
  - delete characters
  - set display name and idle/left/right image paths
- under `キャラクター編集...`: click any character name to switch active character
- menu `打鍵開始の手`: switch first hand (`left` / `right`)
- snap behavior:
  - when released near taskbar top or window top edge, mascot snaps vertically
  - snap position is 10px below detected top edge

## Persistent settings

Saved to `mascot_settings.json` (or path from `--config`):
- `current_character_id`
- `characters[]`:
  - `id`
  - `name`
  - `idle_image_path`
  - `left_image_path`
  - `right_image_path`
- window position (`x`, `y`)
- `scale`
- start hand (`left` / `right`)
- window snap ON/OFF
