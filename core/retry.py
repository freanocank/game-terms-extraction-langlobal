"""Async retry-with-backoff shared across translator, extractor, and NER scan."""
import asyncio, random, logging
from typing import Callable, Awaitable, TypeVar

T = TypeVar("T")

_log = logging.getLogger("retry")


def is_retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in (408, 409, 429) or status >= 500
    return isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__name__ in {
        "APIConnectionError", "APITimeoutError", "ConnectError", "ReadError",
        "ConnectTimeout", "ReadTimeout", "PoolTimeout", "WriteTimeout",
    }


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 5,
    initial_delay: float = 5.0,
    delay_multiplier: float = 2.0,
    delay_cap: float = 120.0,
    jitter_frac: float = 0.5,
    tag: str = "",
) -> T:
    """Call `fn()`. On exception, exponential-backoff retry up to `max_attempts`.

    Raises the last exception if all attempts are exhausted.
    """
    delay = initial_delay
    for attempt in range(1, max_attempts + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            jitter = random.uniform(0, delay * jitter_frac)
            _log.warning(f"{tag} retry#{attempt} {type(exc).__name__}, retry in {delay + jitter:.0f}s")
            await asyncio.sleep(delay + jitter)
            delay = min(delay * delay_multiplier, delay_cap)
