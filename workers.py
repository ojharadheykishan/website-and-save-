import asyncio
import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

QueueHandler = Callable[[dict[str, Any]], Awaitable[None]]
_handlers: dict[str, QueueHandler] = {}


def register_handler(name: str, handler: QueueHandler) -> None:
    if not name or not callable(handler):
        raise ValueError("A queue handler requires a name and callable.")
    _handlers[name] = handler


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteTaskStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS background_tasks (
                    id TEXT PRIMARY KEY,
                    queue TEXT NOT NULL,
                    handler TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS background_tasks_ready "
                "ON background_tasks(queue, status, created_at)"
            )
            # A single Railway bot instance owns this local queue database.
            connection.execute(
                "UPDATE background_tasks SET status='pending', updated_at=? "
                "WHERE status='running'",
                (_utc_now(),),
            )

    def enqueue(self, queue: str, handler: str, payload: dict[str, Any], task_id: str) -> None:
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO background_tasks
                    (id, queue, handler, payload, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (task_id, queue, handler, json.dumps(payload), now, now),
            )

    def claim(self, queue: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM background_tasks "
                "WHERE queue=? AND status='pending' ORDER BY created_at, id LIMIT 1",
                (queue,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE background_tasks SET status='running', attempts=attempts+1, "
                "updated_at=? WHERE id=? AND status='pending'",
                (_utc_now(), row["id"]),
            )
            return {
                "id": row["id"],
                "queue": row["queue"],
                "handler": row["handler"],
                "payload": json.loads(row["payload"]),
                "attempts": row["attempts"] + 1,
            }

    def finish(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET status='done', error=NULL, updated_at=? WHERE id=?",
                (_utc_now(), task_id),
            )

    def retry(self, task_id: str, error: str, retry: bool) -> None:
        status = "pending" if retry else "failed"
        with self._connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET status=?, error=?, updated_at=? WHERE id=?",
                (status, error[:2000], _utc_now(), task_id),
            )

    def release(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET status='pending', updated_at=? WHERE id=? "
                "AND status='running'",
                (_utc_now(), task_id),
            )

    def cancel_running(self, task_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET status='cancelled', updated_at=? "
                "WHERE id=? AND status='running'",
                (_utc_now(), task_id),
            )

    def update_payload(self, task_id: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE background_tasks SET payload=?, updated_at=? WHERE id=?",
                (json.dumps(payload), _utc_now(), task_id),
            )

    def cancel_user(self, user_id: int) -> int:
        with self._connect() as connection:
            try:
                result = connection.execute(
                    "UPDATE background_tasks SET status='cancelled', updated_at=? "
                    "WHERE status='pending' AND json_extract(payload, '$.user_id')=?",
                    (_utc_now(), user_id),
                )
                return result.rowcount
            except sqlite3.OperationalError:
                rows = connection.execute(
                    "SELECT id, payload FROM background_tasks WHERE status='pending'"
                ).fetchall()
                cancelled = 0
                for row in rows:
                    try:
                        payload = json.loads(row["payload"])
                    except json.JSONDecodeError:
                        logger.exception(
                            "Invalid task payload found while cancelling queued tasks."
                        )
                        continue
                    if payload.get("user_id") == user_id:
                        connection.execute(
                            "UPDATE background_tasks SET status='cancelled', updated_at=? WHERE id=?",
                            (_utc_now(), row["id"]),
                        )
                        cancelled += 1
                return cancelled

    def counts(self, user_id: int | None = None) -> dict[str, int]:
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
        with self._connect() as connection:
            if user_id is not None:
                try:
                    rows = connection.execute(
                        "SELECT status, COUNT(*) AS count FROM background_tasks "
                        "WHERE json_extract(payload, '$.user_id')=? GROUP BY status",
                        (user_id,),
                    ).fetchall()
                    for row in rows:
                        if row["status"] in counts:
                            counts[row["status"]] = row["count"]
                    return counts
                except sqlite3.OperationalError:
                    rows = connection.execute(
                        "SELECT status, payload FROM background_tasks"
                    ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT status, COUNT(*) AS count FROM background_tasks GROUP BY status"
                ).fetchall()
                for row in rows:
                    if row["status"] in counts:
                        counts[row["status"]] = row["count"]
                return counts
        for row in rows:
            try:
                if json.loads(row["payload"]).get("user_id") != user_id:
                    continue
            except json.JSONDecodeError:
                logger.exception("Invalid task payload found while reading queue counts.")
                continue
            status = row["status"]
            if status in counts:
                counts[status] += 1
        return counts

    def close(self) -> None:
        return


