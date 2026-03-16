from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional


TaskCallable = Callable[..., Any]


class TaskSchedulerService:
    def __init__(self) -> None:
        self._jobs: Dict[str, dict] = {}
        self._running_tasks: Dict[str, asyncio.Task] = {}

    def list_jobs(self) -> list[dict]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> Optional[dict]:
        return self._jobs.get(job_id)

    def create_once(
        self,
        title: str,
        delay_seconds: float,
        callback: TaskCallable,
        *args,
        **kwargs,
    ) -> dict:
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "title": title,
            "type": "once",
            "delay_seconds": delay_seconds,
            "created_at": time.time(),
            "status": "scheduled",
        }
        self._jobs[job_id] = job
        self._running_tasks[job_id] = asyncio.create_task(
            self._run_once(job_id, delay_seconds, callback, *args, **kwargs)
        )
        return job

    def create_interval(
        self,
        title: str,
        interval_seconds: float,
        callback: TaskCallable,
        *args,
        **kwargs,
    ) -> dict:
        job_id = str(uuid.uuid4())
        job = {
            "id": job_id,
            "title": title,
            "type": "interval",
            "interval_seconds": interval_seconds,
            "created_at": time.time(),
            "status": "scheduled",
            "runs": 0,
        }
        self._jobs[job_id] = job
        self._running_tasks[job_id] = asyncio.create_task(
            self._run_interval(job_id, interval_seconds, callback, *args, **kwargs)
        )
        return job

    async def _run_once(self, job_id: str, delay_seconds: float, callback: TaskCallable, *args, **kwargs) -> None:
        await asyncio.sleep(max(delay_seconds, 0))
        job = self._jobs.get(job_id)
        if not job or job["status"] == "cancelled":
            return

        job["status"] = "running"
        try:
            result = callback(*args, **kwargs)
            if asyncio.iscoroutine(result):
                await result
            job["status"] = "completed"
            job["completed_at"] = time.time()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)

    async def _run_interval(self, job_id: str, interval_seconds: float, callback: TaskCallable, *args, **kwargs) -> None:
        while True:
            job = self._jobs.get(job_id)
            if not job or job["status"] == "cancelled":
                return
            await asyncio.sleep(max(interval_seconds, 0.1))
            job["status"] = "running"
            try:
                result = callback(*args, **kwargs)
                if asyncio.iscoroutine(result):
                    await result
                job["runs"] = int(job.get("runs", 0)) + 1
                job["status"] = "scheduled"
                job["last_run_at"] = time.time()
            except Exception as exc:
                job["status"] = "failed"
                job["error"] = str(exc)
                return

    def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        task = self._running_tasks.get(job_id)
        if not job:
            return False
        job["status"] = "cancelled"
        if task and not task.done():
            task.cancel()
        return True
