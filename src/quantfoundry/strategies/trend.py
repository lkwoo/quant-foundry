"""Interface example only; not a validated investment strategy."""
from math import isfinite
from ..domain.models import Snapshot, RuleResult, Verdict


class TrendStrategy:
    id = "trend"
    version = "1"
    required_features = ("adj_close", "sma_200")

    def evaluate(self, snapshot: Snapshot) -> tuple[RuleResult, ...]:
        if not snapshot.quality_ok:
            return (RuleResult("quality", Verdict.UNKNOWN, "Data quality gate failed"),)
        price, average = (snapshot.features.get(key) for key in self.required_features)
        if any(value is None or not isfinite(value) for value in (price, average)):
            return (RuleResult("above_sma200", Verdict.UNKNOWN, "Missing or non-finite feature"),)
        verdict = Verdict.PASS if price > average else Verdict.FAIL
        return (RuleResult("above_sma200", verdict, f"adj_close={price}, sma_200={average}"),)
