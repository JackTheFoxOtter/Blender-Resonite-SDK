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
from numpy.typing import NDArray
import numpy as np
import threading
import logging
import asyncio
import bmesh
import time
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
    arr[0::3], arr[1::3], arr[2::3] = -arr[0::3], arr[2::3], -arr[1::3] # Transformation (X, Y, Z) -> (-X, Z, -Y)


def _reverse_column_order(arr : np.ndarray):
    """
    For a given array of pairs of 3 elements, reverse the column order.
    Transformation: `X, Y, Z` -> `Z, Y, X`

    """
    arr[0::3], arr[2::3] = arr[2::3], arr[0::3].copy() # Transformation (X, Y, Z) -> (Z, Y, X)


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


def _create_mesh_import_message(
    mesh : bpy.types.Mesh, 
    bone_infos : Optional[List[BoneInfo]] = None,
    bone_weight_count : int = 4,
    color_attribute_index : Optional[int] = None,
    uv_layer_indices : Optional[List[int]] = None,
) -> ImportMeshRawData:
    """
    Creates a ImportMeshRawData message from the provided Blender mesh.

    Note
    ----
    In Blender, faces are defined through loops, the same vertex can be part of multiple loops.
    To ensure we get all the correct face data (shading, UVs etc.), we need to import each loop as a separate vertex into Resonite.

    """
    if bone_infos and not all([ type(info) == BoneInfo for info in bone_infos ]):
        raise ValueError("Attribute 'bone_infos' must be a list of BoneInfo instances.")
    
    if not ( type(bone_weight_count) == int and 1 <= bone_weight_count <= 4 ):
        raise ValueError("Attribute 'bone_weight_count' must be an integer between 1 and 4.")
    
    if color_attribute_index is not None and not ( type(color_attribute_index) == int and 0 <= color_attribute_index < len(mesh.color_attributes)):
        raise ValueError("Attribute 'color_attribute_index' must be an integer and a valid color attribute index of the mesh.")
    
    if uv_layer_indices is not None and not ( 1 <= len(uv_layer_indices) <= 4 and all([ type(uv) == int and 0 <= uv < len(mesh.uv_layers) for uv in uv_layer_indices ]) ):
        raise ValueError("Attribute 'uv_layer_indices' must be a list of 1 to 4 integers where each value is a valid uv layer index of the mesh.")

    t_start = time.time()
    msg_import_mesh = ImportMeshRawData()

    mesh.calc_loop_triangles()
    mesh.calc_tangents()
    mesh.calc_smooth_groups()

    vertex_count = len(mesh.vertices)
    loop_count = len(mesh.loops)
    triangle_count = len(mesh.loop_triangles)

    # Mapping loop index -> vertex index
    loop_vertex_mapping = np.empty(loop_count, dtype=np.int32)
    mesh.loops.foreach_get('vertex_index', loop_vertex_mapping)
    
    # NOTE: I attempted to directly write the data from Blender into views of loop_data, but couldn't figure out a view to do that
    #       without numpy creating a copy of the array, sort of missing the point. This might still be possible as an optimization,
    #       I just couldn't figure it out myself.

    loop_data_segments : List[NDArray] = []

    # Loop positions from referenced vertices
    vertex_positions = np.empty(vertex_count*3, dtype=np.float32)
    mesh.vertices.foreach_get('co', vertex_positions)
    _remap_blender_to_resonite(vertex_positions)
    loop_data_segments.append(vertex_positions.reshape(-1, 3, copy=False)[loop_vertex_mapping])

    # Loop normals
    loop_normals = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('normal', loop_normals)
    _remap_blender_to_resonite(loop_normals)
    loop_data_segments.append(loop_normals.reshape(-1, 3, copy=False))

    # Loop tangents
    loop_tangents = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('tangent', loop_tangents)
    _remap_blender_to_resonite(loop_tangents)
    loop_data_segments.append(loop_tangents.reshape(-1, 3, copy=False))

    # Loop bitangent signs
    loop_bitangent_signs = np.empty(loop_count, dtype=np.float32)
    mesh.loops.foreach_get('bitangent_sign', loop_bitangent_signs)
    loop_data_segments.append(loop_bitangent_signs.reshape(-1, 1, copy=False))

    if color_attribute_index is not None:
        # Loop colors
        loop_colors = np.empty(loop_count*4, dtype=np.float32)
        color_attribute : Union[bpy.types.FloatColorAttribute, bpy.types.ByteColorAttribute] = mesh.color_attributes[color_attribute_index] # type: ignore
        color_attribute.data.foreach_get('color', loop_colors)
        loop_data_segments.append(loop_colors.reshape(-1, 4, copy=False))
    
    if uv_layer_indices is not None:
        # Loop UVs
        for uv_layer_index in range(len(uv_layer_indices)):
            loop_uvs = np.empty(loop_count*2, dtype=np.float32)
            uv_layer : bpy.types.MeshUVLoopLayer = mesh.uv_layers[uv_layer_index]
            uv_layer.data.foreach_get('uv', loop_uvs)
            loop_uvs = np.round(loop_uvs, 4)
            loop_data_segments.append(loop_uvs.reshape(-1, 2, copy=False))
    
    # Combine all loop data into one big 2d array
    loop_data = np.hstack(loop_data_segments)

    # Remove all duplicate entries from the loop data array
    unique_loop_data, unique_loop_indices, unique_loop_inverse_mapping = np.unique(loop_data, axis=0, return_index=True, return_inverse=True)
    unique_loop_inverse_mapping = unique_loop_inverse_mapping.astype(np.int32) # int64 -> int32
    unique_loop_vertex_mapping = loop_vertex_mapping[unique_loop_indices]

    unique_loop_count = len(unique_loop_data)
    offset = 0

    # Write Resonite vertex positions
    unique_loop_positions = unique_loop_data[:, offset:offset+3]
    msg_import_mesh.vertex_count = unique_loop_count
    msg_import_mesh._positions = unique_loop_positions.tobytes()
    offset += 3

    # Write Resonite vertex normals
    unique_loop_normals = unique_loop_data[:, offset:offset+3]
    msg_import_mesh.has_normals = True
    msg_import_mesh._normals = unique_loop_normals.tobytes()
    offset += 3

    # Write Resonite vertex tangents & bitangent signs
    unique_loop_tangents_and_bitangent_signs = unique_loop_data[:, offset:offset+4]
    msg_import_mesh.has_tangents = True
    msg_import_mesh._tangents = unique_loop_tangents_and_bitangent_signs.tobytes()
    offset += 4

    if color_attribute_index is not None:
        # Write Resonite vertex colors
        unique_loop_colors = unique_loop_data[:, offset:offset+4]
        msg_import_mesh.has_colors = True
        msg_import_mesh._colors = unique_loop_colors.tobytes()
        offset += 4
    
    if uv_layer_indices is not None:
        # Write Resonite UVs
        msg_import_mesh.uv_channel_dimensions = [ 2 ] * len(uv_layer_indices)
        msg_import_mesh._uvs = [ ]
        for uv_layer_index in range(len(uv_layer_indices)):
            unique_uvs = unique_loop_data[:, offset:offset+2]
            msg_import_mesh._uvs.append(unique_uvs.tobytes())
            offset += 2
    
    # Write Resonite blendshapes
    if mesh.shape_keys:
        msg_import_mesh.blendshapes = []
        for shape_key_name, shape_key in mesh.shape_keys.key_blocks.items():
            if shape_key.relative_key == shape_key:
                # Skip basis key
                continue
            
            # Vertex positions for shape keys
            # NOTE: Blender doesn't support multi-frame shape keys, only ever one frame
            blendshape_frame_vertex_positions = np.empty(vertex_count*3, dtype=np.float32)
            shape_key.data.foreach_get('co', blendshape_frame_vertex_positions)
            _remap_blender_to_resonite(blendshape_frame_vertex_positions)
            blendshape_frame_loop_positions = blendshape_frame_vertex_positions.reshape(-1, 3)[unique_loop_vertex_mapping]
            
            # Write Resonite blendshape frames
            blendshape_frame_loop_position_deltas = np.subtract(blendshape_frame_loop_positions, unique_loop_positions)
            blendshape_frame = BlendshapeFrameRawData(position=1.0)
            blendshape_frame._position_deltas = blendshape_frame_loop_position_deltas.tobytes()
            msg_import_mesh.blendshapes.append(BlendshapeRawData(
                name=shape_key_name, 
                has_normal_deltas=False,
                has_tangent_deltas=False,
                frames=[ blendshape_frame ]
            ))
    
    # Write Resonite bones (armature) & bone weights
    if bone_infos:
        # Vertex influences for bones
        # NOTE: Blender stores bone influences per vertex as links to vertex groups associated with a weight value.
        #       Unfortunately, there doesn't seem to be a faster way to access that information.
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
        
        loop_influences = vertex_influences.reshape(-1, bone_weight_count)[unique_loop_vertex_mapping]
        
        # Write Resonite bone weights
        msg_import_mesh.bones = [ bone_info.bone for bone_info in bone_infos ]
        msg_import_mesh.bone_weight_count = bone_weight_count
        msg_import_mesh._bone_weights = loop_influences.tobytes()

    # Loop triangles & triangle material indices
    loop_triangle_indices = np.empty(triangle_count*3, dtype=np.int32)
    mesh.loop_triangles.foreach_get('loops', loop_triangle_indices)
    unique_loop_triangle_indices = unique_loop_inverse_mapping[loop_triangle_indices]
    _reverse_column_order(unique_loop_triangle_indices) # Reverse winding
    loop_material_indices = np.empty(triangle_count, dtype=np.int32)
    mesh.loop_triangles.foreach_get('material_index', loop_material_indices)
    triangle_and_material_indices = np.hstack([ unique_loop_triangle_indices.reshape(-1, 3), loop_material_indices.reshape(-1, 1) ])
    
    # Write Resonite submeshes
    # NOTE: Resonite uses one submesh per material index
    msg_import_mesh.submeshes = []
    material_count = np.max(loop_material_indices) + 1
    for material_index in range(material_count):
        submesh_mask = triangle_and_material_indices[:, 3] == material_index # Boolean mask for material index
        submesh_triangle_indices = triangle_and_material_indices[submesh_mask, :3] # Don't include material index column
        triangle_submesh = TriangleSubmeshRawData()
        triangle_submesh.triangle_count = len(submesh_triangle_indices)
        triangle_submesh._indices = submesh_triangle_indices.tobytes()
        msg_import_mesh.submeshes.append(triangle_submesh)
    
    t_end = time.time()
    logging.info(f"Created ResoniteLink ImportMeshRawData message for mesh '{mesh.name}' in {t_end - t_start}s")
    
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
    """
    Imports an armature as a slot hierarchy into Resonite.
    Returns a list of all bone infos, including mapping and bone pose matrix.

    """
    bone_infos : List[BoneInfo] = []
    root_bone = _find_root_bone(armature)

    # NOTE: Potentially the matrix stuff can be improved. I have however already put a significant amount ot work into it,
    #       and it seems to work correctly, so I've decided to not touch it again for the time being.
    
    space_correction = Matrix ((
        (-1.0,  0.0,  0.0,  0.0), 
        ( 0.0,  0.0,  1.0,  0.0), 
        ( 0.0, -1.0,  0.0,  0.0),
        ( 0.0,  0.0,  0.0,  1.0)
    ))
    
    space_correction_2 = Matrix((
        ( 1.0,  0.0,  0.0,  0.0), 
        ( 0.0,  0.0,  1.0,  0.0), 
        ( 0.0, -1.0,  0.0,  0.0),
        ( 0.0,  0.0,  0.0,  1.0)
    ))
    
    inv_x = Matrix((
        (-1.0,  0.0,  0.0,  0.0), 
        ( 0.0,  1.0,  0.0,  0.0), 
        ( 0.0,  0.0,  1.0,  0.0),
        ( 0.0,  0.0,  0.0,  1.0)
    ))

    async def _build_armature_recursive(parent_slot : Union[Slot, SlotProxy], bone : bpy.types.Bone):
        """
        Recursively walks the armature and imports each bone as a Slot underneath the armature root slot.

        """
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
                    msg_import_mesh = _create_mesh_import_message(
                        self._mesh, 
                        self._bone_infos, 
                        bone_weight_count=4, # TODO: Setting
                        color_attribute_index=self._mesh.color_attributes.active_color_index if self._mesh.color_attributes.active_color_index is not None and self._mesh.color_attributes.active_color_index > 0 else None, # TODO: Setting 
                        uv_layer_indices=[ self._mesh.uv_layers.active_index ] if self._mesh.uv_layers.active_index is not None else [ ] # TODO: Setting
                    )

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
    pass


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