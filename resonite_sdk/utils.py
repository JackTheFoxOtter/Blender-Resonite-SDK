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
Collection of various helper functions.

"""
from resonitelink.utils.id_registry import IDRegistry
from resonitelink import Float3, FloatQ, Float4x4
from mathutils import Matrix, Vector, Quaternion
from typing import Callable
import threading
import numpy as np
import bpy


def main_thread_only(func : Callable):
    """
    Blenders Python API is not thread-safe. As such, accessing ANY Blender function is only allowed from the main thread.
    This is a small safety decorator that throws an exception if a function is ever called from a thread other than the main thread.
    Any function that utilizes Blender's Python API in any way should be flagged with this.

    """
    def _decorator():
        if threading.main_thread() != threading.current_thread():
            raise Exception(f"Function '{func.__name__}' is only allowed to be called in the main thread, but was called in thread '{threading.current_thread().name}' instead!")

        return func()
    
    return _decorator


_registry : IDRegistry

def get_registry() -> IDRegistry:
    """
    Returns a global IDRegistry instance for Resonite ID allocation.

    """
    global _registry
    
    if not _registry:
        _registry = IDRegistry()
    
    return _registry


@main_thread_only
def find_root_bone(armature : bpy.types.Armature) -> bpy.types.Bone:
    """
    Returns the root bone of an armature.

    """
    for bone in armature.bones:
        if not bone.parent:
            # Bone has no parent -> Root bone
            return bone
        
    raise ValueError(f"No root bone found in armature!")


def remap_blender_to_resonite(arr : np.ndarray):
    """
    Remaps an array of elements using Blender's coordinate system to Resonite's coordinate system.
    Transformation: `X, Y, Z` -> `-X, Z, -Y`

    """
    arr[0::3], arr[1::3], arr[2::3] = -arr[0::3], arr[2::3], -arr[1::3] # Transformation (X, Y, Z) -> (-X, Z, -Y)


def reverse_column_order(arr : np.ndarray):
    """
    For a given array of pairs of 3 elements, reverse the column order.
    Transformation: `X, Y, Z` -> `Z, Y, X`

    """
    arr[0::3], arr[2::3] = arr[2::3], arr[0::3].copy() # Transformation (X, Y, Z) -> (Z, Y, X)


def matrix_to_float4x4(mat : Matrix) -> Float4x4:
    """
    Convertes a mathutils Matrix instance into a ResoniteLink.py Float4x4 instance.

    """
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


def vector_to_float3(vec : Vector) -> Float3:
    """
    Converts a mathutils Vector into a ResoniteLink.py Float3 instance.

    """
    return Float3(
        x=vec[0],
        y=vec[1],
        z=vec[2]
    )


def quaternion_to_floatQ(quat : Quaternion) -> FloatQ:
    """
    Converts a mathutils Quaternion into a ResoniteLink.py FloatQ instance.

    """
    return FloatQ(
        w=quat.w, 
        x=quat.x,
        y=quat.y,
        z=quat.z
    )
