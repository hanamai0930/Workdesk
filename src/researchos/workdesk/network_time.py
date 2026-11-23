"""Read the public HTTPS response clock without changing the Windows clock."""
from email.utils import parsedate_to_datetime
from http.client import HTTPException
import secrets
import threading
import time
from urllib.request import Request, build_opener

from .profiles import NoRedirect


HOST = 'api-docs.deepseek.com'
REFRESH_SECONDS = 15 * 60
RETRY_SECONDS = 30
MAX_ESTIMATE_AGE = 24 * 3600


def _read_network_time():
    request = Request('https://' + HOST + '/?clock=' + secrets.token_hex(8), method='HEAD',
                      headers={'Cache-Control': 'no-cache, no-store'})
    start = time.monotonic()
    with build_opener(NoRedirect()).open(request, timeout=3) as reply:
        if reply.status != 200 or int(reply.headers.get('Age', '0')) != 0:
            raise ValueError('未取得即时网络时间。')
        date = parsedate_to_datetime(reply.headers.get('Date', ''))
        if date.tzinfo is None:
            raise ValueError('网络时间缺少时区。')
    end = time.monotonic()
    # HTTP Date has second precision. Anchor it at the request midpoint.
    return date.timestamp(), (start + end) / 2


class NetworkClock:
    def __init__(self):
        self.sample = None
        self.next_attempt = 0.0
        self.lock = threading.Lock()

    def _snapshot(self):
        sample = self.sample
        if sample is not None:
            epoch, anchor = sample
            age = time.monotonic() - anchor
            if age <= MAX_ESTIMATE_AGE:
                fresh = age < REFRESH_SECONDS
                return {'now': epoch + age, 'synced': fresh, 'source': HOST,
                        'status': 'synced' if fresh else 'estimated', 'last_synced_at': epoch}
        return {'now': time.time(), 'synced': False, 'source': '本机',
                'status': 'local', 'last_synced_at': sample[0] if sample else None}

    def read(self):
        with self.lock:
            if time.monotonic() >= self.next_attempt:
                try:
                    self.sample = _read_network_time()
                except (OSError, HTTPException, ValueError, OverflowError, TypeError):
                    self.next_attempt = time.monotonic() + RETRY_SECONDS
                else:
                    self.next_attempt = self.sample[1] + REFRESH_SECONDS
        return self._snapshot()

    def now(self):
        # A price timestamp must never delay a provider call on network I/O.
        return self._snapshot()['now']


clock = NetworkClock()
pricing_now = clock.now
