"""
Retry with exponential backoff and decorrelated jitter.

Industry-standard retry pattern for external API calls.

Usage:
    @retry(max_attempts=3, base_delay=1.0, max_delay=30.0)
    def call_api():
        return requests.get("https://api.example.com")
    
    # Or manually:
    for attempt in RetryPolicy(max_attempts=3):
        try:
            result = call_api()
            break
        except TransientError:
            attempt.backoff()
"""
import time
import random
import logging
import functools
from dataclasses import dataclass
from typing import Tuple, Type, Optional

log = logging.getLogger(__name__)


@dataclass
class RetryPolicy:
    """Configurable retry with exponential backoff and decorrelated jitter."""
    max_attempts: int = 3
    base_delay: float = 1.0        # initial delay in seconds
    max_delay: float = 60.0        # cap on delay
    jitter: bool = True            # add randomness to prevent thundering herd
    backoff_factor: float = 2.0    # exponential multiplier
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,)

    def __iter__(self):
        return RetryIterator(self)

    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number using decorrelated jitter."""
        delay = self.base_delay * (self.backoff_factor ** attempt)
        delay = min(delay, self.max_delay)
        if self.jitter:
            # Decorrelated jitter: random between base_delay and calculated delay
            delay = random.uniform(self.base_delay, delay)
        return delay


class RetryIterator:
    """Iterator that yields attempt objects with backoff capability."""
    def __init__(self, policy: RetryPolicy):
        self.policy = policy
        self.attempt = 0

    def __iter__(self):
        return self

    def __next__(self):
        if self.attempt >= self.policy.max_attempts:
            raise StopIteration
        attempt = RetryAttempt(self.attempt, self.policy)
        self.attempt += 1
        return attempt


@dataclass
class RetryAttempt:
    """Single retry attempt with backoff method."""
    number: int
    policy: RetryPolicy

    def backoff(self):
        """Sleep for the calculated backoff duration."""
        delay = self.policy.calculate_delay(self.number)
        if self.number > 0:
            log.debug(
                f"Retry attempt {self.number + 1}/{self.policy.max_attempts}, "
                f"backing off {delay:.1f}s"
            )
        time.sleep(delay)

    @property
    def is_last(self) -> bool:
        return self.number >= self.policy.max_attempts - 1


def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    retryable: Tuple[Type[Exception], ...] = (Exception,),
):
    """
    Decorator: retry a function with exponential backoff + jitter.
    
    Usage:
        @retry(max_attempts=3, base_delay=1.0)
        def flaky_api_call():
            return requests.get(url)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            policy = RetryPolicy(
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
                retryable_exceptions=retryable,
            )
            last_exception = None
            for attempt in policy:
                try:
                    if attempt.number > 0:
                        attempt.backoff()
                    return func(*args, **kwargs)
                except retryable as e:
                    last_exception = e
                    log.warning(
                        f"Retry {attempt.number + 1}/{max_attempts} for "
                        f"{func.__name__}: {e}"
                    )
                    if attempt.is_last:
                        raise
            raise last_exception
        return wrapper
    return decorator

