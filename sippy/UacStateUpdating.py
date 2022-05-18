# Copyright (c) 2003-2005 Maxim Sobolev. All rights reserved.
# Copyright (c) 2006-2016 Sippy Software, Inc. All rights reserved.
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
    CCEventDisconnect,
    CCEventRing,
    CCEventConnect,
    CCEventFail,
    CCEventRedirect,
)


class UacStateUpdating(UaStateGeneric):
    sname = "Updating(UAC)"
    triedauth = False
    connected = True

    async def recvRequest(self, req):
        if req.getMethod() == "INVITE":
            await self.ua.global_config["_sip_tm"].sendResponse(
                req.genResponse(491, "Request Pending", server=self.ua.local_ua)
            )
            return None
        elif req.getMethod() == "BYE":
            await self.ua.global_config["_sip_tm"].cancelTransaction(self.ua.tr)
            await self.ua.global_config["_sip_tm"].sendResponse(
                req.genResponse(200, "OK", server=self.ua.local_ua)
            )
            # print 'BYE received in the Updating state, going to the Disconnected state'
            event = CCEventDisconnect(rtime=req.rtime, origin=self.ua.origin)
            try:
                event.reason = req.getHFBody("reason")
            except Exception:
                pass
            self.ua.equeue.append(event)
            self.ua.cancelCreditTimer()
            self.ua.disconnect_ts = req.rtime
            return (UaStateDisconnected, self.ua.disc_cbs, req.rtime, self.ua.origin)
        # print 'wrong request %s in the state Updating' % req.getMethod()
        return None

    def cb_func(self, event, x):
        return self.ua.delayed_remote_sdp_update(event, x)

    async def recvResponse(self, resp, tr):
        body = resp.getBody()
        code, reason = resp.getSCode()
        scode = (code, reason, body)
        if code < 200:
            self.ua.equeue.append(
                CCEventRing(scode, rtime=resp.rtime, origin=self.ua.origin)
            )
            return None
        if code >= 200 and code < 300:
            event = CCEventConnect(scode, rtime=resp.rtime, origin=self.ua.origin)
            if body != None:
                if self.ua.on_remote_sdp_change != None:
                    try:
                        self.ua.on_remote_sdp_change(body, self.cb_func, en_excpt=True)
                    except Exception as e:
                        event = CCEventFail((502, "Bad Gateway"), rtime=event.rtime)
                        event.setWarning(
                            "Malformed SDP Body received from "
                            'downstream: "%s"' % str(e)
                        )
                        return self.updateFailed(event)
                    return (UaStateConnected,)
                else:
                    self.ua.rSDP = body.getCopy()
            else:
                self.ua.rSDP = None
            self.ua.equeue.append(event)
            return (UaStateConnected,)
        if code in (301, 302) and resp.countHFs("contact") > 0:
            scode = (
                code,
                reason,
                body,
                (resp.getHFBody("contact").getUri().getCopy(),),
            )
            event = CCEventRedirect(scode, rtime=resp.rtime, origin=self.ua.origin)
        elif code == 300 and resp.countHFs("contact") > 0:
            redirects = tuple(x.getUri().getCopy() for x in resp.getHFBodys("contact"))
            scode = (code, reason, body, redirects)
            event = CCEventRedirect(scode, rtime=resp.rtime, origin=self.ua.origin)
        else:
            event = CCEventFail(scode, rtime=resp.rtime, origin=self.ua.origin)
            try:
                event.reason = resp.getHFBody("reason")
            except Exception:
                pass

        if code in (408, 481):
            # If the response for a request within a dialog is a 481
            # (Call/Transaction Does Not Exist) or a 408 (Request Timeout), the
            # UAC SHOULD terminate the dialog.  A UAC SHOULD also terminate a
            # dialog if no response at all is received for the request (the
            # client transaction would inform the TU about the timeout.)
            return self.updateFailed(event)

        self.ua.equeue.append(event)
        return (UaStateConnected,)

    async def updateFailed(self, event):
        self.ua.equeue.append(event)
        req = self.ua.genRequest("BYE", reason=event.reason)
        self.ua.lCSeq += 1
        await self.ua.global_config["_sip_tm"].newTransaction(
            req, laddress=self.ua.source_address, compact=self.ua.compact_sip
        )
        self.ua.cancelCreditTimer()
        self.ua.disconnect_ts = event.rtime
        self.ua.equeue.append(
            CCEventDisconnect(rtime=event.rtime, origin=self.ua.origin)
        )
        return (UaStateDisconnected, self.ua.disc_cbs, event.rtime, event.origin)

    async def recvEvent(self, event):
        if (
            isinstance(event, CCEventDisconnect)
            or isinstance(event, CCEventFail)
            or isinstance(event, CCEventRedirect)
        ):
            await self.ua.global_config["_sip_tm"].cancelTransaction(self.ua.tr)
            req = self.ua.genRequest("BYE", reason=event.reason)
            self.ua.lCSeq += 1
            await self.ua.global_config["_sip_tm"].newTransaction(
                req, laddress=self.ua.source_address, compact=self.ua.compact_sip
            )
            self.ua.cancelCreditTimer()
            self.ua.disconnect_ts = event.rtime
            return (UaStateDisconnected, self.ua.disc_cbs, event.rtime, event.origin)
        # print 'wrong event %s in the Updating state' % event
        return None


if "UaStateConnected" not in globals():
    from sippy.UaStateConnected import UaStateConnected
if "UaStateDisconnected" not in globals():
    from sippy.UaStateDisconnected import UaStateDisconnected
