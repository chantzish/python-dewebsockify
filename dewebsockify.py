import websocket
import SocketServer
import sys
import socket
import select
import errno

print sys.argv
if(len(sys.argv) != 3):
    print("Usage: python dewebsockify.py ws://someserver/path <port>")
    exit(1)

port = sys.argv[2]
ws_target = sys.argv[1]
buffer_size = 65536

class ProxyRequestHandler(SocketServer.BaseRequestHandler):
    
    class CClose(Exception):
        pass
    
    def handle(self):
        # self.log_message("%s: %s WebSocket connection", client_addr,
                     # self.stype)
        self.econnreset = False
        try:
            # msg = "connecting to: %s" % (
                                        # self.server.ws_target)

            # self.log_message(msg)

            try:
                twsock = websocket.WebSocket()
                twsock.connect(ws_target)
                twsock.send_parts = []
            except Exception:
                # self.log_message("Failed to connect to %s",
                                 # self.server.ws_target)
                raise self.CClose(1011, "Failed to connect to downstream server")

            self.request.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)
            twsock.setsockopt(socket.SOL_TCP, socket.TCP_NODELAY, 1)

            # Start proxying
            try:
                self.do_proxy(twsock)
            finally:
                if twsock:
                    twsock.shutdown(socket.SHUT_RDWR)
                    # tsock.close()
                    # if self.verbose:
                        # self.log_message("%s: Closed target",
                                # self.server.ws_target)
        except self.CClose:
            # Close the client
            _, exc, _ = sys.exc_info()
            self.send_close(exc.args[0], exc.args[1])

    def send_close(self, code=1000, reason=''):
        """ Send a WebSocket orderly close frame. """
        # self.request.shutdown(socket.SHUT_RDWR, code, reason)
        if not self.econnreset:
            self.request.shutdown(socket.SHUT_RDWR)
        self.request.close()
    
    def send_frames(self, twsock, bufs=None):
        """ Encode and send WebSocket frames. Any frames already
        queued will be sent first. If buf is not set then only queued
        frames will be sent. Returns True if any frames could not be
        fully sent, in which case the caller should call again when
        the socket is ready. """

        if bufs:
            for buf in bufs:
                twsock.send_parts.append(buf)

        # Flush any previously queued data
        try:
            twsock.sendmsg('')
        except websocket.WebSocketWantWriteError:
            return True

        while twsock.send_parts:
            # Send pending frames
            buf = twsock.send_parts.pop(0)
            try:
                twsock.sendmsg(buf)
            except websocket.WebSocketWantWriteError:
                return True

        return False

    def recv_frames(self, twsock):
        """ Receive and decode WebSocket frames.
        Returns:
            (bufs_list, closed_string)
        """

        closed = False
        bufs = []

        while True:
            try:
                buf = twsock.recvmsg()
            except websocket.WebSocketWantReadError:
                break

            if buf is None:
                closed = {'code': twsock.close_code,
                          'reason': twsock.close_reason}
                return bufs, closed

            bufs.append(buf)

            if not twsock.pending():
                break

        return bufs, closed
    
    def do_proxy(self, target):
        """
        Proxy client WebSocket to normal target socket.
        """
        cqueue = []
        c_pend = 0
        tqueue = []
        rlist = [target, self.request]

        while True:
            wlist = []

            if tqueue: wlist.append(self.request)
            if cqueue or c_pend: wlist.append(target)
            try:
                ins, outs, excepts = select.select(rlist, wlist, [], 1)
            except (select.error, OSError):
                exc = sys.exc_info()[1]
                if hasattr(exc, 'errno'):
                    err = exc.errno
                else:
                    err = exc[0]

                if err != errno.EINTR:
                    raise
                else:
                    continue

            if excepts: raise Exception("Socket exception")

            if target in outs:
                # Send queued target data to the client
                c_pend = self.send_frames(target, cqueue)

                cqueue = []

            if target in ins:
                # Receive client data, decode it, and queue for target
                bufs, closed = self.recv_frames(target)
                tqueue.extend(bufs)

                if closed:
                    # TODO: What about blocking on client socket?
                    # if self.verbose:
                        # self.log_message("%s:%s: Client closed connection",
                                # self.server.target_host, self.server.target_port)
                    raise self.CClose(closed['code'], closed['reason'])


            if self.request in outs:
                # Send queued client data to the target
                dat = tqueue.pop(0)
                sent = self.request.send(dat)
                if sent == len(dat):
                    pass
                else:
                    # requeue the remaining data
                    tqueue.insert(0, dat[sent:])


            if self.request in ins:
                # Receive target data, encode it and queue for client
                try:
                    buf = self.request.recv(buffer_size)
                    if len(buf) == 0:
                        # if self.verbose:
                            # self.log_message("%s:%s: Target closed connection",
                                    # self.server.target_host, self.server.target_port)
                        raise self.CClose(1000, "Client closed")

                    cqueue.append(buf)
                except socket.error, e:
                    if e.errno != errno.ECONNRESET:
                        raise # Not error we are looking for
                    self.econnreset = True
                    print "ECONNRESET"

HOST, PORT = "0.0.0.0", int(port)
server = SocketServer.TCPServer((HOST, PORT), ProxyRequestHandler)
try:
    server.serve_forever()
except KeyboardInterrupt:
    print "\nKeyboardInterrupt"
    server.shutdown()
