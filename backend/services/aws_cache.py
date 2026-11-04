"""Thread-safe, account/region/principal-scoped live context cache."""
from threading import RLock
from cachetools import TTLCache

_cache = TTLCache(maxsize=10, ttl=300)
_lock = RLock()


def cached_context(connection, build):
    from services.storage import get_execution_store
    key = (*connection.cache_key, get_execution_store().cache_revision(connection.partition, connection.account_id))
    with _lock:
        if key not in _cache:
            _cache[key] = build()
        return _cache[key]


def invalidate_account(connection):
    from services.storage import get_execution_store
    get_execution_store().invalidate_cache(connection.partition, connection.account_id)
    # IAM/S3 changes can affect every regional view of this account.
    with _lock:
        for key in list(_cache):
            if key[:2] == (connection.partition, connection.account_id):
                del _cache[key]
