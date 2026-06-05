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
from resonitelink import \
    ResoniteLinkClient, \
    ResoniteLinkWebsocketClient, \
    ImportMeshRawData, \
    AssetData, \
    TriangleSubmeshRawData, \
    Field_Uri, Field_Enum, Field_Float, Reference, SyncList
from typing import Set, Optional
import numpy as np
import threading
import logging
import asyncio
import bmesh
import bpy


class AsyncOperator(bpy.types.Operator):
    """
    This is a wrapper operator to simplify implementing async code.
    From Blenders perspective, it acts like a normal modal operator.

    """
    _thread : threading.Thread

    def execute(self, context) -> Set[str]: # type: ignore
        # TODO: Global sort of "is running" list with operator type names?
        #       if is_running: ...
        if not context.window_manager:
            raise ValueError("No window manager available!")

        self.handle_context(context)

        def _run_async_in_thread():
            asyncio.run(self.execute_async())
        
        self._thread = threading.Thread(target=_run_async_in_thread)
        self._thread.start()

        context.window_manager.event_timer_add(0.5, window=context.window)
        context.window_manager.modal_handler_add(self)

        return { 'RUNNING_MODAL' }
    
    def modal(self, context, event) -> Set[str]: # type: ignore
        if event.type != 'TIMER':
            return { 'PASS_THROUGH' }
        
        if not self._thread.is_alive():
            # TODO: Check how thread completed (result / exception / cancelled)
            return { 'FINISHED' }
        
        return { 'PASS_THROUGH' }

    def handle_context(self, context : bpy.types.Context):
        """
        To be implemented by extending class.

        """
        pass

    async def execute_async(self):
        """
        To be implemened by extending class.

        """
        pass


def _create_mesh_import_message(mesh : bpy.types.Mesh) -> ImportMeshRawData:
    """
    Creates a ImportMeshRawData message from the provided Blender mesh.

    """
    vertex_count = len(mesh.vertices)
    triangle_count = len(mesh.loop_triangles)

    mesh.loop_triangles[0].vertices
    
    positions = np.empty(vertex_count*3, dtype=np.float64)
    mesh.vertices.foreach_get('co', positions)

    triangle_indices = np.empty(triangle_count*3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('vertices', triangle_indices)
    
    msg_import_mesh = ImportMeshRawData()
    msg_import_mesh.vertex_count = vertex_count
    msg_import_mesh._positions = positions.astype(dtype=np.float32).tobytes() # Blender uses 64-Bit floats, Resonite uses 32-Bit floats

    triangle_submesh = TriangleSubmeshRawData()
    triangle_submesh.triangle_count = triangle_count
    triangle_submesh._indices = triangle_indices.tobytes()
    msg_import_mesh.submeshes = [ triangle_submesh ]
    
    return msg_import_mesh


class BLENDER_RESONITE_SDK_OT_send_active_object(AsyncOperator):
    bl_idname = 'blender_resonite_sdk.send_active_object'
    bl_label = ""
    bl_description = "Sends the active object to Resonite."

    _active_object : bpy.types.Object
    _mesh : bpy.types.Mesh

    def handle_context(self, context):
        if not context.active_object:
            return
        
        self._active_object = context.active_object

        if not self._active_object.data:
            return
        
        if type(context.active_object.data) == bpy.types.Mesh:
            self._mesh = context.active_object.data
    
    async def execute_async(self): # type: ignore
        if not self._active_object:
            return
        
        if not self._mesh:
            return

        client = ResoniteLinkWebsocketClient()

        @client.on_started
        async def _on_client_started(client : ResoniteLinkClient):
            try:
                # Import mesh data
                msg_import_mesh = _create_mesh_import_message(self._mesh)
                mesh_asset : AssetData = await client.send_message(msg_import_mesh) # type: ignore
                
                # Create slot to attach mesh to.
                slot = await client.add_slot(name=self._active_object.name)
                
                # Adds a StaticMesh component to the slot and assigns the asset URI of the imported mesh data. 
                static_mesh = await slot.add_component(
                    "[FrooxEngine]FrooxEngine.StaticMesh", 
                    URL=Field_Uri(mesh_asset.asset_url)
                )

                # Adds a PBS_VertexColorMetallic material.
                material = await slot.add_component(
                    "[FrooxEngine]FrooxEngine.PBS_VertexColorMetallic", 
                    Culling=Field_Enum("Off", "[FrooxEngine]FrooxEngine.Culling"),
                    Smoothness=Field_Float(0.0)
                )

                # Creates a mesh renderer for the mesh and material.
                mesh_renderer = await slot.add_component(
                    "[FrooxEngine]FrooxEngine.MeshRenderer", 
                    Mesh=Reference(target_type="[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Mesh>", target_id=static_mesh.id),
                    Materials=SyncList(Reference(target_type="[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Material>", target_id=material.id))
                )

                # Adds MeshCollider component.
                await slot.add_component("[FrooxEngine]FrooxEngine.MeshCollider")

                # Adds Grabbable component and makes it scalable.
                await slot.add_component("[FrooxEngine]FrooxEngine.Grabbable")
            
            finally:
                await client.stop()

        await client.start(auto_discover=True)


class BLENDER_RESONITE_SDK_PT_test_panel(bpy.types.Panel):
    bl_idname = "blender_resonite_sdk.test_panel"
    bl_label = "Resonite SDK"

    bl_category = 'Resonite SDK'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    
    def draw (self, context):
        if not self.layout:
            raise ValueError("Layout does not exist!")
        
        self.layout.operator("blender_resonite_sdk.send_active_object", text="Send Active Object")


classes = (
    BLENDER_RESONITE_SDK_OT_send_active_object,
    BLENDER_RESONITE_SDK_PT_test_panel
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)


def unregister():
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)
