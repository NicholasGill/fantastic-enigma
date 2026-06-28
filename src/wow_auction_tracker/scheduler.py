from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar


T = TypeVar("T")


def run_snapshot_schedule(
    task: Callable[[], T],
    *,
    interval_seconds: int,
    max_runs: int | None = None,
    run_immediately: bool = True,
    sleep: Callable[[float], None] = time.sleep,
    on_success: Callable[[T], None] | None = None,
) -> int:
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than 0")
    if max_runs is not None and max_runs < 0:
        raise ValueError("max_runs must be greater than or equal to 0")

    completed_runs = 0
    while max_runs is None or completed_runs < max_runs:
        if completed_runs > 0 or not run_immediately:
            sleep(interval_seconds)

        result = task()
        completed_runs += 1
        if on_success is not None:
            on_success(result)

    return completed_runs
