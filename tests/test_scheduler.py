from wow_auction_tracker.features.scheduler import run_snapshot_schedule


def test_run_snapshot_schedule_runs_immediately_then_sleeps_between_runs() -> None:
    runs: list[str] = []
    sleeps: list[float] = []

    completed = run_snapshot_schedule(
        lambda: runs.append("run"),
        interval_seconds=300,
        max_runs=3,
        sleep=sleeps.append,
    )

    assert completed == 3
    assert runs == ["run", "run", "run"]
    assert sleeps == [300, 300]


def test_run_snapshot_schedule_can_wait_before_first_run() -> None:
    runs: list[str] = []
    sleeps: list[float] = []

    completed = run_snapshot_schedule(
        lambda: runs.append("run"),
        interval_seconds=60,
        max_runs=1,
        run_immediately=False,
        sleep=sleeps.append,
    )

    assert completed == 1
    assert runs == ["run"]
    assert sleeps == [60]


def test_run_snapshot_schedule_does_not_overlap_slow_tasks() -> None:
    in_progress = False
    max_concurrent = 0

    def task() -> None:
        nonlocal in_progress, max_concurrent
        if in_progress:
            max_concurrent += 1
        in_progress = True
        in_progress = False

    completed = run_snapshot_schedule(task, interval_seconds=60, max_runs=3, sleep=lambda _: None)

    assert completed == 3
    assert max_concurrent == 0