class MongoTaskStore:
    def __init__(self, uri: str, database_name: str, max_pool_size: int) -> None:
        from motor.motor_asyncio import AsyncIOMotorClient

        self.client = AsyncIOMotorClient(uri, maxPoolSize=max_pool_size)
        self.collection = self.client[database_name]["background_tasks"]

    async def initialize(self) -> None:
        await self.client.admin.command("ping")
        await self.collection.create_index([("queue", 1), ("status", 1), ("created_at", 1)])
        await self.collection.update_many(
            {"status": "running"},
            {"$set": {"status": "pending", "updated_at": _utc_now()}},
        )

    async def enqueue(self, queue: str, handler: str, payload: dict[str, Any], task_id: str) -> None:
        await self.collection.insert_one(
            {
                "_id": task_id,
                "queue": queue,
                "handler": handler,
                "payload": payload,
                "status": "pending",
                "attempts": 0,
                "error": None,
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
            }
        )

    async def claim(self, queue: str) -> dict[str, Any] | None:
        from pymongo import ReturnDocument

        row = await self.collection.find_one_and_update(
            {"queue": queue, "status": "pending"},
            {
                "$set": {"status": "running", "updated_at": _utc_now()},
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if row is None:
            return None
        return {
            "id": row["_id"],
            "queue": row["queue"],
            "handler": row["handler"],
            "payload": row["payload"],
            "attempts": row["attempts"],
        }

    async def finish(self, task_id: str) -> None:
        await self.collection.update_one(
            {"_id": task_id},
            {"$set": {"status": "done", "error": None, "updated_at": _utc_now()}},
        )

    async def retry(self, task_id: str, error: str, retry: bool) -> None:
        await self.collection.update_one(
            {"_id": task_id},
            {
                "$set": {
                    "status": "pending" if retry else "failed",
                    "error": error[:2000],
                    "updated_at": _utc_now(),
                }
            },
        )

    async def release(self, task_id: str) -> None:
        await self.collection.update_one(
            {"_id": task_id, "status": "running"},
            {"$set": {"status": "pending", "updated_at": _utc_now()}},
        )

    async def cancel_running(self, task_id: str) -> None:
        await self.collection.update_one(
            {"_id": task_id, "status": "running"},
            {"$set": {"status": "cancelled", "updated_at": _utc_now()}},
        )

    async def update_payload(self, task_id: str, payload: dict[str, Any]) -> None:
        await self.collection.update_one(
            {"_id": task_id},
            {"$set": {"payload": payload, "updated_at": _utc_now()}},
        )

    async def cancel_user(self, user_id: int) -> int:
        result = await self.collection.update_many(
            {"status": "pending", "payload.user_id": user_id},
            {"$set": {"status": "cancelled", "updated_at": _utc_now()}},
        )
        return result.modified_count

    async def counts(self, user_id: int | None = None) -> dict[str, int]:
        query = {"payload.user_id": user_id} if user_id is not None else {}
        rows = self.collection.aggregate(
            [{"$match": query}, {"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        )
        counts = {"pending": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0}
        async for row in rows:
            if row["_id"] in counts:
                counts[row["_id"]] = row["count"]
        return counts

    async def close(self) -> None:
        self.client.close()


class BackgroundWorkers:
    QUEUES = ("downloads",)

    def __init__(self) -> None:
        self.download_concurrency = self._positive_int("DOWNLOAD_CONCURRENCY", 10)
        self.upload_concurrency = self._positive_int("UPLOAD_CONCURRENCY", 5)
        self.max_retries = self._positive_int("TASK_MAX_RETRIES", 3)
        self.graceful_timeout = self._positive_int("WORKER_SHUTDOWN_TIMEOUT", 30)
        self._store: Any = None
        self._tasks: list[asyncio.Task[None]] = []
        self._transfer_tasks: dict[str, list[asyncio.Task[None]]] = {
            "downloads": [],
            "uploads": [],
        }
        self._transfer_queues: dict[
            str,
            asyncio.Queue[
            tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]] | None
            ],
        ] = {queue: asyncio.Queue() for queue in self.QUEUES + ("uploads",)}
        self._active_tasks: dict[
            str, tuple[asyncio.Task[None], int | None, str]
        ] = {}
        self._active_transfers: dict[
            asyncio.Future[Any], tuple[str, asyncio.Task[Any]]
        ] = {}
        self._wakeups = {queue: asyncio.Event() for queue in self.QUEUES}
        self._closing = False
        self._upload_closing = False
        self.backend = "not_started"
        self.backend_error: str | None = None

    @staticmethod
    def _positive_int(name: str, default: int) -> int:
        raw = os.environ.get(name, str(default))
        try:
            value = int(raw)
        except ValueError as error:
            raise ValueError(f"{name} must be a positive integer.") from error
        if value < 1:
            raise ValueError(f"{name} must be a positive integer.")
        return value

    async def start(self) -> None:
        if self._tasks:
            return
        mongo_uri = (os.environ.get("MONGODB_URI") or os.environ.get("MONGO_DB") or "").strip()
        database_name = os.environ.get("MONGO_DATABASE", "save_restricted_bot").strip()
        if mongo_uri:
            pool_size = self._positive_int("MONGO_MAX_POOL_SIZE", 20)
            if not database_name:
                raise ValueError("MONGO_DATABASE cannot be empty.")
            try:
                candidate = MongoTaskStore(
                    mongo_uri,
                    database_name,
                    pool_size,
                )
                await candidate.initialize()
                self._store = candidate
                self.backend = "mongodb"
            except Exception as error:
                self.backend_error = str(error)
                logger.exception("MongoDB task queue unavailable; using the disk queue fallback.")
                try:
                    candidate.client.close()
                except (UnboundLocalError, AttributeError):
                    pass
        if self._store is None:
            queue_path = os.environ.get(
                "TASK_QUEUE_DB",
                os.path.join(os.getcwd(), "data", "background_tasks.sqlite3"),
            )
            candidate = SQLiteTaskStore(queue_path)
            await asyncio.to_thread(candidate.initialize)
            self._store = candidate
            self.backend = "sqlite"

        for worker_number in range(self.download_concurrency):
            task = asyncio.create_task(
                self._worker("downloads"),
                name=f"downloads-job-worker-{worker_number + 1}",
            )
            self._tasks.append(task)
        for queue, concurrency in (
            ("downloads", self.download_concurrency),
            ("uploads", self.upload_concurrency),
        ):
            for worker_number in range(concurrency):
                task = asyncio.create_task(
                    self._transfer_worker(queue),
                    name=f"{queue}-transfer-worker-{worker_number + 1}",
                )
                self._transfer_tasks[queue].append(task)
        logger.info(
            "Background workers started: backend=%s downloads=%d uploads=%d",
            self.backend,
            self.download_concurrency,
            self.upload_concurrency,
        )

    async def submit(self, queue: str, handler: str, payload: dict[str, Any]) -> str:
        if queue not in self.QUEUES:
            raise ValueError(f"Unknown background queue: {queue}")
        if self._store is None or self._closing:
            raise RuntimeError("Background workers are not accepting tasks.")
        if handler not in _handlers:
            raise ValueError(f"Unregistered background task handler: {handler}")
        serialized_payload = json.loads(json.dumps(payload))
        task_id = uuid.uuid4().hex
        await self._call_store("enqueue", queue, handler, serialized_payload, task_id)
        self._wakeups[queue].set()
        return task_id

    async def run_upload(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        return await self._run_transfer("uploads", operation)

    async def run_download(self, operation: Callable[[], Awaitable[Any]]) -> Any:
        return await self._run_transfer("downloads", operation)

    async def _run_transfer(
        self,
        queue: str,
        operation: Callable[[], Awaitable[Any]],
    ) -> Any:
        if not callable(operation):
            raise TypeError("A transfer operation must be callable.")
        if self._store is None:
            return await operation()
        if not self._transfer_tasks[queue] or self._upload_closing:
            raise RuntimeError(f"The {queue} transfer queue is not accepting transfers.")
        result = asyncio.get_running_loop().create_future()
        await self._transfer_queues[queue].put((operation, result))
        try:
            return await result
        except asyncio.CancelledError:
            result.cancel()
            active_transfer = self._active_transfers.get(result)
            transfer_task = active_transfer[1] if active_transfer else None
            if transfer_task and not transfer_task.done():
                transfer_task.cancel()
            raise

    async def _transfer_worker(self, queue: str) -> None:
        while True:
            item = await self._transfer_queues[queue].get()
            try:
                if item is None:
                    return
                operation, result = item
                if result.cancelled():
                    continue
                transfer_task = asyncio.create_task(operation())
                self._active_transfers[result] = (queue, transfer_task)
                try:
                    value = await transfer_task
                except asyncio.CancelledError:
                    if transfer_task.cancelled() and not asyncio.current_task().cancelling():
                        if not result.done():
                            result.cancel()
                        continue
                    raise
                except Exception as error:
                    if not result.done():
                        result.set_exception(error)
                else:
                    if not result.done():
                        result.set_result(value)
                finally:
                    self._active_transfers.pop(result, None)
            finally:
                self._transfer_queues[queue].task_done()

    async def _worker(self, queue: str) -> None:
        while True:
            job = await self._call_store("claim", queue)
            if job is None:
                if self._closing:
                    return
                self._wakeups[queue].clear()
                try:
                    await asyncio.wait_for(self._wakeups[queue].wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
                continue

            task = asyncio.current_task()
            handler_task = None
            try:
                payload = dict(job["payload"])
                payload["_task_id"] = job["id"]
                if task is None:
                    raise RuntimeError("The background task has no worker context.")
                handler = _handlers.get(job["handler"])
                if handler is None:
                    raise RuntimeError(f"Task handler is not registered: {job['handler']}")
                handler_task = asyncio.create_task(
                    handler(payload),
                    name=f"task-{job['id']}",
                )
                self._active_tasks[job["id"]] = (
                    handler_task,
                    payload.get("user_id"),
                    job["handler"],
                )
                await handler_task
            except asyncio.CancelledError:
                if handler_task and handler_task.cancelled() and task and not task.cancelling():
                    await self._call_store("cancel_running", job["id"])
                    logger.info("Background task cancelled: id=%s", job["id"])
                else:
                    await self._call_store("release", job["id"])
                    raise
            except Exception as error:
                retry = job["attempts"] < self.max_retries and not self._closing
                logger.exception(
                    "Background task failed: id=%s queue=%s attempt=%d retry=%s",
                    job["id"],
                    queue,
                    job["attempts"],
                    retry,
                )
                await self._call_store("retry", job["id"], str(error), retry)
                if retry:
                    self._wakeups[queue].set()
            else:
                await self._call_store("finish", job["id"])
            finally:
                self._active_tasks.pop(job["id"], None)

    async def counts(self, user_id: int | None = None) -> dict[str, int]:
        if self._store is None:
            raise RuntimeError("Background workers have not been started.")
        return await self._call_store("counts", user_id)

    async def checkpoint(self, task_id: str, payload: dict[str, Any]) -> None:
        if self._store is None:
            raise RuntimeError("Background workers have not been started.")
        stored_payload = {key: value for key, value in payload.items() if key != "_task_id"}
        stored_payload = json.loads(json.dumps(stored_payload))
        await self._call_store("update_payload", task_id, stored_payload)

    async def cancel_user(self, user_id: int) -> int:
        if self._store is None:
            raise RuntimeError("Background workers have not been started.")
        cancelled = await self._call_store("cancel_user", user_id)
        for task, active_user_id, handler in tuple(self._active_tasks.values()):
            if active_user_id == user_id and handler != "batch_link" and not task.done():
                task.cancel()
                cancelled += 1
        for wakeup in self._wakeups.values():
            wakeup.set()
        return cancelled

    async def _call_store(self, method_name: str, *args: Any) -> Any:
        method = getattr(self._store, method_name)
        if self.backend == "sqlite":
            return await asyncio.to_thread(method, *args)
        return await method(*args)

    async def status(self) -> dict[str, Any]:
        counts = await self.counts() if self._store is not None else {}
        return {
            "backend": self.backend,
            "backend_error": self.backend_error,
            "downloads": self.download_concurrency,
            "uploads": self.upload_concurrency,
            "active_workers": len(self._active_tasks),
            "transfer_queues": {
                queue: {
                    "waiting": self._transfer_queues[queue].qsize(),
                    "active": sum(
                        1 for active_queue, _ in self._active_transfers.values()
                        if active_queue == queue
                    ),
                }
                for queue in self._transfer_queues
            },
            "tasks": counts,
        }

    async def stop(self) -> None:
        if not self._tasks:
            return
        self._closing = True
        for wakeup in self._wakeups.values():
            wakeup.set()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks),
                timeout=self.graceful_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Download worker shutdown timed out after %d seconds; pending tasks will be recovered on restart.",
                self.graceful_timeout,
            )
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            self._tasks.clear()
            self._upload_closing = True
            transfer_tasks = [
                task
                for worker_tasks in self._transfer_tasks.values()
                for task in worker_tasks
            ]
            for queue, worker_tasks in self._transfer_tasks.items():
                for _ in worker_tasks:
                    await self._transfer_queues[queue].put(None)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*transfer_tasks),
                    timeout=self.graceful_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning("Transfer workers did not drain before shutdown; stopping them.")
                for task in transfer_tasks:
                    task.cancel()
                await asyncio.gather(*transfer_tasks, return_exceptions=True)
            for worker_tasks in self._transfer_tasks.values():
                worker_tasks.clear()
            if self._store is not None:
                if self.backend == "sqlite":
                    await asyncio.to_thread(self._store.close)
                else:
                    await self._store.close()
                self._store = None
            self._closing = False


_worker_manager: BackgroundWorkers | None = None


def get_worker_manager() -> BackgroundWorkers:
    global _worker_manager
    if _worker_manager is None:
        _worker_manager = BackgroundWorkers()
    return _worker_manager


async def start_workers() -> BackgroundWorkers:
    manager = get_worker_manager()
    await manager.start()
    return manager


async def stop_workers() -> None:
    if _worker_manager is not None:
        await _worker_manager.stop()
