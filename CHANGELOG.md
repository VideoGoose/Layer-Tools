# Changelog

Layer Tools is in beta. Versions restarted at 0.9.0 when the plugin moved to this repository.

## 0.9.8 — 2026-11-07
- The plugin folder is now `layer-tools` (was `gus_layertools`). Delete an old `gus_layertools` folder when
  updating.
- The helper uploaded by the window is now called `layer_tools_live`. If the upload fails, the window looks for
  the copy Designer loaded at startup under several possible names, and logs which one it used.
- Install and update instructions in the README.

## 0.9.7
- **Director follows editor** now sets the director state that editors read. On a dedicated director the
  director's own local state is separate, so the earlier versions changed nothing the editors could see.
- If the director accepts a change but the editor still doesn't see it after 3 seconds, the log says so once.

## 0.9.6
- **Output sync** is one button that cycles Off (default), Editor follows director, and Director follows
  editor, with a hover tooltip explaining each.
- Director follows editor checks that the address it uses really is the director (not this editor) and logs
  which machine and address it found, or why each one failed.

## 0.9.5
- Hammer-and-wrench icon (`icon.svg`, `icon.png`), shown in the window header, as the page icon, and on the
  README.

## 0.9.4
- **Director follows**: an unlocked editor can send its hold / fade up / fade down to the director.

## 0.9.3
- **Follow director**: an editor unlocked from the director follows its hold / fade up / fade down.

## 0.9.2
- README cleanup.

## 0.9.1
- Fields whose names start with a digit (for example `1st brightness`) link by layer name
  (`module:<layer>.<field>`), because `getByUID(...)` can't be followed by a name starting with a digit.

## 0.9.0
- First version in this repository: Duplicate + UID link, Anchor to bottom, Blur, Clear expressions, Show UID,
  Link to parent (Opacity, Tint, Time, Colour Shift, Move, Crop, Colour), Find by UID, Check arrows, and the
  this PC / director switch.
