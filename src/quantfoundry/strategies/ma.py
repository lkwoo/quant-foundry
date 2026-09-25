"""Kojiro EMA stages and explicitly local relative-strength ranking."""
from math import isfinite
from ..indicators.daily import stage, VERSION

PERIODS = (5, 20, 40)


def stage1_quality(prices):
    """Candidate policy, not a delisting diagnosis. Newest observations first."""
    recent = prices[:20]
    if len(recent) < 20:
        return '최근 거래 관측값 부족(20개 필요)'
    volume = recent[0]['volume']
    if volume is None or not isfinite(volume) or volume < 0:
        return '최신 거래량 확인 불가'
    if volume == 0:
        return '최신 거래량 0'
    active = sum(p['volume'] is not None and isfinite(p['volume']) and p['volume'] > 0 for p in recent)
    if active < 16:
        return '최근 20개 관측일 중 거래량 양수 16일 미만'
    # Prefer unadjusted close so dividend adjustments do not disguise flat quotes.
    closes = [p['close'] if p['close'] is not None else p['adj_close'] for p in recent]
    if len(set(closes)) == 1:
        return '최근 20개 관측일 가격 동일'
    return None


ORDERS = {1: "EMA5 ≥ EMA20 ≥ EMA40", 2: "EMA20 ≥ EMA5 ≥ EMA40",
          3: "EMA20 ≥ EMA40 ≥ EMA5", 4: "EMA40 ≥ EMA20 ≥ EMA5",
          5: "EMA40 ≥ EMA5 ≥ EMA20", 6: "EMA5 ≥ EMA40 ≥ EMA20"}
LABELS = {1: "상승 배열", 2: "상승 약화 배열", 3: "하락 전환 배열",
          4: "하락 배열", 5: "하락 약화 배열", 6: "상승 전환 배열"}


def classify(detail, observations, previous=None):
    if detail is None:
        return None, "최신일 price_detail 없음"
    if detail['calculation_version'] != VERSION:
        return None, "지표 계산 버전 불일치"
    if observations < 40:
        return None, f"이력 부족({observations}/40개 관측값)"
    values = [detail[f'ema_{n}'] for n in PERIODS]
    if any(v is None or not isfinite(v) or v <= 0 for v in values):
        return None, "EMA 값 오류"
    number = stage(*values)
    if detail['stage'] != number:
        return None, "저장 stage와 EMA 배열 불일치"
    directions = None
    if previous is not None and previous['calculation_version'] == VERSION:
        old = [previous[f'ema_{n}'] for n in PERIODS]
        if all(v is not None and isfinite(v) and v > 0 for v in old):
            directions = ['상승' if a > b else '하락' if a < b else '보합' for a, b in zip(values, old)]
    return {'stage': number, 'emas': values, 'boundary': len(set(values)) != 3,
            'directions': directions}, None


def rank_returns(returns):
    """Same-market endpoint-return percentile, 0..99; equal returns share rank.

    This is not IBD RS Rating and does not reuse the strict complete-window
    rs_rating_history universe. Calculate over the full eligible market first.
    """
    ordered = sorted(returns, key=lambda ticker: (returns[ticker], ticker))
    scores, rank, previous = {}, 0, None
    for index, ticker in enumerate(ordered):
        value = returns[ticker]
        if not isfinite(value):
            raise ValueError("RS 수익률은 유한한 값이어야 합니다")
        if previous is None or value != previous:
            rank = index
        scores[ticker] = min(99, 100 * rank // (len(ordered) - 1)) if len(ordered) > 1 else 0
        previous = value
    return scores
