"""Read-only market and ticker queries on one consistent price snapshot."""
from collections import Counter
from datetime import date, timedelta
from math import isfinite
from ..data.calendar import CALENDARS
from ..data.validation import MARKETS
from ..settings import load_settings
from ..storage.database import Database
from ..strategies.ma import classify, rank_returns, ORDERS, LABELS, PERIODS


def rs_sessions(market, as_of):
    import exchange_calendars as xcals
    start = (date.fromisoformat(as_of) - timedelta(days=730)).isoformat()
    calendar = xcals.get_calendar(CALENDARS[market], start=start, end=as_of)
    days = [s.strftime('%Y-%m-%d') for s in calendar.sessions if s.strftime('%Y-%m-%d') <= as_of]
    if len(days) < 253 or days[-1] != as_of:
        raise ValueError(f"{as_of}: 252거래일 RS 기준일을 결정할 수 없습니다")
    return days[-253:]


def resolve_ticker(conn, value):
    symbol = value.strip().upper()
    market = None
    if ':' in symbol:
        market, symbol = symbol.split(':', 1)
        if market not in MARKETS:
            raise ValueError(f"지원하지 않는 시장: {market}")
    symbols = [symbol]
    if len(symbol) == 6 and symbol.isascii() and symbol.isalnum() and symbol[0].isdigit():
        symbols += [symbol + '.KS', symbol + '.KQ']
    placeholders = ','.join('?' for _ in symbols)
    rows = conn.execute(f"SELECT market,ticker FROM instruments WHERE UPPER(ticker) IN ({placeholders})",
                        symbols).fetchall()
    matches = [(r['market'], r['ticker']) for r in rows if market is None or r['market'] == market]
    if not matches:
        raise ValueError(f"판단 기준일: 없음 — 등록되지 않은 종목: {value}")
    if len(matches) != 1:
        raise ValueError("판단 기준일: 미정 — 종목이 여러 시장에 있습니다. " +
                         ', '.join(f'-q ma {m}:{t}' for m, t in matches))
    return matches[0]


def snapshot(conn, market, ticker, as_of):
    detail = conn.execute('SELECT * FROM price_detail WHERE market=? AND ticker=? AND date=?',
                          (market, ticker, as_of)).fetchone()
    # LIMIT bounds warmup checks; no per-symbol scan of the whole price history.
    prices = conn.execute(
        'SELECT date,adj_close FROM price WHERE ticker=? AND market=? AND date<=? ORDER BY date DESC LIMIT 40',
        (ticker, market, as_of)).fetchall()
    days = [r['date'] for r in prices]
    if not days or days[0] != as_of:
        return None, '최신 가격 없음'
    if detail is not None and detail['adj_close'] != prices[0]['adj_close']:
        return None, '지표와 원천 가격 불일치'
    previous = None
    if len(days) > 1:
        previous = conn.execute('SELECT * FROM price_detail WHERE market=? AND ticker=? AND date=?',
                                (market, ticker, days[1])).fetchone()
    result, reason = classify(detail, len(days), previous)
    if result is not None:
        result['previous_date'] = days[1] if len(days) > 1 else None
    return result, reason


def query_ticker(conn, market, ticker):
    latest = conn.execute('SELECT MAX(date) FROM price WHERE market=? AND ticker=?', (market, ticker)).fetchone()[0]
    market_latest = conn.execute('SELECT MAX(date) FROM price WHERE market=?', (market,)).fetchone()[0]
    lines = [f'MA 종목 조회: {market}:{ticker}', f'판단 기준일: {latest or "없음"}',
             f'시장 저장 가격 최신일: {market_latest or "없음"}']
    if latest is None:
        return '\n'.join([*lines, '판단 보류: 가격 데이터 없음']), False
    result, reason = snapshot(conn, market, ticker, latest)
    if latest != market_latest:
        lines.append('주의: 이 종목은 시장 최신일보다 가격이 오래되었습니다.')
    if result is None:
        return '\n'.join([*lines, f'판단 보류: {reason}',
                          f'지표 갱신: quantfoundry update-details --db DB경로 --market {market}']), False
    number = result['stage']
    lines += [f'현재 Stage: {number} ({LABELS[number]})', f'배열 근거: {ORDERS[number]}',
              ', '.join(f'EMA{n}={v:.6f}' for n, v in zip(PERIODS, result['emas']))]
    if result['boundary']:
        lines.append('동률 경계: 기존 DB 규칙에 따라 먼저 일치하는 Stage를 표시합니다.')
    if result['directions']:
        lines.append(f"이전 관측일 {result['previous_date']} 대비 EMA5/20/40: " + '/'.join(result['directions']))
    else:
        lines.append('이전 관측일 지표 없음: 기울기 판단 보류')
    lines.append('Stage는 EMA 배열 분류입니다. 매수 신호 전체 조건을 뜻하지 않습니다.')
    return '\n'.join(lines), True


