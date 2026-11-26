from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from agents.trace_repo import InMemoryTraceRepository
from models.schemas import RunDetail, RunSource, RunStatus, TraceEvent


def test_repo_concurrency():
    repo = InMemoryTraceRepository(max_items=100)
    repo.reset_for_test()
    run_id = "run_shared"
    run = RunDetail(
        id=run_id, trace_id="trc_shared", session_id="ses", robot_id="rob", user_id="user",
        source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
    )
    repo.create_run(run)

    def worker_add_events(worker_id: int):
        for i in range(10):
            event = TraceEvent(
                id=f"evt_{worker_id}_{i}", run_id=run_id, trace_id="trc_shared", sequence=0,
                timestamp=datetime.now(UTC).isoformat(), type="test.event"
            )
            repo.add_event(event)

    futures = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        for i in range(20):
            futures.append(executor.submit(worker_add_events, i))

    for f in futures:
        f.result()

    timeline = repo.get_timeline(run_id)
    assert len(timeline) == 200

    # Check sequence
    seqs = [e.sequence for e in timeline]
    assert sorted(seqs) == list(range(1, 201))


def test_terminal_transition_concurrency():
    import threading
    repo = InMemoryTraceRepository(max_items=100)

    for i in range(50):
        repo.reset_for_test()
        run_id = f"run_concurrent_{i}"
        run = RunDetail(
            id=run_id, trace_id="trc_shared", session_id="ses", robot_id="rob", user_id="user",
            source=RunSource.CHAT, status=RunStatus.RUNNING, input_text="hello", created_at=datetime.now(UTC).isoformat()
        )
        repo.create_run(run)

        # Populate with 499 events to test edge cases of 500 limit
        for j in range(499):
            repo.add_event(TraceEvent(
                id=f"evt_{i}_{j}", run_id=run_id, trace_id="trc_shared", sequence=0,
                timestamp=datetime.now(UTC).isoformat(), type="test.event"
            ))

        barrier = threading.Barrier(2)

        def worker_update_run():
            barrier.wait()
            repo.update_run(run_id, {"status": RunStatus.COMPLETED, "duration_ms": 100, "error": None})

        def worker_add_event():
            barrier.wait()
            repo.add_event(TraceEvent(
                id=f"evt_{i}_fail", run_id=run_id, trace_id="trc_shared", sequence=0,
                timestamp=datetime.now(UTC).isoformat(), type="run.failed", status="FAILED", duration_ms=200, error={"msg": "err"}
            ))

        with ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(worker_update_run)
            f2 = executor.submit(worker_add_event)
            f1.result()
            f2.result()

        timeline = repo.get_timeline(run_id)
        fetched = repo.get_run(run_id)

        # 1. Timeline limit <= 500
        assert len(timeline) <= 500

        # 2. Exactly one terminal event
        terminal_events = [e for e in timeline if e.type in ["run.completed", "run.failed"]]
        assert len(terminal_events) == 1
        t_evt = terminal_events[0]

        # 3. Terminal event consistent with snapshot
        assert t_evt.status == fetched.status
        assert t_evt.duration_ms == fetched.duration_ms
        if t_evt.type == "run.completed":
            assert fetched.status == RunStatus.COMPLETED
            assert fetched.duration_ms == 100
            assert fetched.error is None
        else:
            assert fetched.status == RunStatus.FAILED
            assert fetched.duration_ms == 200
            assert fetched.error["msg"] == "err"

