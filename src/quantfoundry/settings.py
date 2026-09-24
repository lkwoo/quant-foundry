"""Validated defaults for the zero-argument update-all command."""
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from .data.validation import market_name, iso_date
from .data.downloads import DownloadOptions


@dataclass(frozen=True)
class Settings:
    database: Path
    markets: tuple[str, ...]
    start: str
    lookback: int
    download: DownloadOptions = field(default_factory=DownloadOptions)


def load_settings(path=None):
    project = Path(__file__).resolve().parents[2]
    config = Path(path).expanduser().resolve() if path else project / "config/settings.toml"
    if path is not None or config.exists():
        with config.open("rb") as stream:
            data = tomllib.load(stream)
        # config/settings.toml: relative DB paths are rooted at the project.
        project = config.parent.parent
    else:
        data = {}
        project = Path.cwd()
    storage, update = data.get("storage", {}), data.get("update", {})
    db = Path(storage.get("path", "var/data/quantfoundry.sqlite3")).expanduser()
    if not db.is_absolute():
        db = project / db
    raw_markets = update.get("markets", ["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"])
    if not isinstance(raw_markets, list) or not raw_markets:
        raise ValueError("update.markets must be a nonempty list")
    markets = tuple(dict.fromkeys(market_name(m) for m in raw_markets))
    start = iso_date(update.get("start_date", "2024-01-01"))
    lookback = update.get("lookback", 252)
    if isinstance(lookback, bool) or not isinstance(lookback, int) or lookback < 1:
        raise ValueError("update.lookback must be a positive integer")
    download = DownloadOptions(**{name: update[name] for name in
                                 ("workers", "timeout", "attempts", "retry_delay", "rate_limit_delay")
                                 if name in update})
    return Settings(db.resolve(), markets, start, lookback, download)
