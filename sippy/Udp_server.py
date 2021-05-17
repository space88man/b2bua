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

from anyio import create_memory_object_stream, create_task_group, create_udp_socket
import inspect

import socket
import logging
from typing import Union

from sippy.Core.EventDispatcher import ED2
from sippy.Time.MonoTime import MonoTime

_DEFAULT_FLAGS = socket.SO_REUSEADDR
if hasattr(socket, "SO_REUSEPORT"):
    _DEFAULT_FLAGS |= socket.SO_REUSEPORT
_DEFAULT_NWORKERS = 30

STATE = {"UDP_ID": 0}
LOG = logging.getLogger("sippy-async")


class Udp_server_opts(object):
    laddress = None
    data_callback = None
    family = None
    flags = _DEFAULT_FLAGS
    nworkers = None
    ploss_out_rate = 0.0
    pdelay_out_max = 0.0
    ploss_in_rate = 0.0
    pdelay_in_max = 0.0

    def __init__(self, laddress, data_callback, family=None, o=None):
        if o == None:
            if family == None:
                if laddress != None and laddress[0].startswith("["):
                    family = socket.AF_INET6
                    laddress = (laddress[0][1:-1], laddress[1])
                else:
                    family = socket.AF_INET
            self.family = family
            self.laddress = laddress
            self.data_callback = data_callback
        else:
            (
                self.laddress,
                self.data_callback,
                self.family,
                self.nworkers,
                self.flags,
                self.ploss_out_rate,
                self.pdelay_out_max,
                self.ploss_in_rate,
                self.pdelay_in_max,
            ) = (
                o.laddress,
                o.data_callback,
                o.family,
                o.nworkers,
                o.flags,
                o.ploss_out_rate,
                o.pdelay_out_max,
                o.ploss_in_rate,
                o.pdelay_in_max,
            )

    def getCopy(self):
        return self.__class__(None, None, o=self)

    def getSIPaddr(self):
        if self.family == socket.AF_INET:
            return self.laddress
        return ("[%s]" % self.laddress[0], self.laddress[1])

    def isWildCard(self):
        if (self.family, self.laddress[0]) in (
            (socket.AF_INET, "0.0.0.0"),
            (socket.AF_INET6, "::"),
        ):
            return True
        return False


class Udp_server:
    uopts = None
    stats = None
    qsend, qrecv = (None, None)
    id = None

    def __init__(self, global_config, uopts):
        self.uopts = uopts
        self.qsend, self.qrecv = create_memory_object_stream(256)
        self.id = STATE["UDP_ID"]
        self.stats = [0, 0, 0]
        STATE["UDP_ID"] += 1
        ED2.regServer(self)

    async def wrap_callback(self, packet, host, port):
        try:
            if inspect.iscoroutinefunction(self.uopts.data_callback):
                await self.uopts.data_callback(packet, (host, port), self, MonoTime())
            else:
                self.uopts.data_callback(packet, (host, port), self, MonoTime())
        except Exception:
            LOG.exception(
                "Udp_server: unhandled exception when processing incoming data"
            )

    async def run(self):
        LOG.info(
            "UDP server starting: %d, %s", self.id, laddress := self.uopts.laddress
        )

        async with create_task_group() as self._tg:
            async with await create_udp_socket(
                local_host=laddress[0],
                local_port=laddress[1],
            ) as self.sock:
                self._tg.start_soon(self.handle_outgoing)
                async for packet, (host, port) in self.sock:
                    self.stats[2] += 1
                    # the callback expects [] around IPv6 literal addresses
                    if self.uopts.family == socket.AF_INET6:
                        host = "[" + host + "]"
                    self._tg.start_soon(self.wrap_callback, packet, host, port)

    def send_to(self, data, address):
        """Strip [] from IPv6 literal addresses"""

        if self.uopts.family == socket.AF_INET6:
            address = (address[0][1:-1], address[1])
        self.qsend.send_nowait((data, address))

    async def asend_to(self, packet: Union[str, bytes], address: tuple[str, int]):
        """Strip [] from IPv6 literal addresses"""

        if self.uopts.family == socket.AF_INET6:
            address = (address[0][1:-1], address[1])
        if isinstance(packet, str):
            packet = packet.encode()
        await self.sock.sendto(packet, *address)

    async def handle_outgoing(self):
        async with self.qrecv:
            async for item in self.qrecv:
                packet, (host, port) = item
                if isinstance(packet, str):
                    packet = packet.encode()
                await self.sock.sendto(packet, host, port)

    def shutdown(self):
        self._tg.cancel_scope.cancel()


class self_test(object):
    from sys import exit

    npongs = 2
    ping_data = b"ping!"
    ping_data6 = b"ping6!"
    pong_laddr = None
    pong_laddr6 = None
    pong_data = b"pong!"
    pong_data6 = b"pong6!"
    ping_laddr = None
    ping_laddr6 = None
    ping_raddr = None
    ping_raddr6 = None
    pong_raddr = None
    pong_raddr6 = None

    def ping_received(self, data, address, udp_server, rtime):
        if udp_server.uopts.family == socket.AF_INET:
            print("ping_received")
            if data != self.ping_data or address != self.pong_raddr:
                print(data, address, self.ping_data, self.pong_raddr)
                exit(1)
            udp_server.send_to(self.pong_data, address)
        else:
            print("ping_received6")
            if data != self.ping_data6 or address != self.pong_raddr6:
                print(data, address, self.ping_data6, self.pong_raddr6)
                exit(1)
            udp_server.send_to(self.pong_data6, address)

    def pong_received(self, data, address, udp_server, rtime):
        if udp_server.uopts.family == socket.AF_INET:
            print("pong_received")
            if data != self.pong_data or address != self.ping_raddr:
                print(data, address, self.pong_data, self.ping_raddr)
                exit(1)
        else:
            print("pong_received6")
            if data != self.pong_data6 or address != self.ping_raddr6:
                print(data, address, self.pong_data6, self.ping_raddr6)
                exit(1)
        self.npongs -= 1
        if self.npongs == 0:
            ED2.breakLoop()

    def run(self):
        local_host = "127.0.0.1"
        local_host6 = "[::1]"
        remote_host = local_host
        remote_host6 = local_host6
        self.ping_laddr = (local_host, 12345)
        self.pong_laddr = (local_host, 54321)
        self.ping_laddr6 = (local_host6, 12345)
        self.pong_laddr6 = (local_host6, 54321)
        self.ping_raddr = (remote_host, 12345)
        self.pong_raddr = (remote_host, 54321)
        self.ping_raddr6 = (remote_host6, 12345)
        self.pong_raddr6 = (remote_host6, 54321)
        uopts_ping = Udp_server_opts(self.ping_laddr, self.ping_received)
        uopts_ping6 = Udp_server_opts(self.ping_laddr6, self.ping_received)
        uopts_pong = Udp_server_opts(self.pong_laddr, self.pong_received)
        uopts_pong6 = Udp_server_opts(self.pong_laddr6, self.pong_received)
        udp_server_ping = Udp_server({}, uopts_ping)
        udp_server_pong = Udp_server({}, uopts_pong)
        udp_server_pong.send_to(self.ping_data, self.ping_laddr)
        udp_server_ping6 = Udp_server({}, uopts_ping6)
        udp_server_pong6 = Udp_server({}, uopts_pong6)
        udp_server_pong6.send_to(self.ping_data6, self.ping_laddr6)
        ED2.loop()
        udp_server_ping.shutdown()
        udp_server_pong.shutdown()
        udp_server_ping6.shutdown()
        udp_server_pong6.shutdown()


if __name__ == "__main__":
    self_test().run()
