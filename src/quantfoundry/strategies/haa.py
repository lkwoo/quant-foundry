"""HAA-Balanced (Keller/Keuning, 2023): pure portfolio allocation rules."""
from math import isfinite

OFFENSIVE = ("SPY", "IWM", "VEA", "VWO", "VNQ", "DBC", "IEF", "TLT")
DEFENSIVE = ("BIL", "IEF")
TICKERS = (*OFFENSIVE, "TIP", "BIL")
MARKETS = {t: "NASDAQ" if t in ("IEF", "TLT") else "NYSEARCA" for t in TICKERS}
PERIODS = (1, 3, 6, 12)


def allocate(scores):
    """Ties retain universe order; BIL wins a defensive tie. Zero is bad."""
    if any(t not in scores or not isfinite(scores[t]) for t in TICKERS):
        raise ValueError("HAA requires finite momentum for all ten ETFs")
    ranked = sorted(OFFENSIVE, key=lambda t: -scores[t])
    defensive = max(DEFENSIVE, key=lambda t: scores[t])
    weights, reasons = {}, []
    if scores["TIP"] <= 0:
        weights[defensive] = 1.0
        reasons.append(f"TIP 모멘텀 {scores['TIP']:.4%} <= 0: 방어자산 {defensive} 100%")
    else:
        reasons.append(f"TIP 모멘텀 {scores['TIP']:.4%} > 0: 공격자산 상위 4개를 각각 25% 평가")
        for ticker in ranked[:4]:
            target = ticker if scores[ticker] > 0 else defensive
            weights[target] = weights.get(target, 0) + 0.25
            reasons.append(f"{ticker} {scores[ticker]:.4%}: " +
                           ("25% 편입" if target == ticker and scores[ticker] > 0
                            else f"모멘텀 <= 0이므로 {defensive}로 25% 대체"))
    reasons.append(f"방어자산 비교: BIL {scores['BIL']:.4%}, IEF {scores['IEF']:.4%} → {defensive}")
    return {"weights": weights, "reasons": reasons, "ranking": ranked}


def month_key(day, offset=0):
    year, month = map(int, day[:7].split("-"))
    index = year * 12 + month - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def evaluate(prices, as_of, sessions):
    """Month-end anchors; during a month, use its latest price as a preview.

    sessions must include the entire as_of month, including future scheduled
    sessions, so a missing last trading day cannot masquerade as a month end.
    No forward filling, fallback dates, or 21/63/126/252-day approximations.
    """
    if as_of not in sessions:
        raise ValueError(f"HAA 기준일이 미국 거래일이 아닙니다: {as_of}")
    ends = {}
    for day in sorted(sessions):
        ends[day[:7]] = day
    anchors = {n: ends.get(month_key(as_of, -n)) for n in PERIODS}
    if any(day is None for day in anchors.values()):
        raise ValueError("HAA needs an exchange calendar covering the previous 12 months")
    scores, details, missing = {}, {}, []
    for ticker in TICKERS:
        series = prices.get(ticker, {})
        required = [as_of, *anchors.values()]
        absent = [day for day in required if day not in series]
        if absent:
            missing.append(f"{ticker}: {', '.join(absent)}")
            continue
        if any(not isfinite(series[d]) or series[d] <= 0 for d in required):
            raise ValueError(f"HAA invalid adjusted close: {ticker}")
        returns = {n: series[as_of] / series[day] - 1 for n, day in anchors.items()}
        scores[ticker] = sum(returns.values()) / 4
        details[ticker] = {"returns": returns, "momentum": scores[ticker]}
    if missing:
        raise ValueError("HAA 판단 보류: 필요한 조정종가 누락\n" + "\n".join(missing) +
                         "\nquantfoundry update-haa 실행 후 다시 조회하세요.")
    return {"as_of": as_of, "month_end": as_of == ends[as_of[:7]],
            "anchors": anchors, "details": details, **allocate(scores)}


def render_signal(signal, title):
    lines = [f"\n{title}: {signal['as_of']}", "목표 비중: " +
             ", ".join(f"{t} {w:.0%}" for t, w in signal["weights"].items()),
             *signal["reasons"],
             "기간별 기준일: " + ", ".join(f"{n}M={d}" for n, d in signal["anchors"].items()),
             "ETF       1M        3M        6M       12M     13612U"]
    for ticker in (*signal["ranking"], "TIP", "BIL"):
        detail = signal["details"][ticker]
        lines.append(f"{ticker:5s} " + " ".join(f"{detail['returns'][n]:9.3%}" for n in PERIODS)
                     + f" {detail['momentum']:9.3%}")
    return "\n".join(lines)
