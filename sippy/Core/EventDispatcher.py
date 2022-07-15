# Copyright (c) 2003-2005 Maxim Sobolev. All rights reserved.
# Copyright (c) 2006-2018 Sippy Software, Inc. All rights reserved.
#
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
# list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation and/or
# other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from sippy.Time.MonoTime import MonoTime
import os
from anyio import (
    create_task_group, run, sleep,
    create_memory_object_stream, TASK_STATUS_IGNORED,
    CancelScope
)
from anyio.abc import TaskStatus
import inspect
import logging


LOG = logging.getLogger("sippy-async")


class Cancellable:
    """Duck-cousin of sippy.Core.EventDispatcher.EventListener
    Wrapper around a coroutine to allow
    cancellation.

    self._tg is set once the coroutine is scheduled
    and is used for cancellation.
    """

    def __init__(self, ed, task, nticks, secs):
        self._task = task
        self.ed = ed
        self._scope = None
        self.nticks = nticks
        self.is_cancelled = False
        self.secs = secs

    def cancel(self):
        if self.ed.is_running:
            if self._scope:
                self._scope.cancel()
            else:
                self.is_cancelled = True
        else:
            try:
                self.ed.tpending.remove(self)
            except Exception:
                pass

    def go(self):
        self.ed.go_timer(self)

    async def run_task(self):
        if self.is_cancelled:
            LOG.warning("Timer is cancelled before starting")
            return
        with CancelScope() as self._scope:
            await self._task()

            if self.nticks is not None and (self.nticks == -1 or self.nticks > 1):
                # reschedule myself
                if self.nticks > 1:
                    self.nticks -= 1
                await self.ed.tsend.send(self)


def _twrapper(ed, timeout_cb, ival, nticks, abs_time, *cb_params):
    if abs_time:
        secs = max(ival - MonoTime(), 0.0)
    else:
        secs = ival
    async def _task():
        await sleep(secs)

        if inspect.iscoroutinefunction(timeout_cb):
            await timeout_cb(*cb_params)
        else:
            timeout_cb(*cb_params)

    return Cancellable(ed, _task, nticks, secs)


class EventDispatcher:
    """Duck-cousin of sippy.Core.EventDispatcher.EventDispatcher"""

    def __init__(self, freq=100.0):
        self.is_running = False
        self.tpending = []
        self.servers = []
        self.tsend, self.trecv = (None, None)

    def go_timer(self, k):
        """Schedules a timer if the event loop is running;
        else just add it to a pending list.
        """

        if self.is_running:
            self.tg.start_soon(k.run_task)
        else:
            self.tpending.append(k)

    async def _timer_wait(self):
        """This coroutine is also used to signal to exit the loop.
        Just set self.inbox = None and set self._timer
        """

        async with self.trecv:
            async for item in self.trecv:
                if item is None:
                    break
                self.tg.start_soon(item.run_task)

    async def aloop(self, *, task_status: TaskStatus = TASK_STATUS_IGNORED):
        """Runs event loop forever."""

        # overloaded member: used to schedule timers
        # and exit the loop if self.inbox = None

        self.is_running = True
        self.tsend, self.trecv = create_memory_object_stream(256)

        async with create_task_group() as self.tg:
            # schedule pending timers
            while self.tpending:
                await self.tsend.send(self.tpending.pop())

            while self.servers:
                self.tg.start_soon(self.servers.pop().run)

            # this task runs the event loop forever...
            self.tg.start_soon(self._timer_wait)
            task_status.started()

    def loop(self):
        run(self.aloop, backend=os.getenv("SIPPY_ASYNC_BACKEND", "asyncio"))

    def regTimer(self, timeout_cb, ival, nticks, abs_time, *cb_params):
        if nticks == 0:
            return
        timer = _twrapper(self, timeout_cb, ival, nticks, abs_time, *cb_params)
        return timer

    def regServer(self, obj):
        self.servers.append(obj)

    def breakLoop(self):

        self.is_running = False
        self.tg.cancel_scope.cancel()


ED2 = EventDispatcher()
