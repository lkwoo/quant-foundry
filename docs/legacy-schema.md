# 기존 SQLite 스키마: 첨부 이미지 기준

이미지는 참고 자료이며 실제 DB의 DDL을 확인한 결과가 아니다.
타입 아이콘·강조 표시만으로 PK, UNIQUE, NULL, DEFAULT, 인덱스를 확정하지 않는다.

| 테이블 | 이미지에서 확인한 컬럼 |
|---|---|
| stock | market, ticker, update_time |
| price | ticker, market, date, adj_close, volume, insert_time |
| rs_rating_history | ticker, market, date, rs_percentile, return_12m, insert_time |
| price_detail | ticker, market, date, adj_close, volume, stage, sma_50, sma_120, sma_150, sma_200, ema_5, ema_12, ema_20, ema_26, ema_40, macd, macd_5_20, macd_5_40, macd_20_40 |

price_detail 하단은 이미지에 보이지 않는다. 기존 temp.py는 signal, signal_5_20,
signal_5_40, signal_20_40을 사용하지만 실제 존재 여부는 PRAGMA로 확인해야 한다.

## 이관 방침

원본을 읽기 전용으로 열어 PRAGMA table_info/index_list/foreign_key_list와
sqlite_master를 확인한다. 새 DB로 복사하며 원본 테이블은 변경하지 않는다.
시장+티커→내부 종목 ID 대응표를 만들고 중복·NULL·날짜·가격·거래량을 검사한다.
기존 adj_close를 원종가로 추정하지 않는다. 조정 방식과 공급자는 확인되지 않으면
unknown_legacy로 기록하고 새로운 공급자 데이터와 무검증 혼합하지 않는다.
기존 지표와 RS는 legacy 계산 결과로 구분하고 새 정의로 재계산하여 비교한다.
stock.update_time/price.insert_time은 거래 완료일이나 상장일을 대신하지 않는다.
원본과 이관 후 시장별 행 수·날짜 범위·중복·샘플 값을 비교하고 보고서를 남긴다.
