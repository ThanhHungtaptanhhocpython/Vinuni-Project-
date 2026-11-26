import copy
import json
import logging
from pathlib import Path

from models.schemas import RunDetail, TraceEvent

from .trace_repo import InMemoryTraceRepository

logger = logging.getLogger("agent.file_trace_repo")

class FileTraceRepository(InMemoryTraceRepository):
    """
    Kế thừa từ InMemoryTraceRepository để giữ nguyên logic queue SSE realtime,
    nhưng lưu/đọc dữ liệu từ file JSON để persist trạng thái qua các lần restart.
    """
    def __init__(self, data_dir: str = "./data/traces", max_items: int = 1000):
        super().__init__(max_items)
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._load_runs_from_disk()

    def _load_runs_from_disk(self):
        """Khôi phục trạng thái self._runs từ thư mục data_dir khi khởi động"""
        runs = []
        for run_file in self.data_dir.glob("*/run.json"):
            try:
                content = run_file.read_text(encoding="utf-8")
                run_data = json.loads(content)
                run = RunDetail(**run_data)
                runs.append(run)
            except Exception as e:
                logger.error("Failed to load run %s: %s", run_file, e)

        # Sort runs by created_at ascending so oldest is first
        runs.sort(key=lambda r: r.created_at)

        with self._lock:
            for run in runs:
                self._runs[run.id] = run
                self._run_keys_fifo.append(run.id)
                self._sequences[run.id] = 0
                self._events[run.id] = [] # Events loaded lazily in get_timeline
            self._evict_if_needed()

    def _get_run_dir(self, run_id: str) -> Path:
        p = self.data_dir / run_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def create_run(self, run: RunDetail) -> None:
        super().create_run(run)
        with self._lock:
            try:
                run_path = self._get_run_dir(run.id) / "run.json"
                run_path.write_text(self._runs[run.id].model_dump_json(), encoding="utf-8")
            except Exception as e:
                logger.error("Failed to write run %s: %s", run.id, e)

    def update_run(self, run_id: str, updates: dict) -> None:
        super().update_run(run_id, updates)
        with self._lock:
            if run_id in self._runs:
                try:
                    run_path = self._get_run_dir(run_id) / "run.json"
                    run_path.write_text(self._runs[run_id].model_dump_json(), encoding="utf-8")
                except Exception as e:
                    logger.error("Failed to update run %s: %s", run_id, e)

    def _append_event_locked(self, events_list: list[TraceEvent], event: TraceEvent) -> None:
        super()._append_event_locked(events_list, event)
        try:
            events_file = self._get_run_dir(event.run_id) / "events.jsonl"
            with events_file.open("a", encoding="utf-8") as f:
                cloned_event = events_list[-1]
                f.write(cloned_event.model_dump_json() + "\n")
        except Exception as e:
            logger.error("Failed to write trace event for %s: %s", event.run_id, e)

    def get_timeline(self, run_id: str) -> list[TraceEvent]:
        with self._lock:
            if run_id not in self._runs:
                return []

            # Khôi phục events.jsonl từ disk nếu chưa load vào RAM
            if not self._events[run_id]:
                events_file = self._get_run_dir(run_id) / "events.jsonl"
                if events_file.exists():
                    try:
                        lines = events_file.read_text(encoding="utf-8").strip().split("\n")
                        loaded_events = []
                        max_seq = 0
                        for line in lines:
                            if not line.strip():
                                continue
                            evt = TraceEvent(**json.loads(line))
                            loaded_events.append(evt)
                            max_seq = max(max_seq, evt.sequence)
                        self._events[run_id] = loaded_events
                        self._sequences[run_id] = max(self._sequences.get(run_id, 0), max_seq)
                    except Exception as e:
                        logger.error("Failed to read timeline for %s: %s", run_id, e)

            return copy.deepcopy(self._events[run_id])
