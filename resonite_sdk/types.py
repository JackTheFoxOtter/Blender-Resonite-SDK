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
Types and data classes.

"""
from resonitelink import Bone


class BoneInfo():
    _bone : Bone
    _slot_id : str

    @property
    def bone(self) -> Bone:
        return self._bone
    
    @property
    def slot_id(self) -> str:
        return self._slot_id

    def __init__(self, bone : Bone, slot_id : str):
        self._bone = bone
        self._slot_id = slot_id
