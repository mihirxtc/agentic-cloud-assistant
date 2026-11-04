"""CONNECT-only proxy: fixed public AWS HTTPS destinations, no private-IP routing."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import re
import select
import socket
import threading
import time

slots = threading.BoundedSemaphore(64)


def destination(authority):
    match = re.fullmatch(r'([a-z0-9](?:[a-z0-9.-]{0,250}[a-z0-9])?):443', authority)
    if not match or not re.fullmatch(r'(?:[a-z0-9-]+\.)+amazonaws\.com(?:\.cn)?', match[1]):
        raise ValueError('Only AWS HTTPS endpoints are permitted')
    addresses = socket.getaddrinfo(match[1], 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Private/reserved endpoint routing is forbidden')
    return addresses


class Proxy(BaseHTTPRequestHandler):
    timeout = 15
    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        if not slots.acquire(blocking=False):
            self.send_error(429); return
        try:
            addresses = destination(self.path)
            family, kind, proto, _, address = addresses[0]
            with socket.socket(family, kind, proto) as upstream:
                upstream.settimeout(15)
                # Connect to the checked IP, not a second hostname resolution.
                upstream.connect(address)
                self.send_response(200, 'Connection established'); self.end_headers()
                streams, deadline = [self.connection, upstream], time.monotonic() + 360
                while time.monotonic() < deadline:
                    ready, _, _ = select.select(streams, [], [], 15)
                    if not ready:
                        break
                    for stream in ready:
                        content = stream.recv(65536)
                        if not content:
                            return
                        (upstream if stream is self.connection else self.connection).sendall(content)
        except Exception:
            self.close_connection = True
        finally:
            slots.release()


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 3128), Proxy).serve_forever()
