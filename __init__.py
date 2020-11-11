# -*- coding:utf-8 -*-

import os
import re
import time
from datetime import timedelta
from .nVector import nVector
from mathutils import Vector, Matrix
import math

import blf
import bgl
import bpy
import gpu
import bmesh
from gpu_extras.batch import batch_for_shader

from bpy_extras.io_utils import ImportHelper, ExportHelper
from bpy.types import (
    Text,
    Scene,
    Panel,
    Object,
    Operator,
    PropertyGroup,
    AddonPreferences,
    UIList,
)
from bpy.props import (
    IntProperty,
    BoolProperty,
    EnumProperty,
    FloatProperty,
    StringProperty,
    PointerProperty,
    BoolVectorProperty,
    CollectionProperty,
    FloatVectorProperty
)
from bpy_extras.view3d_utils import (
    region_2d_to_vector_3d,
    region_2d_to_origin_3d
)
from nCNC.modules.serial import Serial
# from nCNC.pars.connection import NCNC_PR_Connection, NCNC_PT_Connection

# USB portlarını bulur...
from nCNC.modules.serial.tools.list_ports import comports

bl_info = {
    "name": "nCNC",
    "description": "CNC Controls, G code operations",
    "author": "Manahter",
    "version": (0, 6, 2),
    "blender": (2, 90, 0),
    "location": "View3D",
    "category": "Generic",
    "warning": "Under development. Nothing is guaranteed",
    "doc_url": "https://github.com/manahter/nCNC/wiki",
    "tracker_url": "https://github.com/manahter/nCNC/issues"
    }

# Serial Connecting Machine
dev = None

tr_translate = str.maketrans("ÇĞİÖŞÜçğıöşü", "CGIOSUcgiosu")


"""
Eklenecek Özellikler;
    * Objeyi, ToolPaths'a convert etmeden ekleyebilmelisin.
        Mesela Vievportta Text oluşturduk, bunu convert etmeden, ToolPaths'a ekleyebilmeliyiz.
    * Kod çizgileri görününce, included objeler görünmesin. (Vision'dan bu özellik aktifleştirilebilir olur)
    * Toolpaths HeaderDraw'a Göster/Gizle Ekle -> Objeler için
    * Sadece belli bir objenin yollarını (kodunu) göster/gizle özelliği ekle
"""


class NCNC_Prefs(AddonPreferences):
    # This must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    last_preset: StringProperty()


