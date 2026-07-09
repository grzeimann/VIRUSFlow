from __future__ import annotations

from typing import Dict, Type

from .base import Task, TaskContext
from .calibs import BiasTask, DarkTask

# Simple plugin/task registry keyed by (name, version)
_TASK_REGISTRY: Dict[tuple[str, str], Type[Task]] = {
    (BiasTask.name, BiasTask.version): BiasTask,
    (DarkTask.name, DarkTask.version): DarkTask,
}


def get_task_class(name: str, version: str | None = None) -> Type[Task]:
    if version is None:
        # pick the highest version by lexical order for simplicity
        candidates = sorted([(n, v) for (n, v) in _TASK_REGISTRY.keys() if n == name], key=lambda x: x[1])
        if not candidates:
            raise KeyError(f"No task named {name}")
        _, v = candidates[-1]
        version = v
    cls = _TASK_REGISTRY.get((name, version))
    if cls is None:
        raise KeyError(f"No task named {name} with version {version}")
    return cls


def available_tasks() -> Dict[str, list[str]]:
    out: Dict[str, list[str]] = {}
    for (n, v) in _TASK_REGISTRY:
        out.setdefault(n, []).append(v)
    for n in out:
        out[n].sort()
    return out
