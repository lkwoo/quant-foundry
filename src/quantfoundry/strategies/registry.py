from .base import Strategy
from .trend import TrendStrategy

# Explicit registration keeps strategy discovery predictable on Raspberry Pi.
STRATEGIES: dict[str, Strategy] = {"trend": TrendStrategy()}
