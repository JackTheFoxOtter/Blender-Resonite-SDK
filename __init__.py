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
    Slot, SlotProxy, \
    AssetData, \
    TriangleSubmeshRawData, \
    Float3, FloatQ, Float4x4, \
    BlendshapeRawData, BlendshapeFrameRawData, \
    Bone, BoneWeightRawData, \
    Field_Uri, Field_Enum, Field_Float, Reference, SyncList
from mathutils import Matrix, Vector, Quaternion, Euler
from typing import Set, Optional, Union, List, Sequence
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


def _remap_blender_to_resonite(arr : np.ndarray):
    """
    Remaps an array of elements using Blender's coordinate system to Resonite's coordinate system.
    Transformation: `X, Y, Z` -> `-X, Z, -Y`

    """
    # arr = arr.reshape(-1, 3) # Reshape to 2D Array
    # arr = arr[:, [0, 2, 1]] # Swizzle columns (X, Y, Z) -> (X, Z, Y)
    # arr = np.multiply(arr, np.array([-1, 1, -1], dtype=arr.dtype)) # Invert X & Y
    # return arr.ravel()
    arr[0::3], arr[1::3], arr[2::3] = -arr[0::3], arr[2::3], -arr[1::3] # Faster transformation (X, Y, Z) -> (-X, Z, -Y)

def _reverse_column_order(arr : np.ndarray):
    """
    For a given array of pairs of 3 elements, reverse the column order.
    Transformation: `X, Y, Z` -> `Z, Y, X`

    """
    # return arr = arr.reshape(-1, 3)[:, [2, 1, 0]].ravel() # Reverse order (X, Y, Z) -> (Z, Y, X)
    arr[0::3], arr[2::3] = arr[2::3], arr[0::3].copy() # Faster transformation (X, Y, Z) -> (Z, Y, X)


class BoneInfo():
    _bone : Bone
    _slot : Union[Slot, SlotProxy]

    @property
    def bone(self) -> Bone:
        return self._bone
    
    @property
    def slot(self) -> Union[Slot, SlotProxy]:
        return self._slot

    def __init__(self, bone : Bone, slot : Union[Slot, SlotProxy]):
        self._bone = bone
        self._slot = slot


