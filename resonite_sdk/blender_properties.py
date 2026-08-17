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
Properties for Blender.

"""
from bpy.types import PropertyGroup
from bpy.props import StringProperty, IntProperty,  CollectionProperty


class BLENDER_RESONITE_SDK_PG_resonite_link_session_item(PropertyGroup):
    """
    Represents a discovered ResoniteLink session on the local network.

    """
    bl_idname = 'BLENDER_RESONITE_SDK_PG_resonite_link_session_item'

    name : StringProperty(name="Name") # type: ignore
    port : IntProperty(name="Port") # type: ignore


class BLENDER_RESONITE_SDK_PG_main(PropertyGroup):
    """
    The main property group of the extension.
    This will be accessible for the current scene as `bpy.context.scene.blender_resonite_sdk`.

    """
    bl_idname = 'BLENDER_RESONITE_SDK_PG_main'

    link_sessions : CollectionProperty(type=BLENDER_RESONITE_SDK_PG_resonite_link_session_item) # type: ignore
    active_link_session_index : IntProperty() # type: ignore
