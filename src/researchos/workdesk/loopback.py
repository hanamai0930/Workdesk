"""Loopback ports that installed Node/browser Fetch clients can actually use."""
from http.server import ThreadingHTTPServer

# https://fetch.spec.whatwg.org/#port-blocking (checked 2026-10-18).
# Windows can assign these even for bind(port=0); Node rejects before connect.
FETCH_BAD_PORTS=frozenset({0,1,7,9,11,13,15,17,19,20,21,22,23,25,37,42,43,53,69,77,79,87,95,
    101,102,103,104,109,110,111,113,115,117,119,123,135,137,139,143,161,179,389,427,465,
    512,513,514,515,526,530,531,532,540,548,554,556,563,587,601,636,989,990,993,995,
    1719,1720,1723,2049,3659,4045,4190,5060,5061,6000,6566,6665,6666,6667,6668,6669,6679,6697,10080})

class _BlockedFetchPort(OSError):pass

class FetchSafeHTTPServer(ThreadingHTTPServer):
    def __init__(self,address,handler,**kwargs):
        for _ in range(32):
            try:
                super().__init__(address,handler,**kwargs);return
            except _BlockedFetchPort:
                # TCPServer.__init__ already closed the rejected bound socket.
                # Only retry local port allocation, never a model request.
                if address[1]!=0:raise
        raise OSError('Unable to allocate a Fetch-compatible local port; no request dispatched.')

    def server_bind(self):
        super().server_bind()
        if self.server_port in FETCH_BAD_PORTS:raise _BlockedFetchPort('Local port is blocked by Fetch; allocate another before serving.')