def _create_mesh_import_message(mesh : bpy.types.Mesh, bone_infos : Optional[List[BoneInfo]]) -> ImportMeshRawData:
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
    
    # Mapping to get the vertex index for each loop index
    loop_vertex_mapping = np.empty(loop_count, dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertex_mapping)
    
    msg_import_mesh = ImportMeshRawData()

    # Convert vertex positions
    vertex_positions = np.empty(vertex_count*3, dtype=np.float32)
    mesh.vertices.foreach_get('co', vertex_positions)
    loop_positions = vertex_positions.reshape(-1, 3)[loop_vertex_mapping].ravel()
    _remap_blender_to_resonite(loop_positions)
    msg_import_mesh.vertex_count = loop_count
    msg_import_mesh._positions = loop_positions.tobytes()

    # Convert vertex normals
    normals = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('normal', normals)
    _remap_blender_to_resonite(normals)
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
    if mesh.shape_keys:
        msg_import_mesh.blendshapes = []
        for shape_key_name, shape_key in mesh.shape_keys.key_blocks.items():
            if shape_key.relative_key == shape_key:
                # Skip basis key
                continue
            
            # Blender doesn't support multi-frame shape keys, only ever one frame
            blendshape_frame_vertex_positions = np.empty(vertex_count*3, dtype=np.float32)
            shape_key.data.foreach_get('co', blendshape_frame_vertex_positions)
            blendshape_frame_loop_positions = blendshape_frame_vertex_positions.reshape(-1, 3)[loop_vertex_mapping].ravel()
            _remap_blender_to_resonite(blendshape_frame_loop_positions)
            blendshape_frame_loop_position_deltas = np.subtract(blendshape_frame_loop_positions, loop_positions)
            blendshape_frame = BlendshapeFrameRawData(position=1.0)
            blendshape_frame._position_deltas = blendshape_frame_loop_position_deltas.tobytes()
            msg_import_mesh.blendshapes.append(BlendshapeRawData(
                name=shape_key_name, 
                has_normal_deltas=False,
                has_tangent_deltas=False,
                frames=[ blendshape_frame ]
            ))
    
    # Convert bones (armature) & bone weights
    if bone_infos:
        bone_weight_count = 4 # TODO: Expose as setting # Max amount of bones that can influence a single vertex. Max Resonite supports is 4
        
        # Blender stores bone influences per vertex as links to vertex groups associated with a weight value.
        # Unfortunately, there doesn't seem to be a faster way to access that information.
        vertex_influences = np.empty(vertex_count * bone_weight_count, dtype=[("group", np.int32), ("weight", np.float32)])
        for vert_index, vert in enumerate(mesh.vertices):
            group_index : int = 0
            for group_index, group_element in enumerate(sorted(vert.groups, key=lambda g: g.weight, reverse=True)):
                if group_index == bone_weight_count:
                    # Vertex affected by more than bone_weight_count bones, discard remaining. (Sorted by most influencal bones.)
                    break
                
                vertex_influences[vert_index * bone_weight_count + group_index][0] = group_element.group
                vertex_influences[vert_index * bone_weight_count + group_index][1] = group_element.weight
                
            while group_index < bone_weight_count - 1:
                # Vertex affected by less than bone_weight_count bones, pad remaining.
                group_index += 1
                vertex_influences[vert_index * bone_weight_count + group_index][0] = -1
                vertex_influences[vert_index * bone_weight_count + group_index][1] = 0.0
        
        loop_influences = vertex_influences.reshape(-1, bone_weight_count)[loop_vertex_mapping]
        msg_import_mesh.bones = [ bone_info.bone for bone_info in bone_infos ]
        msg_import_mesh.bone_weight_count = bone_weight_count
        msg_import_mesh._bone_weights = loop_influences.ravel().tobytes()

    # Convert triangles
    triangle_indices = np.empty(triangle_count*3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('loops', triangle_indices)
    _reverse_column_order(triangle_indices) # Reverse winding
    triangle_submesh = TriangleSubmeshRawData()
    triangle_submesh.triangle_count = triangle_count
    triangle_submesh._indices = triangle_indices.tobytes()
    msg_import_mesh.submeshes = [ triangle_submesh ]
    
    return msg_import_mesh


def _find_root_bone(armature : bpy.types.Armature) -> bpy.types.Bone:
    for bone in armature.bones:
        if not bone.parent:
            # Bone has no parent -> Root bone
            return bone
        
    raise ValueError(f"No root bone found in armature!")


def _matrix_to_float4x4(mat : Matrix) -> Float4x4:
    return Float4x4(
        m00=mat[0][0],
        m01=mat[0][1],
        m02=mat[0][2],
        m03=mat[0][3],
        m10=mat[1][0],
        m11=mat[1][1],
        m12=mat[1][2],
        m13=mat[1][3],
        m20=mat[2][0],
        m21=mat[2][1],
        m22=mat[2][2],
        m23=mat[2][3],
        m30=mat[3][0],
        m31=mat[3][1],
        m32=mat[3][2],
        m33=mat[3][3]
    )


def _vector_to_float3(vec : Vector) -> Float3:
    return Float3(
        x=vec[0],
        y=vec[1],
        z=vec[2]
    )


def _quaternion_to_floatQ(quat : Quaternion) -> FloatQ:
    return FloatQ(
        w=quat.w, 
        x=quat.x,
        y=quat.y,
        z=quat.z
    )


async def _import_armature_hierarchy(client : ResoniteLinkClient, root_slot : Union[Slot, SlotProxy], armature : bpy.types.Armature) -> List[BoneInfo]:
    bone_infos : List[BoneInfo] = []
    root_bone = _find_root_bone(armature)

    async def _build_armature_recursive(parent_slot : Union[Slot, SlotProxy], bone : bpy.types.Bone):
        props : BLENDER_RESONITE_SDK_Properties = bpy.context.scene.blender_resonite_sdk # type: ignore
        
        space_correction : Matrix = props.coordinate_conversion_matrix.transposed() # Transposed because Blender's UI mixes up rows / columns
        space_correction_2 : Matrix = props.coordinate_conversion_matrix_2.transposed() # Transposed because Blender's UI mixes up rows / columns
        
        inv_x = Matrix(( # TODO: To constant somewhere.
            (-1.0, 0.0,  0.0, 0.0), 
            (0.0,  1.0,  0.0, 0.0), 
            (0.0,  0.0,  1.0, 0.0),
            (0.0,  0.0,  0.0, 1.0)
        ))
        
        mat : Matrix
        if not bone.parent:
            # Root bone
            mat = space_correction @ bone.matrix_local @ space_correction.inverted() @ space_correction_2

        else:
            # Not the root bone
            mat = inv_x @ bone.parent.matrix_local.inverted() @ bone.matrix_local @ inv_x.inverted()
        
        position, rotation, scale = mat.decompose()
        bone_slot = await client.add_slot(
            name=bone.name, 
            parent=parent_slot,
            position=_vector_to_float3(position),
            rotation=_quaternion_to_floatQ(rotation),
            scale=_vector_to_float3(scale),
        )

        bone_info = BoneInfo(
            bone=Bone(
                name=bone.name, 
                bind_pose=_matrix_to_float4x4(space_correction_2.inverted() @ space_correction @ bone.matrix_local.inverted() @ space_correction.inverted())
            ),
            slot=bone_slot
        )
        bone_infos.append(bone_info)

        for child_bone in bone.children:
            await _build_armature_recursive(bone_slot, child_bone)
    
    await _build_armature_recursive(root_slot, root_bone)

    return bone_infos


class BLENDER_RESONITE_SDK_OT_send_active_object(AsyncOperator):
    bl_idname = 'blender_resonite_sdk.send_active_object'
    bl_label = ""
    bl_description = "Sends the active object to Resonite."

    _object : bpy.types.Object
    _object_name : str
    _armature : Optional[bpy.types.Armature]
    _bone_infos : Optional[List[BoneInfo]]
    _mesh : Optional[bpy.types.Mesh]
    
    def handle_context(self, context):
        if not context.active_object:
            # No active object, abort.
            # TODO: Abort more smartly? We still spawn the task this way.
            return

        self._object = context.active_object
        self._object_name = self._object.name
        
        armature_obj = context.active_object.find_armature()
        if armature_obj:
            # Object has armature
            self._armature = armature_obj.data # type: ignore
            self._object_name = armature_obj.name # Set name of object to name of armature object
        
        if context.active_object.data and type(context.active_object.data) == bpy.types.Mesh:
            # Object has mesh
            self._mesh = context.active_object.data
    
    async def execute_async(self): # type: ignore
        if not self._object:
            # No active object, abort.
            return

        client = ResoniteLinkWebsocketClient()

        @client.on_started
        async def _on_client_started(client : ResoniteLinkClient):
            try:
                # Create slot to attach mesh to.
                object_root_slot = await client.add_slot(name=self._object_name)

                # Adds Grabbable component.
                await object_root_slot.add_component("[FrooxEngine]FrooxEngine.Grabbable")

                # Adds SimpleAvatarProtection for testing in public world.
                await object_root_slot.add_component("[FrooxEngine]FrooxEngine.CommonAvatar.SimpleAvatarProtection")
                
                if self._armature:
                    # Create armature root slot.
                    armature_root_slot = await client.add_slot(name="Armature", parent=object_root_slot)

                    # Import armature as slot hierarchy.
                    self._bone_infos = await _import_armature_hierarchy(client, armature_root_slot, self._armature)

                    # Set up rig component on root with bone references
                    await object_root_slot.add_component(
                        "[FrooxEngine]FrooxEngine.Rig",
                        Bones=SyncList(*[ Reference(target_type="[FrooxEngine]FrooxEngine.Slot", target_id=bone_info.slot.id) for bone_info in self._bone_infos ]) if self._bone_infos else SyncList()
                    )
                
                if self._mesh:
                    # Create mesh root slot
                    mesh_root_slot = await client.add_slot(name=self._object.name, parent=object_root_slot)

                    # Import mesh data
                    msg_import_mesh = _create_mesh_import_message(self._mesh, self._bone_infos)
                    mesh_asset : AssetData = await client.send_message(msg_import_mesh) # type: ignore

                    # Adds a StaticMesh component to the slot and assigns the asset URI of the imported mesh data. 
                    static_mesh = await mesh_root_slot.add_component(
                        "[FrooxEngine]FrooxEngine.StaticMesh", 
                        URL=Field_Uri(mesh_asset.asset_url)
                    )

                    # Adds a PBS_VertexColorMetallic material.
                    material = await mesh_root_slot.add_component(
                        "[FrooxEngine]FrooxEngine.PBS_VertexColorMetallic", 
                        Culling=Field_Enum("Off", "[FrooxEngine]FrooxEngine.Culling"),
                        Smoothness=Field_Float(0.0)
                    )

                    # Creates a mesh renderer for the mesh and material.
                    mesh_renderer = await mesh_root_slot.add_component(
                        "[FrooxEngine]FrooxEngine.SkinnedMeshRenderer" if msg_import_mesh.blendshapes or msg_import_mesh.bone_weights else "[FrooxEngine]FrooxEngine.MeshRenderer", 
                        Mesh=Reference(target_type="[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Mesh>", target_id=static_mesh.id),
                        Materials=SyncList(Reference(target_type="[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Material>", target_id=material.id)),
                        Bones=SyncList(*[ Reference(target_type="[FrooxEngine]FrooxEngine.Slot", target_id=bone_info.slot.id) for bone_info in self._bone_infos ]) if self._bone_infos else SyncList()
                    )

                    # Adds MeshCollider component.
                    await mesh_root_slot.add_component("[FrooxEngine]FrooxEngine.MeshCollider")
            
            finally:
                await client.stop()

        await client.start(auto_discover=True)


# class BLENDER_RESONITE_SDK_OT_send_active_object_evaluated(AsyncOperator):
#     bl_idname = 'blender_resonite_sdk.send_active_object_evaluated'
#     bl_label = ""
#     bl_description = "Applies Modifiers & Sends the active object to Resonite."

#     _object : bpy.types.Object
#     _mesh : bpy.types.Mesh
    
#     def handle_context(self, context):
#         if not context.active_object or not context.scene:
#             return

#         depsgraph = context.evaluated_depsgraph_get()
#         self._object = context.active_object.evaluated_get(depsgraph)
#         self._mesh = self._object.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    
#     async def execute_async(self): # type: ignore
#         if not self._object or not self._mesh:
#             return

#         client = ResoniteLinkWebsocketClient()

#         @client.on_started
#         async def _on_client_started(client : ResoniteLinkClient):
#             try:
#                 # Import mesh data
#                 msg_import_mesh = _create_mesh_import_message(self._mesh)
#                 mesh_asset : AssetData = await client.send_message(msg_import_mesh) # type: ignore
                
#                 # Create slot to attach mesh to.
#                 slot = await client.add_slot(name=self._object.name)
                
#                 # Adds a StaticMesh component to the slot and assigns the asset URI of the imported mesh data. 
#                 static_mesh = await slot.add_component(
#                     "[FrooxEngine]FrooxEngine.StaticMesh", 
#                     URL=Field_Uri(mesh_asset.asset_url)
#                 )

#                 # Adds a PBS_VertexColorMetallic material.
#                 material = await slot.add_component(
#                     "[FrooxEngine]FrooxEngine.PBS_VertexColorMetallic", 
#                     Culling=Field_Enum("Off", "[FrooxEngine]FrooxEngine.Culling"),
#                     Smoothness=Field_Float(0.0)
#                 )

#                 # Creates a mesh renderer for the mesh and material.
#                 mesh_renderer = await slot.add_component(
#                     "[FrooxEngine]FrooxEngine.SkinnedMeshRenderer" if msg_import_mesh.blendshapes or msg_import_mesh.bone_weights else "[FrooxEngine]FrooxEngine.MeshRenderer", 
#                     Mesh=Reference(target_type="[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Mesh>", target_id=static_mesh.id),
#                     Materials=SyncList(Reference(target_type="[FrooxEngine]FrooxEngine.IAssetProvider<[FrooxEngine]FrooxEngine.Material>", target_id=material.id))
#                 )

#                 # Adds MeshCollider component.
#                 await slot.add_component("[FrooxEngine]FrooxEngine.MeshCollider")

#                 # Adds Grabbable component and makes it scalable.
#                 await slot.add_component("[FrooxEngine]FrooxEngine.Grabbable")

#                 # Adds SimpleAvatarProtection for testing in public world.
#                 await slot.add_component("[FrooxEngine]FrooxEngine.CommonAvatar.SimpleAvatarProtection")
            
#             finally:
#                 await client.stop()

#         await client.start(auto_discover=True)


class BLENDER_RESONITE_SDK_Properties(bpy.types.PropertyGroup):
    coordinate_conversion_matrix : bpy.props.FloatVectorProperty(
        name="Coordinate Conversion", 
        size=(4, 4),
        subtype='MATRIX',
        default=(
            (-1.0,  0.0,  0.0,  0.0), 
            ( 0.0,  0.0,  1.0,  0.0), 
            ( 0.0, -1.0,  0.0,  0.0),
            ( 0.0,  0.0,  0.0,  1.0)
        )
    ) # type: ignore

    coordinate_conversion_matrix_2 : bpy.props.FloatVectorProperty(
        name="Coordinate Conversion", 
        size=(4, 4),
        subtype='MATRIX',
        default=(
            ( 1.0,  0.0,  0.0,  0.0), 
            ( 0.0,  0.0,  1.0,  0.0), 
            ( 0.0, -1.0,  0.0,  0.0),
            ( 0.0,  0.0,  0.0,  1.0)
        )
    ) # type: ignore


class BLENDER_RESONITE_SDK_PT_test_panel(bpy.types.Panel):
    bl_idname = "blender_resonite_sdk.test_panel"
    bl_label = "Resonite SDK"

    bl_category = 'Resonite SDK'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    
    def draw (self, context):
        if not self.layout:
            raise ValueError("Layout does not exist!")
        
        props : BLENDER_RESONITE_SDK_Properties = bpy.context.scene.blender_resonite_sdk # type: ignore
        
        self.layout.operator("blender_resonite_sdk.send_active_object", text="Send Active Object")
        self.layout.prop(props, 'coordinate_conversion_matrix')
        self.layout.prop(props, 'coordinate_conversion_matrix_2')
        # self.layout.operator("blender_resonite_sdk.send_active_object_evaluated", text="Apply Modifiers & Send Active Object")


classes = (
    BLENDER_RESONITE_SDK_Properties,
    BLENDER_RESONITE_SDK_OT_send_active_object,
    # BLENDER_RESONITE_SDK_OT_send_active_object_evaluated,
    BLENDER_RESONITE_SDK_PT_test_panel
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)

    bpy.types.Scene.blender_resonite_sdk = bpy.props.PointerProperty(type=BLENDER_RESONITE_SDK_Properties) # type: ignore


def unregister():
    from bpy.utils import unregister_class
    for cls in reversed(classes):
        unregister_class(cls)

    del bpy.types.Scene.blender_resonite_sdk # type: ignore