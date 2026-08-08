# ─────────────────────────────────────────────────────────────────────────────
#  BB Sequence Browser – Nuke-style collapsed image sequences in the File Browser
#  Location : File Browser ▶ header "Sequences", or an import dialog's sidebar
# ─────────────────────────────────────────────────────────────────────────────

bl_info = {
    "name":        "BB Sequence Browser",
    "author":      "Blender Bob + Claude.ai",
    "version":     (1, 1, 0),
    "blender":     (4, 2, 0),
    "location":    "File Browser › Sequences",
    "description": "List image sequences as collapsed ranges, Nuke-style",
    "category":    "Import-Export",
}

import bpy
import os
import re
import time

from bpy.props import (
    BoolProperty, CollectionProperty,
    IntProperty, PointerProperty, StringProperty,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  SEQUENCE DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

# Non-greedy stem is deliberate: for "Plate01.0001.png" a greedy stem would
# split as ("Plate01.000", "1", ".png"). Non-greedy backtracks off the extension
# anchor and lands on ("Plate01.", "0001", ".png").
SEQ_RE = re.compile(r"^(?P<stem>.*?)(?P<num>\d+)(?P<ext>\.[A-Za-z0-9]+)$")

IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".exr", ".dpx", ".cin", ".tif", ".tiff",
    ".tga", ".bmp", ".hdr", ".psd", ".jp2", ".j2c", ".webp", ".sgi", ".rgb",
}

# Guard against pathological directories bloating the RNA collection.
MAX_ITEMS = 10000


def contiguous_ranges(nums):
    """[1,2,3,7,8] -> [(1,3), (7,8)].  Input must be sorted."""
    out   = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append((start, prev))
        start = prev = n
    out.append((start, prev))
    return out


def format_ranges(ranges, max_parts=3):
    parts = [f"{a}-{b}" if a != b else f"{a}" for a, b in ranges[:max_parts]]
    if len(ranges) > max_parts:
        parts.append("…")
    return ", ".join(parts)


def scan_directory(dirpath, images_only=True):
    """Return a list of plain dicts describing the directory contents."""
    try:
        entries = [e.name for e in os.scandir(dirpath) if e.is_file()]
    except OSError:
        return []

    groups  = {}   # (stem, padding, ext) -> [frame numbers]
    singles = []   # filenames with no numeric tail

    for name in entries:
        match = SEQ_RE.match(name)
        ext   = (match.group("ext") if match else os.path.splitext(name)[1]).lower()

        if images_only and ext not in IMAGE_EXTS:
            continue
        if not match:
            singles.append(name)
            continue

        key = (match.group("stem"), len(match.group("num")), match.group("ext"))
        groups.setdefault(key, []).append(int(match.group("num")))

    items = []

    for (stem, padding, ext), frames in groups.items():
        frames.sort()

        # A "sequence" of one is just a file that happens to end in digits.
        if len(frames) == 1:
            singles.append(f"{stem}{frames[0]:0{padding}d}{ext}")
            continue

        ranges = contiguous_ranges(frames)
        span   = frames[-1] - frames[0] + 1

        items.append({
            "name":        f"{stem}{'#' * padding}{ext}",
            "first_file":  f"{stem}{frames[0]:0{padding}d}{ext}",
            "stem":        stem,
            "ext":         ext,
            "padding":     padding,
            "frame_start": frames[0],
            "frame_end":   frames[-1],
            "frame_count": len(frames),
            "missing":     span - len(frames),
            "is_sequence": True,
            "range_text":  format_ranges(ranges),
        })

    for name in singles:
        match = SEQ_RE.match(name)
        items.append({
            "name":        name,
            "first_file":  name,
            "stem":        match.group("stem") if match else name,
            "ext":         (match.group("ext") if match
                            else os.path.splitext(name)[1]).lower(),
            "padding":     len(match.group("num")) if match else 0,
            "frame_start": int(match.group("num")) if match else 0,
            "frame_end":   int(match.group("num")) if match else 0,
            "frame_count": 1,
            "missing":     0,
            "is_sequence": False,
            "range_text":  "",
        })

    items.sort(key=lambda d: (not d["is_sequence"], d["name"].lower()))
    return items[:MAX_ITEMS]


# ═══════════════════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════════════════

