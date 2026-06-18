"""
Retry logic with exponential backoff for external operations.
"""
import asyncio
import logging
from typing import Callable, TypeVar, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def retry_with_backoff(
    operation_name: str,
    operation_func: Callable[..., Any],
    max_attempts: int,
    backoff_factor: float,
    *args,
    **kwargs,
) -> Any:
    """
    Execute an operation with exponential backoff retry.

    If all retries fail, logs the error but returns None (doesn't raise).
    This allows the pipeline to continue even if external services fail.
    """
    last_error = None

    for attempt in range(max_attempts):
        try:
            logger.debug(
                f"[{operation_name}] Attempt {attempt + 1}/{max_attempts}"
            )
            result = await operation_func(*args, **kwargs)
            if attempt > 0:
                logger.info(
                    f"[{operation_name}] Succeeded after {attempt} "
                    f"{'retry' if attempt == 1 else 'retries'}"
                )
            return result
        except asyncio.TimeoutError:
            last_error = "Timeout"
            logger.warning(
                f"[{operation_name}] Timeout "
                f"(attempt {attempt + 1}/{max_attempts})"
            )
        except Exception as exc:
            last_error = str(exc)
            logger.warning(
                f"[{operation_name}] Failed "
                f"(attempt {attempt + 1}/{max_attempts}): {exc}"
            )

        if attempt < max_attempts - 1:
            wait_time = backoff_factor ** attempt
            logger.info(f"[{operation_name}] Retrying in {wait_time:.1f}s...")
            await asyncio.sleep(wait_time)

    logger.error(
        f"[{operation_name}] Failed after {max_attempts} attempts: {last_error}"
    )
    return None
