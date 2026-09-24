"""Provider-independent download failures used by the retry policy."""


class DataUnavailableError(ValueError):
    """No usable data this run; do not infer permanent delisting."""


class TransientDownloadError(Exception):
    """A temporary transport or server failure that may be retried."""


class RateLimitError(TransientDownloadError):
    """Pause all new downloads before retrying at lower concurrency."""
