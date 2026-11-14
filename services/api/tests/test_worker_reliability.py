from __future__ import annotations

import threading

from sqlalchemy.exc import OperationalError, ProgrammingError

from services.worker import main as worker


class StopAfterWaits(threading.Event):
    def __init__(self, count: int) -> None:
        super().__init__()
        self.count = count
        self.delays: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        self.delays.append(timeout if timeout is not None else 0.0)
        if len(self.delays) >= self.count:
            self.set()
        return self.is_set()


def test_worker_retries_database_outage_with_jitter_and_resets_after_recovery(
    monkeypatch,
    caplog,
    tmp_path,
):
    stop_event = StopAfterWaits(3)
    calls = 0
    status_path = tmp_path / "database-blocked"

    def process_jobs() -> None:
        nonlocal calls
        calls += 1
        if calls in {1, 3}:
            raise OperationalError(
                "database connection failed",
                {},
                RuntimeError("synthetic connection secret"),
            )

    monkeypatch.setattr(worker, "process_jobs", process_jobs)
    monkeypatch.setattr(worker, "_database_retry_delay", lambda failures: failures + 0.25)
    monkeypatch.setenv("WORKER_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("WORKER_DATABASE_STATUS_FILE", str(status_path))

    worker.run_worker(stop_event)

    assert calls == 3
    assert stop_event.delays == [1.25, 7, 1.25]
    assert "OperationalError" in caplog.text
    assert "synthetic connection secret" not in caplog.text
    assert "database connection failed" not in caplog.text
    assert status_path.exists()


def test_worker_clears_database_health_marker_after_connection_recovers(
    monkeypatch,
    tmp_path,
):
    stop_event = StopAfterWaits(2)
    status_path = tmp_path / "database-blocked"
    calls = 0

    def process_jobs() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OperationalError("connection failed", {}, RuntimeError("offline"))

    def fixed_retry_delay(failures: int) -> float:
        assert failures == 1
        return 0.5

    monkeypatch.setattr(worker, "process_jobs", process_jobs)
    monkeypatch.setattr(worker, "_database_retry_delay", fixed_retry_delay)
    monkeypatch.setenv("WORKER_INTERVAL_SECONDS", "7")
    monkeypatch.setenv("WORKER_DATABASE_STATUS_FILE", str(status_path))

    worker.run_worker(stop_event)

    assert calls == 2
    assert stop_event.delays == [0.5, 7]
    assert not status_path.exists()


def test_database_retry_delay_is_bounded_and_jittered(monkeypatch):
    ranges: list[tuple[float, float]] = []

    def record_range(low: float, high: float) -> float:
        ranges.append((low, high))
        return high

    monkeypatch.setattr(worker.random, "uniform", record_range)

    assert worker._database_retry_delay(1) == 1
    assert worker._database_retry_delay(100) == worker.DATABASE_BACKOFF_MAX_SECONDS
    assert ranges == [(0.5, 1.0), (30.0, 60.0)]


def test_worker_does_not_hide_unexpected_processing_errors(monkeypatch):
    def fail_unexpectedly() -> None:
        raise ValueError("programming error")

    monkeypatch.setattr(worker, "process_jobs", fail_unexpectedly)

    try:
        worker.run_worker(StopAfterWaits(1))
    except ValueError as exc:
        assert str(exc) == "programming error"
    else:
        raise AssertionError("Unexpected worker errors must not be silently retried.")


def test_worker_does_not_retry_non_connection_database_errors(monkeypatch):
    def fail_with_invalid_query() -> None:
        raise ProgrammingError("invalid query", {}, RuntimeError("syntax error"))

    monkeypatch.setattr(worker, "process_jobs", fail_with_invalid_query)

    try:
        worker.run_worker(StopAfterWaits(1))
    except ProgrammingError:
        pass
    else:
        raise AssertionError("Non-connection database errors must not be retried.")
