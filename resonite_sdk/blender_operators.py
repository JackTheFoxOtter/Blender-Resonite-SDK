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
    ResoniteLinkClient, \
    ResoniteLinkWebsocketClient, \
    AssetData, \
    Field_Uri, Field_Enum, Field_Float, Reference, SyncList
from typing import Set, Optional, List
import threading
import asyncio
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


class BLENDER_RESONITE_SDK_OT_test(bpy.types.Operator):
    bl_idname = 'blender_resonite_sdk.test'
    bl_label = ""
    bl_description = "Test operator for development."

    def execute(self, context) -> Set[str]: # type: ignore
        props : BLENDER_RESONITE_SDK_PG_main = bpy.context.scene.blender_resonite_sdk # type: ignore
        sessions : bpy.types.CollectionProperty = props.link_sessions

        sessions.clear() # type: ignore

        session_item : BLENDER_RESONITE_SDK_PG_resonite_link_session_item = sessions.add() # type: ignore
        session_item.name = "Test Session 1"
        session_item.port = 54321

        session_item : BLENDER_RESONITE_SDK_PG_resonite_link_session_item = sessions.add() # type: ignore
        session_item.name = "Test Session 2"
        session_item.port = 12345

        return { 'FINISHED' }


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
                    self._bone_infos = await import_armature_hierarchy(client, armature_root_slot, self._armature)

                    # Set up rig component on root with bone references
                    await object_root_slot.add_component(
                        "[FrooxEngine]FrooxEngine.Rig",
                        Bones=SyncList(*[ Reference(target_type="[FrooxEngine]FrooxEngine.Slot", target_id=bone_info.slot.id) for bone_info in self._bone_infos ]) if self._bone_infos else SyncList()
                    )
                
                if self._mesh:
                    # Create mesh root slot
                    mesh_root_slot = await client.add_slot(name=self._object.name, parent=object_root_slot)

                    # Import mesh data
                    msg_import_mesh = create_mesh_import_message(
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
