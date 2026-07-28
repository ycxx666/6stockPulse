import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncio
from services.cache import cache, CacheKeys, MemCache

# Test sync
cache.set('test_key', {'value': 42}, ttl=30)
assert cache.get('test_key') == {'value': 42}
cache.delete('test_key')
assert cache.get('test_key') is None
print('SyncCache: OK')

# Test async
async def factory():
    await asyncio.sleep(0.001)
    return 'computed'

async def test_async():
    v = await cache.get_or_set_async('async_key', factory, ttl=10)
    assert v == 'computed', f'got {v}'
    assert cache.get('async_key') == 'computed'
    print('AsyncCache: OK')

asyncio.run(test_async())

# Test incr
cache.set('counter', 0)
assert cache.incr('counter') == 1
assert cache.incr('counter', 5) == 6
print('Incr: OK')

# Test CacheKeys
assert 'quote:latest:' in CacheKeys.QUOTE_LATEST
print('CacheKeys: OK')

print('All cache tests passed')
