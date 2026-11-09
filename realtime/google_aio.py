"""Priority queue contract for short-lived AIO expansion handles.

Provider handles stay in memory and are never included in task payloads, repr,
logs or result objects. This module does not call Google or claim an AIO answer
was absent when expansion failed.
"""
from __future__ import annotations

import heapq
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .google_domain import DomainError, parse_time, text, utc_text

RESULT_STATES = frozenset({
    "expanded", "not_available", "expired", "timeout", "failed",
})


class _SecretHandle:
    __slots__ = ("__value",)

    def __init__(self, value):
        if not isinstance(value, str) or not value.strip():
            raise DomainError("invalid_argument", "AIO expansion handle is empty")
        self.__value = value

    def reveal(self):
        return self.__value

    def __repr__(self):
        return "<redacted-aio-handle>"

    __str__ = __repr__


@dataclass(frozen=True)
class AIOExpansionRequest:
    observation_id: str
    run_id: str
    priority: str = "short_lived"
    timeout_seconds: int = 60
    created_at: str = field(default_factory=lambda: utc_text(datetime.now(timezone.utc)))

    def __post_init__(self):
        for name in ("observation_id", "run_id"):
            text(getattr(self, name), name, maximum=1024)
        if self.priority != "short_lived":
            raise DomainError(
                "invalid_argument", "AIO expansion must remain short-lived priority")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 300:
            raise DomainError(
                "invalid_argument", "AIO timeout must be 1..300 seconds")
        object.__setattr__(self, "created_at", utc_text(parse_time(self.created_at)))

    def to_dict(self):
        return {
            "observation_id": self.observation_id,
            "run_id": self.run_id,
            "priority": self.priority,
            "timeout_seconds": self.timeout_seconds,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AIOExpansionResult:
    observation_id: str
    state: str
    answer_text: str | None = None
    citations: tuple[str, ...] = ()
    reason: str | None = None

    def __post_init__(self):
        text(self.observation_id, "observation_id")
        if self.state not in RESULT_STATES:
            raise DomainError("invalid_argument", "Unknown AIO expansion result state")
        if self.state == "expanded":
            if not isinstance(self.answer_text, str) or not self.answer_text.strip():
                raise DomainError(
                    "invalid_argument", "Expanded AIO requires permitted answer text")
            if self.reason is not None:
                raise DomainError("invalid_argument", "Expanded AIO cannot have a failure reason")
        elif self.answer_text is not None or self.citations:
            raise DomainError(
                "invalid_argument", "Non-expanded AIO cannot invent answer/citations")
        if self.reason is not None:
            text(self.reason, "reason", maximum=1024)
        citations = tuple(self.citations)
        if any(not isinstance(url, str) or not url for url in citations):
            raise DomainError("invalid_argument", "AIO citation URL is invalid")
        object.__setattr__(self, "citations", citations)

    @property
    def eligible_for_visibility(self):
        return self.state == "expanded"

    def to_dict(self):
        return {
            "observation_id": self.observation_id,
            "state": self.state,
            "answer_text": self.answer_text,
            "citations": list(self.citations),
            "reason": self.reason,
            "eligible_for_visibility": self.eligible_for_visibility,
        }


@dataclass(frozen=True)
class AIOTask:
    task_id: str
    request: AIOExpansionRequest
    expires_at: str
    attempts: int = 0

    def __post_init__(self):
        text(self.task_id, "task_id")
        if not isinstance(self.request, AIOExpansionRequest):
            raise DomainError("invalid_argument", "AIO task requires typed request")
        if type(self.attempts) is not int or self.attempts < 0:
            raise DomainError("invalid_argument", "AIO attempts must be nonnegative")
        object.__setattr__(self, "expires_at", utc_text(self.expires_at))

    def public(self):
        return {
            "task_id": self.task_id,
            "observation_id": self.request.observation_id,
            "run_id": self.request.run_id,
            "priority": self.request.priority,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
        }


class AIOExpansionQueue:
    """In-memory fail-closed queue; no provider handle is serialized."""

    def __init__(self, clock=None):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._tasks = {}
        self._heap = []
        self._handles = {}
        self._completed = {}

    def enqueue(self, request, provider_handle):
        if not isinstance(request, AIOExpansionRequest):
            raise DomainError("invalid_argument", "Expected typed AIO request")
        current = parse_time(self.clock())
        task_id = "aio:" + secrets.token_urlsafe(18)
        expiry = current + timedelta(seconds=request.timeout_seconds)
        task = AIOTask(task_id, request, utc_text(expiry))
        self._tasks[task_id] = task
        self._handles[task_id] = _SecretHandle(provider_handle)
        heapq.heappush(self._heap, (expiry, task_id))
        return task.public()

    def claim(self):
        current = parse_time(self.clock())
        while self._heap:
            expiry, task_id = heapq.heappop(self._heap)
            if task_id not in self._tasks:
                continue
            if expiry <= current:
                self._finish(task_id, AIOExpansionResult(
                    self._tasks[task_id].request.observation_id,
                    "expired",
                    reason="short_lived_handle_expired",
                ))
                continue
            task = self._tasks.pop(task_id)
            self._tasks[task_id] = AIOTask(
                task.task_id, task.request, task.expires_at, task.attempts + 1)
            return {
                **self._tasks[task_id].public(),
                "handle": self._handles[task_id],
            }
        return None

    def complete(self, task_id, result):
        if not isinstance(result, AIOExpansionResult):
            raise DomainError("invalid_argument", "Expected typed AIO result")
        task = self._tasks.get(task_id)
        if task is None:
            if task_id in self._completed:
                raise DomainError(
                    "precondition_failed", "AIO task is already complete", 412)
            raise DomainError("not_found", "AIO task is not available", 404)
        if result.observation_id != task.request.observation_id:
            raise DomainError(
                "incompatible_scope", "AIO result observation mismatch", 409)
        if parse_time(self.clock()) >= parse_time(task.expires_at):
            result = AIOExpansionResult(
                task.request.observation_id,
                "expired",
                reason="short_lived_handle_expired",
            )
        elif result.state == "failed" and result.reason is None:
            raise DomainError(
                "invalid_argument", "Failed AIO expansion requires a reason")
        return self._finish(task_id, result)

    def _finish(self, task_id, result):
        self._tasks.pop(task_id, None)
        self._handles.pop(task_id, None)
        self._completed[task_id] = result
        return result.to_dict()

    def result(self, task_id):
        if task_id in self._completed:
            return self._completed[task_id].to_dict()
        if task_id in self._tasks:
            return {**self._tasks[task_id].public(), "result": None}
        raise DomainError("not_found", "AIO task does not exist", 404)

    def expire_due(self):
        current = parse_time(self.clock())
        expired = []
        for task_id, task in tuple(self._tasks.items()):
            if parse_time(task.expires_at) <= current:
                expired.append(self._finish(
                    task_id,
                    AIOExpansionResult(
                        task.request.observation_id,
                        "expired",
                        reason="short_lived_handle_expired",
                    ),
                ))
        return expired
