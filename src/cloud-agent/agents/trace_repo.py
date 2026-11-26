import copy
import queue as queue_module
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

from models.schemas import PaginatedRuns, RunDetail, RunStatus, RunSummary, TraceEvent

from agents.sanitizer import sanitize_payload

_TERMINAL_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.TIMEOUT,
}
_TERMINAL_EVENT_STATUSES = {
    "run.completed": RunStatus.COMPLETED,
    "run.failed": RunStatus.FAILED,
    "run.cancelled": RunStatus.CANCELLED,
    "run.timeout": RunStatus.TIMEOUT,
}


def _sanitize_preserving_images(value):
    if not isinstance(value, dict) or not isinstance(value.get("images"), list):
        return sanitize_payload(value)
    images = value["images"]
    sanitized = sanitize_payload({k: v for k, v in value.items() if k != "images"})
    if isinstance(sanitized, dict):
        sanitized["images"] = images
    return sanitized


@dataclass
class TraceSubscription:
    run_id: str
    queue: queue_module.Queue[TraceEvent] = field(
        default_factory=lambda: queue_module.Queue(maxsize=100)
    )
    closed: bool = False


class TraceRepository:
    def create_run(self, run: RunDetail) -> None:
        raise NotImplementedError

    def update_run(self, run_id: str, updates: dict) -> None:
        raise NotImplementedError

    def add_event(self, event: TraceEvent) -> None:
        raise NotImplementedError

    def get_runs(self, limit: int = 50, offset: int = 0, **filters) -> PaginatedRuns:
        raise NotImplementedError

    def get_run(self, run_id: str) -> RunDetail | None:
        raise NotImplementedError

    def get_timeline(self, run_id: str) -> list[TraceEvent]:
        raise NotImplementedError

    def subscribe(
        self,
        run_id: str,
        *,
        after_sequence: int | None = None,
        max_queue_size: int = 100,
    ) -> tuple[list[TraceEvent], TraceSubscription]:
        raise NotImplementedError

    def unsubscribe(self, subscription: TraceSubscription) -> None:
        raise NotImplementedError