def active_file_space(context):
    """The File Browser space this panel is drawn in, or None."""
    space = getattr(context, "space_data", None)
    if space and space.type == 'FILE_BROWSER' and space.params:
        if getattr(space, "browse_mode", 'FILES') == 'FILES':
            return space
    return None


def params_directory(params):
    directory = params.directory
    if isinstance(directory, bytes):
        directory = directory.decode("utf-8", "replace")
    return directory


def iter_file_browsers():
    """Every open File Browser in file (not asset) mode, as (area, space)."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'FILE_BROWSER':
                continue
            space = area.spaces.active
            if space.params and getattr(space, "browse_mode", 'FILES') == 'FILES':
                yield area, space


def window_of(area):
    for window in bpy.context.window_manager.windows:
        if area in tuple(window.screen.areas):
            return window
    return None


def scanned_browsers():
    """The File Browsers showing the directory we last scanned."""
    wanted = bpy.context.window_manager.bb_seq_dir
    return [(a, s) for a, s in iter_file_browsers()
            if params_directory(s.params) == wanted]


def rebuild(wm, dirpath):
    """Repopulate the item collection from disk, keeping the active row."""
    same_dir = dirpath == wm.bb_seq_dir
    previous = (wm.bb_seq_items[wm.bb_seq_index]
                if 0 <= wm.bb_seq_index < len(wm.bb_seq_items) else None)
    # Moving to a different directory should not carry a selection over.
    wanted = (previous.stem, previous.ext) if (previous and same_dir) else None

    wm.bb_seq_items.clear()
    restored = -1
    for data in scan_directory(dirpath, wm.bb_seq.images_only):
        item = wm.bb_seq_items.add()
        for key, value in data.items():
            setattr(item, key, value)
        if wanted and restored < 0 and (item.stem, item.ext) == wanted:
            restored = len(wm.bb_seq_items) - 1

    wm.bb_seq_dir = dirpath
    # Assign last: this fires _index_changed, which reads the finished list.
    wm.bb_seq_index = restored


def redraw_file_browsers():
    for area, _space in iter_file_browsers():
        area.tag_redraw()


def force_rebuild(context):
    """Rescan now, whatever context the toggle was flipped from."""
    wm    = context.window_manager
    space = active_file_space(context)
    if space is None:
        space = next((s for _a, s in iter_file_browsers()), None)

    if space is not None:
        rebuild(wm, params_directory(space.params))
    else:
        wm.bb_seq_dir = ""
    redraw_file_browsers()


def _settings_changed(self, context):
    force_rebuild(context)


# ── clicking a row selects the whole sequence ────────────────────────────────

# The file list is refiltered asynchronously and Blender re-syncs the highlight
# from params.filename on later redraws, so a single select_all can land too
# early or get clobbered. Re-assert it a few times; select_all is idempotent.
SELECT_RETRIES = (0.05, 0.3, 0.8)

_pending = None   # list of delays still to run, or None


def _apply_selection():
    """Second stage of a row click.

    Runs off a timer because operators must not be called from a property
    update callback.
    """
    global _pending
    if _pending is None:
        return None

    for area, space in scanned_browsers():
        window = window_of(area)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if window is None or region is None:
            continue
        try:
            with bpy.context.temp_override(window=window, area=area,
                                           region=region, space_data=space):
                bpy.ops.file.select_all(action='SELECT')
        except RuntimeError:
            pass
        area.tag_redraw()

    if _pending:
        return _pending.pop(0)
    _pending = None
    return None


def _index_changed(self, context):
    """Clicking a row narrows the browser to that sequence and selects it all.

    Resolved by matching the browsed directory rather than context.space_data:
    the row can be clicked from a header popover, where the context space is
    not reliably the File Browser.
    """
    global _pending, _watch
    wm = context.window_manager
    if not (0 <= wm.bb_seq_index < len(wm.bb_seq_items)):
        return

    item    = wm.bb_seq_items[wm.bb_seq_index]
    pattern = item.stem + "*"

    for area, space in scanned_browsers():
        try:
            # filename first: Blender re-syncs the highlight from it, so it must
            # not be written after the selection has been made.
            space.params.filename      = item.first_file
            space.params.filter_search = pattern
        except (AttributeError, TypeError):
            continue
        area.tag_redraw()

    wm.bb_seq_filter = pattern

    delays   = list(SELECT_RETRIES)
    _pending = delays[1:]
    if not bpy.app.timers.is_registered(_apply_selection):
        bpy.app.timers.register(_apply_selection, first_interval=delays[0])

    if item.is_sequence:
        _arm_load_watch(wm.bb_seq_dir, item)


# ── guarantee the result regardless of how the import operator behaved ───────

# Handing a multi-file selection to an import operator is the clean path, but it
# depends on Blender's selection surviving until execute, which is not something
# an add-on can observe or enforce. So also watch for the image actually landing
# and finish the job if it came in as a single frame.
WATCH_SECONDS = 15.0
_watch = None


def _arm_load_watch(dirpath, item):
    global _watch
    _watch = {
        "path":     os.path.normcase(os.path.normpath(os.path.join(dirpath, item.first_file))),
        "count":    item.frame_count,
        "start":    item.frame_start,
        "deadline": time.monotonic() + WATCH_SECONDS,
    }
    if not bpy.app.timers.is_registered(_watch_for_load):
        bpy.app.timers.register(_watch_for_load, first_interval=0.25)


def iter_image_users():
    """(image, ImageUser) for every place an image sequence can be played."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                space = area.spaces.active
                yield space.image, space.image_user

    # Scene.node_tree exists in 4.x but not 5.x, and the compositor moved into
    # node_groups, so probe for the attribute rather than assuming it.
    trees = list(bpy.data.node_groups)
    for collection in (bpy.data.materials, bpy.data.worlds,
                       bpy.data.scenes, bpy.data.linestyles):
        for owner in collection:
            tree = getattr(owner, "node_tree", None)
            if tree is not None:
                trees.append(tree)

    for tree in trees:
        for node in tree.nodes:
            iuser = getattr(node, "image_user", None)
            if iuser is not None:
                yield getattr(node, "image", None), iuser


