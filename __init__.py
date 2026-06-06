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
    BlendshapeRawData, BlendshapeFrameRawData, \
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


def _remap_blender_to_resonite(arr : np.ndarray) -> np.ndarray:
    """
    Remaps an array of elements using Blender's coordinate system to Resonite's coordinate system.
    Transformation: `X, Y, Z` -> `X, Z, -Y`

    """
    arr = arr.reshape(-1, 3) # Reshape to 2D Array
    arr = arr[:, [0, 2, 1]] # Swizzle columns X, Y, Z -> X, Z, Y
    arr = np.multiply(arr, np.array([1, 1, -1], dtype=arr.dtype)) # Invert Y
    return arr.ravel() # Return transformed data as flattened array

def _create_mesh_import_message(mesh : bpy.types.Mesh) -> ImportMeshRawData:
    """
    Creates a ImportMeshRawData message from the provided Blender mesh.

    Note
    ----
    In Blender, faces are defined through loops, the same vertex can be part of multiple loops.
    To ensure we get all the correct face data (shading, UVs etc.), we need to import each loop as a separate vertex into Resonite.

    """
    mesh.calc_loop_triangles()
    mesh.calc_tangents()

    vertex_count = len(mesh.vertices)
    loop_count = len(mesh.loops)
    triangle_count = len(mesh.loop_triangles)
    
    msg_import_mesh = ImportMeshRawData()

    # Convert vertex positions
    vertex_positions = np.empty(vertex_count*3, dtype=np.float32)
    mesh.vertices.foreach_get('co', vertex_positions)
    loop_vertex_mapping = np.empty(loop_count, dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertex_mapping)
    loop_positions = vertex_positions.reshape(-1, 3)[loop_vertex_mapping].ravel()
    loop_positions = _remap_blender_to_resonite(loop_positions)
    msg_import_mesh.vertex_count = loop_count
    msg_import_mesh._positions = loop_positions.tobytes()

    # Convert vertex normals
    normals = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('normal', normals)
    normals = _remap_blender_to_resonite(normals)
    msg_import_mesh.has_normals = True
    msg_import_mesh._normals = normals.tobytes()

    # Convert vertex tangents & bitangent signs
    tangents = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('tangent', tangents)
    bitangent_signs = np.empty(loop_count, dtype=np.float32)
    mesh.loops.foreach_get('bitangent_sign', bitangent_signs)
    tangents_and_bitangent_signs = np.concatenate((tangents.reshape(-1, 3), bitangent_signs.reshape(-1, 1)), axis=1)
    msg_import_mesh.has_tangents = True
    msg_import_mesh._tangents = tangents_and_bitangent_signs.ravel().tobytes()

    # Convert vertex colors
    if mesh.vertex_colors.active:
        vertex_colors = np.empty(loop_count*4, dtype=np.float32)
        mesh.vertex_colors.active.data.foreach_get('color', vertex_colors)
        msg_import_mesh.has_colors = True
        msg_import_mesh._colors = vertex_colors.tobytes()
    
    # Convert UVs
    # TODO: Resonite supports up to 4 UV layers, so we should have options to define which ones we import.
    #       For now, we only import the active one.
    if mesh.uv_layers.active:
        uv_coords = np.empty(loop_count*2, dtype=np.float32)
        mesh.uv_layers.active.uv.foreach_get('vector', uv_coords)
        msg_import_mesh.uv_channel_dimensions = [ 2 ]
        msg_import_mesh._uvs = [ uv_coords.tobytes() ]
    
    # Convert shape keys
    msg_import_mesh.blendshapes = []
    if mesh.shape_keys:
        for shape_key_name, shape_key in mesh.shape_keys.key_blocks.items():
            if shape_key.relative_key == shape_key:
                # Skip basis key
                continue
            
            # Blender doesn't support multi-frame shape keys, only ever one frame
            blendshape_frame_positions = np.empty(vertex_count*3, dtype=np.float32)
            shape_key.data.foreach_get('co', blendshape_frame_positions)
            blendshape_frame_positions = _remap_blender_to_resonite(blendshape_frame_positions)
            blendshape_frame_position_deltas = np.subtract(blendshape_frame_positions, vertex_positions)
            blendshape_frame = BlendshapeFrameRawData(position=1.0)
            blendshape_frame._position_deltas = blendshape_frame_position_deltas.tobytes()

            msg_import_mesh.blendshapes.append(BlendshapeRawData(
                name=shape_key_name, 
                has_normal_deltas=False,
                has_tangent_deltas=False,
                frames=[ blendshape_frame ]
            ))

    # Convert triangles
    triangle_indices = np.empty(triangle_count*3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('loops', triangle_indices)
    triangle_submesh = TriangleSubmeshRawData()
    triangle_submesh.triangle_count = triangle_count
    triangle_submesh._indices = triangle_indices.tobytes()
    msg_import_mesh.submeshes = [ triangle_submesh ]
    
    return msg_import_mesh


class BLENDER_RESONITE_SDK_OT_send_active_object(AsyncOperator):
    bl_idname = 'blender_resonite_sdk.send_active_object'
    bl_label = ""
    bl_description = "Sends the active object to Resonite."

    _object : bpy.types.Object
    _mesh : bpy.types.Mesh
    
    def handle_context(self, context):
        if not context.active_object or not context.scene:
            return

        if type(context.active_object.data) != bpy.types.Mesh:
            return

        self._object = context.active_object
        self._mesh = context.active_object.data
    
    async def execute_async(self): # type: ignore
        if not self._object or not self._mesh:
            return

        client = ResoniteLinkWebsocketClient()

        @client.on_started
        async def _on_client_started(client : ResoniteLinkClient):
            try:
                # Import mesh data
                msg_import_mesh = _create_mesh_import_message(self._mesh)
                mesh_asset : AssetData = await client.send_message(msg_import_mesh) # type: ignore
                
                # Create slot to attach mesh to.
                slot = await client.add_slot(name=self._object.name)
                
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
                    "[FrooxEngine]FrooxEngine.SkinnedMeshRenderer" if msg_import_mesh.blendshapes or msg_import_mesh.bone_weights else "[FrooxEngine]FrooxEngine.MeshRenderer", 
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


class BLENDER_RESONITE_SDK_OT_send_active_object_evaluated(AsyncOperator):
    bl_idname = 'blender_resonite_sdk.send_active_object_evaluated'
    bl_label = ""
    bl_description = "Applies Modifiers & Sends the active object to Resonite."

    _object : bpy.types.Object
    _mesh : bpy.types.Mesh
    
    def handle_context(self, context):
        if not context.active_object or not context.scene:
            return

        depsgraph = context.evaluated_depsgraph_get()
        self._object = context.active_object.evaluated_get(depsgraph)
        self._mesh = self._object.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    
    async def execute_async(self): # type: ignore
        if not self._object or not self._mesh:
            return

        client = ResoniteLinkWebsocketClient()

        @client.on_started
        async def _on_client_started(client : ResoniteLinkClient):
            try:
                # Import mesh data
                msg_import_mesh = _create_mesh_import_message(self._mesh)
                mesh_asset : AssetData = await client.send_message(msg_import_mesh) # type: ignore
                
                # Create slot to attach mesh to.
                slot = await client.add_slot(name=self._object.name)
                
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
                    "[FrooxEngine]FrooxEngine.SkinnedMeshRenderer" if msg_import_mesh.blendshapes or msg_import_mesh.bone_weights else "[FrooxEngine]FrooxEngine.MeshRenderer", 
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
        self.layout.operator("blender_resonite_sdk.send_active_object_evaluated", text="Apply Modifiers & Send Active Object")


classes = (
    BLENDER_RESONITE_SDK_OT_send_active_object,
    BLENDER_RESONITE_SDK_OT_send_active_object_evaluated,
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
