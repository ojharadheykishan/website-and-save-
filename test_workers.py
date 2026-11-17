import asyncio
import sqlite3

from workers import BackgroundWorkers, SQLiteTaskStore, register_handler


def test_download_workers_bound_concurrency_and_process_backlog(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_QUEUE_DB", str(tmp_path / "tasks.sqlite3"))
    monkeypatch.setenv("DOWNLOAD_CONCURRENCY", "2")
    monkeypatch.setenv("UPLOAD_CONCURRENCY", "1")
    manager = BackgroundWorkers()
    state = {"active": 0, "peak": 0, "completed": 0}
    destinations = []
    complete = asyncio.Event()

    async def handler(payload):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        destinations.append((payload["index"], payload["destination_chat_id"]))
        state["completed"] += 1
        if state["completed"] == 50:
            complete.set()

    register_handler("test_backlog", handler)

    async def run():
        await manager.start()
        try:
            for index in range(50):
                await manager.submit(
                    "downloads",
                    "test_backlog",
                    {
                        "user_id": index % 2,
                        "index": index,
                        "destination_chat_id": -100000 - index,
                    },
                )
            await asyncio.wait_for(complete.wait(), timeout=10)
            for _ in range(100):
                counts = await manager.counts()
                if counts["done"] == 50:
                    break
                await asyncio.sleep(0.01)
            assert state["peak"] == 2
            assert set(destinations) == {
                (index, -100000 - index) for index in range(50)
            }
            assert (await manager.counts(0))["done"] == 25
            assert (await manager.counts(1))["done"] == 25
        finally:
            await manager.stop()

    asyncio.run(run())


def test_running_tasks_are_recovered_and_failed_tasks_do_not_block_queue(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "recovery.sqlite3"
    monkeypatch.setenv("TASK_QUEUE_DB", str(database_path))
    store = SQLiteTaskStore(str(database_path))
    store.initialize()
    store.enqueue(
        "downloads",
        "test_recovery",
        {"user_id": 1, "task": "recovered"},
        "recover-me",
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE background_tasks SET status='running' WHERE id='recover-me'"
        )

    manager = BackgroundWorkers()
    recovered = asyncio.Event()
    attempts = {"retry": 0}

    async def handler(payload):
        if payload["task"] == "retry":
            attempts["retry"] += 1
            if attempts["retry"] == 1:
                raise RuntimeError("temporary error")
        else:
            recovered.set()

    register_handler("test_recovery", handler)
    register_handler("test_retry", handler)

    async def run():
        await manager.start()
        try:
            await manager.submit("downloads", "test_retry", {"user_id": 2, "task": "retry"})
            await asyncio.wait_for(recovered.wait(), timeout=5)
            for _ in range(100):
                counts = await manager.counts()
                if counts["done"] == 2:
                    break
                await asyncio.sleep(0.01)
            assert counts["done"] == 2
            assert counts["failed"] == 0
            assert attempts["retry"] == 2
        finally:
            await manager.stop()

    asyncio.run(run())


def test_upload_queue_limits_active_transfers(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_QUEUE_DB", str(tmp_path / "uploads.sqlite3"))
    monkeypatch.setenv("DOWNLOAD_CONCURRENCY", "2")
    monkeypatch.setenv("UPLOAD_CONCURRENCY", "1")
    manager = BackgroundWorkers()
    active = {"now": 0, "peak": 0}

    async def operation():
        active["now"] += 1
        active["peak"] = max(active["peak"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1

    async def run():
        await manager.start()
        try:
            await asyncio.gather(
                *(manager.run_upload(operation) for _ in range(12))
            )
            assert active["peak"] == 1
        finally:
            await manager.stop()

    asyncio.run(run())
