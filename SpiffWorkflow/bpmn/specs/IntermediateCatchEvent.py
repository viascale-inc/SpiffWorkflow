# -*- coding: utf-8 -*-
from __future__ import division
# Copyright (C) 2012 Matthew Hampton
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
# 02110-1301  USA

from ...task import Task
from .BpmnSpecMixin import BpmnSpecMixin
from ...specs.Simple import Simple
from SpiffWorkflow.bpmn.specs.StartEvent import StartEvent

class IntermediateCatchEvent(Simple, BpmnSpecMixin):

    """
    Task Spec for a bpmn:intermediateCatchEvent node.
    """

    def __init__(self, wf_spec, name, event_definition=None, **kwargs):
        """
        Constructor.

        :param event_definition: the EventDefinition that we must wait for.
        """
        super(IntermediateCatchEvent, self).__init__(wf_spec, name, **kwargs)
        self.event_definition = event_definition

    def _update_hook(self, my_task):
        target_state = getattr(my_task, '_bpmn_load_target_state', None)
        message = self.event_definition._message_ready(my_task)
        if message:
            my_task.data = my_task.workflow.last_task.data
            my_task.data[my_task.task_spec.name+'_Response'] = message
            like_me = my_task.workflow.get_tasks_from_spec_name(my_task.task_spec.name)
            #self.accept_message(my_task,self.event_definition.message)
            #self._on_complete_hook(my_task)
            my_task.children = []
            my_task._sync_children(my_task.task_spec.outputs)
            super(IntermediateCatchEvent, self)._update_hook(my_task)
            data = my_task.data
            # the update loop on all waiting tasks copies the information
            # from their parents. I'm not sure what the ramifications are if I
            # disable that.
            for task in like_me:
                task.parent.data = data
        elif target_state == Task.READY or (
                not my_task.workflow._is_busy_with_restore() and
                self.event_definition.has_fired(my_task)):
            super(IntermediateCatchEvent, self)._update_hook(my_task)
        else:
            if not my_task.parent._is_finished():
                return
            if not my_task.state == Task.WAITING:
                my_task._set_state(Task.WAITING)
                if not my_task.workflow._is_busy_with_restore():
                    self.entering_waiting_state(my_task)

    def _on_ready_hook(self, my_task):
        self._predict(my_task)

    def _on_complete_hook(self, my_task):
        for task in my_task.children:
            task.data = my_task.data
            task._set_state(Task.READY)

        my_task._set_state(Task.WAITING)
        #del(my_task.internal_data['has_fired'])

    def accept_message(self, my_task, message):
        if (my_task.state == Task.WAITING and
                self.event_definition._accept_message(my_task, message)):
            self._update(my_task)
            return True
        return False
