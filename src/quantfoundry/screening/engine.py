from ..domain.models import RuleResult, Verdict


def is_candidate(results: tuple[RuleResult, ...]) -> bool:
    """An empty evaluation or an unknown rule must never select a candidate."""
    return bool(results) and all(result.verdict is Verdict.PASS for result in results)
