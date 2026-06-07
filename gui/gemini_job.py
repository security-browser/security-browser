"""Shared Job model + thread-safe store for the Gemini automation engine."""

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Job:
    type: str                       # 'image' | 'video' | 'dump'
    prompt: str = ""
    input_media: List[Dict[str, Any]] = field(default_factory=list)
    account: Optional[str] = None   # pinned profile/account, or None for round-robin
    id: str = field(default_factory=lambda: "job_" + uuid.uuid4().hex)
    created: float = 0.0            # set by engine (Date.now unavailable in some envs)
    profile: str = ""               # profile that handled it
    status: str = "pending"         # pending|running|needs_verification|completed|failed
    results: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    error: str = ""

    def public(self) -> Dict[str, Any]:
        return {
            "job_id": self.id,
            "type": self.type,
            "status": self.status,
            "profile": self.profile,
            "text": self.text,
            "results": self.results,
            "error": self.error,
            "created": self.created,
        }


class JobStore:
    def __init__(self):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def add(self, job: Job):
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> List[Job]:
        with self._lock:
            return list(self._jobs.values())

    def purge_older_than(self, cutoff: float):
        with self._lock:
            stale = [k for k, j in self._jobs.items()
                     if j.created and j.created < cutoff and j.status in ("completed", "failed")]
            for k in stale:
                del self._jobs[k]
