"""Simple event bus for inter-agent communication."""
import logging
from collections import defaultdict
from typing import Callable, Any

logger = logging.getLogger(__name__)


class EventBus:
    """Publish-subscribe event bus for agent communication."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._event_log: list[dict] = []

    def subscribe(self, event_type: str, callback: Callable):
        self._subscribers[event_type].append(callback)
        logger.debug(f"Subscribed to {event_type}")

    def publish(self, event_type: str, data: Any = None):
        self._event_log.append({"type": event_type, "data": data})

        for callback in self._subscribers.get(event_type, []):
            try:
                callback(data)
            except Exception as e:
                logger.error(f"Error in {event_type} handler: {e}")

    def get_log(self) -> list[dict]:
        return self._event_log.copy()

    def clear_log(self):
        self._event_log.clear()


# Global event bus instance
bus = EventBus()
