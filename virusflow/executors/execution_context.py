from __future__ import annotations

from contextvars import ContextVar, Token


_worker_id: ContextVar[str | None] = ContextVar("virusflow_worker_id", default=None)


def current_worker_id() -> str:
    return _worker_id.get() or "main"


def in_task_worker() -> bool:
    return _worker_id.get() is not None


def enter_worker(worker_id: str) -> Token:
    return _worker_id.set(str(worker_id))


def leave_worker(token: Token) -> None:
    _worker_id.reset(token)
