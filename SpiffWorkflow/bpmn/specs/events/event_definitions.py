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

import sys
import datetime
import logging
import datetime

from builtins import object

LOG = logging.getLogger(__name__)


class EventDefinition(object):
    """
    This is the base class for Event Definitions.  It implements the default throw/catch
    behavior for events.

    If internal is true, this event should be thrown to the current workflow
    If external is true, this event should be thrown to the outer workflow

    Default throw behavior is to send the event based on the values of the internal
    and external flags.
    Default catch behavior is to set the event to fired
    """

    def __init__(self):
        # Ideally I'd mke these parameters, but I don't want to them to be parameters
        # for any subclasses (as they are based on event type, not user choice) and
        # I don't want to write a separate deserializer for every every type.
        self.internal, self.external = True, True

    def has_fired(self, my_task):
        return my_task._get_internal_data('event_fired', False)

    def catch(self, my_task, event_definition=None):
        my_task._set_internal_data(event_fired=True) 

    def throw(self, my_task):
        self._throw(
            event=my_task.task_spec.event_definition, 
            workflow=my_task.workflow, 
            outer_workflow=my_task.workflow.outer_workflow
        )

    def reset(self, my_task):
        my_task._set_internal_data(event_fired=False)

    def _throw(self, event, workflow, outer_workflow):
        # This method exists because usually we just want to send the event in our
        # own task spec, but we can't do that for message events.
        # We also don't have a more sophisticated method for addressing events to
        # a particular process, but this at least provides a mechanism for distinguishing
        # between processes and subprocesses.
        if self.internal:
            workflow.catch(event)
        if self.external and workflow != outer_workflow:
            outer_workflow.catch(event)

    def __eq__(self, other):
        return self.__class__.__name__ == other.__class__.__name__

    def serialize(self):
        return { 
            'classname': self.__class__.__module__ + '.' + self.__class__.__name__,
            'internal': self.internal,
            'external': self.external,
        }

    @classmethod
    def deserialize(cls, dct):
        dct.pop('classname')
        internal, external = dct.pop('internal'), dct.pop('external')
        obj = cls(**dct)
        obj.internal, obj.external = internal, external
        return obj

class NamedEventDefinition(EventDefinition):
    """
    Extend the base event class to provide a name for the event.  Most throw/catch events
    have names that names that will be used to identify the event.

    :param name: the name of this event
    """

    def __init__(self, name):
        super(NamedEventDefinition, self).__init__()
        self.name = name

    def reset(self, my_task):
        super(NamedEventDefinition, self).reset(my_task)

    def __eq__(self, other):
        return self.__class__.__name__ == other.__class__.__name__ and self.name == other.name

    def serialize(self):
        retdict = super(NamedEventDefinition, self).serialize()
        retdict['name'] = self.name
        return retdict


class CancelEventDefinition(EventDefinition):
    """
    Cancel events are only handled by the outerworkflow, as they can only be used inside
    of transaction subprocesses.
    """
    def __init__(self):
        super(CancelEventDefinition, self).__init__()
        self.internal = False

class ErrorEventDefinition(NamedEventDefinition):
    """
    Error events can occur only in subprocesses and as subprocess boundary events.  They're
    matched by code rather than name.
    """

    def __init__(self, name, error_code=None):
        super(ErrorEventDefinition, self).__init__(name)
        self.error_code = error_code
        self.internal = False

    def __eq__(self, other):
        return self.__class__.__name__ == other.__class__.__name__ and self.error_code in [ None, other.error_code ]

    def serialize(self):
        retdict = super(ErrorEventDefinition, self).serialize()
        retdict['error_code'] = self.error_code
        return retdict

class EscalationEventDefinition(NamedEventDefinition):
    """
    Escalation events have names, though they don't seem to be used for anything.  Instead
    the spec says that the escalation code should be matched.
    """

    def __init__(self, name, escalation_code=None):
        """
        Constructor.

        :param escalation_code: The escalation code this event should
        react to. If None then all escalations will activate this event.
        """
        super(EscalationEventDefinition, self).__init__(name)
        self.escalation_code = escalation_code

    def __eq__(self, other):
        return self.__class__.__name__ == other.__class__.__name__ and self.escalation_code in [ None, other.escalation_code ]

    def serialize(self):
        retdict = super(EscalationEventDefinition, self).serialize()
        retdict['escalation_code'] = self.escalation_code
        return retdict


class MessageEventDefinition(NamedEventDefinition):
    """
    Message Events have both a name and a payload.
    """

    # It is not entirely clear how the payload is supposed to be handled, so I have 
    # deviated from what the earlier code did as little as possible, but I believe
    # this should be revisited: for one thing, we're relying on some Camunda-sepcific
    # properties.

    def __init__(self, name, payload=None, result_var=None):

        super(MessageEventDefinition, self).__init__(name)
        self.payload = payload
        self.result_var = result_var

        # The BPMN spec says that Messages should not be used within a process; however
        # we're doing this wrong so I am putting this here as a reminder that it should be
        # fixed, but commenting it out.

        #self.internal = False

    def catch(self, my_task, event_definition):
        
        # It seems very stupid to me that the sender of the message should define the
        # name of the variable the payload is saved in (the receiver ought to decide
        # what to do with it); however, Camunda puts the field on the sender, not the
        # receiver.
        if event_definition.result_var is None:
            result_var = f'{my_task.task_spec.name}_Response' 
        else:
            result_var = event_definition.result_var
        my_task.internal_data[event_definition.name] = result_var, event_definition.payload
        super(MessageEventDefinition, self).catch(my_task, event_definition)

    def throw(self, my_task):
        # We need to evaluate the message payload in the context of this task
        result = my_task.workflow.script_engine.evaluate(my_task, self.payload)
        # We can't update our own payload, because if this task is reached again
        # we have to evaluate it again so we have to create a new event
        event = MessageEventDefinition(self.name, payload=result, result_var=self.result_var)
        self._throw(event, my_task.workflow, my_task.workflow.outer_workflow)

    def reset(self, my_task):
        my_task.internal_data.pop(self.name, None)
        super(MessageEventDefinition, self).reset(my_task)

    def serialize(self):
        retdict = super(MessageEventDefinition, self).serialize()
        retdict['payload'] = self.payload
        retdict['result_var'] = self.result_var
        return retdict