class InMemoryTraceRepository(TraceRepository):
    def __init__(self, max_items: int = 1000):
        self._max_items = max_items
        self._runs: dict[str, RunDetail] = {}
        self._events: dict[str, list[TraceEvent]] = {}
        self._run_keys_fifo: list[str] = []
        self._lock = threading.RLock()
        self._sequences: dict[str, int] = {}
        self._subscribers: dict[str, list[TraceSubscription]] = {}

    def reset_for_test(self):
        with self._lock:
            self._runs.clear()
            self._events.clear()
            self._run_keys_fifo.clear()
            self._sequences.clear()
            self._subscribers.clear()

    def _evict_if_needed(self):
        overflow = len(self._run_keys_fifo) - self._max_items
        if overflow <= 0:
            return

        for _ in range(overflow):
            run_id = self._oldest_evictable_run_id_locked()
            if run_id is None:
                return
            self._drop_run_locked(run_id)

    def _oldest_evictable_run_id_locked(self) -> str | None:
        for run_id in self._run_keys_fifo:
            run = self._runs.get(run_id)
            if run is None or run.status in _TERMINAL_STATUSES:
                return run_id
        return self._run_keys_fifo[0] if self._run_keys_fifo else None

    def _drop_run_locked(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
        self._events.pop(run_id, None)
        self._sequences.pop(run_id, None)
        self._run_keys_fifo = [item for item in self._run_keys_fifo if item != run_id]
        for subscriber in self._subscribers.pop(run_id, []):
            subscriber.closed = True

    def create_run(self, run: RunDetail) -> None:
        with self._lock:
            if run.id not in self._runs:
                self._run_keys_fifo.append(run.id)
                self._sequences[run.id] = 0
                self._events[run.id] = []

            cloned = copy.deepcopy(run)
            cloned.input_text = sanitize_payload(cloned.input_text)
            if cloned.error:
                cloned.error = sanitize_payload(cloned.error)
            self._runs[run.id] = cloned
            self._evict_if_needed()

    def _mark_terminal_locked(
        self,
        run_id: str,
        status: RunStatus | str,
        event_type: str,
        error: dict | None = None,
        duration_ms: int | None = None,
        outcome: str | None = None,
    ) -> None:
        run = self._runs.get(run_id)
        if not run:
            return

        is_terminal_snapshot = run.status in _TERMINAL_STATUSES
        has_terminal_event = any(
            event.type in _TERMINAL_EVENT_STATUSES
            for event in self._events.get(run_id, [])
        )

        if is_terminal_snapshot or has_terminal_event:
            return  # First terminal state wins

        run.status = status
        if error is not None:
            run.error = sanitize_payload(error)
        if duration_ms is not None:
            run.duration_ms = duration_ms
        if outcome is not None:
            run.outcome = sanitize_payload(outcome)
        run.completed_at = datetime.now(UTC).isoformat(timespec="milliseconds")

        md = {}
        if run.outcome:
            md["outcome"] = run.outcome
        if run.final_response:
            md["final_response"] = run.final_response
        t_evt = TraceEvent(
            id=f"{run_id}_terminal",
            run_id=run_id,
            trace_id=run.trace_id,
            sequence=0,
            timestamp=run.completed_at,
            type=event_type,
            error=run.error,
            status=str(status),
            duration_ms=run.duration_ms,
            metadata=md,
        )
        self._add_event_locked(t_evt)

    def _add_event_locked(self, event: TraceEvent) -> None:
        if event.run_id not in self._sequences:
            self._sequences[event.run_id] = 0
            self._events[event.run_id] = []

        if event.type == "token.delta":
            self._sequences[event.run_id] += 1
            cloned = copy.deepcopy(event)
            cloned.sequence = self._sequences[event.run_id]
            self._notify_subscribers_locked(cloned)
            return

        is_terminal = event.type in _TERMINAL_EVENT_STATUSES

        if is_terminal and any(
            existing.type in _TERMINAL_EVENT_STATUSES
            for existing in self._events[event.run_id]
        ):
            return  # Strictly max 1 terminal event

        events_list = self._events[event.run_id]

        if len(events_list) >= 500:
            if not is_terminal:
                if event.run_id in self._runs:
                    self._runs[event.run_id].events_dropped += 1
                return
            popped = events_list.pop()
            if popped.type != "system.warning" and event.run_id in self._runs:
                self._runs[event.run_id].events_dropped += 1
        elif len(events_list) == 499 and not is_terminal:
            warning_evt = TraceEvent(
                id=f"{event.run_id}_trunc", run_id=event.run_id, trace_id=event.trace_id, sequence=0,
                timestamp=datetime.now(UTC).isoformat(timespec="milliseconds"), type="system.warning",
                metadata={"message": "Events truncated at 500 limit"}
            )
            event = warning_evt
            if event.run_id in self._runs:
                self._runs[event.run_id].events_dropped += 1

        self._append_event_locked(events_list, event)

    def _append_event_locked(self, events_list: list[TraceEvent], event: TraceEvent) -> None:
        self._sequences[event.run_id] += 1
        cloned = copy.deepcopy(event)
        cloned.sequence = self._sequences[event.run_id]

        if cloned.arguments:
            cloned.arguments = sanitize_payload(cloned.arguments)
        if cloned.result_summary:
            cloned.result_summary = _sanitize_preserving_images(cloned.result_summary)
        if cloned.error:
            cloned.error = sanitize_payload(cloned.error)
        if cloned.metadata:
            cloned.metadata = sanitize_payload(cloned.metadata)

        events_list.append(cloned)
        self._notify_subscribers_locked(cloned)

    def _notify_subscribers_locked(self, event: TraceEvent) -> None:
        subscribers = self._subscribers.get(event.run_id, [])
        if not subscribers:
            return

        active: list[TraceSubscription] = []
        for subscriber in subscribers:
            if subscriber.closed:
                continue
            try:
                subscriber.queue.put_nowait(copy.deepcopy(event))
                active.append(subscriber)
            except queue_module.Full:
                # Never block agent execution because a client is slow. The
                # stream generator observes closed=True and cleans itself up.
                subscriber.closed = True
        if active:
            self._subscribers[event.run_id] = active
        else:
            self._subscribers.pop(event.run_id, None)

    def update_run(self, run_id: str, updates: dict) -> None:
        with self._lock:
            if run_id not in self._runs:
                import logging
                logging.getLogger("agent.events").warning("update_run called for unknown run_id=%s", run_id)
                return

            run = self._runs[run_id]
            is_terminal_snapshot = run.status in _TERMINAL_STATUSES
            if is_terminal_snapshot:
                return

            will_be_terminal = updates.get("status") in _TERMINAL_STATUSES
            if will_be_terminal:
                status = updates.get("status")
                evt_type = "run.completed" if status in [RunStatus.COMPLETED, "COMPLETED"] else f"run.{str(status).lower()}"
                self._mark_terminal_locked(
                    run_id,
                    status=status,
                    event_type=evt_type,
                    error=updates.get("error"),
                    duration_ms=updates.get("duration_ms"),
                    outcome=updates.get("outcome")
                )
                updates = {k: v for k, v in updates.items() if k not in ["status", "error", "duration_ms", "outcome"]}

            for k, v in updates.items():
                if hasattr(run, k):
                    val = copy.deepcopy(v)
                    if k in ["error", "final_response", "outcome"]:
                        val = sanitize_payload(val)
                    setattr(run, k, val)

            self._evict_if_needed()

    def add_event(self, event: TraceEvent) -> None:
        with self._lock:
            is_terminal = event.type in _TERMINAL_EVENT_STATUSES
            if is_terminal:
                self._mark_terminal_locked(
                    event.run_id,
                    # Event type is canonical. This prevents a run.cancelled
                    # event without an explicit status from becoming FAILED.
                    status=_TERMINAL_EVENT_STATUSES[event.type],
                    event_type=event.type,
                    error=event.error,
                    duration_ms=event.duration_ms,
                    outcome=event.metadata.get("outcome") if event.metadata else None
                )
            else:
                self._add_event_locked(event)

    def subscribe(
        self,
        run_id: str,
        *,
        after_sequence: int | None = None,
        max_queue_size: int = 100,
    ) -> tuple[list[TraceEvent], TraceSubscription]:
        with self._lock:
            if run_id not in self._runs:
                raise KeyError(run_id)
            cursor = after_sequence if after_sequence is not None else 0
            backlog = [
                copy.deepcopy(event)
                for event in self._events.get(run_id, [])
                if event.sequence > cursor
            ]
            subscription = TraceSubscription(
                run_id=run_id,
                queue=queue_module.Queue(maxsize=max_queue_size),
            )
            self._subscribers.setdefault(run_id, []).append(subscription)
            return backlog, subscription

    def unsubscribe(self, subscription: TraceSubscription) -> None:
        with self._lock:
            subscription.closed = True
            subscribers = self._subscribers.get(subscription.run_id)
            if not subscribers:
                return
            remaining = [item for item in subscribers if item is not subscription and not item.closed]
            if remaining:
                self._subscribers[subscription.run_id] = remaining
            else:
                self._subscribers.pop(subscription.run_id, None)

    def subscriber_count(self, run_id: str) -> int:
        with self._lock:
            return len([item for item in self._subscribers.get(run_id, []) if not item.closed])

    def get_runs(self, limit: int = 50, offset: int = 0, **filters) -> PaginatedRuns:
        limit = min(limit, 100)
        with self._lock:
            filtered = []
            for run_id in reversed(self._run_keys_fifo):
                run = self._runs.get(run_id)
                if not run:
                    continue

                match = True
                for k, v in filters.items():
                    if v is not None:
                        val = getattr(run, k, None)
                        if val != v:
                            match = False
                            break
                if match:
                    filtered.append(RunSummary(**run.model_dump()))

            total = len(filtered)
            paginated = copy.deepcopy(filtered[offset:offset+limit])
            return PaginatedRuns(items=paginated, total=total)

    def get_run(self, run_id: str) -> RunDetail | None:
        with self._lock:
            if run_id in self._runs:
                return copy.deepcopy(self._runs[run_id])
            return None

    def get_timeline(self, run_id: str) -> list[TraceEvent]:
        with self._lock:
            events = self._events.get(run_id, [])
            return copy.deepcopy(events)

class LazyTraceRepository:
    """Create the DB-backed repository only when it is first used."""

    def __init__(self) -> None:
        self._instance: TraceRepository | None = None
        self._lock = threading.Lock()

    def _repo(self) -> TraceRepository:
        if self._instance is None:
            with self._lock:
                if self._instance is None:
                    from .db_trace_repo import DbTraceRepository

                    self._instance = DbTraceRepository()
        return self._instance

    def __getattr__(self, name: str):
        return getattr(self._repo(), name)


trace_repository = LazyTraceRepository()
