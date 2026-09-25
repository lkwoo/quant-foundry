"""Explicit ETF refresh and read-only HAA portfolio queries."""
from calendar import monthrange
from dataclasses import asdict
from datetime import date
from ..settings import load_settings
from ..storage.database import Database
from ..strategies.haa import TICKERS, MARKETS, month_key, evaluate, render_signal


def calendar_sessions(as_of):
    import exchange_calendars as xcals
    day = date.fromisoformat(as_of)
    end = day.replace(day=monthrange(day.year, day.month)[1]).isoformat()
    calendar = xcals.get_calendar("XNYS", start=month_key(as_of, -13) + "-01", end=end)
    return [s.strftime("%Y-%m-%d") for s in calendar.sessions]


def query_haa(*, config=None, database=None, sessions=None):
    db = Database(database or load_settings(config).database)
    prices, latest = {}, {}
    with db.connection(readonly=True) as conn:
        conn.execute("BEGIN")  # All ten series belong to one consistent SQLite snapshot.
        for ticker in TICKERS:
            latest[ticker] = conn.execute("SELECT MAX(date) FROM price WHERE market=? AND ticker=?",
                                          (MARKETS[ticker], ticker)).fetchone()[0]
        as_of = max((d for d in latest.values() if d), default=None)
        if as_of is None:
            raise ValueError("HAA 기준일: 없음 — ETF 가격이 없습니다. quantfoundry update-haa를 먼저 실행하세요.")
        for ticker in TICKERS:
            prices[ticker] = dict(conn.execute(
                "SELECT date,adj_close FROM price WHERE market=? AND ticker=? AND date>=? AND date<=?",
                (MARKETS[ticker], ticker, month_key(as_of, -13) + "-01", as_of)))
    lines = ["HAA-Balanced · 13612U · 조정종가 기준", f"데이터 최신 기준일: {as_of}",
             f"DB: {db.path}", "ETF별 최신일: " + ", ".join(f"{t}={d or '없음'}" for t, d in latest.items())]
    try:
        sessions = calendar_sessions(as_of) if sessions is None else sessions
        signal = evaluate(prices, as_of, sessions)
        if signal["month_end"]:
            lines += ["월말 확정 신호: 목표 비중으로 리밸런싱하고 다음 월말까지 유지합니다.",
                      render_signal(signal, "월말 리밸런싱 기준일")]
        else:
            lines += ["월중 잠정 신호입니다. 원전 규칙은 매월 마지막 거래일 종가로 판단합니다.",
                      render_signal(signal, "최신 잠정 신호")]
            previous_end = max(d for d in sessions if d[:7] == month_key(as_of, -1))
            try:
                official = evaluate(prices, previous_end, sessions)
                lines += [render_signal(official, "최근 월말 보유 목표 기준일"),
                          "현재 결정: 최근 월말 목표를 유지하고 다음 월말에 재평가합니다."]
            except ValueError as exc:
                lines.append(f"최근 월말 보유 목표는 계산할 수 없습니다: {exc}")
                return "\n".join(lines), False
        lines.append("보유 내역 미입력: 목표 비중만 표시하며 실제 매수·매도 수량은 계산하지 않습니다.")
        lines.append("기준일은 저장 데이터 기준입니다. 최신 시세 수집: quantfoundry update-haa")
        return "\n".join(lines), True
    except ValueError as exc:
        return "\n".join([*lines, str(exc)]), False


def update_haa(*, config=None, database=None):
    from ..data.calendar import completed_sessions
    from ..data.downloads import PriceDownloader
    from ..data.updater import update_market_prices
    from ..providers.market import YahooProvider
    settings = load_settings(config)
    db = Database(database or settings.database)
    db.initialize()
    downloader = PriceDownloader(settings.download)
    provider = YahooProvider(timeout=settings.download.timeout)
    results = {}
    for market in dict.fromkeys(MARKETS.values()):
        tickers = [t for t in TICKERS if MARKETS[t] == market]
        # At least 14 calendar months, even if the stock update starts later.
        start = min(settings.start, month_key(date.today().isoformat(), -14) + "-01")
        with db.connection(readonly=True) as conn:
            for ticker in tickers:
                earliest = conn.execute("SELECT MIN(date) FROM price WHERE market=? AND ticker=?",
                                        (market, ticker)).fetchone()[0]
                if earliest:
                    start = min(start, earliest)
        days = completed_sessions(market, start)
        result = update_market_prices(db, market, days, provider=provider,
                                      downloader=downloader, tickers=tickers)
        results[market] = asdict(result)
    return {"status": "SUCCESS" if all(r["status"] == "SUCCESS" for r in results.values()) else "PARTIAL",
            "markets": results}
