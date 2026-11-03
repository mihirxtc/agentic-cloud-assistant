"""Thread-safe, account/region/principal-scoped live context cache."""
from threading import RLock
from cachetools import TTLCache

_cache = TTLCache(maxsize=10, ttl=300)
_lock = RLock()


def cached_context(connection, build):
    with _lock:
        if connection.cache_key not in _cache:
            _cache[connection.cache_key] = build()
        return _cache[connection.cache_key]


def invalidate_account(connection):
    # IAM/S3 changes can affect every regional view of this account.
    with _lock:
        for key in list(_cache):
            if key[:2] == (connection.partition, connection.account_id):
                del _cache[key]
