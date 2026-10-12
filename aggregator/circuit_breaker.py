"""
Generalized Circuit Breaker pattern for external service protection.

States: CLOSED (normal) → OPEN (failing) → HALF_OPEN (testing recovery)

Usage:
    sheets_cb = CircuitBreaker("google_sheets", failure_threshold=5, reset_timeout=60)
    
    if sheets_cb.allow_request():
        try:
            result = sheets_api.write(...)
            sheets_cb.record_success()
        except RateLimitError:
            sheets_cb.record_failure()
    else:
        log.warning("Google Sheets circuit OPEN — skipping write")
"""
import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, Optional

log = logging.getLogger(__name__)


class State(Enum):
    CLOSED = "closed"        # Normal operation
    OPEN = "open"            # Failing — reject requests
    HALF_OPEN = "half_open"  # Testing recovery


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for any external service.
    
    - CLOSED: requests flow normally
    - OPEN: after failure_threshold consecutive failures, reject all requests
    - HALF_OPEN: after reset_timeout seconds, allow one test request
    """
    name: str
    failure_threshold: int = 5
    reset_timeout: int = 60       # seconds before trying again
    success_threshold: int = 2    # successes needed in HALF_OPEN to close

    state: State = field(default=State.CLOSED, init=False)
    failure_count: int = field(default=0, init=False)
    success_count: int = field(default=0, init=False)
    last_failure_time: float = field(default=0.0, init=False)
    total_trips: int = field(default=0, init=False)

    def allow_request(self) -> bool:
        """Check if a request should be allowed."""
        if self.state == State.CLOSED:
            return True
        elif self.state == State.OPEN:
            if time.time() - self.last_failure_time >= self.reset_timeout:
                self.state = State.HALF_OPEN
                self.success_count = 0
                log.info(f"CircuitBreaker[{self.name}]: OPEN → HALF_OPEN (testing recovery)")
                return True
            return False
        elif self.state == State.HALF_OPEN:
            return True
        return False

    def record_success(self):
        """Record a successful request."""
        if self.state == State.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self.state = State.CLOSED
                self.failure_count = 0
                log.info(f"CircuitBreaker[{self.name}]: HALF_OPEN → CLOSED (recovered)")
        elif self.state == State.CLOSED:
            self.failure_count = 0

    def record_failure(self):
        """Record a failed request."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == State.HALF_OPEN:
            self.state = State.OPEN
            self.total_trips += 1
            log.warning(f"CircuitBreaker[{self.name}]: HALF_OPEN → OPEN (recovery failed)")
        elif self.state == State.CLOSED and self.failure_count >= self.failure_threshold:
            self.state = State.OPEN
            self.total_trips += 1
            log.warning(
                f"CircuitBreaker[{self.name}]: CLOSED → OPEN "
                f"(failed {self.failure_count}x, threshold={self.failure_threshold})"
            )

    @property
    def is_open(self) -> bool:
        return self.state == State.OPEN

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "total_trips": self.total_trips,
            "last_failure": self.last_failure_time,
        }


class CircuitBreakerRegistry:
    """Global registry of all circuit breakers."""
    _breakers: Dict[str, CircuitBreaker] = {}

    @classmethod
    def get(cls, name: str, **kwargs) -> CircuitBreaker:
        if name not in cls._breakers:
            cls._breakers[name] = CircuitBreaker(name=name, **kwargs)
        return cls._breakers[name]



