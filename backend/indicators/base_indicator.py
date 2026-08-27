"""
Base technical indicator class
"""
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class BaseIndicator(ABC):
    """Base class for all technical indicators"""
    
    def __init__(self, name: str, parameters: dict[str, Any]):
        self.name = name
        self.parameters = parameters
        self.values: list[float] = []
        self.timestamps: list[datetime] = []
    
    @abstractmethod
    def calculate(self, data: list[dict[str, Any]]) -> list[float]:
        """Calculate indicator values for the given data"""
    
    @abstractmethod
    def update(self, new_data: dict[str, Any]) -> float | None:
        """Update indicator with new data point and return latest value"""
    
    def get_latest(self) -> float | None:
        """Get the latest calculated value"""
        return self.values[-1] if self.values else None
    
    def get_values(self) -> list[float]:
        """Get all calculated values"""
        return self.values.copy()
    
    def reset(self):
        """Reset indicator to initial state"""
        self.values = []
        self.timestamps = []

class IndicatorEngine:
    """Engine for managing and calculating technical indicators"""
    
    def __init__(self):
        self.indicators: dict[str, BaseIndicator] = {}
        self.data_history: list[dict[str, Any]] = []
    
    def add_indicator(self, indicator: BaseIndicator):
        """Add an indicator to the engine"""
        self.indicators[indicator.name] = indicator
    
    def remove_indicator(self, name: str):
        """Remove an indicator from the engine"""
        if name in self.indicators:
            del self.indicators[name]
    
    def update_data(self, new_data: dict[str, Any]):
        """Update engine with new market data"""
        self.data_history.append(new_data)
        
        # Update all indicators with the new data
        for indicator in self.indicators.values():
            indicator.update(new_data)
    
    def calculate_all(self, data: list[dict[str, Any]]) -> dict[str, list[float]]:
        """Calculate all indicators for the given data"""
        results = {}
        for name, indicator in self.indicators.items():
            results[name] = indicator.calculate(data)
        return results
    
    def get_latest_values(self) -> dict[str, float | None]:
        """Get latest values for all indicators"""
        return {name: indicator.get_latest() for name, indicator in self.indicators.items()}
    
    def get_indicator(self, name: str) -> BaseIndicator | None:
        """Get a specific indicator by name"""
        return self.indicators.get(name)
    
    def list_indicators(self) -> list[str]:
        """List all indicator names"""
        return list(self.indicators.keys())
