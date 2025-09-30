
from abc import ABC, abstractmethod
from typing import List, Dict

class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_candidates(self) -> List[Dict]:
        """Return raw candidate tokens/pairs with metrics."""
        raise NotImplementedError

    @abstractmethod
    def filter_candidates(self, items: List[Dict]) -> List[Dict]:
        """Filter by rules (FDV, liquidity, volume trend, etc.)."""
        raise NotImplementedError

    def get_signals(self) -> List[Dict]:
        items = self.fetch_candidates()
        return self.filter_candidates(items)
