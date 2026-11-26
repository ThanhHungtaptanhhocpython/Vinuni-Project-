from stores.edge_registry import edge_registry_store
from stores.long_term_memory import long_term_memory_store
from stores.robots import robot_store
from stores.runs import run_store
from stores.schedules import schedule_store
from stores.sessions import session_store
from stores.task_memory import task_memory_store

__all__ = [
    "edge_registry_store",
    "robot_store",
    "run_store",
    "schedule_store",
    "session_store",
    "task_memory_store",
    "long_term_memory_store",
]


def reset_console_stores() -> None:
    edge_registry_store.reset()
    robot_store.reset()
    session_store.reset()
    task_memory_store.reset()
    long_term_memory_store.reset()
    schedule_store.reset()
    run_store.reset()
