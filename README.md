# BB Sequence Browser

Nuke-style collapsed image sequences for Blender's File Browser.

A directory of 3037 frames reads as 14 rows:

```
NewSpritesBack01.####.png     2-217
NewSpritesBack02.####.png     1-217
NewSpritesBack03.####.png     1-217
...
gappedPlate.####.exr      ⚠   1-50, 60-100
```

**Clicking a row loads the whole sequence.** That is the entire interaction:
the list narrows the browser to that sequence and selects every frame, so the
import operator you already started receives all 217 files instead of the one
you clicked — and if that selection does not survive to execute, the image is
repaired into a sequence afterwards. See
[How clicking a row loads the whole sequence](#how-clicking-a-row-loads-the-whole-sequence).

## Where the list appears

It depends on how the File Browser was opened, because the two cases expose
opposite regions:

| How you opened it | Where the list is |
|---|---|
| File Browser as an editor | **Sequences** button in the header, next to the filter funnel |
| A dialog from an operator — Image Texture ▸ Open, Import, … | **Image Sequences** panel in the right sidebar, under the import options |

In an operator dialog the header region is collapsed to 1px so there is nowhere
to put a header button; in a standalone editor the right sidebar cannot be
opened at all. Between the two, every File Browser is covered.

## Controls

- **Images Only** — ignore files that are not image formats Blender can read.
- **Auto Refresh** (clock) — rescan when you navigate to a new directory. On by
  default; turn it off on very slow network shares.
- **Refresh** — rescan now.
- **Show All Files** — clear the name filter a row click applied, and go back to
  the full directory listing. Navigating to another directory clears it too
  (but never a filter you typed yourself).
- **Load as Image Sequence** — only in the standalone editor, where there is no
  import operator to hand off to. Loads the row as an Image data-block with
  `source = 'SEQUENCE'` and displays it in an open Image Editor with the frame
  duration and offset set.

Gapped sequences show a ⚠ and list each contiguous run (`1-50, 60-100`); the
detail box below the list gives the exact missing-frame count.

## Preferences

**Open the Options Sidebar in Import Dialogs** (on by default). Blender rebuilds
the temp file-select window from defaults every time it opens, so an import
dialog's Options sidebar is always closed and there is nothing for Blender to
persist — you would have to open it by hand on every import to see the list.
This forces it open.

It is forced *once per dialog*, so closing it by hand still sticks for that
dialog. The one gap: closing a dialog and reopening one inside the same 0.5s
timer tick is seen as the same dialog, because Blender reuses the freed space's
memory address. Not reachable at human speed.

**Also Show in Left Sidebar** adds an always-visible copy of the list to the
File Browser's left sidebar. It lands *below* the Bookmarks / System / Volumes
/ Recent panels — collapse those once and Blender remembers the layout. Off by
default.

## What this does not do

It does not replace the native file grid. Blender's File Browser is a C space
type (`space_file`); the file list is built in `filelist.cc` and drawn in
`file_draw.cc`, and nothing in that pipeline is exposed to Python — there is no
hook to supply or transform file entries, no way to add columns, and no UIList
for the main region. The scriptable surface is `FileSelectParams` (`directory`,
`filename`, `filter_glob`, `filter_search`, `display_type`, `sort_method`, the
`use_filter_*` booleans) and nothing more.

So the grid still shows all 3037 files. This add-on adds a collapsed view
beside it. Genuinely replacing the grid means patching Blender's C source and
shipping a custom build.

Placement facts found the hard way:

- The left sidebar (`TOOLS`) reports `active_panel_category` as `UNSUPPORTED`,
  so `bl_category` is ignored — no tab of your own. Added panels append below
  the built-ins and `bl_order` does not override that.
- The right sidebar (`TOOL_PROPS`) cannot be opened in a standalone File
  Browser editor; it only exists while an operator is driving the browser.
- An operator dialog collapses the header region to 1×1px.

## How clicking a row loads the whole sequence

Two mechanisms, because the first one cannot be made reliable on its own.

**1. Hand the selection to the import operator.** There is no API to select
File Browser entries by name, and writing an operator's `files` collection does
not stick — Blender fills it from the file list selection at execute time. So a
row click sets `params.filter_search` to the sequence stem and runs
`file.select_all`, which only touches entries the filter leaves visible.

That runs off a timer, not directly in the property update callback: operators
must not be called from an update callback. It is re-asserted three times
(0.05s, 0.3s, 0.8s) because the file list refilters asynchronously and Blender
re-syncs the highlight from `params.filename` on later redraws, either of which
can drop a single select_all. `params.filename` is therefore written *before*
the filter, never after the selection.

When this path works, Blender's own `use_sequence_detection` sees all 217 files
and sets `source = 'SEQUENCE'` itself.

**2. Repair the result if it did not.** Whether the selection survives to
execute is not something an add-on can observe or enforce, and in practice it
sometimes does not — you get a single frame. So a row click also arms a
15-second watch for an Image data-block appearing at that sequence's first
frame with `source = 'FILE'`. If one shows up, it is switched to `'SEQUENCE'`
and every ImageUser referencing it — shader nodes, compositor nodes, Image
Editors — gets `frame_duration`, `frame_offset`, `frame_start` and
`use_auto_refresh` set.

The consequence worth knowing: for 15 seconds after clicking a row, an image
loaded from that exact path will be converted to a sequence even if you loaded
it some other way. That is the intended reading of the click, but it is not
scoped to the import dialog.

## Install

Blender 4.2+. Install the folder as an extension, or zip it and use
*Edit ▸ Preferences ▸ Add-ons ▸ Install from Disk*.

## Notes

- Sequence detection groups on `(stem)(digits)(.ext)` with the digit run
  immediately before the extension, so `Plate01.0001.png` groups as
  `Plate01.####.png`, not `Plate01.000#.png`.
- Grouping is keyed on padding as well as stem, so `frame1.png`…`frame9.png`
  and `frame10.png` are listed separately rather than silently merged.
- A "sequence" of a single frame is listed as a plain file.
- The list is capped at 10000 rows.
