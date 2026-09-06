from typing import Protocol
from ..domain.models import Snapshot, RuleResult


class Strategy(Protocol):
    id: str
    version: str
    required_features: tuple[str, ...]

    def evaluate(self, snapshot: Snapshot) -> tuple[RuleResult, ...]: ...
