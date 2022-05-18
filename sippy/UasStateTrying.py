# Copyright (c) 2003-2005 Maxim Sobolev. All rights reserved.
# Copyright (c) 2006-2014 Sippy Software, Inc. All rights reserved.
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

from sippy.UaStateGeneric import UaStateGeneric
from sippy.CCEvents import (
    CCEventRing,
    CCEventConnect,
    CCEventFail,
    CCEventRedirect,
    CCEventDisconnect,
    CCEventPreConnect,
)
from sippy.SipContact import SipContact
from sippy.SipAddress import SipAddress
from sippy.Time.Timeout import TimeoutAbsMono
from functools import partial


class UasStateTrying(UaStateGeneric):
    sname = "Trying(UAS)"

    async def recvEvent(self, event):
        if isinstance(event, CCEventRing):
            scode = event.getData()
            if scode == None:
                code, reason, body = (180, "Ringing", None)
            else:
                code, reason, body = scode
                if code == 100:
                    return None
                if (
                    body != None
                    and self.ua.on_local_sdp_change != None
                    and body.needs_update
                ):
                    self.ua.on_local_sdp_change(
                        body, partial(self.ua.recvEvent, event)
                    )
                    return None
            self.ua.lSDP = body
            await self.ua.sendUasResponse(code, reason, body)
            if self.ua.no_progress_timer != None:
                self.ua.no_progress_timer.cancel()
                self.ua.no_progress_timer = None
                if self.ua.expire_time != None:
                    self.ua.expire_timer = TimeoutAbsMono(
                        self.ua.expires, self.ua.expire_mtime
                    )
            if self.ua.p1xx_ts == None:
                self.ua.p1xx_ts = event.rtime
            return (UasStateRinging, self.ua.ring_cbs, event.rtime, event.origin, code)
        elif isinstance(event, CCEventConnect) or isinstance(event, CCEventPreConnect):
            code, reason, body = event.getData()
            if (
                body != None
                and self.ua.on_local_sdp_change != None
                and body.needs_update
            ):
                self.ua.on_local_sdp_change(body, partial(self.ua.recvEvent, event))
                return None
            if event.extra_headers != None:
                extra_headers = tuple(event.extra_headers)
            else:
                extra_headers = None
            self.ua.lSDP = body
            if self.ua.no_progress_timer != None:
                self.ua.no_progress_timer.cancel()
                self.ua.no_progress_timer = None
            if isinstance(event, CCEventConnect):
                await self.ua.sendUasResponse(
                    code,
                    reason,
                    body,
                    (self.ua.lContact,),
                    ack_wait=False,
                    extra_headers=extra_headers,
                )
                if self.ua.expire_timer != None:
                    self.ua.expire_timer.cancel()
                    self.ua.expire_timer = None
                self.ua.startCreditTimer(event.rtime)
                self.ua.connect_ts = event.rtime
                return (UaStateConnected, self.ua.conn_cbs, event.rtime, event.origin)
            else:
                await self.ua.sendUasResponse(
                    code,
                    reason,
                    body,
                    (self.ua.lContact,),
                    ack_wait=True,
                    extra_headers=extra_headers,
                )
                return (UaStateConnected,)
        elif isinstance(event, CCEventRedirect):
            scode = event.getData()
            contacts = None
            if scode == None:
                scode = (500, "Failed", None, None)
            elif scode[3] != None:
                contacts = tuple(SipContact(address=x) for x in scode[3])
            await self.ua.sendUasResponse(scode[0], scode[1], scode[2], contacts)
            if self.ua.expire_timer != None:
                self.ua.expire_timer.cancel()
                self.ua.expire_timer = None
            if self.ua.no_progress_timer != None:
                self.ua.no_progress_timer.cancel()
                self.ua.no_progress_timer = None
            self.ua.disconnect_ts = event.rtime
            return (
                UaStateFailed,
                self.ua.fail_cbs,
                event.rtime,
                event.origin,
                scode[0],
            )
        elif isinstance(event, CCEventFail):
            scode = event.getData()
            if scode == None:
                scode = (500, "Failed")
            extra_headers = []
            if event.extra_headers != None:
                extra_headers.extend(event.extra_headers)
            if event.challenges != None:
                extra_headers.extend(event.challenges)
            if len(extra_headers) == 0:
                extra_headers = None
            else:
                extra_headers = tuple(extra_headers)
            await self.ua.sendUasResponse(
                scode[0],
                scode[1],
                reason_rfc3326=event.reason,
                extra_headers=extra_headers,
            )
            if self.ua.expire_timer != None:
                self.ua.expire_timer.cancel()
                self.ua.expire_timer = None
            if self.ua.no_progress_timer != None:
                self.ua.no_progress_timer.cancel()
                self.ua.no_progress_timer = None
            self.ua.disconnect_ts = event.rtime
            return (
                UaStateFailed,
                self.ua.fail_cbs,
                event.rtime,
                event.origin,
                scode[0],
            )
        elif isinstance(event, CCEventDisconnect):
            # import sys, traceback
            # traceback.print_stack(file = sys.stdout)
            self.ua.sendUasResponse(500, "Disconnected", reason_rfc3326=event.reason)
            if self.ua.expire_timer != None:
                self.ua.expire_timer.cancel()
                self.ua.expire_timer = None
            if self.ua.no_progress_timer != None:
                self.ua.no_progress_timer.cancel()
                self.ua.no_progress_timer = None
            self.ua.disconnect_ts = event.rtime
            return (
                UaStateDisconnected,
                self.ua.disc_cbs,
                event.rtime,
                event.origin,
                self.ua.last_scode,
            )
        # print 'wrong event %s in the Trying state' % event
        return None

    async def cancel(self, rtime, req):
        self.ua.disconnect_ts = rtime
        self.ua.changeState(
            (UaStateDisconnected, self.ua.disc_cbs, rtime, self.ua.origin)
        )
        event = CCEventDisconnect(rtime=rtime, origin=self.ua.origin)
        if req != None:
            try:
                event.reason = req.getHFBody("reason")
            except Exception:
                pass
        await self.ua.emitEvent(event)


if "UasStateRinging" not in globals():
    from sippy.UasStateRinging import UasStateRinging
if "UaStateFailed" not in globals():
    from sippy.UaStateFailed import UaStateFailed
if "UaStateConnected" not in globals():
    from sippy.UaStateConnected import UaStateConnected
if "UaStateDisconnected" not in globals():
    from sippy.UaStateDisconnected import UaStateDisconnected
