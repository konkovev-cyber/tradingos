"""
core/signals/base.py
Base Signal Engine interface — defines the contract for all strategies.
"""
from abc import ABC, abstractmethod
from typing import List, Optional
from .models import Signal

class SignalEngine(ABC):
    """Abstract base class for all trading signal engines."""
    
    @abstractmethod
    def analyze(self, data) -> List[Signal]:
        """Process market data and generate a list of potential signals."""
        pass

    @abstractmethod
    def get_name(self) -> str:
        """Return the unique name of the strategy."""
        pass

    @abstractmethod
    def __str__(self) -> str:
        pass
