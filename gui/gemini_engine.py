"""
GeminiEngine — pool manager + round-robin dispatcher for Gemini automation.

Owns the pool of account profiles, accepts jobs (from the HTTP API thread),
and dispatches each to an account's CamoufoxWorker on a round-robin basis,
auto-launching the profile's visible browser when needed. The browsers are the
SAME workers the explorer GUI uses (MainWindow.workers), so there is no
profile-lock conflict.

Threading: the engine runs its own dispatcher thread. It NEVER creates Qt
objects directly — it asks MainWindow to launch a worker via a thread-safe
signal (MainWindow.request_launch) and then polls MainWindow.workers until the
worker is ready. All Playwright work happens on the worker thread.
"""

import json
import os
import threading
import time
from typing import Any, Dict, List, Optional

from gemini_job import Job, JobStore

HERE = os.path.dirname(os.path.abspath(__file__))
POOL_FILE = os.path.join(HERE, "gemini_pool.json")
PROFILES_FILE = os.path.join(HERE, "profiles.json")

MEDIA_DIR = os.environ.get("GEMINI_MEDIA_DIR", "/tmp/gemini_media")
LAUNCH_TIMEOUT = 90          # seconds to wait for a browser to become ready
JOB_TTL = 2 * 3600           # completed/failed jobs purged after 2h


def load_pool() -> List[Dict[str, Any]]:
    """Return pool entries [{profile, slot, enabled}]. Falls back to every
    @-named profile in profiles.json (each = one Google account, slot 0)."""
    if os.path.exists(POOL_FILE):
        try:
            with open(POOL_FILE, "r", encoding="utf-8") as f:
                entries = json.load(f)
            return [e for e in entries if e.get("enabled", True)]
        except Exception:
            pass
    pool = []
    try:
        with open(PROFILES_FILE, "r", encoding="utf-8") as f:
            for p in json.load(f):
                if "@" in p.get("name", ""):
                    pool.append({"profile": p["name"], "slot": 0, "enabled": True})
    except Exception:
        pass
    return pool


