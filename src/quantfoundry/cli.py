"""Explicit database commands; never opens the legacy database by default."""
import argparse
from dataclasses import asdict
import json
import sqlite3
from pathlib import Path
from .strategies.registry import STRATEGIES


def main():
    parser = argparse.ArgumentParser(description="QuantFoundry")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("strategies")
    for name in ("init-db", "update-stock", "update-prices", "update-details", "update-rs", "update-all", "daily"):
        command = sub.add_parser(name)
        command.add_argument("--db", required=True, help="New QuantFoundry SQLite path")
        if name != "init-db":
            command.add_argument("--market", required=True)
        if name in ("update-prices", "update-rs", "update-all", "daily"):
            command.add_argument("--sessions", required=True, help="JSON list of completed exchange session dates")
        if name in ("update-rs", "update-all", "daily"):
            command.add_argument("--lookback", type=int, default=252)
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if args.command == "strategies":
        for name, strategy in STRATEGIES.items():
            print(f"{name} v{strategy.version}")
        return
    from .stock import Database, update_stock, update_price_detail, update_rs_rating_history, update_market_prices, update_all
    from .providers.market import YahooProvider
    db = Database(args.db)
    try:
        sessions = json.loads(Path(args.sessions).read_text(encoding="utf-8")) if hasattr(args, "sessions") else None
        if args.command == "init-db":
            db.initialize()
            result = {"initialized": str(db.path)}
        elif args.command == "update-stock":
            result = {"stocks": update_stock(db, args.market, YahooProvider().list_tickers(args.market))}
        elif args.command == "update-prices":
            result = asdict(update_market_prices(db, args.market, sessions))
        elif args.command == "update-details":
            result = {"detail_rows": update_price_detail(db, args.market)}
        elif args.command == "update-rs":
            result = update_rs_rating_history(db, args.market, sessions, lookback=args.lookback)
        elif args.command == "daily":
            from .jobs.daily import run_daily
            result = run_daily(db, args.market, sessions, lookback=args.lookback)
        else:
            result = update_all(db, args.market, sessions, lookback=args.lookback)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        status = result.get("status", result.get("prices", {}).get("status", "SUCCESS"))
        if status in ("PARTIAL", "FAILED"):
            raise SystemExit(1)
    except (ValueError, OSError, ImportError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
