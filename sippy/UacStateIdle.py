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

from sippy.Time.Timeout import TimeoutAbsMono
from sippy.UaStateGeneric import UaStateGeneric
from sippy.CCEvents import CCEventTry, CCEventFail, CCEventRedirect, CCEventDisconnect
from sippy.SipContact import SipContact
from sippy.SipAddress import SipAddress
from sippy.SipURL import SipURL
from sippy.SipTo import SipTo
from sippy.SipFrom import SipFrom
from sippy.SipCallId import SipCallId
from sippy.SipHeader import SipHeader
from functools import partial

class UacStateIdle(UaStateGeneric):
    sname = "Idle(UAC)"

    async def recvEvent(self, event):
        if isinstance(event, CCEventTry):
            if self.ua.setup_ts == None:
                self.ua.setup_ts = event.rtime
            self.ua.origin = 'callee'
            cId, callingID, calledID, body, auth, callingName = event.getData()
            if body != None:
                if self.ua.on_local_sdp_change != None and body.needs_update:
                    self.ua.on_local_sdp_change(
                        body, partial(self.ua.recvEvent, event)
                    )
                    return None
            else:
                self.ua.late_media = True
            if cId == None:
                self.ua.cId = SipCallId()
            else:
                self.ua.cId = cId.getCopy()
            self.ua.global_config["_sip_tm"].regConsumer(
                self.ua, str(self.ua.cId), compact=self.ua.compact_sip
            )

            if self.ua.rTarget is None:
                self.ua.rTarget = SipURL(username=calledID, host=self.ua.rAddr0[0])
            # self.ua.rTarget = SipURL(
            #    username=calledID, host=self.ua.rAddr0[0], port=self.ua.rAddr0[1]
            # )
            # self.ua.rUri = SipTo(
            #    address=SipAddress(url=self.ua.rTarget.getCopy(), hadbrace=True)
            # )
            # self.ua.rUri.getUrl().port = None
            # self.ua.lUri = SipFrom(
            #    address=SipAddress(
            # url=SipURL(username=callingID), hadbrace=True, name=callingName
            #    )
            # )

            if self.ua.lContact is None:
                self.ua.lContact = SipContact()
            self.ua.lContact.getUrl().username = callingID
            self.ua.routes = []
            self.ua.lSDP = body
            event.onUacSetupComplete(self.ua)
            req = self.ua.genRequest(
                "INVITE", body, reason=event.reason, max_forwards=event.max_forwards
            )
            if auth != None and self.ua.pass_auth:
                req.appendHeader(SipHeader(body = auth))
            self.ua.lCSeq += 1
            self.ua.tr = await self.ua.global_config["_sip_tm"].newTransaction(
                req,
                self.ua.recvResponse,
                laddress=self.ua.source_address,
                cb_ifver=2,
                compact=self.ua.compact_sip,
            )
            self.ua.auth = None
            if self.ua.expire_time != None:
                self.ua.expire_mtime = event.rtime.getOffsetCopy(self.ua.expire_time)
            if self.ua.no_progress_time != None:
                self.ua.no_progress_mtime = event.rtime.getOffsetCopy(
                    self.ua.no_progress_time
                )
                if (
                    self.ua.expire_time != None
                    and self.ua.no_progress_time >= self.ua.expire_time
                ):
                    self.ua.no_progress_time = None
            if self.ua.no_reply_time != None:
                if self.ua.no_reply_time < 32:
                    no_reply_mtime = event.rtime.getOffsetCopy(self.ua.no_reply_time)
                    if (
                        self.ua.expire_time != None
                        and self.ua.no_reply_time >= self.ua.expire_time
                    ):
                        self.ua.no_reply_time = None
                    elif (
                        self.ua.no_progress_time != None
                        and self.ua.no_reply_time >= self.ua.no_progress_time
                    ):
                        self.ua.no_reply_time = None
                else:
                    self.ua.no_reply_time = None
            if self.ua.no_reply_time != None:
                self.ua.no_reply_timer = TimeoutAbsMono(
                    self.ua.no_reply_expires, no_reply_mtime
                )
            elif self.ua.no_progress_time != None:
                self.ua.no_progress_timer = TimeoutAbsMono(
                    self.ua.no_progress_expires, self.ua.no_progress_mtime
                )
            elif self.ua.expire_time != None:
                self.ua.expire_timer = TimeoutAbsMono(
                    self.ua.expires, self.ua.expire_mtime
                )
            return (UacStateTrying,)
        if (
            isinstance(event, CCEventFail)
            or isinstance(event, CCEventRedirect)
            or isinstance(event, CCEventDisconnect)
        ):
            return (UaStateDead, self.ua.disc_cbs, event.rtime, event.origin)
        return None


if "UacStateTrying" not in globals():
    from sippy.UacStateTrying import UacStateTrying
if "UaStateDead" not in globals():
    from sippy.UaStateDead import UaStateDead