class NCNC_PR_Texts(PropertyGroup):
    loading: IntProperty(
        name="Loading...",
        subtype="PERCENTAGE",
        default=0,
        min=0,
        max=100
    )

    def template_texts(self, layout, context=None):
        row = layout.row(align=True)

        # Show / Hide
        if context:
            context.scene.ncnc_pr_vision.prop_bool(row, "gcode")

        row.prop(self, "texts", text="", icon="TEXT", icon_only=True)
        if self.active_text:
            row.prop(self.active_text, "name", text="")
        row.operator("ncnc.textsopen", icon="FILEBROWSER", text=("" if self.active_text else "Open"))
        if self.active_text:
            row.operator("ncnc.textsremove", icon="X", text="")
            # row.operator("ncnc.textssave", icon="EXPORT", text="")

        if self.loading > 0:
            row = layout.row(align=True)
            row.prop(self, "loading", slider=True)

        return row

    def texts_items(self, context):
        return [(i.name, i.name, "") for i in bpy.data.texts]

    def update_texts(self, context):
        self.active_text = bpy.data.texts[self.texts]

    last_texts = []
    texts: EnumProperty(
        items=texts_items,
        name="Texts",
        description="Select CNC code text",
        update=update_texts
    )

    def update_active_text(self, context):
        if not self.active_text:
            return

        if bpy.ops.ncnc.vision.poll():
            bpy.ops.ncnc.vision()

        self.active_text.ncnc_pr_text.load()

        for area in context.screen.areas:
            if area.type == "TEXT_EDITOR":
                area.spaces[0].text = self.active_text

        context.scene.ncnc_pr_vision.gcode = context.scene.ncnc_pr_vision.gcode

    active_text: PointerProperty(
        type=Text,
        update=update_active_text
    )

    @property
    def code(self):
        return bpy.data.texts[self.texts].as_string() if self.texts else ""

    @classmethod
    def register(cls):
        Scene.ncnc_pr_texts = PointerProperty(
            name="NCNC_PR_Texts Name",
            description="NCNC_PR_Texts Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_texts


class NCNC_OT_TextsOpen(Operator, ImportHelper):
    bl_idname = "ncnc.textsopen"
    bl_label = "Open GCode Text"
    bl_description = "Import a GCode file"
    bl_options = {'REGISTER'}

    # References:
    # https://docs.blender.org/api/current/bpy_extras.io_utils.html
    # https://sinestesia.co/blog/tutorials/using-blenders-filebrowser-with-python/
    # https://blender.stackexchange.com/questions/177742/how-do-i-create-a-text-datablock-and-populate-it-with-text-with-python

    filter_glob: StringProperty(
        default='*.text;*.txt;*.cnc;*.nc;*.tap;*.ngc;*.gc;*.gcode;*.ncnc',
        options={'HIDDEN'}
    )

    def execute(self, context):
        with open(self.filepath, 'r') as f:
            txt = bpy.data.texts.new(os.path.basename(self.filepath))
            txt.write(f.read())
            context.scene.ncnc_pr_texts.texts = txt.name

        return {'FINISHED'}


class NCNC_OT_TextsSave(Operator, ExportHelper):
    bl_idname = "ncnc.textssave"
    bl_label = "Export to GCode"
    bl_description = "Export a GCode file"
    bl_options = {'REGISTER'}

    # References:
    # https://docs.blender.org/api/current/bpy_extras.io_utils.html
    # https://blender.stackexchange.com/questions/150932/export-file-dialog-in-blender-2-80

    filter_glob: StringProperty(
        default='*.text;*.txt;*.cnc;*.nc;*.tap;*.ngc;*.gc;*.gcode;*.ncnc',
        options={'HIDDEN'}
    )
    filename_ext = ".cnc"

    def execute(self, context):
        active = context.scene.ncnc_pr_texts.active_text

        if active:
            text = active.as_string()
            with open(self.filepath, "wb") as f:
                f.write(text.encode("ASCII"))

            self.report({"INFO"}, "Exported")

        return {'FINISHED'}


class NCNC_OT_TextsRemove(Operator):
    bl_idname = "ncnc.textsremove"
    bl_label = "Remove Text File"
    bl_description = "Remove selected Text File"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        txt = context.scene.ncnc_pr_texts.active_text
        if txt:
            bpy.data.texts.remove(txt)
        return {"FINISHED"}


# #################################
# #################################
# #################################
class NCNC_PR_Lines(PropertyGroup):
    co: FloatVectorProperty()


class NCNC_PR_TextLine(PropertyGroup):
    lines: CollectionProperty(type=NCNC_PR_Lines)
    index: IntProperty()
    ismove: BoolProperty(default=False)

    code_full: StringProperty()
    code: StringProperty()
    comment: StringProperty()

    mode_distance: IntProperty(default=90)
    mode_plane: IntProperty(default=17)
    mode_units: IntProperty(default=21)
    mode_move: IntProperty(default=0)

    xyz_in_code: FloatVectorProperty()
    ijk_in_code: FloatVectorProperty()

    xyz: FloatVectorProperty()
    ijk: FloatVectorProperty()

    f: FloatProperty()

    length: FloatProperty(default=0)
    pause: FloatProperty(default=0)

    def get_estimated_time(self):
        f = 500 if self.mode_move == 0 else self.f
        return (self.length / (f * (1 if self.mode_units == 21 else 25.4))) * 60

    estimated_time: FloatProperty(get=get_estimated_time)

    def load(self, value: str):
        ismove_xyz = False
        ismove_ijk = False

        self.code_full = value
        self.prev_line = self.id_data.ncnc_pr_text.lines[self.index - 1]

        # ###############################################
        # ####################################### Comment
        for i in re.findall(r'\([^()]*\)', value):
            self.comment += i
            value = value.replace(i, "")

        # ###############################################
        # ########################################  G0-3
        value = value.upper()
        self.code = value

        for rex, key in [(r'G *(9[01])(?:\D|$)', "mode_distance"),
                         (r'G *(1[7-9])(?:\D|$)', "mode_plane"),
                         (r'G *(2[01])(?:\D|$)', "mode_units"),
                         (r'G *(0?[0-3])(?:\D|$)', "mode_move"),
                         ]:
            fn = re.findall(rex, value)
            exec(f"self.{key} = int(fn[0]) if {len(fn) == 1} else self.prev_line.{key}")

        if self.prev_line:
            self.xyz = self.prev_line.xyz

        # ###############################################
        # ################################ X0.0 Y0.0 Z0.0
        for j, v in enumerate("XYZ"):
            ps = re.findall(f'{v} *([+-]?\d*\.?\d*)', value)
            if len(ps) == 1 and re.sub("[+-.]", "", ps[0]).isdigit():
                ismove_xyz = True
                self.xyz_in_code[j] = float(ps[0])
                self.xyz[j] = float(ps[0]) + (self.xyz[j] if self.mode_distance == 91 else 0)
                self.xyz[j] *= (1 if self.mode_units == 21 else 25.4)

        # ###############################################
        # ################################ I0.0 J0.0 K0.0
        for j, v in enumerate("IJK"):
            ps = re.findall(f'{v} *([+-]?\d*\.?\d*)', value)
            if len(ps) == 1 and re.sub("[+-.]", "", ps[0]).isdigit():
                ismove_ijk = True
                self.ijk_in_code[j] = float(ps[0])
                self.ijk[j] = float(ps[0]) * (1 if self.mode_units == 21 else 25.4)

        # ###############################################
        # ############################################# F

        ps = re.findall('F *([+]?\d*\.?\d*)', value)
        if len(ps) == 1 and re.sub("[+.]", "", ps[0]).isdigit():
            self.f = float(ps[0])
        else:
            self.f = self.prev_line.f

        # ###############################################
        # ######################################## PAUSE
        ps = re.findall('G4 *P([+]?\d*\.?\d*)', value)
        if len(ps) == 1 and re.sub("[+.]", "", ps[0]).isdigit():
            self.pause = float(ps[0])

        if (ismove_xyz and self.mode_move in (0, 1)) or (ismove_xyz and ismove_ijk):
            self.ismove = True

        if self.ismove:
            for i in self.calc_lines():
                a = self.lines.add()
                a.co = i
        return

    def calc_lines(self, step: int = 0):
        """For this item"""
        mv = self.mode_move

        prev_xyz = Vector(self.prev_line.xyz)
        xyz = Vector(self.xyz)

        if mv in (0, 1):
            self.length = (prev_xyz - xyz).length
            return prev_xyz, xyz

        ijk = Vector(self.ijk)
        center = prev_xyz + ijk

        bm = bmesh.new()

        # Uyarı Buradan sonrası G17 düzlemi için hesaplanmıştır.
        # Diğer düzlemler için düzenlemek kolay.
        # Farkettiysen, Vektörlerin Z'lerinin yerine 0 yazdık.
        # Oraları düzenleyerek diğer düzlemler için uygulayabilirsin.

        # From the CENTER to the CURRENT POINT
        v1 = prev_xyz - center
        v1.z = 0

        # From the CENTER to the POINT of DESTINATION
        v2 = xyz - center
        v2.z = 0

        # Angle between V1 and V2 (RADIANS)
        angle = v1.angle(v2)
        if v1.cross(v2).z > 0 and mv == 2:
            angle = math.radians(360) - angle
        elif v1.cross(v2).z < 0 and mv == 3:
            angle = math.radians(360) - angle

        self.length = angle * v1.length

        # Angle between V1 and V2 (DEGREES)
        angle_degrees = math.degrees(angle)

        if step:
            pass
        elif v1.length < 10:
            step = math.ceil(angle_degrees / 10)
        elif v1.length < 50:
            step = math.ceil(angle_degrees / 5)
        else:
            step = math.ceil(angle_degrees / 2)

        # ####### !!!!!!!!!!!!
        # Bu kısımda axis'i güncelle ileride
        # Çünkü, G17, G18 vs düzlemine göre axis değişir
        bmesh.ops.spin(bm,
                       geom=[bm.verts.new(prev_xyz)],
                       axis=(0, 0, (1 if mv == 2 else -1)),
                       # axis=(.7, 0, (1 if mv == 2 else -1)),
                       steps=step,
                       angle=-angle,
                       cent=center
                       )
        # print("\n"*2)
        # print("CODE :", self.CODE)
        # print("NO :", self.NO)
        # print("Prev :", prev_xyz)
        # print("XYZ :", xyz)
        # print("IJK :", ijk)
        # print("Center :", center)
        # print("Vector1 :", v1)
        # print("Vector2 :", v2)
        # print("Angle :", angle)
        # print("Degrees", angle_degrees)
        # print("Cross :", v1.cross(v2))
        # print("Dot :", round(v1.dot(v2), 3))

        lines = []

        z_step = (xyz.z - prev_xyz.z) / step if step else 0

        for n, t in enumerate(bm.verts):
            x = round(t.co.x, 3)
            y = round(t.co.y, 3)
            z = round(t.co.z + n * z_step, 3)

            lines.append((prev_xyz.x, prev_xyz.y, prev_xyz.z))
            prev_xyz.x = x
            prev_xyz.y = y
            prev_xyz.z = z
            lines.append((prev_xyz.x, prev_xyz.y, prev_xyz.z))

        return lines


class NCNC_PR_Text(PropertyGroup):
    # Modals, stop, run ...
    isrun = []

    event: BoolProperty(default=False)
    event_selected: BoolProperty(default=False)

    last_cur_index: IntProperty()
    last_end_index: IntProperty()

    lines: CollectionProperty(
        type=NCNC_PR_TextLine,
        name="Objects",
        description="All Object Items Collection",
    )

    # Total Line
    count: IntProperty()

    # Milimeters
    distance_to_travel: FloatProperty()

    # Seconds
    estimated_time: FloatProperty()

    minimum: FloatVectorProperty()
    maximum: FloatVectorProperty()

    def event_control(self):
        cur_ind = self.id_data.current_line_index + 1
        end_ind = self.id_data.select_end_line_index + 1

        cur_ind, end_ind = min(cur_ind, end_ind), max(cur_ind, end_ind) + 1
        if cur_ind != self.last_cur_index or end_ind != self.last_end_index:
            self.last_cur_index = cur_ind
            self.last_end_index = end_ind
            self.event_selected = True

        self.load()

    def get_points(self):
        return [c.xyz for c in self.lines if c.ismove]

    def get_lines(self, move_mode=0):
        self.event = False
        lines = []
        for c in self.lines:
            if c.ismove and (c.mode_move == move_mode):
                lines.extend([i.co[:] for i in c.lines])

        return lines

    def get_selected(self):
        self.event_selected = False
        if self.isrun and self.isrun[-1]:
            return []

        count = len(self.lines)

        if count > self.last_end_index > self.last_cur_index:
            lines = []
            for i in range(self.last_cur_index, self.last_end_index):
                line = self.lines[i]
                if line.ismove:
                    lines.extend([i.co[:] for i in line.lines])
            return lines

        return [(0, 0, 0), (0, 0, 0)]

    def load(self):
        if not self.ismodified:
            return

        count = len(self.isrun)
        if count:
            self.isrun[-1] = False

        self.isrun.append(True)

        # ####################
        # Before Reset to vars
        self.lines.clear()
        self.count = 0
        self.distance_to_travel = 0
        self.estimated_time = 0
        self.minimum = (0, 0, 0)
        self.maximum = (0, 0, 0)

        bpy.ops.ncnc.gcode(text_name=self.id_data.name, run_index=count)
        self.prev_str = self.id_data.as_string()

    prev_str: StringProperty()

    def get_ismodified(self):
        return self.id_data.as_string() != self.prev_str

    ismodified: BoolProperty(get=get_ismodified)

    @classmethod
    def register(cls):
        Text.ncnc_pr_text = PointerProperty(
            name="NCNC_PR_Text Name",
            description="NCNC_PR_Text Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Text.ncnc_pr_text


class NCNC_OT_Text(Operator):
    bl_idname = "ncnc.gcode"
    bl_label = "Gcode Read"
    bl_description = ""
    bl_options = {'REGISTER'}

    text_name: StringProperty()
    run_index: IntProperty()
    code_lines = []
    last_index = 0
    pr_txt = None
    delay = .1

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        self.pr_txt = bpy.data.texts[self.text_name].ncnc_pr_text
        context.window_manager.modal_handler_add(self)

        line_0 = self.pr_txt.lines.add()
        line_0.load("G0 G90 G17 G21 X0 Y0 Z0 F500")

        self.code_lines = self.pr_txt.id_data.as_string().splitlines()

        return self.timer_add(context)

    def timer_add(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(self.delay, window=context.window)
        return {"RUNNING_MODAL"}

    def timer_remove(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        return {'CANCELLED'}

    def modal(self, context, event):
        if not self.pr_txt.isrun[self.run_index]:
            return self.timer_remove(context)

        pr = self.pr_txt

        context.scene.ncnc_pr_texts.loading = (self.last_index / len(self.code_lines)) * 100

        loop_count = 100 if event.type == "TIMER" else 20
        for no, code in enumerate(self.code_lines[self.last_index:], start=self.last_index + 1):
            pr.event = True
            pr.event_selected = True
            self.last_index += 1

            pr.count = no

            l = pr.lines.add()
            l.index = no
            l.load(code)

            # Calc -> Total Length, Time
            if l.length:
                pr.distance_to_travel += l.length
                pr.estimated_time += l.estimated_time

            # Calc -> Total Pause Time
            if l.pause:
                pr.estimated_time += l.pause

            # Calc -> Min/Max X,Y,Z
            for j, v in enumerate(l.xyz):
                if pr.minimum[j] > v:
                    pr.minimum[j] = v
                if pr.maximum[j] < v:
                    pr.maximum[j] = v

            if self.last_index % loop_count == 0:
                return {'PASS_THROUGH'}

        pr.event = True

        if context.area:
            context.area.tag_redraw()

        self.report({'INFO'}, "Loaded")
        self.pr_txt.isrun[self.run_index] = False
        context.scene.ncnc_pr_texts.loading = 0
        return self.timer_remove(context)


##################################
##################################
##################################

class NCNC_PR_Head(PropertyGroup):
    def update_common(self, context, key):
        keys = ["scene", "gcode", "machine", "vision"]
        keys.remove(key)
        for key in keys:
            exec(f"self.tool_{key} = False")

        # Apply Scene Settings
        bpy.ops.ncnc.scene()

        pr_vis = context.scene.ncnc_pr_vision

        # Load recent settings for pr_vis
        pref = bpy.context.preferences.addons.get(__name__)
        if pref and pref.preferences.last_preset:
            pr_vis.presets = pref.preferences.last_preset

        pr_vis.gcode = pr_vis.gcode
        pr_vis.dash = pr_vis.dash
        pr_vis.mill = pr_vis.mill

    def update_tool_scene(self, context):
        if self.tool_scene:
            self.update_common(context, "scene")

    def update_tool_machine(self, context):
        if self.tool_machine:
            self.update_common(context, "machine")

    def update_tool_vision(self, context):
        if self.tool_vision:
            self.update_common(context, "vision")

    def update_tool_gcode(self, context):
        if self.tool_gcode:
            self.update_common(context, "gcode")

            # Track Included Objects
            bpy.ops.ncnc.objects(start=True)
        else:
            # Cancel Track
            bpy.ops.ncnc.objects(start=False)

    tool_scene: BoolProperty(
        name="Scene Tools",
        description="Show/Hide regions",
        default=True,
        update=update_tool_scene
    )
    tool_machine: BoolProperty(
        name="Machine Tools",
        description="Show/Hide regions",
        default=False,
        update=update_tool_machine
    )
    tool_gcode: BoolProperty(
        name="G-code Generation Tools",
        description="Show/Hide regions",
        default=False,
        update=update_tool_gcode
    )
    tool_vision: BoolProperty(
        name="Vision Tools",
        description="Show/Hide regions",
        default=False,
        update=update_tool_vision
    )

    @classmethod
    def register(cls):
        Scene.ncnc_pr_head = PointerProperty(
            name="NCNC_PR_Head Name",
            description="NCNC_PR_Head Description",
            type=cls
        )

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_head


class NCNC_PT_Head(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "nCNC"
    bl_label = ""
    bl_idname = "NCNC_PT_head"

    def draw(self, context):
        pr_txs = context.scene.ncnc_pr_texts
        pr_con = context.scene.ncnc_pr_connection
        pr_com = context.scene.ncnc_pr_communication

        layout = self.layout
        layout.template_running_jobs()
        pr_txs.template_texts(layout, context=context)

        if pr_con.isconnected:
            row = layout.row()
            if pr_com.run_mode == "stop":
                row.operator("ncnc.communicationrun", icon="PLAY", text="Start").action = "start"
            elif pr_com.run_mode == "pause":
                row.operator("ncnc.communicationrun", icon="PLAY", text="Resume").action = "resume"
                row.operator("ncnc.communicationrun", icon="SNAP_FACE", text="Stop").action = "stop"
            else:
                row.operator("ncnc.communicationrun", icon="PAUSE", text="Pause").action = "pause"
                row.operator("ncnc.communicationrun", icon="SNAP_FACE", text="Stop").action = "stop"

    def draw_header(self, context):
        prop = context.scene.ncnc_pr_head

        row = self.layout.row(align=True)
        row.prop(prop, "tool_scene", text="", expand=True, icon="TOOL_SETTINGS")

        row.separator(factor=1)

        row.prop(prop, "tool_gcode", text="", expand=True, icon="COLOR_GREEN")
        row.prop(prop, "tool_machine", text="", expand=True, icon="PLUGIN")

    def draw_header_preset(self, context):
        self.layout.prop(context.scene.ncnc_pr_head, "tool_vision", text="", expand=True, icon="CAMERA_STEREO")


class NCNC_PT_HeadTextDetails(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "nCNC"
    bl_label = "GCode Details"
    bl_idname = "NCNC_PT_filedetails"
    bl_parent_id = "NCNC_PT_head"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_texts.active_text

    def draw(self, context):
        pr_txs = context.scene.ncnc_pr_texts
        if not pr_txs.active_text:
            return
        pr_txt = pr_txs.active_text.ncnc_pr_text

        layout = self.layout

        row = layout.row(align=True)
        col1 = row.column()
        col1.alignment = "RIGHT"
        col1.label(text="Distance to Travel")
        col1.label(text="Estimated Time")
        col1.label(text="Total Line")
        for i in range(3):
            col1.label(text=f"{round(pr_txt.minimum[i], 1)} || {round(pr_txt.maximum[i], 1)}")

        col2 = row.column(align=False)
        col2.label(text=f"{int(pr_txt.distance_to_travel)} mm")
        col2.label(text=f"{timedelta(seconds=int(pr_txt.estimated_time))}")
        col2.label(text=f"{pr_txt.count}")
        for i in "XYZ":
            col2.label(text=i)

        row = layout.row()
        row.operator("ncnc.textssave", icon="EXPORT", text="Export")


class NCNC_PR_Scene(PropertyGroup):
    def set_mm(self, val):
        unit = bpy.context.scene.unit_settings
        if unit.system != 'METRIC':
            unit.system = 'METRIC'

        if unit.length_unit != 'MILLIMETERS':
            unit.length_unit = 'MILLIMETERS'

    def get_mm(self):
        return bpy.context.scene.unit_settings.length_unit == 'MILLIMETERS'

    mm: BoolProperty(
        name="Milimeters",
        set=set_mm,
        get=get_mm
    )

    def set_inc(self, val):
        unit = bpy.context.scene.unit_settings
        if unit.system != 'IMPERIAL':
            unit.system = 'IMPERIAL'

        if unit.length_unit != 'INCHES':
            unit.length_unit = 'INCHES'

    def get_inc(self):
        return bpy.context.scene.unit_settings.length_unit == 'INCHES'

    inc: BoolProperty(
        name="Inches",
        set=set_inc,
        get=get_inc
    )

    @classmethod
    def register(cls):
        Scene.ncnc_pr_scene = PointerProperty(
            name="NCNC_PR_Head Name",
            description="NCNC_PR_Head Description",
            type=cls
        )

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_scene


class NCNC_OT_Scene(Operator):
    bl_idname = "ncnc.scene"
    bl_label = "NCNC Scene Settings"
    bl_description = "New: Deletes the objects and renewed the workspace\n" \
                     "Mod: Adjust scene settings for nCNC"
    bl_options = {'REGISTER', 'UNDO'}

    newscene: BoolProperty(
        name="New Scene",
        description="Deletes the objects and renewed the workspace",
        default=False
    )

    settings: BoolProperty(
        name="Apply nCNC Scene Settings",
        description="Adjust scene settings",
        default=True
    )

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event=None):

        if self.newscene:
            for i in bpy.data.objects:
                i.ncnc_pr_toolpathconfigs.included = False
            bpy.ops.object.select_all(action='SELECT')
            bpy.ops.object.delete(use_global=False, confirm=False)
            bpy.ops.curve.primitive_bezier_curve_add(radius=20, enter_editmode=False, location=(0, 0, 0))
            bpy.ops.view3d.view_all(center=True)
            context.active_object.ncnc_pr_toolpathconfigs.included = True
            bpy.ops.ncnc.convert()
            self.report({'INFO'}, "Workspace has been renewed for nCNC")

            bpy.context.space_data.overlay.show_extra_edge_length = True

            bpy.ops.view3d.view_axis(type="TOP")
            self.report({'INFO'}, "Applied to nCNC Settings")

        if self.settings:
            unit = context.scene.unit_settings
            spce = context.space_data
            prop = context.scene.ncnc_pr_scene

            if prop.inc:
                prop.inc = True
            else:
                prop.mm = True

            if unit.scale_length != 0.001:
                unit.scale_length = 0.001

            if spce.overlay.grid_scale != 0.001:
                spce.overlay.grid_scale = 0.001

            if spce.clip_end != 10000:
                spce.clip_end = 10000

        return {"FINISHED"}


class NCNC_PT_Scene(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "nCNC"
    bl_label = "Scene"
    bl_idname = "NCNC_PT_scene"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_scene

    def draw(self, context):
        pr_scn = context.scene.ncnc_pr_scene

        row = self.layout.row(align=True)
        col1 = row.column()
        col1.alignment = "RIGHT"
        col1.label(text="Scene")
        col1.label(text="")
        col1.label(text="Units")

        col1.scale_x = 1

        col2 = row.column(align=False)
        col2.operator("ncnc.scene", text="New", icon="FILE_NEW").newscene = True
        col2.operator("ncnc.scene", text="Apply", icon="SETTINGS").settings = True  # "OPTIONS"
        col2.prop(pr_scn, "mm", text="Milimeters")
        col2.prop(pr_scn, "inc", text="Inches")


class nCompute:
    # References;
    # Circle Center;
    # https://blender.stackexchange.com/questions/28239/how-can-i-determine-calculate-a-circumcenter-with-3-points

    @classmethod
    def replace_col(cls, M, i, C):
        for r in range(len(M)):
            M[r][i] = C[r]

    @classmethod
    def circle_center_(cls, B, C, N):
        m_d = Matrix([
            B, C, N
        ])
        col = [B.dot(B) * 0.5,
               C.dot(C) * 0.5,
               0]
        m_x = m_d.copy()
        cls.replace_col(m_x, 0, col)
        m_y = m_d.copy()
        cls.replace_col(m_y, 1, col)
        m_z = m_d.copy()
        cls.replace_col(m_z, 2, col)
        m_d_d = m_d.determinant() or 1
        x = m_x.determinant() / m_d_d
        y = m_y.determinant() / m_d_d
        z = m_z.determinant() / m_d_d

        return Vector([x, y, z])

    @classmethod
    def circle_center(cls, A, B, C):
        B_ = B - A
        C_ = C - A
        N = B_.cross(C_)
        return A + cls.circle_center_(B_, C_, N)


class NCNC_OT_Convert(Operator):
    bl_idname = "ncnc.convert"
    bl_label = "Convert"
    bl_description = "Convert included objects to Gcode"
    bl_options = {'REGISTER'}

    kodlar = []
    shape = 0
    block = 0
    first_point = None

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event=None):
        self.first_point = Vector((0, 0, 0))
        pr_obj = bpy.context.scene.ncnc_pr_objects
        pr_txs = bpy.context.scene.ncnc_pr_texts

        ##################
        # Convert to GCodes
        self.kodlar.clear()
        self.add_header(context)
        self.shape = 0

        last_selected_object = context.active_object

        for obj_orj in pr_obj.items:
            # Copy and select to object
            obj = obj_orj.obj.copy()
            obj.data = obj_orj.obj.data.copy()
            bpy.data.collections[0].objects.link(obj)

            # Do active to object
            bpy.ops.object.select_all(action='DESELECT')
            context.view_layer.objects.active = obj
            obj.select_set(True)

            # To avoid the error in 2D
            obj.data.dimensions = "3D"

            # Reference:
            # https://blender.stackexchange.com/questions/75380/apply-transforms-on-object-copies-not-working
            bpy.ops.object.transform_apply(location=True, scale=True, rotation=True)

            if not obj or not obj.ncnc_pr_toolpathconfigs.included:
                continue

            elif obj.type == 'CURVE':
                # The configurations of the object
                conf = obj.ncnc_pr_toolpathconfigs

                self.dongu = []

                # Steps in the Z axis -> 0.5, 1.0, 1.5, 2.0 ...
                self.dongu.extend([i * conf.step for i in range(1, int(conf.depth / conf.step + 1), )])

                # Calculate last Z step
                if conf.depth % conf.step > 0.01:
                    if len(self.dongu):
                        self.dongu.append(round(self.dongu[-1] + conf.depth % conf.step, conf.round_loca))
                    else:
                        self.dongu.append(round(self.dongu[-1], conf.round_loca))

                self.block = 0
                self.shape += 1

                # Create initial configs of the shape -> Block x.0
                self.add_block(expand="1", enable="1")
                self.kodlar.append(f"{conf.plane} ( Plane Axis )")
                self.kodlar.append(f"S{conf.spindle} ( Spindle )")
                self.kodlar.append(f"( Safe Z : {conf.safe_z} )")
                self.kodlar.append(f"( Step Z : {conf.step} )")
                self.kodlar.append(f"( Total depth : {round(conf.depth, 3)} )")
                self.kodlar.append(f"( Feed Rate -mm/min- : {conf.feed} )")
                self.kodlar.append(f"( Plunge Rate -mm/min- : {conf.plunge} )")

                # Necessary calculations have been made
                # Gcode can now be creating for object
                self.convert_gcode(obj)

            # Remove to volatile object
            bpy.data.collections[0].objects.unlink(obj)
            bpy.data.objects.remove(obj)
        self.add_footer()

        ###########################
        # Create Internal Text File
        file_name = "nCNC"

        if pr_obj.overwrite and file_name in bpy.data.texts.keys():
            bpy.data.texts.remove(bpy.data.texts[file_name])

        str_codes = "\n".join(self.kodlar)
        mytext = bpy.data.texts.new(file_name)
        mytext.write(str_codes)

        pr_txs.texts = mytext.name
        self.report({"INFO"}, "Converted")

        if last_selected_object:
            # Select before object
            bpy.ops.object.select_all(action='DESELECT')
            context.view_layer.objects.active = last_selected_object
            last_selected_object.select_set(True)

        return {"FINISHED"}

    def add_header(self, context):
        self.add_block(name="Header", expand="1", enable="1")
        self.kodlar.append("(Made in Blender by nCNC addons)")
        self.kodlar.append("M3 S1200")
        self.kodlar.append("G4 P1 (Pause 1 second)")
        self.kodlar.append("G21 (All units in mm)")
        # self.kodlar.append("G0 Z5")

    def add_footer(self):
        self.add_block(name="Footer", expand="1", enable="1")
        self.kodlar.append(f"G0 Z{round(self.first_point.z, 3) or 5}")
        self.kodlar.append("M5")
        self.kodlar.append("G0 X0 Y0")
        self.kodlar.append("M2")
        self.kodlar.append("(Total Number of Lines : {})".format(len(self.kodlar)))

    def add_block(self, name=None, expand="0", enable="1"):
        self.kodlar.append("") if len(self.kodlar) > 0 else None
        self.kodlar.append("(Block-name: " + ("Shape{}.{})".format(self.shape, self.block) if not name else name + ")"))
        self.kodlar.append("(Block-expand: %s)" % expand)
        self.kodlar.append("(Block-enable: %s)" % enable)

    def convert_gcode(self, obj):
        for i, subcurve in enumerate(obj.data.splines):  # Curve altındaki tüm Spline'ları sırayla al
            self.block += 1
            self.add_block(expand="0", enable="1")  # Yeni bir blok başlığı ekle

            curvetype = subcurve.type
            print("curvetype", curvetype)

            for j, k in enumerate(self.dongu):
                self.z_adim = Vector((0, 0, k))

                if curvetype == 'NURBS':
                    # Yapım aşamasında !!!

                    # print("curve is closed:", subcurve.use_cyclic_u)
                    xl = []
                    yl = []
                    # for i in range(11):
                    #    a = nVector.bul_nurbs_1t1pl(0.1 * i, context)
                    #    # print(a)
                    #    xl.append(a.x)
                    #    yl.append(a.y)
                    # empty = bpy.data.objects["Empty"].location
                    # print("a",a)
                    # empty.x = a.x
                    # empty.y = a.y
                    # empty.z = a.z

                    # print("a",a)
                    # for nurbspoint in subcurve.points:
                    #    print([nurbspoint.co[0], nurbspoint.co[1], nurbspoint.co[2]], ',')

                # Poly tipindeki Spline'ı convert et
                elif curvetype == 'POLY':
                    self.poly(obj, subcurve)

                # Bezier tipindeki Spline'ı convert et
                elif curvetype == 'BEZIER':
                    self.bezier(obj, subcurve, reverse=j % 2 is 1)

    def bezier(self, obj, subcurve, reverse=False):

        pref = obj.ncnc_pr_toolpathconfigs
        rc = pref.round_circ
        r = pref.round_loca
        z_safe = pref.safe_z
        max_z = -10000
        step_z = obj.ncnc_pr_toolpathconfigs.step

        nokta_sayisi = len(subcurve.bezier_points) - (0 if subcurve.use_cyclic_u else 1)
        nokta_list = []
        for j in range(nokta_sayisi):
            cycle_point = j == nokta_sayisi - 1 and subcurve.use_cyclic_u
            lp = 0 if cycle_point else j + 1  # last point : son nokta

            m1 = subcurve.bezier_points[j].co - self.z_adim
            hr = subcurve.bezier_points[j].handle_right - self.z_adim
            hl = subcurve.bezier_points[lp].handle_left - self.z_adim
            m2 = subcurve.bezier_points[lp].co - self.z_adim

            # Aşağıda yapılan iş şöyle özetlenebilir;
            #   Üstteki m1 ve m2 (baş ve son) noktaları arasından alınan 3 değer ile bir inceleme yapılır
            #   Bu m1, m2 ve diğer 3 değerin;
            #       Bir çember üzerinde mi
            #       Bir doğru üzerinde mi .. olduğu kontrol edilir. Eğer öyleyseler daha az Gkodu elde edilir

            sorgula = [0.25, 0.5, 0.75]
            bak_merkez = []
            bak_dogru = []
            for i in sorgula:
                ps = nVector.bul_bezier_nokta_4p1t(i, m1, hr, hl, m2)
                # print("m1",m1,"m2",m2,"ps",ps)
                # bak_merkez.append(nVector.yuvarla_vector(rc, nVector.bul_cember_merkezi_3p(m1, ps, m2, duzlem="XYZ")))
                bak_merkez.append(nVector.yuvarla_vector(rc, nCompute.circle_center(m1, ps, m2)))
                bak_dogru.append(nVector.bul_dogru_uzerindemi_3p(m1, m2, ps))
                # print("Doğruda mı",nVector.bul_dogru_uzerindemi_3p(m1,m2,ps))
            # print("\n\n")
            if False not in bak_dogru:  # Eğer düz bir doğruysa
                if j == 0:
                    nokta_list.append(m1)
                nokta_list.append(nVector.bul_dogrunun_ortasi_2p(m1, m2))
                nokta_list.append(m2)
            elif bak_merkez[0] == bak_merkez[1] and bak_merkez[1] == bak_merkez[2]:
                if j == 0:
                    nokta_list.append(m1)
                nokta_list.append(nVector.bul_bezier_nokta_4p1t(0.5, m1, hr, hl, m2))
                nokta_list.append(m2)
            else:
                resolution = subcurve.resolution_u
                if resolution % 2 == 1:  # Çözünürlük çift katsayılı yapıldı
                    resolution += 1
                step = 1 / resolution
                for i in range(resolution + 1):
                    o = nVector.bul_bezier_nokta_4p1t(step * i, m1, hr, hl, m2)
                    if i == 0 and j != 0:
                        pass
                    else:
                        nokta_list.append(o)

        if reverse:
            nokta_list.reverse()

        kac_kesit = len(nokta_list) - 2
        for i in range(0, kac_kesit, 2):
            p1 = nokta_list[i]
            p2 = nokta_list[i + 1]
            p3 = nokta_list[i + 2]
            m = nVector.bul_cember_merkezi_3p(p1, p2, p3, duzlem=pref.plane)
            # print(m)
            b = nVector.bul_yonu_1m3p(m, p1, p2, p3)
            I = m.x - p1.x if pref.plane != "G19" else 0
            J = m.y - p1.y if pref.plane != "G18" else 0
            K = m.z - p1.z if pref.plane != "G17" else 0

            # print("p1", p1, "p2", p2, "p3", p3, "m", m, I, J, K)

            limit = 800
            if i == 0:
                # Find Max Z Point
                for nlp in nokta_list:
                    if nlp.z > max_z:
                        max_z = nlp.z + step_z

                if not self.first_point.z:
                    # First Z Position (Safe Z)
                    self.first_point.z = max(max_z + z_safe, z_safe)
                    self.first_point.x = p1.x
                    self.first_point.y = p1.y

                self.kodlar.append(f"G0 Z{round(self.first_point.z, r)}")

                # First XY Pozition
                self.kodlar.append(f"G0 X{round(p1.x, r)} Y{round(p1.y, r)}")

                # Rapid Z, Nearest point
                self.kodlar.append(f"G0 Z{round(max_z + 1, r)}")

                # First Plunge in Z
                self.kodlar.append(f"G1 Z{round(p1.z, r)} F{pref.plunge}")

            if abs(I) > limit or abs(J) > limit or abs(K) > limit:
                # q = "G1 X{1:.{0}f} Y{2:.{0}f} Z{3:.{0}f}".format(r, p2.x, p2.y, p2.z)
                q = "G1 X{1:.{0}f} Y{2:.{0}f} Z{3:.{0}f}".format(r, p3.x, p3.y, p3.z)
            else:
                q = "{1} X{2:.{0}f} Y{3:.{0}f} Z{4:.{0}f} I{5:.{0}f} J{6:.{0}f} K{7:.{0}f}".format(r, b, p3.x, p3.y,
                                                                                                   p3.z, I, J, K)
            if i == 0: q += " F{}".format(pref.feed)
            self.kodlar.append(q)

        # z_pos = max(max_z + z_safe, z_safe)
        # self.kodlar.append(f"G0 Z{round(max_z + 1, r)}")
        # self.kodlar.append("G0 Z{1:.{0}f}".format(r, z_safe))

    def poly(self, obj, subcurve):
        pref = obj.ncnc_pr_toolpathconfigs
        r = pref.round_loca
        z_safe = pref.safe_z
        for i, p in enumerate(subcurve.points):
            # obj2 = obj.copy()
            # p.co.rotate(obj2.rotation_euler)
            # p.co
            # resize
            # resize_3d
            # rotate
            loc = p.co.to_3d() - self.z_adim

            if i == 0:
                self.kodlar.append("G0 Z{1:.{0}f}".format(r, z_safe))
                self.kodlar.append("G0 X{1:.{0}f} Y{2:.{0}f}".format(r, loc.x, loc.y))
                # self.kodlar.append("G0 Z1")
                self.kodlar.append("G1 Z{1:.{0}f} F{2}".format(r, loc.z, pref.plunge))
            else:
                q = "G1 X{1:.{0}f} Y{2:.{0}f} Z{3:.{0}f}".format(r, loc.x, loc.y, loc.z)
                if i == 1: q += " F{}".format(pref.feed)
                self.kodlar.append(q)

        if subcurve.use_cyclic_u:
            loc = subcurve.points[0].co.to_3d() - self.z_adim
            self.kodlar.append("G1 X{1:.{0}f} Y{2:.{0}f} Z{3:.{0}f}".format(r, loc.x, loc.y, loc.z))
            self.kodlar.append("G0 Z{1:.{0}f}".format(r, z_safe))
        else:
            self.kodlar.append("G0 Z{1:.{0}f}".format(r, z_safe))


# #################################
# #################################
# #################################
class NCNC_PR_Connection(PropertyGroup):
    """
    Only CNC Connection Panel Properties
    """

    def get_isconnected(self):
        if dev:
            try:
                dev.inWaiting()
            except:
                return False
        return True if dev else False

    def set_isconnected(self, value):
        """Value : True->Connect,  False->Disconnect"""
        global dev

        if dev:
            try:
                dev.close()
            except:
                ...
            dev = None

        if value:
            try:
                s = Serial(self.ports, self.bauds)
                s.write("\r\n\r\n".encode("ascii"))
                time.sleep(.1)
                s.flushInput()
                dev = s
            except:
                ...

            bpy.ops.ncnc.communication(start=True)
        else:
            bpy.ops.ncnc.communication(start=False)


    def get_ports(self, context):
        return [(i.device, str(i), i.name) for i in comports()]

    isconnected: BoolProperty(
        name="IsConnected",
        description="Is Connected ?",
        default=False,
        get=get_isconnected,
        set=set_isconnected
    )
    ports: EnumProperty(
        name="Select Machine",
        description="Select the machine you want to connect",
        items=get_ports
    )
    bauds: EnumProperty(
        items=[("2400", "2400", ""),
               ("4800", "4800", ""),
               ("9600", "9600", ""),
               ("19200", "19200", ""),
               ("38400", "38400", ""),
               ("57600", "57600", ""),
               ("115200", "115200", ""),
               ("230400", "230400", "")
               ],
        name="Select Baud",
        description="Select the machine you want to connect",
        default="115200"
    )
    controller: EnumProperty(
        items=[("GRBL", "GRBL v1.1 (Tested)", "")],
        name="Controller",
        description="Under development...",
        default="GRBL"
    )

    @classmethod
    def register(cls):
        Scene.ncnc_pr_connection = PointerProperty(
            name="NCNC_PR_Connection Name",
            description="NCNC_PR_Connection Description",
            type=cls
        )

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_connection


class NCNC_OT_Connection(Operator):
    bl_idname = "ncnc.connection"
    bl_label = "Connection"
    bl_description = "Connect / Disconnect"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        pr_con = context.scene.ncnc_pr_connection
        pr_con.isconnected = not pr_con.isconnected

        context.scene.ncnc_pr_vision.dash = pr_con.isconnected
        context.scene.ncnc_pr_vision.mill = pr_con.isconnected

        # Start communication when connected
        # bpy.ops.ncnc.communication(start=pr_con.isconnected)

        bpy.ops.ncnc.decoder(start=pr_con.isconnected)

        return {'FINISHED'}


class NCNC_PT_Connection(Panel):
    bl_idname = "NCNC_PT_connection"
    bl_label = "Connection"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_machine

    def draw(self, context):
        pr_con = context.scene.ncnc_pr_connection

        layout = self.layout
        col = layout.column()
        col.prop(pr_con, "ports", text="Port")
        col.prop(pr_con, "bauds", text="Baud")
        col.prop(pr_con, "controller")

        conn = pr_con.isconnected

        col.operator("ncnc.connection",
                     text=("Connected" if conn else "Connect"),
                     icon=("LINKED" if conn else "UNLINKED"),
                     depress=conn
                     )


# #################################
# #################################
# #################################
class NCNC_PR_MessageItem(PropertyGroup):
    ingoing: BoolProperty(
        name="Ingoing?",
        description="Message is Ingoing / Outgoing"
    )
    message: StringProperty(
        name="Messsage?",
        description="Message"
    )

    # time = time.time()
    # incoming = StringProperty(name="Incoming", default="")

    @classmethod
    def register(cls):
        Scene.ncnc_pr_messageitem = PointerProperty(
            name="NCNC_PR_MessageItem Name",
            description="NCNC_PR_MessageItem Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_messageitem


class NCNC_UL_Messages(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row()
        if item.message.startswith("error:"):
            icon = "FUND"  # "FUND" or "COLORSET_01_VEC"
        elif item.ingoing:
            icon = "BLANK1"
        else:
            icon = "RIGHTARROW_THIN"
        row.prop(item, "message",
                 text="",  # time.strftime(item.time),
                 icon=icon,  # "BLANK1"  "NONE"
                 emboss=False)


class NCNC_OP_Messages(Operator):
    bl_idname = "ncnc.messages"
    bl_label = "Messages Operator"
    bl_description = "Clear Messages in the ListBox"
    bl_options = {'REGISTER'}

    action: EnumProperty(
        items=[
            ("add", "Add to message", ""),
            ("remove", "Remove to message", ""),
            ("clear", "Clear all messages", ""),
            ("clearqueu", "Clear Queu", "")]
    )

    def execute(self, context):

        pr_com = context.scene.ncnc_pr_communication

        if self.action == "add":
            print("Developing ...")

        elif self.action == "remove":
            print("Developing ...")
            pr_com.items.remove(pr_com.active_item_index)

        elif self.action == "clear":
            pr_com.items.clear()
            pr_com.active_item_index = 0

        elif self.action == "clearqueu":
            pr_com.clear_queue()

        return {'FINISHED'}


class NCNC_PR_Communication(PropertyGroup):
    def get_active(self):
        return bpy.context.scene.ncnc_pr_machine.status in ("IDLE", "RUN", "JOG", "CHECK", "HOME", "")

    def run_mode_update(self, context):
        self.isrun = self.run_mode != "stop"

    items: CollectionProperty(
        type=NCNC_PR_MessageItem,
        name="Messages",
        description="All Message Items Collection"
    )
    active_item_index: IntProperty(
        name="Active Item",
        default=-1,
        description="Selected message index in Collection"
    )
    isactive: BoolProperty(
        name='Communication is Active?',
        description='İletişimi durdur veya sürdür',
        default=True,
        get=get_active
    )

    isrun: BoolProperty(default=False)

    run_mode: EnumProperty(
        items=[
            ("stop", "Stop", "Stop and end"),
            ("start", "Run", "Send to GCodes"),
            ("pause", "Pause", "Pause to Send"),
            ("resume", "Resume", "Pause to Sending"),
        ],
        name="Gcode",
        default="stop",
        update=run_mode_update
    )

    ############################################################
    # #################################################### QUEUE
    # Mesaj Kuyruğu
    queue_list = []

    ######################################
    # ############################# Hidden
    # Mesaj Kuyruğu Gizli
    queue_list_hidden = []

    # Cevap Kuyruğu Gizli
    answers = []

    def set_hidden(self, message):
        self.queue_list_hidden.append(message)
        # if len(self.queue_list_hidden) > 10:
        #    _volatile = self.queue_list_hidden[:10]
        #    self.queue_list_hidden.clear()
        #    self.queue_list_hidden.extend(_volatile)

        # print("queue_list_hidden", self.queue_list_hidden)

    def get_answer(self):
        if self.isrun and not len(self.queue_list):
            self.run_mode = "stop"

        return self.answers.pop(0) if len(self.answers) else ""

    ######################################
    # ############################# Hardly
    # Mesaj Kuyruğu zorla
    queue_list_hardly = []

    def set_hardly(self, message):
        self.queue_list_hardly.append(message)

    def clear_queue(self):
        self.queue_list.clear()
        self.queue_list_hidden.clear()

    ############################################################
    # ################################################ MESSAGING
    def update_messaging(self, context):
        if not self.messaging:
            return
        self.send_in_order(self.messaging)
        self.messaging = ""

    messaging: StringProperty(name="Outgoing Message",
                              update=update_messaging)

    ############################################################
    # ################################################## METHODS
    def send_in_order(self, msg=None):
        if not msg:
            return

        if "=" in msg and "$J" not in msg:
            self.set_hidden("$$")

        self.queue_list.append(msg)

    @classmethod
    def register(cls):
        Scene.ncnc_pr_communication = PointerProperty(
            name="NCNC_PR_Communication Name",
            description="NCNC_PR_Communication Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_communication


class NCNC_OT_CommunicationRun(Operator):
    bl_idname = "ncnc.communicationrun"
    bl_label = "Communication Run"
    bl_description = "Communication Description"
    bl_options = {'REGISTER'}

    action: EnumProperty(
        items=[
            ("start", "Start", ""),
            ("pause", "Pause", ""),
            ("resume", "Resume", ""),
            ("stop", "Stop", "")]
    )

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        pr_com = context.scene.ncnc_pr_communication
        pr_txt = context.scene.ncnc_pr_texts.active_text

        if self.action == "start":
            if not pr_txt:
                self.report({'INFO'}, "No Selected Text")
                return {"CANCELLED"}

            for i in pr_txt.as_string().splitlines():
                x = i.strip()
                if not x:  # or (x.startswith("(") and x.endswith(")")):
                    continue
                pr_com.send_in_order(x)

            pr_com.run_mode = "start"

        elif self.action == "pause":
            bpy.ops.ncnc.machine(action="hold")
            pr_com.run_mode = "pause"

        elif self.action == "resume":
            bpy.ops.ncnc.machine(action="resume")
            pr_com.run_mode = "start"

        elif self.action == "stop":
            pr_com.run_mode = "stop"
            bpy.ops.ncnc.machine(action="reset")

        return {'FINISHED'}


# ##########################################################
# ##########################################################
running_modals = {}


def register_modal(self):
    # if exists previous modal (self), stop it
    unregister_modal(self)

    # Register to self
    running_modals[self.bl_idname] = self

    # self.report({'INFO'}, "NCNC Communication: Started")


def unregister_modal(self):
    # Get previous running modal
    self_prev = running_modals.get(self.bl_idname)
    
    try:
        # if exists previous modal (self), stop it
        if self_prev:
            self_prev.inloop = False
            running_modals.pop(self.bl_idname)

            # self.report({'INFO'}, "NCNC Communication: Stopped (Previous Modal)")
    except:
        running_modals.pop(self.bl_idname)

# ##########################################################
# ##########################################################


class NCNC_OT_Communication(Operator):
    bl_idname = "ncnc.communication"
    bl_label = "Communication"
    bl_description = "Communication Description"
    bl_options = {'REGISTER'}

    # Sent Mode (only_read)
    #   0.0: Hardly -> Read
    #   0.1: Hardly -> Write
    #   1.0: Public -> Read
    #   1.1: Public -> Write
    #   2.0: Hidden -> Read
    #   2.1: Hidden -> Write
    sent = 0

    pr_con = None
    pr_com = None
    pr_dev = None

    inloop = True
    delay = 0.1
    _last_time = 0

    start: BoolProperty(default=True)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        # ########################### STANDARD
        if not self.start:
            unregister_modal(self)
            return {'CANCELLED'}
        register_modal(self)
        # ####################################
        # ####################################

        # bpy.app.driver_namespace[self.bl_idname] = self

        self.pr_dev = context.scene.ncnc_pr_machine
        self.pr_con = context.scene.ncnc_pr_connection
        self.pr_com = context.scene.ncnc_pr_communication

        context.window_manager.modal_handler_add(self)

        return self.timer_add(context)

    def timer_add(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(self.delay, window=context.window)
        return {"RUNNING_MODAL"}

    def timer_remove(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        return {'CANCELLED'}

    def modal(self, context, event):
        # ########################### STANDARD
        if not self.inloop:
            if context.area:
                context.area.tag_redraw()
            return self.timer_remove(context)

        if time.time() - self._last_time < self.delay:
            return {'PASS_THROUGH'}

        self._last_time = time.time()

        if not self.pr_con.isconnected:
            unregister_modal(self)
            return self.timer_remove(context)
        # ####################################
        # ####################################

        self.delay = self.contact()

        return {'PASS_THROUGH'}

    def contact(self):
        """return: delay ms -> float"""
        pr_com = self.pr_com
        pr_dev = self.pr_dev

        # READ HARDLY
        if self.sent == 0.0:
            for i in self.read().strip().split("\n"):
                c = i.strip()
                if not c:
                    continue
                item = pr_com.items.add()
                item.ingoing = True
                item.message = c
                pr_com.active_item_index = len(pr_com.items) - 1
                pr_com.answers.append(c)

            self.sent = 3.1
            # print("READ HARDLY", c)

        # READ PUBLIC
        elif self.sent == 1.0:
            for i in self.read().strip().split("\n"):
                c = i.strip()
                if not c:
                    continue
                item = pr_com.items.add()
                item.ingoing = True
                item.message = c
                pr_com.active_item_index = len(pr_com.items) - 1
                pr_com.answers.append(c)

            # One visible code has been sent and read. The queue is in one hidden code.
            self.sent = 2.1

        # READ HIDDEN
        elif self.sent == 2.0:
            c = [i.strip() for i in self.read().strip().split("\n")]
            pr_com.answers.extend(c)

            self.sent = 1.1
            # print("READ HIDDEN", c)

        #############
        # SEND HARDLY
        if len(pr_com.queue_list_hardly):
            code = pr_com.queue_list_hardly.pop(0)
            gi = self.send(code)

            item = pr_com.items.add()
            item.ingoing = False
            item.message = gi
            pr_com.active_item_index = len(pr_com.items) - 1

            self.sent = 0.0
            # print("SEND HARDLY", code, "\n"*5)
            return .1

        if self.sent == 3.1:
            self.sent = 2.1
        elif not pr_com.isactive:
            # print("Communication Passive")
            return 0

        # SEND PUBLIC
        if self.sent == 1.1:
            if len(pr_com.queue_list) and pr_dev.buffer > 10:  # and pr_dev.bufwer > 100
                # If the buffer's remainder is greater than 10, new code can be sent.
                code = pr_com.queue_list.pop(0)
                gi = self.send(code)
                item = pr_com.items.add()
                item.ingoing = False
                item.message = gi
                pr_com.active_item_index = len(pr_com.items) - 1
                self.sent = 1.0

                # "G4 P3" -> 3 sn bekle gibi komutunu bize de uygula
                wait = re.findall('(?<!\()[Gg]0*4 *[pP](\d+\.*\d*)', code)
                if wait:
                    return float(wait[0])
                # print("SEND PUBLIC", code)
                return .2
            else:
                self.sent = 2.1

        # SEND HIDDEN
        if self.sent == 2.1:
            if len(pr_com.queue_list_hidden):
                code = pr_com.queue_list_hidden.pop(0)
                self.send(code)
                self.sent = 2.0
                # print("SEND HIDDEN", code)
                return .1  # if (pr_dev.buffer > 0) and (pr_dev.bufwer > 100) else 1
            else:
                self.sent = 1.1

        return 0

    @classmethod
    def send(cls, msg=None):
        if not dev:
            return
        if not msg:
            msg = "$$"  # Texinput here

        if msg.startswith("0x") or msg.startswith("0X"):
            code = bytearray.fromhex(msg[2:])  # int(msg[2:], 16)
            dev.write(code)
            return msg

        msg = msg.translate(tr_translate).upper()
        dev.write(f"{msg}\n".encode("ascii"))
        return msg

    @classmethod
    def read(cls):
        if not dev:
            return
        a = dev.read_all().decode("utf-8")
        return a


class NCNC_PT_Communication(Panel):
    bl_idname = "NCNC_PT_communication"
    bl_label = "Communication"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"

    # bl_options = {"DEFAULT_CLOSED", "HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_machine

    def draw(self, context):
        layout = self.layout
        pr_com = context.scene.ncnc_pr_communication

        col = layout.column(align=True)
        col.template_list(
            "NCNC_UL_Messages",  # TYPE
            "ncnc_ul_messages",  # ID
            pr_com,  # Data Pointer
            "items",  # Propname
            pr_com,  # active_dataptr
            "active_item_index",  # active_propname
            rows=3,
            type='DEFAULT'
        )

        row = col.row(align=True)

        # if not context.scene.ncnc_pr_connection.isconnected:
        #    row.enabled = False
        #    row.alert = True

        row.prop(pr_com, "messaging", text="", full_event=False)
        row.operator("ncnc.messages", text="", icon="TRASH", ).action = "clear"

        # row = layout.row(align=True)
        # row.label(text=f"Messages -> {len(pr_com.items)}")
        # row.operator("ncnc.messages", text="", icon="TRASH").action = "clear"

        row = layout.row(align=True)
        row.label(text=f"Queue -> Public {len(pr_com.queue_list)}, Hidden {len(pr_com.queue_list_hidden)}")
        row.operator("ncnc.messages", text="", icon="TRASH").action = "clearqueu"


rex_conf = '\$ *(\d*?) *\= *(\d+\.*\d*?)(?:$|\D+.*$)'
"""
>>> re.findall('\$ *(\d*?) *\= *(\d+\.*\d*?)(?:$|\D+.*$)', "$12=34.56 a1b2c3")
$12=34              #->     [('12', '34')]
$ 12 = 34           #->     [('12', '34')]
$12=34.56           #->     [('12', '34.56')]
$12=34.56 a1b2c3    #->     [('12', '34.56')]
"""


def mask(my_int, min_len=3):
    """
    my_int:
        1       -> 001
        15      -> 1111
        ...
    min_len:    minimum_len -> List Count
        1 ->    [ True ]
        2 ->    [ True, True ]
        3 ->    [ True, True, True ]
        ...
    """
    return [b == '1' for b in bin(my_int)[2:].rjust(min_len)[::-1]]


def mask_s10(my_int):
    return str(my_int % 3)


dev_list = {
    "0": int,  # $0=10
    "1": int,  # $1=25
    "2": mask,  # $2=0      # BoolVectorProperty
    "3": mask,  # $3=5      # BoolVectorProperty
    "4": bool,  # $4=0
    "5": bool,  # $5=0
    "6": bool,  # $6=0
    "10": int,  # $10=1
    "11": float,  # $11=0.010
    "12": float,  # $12=0.002
    "13": str,  # $13=0
    "20": bool,  # $20=0
    "21": bool,  # $21=0
    "22": bool,  # $22=0
    "23": mask,  # $23=0    # BoolVectorProperty
    "24": float,  # $24=25.000
    "25": float,  # $25=500.000
    "26": int,  # $26=250
    "27": float,  # $27=1.000
    "30": int,  # $30=1000
    "31": int,  # $31=0
    "100": float,  # $100=800.000
    "101": float,  # $101=800.000
    "102": float,  # $102=800.000
    "110": float,  # $110=500.000
    "111": float,  # $111=500.000
    "112": float,  # $112=500.000
    "120": float,  # $120=10.000
    "121": float,  # $121=10.000
    "122": float,  # $122=10.000
    "130": float,  # $130=200.000
    "131": float,  # $131=200.000
    "132": float,  # $132=200.000
}


class NCNC_OT_Decoder(Operator):
    bl_idname = "ncnc.decoder"
    bl_label = "NCNC Decoder"
    bl_description = "Resolve Receive Codes"
    # bl_options = {'REGISTER'}

    q_count = 0

    ct_reg = None  # Context, Regions
    pr_con = None
    pr_com = None
    pr_dev = None

    inloop = True
    delay = 0.1
    _last_time = 0

    start: BoolProperty(default=True)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        # ########################### STANDARD
        if not self.start:
            unregister_modal(self)
            return {'CANCELLED'}
        register_modal(self)
        context.window_manager.modal_handler_add(self)
        # ####################################
        # ####################################

        self.report({'INFO'}, "NCNC Decoder Started")

        return self.timer_add(context)

    def timer_add(self, context):
        # add to timer
        wm = context.window_manager
        self._timer = wm.event_timer_add(self.delay, window=context.window)
        return {"RUNNING_MODAL"}

    def timer_remove(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        return {'CANCELLED'}

    def modal(self, context, event):
        # ########################### STANDARD
        if not self.inloop:
            if context.area:
                context.area.tag_redraw()
            return self.timer_remove(context)

        if time.time() - self._last_time < self.delay:
            return {'PASS_THROUGH'}

        self._last_time = time.time()
        # ####################################
        # ####################################

        # !!! 3D ViewPort kısmını, sol üstten, TextEditor vs 'ye çevirince, bu kısımda hata çıkıyor.
        # Bu hatayı onar !!!
        # if not context.area:
        #     print(context.area)
        #     return {"CANCELLED"}
        self.ct_reg = context.area.regions
        self.pr_dev = context.scene.ncnc_pr_machine
        self.pr_con = context.scene.ncnc_pr_connection
        self.pr_com = context.scene.ncnc_pr_communication

        if not self.pr_con.isconnected:
            return self.timer_remove(context)

        if not self.pr_com.isactive or self.pr_com.isrun or self.q_count < 5:
            self.decode("?")
            self.q_count += 1
        else:
            self.decode("$G")
            self.q_count = 0

        # self.decode("?")

        return {'PASS_THROUGH'}

    def decode(self, msg="?"):
        if msg:
            if not (len(self.pr_com.queue_list_hidden) and self.pr_com.queue_list_hidden[-1] == msg):
                self.pr_com.set_hidden(msg)
        while 1:
            c = self.pr_com.get_answer()
            if not c:
                break
            c = c.lower()

            # print("get_answer   ->", c)

            if c == "ok":
                """ok : Indicates the command line received was parsed and executed (or set to be executed)."""
                continue
            elif c.startswith("error:"):
                """error:x : Indicated the command line received contained an error, with an error code x, and was 
                purged. See error code section below for definitions."""
                continue
            elif c.startswith("alarm"):
                self.pr_dev.status = c.upper()
                continue
            elif c.startswith("<") and c.endswith(">"):
                """< > : Enclosed chevrons contains status report data.Examples;
                    <Idle|WPos:120.000,50.000,0.000|FS:0,0>
                    <Jog|WPos:94.853,50.000,0.000|FS:500,0>
                """
                self.status_report(c.strip("<>"))
                continue
            elif re.findall("\[gc\:(.*)\]", c):  # c.startswith("[gc") and c.endswith("]"):
                """[gc:g0 g54 g17 g21 g90 g94 m5 m9 t0 f0 s0]"""
                self.modes(re.findall("\[gc\:(.*)\]", c)[0])

            # ############################################### RESOLVE
            # ################################################ $x=val
            # r = [('12', '0.002')]
            for i in re.findall(rex_conf, c):

                # i = ('12', '0.002')
                if i[0] in dev_list.keys():

                    # '12', "0.002"     before -> "$12=0.002"
                    x, val = i

                    # float/int/set/mask
                    conv = dev_list[x]

                    # prop = cls.pr_dev.s1/2/3...
                    local_vars = {}

                    exec(f"p = self.pr_dev.s{x}", {"self": self}, local_vars)
                    prop = local_vars["p"] if conv is not float else round(local_vars["p"], 4)

                    # float("0.002")
                    var = conv(int(val)) if conv in [bool, mask, mask_s10] else conv(val)

                    # [True, False, True]
                    if conv is mask:
                        for k in range(len(var)):
                            if var[k] != prop[k]:
                                exec(f"self.pr_dev.s{x}[{k}] = {var[k]}")
                                # cls.pr_dev[f"s"][k] = var[k]
                    else:
                        if var != prop:
                            if conv in [str, mask_s10]:
                                exec(f'self.pr_dev.s{x} = "{var}"')
                            else:
                                exec(f'self.pr_dev.s{x} = {var}')
                            # cls.pr_dev[f"s{x}"] = var

        if self.ct_reg:
            for region in self.ct_reg:
                if region.type == "UI":
                    region.tag_redraw()

    def status_report(self, code):
        """ >> ?
        Idle|MPos:0.000,0.000,0.000|FS:0,0|WCO:-80.000,-50.000,0.000
        Idle|MPos:0.000,0.000,0.000|FS:0,0|Ov:100,100,100
        Idle|MPos:0.000,0.000,0.000|FS:0,0

        Idle|WPos:0.000,0.000,0.000|FS:0,0

        jog|wpos:90.003,50.000,0.000|bf:15,127|fs:0,0

        Status; Idle, Run, Hold, Jog, Alarm, Door, Check, Home, Sleep
        """

        codes = code.split("|")

        if len(codes):
            self.pr_dev.status = codes.pop(0).upper()

        for i in codes:
            a = i.split(":")[1].split(",")
            for key, var in (("mpos", self.pr_dev.mpos),
                             ("wpos", self.pr_dev.wpos),
                             ("wco", self.pr_dev.wco)):
                if key in i:
                    for j in range(len(a)):
                        var[j] = float(a[j])

            if "fs" in i:
                self.pr_dev.feed = float(a[0])
                self.pr_dev.spindle = float(a[1])
            elif "bf" in i:
                self.pr_dev.buffer = int(a[0])
                self.pr_dev.bufwer = int(a[1])

    def modes(self, code):
        """Mode Group"""
        for c in code.upper().split():
            for key, var in (("motion_mode", ("G0", "G1", "G2", "G3", "G38.2", "G38.3", "G38.4", "G38.5", "G80")),
                             ("coordinate_system", ("G54", "G55", "G56", "G57", "G58", "G59")),
                             ("plane", ("G17", "G18", "G19")),
                             ("distance_mode", ("G90", "G91")),
                             ("arc_ijk_distance", ["G91.1"]),
                             ("feed_rate_mode", ("G93", "G94")),
                             ("units_mode", ("G20", "G21")),
                             ("cutter_radius_compensation", ["G40"]),
                             ("tool_length_offset", ("G43.1", "G49")),
                             ("program_mode", ("M0", "M1", "M2", "M30")),
                             ("spindle_state", ("M3", "M4", "M5")),
                             ("coolant_state", ("M7", "M8", "M9")),
                             ):
                vars = {}

                exec(f"eq = self.pr_dev.{key} == c", {"self": self, "c": c}, vars)

                if c in var and not vars["eq"]:
                    exec(f"self.pr_dev.{key} = c", {"self": self, "c": c}, {})

            if c.startswith("S"):
                self.pr_dev.saved_spindle = float(c[1:])

            elif c.startswith("F"):
                self.pr_dev.saved_feed = float(c[1:])


"""
>>> $$
$0 = 10    (Step pulse time, microseconds)
$1 = 25    (Step idle delay, milliseconds)
$2 = 0    (Step pulse invert, mask)
$3 = 5    (Step direction invert, mask)
$4 = 0    (Invert step enable pin, boolean)
$5 = 0    (Invert limit pins, boolean)
$6 = 0    (Invert probe pin, boolean)
$10 = 0    (Status report options, mask)
$11 = 0.010    (Junction deviation, millimeters)
$12 = 0.002    (Arc tolerance, millimeters)
$13 = 0    (Report in inches, boolean)
$20 = 0    (Soft limits enable, boolean)
$21 = 0    (Hard limits enable, boolean)
$22 = 0    (Homing cycle enable, boolean)
$23 = 0    (Homing direction invert, mask)
$24 = 25.000    (Homing locate feed rate, mm/min)
$25 = 500.000    (Homing search seek rate, mm/min)
$26 = 250    (Homing switch debounce delay, milliseconds)
$27 = 1.000    (Homing switch pull-off distance, millimeters)
$30 = 1000    (Maximum spindle speed, RPM)
$31 = 0    (Minimum spindle speed, RPM)
$32 = 0    (Laser-mode enable, boolean)
$100 = 800.000    (X-axis travel resolution, step/mm)
$101 = 800.000    (Y-axis travel resolution, step/mm)
$102 = 800.000    (Z-axis travel resolution, step/mm)
$110 = 500.000    (X-axis maximum rate, mm/min)
$111 = 500.000    (Y-axis maximum rate, mm/min)
$112 = 500.000    (Z-axis maximum rate, mm/min)
$120 = 10.000    (X-axis acceleration, mm/sec^2)
$121 = 10.000    (Y-axis acceleration, mm/sec^2)
$122 = 10.000    (Z-axis acceleration, mm/sec^2)
$130 = 200.000    (X-axis maximum travel, millimeters)
$131 = 200.000    (Y-axis maximum travel, millimeters)
$132 = 200.000    (Z-axis maximum travel, millimeters)

>>> $G
[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]
"""


# #################################
# #################################
# #################################
class NCNC_PR_Machine(PropertyGroup):
    # ################################################ ?
    status: StringProperty(name="Status")
    """IDLE, JOG, RUN, ALARM:0.., HOLD:0.., DOOR:0..,"""

    wco: FloatVectorProperty(
        name="WCO",
        subtype='XYZ',
        default=[0.0, 0.0, 0.0]
    )

    def wpos_update(self, context):
        if self.pos_type == "mpos":
            for i in range(3):
                self.mpos[i] = self.wpos[i] + self.wco[i]

    # Workspace Position
    wpos: FloatVectorProperty(
        name="WPos",
        subtype='XYZ',
        update=wpos_update,
        default=[0.0, 0.0, 0.0]
    )

    def mpos_update(self, context):
        if self.pos_type == "wpos":
            for i in range(3):
                self.wpos[i] = self.mpos[i] - self.wco[i]

    # Machine Position
    mpos: FloatVectorProperty(
        name="MPos",
        subtype='XYZ',
        update=mpos_update,
        default=[0.0, 0.0, 0.0],
    )
    feed: FloatProperty(
        name="Feed",
        default=0,
        precision=1,
        description="Feed Rate (Current)"
    )
    spindle: FloatProperty(
        name="Spindle",
        default=0,
        precision=1,
        description="Spindle (Current)"
    )
    saved_feed: FloatProperty(
        name="&Feed",
        default=0,
        precision=1,
        description="Feed Rate (Saved) - Only read"
    )
    saved_spindle: FloatProperty(
        name="Saved Spindle",
        default=0,
        precision=1,
        description="Spindle (Saved) - Only read"
    )

    buffer: IntProperty(
        name="Buffer",
        default=15,
        description="""Buffer State:

    Bf:15,128. The first value is the number of available blocks in the planner buffer and the second is number of available bytes in the serial RX buffer.

    The usage of this data is generally for debugging an interface, but is known to be used to control some GUI-specific tasks. While this is disabled by default, GUIs should expect this data field to appear, but they may ignore it, if desired.

    NOTE: The buffer state values changed from showing "in-use" blocks or bytes to "available". This change does not require the GUI knowing how many block/bytes Grbl has been compiled with.

    This data field appears:
        In every status report when enabled. It is disabled in the settings mask by default.

    This data field will not appear if:
        It is disabled by the $ status report mask setting or disabled in the config.h file.

""")

    bufwer: IntProperty(
        name="Buffer Answer on Machine",
        default=15,
        description="""Buffer State:

    Bf:15,128. The first value is the number of available blocks in the planner buffer and the second is number of available bytes in the serial RX buffer.

    The usage of this data is generally for debugging an interface, but is known to be used to control some GUI-specific tasks. While this is disabled by default, GUIs should expect this data field to appear, but they may ignore it, if desired.

    NOTE: The buffer state values changed from showing "in-use" blocks or bytes to "available". This change does not require the GUI knowing how many block/bytes Grbl has been compiled with.

    This data field appears:
        In every status report when enabled. It is disabled in the settings mask by default.

    This data field will not appear if:
        It is disabled by the $ status report mask setting or disabled in the config.h file.

""")

    # ########################################################################## $0
    def s0_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$0={self.s0}")

    s0: IntProperty(
        name="Step pulse (µs)",
        default=10,
        min=1,
        max=255,
        subtype='TIME',
        update=s0_update,
        description="""$0 – Step pulse, microseconds
Stepper drivers are rated for a certain minimum step pulse length. 
Check the data sheet or just try some numbers. You want the shortest 
pulses the stepper drivers can reliably recognize. If the pulses are 
too long, you might run into trouble when running the system at very 
high feed and pulse rates, because the step pulses can begin to 
overlap each other. We recommend something around 10 microseconds, 
which is the default value.""")

    # ########################################################################## $1
    def s1_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$1={self.s1}")

    s1: IntProperty(
        name="Step idle delay (ms)",
        default=25,
        min=0,
        max=255,
        update=s1_update,
        description="""$1 - Step idle delay, milliseconds

Every time your steppers complete a motion and come to a stop, Grbl will delay disabling the steppers by this value. OR, you can always keep your axes enabled (powered so as to hold position) by setting this value to the maximum 255 milliseconds. Again, just to repeat, you can keep all axes always enabled by setting $1=255.

The stepper idle lock time is the time length Grbl will keep the steppers locked before disabling. Depending on the system, you can set this to zero and disable it. On others, you may need 25-50 milliseconds to make sure your axes come to a complete stop before disabling. This is to help account for machine motors that do not like to be left on for long periods of time without doing something. Also, keep in mind that some stepper drivers don't remember which micro step they stopped on, so when you re-enable, you may witness some 'lost' steps due to this. In this case, just keep your steppers enabled via $1=255.""")

    # ########################################################################## $2
    def s2_update(self, context):
        a = 0
        if self.s2[0]:
            a += 1
        if self.s2[1]:
            a += 2
        if self.s2[2]:
            a += 4
        context.scene.ncnc_pr_communication.send_in_order(f"$2={a}")

    s2: BoolVectorProperty(
        name="Step Port",  # Invert
        default=[False, False, False],
        subtype='XYZ',
        update=s2_update,
        description="""$2 – Step port invert, mask
This setting inverts the step pulse signal. By default, a step signal starts at normal-low and goes high upon a step pulse event. After a step pulse time set by $0, the pin resets to low, until the next step pulse event. When inverted, the step pulse behavior switches from normal-high, to low during the pulse, and back to high. Most users will not need to use this setting, but this can be useful for certain CNC-stepper drivers that have peculiar requirements. For example, an artificial delay between the direction pin and step pulse can be created by inverting the step pin.

This invert mask setting is a value which stores the axes to invert as bit flags. You really don't need to completely understand how it works. You simply need to enter the settings value for the axes you want to invert. For example, if you want to invert the X and Z axes, you'd send $2=5 to Grbl and the setting should now read $2=5 (step port invert mask:00000101)""")
    """
    Setting Value 	Mask 	Invert X 	Invert Y 	Invert Z
        0 	      00000000 	    N 	        N 	        N
        1 	      00000001 	    Y 	        N 	        N
        2 	      00000010 	    N 	        Y 	        N
        3 	      00000011 	    Y 	        Y 	        N
        4 	      00000100 	    N 	        N 	        Y
        5 	      00000101 	    Y 	        N 	        Y
        6 	      00000110 	    N 	        Y 	        Y
        7 	      00000111 	    Y 	        Y 	        Y
    """

    # ########################################################################## $3
    def s3_update(self, context):
        a = 0
        if self.s3[0]:
            a += 1
        if self.s3[1]:
            a += 2
        if self.s3[2]:
            a += 4
        context.scene.ncnc_pr_communication.send_in_order(f"$3={a}")

    s3: BoolVectorProperty(
        name="Direction Port",  # Invert
        default=[True, False, True],
        subtype='XYZ',
        update=s3_update,
        description="""$3 – Direction port invert, mask

This setting inverts the direction signal for each axis. By default, Grbl assumes that the axes move in a positive direction when the direction pin signal is low, and a negative direction when the pin is high. Often, axes don't move this way with some machines. This setting will invert the direction pin signal for those axes that move the opposite way.

This invert mask setting works exactly like the step port invert mask and stores which axes to invert as bit flags. To configure this setting, you simply need to send the value for the axes you want to invert. Use the table above. For example, if want to invert the Y axis direction only, you'd send $3=2 to Grbl and the setting should now read $3=2 (dir port invert mask:00000010)""")
    """
    Setting Value 	Mask 	Invert X 	Invert Y 	Invert Z
        0 	      00000000 	    N 	        N 	        N
        1 	      00000001 	    Y 	        N 	        N
        2 	      00000010 	    N 	        Y 	        N
        3 	      00000011 	    Y 	        Y 	        N
        4 	      00000100 	    N 	        N 	        Y
        5 	      00000101 	    Y 	        N 	        Y
        6 	      00000110 	    N 	        Y 	        Y
        7 	      00000111 	    Y 	        Y 	        Y
    """

    # ########################################################################## $4
    def s4_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$4={1 if self.s4 else 0}")

    s4: BoolProperty(
        name="$4 - Step enable invert",
        default=False,
        update=s4_update,
        description="""$4 - Step enable invert, boolean

By default, the stepper enable pin is high to disable and low to enable. If your setup needs the opposite, just invert the stepper enable pin by typing $4=1. Disable with $4=0. (May need a power cycle to load the change.)""")

    # ########################################################################## $5
    def s5_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$5={1 if self.s5 else 0}")

    s5: BoolProperty(
        name="$5 - Limit pins invert",
        default=False,
        update=s5_update,
        description="""$5 - Limit pins invert, boolean

By default, the limit pins are held normally-high with the Arduino's internal pull-up resistor. When a limit pin is low, Grbl interprets this as triggered. For the opposite behavior, just invert the limit pins by typing $5=1. Disable with $5=0. You may need a power cycle to load the change.

NOTE: For more advanced usage, the internal pull-up resistor on the limit pins may be disabled in config.h.""")

    # ########################################################################## $6
    def s6_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$6={1 if self.s6 else 0}")

    s6: BoolProperty(
        name="$6 - Probe pin invert",
        default=False,
        update=s6_update,
        description="""$6 - Probe pin invert, boolean

By default, the probe pin is held normally-high with the Arduino's internal pull-up resistor. When the probe pin is low, Grbl interprets this as triggered. For the opposite behavior, just invert the probe pin by typing $6=1. Disable with $6=0. You may need a power cycle to load the change.""")

    # ########################################################################## $10
    def s10_update(self, context):
        if self.s10 != 2:
            context.scene.ncnc_pr_communication.send_in_order(f"$10=2")

    s10: IntProperty(
        name="$10 - Status report, mask",
        default=2,
        min=0,
        max=255,
        description="$10 - Status report, mask\n0:WPos, 1:MPos, 2:Buf",
        update=s10_update
    )

    # Not CNC Configuration, only select for UI
    pos_type: EnumProperty(
        name="Select Position Mode for Display",
        description="$10 - Status report",  # 0:WPos, 1:MPos, 2:Buf
        default="wpos",
        update=s10_update,
        items=[("wpos", "WPos", "Working Position"),  # "MATPLANE", "SNAP_GRID"
               ("mpos", "MPos", "Machine Position"),  # "ORIENTATION_LOCAL"
               ])
    """
    $10   --> '?' query. Get Position Info
    
    Position Type 	0 	Enable WPos:    Disable MPos:.
    Position Type 	1 	Enable MPos:.   Disable WPos:.
    Buffer Data 	2 	Enabled Buf: field appears with planner and serial RX available buffer.
    """

    # ########################################################################## $11
    def s11_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$11={round(self.s11, 4)}")

    s11: FloatProperty(
        name="Junction deviation (mm)",
        default=0.010,
        precision=3,
        update=s11_update,
        description="""$11 - Junction deviation, mm

Junction deviation is used by the acceleration manager to determine how fast it can move through line segment junctions of a G-code program path. For example, if the G-code path has a sharp 10 degree turn coming up and the machine is moving at full speed, this setting helps determine how much the machine needs to slow down to safely go through the corner without losing steps.

How we calculate it is a bit complicated, but, in general, higher values gives faster motion through corners, while increasing the risk of losing steps and positioning. Lower values makes the acceleration manager more careful and will lead to careful and slower cornering. So if you run into problems where your machine tries to take a corner too fast, decrease this value to make it slow down when entering corners. If you want your machine to move faster through junctions, increase this value to speed it up. For curious people, hit this link to read about Grbl's cornering algorithm, which accounts for both velocity and junction angle with a very simple, efficient, and robust method.""")

    # ########################################################################## $12
    def s12_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$12={round(self.s12, 4)}")

    s12: FloatProperty(
        name="Arc tolerance (mm)",
        default=0.002,
        precision=3,
        update=s12_update,
        description="""$12 – Arc tolerance, mm

Grbl renders G2/G3 circles, arcs, and helices by subdividing them into teeny tiny lines, such that the arc tracing accuracy is never below this value. You will probably never need to adjust this setting, since 0.002mm is well below the accuracy of most all CNC machines. But if you find that your circles are too crude or arc tracing is performing slowly, adjust this setting. Lower values give higher precision but may lead to performance issues by overloading Grbl with too many tiny lines. Alternately, higher values traces to a lower precision, but can speed up arc performance since Grbl has fewer lines to deal with.

For the curious, arc tolerance is defined as the maximum perpendicular distance from a line segment with its end points lying on the arc, aka a chord. With some basic geometry, we solve for the length of the line segments to trace the arc that satisfies this setting. Modeling arcs in this way is great, because the arc line segments automatically adjust and scale with length to ensure optimum arc tracing performance, while never losing accuracy.""")

    # ########################################################################## $13
    def s13_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$13={self.s13}")

    s13: EnumProperty(
        items=[("0", "0: mm", ""),
               ("1", "1: inch", ""),
               ],
        name="Unit Mode",
        default="0",
        update=s13_update,
        description="""$13 - Report inches, boolean

Grbl has a real-time positioning reporting feature to provide a user feedback on where the machine is exactly at that time, as well as, parameters for coordinate offsets and probing. By default, it is set to report in mm, but by sending a $13=1 command, you send this boolean flag to true and these reporting features will now report in inches. $13=0 to set back to mm.""")

    # ########################################################################## $20
    def s20_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$20={1 if self.s20 else 0}")

    s20: BoolProperty(
        name="$20 - Soft limits",
        default=False,
        update=s20_update,
        description="""$20 - Soft limits, boolean

Soft limits is a safety feature to help prevent your machine from traveling too far and beyond the limits of travel, crashing or breaking something expensive. It works by knowing the maximum travel limits for each axis and where Grbl is in machine coordinates. Whenever a new G-code motion is sent to Grbl, it checks whether or not you accidentally have exceeded your machine space. If you do, Grbl will issue an immediate feed hold wherever it is, shutdown the spindle and coolant, and then set the system alarm indicating the problem. Machine position will be retained afterwards, since it's not due to an immediate forced stop like hard limits.

NOTE: Soft limits requires homing to be enabled and accurate axis maximum travel settings, because Grbl needs to know where it is. $20=1 to enable, and $20=0 to disable.""")

    # ########################################################################## $21
    def s21_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$21={1 if self.s21 else 0}")

    s21: BoolProperty(
        name="$21 - Hard limits",
        default=False,
        update=s21_update,
        description="""$21 - Hard limits, boolean

Hard limit work basically the same as soft limits, but use physical switches instead. Basically you wire up some switches (mechanical, magnetic, or optical) near the end of travel of each axes, or where ever you feel that there might be trouble if your program moves too far to where it shouldn't. When the switch triggers, it will immediately halt all motion, shutdown the coolant and spindle (if connected), and go into alarm mode, which forces you to check your machine and reset everything.

To use hard limits with Grbl, the limit pins are held high with an internal pull-up resistor, so all you have to do is wire in a normally-open switch with the pin and ground and enable hard limits with $21=1. (Disable with $21=0.) We strongly advise taking electric interference prevention measures. If you want a limit for both ends of travel of one axes, just wire in two switches in parallel with the pin and ground, so if either one of them trips, it triggers the hard limit.

Keep in mind, that a hard limit event is considered to be critical event, where steppers immediately stop and will have likely have lost steps. Grbl doesn't have any feedback on position, so it can't guarantee it has any idea where it is. So, if a hard limit is triggered, Grbl will go into an infinite loop ALARM mode, giving you a chance to check your machine and forcing you to reset Grbl. Remember it's a purely a safety feature.""")

    # ########################################################################## $22
    def s22_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$22={1 if self.s22 else 0}")

    s22: BoolProperty(
        name="$22 - Homing cycle",
        default=False,
        update=s22_update,
        description="""$22 - Homing cycle, boolean

Ahh, homing. For those just initiated into CNC, the homing cycle is used to accurately and precisely locate a known and consistent position on a machine every time you start up your Grbl between sessions. In other words, you know exactly where you are at any given time, every time. Say you start machining something or are about to start the next step in a job and the power goes out, you re-start Grbl and Grbl has no idea where it is due to steppers being open-loop control. You're left with the task of figuring out where you are. If you have homing, you always have the machine zero reference point to locate from, so all you have to do is run the homing cycle and resume where you left off.

To set up the homing cycle for Grbl, you need to have limit switches in a fixed position that won't get bumped or moved, or else your reference point gets messed up. Usually they are setup in the farthest point in +x, +y, +z of each axes. Wire your limit switches in with the limit pins, add a recommended RC-filter to help reduce electrical noise, and enable homing. If you're curious, you can use your limit switches for both hard limits AND homing. They play nice with each other.

Prior to trying the homing cycle for the first time, make sure you have setup everything correctly, otherwise homing may behave strangely. First, ensure your machine axes are moving in the correct directions per Cartesian coordinates (right-hand rule). If not, fix it with the $3 direction invert setting. Second, ensure your limit switch pins are not showing as 'triggered' in Grbl's status reports. If are, check your wiring and settings. Finally, ensure your $13x max travel settings are somewhat accurate (within 20%), because Grbl uses these values to determine how far it should search for the homing switches.

By default, Grbl's homing cycle moves the Z-axis positive first to clear the workspace and then moves both the X and Y-axes at the same time in the positive direction. To set up how your homing cycle behaves, there are more Grbl settings down the page describing what they do (and compile-time options as well.)

Also, one more thing to note, when homing is enabled. Grbl will lock out all G-code commands until you perform a homing cycle. Meaning no axes motions, unless the lock is disabled ($X) but more on that later. Most, if not all CNC controllers, do something similar, as it is mostly a safety feature to prevent users from making a positioning mistake, which is very easy to do and be saddened when a mistake ruins a part. If you find this annoying or find any weird bugs, please let us know and we'll try to work on it so everyone is happy. :)

NOTE: Check out config.h for more homing options for advanced users. You can disable the homing lockout at startup, configure which axes move first during a homing cycle and in what order, and more.""")

    # ########################################################################## $23
    def s23_update(self, context):
        a = 0
        if self.s23[0]:
            a += 1
        if self.s23[1]:
            a += 2
        if self.s23[2]:
            a += 4
        context.scene.ncnc_pr_communication.send_in_order(f"$23={a}")

    s23: BoolVectorProperty(
        name="Homing Dir",  # Invert
        default=[False, False, False],
        subtype='XYZ',
        update=s23_update,
        description="""$23 - Homing dir invert, mask

By default, Grbl assumes your homing limit switches are in the positive direction, first moving the z-axis positive, then the x-y axes positive before trying to precisely locate machine zero by going back and forth slowly around the switch. If your machine has a limit switch in the negative direction, the homing direction mask can invert the axes' direction. It works just like the step port invert and direction port invert masks, where all you have to do is send the value in the table to indicate what axes you want to invert and search for in the opposite direction.""")

    # ########################################################################## $24
    def s24_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$24={round(self.s24, 4)}")

    s24: FloatProperty(
        name="Homing feed (mm/min)",
        default=25.000,
        precision=3,
        update=s24_update,
        description="""$24 - Homing feed, mm/min

The homing cycle first searches for the limit switches at a higher seek rate, and after it finds them, it moves at a slower feed rate to home into the precise location of machine zero. Homing feed rate is that slower feed rate. Set this to whatever rate value that provides repeatable and precise machine zero locating.""")

    # ########################################################################## $25
    def s25_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$25={round(self.s25, 4)}")

    s25: FloatProperty(
        name="Homing seek (mm/min)",
        default=500.000,
        precision=3,
        update=s25_update,
        description="""$25 - Homing seek, mm/min

Homing seek rate is the homing cycle search rate, or the rate at which it first tries to find the limit switches. Adjust to whatever rate gets to the limit switches in a short enough time without crashing into your limit switches if they come in too fast.""")

    # ########################################################################## $26
    def s26_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$26={self.s26}")

    s26: IntProperty(
        name="Homing debounce (ms)",
        default=250,
        min=10,
        max=1000,
        subtype='TIME',
        update=s26_update,
        description="""$26 - Homing debounce, milliseconds

Whenever a switch triggers, some of them can have electrical/mechanical noise that actually 'bounce' the signal high and low for a few milliseconds before settling in. To solve this, you need to debounce the signal, either by hardware with some kind of signal conditioner or by software with a short delay to let the signal finish bouncing. Grbl performs a short delay, only homing when locating machine zero. Set this delay value to whatever your switch needs to get repeatable homing. In most cases, 5-25 milliseconds is fine.""")

    # ########################################################################## $27
    def s27_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$27={round(self.s27, 4)}")

    s27: FloatProperty(
        name="Homing pull-off (mm)",
        default=1.000,
        precision=3,
        update=s27_update,
        description="""$27 - Homing pull-off, mm

To play nice with the hard limits feature, where homing can share the same limit switches, the homing cycle will move off all of the limit switches by this pull-off travel after it completes. In other words, it helps to prevent accidental triggering of the hard limit after a homing cycle. Make sure this value is large enough to clear the limit switch. If not, Grbl will throw an alarm error for failing to clear it.""")

    # ########################################################################## $30
    def s30_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$30={self.s30}")

    s30: IntProperty(
        name="Max spindle speed (RPM)",
        default=1000,
        min=0,
        max=25000,
        subtype='ANGLE',
        update=s30_update,
        description="""$30 - Max spindle speed, RPM

This sets the spindle speed for the maximum 5V PWM pin output. For example, if you want to set 10000rpm at 5V, program $30=10000. For 255rpm at 5V, program $30=255. If a program tries to set a higher spindle RPM greater than the $30 max spindle speed, Grbl will just output the max 5V, since it can't go any faster. By default, Grbl linearly relates the max-min RPMs to 5V-0.02V PWM pin output in 255 equally spaced increments. When the PWM pin reads 0V, this indicates spindle disabled. Note that there are additional configuration options are available in config.h to tweak how this operates.""")

    # ########################################################################## $31
    def s31_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$31={self.s31}")

    s31: IntProperty(
        name="Min spindle speed (RPM)",
        default=0,
        min=0,
        max=25000,
        subtype='ANGLE',
        update=s31_update,
        description="""$31 - Min spindle speed, RPM

This sets the spindle speed for the minimum 0.02V PWM pin output (0V is disabled). Lower RPM values are accepted by Grbl but the PWM output will not go below 0.02V, except when RPM is zero. If zero, the spindle is disabled and PWM output is 0V.""")

    # ########################################################################## $32
    def s32_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$32={1 if self.s32 else 0}")

    s32: BoolProperty(
        name="$32 - Laser mode",
        default=False,
        update=s32_update,
        description="""$32 - Laser mode, boolean

When enabled, Grbl will move continuously through consecutive G1, G2, or G3 motion commands when programmed with a S spindle speed (laser power). The spindle PWM pin will be updated instantaneously through each motion without stopping. Please read the GRBL laser documentation and your laser machine documentation prior to using this mode. Lasers are very dangerous. They can instantly damage your vision permanantly and cause fires. Grbl does not assume any responsibility for any issues the firmware may cause, as defined by its GPL license.

When disabled, Grbl will operate as it always has, stopping motion with every S spindle speed command. This is the default operation of a milling machine to allow a pause to let the spindle change speeds.""")

    # ########################################################################## $100
    def s100_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$100={round(self.s100, 3)}")

    s100: FloatProperty(
        name="X",
        default=800.000,
        precision=3,
        update=s100_update,
        description="""X-axis travel resolution, step/mm
$100, $101 and $102 – [X,Y,Z] steps/mm

Grbl needs to know how far each step will take the tool in reality. To calculate steps/mm for an axis of your machine you need to know:

    The mm traveled per revolution of your stepper motor. This is dependent on your belt drive gears or lead screw pitch.
    The full steps per revolution of your steppers (typically 200)
    The microsteps per step of your controller (typically 1, 2, 4, 8, or 16). Tip: Using high microstep values (e.g., 16) can reduce your stepper motor torque, so use the lowest that gives you the desired axis resolution and comfortable running properties.

The steps/mm can then be calculated like this: steps_per_mm = (steps_per_revolution*microsteps)/mm_per_rev

Compute this value for every axis and write these settings to Grbl.""")

    # ########################################################################## $101
    def s101_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$101={round(self.s101, 3)}")

    s101: FloatProperty(
        name="Y",
        default=800.000,
        precision=3,
        update=s101_update,
        description="""Y-axis travel resolution, step/mm
$100, $101 and $102 – [X,Y,Z] steps/mm

Grbl needs to know how far each step will take the tool in reality. To calculate steps/mm for an axis of your machine you need to know:

    The mm traveled per revolution of your stepper motor. This is dependent on your belt drive gears or lead screw pitch.
    The full steps per revolution of your steppers (typically 200)
    The microsteps per step of your controller (typically 1, 2, 4, 8, or 16). Tip: Using high microstep values (e.g., 16) can reduce your stepper motor torque, so use the lowest that gives you the desired axis resolution and comfortable running properties.

The steps/mm can then be calculated like this: steps_per_mm = (steps_per_revolution*microsteps)/mm_per_rev

Compute this value for every axis and write these settings to Grbl.""")

    # ########################################################################## $102
    def s102_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$102={round(self.s102, 3)}")

    s102: FloatProperty(
        name="Y",
        default=800.000,
        precision=3,
        update=s102_update,
        description="""Z-axis travel resolution, step/mm
$100, $101 and $102 – [X,Y,Z] steps/mm

Grbl needs to know how far each step will take the tool in reality. To calculate steps/mm for an axis of your machine you need to know:

    The mm traveled per revolution of your stepper motor. This is dependent on your belt drive gears or lead screw pitch.
    The full steps per revolution of your steppers (typically 200)
    The microsteps per step of your controller (typically 1, 2, 4, 8, or 16). Tip: Using high microstep values (e.g., 16) can reduce your stepper motor torque, so use the lowest that gives you the desired axis resolution and comfortable running properties.

The steps/mm can then be calculated like this: steps_per_mm = (steps_per_revolution*microsteps)/mm_per_rev

Compute this value for every axis and write these settings to Grbl.""")

    # ########################################################################## $110
    def s110_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$110={round(self.s110, 3)}")

    s110: FloatProperty(
        name="X",
        default=500.000,
        precision=3,
        update=s110_update,
        description="""X-axis maximum rate, mm/min
$110, $111 and $112 – [X,Y,Z] Max rate, mm/min

This sets the maximum rate each axis can move. Whenever Grbl plans a move, it checks whether or not the move causes any one of these individual axes to exceed their max rate. If so, it'll slow down the motion to ensure none of the axes exceed their max rate limits. This means that each axis has its own independent speed, which is extremely useful for limiting the typically slower Z-axis.

The simplest way to determine these values is to test each axis one at a time by slowly increasing max rate settings and moving it. For example, to test the X-axis, send Grbl something like G0 X50 with enough travel distance so that the axis accelerates to its max speed. You'll know you've hit the max rate threshold when your steppers stall. It'll make a bit of noise, but shouldn't hurt your motors. Enter a setting a 10-20% below this value, so you can account for wear, friction, and the mass of your workpiece/tool. Then, repeat for your other axes.

NOTE: This max rate setting also sets the G0 seek rates.""")

    # ########################################################################## $111
    def s111_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$111={round(self.s111, 3)}")

    s111: FloatProperty(
        name="Y",
        default=500.000,
        precision=3,
        update=s111_update,
        description="""Y-axis maximum rate, mm/min
$110, $111 and $112 – [X,Y,Z] Max rate, mm/min

This sets the maximum rate each axis can move. Whenever Grbl plans a move, it checks whether or not the move causes any one of these individual axes to exceed their max rate. If so, it'll slow down the motion to ensure none of the axes exceed their max rate limits. This means that each axis has its own independent speed, which is extremely useful for limiting the typically slower Z-axis.

The simplest way to determine these values is to test each axis one at a time by slowly increasing max rate settings and moving it. For example, to test the X-axis, send Grbl something like G0 X50 with enough travel distance so that the axis accelerates to its max speed. You'll know you've hit the max rate threshold when your steppers stall. It'll make a bit of noise, but shouldn't hurt your motors. Enter a setting a 10-20% below this value, so you can account for wear, friction, and the mass of your workpiece/tool. Then, repeat for your other axes.

NOTE: This max rate setting also sets the G0 seek rates.""")

    # ########################################################################## $112
    def s112_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$112={round(self.s112, 3)}")

    s112: FloatProperty(
        name="Z",
        default=500.000,
        precision=3,
        update=s112_update,
        description="""Z-axis maximum rate, mm/min
$110, $111 and $112 – [X,Y,Z] Max rate, mm/min

This sets the maximum rate each axis can move. Whenever Grbl plans a move, it checks whether or not the move causes any one of these individual axes to exceed their max rate. If so, it'll slow down the motion to ensure none of the axes exceed their max rate limits. This means that each axis has its own independent speed, which is extremely useful for limiting the typically slower Z-axis.

The simplest way to determine these values is to test each axis one at a time by slowly increasing max rate settings and moving it. For example, to test the X-axis, send Grbl something like G0 X50 with enough travel distance so that the axis accelerates to its max speed. You'll know you've hit the max rate threshold when your steppers stall. It'll make a bit of noise, but shouldn't hurt your motors. Enter a setting a 10-20% below this value, so you can account for wear, friction, and the mass of your workpiece/tool. Then, repeat for your other axes.

NOTE: This max rate setting also sets the G0 seek rates.""")

    # ########################################################################## $120
    def s120_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$120={round(self.s120, 3)}")

    s120: FloatProperty(
        name="X",
        default=10.000,
        precision=3,
        update=s120_update,
        description="""X-axis acceleration, mm/sec^2
$120, $121, $122 – [X,Y,Z] Acceleration, mm/sec^2

This sets the axes acceleration parameters in mm/second/second. Simplistically, a lower value makes Grbl ease slower into motion, while a higher value yields tighter moves and reaches the desired feed rates much quicker. Much like the max rate setting, each axis has its own acceleration value and are independent of each other. This means that a multi-axis motion will only accelerate as quickly as the lowest contributing axis can.

Again, like the max rate setting, the simplest way to determine the values for this setting is to individually test each axis with slowly increasing values until the motor stalls. Then finalize your acceleration setting with a value 10-20% below this absolute max value. This should account for wear, friction, and mass inertia. We highly recommend that you dry test some G-code programs with your new settings before committing to them. Sometimes the loading on your machine is different when moving in all axes together.
""")

    # ########################################################################## $121
    def s121_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$121={round(self.s121, 3)}")

    s121: FloatProperty(
        name="Y",
        default=10.000,
        precision=3,
        update=s121_update,
        description="""Y-axis acceleration, mm/sec^2
$120, $121, $122 – [X,Y,Z] Acceleration, mm/sec^2

This sets the axes acceleration parameters in mm/second/second. Simplistically, a lower value makes Grbl ease slower into motion, while a higher value yields tighter moves and reaches the desired feed rates much quicker. Much like the max rate setting, each axis has its own acceleration value and are independent of each other. This means that a multi-axis motion will only accelerate as quickly as the lowest contributing axis can.

Again, like the max rate setting, the simplest way to determine the values for this setting is to individually test each axis with slowly increasing values until the motor stalls. Then finalize your acceleration setting with a value 10-20% below this absolute max value. This should account for wear, friction, and mass inertia. We highly recommend that you dry test some G-code programs with your new settings before committing to them. Sometimes the loading on your machine is different when moving in all axes together.
""")

    # ########################################################################## $122
    def s122_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$122={round(self.s122, 3)}")

    s122: FloatProperty(
        name="Z",
        default=10.000,
        precision=3,
        update=s122_update,
        description="""Z-axis acceleration, mm/sec^2
$120, $121, $122 – [X,Y,Z] Acceleration, mm/sec^2

This sets the axes acceleration parameters in mm/second/second. Simplistically, a lower value makes Grbl ease slower into motion, while a higher value yields tighter moves and reaches the desired feed rates much quicker. Much like the max rate setting, each axis has its own acceleration value and are independent of each other. This means that a multi-axis motion will only accelerate as quickly as the lowest contributing axis can.

Again, like the max rate setting, the simplest way to determine the values for this setting is to individually test each axis with slowly increasing values until the motor stalls. Then finalize your acceleration setting with a value 10-20% below this absolute max value. This should account for wear, friction, and mass inertia. We highly recommend that you dry test some G-code programs with your new settings before committing to them. Sometimes the loading on your machine is different when moving in all axes together.
""")

    # ########################################################################## $130
    def s130_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$130={round(self.s130, 3)}")

    s130: FloatProperty(
        name="X",
        default=200.000,
        precision=3,
        update=s130_update,
        description="""X-axis maximum travel, millimeters
$130, $131, $132 – [X,Y,Z] Max travel, mm

This sets the maximum travel from end to end for each axis in mm. This is only useful if you have soft limits (and homing) enabled, as this is only used by Grbl's soft limit feature to check if you have exceeded your machine limits with a motion command.""")

    # ########################################################################## $131
    def s131_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$131={round(self.s131, 3)}")

    s131: FloatProperty(
        name="Y",
        default=200.000,
        precision=3,
        update=s131_update,
        description="""Y-axis maximum travel, millimeters
$130, $131, $132 – [X,Y,Z] Max travel, mm

This sets the maximum travel from end to end for each axis in mm. This is only useful if you have soft limits (and homing) enabled, as this is only used by Grbl's soft limit feature to check if you have exceeded your machine limits with a motion command.""")

    # ########################################################################## $132
    def s132_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"$132={round(self.s132, 3)}")

    s132: FloatProperty(
        name="Z",
        default=200.000,
        precision=3,
        update=s132_update,
        description="""Z-axis maximum travel, millimeters
$130, $131, $132 – [X,Y,Z] Max travel, mm

This sets the maximum travel from end to end for each axis in mm. This is only useful if you have soft limits (and homing) enabled, as this is only used by Grbl's soft limit feature to check if you have exceeded your machine limits with a motion command.""")

    # ##############################################################################
    # def motion_mode_update(self, context):
    #    context.scene.ncnc_pr_communication.send_in_order(f"{self.motion_mode}")

    motion_mode: EnumProperty(
        name="Motion Mode",
        default="G0",
        description="Only Read",
        items=[("G0", "G0 - Rapid Move", "G0 - For rapid motion, program G0 axes, where all the axis words are "
                                         "optional. The G0 is optional if the current motion mode is G0. This will "
                                         "produce coordinated motion to the destination point at the maximum rapid "
                                         "rate (or slower). G0 is typically used as a positioning move."),
               ("G1", "G1 - Linear Move",
                "G1 - For linear (straight line) motion at programed feed "
                "rate (for cutting or not), program G1 'axes', "
                "where all the axis words are optional. The G1 is optional "
                "if the current motion mode is G1. This will produce "
                "coordinated motion to the destination point at the "
                "current feed rate (or slower)."),
               ("G2", "G2 - Clockwise Arc Move", "G2 CW - A circular or helical arc is specified "
                                                 "using either G2 (clockwise arc) or G3 ("
                                                 "counterclockwise arc) at the current feed rate. "
                                                 "The direction (CW, CCW) is as viewed from the "
                                                 "positive end of the axis about which the circular "
                                                 "motion occurs."),
               ("G3", "G3 - CounterClockwise Arc Move", "G3 CCW - A circular or helical arc is "
                                                        "specified using either G2 (clockwise arc) "
                                                        "or G3 (counterclockwise arc) at the current "
                                                        "feed rate. The direction (CW, CCW) is as "
                                                        "viewed from the positive end of the axis "
                                                        "about which the circular motion occurs."),
               ("G38.2", "G38.2 - Straight Probe", "G38.2 - probe toward workpiece, stop on contact, signal error if "
                                                   "failure "),
               ("G38.3", "G38.3 - Straight Probe", "G38.3 - probe toward workpiece, stop on contact "),
               ("G38.4", "G38.4 - Straight Probe", "G38.4 - probe away from workpiece, stop on loss of contact, "
                                                   "signal error if failure"),
               ("G38.5", "G38.5 - Straight Probe", "G38.5 - probe away from workpiece, stop on loss of contact"),
               ("G80", "G80 - Cancel Canned Cycle", "G80 - cancel canned cycle modal motion. G80 is part of modal "
                                                    "group 1, so programming any other G code from modal group 1 will"
                                                    " also cancel the canned cycle. "),
               ],
        # update=motion_mode_update
    )

    # ##############################################################################
    def coordinate_system_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.coordinate_system}")

    coordinate_system: EnumProperty(
        name="Coordinate System",
        default="G54",
        update=coordinate_system_update,
        items=[("G54", "G54 - System 1", "Select coordinate system 1"),
               ("G55", "G55 - System 2", "Select coordinate system 2"),
               ("G56", "G56 - System 3", "Select coordinate system 3"),
               ("G57", "G57 - System 4", "Select coordinate system 4"),
               ("G58", "G58 - System 5", "Select coordinate system 5"),
               ("G59", "G59 - System 6", "Select coordinate system 6"),
               # ("G59.1", "G59.1 - System 7", "Select coordinate system 7"),
               # ("G59.2", "G59.2 - System 8", "Select coordinate system 8"),
               # ("G59.3", "G59.3 - System 9", "Select coordinate system 9"),
               ])

    # ##############################################################################
    def distance_mode_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.distance_mode}")

    distance_mode: EnumProperty(
        name="Distance Mode",
        default="G90",
        update=distance_mode_update,
        items=[("G90", "G90 - Absolute", "G90 - Absolute Distance Mode"),
               ("G91", "G91 - Incremental", "91 - Incremental Distance Mode")
               ])

    # ##############################################################################
    def plane_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.plane}")

    plane: EnumProperty(
        name="Plane Select",
        description="These codes set the current plane",
        default="G17",
        update=plane_update,
        items=[
            ("G17", "G17 - XY", ""),
            ("G18", "G18 - ZX", ""),
            ("G19", "G19 - YZ", "")
        ])

    # ##############################################################################
    def arc_ijk_distance_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.arc_ijk_distance}")

    arc_ijk_distance: EnumProperty(
        name="Arc IJK Distance Mode",
        description="Arc Distance Mode",
        default="G91.1",
        update=arc_ijk_distance_update,
        items=[("G91.1", "G91.1", "G91.1 - incremental distance mode for I, J & K offsets. G91.1 Returns I, J & K to "
                                  "their default behavior. ")
               ])

    # ##############################################################################
    def feed_rate_mode_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.feed_rate_mode}")

    feed_rate_mode: EnumProperty(
        name="Feed Rate Mode",
        description="",
        default="G94",
        update=feed_rate_mode_update,
        items=[
            ("G93", "G93 - Inverse Time", "G93 - is Inverse Time Mode. In inverse time feed "
                                          "rate mode, "
                                          "an F word means the move should be completed in [one divided by "
                                          "the F number] minutes. For example, if the F number is 2.0, "
                                          "the move should be completed in half a minute.\nWhen the inverse "
                                          "time feed rate mode is active, an F word must appear on every "
                                          "line which has a G1, G2, or G3 motion, and an F word on a line "
                                          "that does not have G1, G2, or G3 is ignored. Being in inverse "
                                          "time feed rate mode does not affect G0 (rapid move) motions."),
            ("G94", "G94 - Units per Minute", "G94 - is Units per Minute Mode. In units per "
                                              "minute feed mode, "
                                              "an F word is interpreted to mean the controlled point should "
                                              "move at a certain number of inches per minute, millimeters per "
                                              "minute, or degrees per minute, depending upon what length units "
                                              "are being used and which axis or axes are moving. ")
        ])

    # ##############################################################################
    def units_mode_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.units_mode}")

    units_mode: EnumProperty(
        name="Units Mode",
        description="",
        default="G21",
        update=units_mode_update,
        items=[
            ("G20", "G20 - inc", "G20 - to use inches for length units."),
            ("G21", "G21 - mm", "G21 - to use millimeters for length units.")
        ])

    cutter_radius_compensation: EnumProperty(
        name="Cutter Radius Compensation",
        description="",
        default="G40",
        items=[
            ("G40", "G40", "G40 - turn cutter compensation off. If tool "
                           "compensation was on the next move must be a linear "
                           "move and longer than the tool diameter. It is OK to "
                           "turn compensation off when it is already off. ")
        ])

    # ##############################################################################
    def tool_length_offset_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.tool_length_offset}")

    tool_length_offset: EnumProperty(
        name="Tool Length Offset",
        description="",
        default="G49",
        update=tool_length_offset_update,
        items=[
            ("G43.1", "G43.1 - Dynamic", "G43.1 axes - change subsequent motions by "
                                         "replacing the current offset(s) of axes. G43.1 "
                                         "does not cause any motion. The next time a "
                                         "compensated axis is moved, that axis’s "
                                         "endpoint is the compensated location. "),
            ("G49", "G49 - Cancels", "It is OK to program using the same offset already "
                                     "in use. It is also OK to program using no tool "
                                     "length offset if none is currently being used.")
        ])

    # ##############################################################################
    def program_mode_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.program_mode}")

    program_mode: EnumProperty(
        name="Program Mode",
        description="",
        default="M0",
        update=program_mode_update,
        items=[
            ("M0", "M0 - Pause", "M0 - pause a running program temporarily. CNC remains in the "
                                 "Auto Mode so MDI and other manual actions are not enabled. "
                                 "Pressing the resume button will restart the program at the "
                                 "following line. "),
            ("M1", "M1 - Pause", "M1 - pause a running program temporarily if the optional "
                                 "stop switch is on. LinuxCNC remains in the Auto Mode so MDI "
                                 "and other manual actions are not enabled. Pressing the "
                                 "resume button will restart the program at the following "
                                 "line. "),
            ("M2", "M2 - End", 'M2 - end the program. Pressing Cycle Start ("R" in the Axis '
                               'GUI) will restart the program at the beginning of the file. '),
            ("M30", "M30 - End", "M30 - exchange pallet shuttles and end the program. Pressing "
                                 "Cycle Start will start the program at the beginning of the "
                                 "file. ")
        ])

    # ##############################################################################
    def spindle_state_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.spindle_state}")

    spindle_state: EnumProperty(
        name="Spindle State",
        description="Spindle Control",
        default="M5",
        update=spindle_state_update,
        items=[
            ("M3", "M3 - Start CW", "M3 - start the spindle clockwise at the S speed."),
            ("M4", "M4 - Start CCW", "M4 - start the spindle counterclockwise at the S speed."),
            ("M5", "M5 - Stop", "M5 - stop the spindle. ")
        ])

    # ##############################################################################
    def coolant_state_update(self, context):
        context.scene.ncnc_pr_communication.send_in_order(f"{self.coolant_state}")

    coolant_state: EnumProperty(
        name="Coolant State",
        description="",
        default="M9",
        update=coolant_state_update,
        items=[
            ("M7", "M7 - turn mist coolant on", "M7 - turn mist coolant on. M7 controls "
                                                "iocontrol.0.coolant-mist pin. "),
            ("M8", "M8 - turn flood coolant on", "M8 - turn flood coolant on. M8 controls "
                                                 "iocontrol.0.coolant-flood pin."),
            ("M9", "M9 - turn off", "M9 - turn both M7 and M8 off. ")
        ])

    @classmethod
    def register(cls):
        Scene.ncnc_pr_machine = PointerProperty(
            name="NCNC_PR_Machine Name",
            description="NCNC_PR_Machine Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_machine


class NCNC_OT_Machine(Operator):
    bl_idname = "ncnc.machine"
    bl_label = "Machine Controls"
    bl_description = "Machine Controllers"
    bl_options = {'REGISTER'}

    action: EnumProperty(items=[
        ("bos", "", ""),
        ("reset", "Soft Reset", "Immediately halts and safely resets Grbl without a power-cycle."
                                "Accepts and executes this command at any time."),
        ("resume", "Cycle Start / Resume", "Resumes a feed hold, a safety door/parking state when the door is closed, "
                                           "and the M0 program pause states."),
        ("hold", "Feed Hold", "Places Grbl into a suspend or HOLD state. If in motion, the machine will decelerate to "
                              "a stop and then be suspended.Command executes when Grbl is in an IDLE, RUN, "
                              "or JOG state. It is otherwise ignored."),
        ("door", "Safety Door", "Although typically connected to an input pin to detect the opening of a safety door, "
                                "this command allows a GUI to enact the safety door behavior with this command."),
        ("cancel", "Jog Cancel", "Immediately cancels the current jog state by a feed hold and automatically flushing "
                                 "any remaining jog commands in the buffer. Command is ignored, if not in a JOG state "
                                 "or if jog cancel is already invoked and in-process. Grbl will return to the IDLE "
                                 "state or the DOOR state, if the safety door was detected as ajar during the "
                                 "cancel."),
        ("unlock", "Kill alarm lock", "Grbl's alarm mode is a state when something has gone critically wrong, "
                                      "such as a hard limit or an abort during a cycle, or if Grbl doesn't know its "
                                      "position. By default, if you have homing enabled and power-up the Arduino, "
                                      "Grbl enters the alarm state, because it does not know its position. The alarm "
                                      "mode will lock all G-code commands until the '$H' homing cycle has been "
                                      "performed. Or if a user needs to override the alarm lock to move their axes "
                                      "off their limit switches, for example, '$X' kill alarm lock will override the "
                                      "locks and allow G-code functions to work again."),

        ("sleep", "Sleep", "This command will place Grbl into a de-powered sleep state, shutting down the spindle, "
                           "coolant, and stepper enable pins and block any commands. It may only be exited by a "
                           "soft-reset or power-cycle. Once re-initialized, Grbl will automatically enter an ALARM "
                           "state, because it's not sure where it is due to the steppers being disabled."),
        ("run", "Run", ""),
    ])

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        pr_com = context.scene.ncnc_pr_communication
        pr_vis = context.scene.ncnc_pr_vision

        if self.action == "run":
            if not pr_vis.texts:
                self.report({'INFO'}, "No Selected Text")
                return {"CANCELLED"}
            txt = bpy.data.texts[pr_vis.texts]

            for i in txt.as_string().splitlines():
                x = i.strip()
                if not x or (x.startswith("(") and x.endswith(")")):
                    continue
                pr_com.send_in_order(x)

        elif self.action == "reset":
            pr_com.set_hardly("0x18")
            pr_com.set_hardly("$X")
            pr_com.clear_queue()

        elif self.action == "resume":
            pr_com.set_hardly("~")

        elif self.action == "hold":
            pr_com.set_hardly("!")

        elif self.action == "door":
            pr_com.set_hardly("0x84")

        elif self.action == "cancel":
            pr_com.set_hardly("0x85")

        elif self.action == "unlock":
            pr_com.set_hardly("$X")

        elif self.action == "sleep":
            pr_com.set_hardly("$SLP")

        return {'FINISHED'}


class NCNC_PT_Machine(Panel):
    bl_idname = "NCNC_PT_machine"
    bl_label = "Machine"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_machine

    def draw(self, context):
        layout = self.layout

        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False

        status = context.scene.ncnc_pr_machine.status

        row = layout.row()
        row.alert = status.startswith("ALARM") or status in ("HOLD:0", "SLEEP", "DOOR:0")
        row.operator("ncnc.machine", text="Reset", icon="FILE_REFRESH", ).action = "reset"
        row.alert = status in ("ALARM:3")
        row.operator("ncnc.machine", text="Unlock", icon="UNLOCKED", ).action = "unlock"
        row = layout.row()
        row.operator("ncnc.machine", text="Hold!", icon="PAUSE", ).action = "hold"

        row.alert = status in ("HOLD:0", "HOLD:1", "DOOR:0")
        row.operator("ncnc.machine", text="Resume", icon="PLAY", ).action = "resume"

        row = layout.row()
        row.operator("ncnc.machine", text="Sleep", icon="SORTTIME", ).action = "sleep"
        row.operator("ncnc.machine", text="Door", icon="ARMATURE_DATA", ).action = "door"

    def draw_header(self, context):
        status = context.scene.ncnc_pr_machine.status
        if status.startswith("ALARM") or status in ("HOLD:0", "SLEEP", "DOOR:0"):
            self.layout.operator("ncnc.machine", text="", icon="FILE_REFRESH", ).action = "reset"


class NCNC_PT_MachineDash(Panel):
    bl_idname = "NCNC_PT_machinedash"
    bl_label = "Dashboard"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machine"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        props = context.scene.ncnc_pr_machine

        layout = self.layout

        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False

        # STATUS
        row = layout.row()
        row.alert = True
        row.operator("ncnc.empty", text=f"{props.status}")

        col = layout.column(align=True)

        # POS MODE
        row = col.row(align=True)
        row.prop(props, "pos_type", expand=True, )
        row.scale_y = 0.8

        pos = props.mpos if props.pos_type == "mpos" else props.wpos

        # POS LABEL
        row = col.row(align=True)
        row.alert = True
        row.operator("ncnc.empty", text="X", depress=True)  # emboss=True,
        row.operator("ncnc.empty", text="Y", depress=True)  # emboss=True,
        row.operator("ncnc.empty", text="Z", depress=True)  # emboss=True,

        # POS
        row = layout.row(align=True)
        row.operator("ncnc.empty", text=f"{round(pos[0], 2)}", emboss=False)  # , depress=True
        row.operator("ncnc.empty", text=f"{round(pos[1], 2)}", emboss=False)  # , depress=True
        row.operator("ncnc.empty", text=f"{round(pos[2], 2)}", emboss=False)  # , depress=True

        # SPLIT
        row = layout.split()

        # LABELS
        row = layout.row(align=True)
        row.alert = True
        row.operator("ncnc.empty", text="Feed", depress=True)  # emboss=False,
        row.operator("ncnc.empty", text="Spindle", depress=True)  # emboss=False,
        row.operator("ncnc.empty", text="Buffer", depress=True)  # emboss=False,
        row.enabled = True

        # VALS
        row = layout.row(align=True)
        row.operator("ncnc.empty", text=f"{props.feed}", emboss=False)
        row.operator("ncnc.empty", text=f"{props.spindle}", emboss=False)
        row.operator("ncnc.empty", text=f"{props.buffer},{props.bufwer}", emboss=False)

    def draw_header(self, context):
        context.scene.ncnc_pr_vision.prop_bool(self.layout, "dash")


class NCNC_PT_MachineModes(Panel):
    bl_idname = "NCNC_PT_machinemodes"
    bl_label = "Modes"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machine"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        props = context.scene.ncnc_pr_machine

        layout = self.layout

        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False

        row = layout.row(heading="Motion Mode")
        row.prop(props, "motion_mode", text="")

        row = layout.row(heading="Coordinate System")
        row.prop(props, "coordinate_system", text="")

        row = layout.row(heading="Distance Mode")
        row.prop(props, "distance_mode", text="")

        row = layout.row(heading="Plane")
        row.prop(props, "plane", text="")

        row = layout.row(heading="Feed Rate Mode")
        row.prop(props, "feed_rate_mode", text="")

        row = layout.row(heading="Units Mode")
        row.prop(props, "units_mode", text="")

        row = layout.row(heading="Spindle State")
        row.prop(props, "spindle_state", text="")

        row = layout.row(heading="Coolant State")
        row.prop(props, "coolant_state", text="")

        row = layout.row(heading="Saved Feed")
        row.prop(props, "saved_feed", text="")
        # row.enabled = False

        row = layout.row(heading="Saved Spindle")
        row.prop(props, "saved_spindle", text="")
        # row.enabled = False

        # row = layout.row(heading="Cutter Radius Compensation")
        # row.prop(props, "cutter_radius_compensation", text="")

        # row = layout.row(heading="Arc Distance")
        # row.prop(props, "arc_ijk_distance", text="")

        # row = layout.row(heading="Tool Length Offset")
        # row.prop(props, "tool_length_offset", text="")

        # row = layout.row(heading="Program Mode")
        # row.prop(props, "program_mode", text="")


class NCNC_PT_MachineDetails(Panel):
    bl_idname = "NCNC_PT_machinedetails"
    bl_label = "Configs"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machine"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        pass


class NCNC_PT_MachineDetail(Panel):
    bl_label = "Detail"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machinedetails"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        props = context.scene.ncnc_pr_machine

        layout = self.layout

        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False

        row = layout.row(heading="Motion Mode")
        row.prop(props, "motion_mode", text="")

        # ### Numeric
        col = layout.column(align=True)
        col.prop(props, "s0")
        col.prop(props, "s1")
        col.prop(props, "s11")
        col.prop(props, "s12")
        col.prop(props, "s24")
        col.prop(props, "s25")
        col.prop(props, "s26")
        col.prop(props, "s27")
        col.prop(props, "s30")
        col.prop(props, "s31")


class NCNC_PT_MachineDetailInvert(Panel):
    bl_label = "Detail: Invert"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machinedetails"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        props = context.scene.ncnc_pr_machine

        layout = self.layout
        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False

        col = layout.column(align=False)
        col.alignment = "RIGHT"
        col.prop(props, "s4")
        col.prop(props, "s5")
        col.prop(props, "s6")
        col.prop(props, "s20")
        col.prop(props, "s21")
        col.prop(props, "s22")
        col.prop(props, "s32")


class NCNC_PT_MachineDetailAxis(Panel):
    bl_label = "Detail: Invert Axis"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machinedetails"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        props = context.scene.ncnc_pr_machine

        layout = self.layout

        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False

        # row = layout.column(align=False)
        """
        row = layout.row(align=False)
        col = row.column()
        col.prop(props, "s2")
        col = row.column()
        col.prop(props, "s3")
        col = row.column()
        col.prop(props, "s23")"""

        col = layout.column(align=False)

        col.label(text="Step Port Invert:")
        row = col.row()
        row.prop(props, "s2", text="")

        col.label(text="Direction Port Invert:")
        row = col.row()
        row.prop(props, "s3", text="")

        col.label(text="Homing Dir Invert:")
        row = col.row()
        row.prop(props, "s23", text="")


class NCNC_PT_MachineDetailAxisInvert(Panel):
    bl_label = "Detail: Axis"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_machinedetails"
    bl_options = {'DEFAULT_CLOSED'}  # DEFAULT_CLOSED

    def draw(self, context):
        props = context.scene.ncnc_pr_machine

        layout = self.layout
        if not context.scene.ncnc_pr_connection.isconnected:
            layout.enabled = False
            # return

        col = layout.column(align=True, heading="Axis Travel Resolution (step/mm)")
        col.prop(props, "s100")
        col.prop(props, "s101")
        col.prop(props, "s102")

        col = layout.column(align=True, heading="Axis Maximum Rate (mm/min)")
        col.prop(props, "s110")
        col.prop(props, "s111")
        col.prop(props, "s112")

        col = layout.column(align=True, heading="Axis Acceleration (mm/sec^2)")
        col.prop(props, "s120")
        col.prop(props, "s121")
        col.prop(props, "s122")

        col = layout.column(align=True, heading="Axis Maximum Travel (mm)")
        col.prop(props, "s130")
        col.prop(props, "s131")
        col.prop(props, "s132")


# #################################
# #################################
# #################################
class NCNC_PR_JogController(PropertyGroup):
    def update_spindle_speed(self, context):
        pr_com = context.scene.ncnc_pr_communication
        pr_com.send_in_order(f"S{self.spindle_speed}")

    def update_spindle_state(self, context):
        pr_com = context.scene.ncnc_pr_communication
        pr_mac = context.scene.ncnc_pr_machine
        if pr_mac.spindle_state not in ("M3", "M4"):
            pr_com.send_in_order(f"M3 S{self.spindle_speed}")

        else:
            pr_com.send_in_order(f"M5")

    # Auto Update On/Off BUTTON
    step_size_xy: FloatProperty(
        name="Step Size XY",
        step=200,
        default=10.000
    )
    step_size_z: FloatProperty(
        name="Step Size Z",
        step=100,
        default=1.0
    )
    feed_rate: IntProperty(
        name="Feed",
        step=50,
        default=500,
        description="Feed Rate"
    )
    spindle_speed: IntProperty(
        name="Spindle",
        default=1000,
        step=200,
        min=0,
        max=75000,
        description="Current Speed",
        update=update_spindle_speed
    )
    spindle_state: BoolProperty(
        name="Spindle On/Off",
        default=False,
        description="Start / Stop",
        update=update_spindle_state
    )

    @classmethod
    def register(cls):
        Scene.ncnc_pr_jogcontroller = PointerProperty(
            name="NCNC_PR_JogController Name",
            description="NCNC_PR_JogController Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_jogcontroller


class NCNC_OT_JogController(Operator):
    bl_idname = "ncnc.jogcontroller"
    bl_label = "Jog Control Operators"
    bl_description = "Jog Control Operators,\nMove X / Y / Z"
    bl_options = {'REGISTER'}

    action: EnumProperty(name="Jog Controller",
                         items=[("x+", "X+", "TRIA_RIGHT"),  # EVENT_X
                                ("x-", "X-", "TRIA_LEFT"),  # EVENT_Y
                                ("y+", "Y+", "TRIA_UP"),
                                ("y-", "Y-", "TRIA_DOWN"),
                                ("z+", "Z+", "TRIA_UP"),
                                ("z-", "Z-", "TRIA_DOWN"),

                                ("x+y+", "X+ Y+", "DOT"),
                                ("x+y-", "X+ Y-", "DOT"),
                                ("x-y+", "X- Y+", "DOT"),
                                ("x-y-", "X- Y-", "DOT"),

                                ("x0y0", "X0 Y0", "DOT"),
                                ("z0", "Z0", "DOT"),

                                ("0xy", "XY:0", "XY:0"),
                                ("0x", "X:0", "X:0"),
                                ("0y", "Y:0", "Y:0"),
                                ("0z", "Z:0", "Z:0"),
                                ("home", "Home", "Home: XYZ"),
                                ("safez", "Safe Z", "Safe Z: 5mm"),
                                ("cancel", "Jog Cancel", "Jog Cancel and Clear"),
                                ("mousepos", "Mouse Pos", "Set mouse pos")
                                ])

    def invoke(self, context, event=None):

        pr_dev = context.scene.ncnc_pr_machine
        pr_jog = context.scene.ncnc_pr_jogcontroller
        pr_com = context.scene.ncnc_pr_communication

        if self.action == "x+":
            pr_com.send_in_order(f"$J=G21 G91 X{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "x-":
            pr_com.send_in_order(f"$J=G21 G91 X-{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "y+":
            pr_com.send_in_order(f"$J=G21 G91 Y{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "y-":
            pr_com.send_in_order(f"$J=G21 G91 Y-{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "z+":
            pr_com.send_in_order(f"$J=G21 G91 Z{pr_jog.step_size_z} F{pr_jog.feed_rate}")
        elif self.action == "z-":
            pr_com.send_in_order(f"$J=G21 G91 Z-{pr_jog.step_size_z} F{pr_jog.feed_rate}")
        elif self.action == "x+y+":
            pr_com.send_in_order(f"$J=G21 G91 X{pr_jog.step_size_xy} Y{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "x+y-":
            pr_com.send_in_order(f"$J=G21 G91 X{pr_jog.step_size_xy} Y-{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "x-y+":
            pr_com.send_in_order(f"$J=G21 G91 X-{pr_jog.step_size_xy} Y{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "x-y-":
            pr_com.send_in_order(f"$J=G21 G91 X-{pr_jog.step_size_xy} Y-{pr_jog.step_size_xy} F{pr_jog.feed_rate}")
        elif self.action == "x0y0":
            pos = pr_dev.mpos if pr_dev.pos_type == "mpos" else pr_dev.wpos
            if pos[2] < 3:
                pr_com.send_in_order(f"$J=G21 G90 Z3 F{pr_jog.feed_rate}")
            # pr_com.send_in_order(f"$J=G21 G91 X{round(pos[0], 3) * -1}Y{round(pos[1], 3) * -1}F{pr_jog.feed_rate}")
            pr_com.send_in_order(f"$J=G21 G90 X0 Y0 F{pr_jog.feed_rate}")
        elif self.action == "z0":
            pos = pr_dev.mpos if pr_dev.pos_type == "mpos" else pr_dev.wpos
            pr_com.send_in_order(f"$J=G21G91Z{round(pos[2], 3) * -1}F{pr_jog.feed_rate}")

        # #### Reset Zero XYZ
        elif self.action == "0xy":
            pr_com.send_in_order("G10 L20 X0 Y0")
        elif self.action == "0x":
            pr_com.send_in_order("G10 L20 X0")
        elif self.action == "0y":
            pr_com.send_in_order("G10 L20 Y0")
        elif self.action == "0z":
            pr_com.send_in_order("G10 L20 Z0")

        elif self.action == "home":
            pos = pr_dev.mpos if pr_dev.pos_type == "mpos" else pr_dev.wpos
            if pos[2] < 3:
                pr_com.send_in_order(f"$J=G21 G90 Z3 F{pr_jog.feed_rate}")
            pr_com.send_in_order(f"$J=G21 G90 X0 Y0 F{pr_jog.feed_rate}")
            pr_com.send_in_order(f"$J=G21 G90 Z0 F{pr_jog.feed_rate}")
        elif self.action == "safez":
            pos = pr_dev.mpos if pr_dev.pos_type == "mpos" else pr_dev.wpos
            pr_com.send_in_order(f"$J=G21 G90 Z5 F{pr_jog.feed_rate}")
        elif self.action == "cancel":
            pr_com.set_hardly("0x85")

        elif self.action == "mousepos":
            # context.region
            # bpy.ops.view3d.view_axis(type="TOP")
            context.window_manager.modal_handler_add(self)
            self.draw_handle_2d = bpy.types.SpaceView3D.draw_handler_add(self.draw_callback_2d,
                                                                         (self, context),
                                                                         "WINDOW",
                                                                         "POST_PIXEL")
            return {"RUNNING_MODAL"}
        return {"FINISHED"}

    def modal(self, context, event):
        if event.type == "LEFTMOUSE":
            # print("Mouse     ; ", event.mouse_x, event.mouse_y)
            # print("Mouse Prev; ", event.mouse_prev_x, event.mouse_prev_y)
            # print("Mouse Regn; ", event.mouse_region_x, event.mouse_region_y)

            for area in context.window.screen.areas:

                if area.type != 'VIEW_3D':
                    continue

                if area.x < event.mouse_x < area.x + area.width and area.y < event.mouse_y < area.y + area.height:

                    active_region = None
                    active_region_3d = None

                    ##############
                    # on Quad View
                    if len(area.spaces.active.region_quadviews):
                        #  +-----------------+
                        #  | quad 1 | quad 3 |
                        #  |--------+--------|
                        #  | quad 0 | quad 2 |
                        #  +-----------------+
                        quad_index = -1
                        for region in area.regions:

                            if region.type == "WINDOW":

                                quad_index += 1
                                if (region.x <= event.mouse_x < region.width + region.x) and \
                                        (region.y <= event.mouse_y < region.height + region.y):
                                    active_region = region
                                    active_region_3d = area.spaces.active.region_quadviews[quad_index]

                                    break

                    #####################
                    # on Normal View (3D)
                    else:
                        for region in area.regions:
                            if region.type == "WINDOW":
                                active_region = region
                                break
                        active_region_3d = area.spaces[0].region_3d

                    if not (active_region and active_region_3d):
                        self.report({'WARNING'}, "View should be [TOP, LEFT, RIGHT ...]")
                        return {'CANCELLED'}

                    m_pos = (event.mouse_x - region.x, event.mouse_y - region.y)
                    origin = region_2d_to_origin_3d(active_region, active_region_3d, m_pos)
                    direction = region_2d_to_vector_3d(active_region, active_region_3d, m_pos)

                    # print(origin, direction)
                    # print("Area     ;", area)
                    # print("Region   ;", active_region)
                    # print("Region3D ;", active_region_3d)
                    # print("Origin   ;", origin)
                    # print("Direction;", direction)

                    pr_jog = context.scene.ncnc_pr_jogcontroller
                    pr_com = context.scene.ncnc_pr_communication

                    at = ""

                    # ##################
                    # Move XY - TOP VIEW
                    if direction[2] == -1:
                        at = f"X{round(origin[0], 2)} Y{round(origin[1], 2)}"

                    # #####################
                    # Move XY - BOTTOM VIEW
                    if direction[2] == 1:
                        at = f"X{round(origin[0], 2)} Y{round(origin[1], 2)}"

                    # ####################
                    # Move XZ - FRONT VIEW
                    elif direction[1] == 1:
                        at = f"X{round(origin[0], 2)} Z{round(origin[2], 2)}"

                    # ###################
                    # Move XZ - BACK VIEW
                    elif direction[1] == -1:
                        at = f"X{round(origin[0], 2)} Z{round(origin[2], 2)}"

                    # ####################
                    # Move YZ - RIGHT VIEW
                    elif direction[0] == -1:
                        at = f"Y{round(origin[1], 2)} Z{round(origin[2], 2)}"

                    # ###################
                    # Move YZ - LEFT VIEW
                    elif direction[0] == 1:
                        at = f"Y{round(origin[1], 2)} Z{round(origin[2], 2)}"

                    if at:
                        pr_com.send_in_order(f"$J=G21 G90 {at} F{pr_jog.feed_rate}")
                    else:
                        self.report({'WARNING'}, "View should be [TOP, LEFT, RIGHT ...]")

                    break

        if event.value == "PRESS" or event.type == "ESC":
            bpy.types.SpaceView3D.draw_handler_remove(self.draw_handle_2d, "WINDOW")
            if context.area:
                context.area.tag_redraw()
            return {'CANCELLED'}

        return {"PASS_THROUGH"}

    def draw_callback_2d(self, op, context):
        # Draw text to indicate that draw mode is active
        region = context.region
        text = "- Move: Mouse Left Click (inView: TOP, LEFT, RIGHT ...)-"
        subtext = "Close: Press Anything"

        xt = int(region.width / 2.0)

        blf.size(0, 24, 72)
        blf.position(0, xt - blf.dimensions(0, text)[0] / 2, 60, 0)
        blf.draw(0, text)

        blf.size(1, 20, 72)
        blf.position(1, xt - blf.dimensions(0, subtext)[0] / 2, 30, 1)
        blf.draw(1, subtext)

        # Draw handler to paint onto the screen


class NCNC_PT_JogController(Panel):
    bl_idname = "NCNC_PT_jogcontroller"
    bl_label = "Jog"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_machine

    def draw(self, context):
        if not context.scene.ncnc_pr_connection.isconnected:
            self.layout.enabled = False

        pr_jog = context.scene.ncnc_pr_jogcontroller
        layout = self.layout

        row_jog = layout.row(align=True)
        row_jog.scale_y = 1.8
        col = row_jog.column(align=True)
        col.operator("ncnc.jogcontroller", text="", icon="DOT").action = "x-y+"
        col.operator("ncnc.jogcontroller", text="", icon="TRIA_LEFT").action = "x-"
        col.operator("ncnc.jogcontroller", text="", icon="DOT").action = "x-y-"
        zero = col.split()
        zero.operator("ncnc.jogcontroller", text="X:0").action = "0x"
        zero.scale_y = 0.5

        col = row_jog.column(align=True)
        col.operator("ncnc.jogcontroller", text="", icon="TRIA_UP").action = "y+"
        col.operator("ncnc.jogcontroller", text="", icon="RADIOBUT_ON").action = "x0y0"  # SNAP_FACE_CENTER
        col.operator("ncnc.jogcontroller", text="", icon="TRIA_DOWN").action = "y-"
        zero = col.split()
        zero.operator("ncnc.jogcontroller", text="Y:0").action = "0y"
        zero.scale_y = 0.5

        col = row_jog.column(align=True)
        col.operator("ncnc.jogcontroller", text="", icon="DOT").action = "x+y+"
        col.operator("ncnc.jogcontroller", text="", icon="TRIA_RIGHT").action = "x+"
        col.operator("ncnc.jogcontroller", text="", icon="DOT").action = "x+y-"
        zero = col.split()
        zero.operator("ncnc.jogcontroller", text="XY:0").action = "0xy"
        zero.scale_y = 0.5

        col = row_jog.column(align=True)
        col.label(icon="BLANK1")
        col.operator("ncnc.jogcontroller", text="", icon="CON_OBJECTSOLVER").action = "mousepos"

        col = row_jog.column(align=True)
        col.operator("ncnc.jogcontroller", text="", icon="TRIA_UP").action = "z+"
        col.operator("ncnc.jogcontroller", text="", icon="RADIOBUT_ON").action = "z0"
        col.operator("ncnc.jogcontroller", text="", icon="TRIA_DOWN").action = "z-"
        zero = col.split()
        zero.operator("ncnc.jogcontroller", text="Z:0").action = "0z"
        zero.scale_y = 0.5

        row_conf = layout.row(align=True)

        col = row_conf.column(align=True)
        col.prop(pr_jog, "step_size_xy", icon="AXIS_TOP")
        col.prop(pr_jog, "step_size_z", icon="EMPTY_SINGLE_ARROW", )
        col.prop(pr_jog, "feed_rate", icon="CON_TRACKTO")
        col.prop(pr_jog, "spindle_speed", icon="CON_TRACKTO")

        col = row_conf.column(align=True)
        col.operator("ncnc.jogcontroller", text="", icon="HOME").action = "home"
        col.operator("ncnc.jogcontroller", text="", icon="EMPTY_SINGLE_ARROW").action = "safez"
        if context.scene.ncnc_pr_machine.status == "JOG":
            col.operator("ncnc.jogcontroller", text="", icon="CANCEL").action = "cancel"
        else:
            col.label(icon="BLANK1")

        pr_mac = context.scene.ncnc_pr_machine
        col.alert = pr_mac.spindle_state != "M5"
        col.prop(pr_jog, "spindle_state", icon="DISC", icon_only=True,
                 invert_checkbox=pr_jog.spindle_state or col.alert)

    def draw_header(self, context):
        context.scene.ncnc_pr_vision.prop_bool(self.layout, "mill")

        if context.scene.ncnc_pr_machine.status == "JOG":
            self.layout.operator("ncnc.jogcontroller", text="", icon="CANCEL").action = "cancel"


##################################
##################################
##################################

def handles() -> dict:
    keycode = "ncnc_pr_vision.handles"
    ns = bpy.app.driver_namespace

    if ns.get(keycode):
        return ns.get(keycode)

    ns[keycode] = {}
    return ns[keycode]


def handle_remove(keycode) -> handles:
    handle_list = handles()
    if handle_list.get(keycode):
        bpy.types.SpaceView3D.draw_handler_remove(handle_list.pop(keycode), 'WINDOW')

    return handle_list


class NCNC_PR_Vision(PropertyGroup):

    # ##########################
    # ########### Layout Methods
    def prop_bool(self, layout, prop: str):
        return layout.prop(self, prop,
                           emboss=False,
                           text="",
                           icon=("RESTRICT_VIEW_OFF" if eval(f"self.{prop}") else "RESTRICT_VIEW_ON"), )

    def prop_theme(self, layout, prop: str, text=""):
        row = layout.row(align=True)
        self.prop_bool(row, prop)
        row.label(text=text)
        col = row.column(align=True)
        col.prop(self, f"color_{prop}", text="")
        col.prop(self, f"thick_{prop}", text="")
        return row

    # ##########################
    # ################## Presets
    def update_presets(self, context):
        prs = {"def": (("g0", (.5, .5, .5, 0.5), 1),
                       ("g1", (0, .44, .77, 0.5), 2),
                       ("g2", (.77, .2, .3, 0.5), 2),
                       ("g3", (.3, .77, .2, 0.5), 2),
                       ("gp", (.1, .1, .1, 1), 2),
                       ("dash", (1, 1, 1, .9), 14),
                       ("status", (1, .8, .2, .9), 14),
                       ("pos", (1, .8, .2, .9), 14),
                       ("mill", (.9, .4, .6, .9), 3),
                       ),
               "blu": (("gcode", (0, .44, .77, 0.5), 1),
                       ("g0", (.2, .3, .5, .5), 1),
                       ("gp", (0, .1, .2, 1), 2),
                       ("dash", (.5, .7, 1, .9), 14),
                       ("mill", (0, .5, .8, .9), 3),
                       ),
               "bla": (("gcode", (0, 0, 0, 1), 1),
                       ("g0", (0, 0, 0, 1), 1),
                       ("gp", (.3, .3, .3, 1), 2),
                       ("dash", (0, 0, 0, 1), 14),
                       ("mill", (0, 0, .1, 1), 3),
                       ),
               "whi": (("gcode", (1, 1, 1, 1), 2),
                       ("g0", (1, 1, 1, 1), 1),
                       ("gp", (.4, .4, .4, 1), 2),
                       ("dash", (1, 1, 1, .9), 14),
                       ("mill", (.7, .8, 1, 1), 3),
                       ),
               }

        for key, color, thick in prs[self.presets]:
            exec(f"self.color_{key} = {color}")
            exec(f"self.thick_{key} = {thick}")

        # Save to last preset
        addon = bpy.context.preferences.addons.get(__name__)
        if addon:
            addon.preferences.last_preset = self.presets
            bpy.context.preferences.use_preferences_save = True

    presets: EnumProperty(
        items=[("def", "Default", ""),
               ("bla", "Black", ""),
               ("whi", "White", ""),
               ("blu", "Blue", "")],
        name="Presets",
        update=update_presets
    )

    # ##########################
    # #################### DASH
    def update_dash(self, context):
        keycode = "DASH"
        handles = handle_remove(keycode)
        if self.dash:
            handles[keycode] = bpy.types.SpaceView3D.draw_handler_add(self.dash_callback,
                                                                      (self, context),
                                                                      "WINDOW",
                                                                      "POST_PIXEL")

    dash: BoolProperty(
        name="Machine Dashboard",
        description="Show/Hide in Viewport",
        default=False,
        update=update_dash
    )
    feed: BoolProperty(
        name="Feed on Dashboard",
        description="Show/Hide in Viewport",
        default=True
    )
    spindle: BoolProperty(
        name="Spindle on Dashboard",
        description="Show/Hide in Viewport",
        default=True
    )
    buffer: BoolProperty(
        name="Buffer on Dashboard",
        description="Show/Hide in Viewport",
        default=True
    )
    status: BoolProperty(
        name="Status on Dashboard",
        description="Show/Hide in Viewport",
        default=True
    )
    pos: BoolProperty(
        name="Position on Dashboard",
        description="Show/Hide in Viewport",
        default=True
    )

    def update_color_dash(self, context):
        for key in ("feed", "spindle", "buffer", "status", "pos"):
            self[f"color_{key}"] = self.color_dash

    color_dash: FloatVectorProperty(
        name='Dashboard',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, 1, 1, 0.9),
        update=update_color_dash
    )
    color_feed: FloatVectorProperty(
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, 1, 1, 0.9)
    )
    color_spindle: FloatVectorProperty(
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, 1, 1, 0.9)
    )
    color_buffer: FloatVectorProperty(
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, 1, 1, 0.9)
    )
    color_status: FloatVectorProperty(
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, .8, .2, 0.9)
    )
    color_pos: FloatVectorProperty(
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, .8, .2, 0.9)
    )

    def update_thick_dash(self, context):
        for key in ("feed", "spindle", "buffer", "status", "pos"):
            self[f"thick_{key}"] = self.thick_dash

    thick_dash: IntProperty(default=14, min=8, max=20, description="Font Size", update=update_thick_dash)
    thick_feed: IntProperty(default=14, min=8, max=20, description="Font Size")
    thick_spindle: IntProperty(default=14, min=8, max=20, description="Font Size")
    thick_buffer: IntProperty(default=14, min=8, max=20, description="Font Size")
    thick_status: IntProperty(default=14, min=8, max=20, description="Font Size")
    thick_pos: IntProperty(default=14, min=8, max=20, description="Font Size")

    @classmethod
    def dash_callback(cls, self, context):
        if not cls.register_check(context):
            return
        # Draw text to indicate that draw mode is active
        pr_mac = context.scene.ncnc_pr_machine
        pos = pr_mac.mpos if pr_mac.pos_type == "mpos" else pr_mac.wpos

        blf_pos_y = 10

        pos_type = 'WPos' if pr_mac.pos_type == 'wpos' else 'MPos'
        for prop, text, val in [
            ("pos", pos_type, f"X {round(pos[0], 2)}   Y {round(pos[1], 2)}   Z {round(pos[2], 2)}"),
            ("buffer", "Buffer", f"{pr_mac.buffer},{pr_mac.bufwer}"),
            ("spindle", "Spindle", pr_mac.spindle),
            ("feed", "Feed", pr_mac.feed),
            ("status", "Status", pr_mac.status),
        ]:

            if not eval(f"self.{prop}"):
                continue

            size = eval(f"self.thick_{prop}")
            blf.color(0, *eval(f"self.color_{prop}"))
            blf.size(0, size, 64)
            blf.position(0, 10, blf_pos_y, 0)
            blf.draw(0, text)

            blf.position(0, size * 5, blf_pos_y, 0)
            blf.draw(0, f"{val}")
            blf_pos_y += size * 1.5

    @classmethod
    def dash_callback_recovery(cls, self, context):
        if not cls.register_check(context):
            return
        # Draw text to indicate that draw mode is active
        pr_mac = context.scene.ncnc_pr_machine
        pos = pr_mac.mpos if pr_mac.pos_type == "mpos" else pr_mac.wpos

        blf_pos_y = 10

        pos_type = 'WPos' if pr_mac.pos_type == 'wpos' else 'MPos'
        pos_str = f"X {round(pos[0], 2)}   Y {round(pos[1], 2)}   Z {round(pos[2], 2)}"
        buf_str = f"{pr_mac.buffer},{pr_mac.bufwer}"
        for text, val, show, color, size in [(pos_type, pos_str, self.pos, self.color_pos, self.thick_pos),
                                             ("Buffer", buf_str, self.buffer, self.color_buffer, self.thick_buffer),
                                             ("Spindle", pr_mac.spindle, self.spindle, self.color_spindle,
                                              self.thick_spindle),
                                             ("Feed", pr_mac.feed, self.feed, self.color_feed, self.thick_feed),
                                             ("Status", pr_mac.status, self.status, self.color_status,
                                              self.thick_status),
                                             ]:
            print(eval(f"self.pos"))
            if not show:
                continue
            blf.color(0, *color)
            blf.size(0, size, 64)
            blf.position(0, 10, blf_pos_y, 0)
            blf.draw(0, text)

            blf.position(0, size * 5, blf_pos_y, 0)
            blf.draw(0, f"{val}")
            blf_pos_y += size * 1.5

    # ##########################
    # #################### GCODE
    def update_gcode(self, context):
        keycode = "GCODE"
        handles = handle_remove(keycode)

        pr_act = context.scene.ncnc_pr_texts.active_text
        if not pr_act:
            return
        pr_txt = pr_act.ncnc_pr_text

        if self.gcode:
            # For different shader / color
            # https://docs.blender.org/api/current/gpu.html#mesh-with-random-vertex-colors

            # Dotted Line For G0
            # https://docs.blender.org/api/current/gpu.html#custom-shader-for-dotted-3d-line

            cls = self.__class__
            for i in range(4):
                cls.gcode_shaders[i] = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
                cls.gcode_batchs[i] = batch_for_shader(cls.gcode_shaders[i],
                                                       'LINES',
                                                       {"pos": pr_txt.get_lines(i)}
                                                       # {"pos": []}
                                                       )

            cls.gcode_shaders["p"] = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
            cls.gcode_batchs["p"] = batch_for_shader(cls.gcode_shaders["p"],
                                                     'POINTS',
                                                     {"pos": pr_txt.get_points()}
                                                     # {"pos": []}
                                                     )

            cls.gcode_shaders["c"] = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
            cls.gcode_batchs["c"] = batch_for_shader(cls.gcode_shaders["c"],
                                                     'LINES',
                                                     {"pos": []}
                                                     )

            handles[keycode] = bpy.types.SpaceView3D.draw_handler_add(cls.gcode_callback,
                                                                      (self, context),
                                                                      "WINDOW",
                                                                      "POST_VIEW")

    @classmethod
    def gcode_callback(cls, self, context):
        if not cls.register_check(context):
            return

        pr_txt = context.scene.ncnc_pr_texts.active_text
        if not pr_txt:
            return

        pr_txt = pr_txt.ncnc_pr_text
        if pr_txt.event:
            cls.gcode_batchs["p"] = batch_for_shader(cls.gcode_shaders["p"],
                                                     'POINTS',
                                                     {"pos": pr_txt.get_points()})
            for i in range(4):
                cls.gcode_batchs[i] = batch_for_shader(cls.gcode_shaders[i],
                                                       'LINES',
                                                       {"pos": pr_txt.get_lines(i)})
            if context.area:
                context.area.tag_redraw()

        if pr_txt.event_selected:
            cls.gcode_batchs["c"] = batch_for_shader(cls.gcode_shaders["c"],
                                                     'LINES',
                                                     {"pos": pr_txt.get_selected()})

        for i, color, thick, show in [(0, self.color_g0, self.thick_g0, self.g0),
                                      (1, self.color_g1, self.thick_g1, self.g1),
                                      (2, self.color_g2, self.thick_g2, self.g2),
                                      (3, self.color_g3, self.thick_g3, self.g3),
                                      ("p", self.color_gp, self.thick_gp, self.gp),
                                      ("c", self.color_gc, self.thick_gc, self.gc)
                                      ]:
            if not show:
                continue
            if i == "p":
                bgl.glPointSize(thick)
            else:
                bgl.glLineWidth(thick)
            cls.gcode_shaders[i].bind()
            cls.gcode_shaders[i].uniform_float("color", color)
            cls.gcode_batchs[i].draw(cls.gcode_shaders[i])

    gcode_shaders = {}
    gcode_batchs = {}
    gcode_last = ""
    gcode_prev_current_line = None

    gcode: BoolProperty(default=True, update=update_gcode)
    gp: BoolProperty(default=True)
    gc: BoolProperty(default=True)
    g0: BoolProperty(default=True)
    g1: BoolProperty(default=True)
    g2: BoolProperty(default=True)
    g3: BoolProperty(default=True)

    def update_thick_gcode(self, context):
        for key in (0, 1, 2, 3, "p"):
            self[f"thick_g{key}"] = self.thick_gcode

    thick_gcode: FloatProperty(name="General", default=2.0, min=0, max=10, description="Line Thickness",
                               update=update_thick_gcode)
    thick_gp: FloatProperty(name="Point", default=3.0, min=0, max=10, description="Point Thickness")
    thick_gc: FloatProperty(name="Current", default=3.0, min=0, max=10, description="Line Thickness")
    thick_g0: FloatProperty(name="Rapid", default=1.0, min=0, max=10, description="Line Thickness")
    thick_g1: FloatProperty(name="Linear", default=2.0, min=0, max=10, description="Line Thickness")
    thick_g2: FloatProperty(name="Arc CW", default=2.0, min=0, max=10, description="Line Thickness")
    thick_g3: FloatProperty(name="Arc CCW", default=2.0, min=0, max=10, description="Line Thickness")

    def update_color_gcode(self, context):
        for key in (0, 1, 2, 3, "p"):
            self[f"color_g{key}"] = self.color_gcode

    color_gcode: FloatVectorProperty(
        name='General',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(.5, .5, .5, .5),
        update=update_color_gcode
    )
    color_gp: FloatVectorProperty(
        name='Point Color',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(.1, .1, .1, .5)
    )
    color_gc: FloatVectorProperty(
        name='Current Code Line Color',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(1, 0, 1, .5)
    )
    color_g0: FloatVectorProperty(
        name='Rapid Color',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(.5, .5, .5, .5)
    )
    color_g1: FloatVectorProperty(
        name='Linear Color',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        # default=(0.7, 0.5, 0.2, 0.5)
        default=(0, .44, .77, 0.5)
    )
    color_g2: FloatVectorProperty(
        name='Arc Color CW',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(.77, .2, .3, 0.5)
    )
    color_g3: FloatVectorProperty(
        name='Arc Color CCW',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(.3, .77, .2, 0.5)
    )

    # ##########################
    # #################### MILL
    def update_mill(self, context):
        keycode = "MILL"
        handles = handle_remove(keycode)
        if self.mill:
            cls = self.__class__
            pr_mac = context.scene.ncnc_pr_machine
            pos = pr_mac.mpos if pr_mac.pos_type == "mpos" else pr_mac.wpos

            cls.mill_shader = gpu.shader.from_builtin('3D_UNIFORM_COLOR')
            cls.mill_batch = batch_for_shader(cls.mill_shader,
                                              'LINES',
                                              {"pos": cls.mill_lines(*pos)})

            handles[keycode] = bpy.types.SpaceView3D.draw_handler_add(cls.mill_callback,
                                                                      (self, context),
                                                                      "WINDOW",
                                                                      "POST_VIEW")

    mill: BoolProperty(
        name="Machine Mill",
        description="Show/Hide in Viewport",
        default=False,
        update=update_mill
    )
    color_mill: FloatVectorProperty(
        name='Arc Color CCW',
        subtype='COLOR',
        size=4,
        min=0.0,
        max=1.0,
        default=(0.9, 0.3, 0.5, 0.5)
    )

    thick_mill: FloatProperty(name="Arc CCW", default=3.0, min=0, max=10, description="Line Thickness")
    mill_delay = .5
    mill_last_time = 0
    mill_shader = None
    mill_batch = None

    @classmethod
    def mill_callback(cls, self, context):
        if not cls.register_check(context):
            return

        if time.time() - cls.mill_last_time > cls.mill_delay:
            pr_mac = context.scene.ncnc_pr_machine
            pos = pr_mac.mpos if pr_mac.pos_type == "mpos" else pr_mac.wpos

            cls.mill_last_time = time.time()
            cls.mill_delay = .1 if pr_mac.status in ("JOG", "RUN") else .5
            cls.mill_batch = batch_for_shader(cls.mill_shader,
                                              'LINES',
                                              {"pos": cls.mill_lines(*pos)})

        bgl.glLineWidth(self.thick_mill)
        cls.mill_shader.bind()
        cls.mill_shader.uniform_float("color", self.color_mill)
        cls.mill_batch.draw(cls.mill_shader)

    @classmethod
    def mill_lines(cls, x, y, z):
        s = 1.5
        s2 = s * 5
        return [
            (x, y, z), (x + s, y + s, z + s2),
            (x, y, z), (x - s, y - s, z + s2),
            (x, y, z), (x + s, y - s, z + s2),
            (x, y, z), (x - s, y + s, z + s2),
            (x - s, y - s, z + s2), (x - s, y + s, z + s2),
            (x - s, y + s, z + s2), (x + s, y + s, z + s2),
            (x + s, y - s, z + s2), (x + s, y + s, z + s2),
            (x - s, y - s, z + s2), (x + s, y - s, z + s2),
            (x, y, z + s2), (x, y, z + s2 * 2)
        ]

    @classmethod
    def register_check(cls, context) -> bool:
        return hasattr(context.scene, "ncnc_pr_machine") and hasattr(context.scene, "ncnc_pr_vision")

    @classmethod
    def register(cls):
        Scene.ncnc_pr_vision = PointerProperty(
            name="NCNC_PR_Vision Name",
            description="NCNC_PR_Vision Description",
            type=cls)

        # bpy.context.scene.ncnc_pr_vision.presets = pf.last_preset

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_vision
        for keycode in ("DASH", "MILL", "GCODE"):
            handle_remove(keycode)


class NCNC_OT_Vision(Operator):
    bl_idname = "ncnc.vision"
    bl_label = "Update View"
    bl_description = "Update View"
    bl_options = {'REGISTER'}

    inloop = True
    delay = 0.1
    _last_time = 0

    start: BoolProperty(default=True)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        # ########################### STANDARD
        if not self.start:
            unregister_modal(self)
            return {'CANCELLED'}
        register_modal(self)
        context.window_manager.modal_handler_add(self)
        # ####################################
        # ####################################

        return self.timer_add(context)

    def timer_add(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(self.delay, window=context.window)
        return {"RUNNING_MODAL"}

    def timer_remove(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        return {'CANCELLED'}

    def modal(self, context, event):
        # ########################### STANDARD
        if not self.inloop:
            if context.area:
                context.area.tag_redraw()
            return self.timer_remove(context)

        if time.time() - self._last_time < self.delay:
            return {'PASS_THROUGH'}

        self._last_time = time.time()
        # ####################################
        # ####################################

        pr_act = context.scene.ncnc_pr_texts.active_text
        if not pr_act:
            return {'PASS_THROUGH'}

        pr_txt = pr_act.ncnc_pr_text

        pr_txt.event_control()
        if pr_txt.event or pr_txt.event_selected:
            for area in context.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()

        return {'PASS_THROUGH'}


class NCNC_PT_Vision(Panel):
    bl_idname = "NCNC_PT_vision"
    bl_label = "Vision"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_vision

    def draw(self, context):
        # Filtreleme özelliği Ekle
        # Koddaki belli satırlar arası Filtrele
        # X Y Z aralıkları filtrele

        pr_vis = context.scene.ncnc_pr_vision
        layout = self.layout


class NCNC_PT_VisionThemes(Panel):
    bl_idname = "NCNC_PT_visionthemes"
    bl_label = "Themes"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_vision

    def draw(self, context):
        pr_vis = context.scene.ncnc_pr_vision
        layout = self.layout

        layout.prop(pr_vis, "presets", text="")


class NCNC_PT_VisionThemesGcode(Panel):
    bl_label = "G Codes"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_visionthemes"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        pr_vis = context.scene.ncnc_pr_vision
        layout = self.layout

        for pr, text in [("gcode", "General"),
                         ("gp", "G Points"),
                         ("g0", "G0 - Rapid"),
                         ("g1", "G1 - Linear"),
                         ("g2", "G2 - Arc (CW)"),
                         ("g3", "G3 - Arc (CCW)"),
                         ("gc", "Current Line"),
                         ]:
            pr_vis.prop_theme(layout, pr, text)

    def draw_header(self, context):
        context.scene.ncnc_pr_vision.prop_bool(self.layout, "gcode")


class NCNC_PT_VisionThemesDash(Panel):
    bl_label = "Dashboard"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_visionthemes"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        pr_vis = context.scene.ncnc_pr_vision
        layout = self.layout

        for pr, text in [("dash", "General"),
                         ("status", "Status"),
                         ("feed", "Feed"),
                         ("spindle", "Spindle"),
                         ("buffer", "Buffer"),
                         ("pos", "Position"),
                         ]:
            pr_vis.prop_theme(layout, pr, text)

    def draw_header(self, context):
        context.scene.ncnc_pr_vision.prop_bool(self.layout, "dash")


class NCNC_PT_VisionThemesMill(Panel):
    bl_label = "Mill"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_visionthemes"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        pr_vis = context.scene.ncnc_pr_vision
        layout = self.layout

        for pr, text in [("mill", "Mill")]:
            pr_vis.prop_theme(layout, pr, text)

    def draw_header(self, context):
        context.scene.ncnc_pr_vision.prop_bool(self.layout, "mill")


# #################################
# #################################
# #################################
class NCNC_PR_ToolpathConfigs(PropertyGroup):
    """Configs of the object. Located on the object itself"""
    obj: PointerProperty(type=Object, name="Object")

    def reload_gcode(self, context):
        bpy.ops.ncnc.convert()

    def update_included(self, context):
        if self.included:
            if self.check_curve(self.id_data):
                context.scene.ncnc_pr_objects.add_item(self.id_data)
                self.reload_gcode(context)
            else:
                self.included = False
        else:
            context.scene.ncnc_pr_objects.remove_item(self.id_data)

    included: BoolProperty(
        name="Included",
        default=False,
        description="Include in CNC machining?",
        update=update_included
    )
    plane: EnumProperty(
        name="Working Plane Selector",
        description="Select Plane (Under development. Doesn't work yet)",
        update=reload_gcode,
        items=[("G17", "XY", "G17: Work in XY Plane"),
               ("G18", "XZ", "G18: Work in XZ Plane"),
               ("G19", "YZ", "G19: Work in YZ Plane"),
               ("G17", "XYZ", "Under development (Doesn't work with GRBL v1.1)"),
               ]
    )

    ##############################################################################
    ##############################################################################
    safe_z: FloatProperty(
        name="Safe Z",
        default=5,
        # unit="LENGTH",
        description="Safe Z position (default:5)",
        update=reload_gcode
    )
    step: FloatProperty(
        name="Step Z",
        default=0.5,
        # unit="LENGTH",
        description="Z Machining depth in one step",
        update=reload_gcode
    )
    depth: FloatProperty(
        name="Total Depth",
        default=1,
        # unit="LENGTH",
        description="Son işleme derinliği",
        update=reload_gcode
    )

    ##############################################################################
    ##############################################################################
    feed: IntProperty(
        name="Feed Rate (mm/min)",
        default=60,
        min=30,
        description="Feed rate is the velocity at which the cutter is fed, that is, advanced against "
                    "the workpiece. It is expressed in units of distance per revolution for turning and "
                    "boring (typically inches per revolution [ipr] or millimeters per "
                    "revolution).\nDefault:200",
        update=reload_gcode
    )
    plunge: IntProperty(
        name="Plunge Rate (mm/min)",
        default=50,
        min=10,
        update=reload_gcode,
        description="Plunge rate is the speed at which the router bit is driven down into the "
                    "material when starting a cut and will vary depending on the bit used and the "
                    "material being processed. It is important not to plunge too fast as it is easy "
                    "to damage the tip of the cutter during this operation\ndefault: 100",
    )
    spindle: IntProperty(
        name="Spindle (rpm/min)",  # "Spindle Speed (rpm/min)"
        default=1000,
        min=600,
        update=reload_gcode,
        description="The spindle speed is the rotational frequency of the spindle of the machine, "
                    "measured in revolutions per minute (RPM). The preferred speed is determined by "
                    "working backward from the desired surface speed (sfm or m/min) and "
                    "incorporating the diameter (of workpiece or cutter).\nDefault:1200",
    )
    # #############################################################################
    # #############################################################################
    round_loca: IntProperty(
        name="Round (Location)",
         default=3,
         min=0,
         max=6,
         update=reload_gcode,
         description="Floating point resolution of location analysis? (default=3)\n"
                     "[0-6] = Rough analysis - Detailed analysis"
    )
    round_circ: IntProperty(
        name="Round (Circle)",
        default=1,
        min=0,
        max=6,
        update=reload_gcode,
        description="Floating point resolution of circular analysis? (default=1)\n"
                    "[0-6] = Rough analysis - Detailed analysis"
    )
    yvrla_g23d: IntProperty(
        name="Yuvarla (G2-G3 Koordinat)",
        default=0,
        min=0,
        max=6,
        update=reload_gcode,
        description="G2-G3 koordinatları kaç basamak yuvarlanacak ? (d=0). \n"
                    "[0-6] = Kaba-Detaylı. GRBL v1.1 için 0 değeri gir"
    )
    cmbr_m_lmt: IntProperty(
        name="Çember Merkez Uzaklığı Limiti",
        default=800,
        min=0,
        max=6,
        update=reload_gcode,
        description="Eğri hesaplanırken, radyal merkez çok uzakta çıkarsa \n"
                    "[0-6] = Kaba Hesap - Detaylı hesap"
    )

    icindeki_tipler: StringProperty()

    def check_curve(self, obj):
        """ Checks if the object type is Curve (Bezier or Poly)"""
        if obj.type == "CURVE":
            o = []
            for i in obj.data.splines:
                o.append(i.type == "POLY" or i.type == "BEZIER")
            return False not in o
        else:
            return False

    @classmethod
    def register(cls):
        Object.ncnc_pr_toolpathconfigs = PointerProperty(
            name="NCNC_PR_ToolpathConfigs Name",
            description="NCNC_PR_ToolpathConfigs Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Object.ncnc_pr_toolpathconfigs


class NCNC_OT_ToolpathConfigs(Operator):
    bl_idname = "ncnc.toolpathconfigs"
    bl_label = "Convert to Curve"
    bl_description = "Convert to curve for CNC machining"
    bl_options = {'REGISTER'}

    def execute(self, context):
        return self.invoke(context)

    def invoke(self, context, event=None):
        obj = context.active_object
        obj.select_set(True)
        objAyar = obj.ncnc_pr_toolpathconfigs

        if not obj:
            self.report({'WARNING'}, "No Object Selected")
            return {"FINISHED"}

        if obj.type != 'CURVE':  # Curve değilse
            bpy.ops.object.convert(target='CURVE')  # Curve'e çevir

        if obj.type != 'CURVE':  # Curve'e çevrilmiyorsa
            self.report({'WARNING'}, f"Cannot convert to curve : {obj.name}")
            return {"CANCELLED"}

        if not objAyar.check_curve(obj):  # Curve ama Bezier veya Poly değilse    (ilerde geliştirilecek)
            self.report({'INFO'}, "Curve tipi uygun değil : %s" % (obj.name))
            return {"FINISHED"}  # Bitir

        objAyar.included = True  # Convert edildikten sonra, CNC'de işlenmek üzere included edilir.
        if "nCurve" not in obj.name:
            obj.name = "nCurve." + obj.name

        self.report({'INFO'}, f"Convert to Curve : {obj.name}")

        return {"FINISHED"}


class NCNC_PT_ToolpathConfigs(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "nCNC"
    bl_label = "Toolpath Configs"
    bl_idname = "NCNC_PT_objectconfigs"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_gcode

    def draw(self, context):

        obj = context.active_object

        layout = self.layout

        if not obj:
            col = layout.column()
            col.label(text="No object selected", icon="CURVE_DATA")
            for i in range(7):
                col.label()
            return

        props = obj.ncnc_pr_toolpathconfigs

        row = layout.row(align=True)
        row.prop(props, "included", text="", icon="CHECKBOX_HLT" if props.included else "CHECKBOX_DEHLT")
        row.enabled = props.check_curve(obj)
        row.prop(obj, "name", text="")

        # if not props.check_curve(obj):
        #    row.operator("ncnc.toolpathconfigs", text="", icon="CURVE_DATA")

        isok = props.check_curve(obj)

        row = layout.row(align=True)
        if not isok:
            row.operator("ncnc.toolpathconfigs", text="Convert to Curve", icon="CURVE_DATA")
        else:
            row.enabled = props.included  # Tip uygun değilse buraları pasif yapar
            row.prop(props, "plane", expand=True)
            row.enabled = False

        col = layout.column(align=True)
        col.enabled = props.included  # Tip uygun değilse buraları pasif yapar
        col.prop(props, "safe_z")
        col.prop(props, "step")
        col.prop(props, "depth")

        col = layout.column(align=True)
        col.enabled = props.included  # Tip uygun değilse buraları pasif yapar
        col.prop(props, "feed")
        col.prop(props, "plunge")
        col.prop(props, "spindle")


class NCNC_PT_ToolpathConfigsDetails(Panel):
    bl_idname = "NCNC_PT_tconfigsdetails"
    bl_label = "Detail: Converting"
    bl_region_type = "UI"
    bl_space_type = "VIEW_3D"
    bl_category = "nCNC"
    bl_parent_id = "NCNC_PT_objectconfigs"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        obj = context.active_object
        if not obj:
            return

        props = obj.ncnc_pr_toolpathconfigs

        if not props.check_curve(obj):
            return

        layout = self.layout
        col = layout.column(align=True)
        col.enabled = props.included  # Tip uygun değilse buraları pasif yapar
        col.prop(props, "round_circ", slider=True)
        col.prop(props, "round_loca", slider=True)

        col = layout.column(align=True)
        col.enabled = props.included  # Tip uygun değilse buraları pasif yapar
        if obj.type == "CURVE":
            col.prop(obj.data, "resolution_u", slider=True, text="Resolution Obj General")
            if obj.data.splines.active:
                col.prop(obj.data.splines.active, "resolution_u", slider=True, text="Resolution Spline in Obj")


##################################
##################################
##################################

class NCNC_UL_Objects(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        obj = item.obj

        sor = obj.name not in context.scene.objects.keys()
        row = layout.row()
        row.prop(obj, "name",
                 text="",
                 emboss=False,
                 icon_only=sor,
                 icon=f"OUTLINER_OB_{obj.type}" if not sor else "TRASH",
                 # icon_value=layout.icon(obj.data)
                 )


class NCNC_PR_Objects(PropertyGroup):
    def add_item(self, obj):
        if obj.type != 'CURVE':
            obj.ncnc_pr_toolpathconfigs.included = False
            return

        for j, i in enumerate(self.items):

            if obj == i.obj:
                # if obj.select_get():
                #    self.active_item_index = j
                return

        item = self.items.add()
        item.obj = obj
        self.active_item_index = len(self.items) - 1

    def remove_item(self, obj):
        for j, i in enumerate(self.items):
            if obj == i.obj:
                self.items.remove(j)
                # self.active_item_index = len(self.items) - 1
                break

    def update_active_item_index(self, context):
        bpy.ops.object.select_all(action='DESELECT')
        obj = self.items[self.active_item_index].obj

        if obj.name not in context.scene.objects.keys():
            self.items.remove(self.active_item_index)
            return

        obj.select_set(True)
        context.view_layer.objects.active = obj

    items: CollectionProperty(
        type=NCNC_PR_ToolpathConfigs,
        name="Objects",
        description="All Object Items Collection",
    )
    active_item_index: IntProperty(
        name="Active Item",
        default=-1,
        description="Selected object index in Collection",
        update=update_active_item_index,
    )
    overwrite: BoolProperty(
        name="Overwrite",
        default=True,
        description="Overwrite the last text"
    )

    @classmethod
    def register(cls):
        Scene.ncnc_pr_objects = PointerProperty(
            name="NCNC_PR_Objects Name",
            description="NCNC_PR_Objects Description",
            type=cls)

    @classmethod
    def unregister(cls):
        del Scene.ncnc_pr_objects


class NCNC_OT_Objects(Operator):
    bl_idname = "ncnc.objects"
    bl_label = "Objects Operator"
    bl_description = "for Selected Object ;\n" \
                     "( + ) : Add the object to the CNC work" \
                     "( - ) : Removing the object from CNC work\n" \
                     "(bin) : Delete object"
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(name="Select Object",
                         items=[("bos", "Select", ""),
                                ("add", "Addt", ""),
                                ("remove", "Remove", ""),
                                ("delete", "Delete", ""),
                                ("up", "Up", ""),
                                ("down", "Down", "")
                                ])

    inloop = True
    delay = 0.2  # 0.5
    _last_time = 0

    start: BoolProperty(default=True)

    def execute(self, context):
        return self.invoke(context, None)

    def invoke(self, context, event):
        props = context.scene.ncnc_pr_objects
        items = props.items
        index = props.active_item_index

        if self.action == "add":
            bpy.context.active_object.ncnc_pr_toolpathconfigs.included = True
            self.report({'INFO'}, "Object Added")

        elif self.action == "remove":
            bpy.context.active_object.ncnc_pr_toolpathconfigs.included = False
            self.report({'INFO'}, "Object Removed")

        elif self.action == "delete":
            self.report({'INFO'}, "Object Deleted")
            bpy.ops.object.delete(use_global=False, confirm=False)

        elif self.action == 'down' and index < len(items) - 1:
            items.move(index, index + 1)
            props.active_item_index += 1

        elif self.action == 'up' and index >= 1:
            items.move(index, index - 1)
            props.active_item_index -= 1

        # ########################### STANDARD
        else:
            if not self.start:
                unregister_modal(self)
                return {'CANCELLED'}
            register_modal(self)
            context.window_manager.modal_handler_add(self)
        # ####################################
        # ####################################

        return self.timer_add(context)

    def timer_add(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(self.delay, window=context.window)
        return {"RUNNING_MODAL"}

    def timer_remove(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        return {'CANCELLED'}

    def modal(self, context, event):
        # ########################### STANDARD
        if not self.inloop:
            if context.area:
                context.area.tag_redraw()
            return self.timer_remove(context)

        if time.time() - self._last_time < self.delay:
            return {'PASS_THROUGH'}

        self._last_time = time.time()
        # ####################################
        # ####################################

        props = context.scene.ncnc_pr_objects

        # Add new items
        for obj in context.scene.objects:
            if obj.ncnc_pr_toolpathconfigs.included:
                props.add_item(obj)

        # Remove items
        for i in props.items:
            if not i.obj or (i.obj.name not in context.scene.objects.keys()) or (
                    not i.obj.ncnc_pr_toolpathconfigs.included):
                props.remove_item(i.obj)
                if context.area:
                    context.area.tag_redraw()

        return {'PASS_THROUGH'}


class NCNC_PT_Objects(Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "nCNC"
    bl_label = "Toolpaths"  # Included Objects
    bl_idname = "NCNC_PT_objects"

    # bl_parent_id = "NCNC_PT_output"

    @classmethod
    def poll(cls, context):
        return context.scene.ncnc_pr_head.tool_gcode

    def draw(self, context):
        layout = self.layout

        props = context.scene.ncnc_pr_objects

        row = layout.row()

        col2 = row.column(align=True)
        col2.operator("ncnc.objects", icon="ADD", text="").action = "add"
        col2.operator("ncnc.objects", icon="REMOVE", text="").action = "remove"
        col2.operator("ncnc.objects", icon="TRASH", text="").action = "delete"
        col2.separator()
        col2.operator("ncnc.objects", icon="TRIA_UP", text="").action = "up"
        col2.operator("ncnc.objects", icon="TRIA_DOWN", text="").action = "down"

        col1 = row.column()  # .box()
        col1.template_list(
            "NCNC_UL_Objects",  # TYPE
            "ncnc_ul_objects",  # ID
            props,  # Data Pointer
            "items",  # Propname
            props,  # active_dataptr
            "active_item_index",  # active_propname
            rows=5,
            type='DEFAULT'
        )
        row = layout.row(align=False)
        row.prop(props, "overwrite",
                 icon_only=True,
                 icon=("RADIOBUT_ON" if props.overwrite else "RADIOBUT_OFF"),
                 invert_checkbox=props.overwrite)

        row.operator("ncnc.convert",
                     text="Convert to GCode",
                     icon="COLOR_GREEN")


##################################
##################################
##################################


class NCNC_OT_Empty(Operator):
    bl_idname = "ncnc.empty"
    bl_label = ""
    bl_description = ""
    bl_options = {'REGISTER'}

    def invoke(self, context, event=None):
        return {"CANCELLED"}


"""
    Header -> _HT_
    Menu -> _MT_
    Operator -> _OT_
    Panel -> _PT_
    UIList -> _UL_
"""

classes = [
    NCNC_OT_Decoder,
    NCNC_OT_Empty,
    NCNC_Prefs,

    NCNC_PR_Head,
    NCNC_PT_Head,
    NCNC_PT_HeadTextDetails,

    NCNC_PR_Texts,
    NCNC_OT_TextsRemove,
    NCNC_OT_TextsOpen,
    NCNC_OT_TextsSave,

    NCNC_PR_Lines,
    NCNC_PR_TextLine,
    NCNC_PR_Text,
    NCNC_OT_Text,

    NCNC_PR_Scene,
    NCNC_OT_Scene,
    NCNC_PT_Scene,

    NCNC_OT_Convert,

    NCNC_PR_Connection,
    NCNC_OT_Connection,
    NCNC_PT_Connection,

    NCNC_PR_MessageItem,
    NCNC_PR_Communication,
    NCNC_OT_CommunicationRun,
    NCNC_OT_Communication,
    NCNC_UL_Messages,
    NCNC_OP_Messages,
    NCNC_PT_Communication,

    NCNC_PR_Machine,
    NCNC_OT_Machine,
    NCNC_PT_Machine,

    NCNC_PR_JogController,
    NCNC_OT_JogController,
    NCNC_PT_JogController,

    NCNC_PT_MachineDash,
    NCNC_PT_MachineModes,
    NCNC_PT_MachineDetails,
    NCNC_PT_MachineDetail,
    NCNC_PT_MachineDetailInvert,
    NCNC_PT_MachineDetailAxis,
    NCNC_PT_MachineDetailAxisInvert,

    NCNC_PR_Vision,
    NCNC_OT_Vision,
    NCNC_PT_Vision,
    NCNC_PT_VisionThemes,
    NCNC_PT_VisionThemesGcode,
    NCNC_PT_VisionThemesDash,
    NCNC_PT_VisionThemesMill,

    NCNC_PR_ToolpathConfigs,
    NCNC_OT_ToolpathConfigs,
    NCNC_UL_Objects,
    NCNC_PR_Objects,
    NCNC_OT_Objects,
    NCNC_PT_ToolpathConfigs,
    NCNC_PT_ToolpathConfigsDetails,
    NCNC_PT_Objects,
]


def register():
    for i in classes:
        bpy.utils.register_class(i)


def unregister():
    for i in classes[::-1]:
        bpy.utils.unregister_class(i)


if __name__ == "__main__":
    register()
