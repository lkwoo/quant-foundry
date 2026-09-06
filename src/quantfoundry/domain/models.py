"""Immutable inputs and explainable rule results."""
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Mapping


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Snapshot:
    market: str
    ticker: str
    as_of: date
    data_version: str
    features: Mapping[str, float | None]
    quality_ok: bool


@dataclass(frozen=True)
class RuleResult:
    rule: str
    verdict: Verdict
    reason: str
