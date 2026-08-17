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
Blender UI panels and elements.

"""
from typing import Any, Optional

from bpy.types import Context, UILayout

from .blender_properties import *
import bpy


class BLENDER_RESONITE_SDK_UL_resonite_link_sessions(bpy.types.UIList):
    bl_idname = "BLENDER_RESONITE_SDK_UL_resonite_link_sessions"

    use_filter_show = False # Hide filter section, we don't need it for this.

    def draw_item(
        self, 
        context: Context, 
        layout: UILayout, 
        data: Optional[BLENDER_RESONITE_SDK_PG_main], 
        item: Optional[BLENDER_RESONITE_SDK_PG_resonite_link_session_item], 
        icon: Optional[int], 
        active_data: Any, 
        active_property: Optional[str], 
        index: Optional[int], 
        flt_flag: Optional[int]
    ) -> None:
        """
        The draw_item function is called for each item of the collection that is visible in the list.

        data is the RNA object containing the collection,
        item is the current drawn item of the collection,
        icon is the "computed" icon for the item (as an integer, because some objects like materials or textures have custom icons ID, which are not available as enum items).
        active_data is the RNA object containing the active property for the collection (i.e. integer pointing to the active item of the collection).
        active_propname is the name of the active property (use 'getattr(active_data, active_propname)').
        index is index of the current item in the collection.
        flt_flag is the result of the filtering process for this item.
        
        """
        if item:
            layout.prop(item, "name", text="", emboss=False, icon_value=icon)
        else:
            layout.label(text="", translate=False, icon_value=icon)


class BLENDER_RESONITE_SDK_PT_test_panel(bpy.types.Panel):
    bl_idname = "BLENDER_RESONITE_SDK_PT_test_panel"
    bl_label = "Resonite SDK"

    bl_category = 'Resonite SDK'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    
    def draw (self, context):
        if not self.layout:
            raise ValueError("Layout does not exist!")
        
        props : BLENDER_RESONITE_SDK_PG_main = bpy.context.scene.blender_resonite_sdk # type: ignore
        
        self.layout.operator("blender_resonite_sdk.test", text="Test Operator")
        self.layout.template_list("BLENDER_RESONITE_SDK_UL_resonite_link_sessions", "", props, "link_sessions", props, "active_link_session_index")

        self.layout.operator("blender_resonite_sdk.send_active_object", text="Send Active Object")
        # self.layout.operator("blender_resonite_sdk.send_active_object_evaluated", text="Apply Modifiers & Send Active Object")