def _watch_for_load():
    global _watch
    if _watch is None:
        return None
    if time.monotonic() > _watch["deadline"]:
        _watch = None
        return None

    for image in bpy.data.images:
        if image.source != 'FILE' or not image.filepath:
            continue
        try:
            path = os.path.normcase(os.path.normpath(bpy.path.abspath(image.filepath)))
        except (ValueError, RuntimeError):
            continue
        if path != _watch["path"]:
            continue

        image.source = 'SEQUENCE'
        for used, iuser in iter_image_users():
            if used != image:
                continue
            iuser.frame_duration   = _watch["count"]
            iuser.frame_start      = bpy.context.scene.frame_start
            iuser.frame_offset     = _watch["start"] - 1
            iuser.use_auto_refresh = True

        _watch = None
        return None

    return 0.25


# ── auto-refresh timer ───────────────────────────────────────────────────────

TIMER_INTERVAL = 0.5


def _poll_directory():
    try:
        wm = bpy.context.window_manager
    except AttributeError:
        return TIMER_INTERVAL

    if not getattr(wm, "bb_seq", None) or not wm.bb_seq.auto_refresh:
        return TIMER_INTERVAL

    for area, space in iter_file_browsers():
        directory = params_directory(space.params)
        if directory and directory != wm.bb_seq_dir:
            # Drop our own name filter on navigation, but never a typed one.
            if wm.bb_seq_filter and space.params.filter_search == wm.bb_seq_filter:
                space.params.filter_search = ""
            wm.bb_seq_filter = ""
            rebuild(wm, directory)
            area.tag_redraw()
        break

    return TIMER_INTERVAL


# ═══════════════════════════════════════════════════════════════════════════════
#  PROPERTIES
# ═══════════════════════════════════════════════════════════════════════════════

class BBSEQ_Item(bpy.types.PropertyGroup):
    name:        StringProperty()
    first_file:  StringProperty()
    stem:        StringProperty()
    ext:         StringProperty()
    padding:     IntProperty()
    frame_start: IntProperty()
    frame_end:   IntProperty()
    frame_count: IntProperty()
    missing:     IntProperty()
    is_sequence: BoolProperty()
    range_text:  StringProperty()


