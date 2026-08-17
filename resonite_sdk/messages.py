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
from .types import *
from .utils import *
from resonitelink import \
    ImportMeshRawData, \
    Slot, \
    TriangleSubmeshRawData, \
    BlendshapeRawData, BlendshapeFrameRawData, \
    Bone, \
    Reference, \
    Message, AddSlot, \
    Field_Float3, Field_FloatQ
from mathutils import Matrix
from typing import Optional, Union, List, Tuple
from numpy.typing import NDArray
import numpy as np
import logging
import time
import bpy


@main_thread_only
def create_armature_import_messages(root_slot_id : str, armature : bpy.types.Armature) -> Tuple[List[Message], List[BoneInfo]]:
    """
    Creates messages to import a Blender armature as a slot hierarchy into Resonite.
    Returns a list of AddSlot messages and list of bone infos, which include bone mapping and bone pose matrix.

    """
    messages : List[Message] = []
    bone_infos : List[BoneInfo] = []
    root_bone = find_root_bone(armature)

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

    def _build_armature_recursive(parent_slot_id : str, bone : bpy.types.Bone):
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
        
        bone_slot_id = get_registry().generate_id()
        position, rotation, scale = mat.decompose()
        parent_slot_id = parent_slot_id
        msg_create_slot = AddSlot(data=Slot(
            id = bone_slot_id, 
            parent = Reference(target_id=parent_slot_id, target_type="[FrooxEngine]FrooxEngine.Slot"),
            position=Field_Float3(vector_to_float3(position)),
            rotation=Field_FloatQ(quaternion_to_floatQ(rotation)),
            scale=Field_Float3(vector_to_float3(scale)),
        ))
        messages.append(msg_create_slot)

        bone_info = BoneInfo(
            bone=Bone(
                name=bone.name, 
                bind_pose=matrix_to_float4x4(space_correction_2.inverted() @ space_correction @ bone.matrix_local.inverted() @ space_correction.inverted())
            ),
            slot_id=bone_slot_id
        )
        bone_infos.append(bone_info)

        for child_bone in bone.children:
            _build_armature_recursive(bone_slot_id, child_bone)
    
    _build_armature_recursive(root_slot_id, root_bone)

    return messages, bone_infos


@main_thread_only
def create_mesh_import_message(
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
    In Blender, polygons are defined through loops of vertices, the same vertex can be part of multiple loops.
    First, this code collects the relevant vertex data for every loop of the mesh. This data includes duplicate entries.
    Then all of that loop data is put into one big 2d array, before duplicates are removed from it.
    This way every unique loop point (postion, normal, tangent, color, UV coordinates) maps to a unique vertex in Resonite.

    For some reason, this code currently seems to create more unique vertices than the mesh would have if exported as a .glb file.
    In my test example, an avatar I converted had ~500 extra vertices compared to the .glb export. I'm not quite sure where those
    extra vertices are coming from. This should still be investigated and improved.

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

    # Vertex indices. Adding these to the loop data ensures that only loops belonging to the same vertex get merged later on.
    loop_data_segments.append(loop_vertex_mapping.astype(np.float32).reshape(-1, 1, copy=False))

    # Loop positions from referenced vertices
    vertex_positions = np.empty(vertex_count*3, dtype=np.float32)
    mesh.vertices.foreach_get('co', vertex_positions)
    remap_blender_to_resonite(vertex_positions)
    loop_data_segments.append(vertex_positions.reshape(-1, 3, copy=False)[loop_vertex_mapping])

    # Loop normals
    loop_normals = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('normal', loop_normals)
    remap_blender_to_resonite(loop_normals)
    loop_data_segments.append(loop_normals.reshape(-1, 3, copy=False))

    # Loop tangents
    loop_tangents = np.empty(loop_count*3, dtype=np.float32)
    mesh.loops.foreach_get('tangent', loop_tangents)
    remap_blender_to_resonite(loop_tangents)
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
    unique_loop_indices = unique_loop_indices.astype(np.int32) # int64 -> int32
    unique_loop_inverse_mapping = unique_loop_inverse_mapping.astype(np.int32) # int64 -> int32
    unique_loop_vertex_mapping = loop_vertex_mapping[unique_loop_indices]
    unique_loop_count = len(unique_loop_data)
    
    # Start at 1 because we don't actually care about the vertex indices from here on
    offset = 1

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
            remap_blender_to_resonite(blendshape_frame_vertex_positions)
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
    reverse_column_order(unique_loop_triangle_indices) # Reverse winding
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
