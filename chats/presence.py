"""
Online status.

- A cache counter tracks open connections per user (all tabs/devices); it
  expires unless heartbeats refresh it, so a crashed server cannot pin it.
- The database keeps `is_online` / `last_seen`; while connected, heartbeats
  move `last_seen` forward, and a user only counts as online if it is recent.
- Going offline waits a short grace period, so reloading the page or a quick
  reconnect does not flash "offline" to the other person.
"""

import asyncio

from channels.db import database_sync_to_async
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from users.models import User

from .realtime import format_datetime, peer_ids_of, send_to_users_async

_background_tasks = set()


def _counter_key(user_id):
    return f'presence:connections:{user_id}'


@database_sync_to_async
def _store_presence(user_id, is_online):
    now = timezone.now()
    User.objects.filter(pk=user_id).update(is_online=is_online, last_seen=now)
    return now


@database_sync_to_async
def _peer_ids(user_id):
    return peer_ids_of(user_id)


async def _broadcast(user_id, is_online, last_seen):
    payload = {
        'type': 'presence',
        'user_id': user_id,
        'is_online': is_online,
        'last_seen': format_datetime(last_seen),
    }
    await send_to_users_async(await _peer_ids(user_id), payload)


async def connection_opened(user_id):
    key = _counter_key(user_id)
    await cache.aadd(key, 0, timeout=settings.PRESENCE_CACHE_TTL)
    connections = await cache.aincr(key)
    await cache.atouch(key, settings.PRESENCE_CACHE_TTL)
    last_seen = await _store_presence(user_id, True)
    if connections == 1:
        await _broadcast(user_id, True, last_seen)


async def heartbeat(user_id, write_last_seen):
    await cache.atouch(_counter_key(user_id), settings.PRESENCE_CACHE_TTL)
    if write_last_seen:
        await _store_presence(user_id, True)


async def connection_closed(user_id):
    key = _counter_key(user_id)
    try:
        connections = await cache.adecr(key)
    except ValueError:  # the counter already expired
        connections = 0
    if connections > 0:
        return
    await cache.adelete(key)
    task = asyncio.create_task(_go_offline_after_grace(user_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _go_offline_after_grace(user_id):
    await asyncio.sleep(settings.PRESENCE_OFFLINE_GRACE_SECONDS)
    if (await cache.aget(_counter_key(user_id)) or 0) > 0:
        return  # reconnected in the meantime
    last_seen = await _store_presence(user_id, False)
    await _broadcast(user_id, False, last_seen)