def query_market(conn, market, *, sessions=None):
    tickers = [r[0] for r in conn.execute(
        'SELECT ticker FROM stock WHERE market=? AND in_current_listing=1 ORDER BY ticker', (market,))]
    latest = conn.execute('''SELECT MAX(p.date) FROM price p JOIN stock s
        ON s.market=p.market AND s.ticker=p.ticker
        WHERE p.market=? AND s.in_current_listing=1''', (market,)).fetchone()[0]
    lines = [f'MA 시장 조회: {market}', f'판단 기준일: {latest or "없음"}']
    if latest is None:
        return '\n'.join([*lines, '판단 보류: 현재 종목 목록 또는 가격 데이터 없음']), False
    try:
        days = rs_sessions(market, latest) if sessions is None else sessions
    except (ImportError, ValueError) as exc:
        return '\n'.join([*lines, f'판단 보류: 거래소 캘린더 확인 실패 — {exc}',
                          '캘린더 설치·갱신: python -m pip install -e ".[market-data]"']), False
    if len(days) != 253 or days[-1] != latest or days != sorted(set(days)):
        raise ValueError(f'판단 기준일: {latest} — RS에 정확히 253개의 정렬된 거래일이 필요합니다')
    start = days[0]
    current = dict(conn.execute('SELECT ticker,adj_close FROM price WHERE market=? AND date=?', (market, latest)))
    base = dict(conn.execute('SELECT ticker,adj_close FROM price WHERE market=? AND date=?', (market, start)))
    returns, excluded = {}, Counter()
    for ticker in tickers:
        if ticker not in current:
            excluded['최신 가격 없음'] += 1
        elif ticker not in base:
            excluded['252거래일 전 가격 없음'] += 1
        elif any(not isfinite(p) or p <= 0 for p in (current[ticker], base[ticker])):
            excluded['가격 오류'] += 1
        else:
            value = current[ticker] / base[ticker] - 1
            if isfinite(value):
                returns[ticker] = value
            else:
                excluded['수익률 오류'] += 1
    scores = rank_returns(returns)
    groups = {1: [], 6: []}
    stage_counts = Counter()
    for ticker in returns:
        result, reason = snapshot(conn, market, ticker, latest)
        if result is None:
            excluded[reason] += 1
            continue
        stage_counts[result['stage']] += 1
        if result['stage'] not in groups:
            continue
        if result['boundary']:
            excluded['EMA 동률 경계'] += 1
            continue
        groups[result['stage']].append((ticker, result))
    lines += [f'RS 기간: {start} → {latest} (252거래일)',
              f'RS 비교 대상: {len(scores)}/{len(tickers)}개 현재 상장 종목',
              '정렬: 자체 RS 내림차순 → 252거래일 수익률 내림차순 → 종목코드 오름차순',
              '자체 RS는 시장 내 수익률 백분위(0~99)이며 IBD 공식 RS Rating이 아닙니다.',
              'RS는 양 끝 날짜의 가격으로 계산합니다. 중간 누락을 보간하거나 과거 RS를 섞지 않습니다.',
              'EMA는 저장 관측값 기준이며 최소 40개가 필요합니다. 동률 경계는 Top 10에서 제외합니다.']
    for number, entries in groups.items():
        entries.sort(key=lambda entry: (-scores[entry[0]], -returns[entry[0]], entry[0]))
        lines += [f'\nStage {number} Top 10 — {LABELS[number]} / {ORDERS[number]}',
                  f'표시 {min(10, len(entries))}개 / 순위 가능 {len(entries)}개 / RS 대상 중 Stage {number}: {stage_counts[number]}개',
                  '순위 종목             RS    252일수익률   EMA5/20/40 방향 (이전 관측일)']
        for index, (ticker, result) in enumerate(entries[:10], 1):
            direction = '/'.join(result['directions']) if result['directions'] else '판단 보류'
            lines.append(f"{index:>2}   {ticker:<15} {scores[ticker]:>2} {returns[ticker]:>12.2%}   "
                         f"{direction} ({result['previous_date'] or '없음'})")
        if len(entries) < 10:
            lines.append('조건을 만족하는 종목이 10개 미만이므로 있는 종목만 표시합니다.')
    if excluded:
        lines.append('\n제외 사유: ' + ', '.join(f'{k} {v}개' for k, v in sorted(excluded.items())))
    if not stage_counts:
        lines.append('판단 보류: 같은 기준일에 RS와 Stage를 평가할 수 있는 종목이 없습니다.')
    lines.append('기울기는 참고 표시이며 순위 필터가 아닙니다. Stage 1·6만으로 매수를 확정하지 않습니다.')
    return '\n'.join(lines), bool(stage_counts)


def query_ma(target, *, config=None, database=None, sessions=None):
    db = Database(database or load_settings(config).database)
    with db.connection(readonly=True) as conn:
        conn.execute('BEGIN')
        normalized = target.strip().upper()
        if normalized in MARKETS:
            return query_market(conn, normalized, sessions=sessions)
        return query_ticker(conn, *resolve_ticker(conn, normalized))
