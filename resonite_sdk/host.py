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
Host code for running the ResoniteLinkWebsocketClient.

"""
from __future__ import annotations

from typing import Optional
from threading import Thread, Lock
from resonitelink import ResoniteLinkClient, ResoniteLinkWebsocketClient
from resonitelink.utils.session_listener import ResoniteLinkSessionListener


_host : ResoniteLinkHost


def get_host():
    """
    Returns the global ResoniteLinkHost instance.
    
    """
    global _host

    if not _host:
        _host = ResoniteLinkHost()
    
    return _host


class ResoniteLinkHost():
    """
    Host 
    
    """
    _listener : ResoniteLinkSessionListener
    _discovered_session = []

    def start_session_discovery(self):
        pass

    def end_session_discovery(self):
        pass

    def start_session(self):
        pass

    def end_session(self):
        pass
