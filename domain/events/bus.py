"""
domain/events/bus.py — 轻量级事件总线 (asyncio.Queue 实现)
"""
from __future__ import annotations
import asyncio
import logging
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[None]]


class EventBus:
    """单进程异步事件总线"""

    def __init__(self):
        self._subscribers: dict[str, list[Handler]] = {}

    def subscribe(self, topic: str, handler: Handler):
        """订阅主题"""
        self._subscribers.setdefault(topic, []).append(handler)
        logger.debug("EventBus: subscribed to '%s' (total=%d)", topic, len(self._subscribers[topic]))

    def unsubscribe(self, topic: str, handler: Handler):
        """取消订阅"""
        handlers = self._subscribers.get(topic, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, topic: str, event):
        """发布事件到所有订阅者"""
        handlers = self._subscribers.get(topic, [])
        if not handlers:
            return
        tasks = [h(event) for h in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for h, r in zip(handlers, results):
            if isinstance(r, Exception):
                logger.warning("EventBus: handler %s on '%s' failed: %s", h.__name__, topic, r)


# 全局单例
bus = EventBus()
