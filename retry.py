"""
Retry utilities with exponential backoff for transient errors.

Handles:
- API rate limits (429)
- Transient 5xx errors
- Network timeouts
- Connection errors

Usage:
    @retry_with_backoff(max_attempts=3)
    def call_api():
        return client.api.do_thing()

    # Or directly:
    result = retry_call(lambda: client.api.do_thing(), max_attempts=3)
"""

import functools
import time
from typing import Callable, Any, Optional, Type, Tuple

# Default error types to retry on
DEFAULT_RETRY_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)


class RetryError(Exception):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, message: str, attempts: int, last_error: Exception):
        super().__init__(message)
        self.attempts = attempts
        self.last_error = last_error


def is_retryable_anthropic_error(error: Exception) -> bool:
    """Check if an Anthropic API error is worth retrying."""
    error_msg = str(error).lower()
    error_type = type(error).__name__.lower()

    # Retryable conditions
    retryable_keywords = [
        "rate_limit", "rate limit", "429",
        "internal_server", "500", "502", "503", "504",
        "timeout", "connection",
        "overloaded",
    ]
    return any(kw in error_msg or kw in error_type for kw in retryable_keywords)


def retry_with_backoff(
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRY_EXCEPTIONS,
    is_retryable: Optional[Callable[[Exception], bool]] = None,
    logger=None,
):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including first)
        initial_delay: Initial delay in seconds
        max_delay: Maximum delay between attempts
        exponential_base: Base for exponential calculation
        retryable_exceptions: Tuple of exception types to retry on
        is_retryable: Optional function to check if exception is retryable
        logger: Optional StructuredLogger to log retry attempts
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return retry_call(
                lambda: func(*args, **kwargs),
                max_attempts=max_attempts,
                initial_delay=initial_delay,
                max_delay=max_delay,
                exponential_base=exponential_base,
                retryable_exceptions=retryable_exceptions,
                is_retryable=is_retryable,
                logger=logger,
                func_name=func.__name__,
            )

        return wrapper

    return decorator


def retry_call(
    fn: Callable[[], Any],
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRY_EXCEPTIONS,
    is_retryable: Optional[Callable[[Exception], bool]] = None,
    logger=None,
    func_name: str = "anonymous",
) -> Any:
    """
    Call a function with retry logic and exponential backoff.

    Returns the result of fn() on success.
    Raises RetryError if all attempts fail.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            last_error = e
            should_retry = True
            if is_retryable is not None:
                should_retry = is_retryable(e)

            if not should_retry or attempt == max_attempts:
                if logger:
                    logger.error(
                        f"{func_name} failed after {attempt} attempts",
                        error=e,
                        attempts=attempt,
                    )
                raise RetryError(
                    f"{func_name} failed after {attempt} attempts",
                    attempts=attempt,
                    last_error=e,
                ) from e

            delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)
            if logger:
                logger.warn(
                    f"{func_name} attempt {attempt} failed, retrying in {delay}s",
                    error_type=type(e).__name__,
                    error_message=str(e),
                    next_attempt=attempt + 1,
                    delay_seconds=delay,
                )
            time.sleep(delay)

        except Exception as e:
            # Non-retryable exception
            last_error = e
            if is_retryable is not None and is_retryable(e):
                # Check custom logic for retryability
                if attempt < max_attempts:
                    delay = min(initial_delay * (exponential_base ** (attempt - 1)), max_delay)
                    if logger:
                        logger.warn(
                            f"{func_name} attempt {attempt} failed (retryable), retrying in {delay}s",
                            error_type=type(e).__name__,
                            delay_seconds=delay,
                        )
                    time.sleep(delay)
                    continue
            if logger:
                logger.error(f"{func_name} failed (non-retryable)", error=e)
            raise

    # Shouldn't reach here, but just in case
    raise RetryError(
        f"{func_name} failed after {max_attempts} attempts",
        attempts=max_attempts,
        last_error=last_error or Exception("Unknown"),
    )


def with_timeout(seconds: float):
    """
    Decorator to enforce a timeout on a function.
    Note: For platform-portable timeout, use signal-based on Unix only.
    For Vercel, rely on platform timeout + check elapsed time in long loops.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start
            if elapsed > seconds:
                # Log warning but don't fail (function already completed)
                print(f"[WARN] {func.__name__} took {elapsed:.1f}s (limit: {seconds}s)", flush=True)
            return result

        return wrapper

    return decorator
