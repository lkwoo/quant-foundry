"""Provider-independent download failures used by the retry policy."""


class DataUnavailableError(ValueError):
    """Nonfatal no-price-data signal; recorded as NO_DATA, not as a failure."""


class SymbolLookupError(ValueError):
    """The provider could not resolve a symbol; absence of prices is unconfirmed."""


class ProviderResponseError(ValueError):
    """Malformed or ambiguous provider response, not confirmed absence of data."""


class TransientDownloadError(Exception):
    """A temporary transport or server failure that may be retried."""


class RateLimitError(TransientDownloadError):
    """Pause all new downloads before retrying at lower concurrency."""
