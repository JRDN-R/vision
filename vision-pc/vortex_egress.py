"""CONNECT-only public HTTP(S) egress for the isolated Cobalt container.

No request/URL logging. The service network is Docker-internal; ONLY this proxy
has an outbound route. A client ignoring its proxy cannot reach the internet or
the host. DNS answers and the connected IP are both checked before tunnelling.
"""
import select
import socket
import socketserver
import threading
import time

from vortex_network import resolve_public

SLOTS = threading.BoundedSemaphore(16)


def connect_public(authority):
    host, separator, port = authority.rpartition(':')
    if not separator or port not in ('80', '443') or any(c in host for c in '/@?#\\'):
        raise ValueError('invalid_destination')
    answers = resolve_public(host.strip('[]'), int(port))
    family, socktype, proto, _, address = answers[0]
    target = socket.socket(family, socktype, proto)
    target.settimeout(15)
    try:
        target.connect(address)  # Numeric checked address: no second DNS lookup.
        return target
    except Exception:
        target.close()
        raise


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        if not SLOTS.acquire(blocking=False):
            return
        upstream = None
        try:
            self.connection.settimeout(15)
            line = self.rfile.readline(4097)
            if len(line) > 4096:
                return
            method, authority, protocol = line.decode('ascii').strip().split(' ')
            if method != 'CONNECT' or protocol not in ('HTTP/1.0', 'HTTP/1.1'):
                raise ValueError('connect_required')
            size = len(line)
            while True:
                line = self.rfile.readline(4097)
                size += len(line)
                if size > 16384 or not line:
                    raise ValueError('header_limit')
                if line in (b'\r\n', b'\n'):
                    break
            upstream = connect_public(authority)
            self.wfile.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            self.wfile.flush()
            started, transferred = time.monotonic(), 0
            while time.monotonic() - started < 2700 and transferred < 3 * 1024**3:
                readable, _, _ = select.select([self.connection, upstream], [], [], 30)
                if not readable:
                    break
                for source in readable:
                    chunk = source.recv(65536)
                    if not chunk:
                        return
                    transferred += len(chunk)
                    (upstream if source is self.connection else self.connection).sendall(chunk)
        except Exception:
            if upstream is None:
                try:
                    self.wfile.write(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                except OSError:
                    pass
        finally:
            if upstream:
                upstream.close()
            SLOTS.release()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    def handle_error(self, *_args):
        pass  # Never log signed destinations or client headers.


if __name__ == '__main__':
    with Server(('0.0.0.0', 8080), Handler) as server:
        server.serve_forever()
