"""
Small shared helper for the "performance metrics / execution time for
critical operations" logging requirement — one consistent way to time a
block of code and log it, instead of every service hand-rolling its own
time.time() bookkeeping with slightly different message formats.
"""
import time
from contextlib import contextmanager


@contextmanager
def log_duration(logger, operation: str, level: int = 20, **extra_fields):
    """
    Usage:
        with log_duration(logger, "embedding_generation", batch_size=len(texts)):
            ... do the work ...

    Logs one line on exit with the operation name, elapsed milliseconds,
    and any extra structured fields passed in (customer_id, model, etc.).
    On an exception, still logs the elapsed time (tagged failed=True) and
    re-raises — timing data for failed operations is often exactly what
    you need when diagnosing a slow/broken external call.
    """
    start = time.perf_counter()
    failed = False
    try:
        yield
    except Exception:
        failed = True
        raise
    finally:
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.log(
            level,
            f"{operation} {'failed' if failed else 'completed'} in {elapsed_ms}ms",
            extra={"operation": operation, "duration_ms": elapsed_ms, "failed": failed, **extra_fields}
        )
