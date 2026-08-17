# ##### BEGIN GPL LICENSE BLOCK #####
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
# ##### END GPL LICENSE BLOCK #####

"""
Resonite SDK for Blender.

"""
from .resonite_sdk.blender_operators import *
from .resonite_sdk.blender_properties import *
from .resonite_sdk.blender_ui import *
import bpy


classes = (
    BLENDER_RESONITE_SDK_PG_resonite_link_session_item,
    BLENDER_RESONITE_SDK_PG_main,
    BLENDER_RESONITE_SDK_OT_test,
    BLENDER_RESONITE_SDK_OT_send_active_object,
    # BLENDER_RESONITE_SDK_OT_send_active_object_evaluated,
    BLENDER_RESONITE_SDK_UL_resonite_link_sessions,
    BLENDER_RESONITE_SDK_PT_test_panel
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.blender_resonite_sdk = bpy.props.PointerProperty(type=BLENDER_RESONITE_SDK_PG_main) # type: ignore


def unregister():
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)

    del bpy.types.Scene.blender_resonite_sdk # type: ignore
