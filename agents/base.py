"""Base agent class for all trading agents."""
import logging
from abc import ABC, abstractmethod


class BaseAgent(ABC):
    """Base class for all agents in the trading pipeline."""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agent.{name}")
        self._running = False

    def setup(self):
        """Initialize agent resources."""
        self.logger.info(f"{self.name} agent initialized")
        self._running = True

    @abstractmethod
    def process(self, data: dict) -> dict:
        """Process incoming data and return results."""
        pass

    def teardown(self):
        """Cleanup agent resources."""
        self._running = False
        self.logger.info(f"{self.name} agent shut down")

    @property
    def is_running(self) -> bool:
        return self._running