class BBSEQ_Settings(bpy.types.PropertyGroup):
    images_only: BoolProperty(
        name="Images Only",
        description="Ignore files that are not image formats Blender can read",
        default=True,
        update=_settings_changed,
    )
    auto_refresh: BoolProperty(
        name="Auto Refresh",
        description="Rescan automatically when the browsed directory changes",
        default=True,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  OPERATORS
# ═══════════════════════════════════════════════════════════════════════════════

def _active_item(context):
    wm = context.window_manager
    if 0 <= wm.bb_seq_index < len(wm.bb_seq_items):
        return wm.bb_seq_items[wm.bb_seq_index]
    return None


class BBSEQ_OT_refresh(bpy.types.Operator):
    bl_idname      = "bb_seq.refresh"
    bl_label       = "Refresh"
    bl_description = "Rescan the current directory"

    def execute(self, context):
        force_rebuild(context)
        if not context.window_manager.bb_seq_dir:
            self.report({'WARNING'}, "No File Browser directory to scan")
            return {'CANCELLED'}
        return {'FINISHED'}


class BBSEQ_OT_clear_filter(bpy.types.Operator):
    bl_idname      = "bb_seq.clear_filter"
    bl_label       = "Show All Files"
    bl_description = "Clear the name filter and show the whole directory again"

    def execute(self, context):
        wm = context.window_manager
        for area, space in scanned_browsers():
            space.params.filter_search = ""
            area.tag_redraw()
        wm.bb_seq_filter = ""
        wm.bb_seq_index = -1
        return {'FINISHED'}


class BBSEQ_OT_load_sequence(bpy.types.Operator):
    bl_idname      = "bb_seq.load_sequence"
    bl_label       = "Load as Image Sequence"
    bl_description = ("Load the active row as an Image data-block with source set to "
                      "Sequence, and show it in an open Image Editor if there is one")

    def execute(self, context):
        wm   = context.window_manager
        item = _active_item(context)
        if not item:
            self.report({'WARNING'}, "Nothing selected")
            return {'CANCELLED'}

        filepath = os.path.join(wm.bb_seq_dir, item.first_file)
        try:
            image = bpy.data.images.load(filepath, check_existing=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        if item.is_sequence:
            image.source = 'SEQUENCE'

        for window in wm.windows:
            for area in window.screen.areas:
                if area.type != 'IMAGE_EDITOR':
                    continue
                space = area.spaces.active
                space.image = image
                if item.is_sequence:
                    space.image_user.frame_duration = item.frame_count
                    space.image_user.frame_start    = context.scene.frame_start
                    space.image_user.frame_offset   = item.frame_start - 1
                area.tag_redraw()

        if item.is_sequence:
            self.report({'INFO'}, f"Loaded {item.name}  [{item.range_text}]")
        else:
            self.report({'INFO'}, f"Loaded {item.name}")
        return {'FINISHED'}


# ═══════════════════════════════════════════════════════════════════════════════
#  UI
# ═══════════════════════════════════════════════════════════════════════════════

class BBSEQ_UL_sequences(bpy.types.UIList):

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index):
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            split = layout.split(factor=0.66)
            split.label(
                text=item.name,
                icon='RENDERLAYERS' if item.is_sequence else 'FILE_IMAGE',
            )
            row = split.row()
            row.alignment = 'RIGHT'
            if item.missing:
                row.label(text="", icon='ERROR')
            row.label(text=item.range_text)
        else:
            layout.label(text=item.name)


def draw_panel(self, context):
    layout   = self.layout
    wm       = context.window_manager
    settings = wm.bb_seq

    row = layout.row(align=True)
    row.prop(settings, "images_only", toggle=True)
    row.prop(settings, "auto_refresh", text="", icon='TIME')
    row.operator("bb_seq.refresh", text="", icon='FILE_REFRESH')

    layout.template_list(
        "BBSEQ_UL_sequences", "",
        wm, "bb_seq_items",
        wm, "bb_seq_index",
        rows=10,
    )

    item = _active_item(context)
    if not item:
        layout.label(text="Click a sequence to select every frame", icon='INFO')
        return

    box = layout.box().column(align=True)
    if item.is_sequence:
        box.label(text=f"Frames  {item.frame_start} – {item.frame_end}")
        box.label(text=f"Count   {item.frame_count}")
        if item.missing:
            box.label(text=f"Missing {item.missing}", icon='ERROR')
    else:
        box.label(text="Single file")

    space = context.space_data
    if not (space and getattr(space, "active_operator", None)):
        layout.operator("bb_seq.load_sequence", icon='IMAGE_DATA')

    layout.operator("bb_seq.clear_filter", icon='X')


# Primary UI: a header popover, the same mechanism Blender's own Filter and
# Display buttons use. Used when the File Browser is open as an editor.
class BBSEQ_PT_popover(bpy.types.Panel):
    bl_idname      = "BBSEQ_PT_popover"
    bl_label       = "Image Sequences"
    bl_space_type  = 'FILE_BROWSER'
    bl_region_type = 'HEADER'
    bl_ui_units_x  = 20

    draw = draw_panel


# When an operator drives the browser (Image Texture > Open, Import, ...) the
# header region is collapsed to 1px, so the popover button has nowhere to draw.
# That case gets the right sidebar instead, which only exists while an operator
# is running and holds its import options. Between the two, every File Browser
# is covered.
class BBSEQ_PT_operator_props(bpy.types.Panel):
    bl_idname      = "BBSEQ_PT_operator_props"
    bl_label       = "Image Sequences"
    bl_space_type  = 'FILE_BROWSER'
    bl_region_type = 'TOOL_PROPS'

    @classmethod
    def poll(cls, context):
        space = active_file_space(context)
        return space is not None and space.active_operator is not None

    draw = draw_panel


# Optional always-visible copy. Lands under Bookmarks / System / Volumes /
# Recent, so collapse those once and Blender will remember the layout.
class BBSEQ_PT_sidebar(bpy.types.Panel):
    bl_idname      = "BBSEQ_PT_sidebar"
    bl_label       = "Image Sequences"
    bl_space_type  = 'FILE_BROWSER'
    bl_region_type = 'TOOLS'

    @classmethod
    def poll(cls, context):
        return active_file_space(context) is not None

    draw = draw_panel


def draw_header_button(self, context):
    if active_file_space(context):
        self.layout.popover(panel="BBSEQ_PT_popover", text="Sequences", icon='SEQUENCE')


def set_sidebar_panel(enabled):
    registered = hasattr(bpy.types, "BBSEQ_PT_sidebar")
    if enabled and not registered:
        bpy.utils.register_class(BBSEQ_PT_sidebar)
    elif not enabled and registered:
        try:
            bpy.utils.unregister_class(BBSEQ_PT_sidebar)
        except RuntimeError:
            pass


class BBSEQ_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__ or __name__

    def _sidebar_changed(self, context):
        set_sidebar_panel(self.show_sidebar_panel)

    show_sidebar_panel: BoolProperty(
        name="Also Show in Left Sidebar",
        description=("Add an always-visible copy of the list to the File Browser's left "
                     "sidebar, below the Bookmarks panels"),
        default=False,
        update=_sidebar_changed,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "show_sidebar_panel")
        layout.label(text="The list is always available from the Sequences button "
                          "in the File Browser header, and from the sidebar of any "
                          "import dialog.", icon='INFO')


# ═══════════════════════════════════════════════════════════════════════════════
#  REGISTRATION
# ═══════════════════════════════════════════════════════════════════════════════

classes = (
    BBSEQ_Item,
    BBSEQ_Settings,
    BBSEQ_Preferences,
    BBSEQ_OT_refresh,
    BBSEQ_OT_clear_filter,
    BBSEQ_OT_load_sequence,
    BBSEQ_UL_sequences,
    BBSEQ_PT_popover,
    BBSEQ_PT_operator_props,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    wm = bpy.types.WindowManager
    wm.bb_seq        = PointerProperty(type=BBSEQ_Settings)
    wm.bb_seq_items  = CollectionProperty(type=BBSEQ_Item)
    wm.bb_seq_index  = IntProperty(default=-1, update=_index_changed)
    wm.bb_seq_dir    = StringProperty(default="")
    wm.bb_seq_filter = StringProperty(default="")

    bpy.types.FILEBROWSER_HT_header.append(draw_header_button)

    prefs = bpy.context.preferences.addons.get(__package__ or __name__)
    if prefs and prefs.preferences.show_sidebar_panel:
        set_sidebar_panel(True)

    if not bpy.app.timers.is_registered(_poll_directory):
        bpy.app.timers.register(_poll_directory, persistent=True)


def unregister():
    for timer in (_poll_directory, _apply_selection, _watch_for_load):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)

    bpy.types.FILEBROWSER_HT_header.remove(draw_header_button)
    set_sidebar_panel(False)

    wm = bpy.types.WindowManager
    for prop in ("bb_seq", "bb_seq_items", "bb_seq_index",
                 "bb_seq_dir", "bb_seq_filter"):
        if hasattr(wm, prop):
            delattr(wm, prop)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