class NoneEventDefinition(EventDefinition):
    """
    This class defines behavior for NoneEvents.  We override throw to do nothing.
    """
    def __init__(self):
        self.internal, self.external = False, False

    def throw(self, my_task):
        pass

    def reset(self, my_task):
        pass


class SignalEventDefinition(NamedEventDefinition):
    """The SignalEventDefinition is the implementation of event definition used for Signal Events."""
    pass

class TerminateEventDefinition(EventDefinition):
    """The TerminateEventDefinition is the implementation of event definition used for Termination Events."""
    
    def __init__(self):
        super(TerminateEventDefinition, self).__init__()
        self.external = False

class TimerEventDefinition(EventDefinition):
    """
    The TimerEventDefinition is the implementation of event definition used for
    Catching Timer Events (Timer events aren't thrown).
    """

    def __init__(self, label, dateTime):
        """
        Constructor.

        :param label: The label of the event. Used for the description.

        :param dateTime: The dateTime expression for the expiry time. This is
        passed to the Script Engine and must evaluate to a datetime (in the case of
        a time-date event) or a timedelta (in the case of a duration event).
        """
        super(TimerEventDefinition, self).__init__()
        self.label = label
        self.dateTime = dateTime

    def has_fired(self, my_task):
        """
        The Timer is considered to have fired if the evaluated dateTime
        expression is before datetime.datetime.now()
        """
        dt = my_task.workflow.script_engine.evaluate(my_task, self.dateTime)
        if isinstance(dt,datetime.timedelta):
            if my_task._get_internal_data('start_time',None) is not None:
                start_time = datetime.datetime.strptime(my_task._get_internal_data('start_time',None),'%Y-%m-%d '
                                                                                                   '%H:%M:%S.%f')
                elapsed = datetime.datetime.now() - start_time
                return elapsed > dt
            else:
                my_task.internal_data['start_time'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
                return False

        if dt is None:
            return False
        if isinstance(dt, datetime.datetime):
            if dt.tzinfo:
                tz = dt.tzinfo
                now = tz.fromutc(datetime.datetime.utcnow().replace(tzinfo=tz))
            else:
                now = datetime.datetime.now()
        else:
            # assume type is a date, not datetime
            now = datetime.date.today()
        return now > dt

    def serialize(self):
        retdict = super(TimerEventDefinition, self).serialize()
        retdict['label'] = self.label
        retdict['dateTime'] = self.dateTime
        return retdict


class CycleTimerEventDefinition(EventDefinition):
    """
    The TimerEventDefinition is the implementation of event definition used for
    Catching Timer Events (Timer events aren't thrown).

    The cycle definition should evaluate to a tuple of
    (n repetitions, repetition duration)
    """
    def __init__(self, label, cycle_definition):

        super(CycleTimerEventDefinition, self).__init__()
        self.label = label
        # The way we're using cycle timers doesn't really align with how the BPMN spec
        # describes is (the example of "every monday at 9am")
        # I am not sure why this isn't a subprocess with a repeat count that starts
        # with a duration timer
        self.cycle_definition = cycle_definition

    def has_fired(self, my_task):
        # We will fire this timer whenever a cycle completes
        # The task itself will manage counting how many times it fires

        repeat, delta = my_task.workflow.script_engine.evaluate(my_task, self.cycle_definition)

        # This is the first time we've entered this event
        if my_task.internal_data.get('repeat') is None:
            my_task.internal_data['repeat'] = repeat
        if my_task.get_data('repeat_count') is None:
            # This is now a looping task, and if we use internal data, the repeat count won't persist
            my_task.set_data(repeat_count=0)

        now = datetime.datetime.now()
        if my_task._get_internal_data('start_time') is None:
            start_time = now
            my_task.internal_data['start_time'] = now.strftime('%Y-%m-%d %H:%M:%S.%f')
        else:
            start_time = datetime.datetime.strptime(
                my_task._get_internal_data('start_time'),
                '%Y-%m-%d %H:%M:%S.%f'
            )
        
        if my_task.get_data('repeat_count') >= repeat:
            return False
        elif (now - start_time) < delta:
            return False
        return True

    def reset(self, my_task):
        repeat_count = my_task.get_data('repeat_count')
        if repeat_count is None:
            # If this is a boundary event, then repeat count will not have been set
            my_task.set_data(repeat_count=0)
        else:
            my_task.set_data(repeat_count=repeat_count + 1)
        my_task.internal_data['start_time'] = None
        super(CycleTimerEventDefinition, self).reset(my_task)

    def serialize(self):
        retdict = super(CycleTimerEventDefinition, self).serialize()
        retdict['label'] = self.label
        retdict['cycle_definition'] = self.cycle_definition
        return retdict