class GeminiEngine:
    def __init__(self, main_window):
        self.mw = main_window
        self.pool = load_pool()
        self.store = JobStore()
        self._incoming: "list[Job]" = []
        self._cv = threading.Condition()
        self._rr = 0
        self._stop = False
        self._dispatcher = threading.Thread(target=self._run, name="gemini-dispatch", daemon=True)
        self._dispatcher.start()

    # ── public API (called from the HTTP thread) ──
    def submit(self, type: str, prompt: str = "", input_media=None,
               account: Optional[str] = None) -> Job:
        job = Job(type=type, prompt=prompt, input_media=input_media or [],
                  account=account, created=time.time())
        self.store.add(job)
        with self._cv:
            self._incoming.append(job)
            self._cv.notify()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self.store.get(job_id)

    def pool_status(self) -> Dict[str, Any]:
        workers = getattr(self.mw, "workers", {})
        members = []
        for e in self.pool:
            w = workers.get(e["profile"])
            members.append({
                "profile": e["profile"],
                "slot": e.get("slot", 0),
                "running": bool(w and w.isRunning()),
                "state": getattr(w, "state", "stopped") if w else "stopped",
            })
        return {"size": len(self.pool), "members": members, "media_dir": MEDIA_DIR}

    def shutdown(self):
        self._stop = True
        with self._cv:
            self._cv.notify()

    # ── dispatcher thread ──
    def _run(self):
        last_purge = 0.0
        while not self._stop:
            with self._cv:
                while not self._incoming and not self._stop:
                    self._cv.wait(timeout=5)
                if self._stop:
                    return
                job = self._incoming.pop(0)
            try:
                self._dispatch(job)
            except Exception as e:
                job.status = "failed"
                job.error = f"dispatch error: {type(e).__name__}: {e}"
            now = time.time()
            if now - last_purge > 600:
                self.store.purge_older_than(now - JOB_TTL)
                last_purge = now

    def _dispatch(self, job: Job):
        entry = self._select_entry(job)
        if not entry:
            job.status = "failed"
            job.error = "no account available in the pool"
            return
        job.profile = entry["profile"]
        worker = self._ensure_ready(entry)
        if not worker:
            # Unlaunchable account (e.g. no matching profile, or launch timed out).
            # For round-robin jobs, skip it and re-route to another profile instead
            # of failing; only a pinned account fails outright.
            if not job.account:
                self.requeue(job, entry["profile"])
                return
            job.status = "failed"
            job.error = f"could not launch profile '{entry['profile']}'"
            return
        worker.engine = self            # so the worker can re-route on wrong_account
        worker.gemini_slot = entry.get("slot", 0)
        worker.media_dir = MEDIA_DIR
        worker.submit_job(job)
        # Optimistically mark busy NOW: the worker only flips state to "busy" once
        # its thread dequeues the job, but the dispatcher may pick the next job
        # before that happens — without this, back-to-back jobs all see this worker
        # as idle and pile onto it instead of spreading to free accounts.
        worker.state = "busy"

    def _select_entry(self, job: Job) -> Optional[Dict[str, Any]]:
        if job.account:
            for e in self.pool:
                if e["profile"] == job.account:
                    return e
            # account pinned but not in pool → run it anyway, slot 0
            return {"profile": job.account, "slot": 0, "enabled": True}
        if not self.pool:
            return None
        # Route to a free account, never piling onto a busy one. Priority (each
        # scanned round-robin from _rr, skipping profiles this job already tried):
        #   1. a running idle worker  → reuse, no launch
        #   2. a stopped account      → launch a fresh idle one
        #   3. a busy/starting worker → queue behind it (only if all are busy)
        exclude = set(job.tried_profiles)
        workers = getattr(self.mw, "workers", {})
        n = len(self.pool)
        idle = stopped = busy = None
        for i in range(n):
            e = self.pool[(self._rr + i) % n]
            if e["profile"] in exclude:
                continue
            w = workers.get(e["profile"])
            running = bool(w and w.isRunning())
            state = getattr(w, "state", "stopped") if w else "stopped"
            if running and state == "idle":
                idle = (i, e)
                break
            if not running and stopped is None:
                stopped = (i, e)
            elif running and busy is None:   # busy or still starting
                busy = (i, e)
        chosen = idle or stopped or busy
        if not chosen:
            return None  # every account excluded (all tried)
        i, e = chosen
        self._rr = (self._rr + i + 1) % n
        return e

    def requeue(self, job: Job, failed_profile: str):
        """Re-dispatch a job whose profile couldn't serve it (signed out, wrong
        Google account, or unlaunchable), excluding the offending profile. Pinned
        jobs can't be honored elsewhere, and once every profile has been tried we
        give up — both end as failed (keeping the last reason)."""
        last_error = job.error or "profile unavailable"
        if job.account:
            job.status = "failed"
            job.error = f"pinned account '{job.account}' unavailable: {last_error}"
            return
        if failed_profile and failed_profile not in job.tried_profiles:
            job.tried_profiles.append(failed_profile)
        if len(set(job.tried_profiles)) >= len(self.pool):
            job.status = "failed"
            job.error = f"no usable profile in the pool (last: {last_error})"
            return
        job.status = "pending"
        job.profile = ""
        job.error = ""
        with self._cv:
            self._incoming.append(job)
            self._cv.notify()

    def _ensure_ready(self, entry: Dict[str, Any]):
        name = entry["profile"]
        workers = getattr(self.mw, "workers", {})
        w = workers.get(name)
        if w and getattr(w, "ready", False) and w.isRunning():
            return w
        # Fast-fail if there is no such profile to launch — otherwise we would
        # block the single dispatcher thread for the full LAUNCH_TIMEOUT.
        known = {getattr(p, "name", None) for p in getattr(self.mw, "profiles", [])}
        if known and name not in known:
            print(f"[GeminiEngine] no profile named '{name}' — skipping")
            return None
        # Ask the GUI thread to launch it (thread-safe signal).
        try:
            self.mw.request_launch.emit(name)
        except Exception as e:
            print(f"[GeminiEngine] launch request failed for {name}: {e}")
            return None
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            w = getattr(self.mw, "workers", {}).get(name)
            if w and getattr(w, "ready", False) and w.isRunning():
                return w
            time.sleep(0.3)
        return None
