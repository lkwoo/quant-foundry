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
        command.add_argument("--db", required=name not in ("update-all", "update-stock"), help="New QuantFoundry SQLite path")
        if name != "init-db":
            command.add_argument("--market", required=name not in ("update-all", "update-stock"))
        if name in ("update-prices", "update-rs", "update-all", "daily"):
            command.add_argument("--sessions", required=name != "update-all", help="JSON list of completed exchange session dates")
        if name in ("update-rs", "update-all", "daily"):
            command.add_argument("--lookback", type=int, default=None if name == "update-all" else 252)
        if name in ("update-all", "update-stock"):
            command.add_argument("--config", help="Settings TOML; defaults to project config/settings.toml")
        if name in ("update-all", "update-prices", "daily"):
            command.add_argument("--workers", type=int, help="Concurrent price downloads (default: 4)")
            command.add_argument("--timeout", type=float, help="Price request timeout in seconds (default: 10)")
            command.add_argument("--attempts", type=int, help="Maximum attempts for transient failures (default: 2)")
        if name == "update-rs":
            command.add_argument("--missing-policy", choices=["error", "exclude"], default="error")
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return
    if args.command == "strategies":
        for name, strategy in STRATEGIES.items():
            print(f"{name} v{strategy.version}")
        return
    from .stock import Database, update_price_detail, update_rs_rating_history, update_market_prices
    try:
        sessions = json.loads(Path(args.sessions).read_text(encoding="utf-8")) if getattr(args, "sessions", None) else None
        if args.command == "update-stock":
            from .jobs.update_all import run_stock_update
            result = run_stock_update(config=args.config, database=args.db, market=args.market)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if args.command == "update-all":
            from .jobs.update_all import run_configured_update
            result = run_configured_update(config=args.config, database=args.db,
                                           market=args.market, sessions=sessions, lookback=args.lookback,
                                           workers=args.workers, timeout=args.timeout, attempts=args.attempts)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result["status"] != "SUCCESS":
                raise SystemExit(1)
            return
        download_kwargs = {}
        if args.command in ("update-prices", "daily"):
            from .data.downloads import DownloadOptions, PriceDownloader
            overrides = {name: getattr(args, name) for name in ("workers", "timeout", "attempts")
                         if getattr(args, name) is not None}
            if overrides:
                download_kwargs["downloader"] = PriceDownloader(DownloadOptions(**overrides))
        db = Database(args.db)
        if args.command == "init-db":
            db.initialize()
            result = {"initialized": str(db.path)}
        elif args.command == "update-prices":
            result = asdict(update_market_prices(db, args.market, sessions, **download_kwargs))
        elif args.command == "update-details":
            result = {"detail_rows": update_price_detail(db, args.market)}
        elif args.command == "update-rs":
            result = update_rs_rating_history(db, args.market, sessions, lookback=args.lookback, missing_policy=args.missing_policy)
        elif args.command == "daily":
            from .jobs.daily import run_daily
            result = run_daily(db, args.market, sessions, lookback=args.lookback, **download_kwargs)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        status = result.get("status", result.get("prices", {}).get("status", "SUCCESS"))
        if status in ("PARTIAL", "FAILED", "PARTIAL_UNIVERSE", "INSUFFICIENT_DATA"):
            raise SystemExit(1)
    except (ValueError, OSError, ImportError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
