import asyncio
import json
import multiprocessing
import time
from pathlib import Path

import pytest

from pharm.agent.jobs import QueryCancelled, QueryManager
from pharm.core.common import write_json


class Connection:
    disconnected = False

    async def is_disconnected(self):
        return self.disconnected


def sample_worker(body, directory):
    directory = Path(directory)
    write_json(directory / "ready.json", body)
    time.sleep(body.get("delay", 0))
    write_json(directory / "result.json", {"status": "succeeded", "candidates": [], "scientific_complete": False})
    write_json(directory / "worker_outcome.json", {"status": "succeeded"})


def crash_worker(body, directory):
    raise SystemExit(9)


async def ready(manager, request_id):
    for _ in range(400):
        job = manager.active.get(request_id)
        if job and (job.directory / "ready.json").is_file():
            return job
        await asyncio.sleep(0.05)
    raise AssertionError("worker did not start")


def test_disconnected_query_terminates_its_worker(tmp_path):
    async def scenario():
        manager, connection = QueryManager(sample_worker), Connection()
        task = asyncio.create_task(manager.run(connection, {"delay": 120}, tmp_path, "disconnect"))
        job = await ready(manager, "disconnect")
        worker_pid = job.process.pid
        connection.disconnected = True
        with pytest.raises(QueryCancelled):
            await asyncio.wait_for(task, timeout=10)
        assert worker_pid not in {child.pid for child in multiprocessing.active_children()}
        assert not (job.directory / "result.json").exists()
        record = json.loads((job.directory / "job.json").read_text(encoding="utf-8"))
        assert record["status"] == "cancelled" and record["reason"] == "client_disconnected"
        assert not manager.active
    asyncio.run(scenario())


def test_retry_stops_previous_before_starting_replacement(tmp_path):
    async def scenario():
        manager = QueryManager(sample_worker)
        old_task = asyncio.create_task(manager.run(Connection(), {"delay": 120}, tmp_path, "old-query"))
        old_job = await ready(manager, "old-query")
        old_pid = old_job.process.pid
        result = await manager.run(Connection(), {}, tmp_path, "new-query", supersedes="old-query")
        with pytest.raises(QueryCancelled):
            await old_task
        old = json.loads((old_job.directory / "job.json").read_text(encoding="utf-8"))
        new = json.loads((tmp_path / "local/agent/forward" / result["run_id"] / "job.json").read_text(encoding="utf-8"))
        assert old["reason"] == "superseded_by_retry"
        assert new["created_at"] >= old["finished_at"]
        assert old_pid not in {child.pid for child in multiprocessing.active_children()}
        assert not manager.active and result["result"]["status"] == "succeeded"
    asyncio.run(scenario())


def test_explicit_cancel_and_early_cancel(tmp_path):
    async def scenario():
        manager = QueryManager(sample_worker)
        task = asyncio.create_task(manager.run(Connection(), {"delay": 120}, tmp_path, "cancel-me"))
        await ready(manager, "cancel-me")
        assert (await manager.stop("cancel-me"))["stopped"]
        with pytest.raises(QueryCancelled):
            await task
        await manager.stop("not-started")
        with pytest.raises(QueryCancelled):
            await manager.run(Connection(), {}, tmp_path, "not-started")
        assert not manager.active
    asyncio.run(scenario())


def test_worker_crash_is_archived_as_failed(tmp_path):
    async def scenario():
        manager = QueryManager(crash_worker)
        with pytest.raises(RuntimeError, match="9"):
            await manager.run(Connection(), {}, tmp_path, "crash")
        status = json.loads((tmp_path / "local/agent/jobs/crash.json").read_text(encoding="utf-8"))
        assert status["status"] == "failed"
        assert not manager.active
    asyncio.run(scenario())


def test_real_forward_worker_reports_invalid_input(tmp_path):
    async def scenario():
        manager = QueryManager()
        with pytest.raises(RuntimeError, match="herbs"):
            await manager.run(Connection(), {}, tmp_path, "invalid")
        assert not manager.active
    asyncio.run(scenario())


def test_duplicate_id_and_cancelled_handler_leave_no_worker(tmp_path):
    async def scenario():
        manager = QueryManager(sample_worker)
        task = asyncio.create_task(manager.run(Connection(), {"delay": 120}, tmp_path, "same-id"))
        job = await ready(manager, "same-id")
        worker_pid = job.process.pid
        with pytest.raises(ValueError, match="重复提交"):
            await manager.run(Connection(), {}, tmp_path, "same-id")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert worker_pid not in {child.pid for child in multiprocessing.active_children()}
        assert not manager.active
    asyncio.run(scenario())


@pytest.mark.parametrize("supersedes", ["", 0, False])
def test_invalid_previous_request_id_never_starts_worker(tmp_path, supersedes):
    async def scenario():
        manager = QueryManager(sample_worker)
        with pytest.raises(ValueError, match="request_id/supersedes"):
            await manager.run(Connection(), {}, tmp_path, "new", supersedes=supersedes)
        assert not manager.active
    asyncio.run(scenario())


def test_navigation_and_background_tab_continue_until_tab_closes(tmp_path):
    async def scenario():
        manager = QueryManager(sample_worker, close_grace=0.5)
        manager.connect_tab("tab-one")
        connection = Connection()
        connection.disconnected = True
        task = asyncio.create_task(manager.run(connection, {"delay": 120}, tmp_path, "owned", owner_id="tab-one"))
        job = await ready(manager, "owned")
        worker_pid = job.process.pid
        manager.disconnect_tab("tab-one")
        await asyncio.sleep(0.15)
        assert job.process.is_alive()
        manager.connect_tab("tab-one")
        await asyncio.sleep(0.6)
        assert job.process.is_alive()  # The query HTTP connection has gone; the tab remains.
        manager.disconnect_tab("tab-one")
        with pytest.raises(QueryCancelled):
            await asyncio.wait_for(task, timeout=10)
        record = json.loads((job.directory / "job.json").read_text(encoding="utf-8"))
        assert record["reason"] == "tab_closed"
        assert worker_pid not in {child.pid for child in multiprocessing.active_children()}
    asyncio.run(scenario())


def test_owned_query_can_finish_after_its_http_connection_disappears(tmp_path):
    async def scenario():
        manager = QueryManager(sample_worker)
        manager.connect_tab("tab-active")
        connection = Connection()
        connection.disconnected = True
        result = await manager.run(connection, {}, tmp_path, "owned-success", owner_id="tab-active")
        assert result["result"]["status"] == "succeeded"
        assert not manager.active
    asyncio.run(scenario())


def test_reverse_uses_shared_presence_without_stopping_other_tab(tmp_path):
    async def scenario():
        manager = QueryManager(sample_worker, close_grace=0.1)
        manager.connect_tab("reverse-tab")
        manager.connect_tab("forward-tab")
        reverse_task = asyncio.create_task(manager.run(Connection(), {"delay": 120}, tmp_path, "reverse-job", owner_id="reverse-tab", direction="reverse"))
        forward_task = asyncio.create_task(manager.run(Connection(), {"delay": 120}, tmp_path, "forward-job", owner_id="forward-tab"))
        reverse_job = await ready(manager, "reverse-job")
        reverse_pid = reverse_job.process.pid
        forward_job = await ready(manager, "forward-job")
        assert reverse_job.directory.parent.name == "reverse"
        manager.disconnect_tab("reverse-tab")
        with pytest.raises(QueryCancelled):
            await asyncio.wait_for(reverse_task, timeout=10)
        assert forward_job.process.is_alive()
        assert reverse_pid not in {child.pid for child in multiprocessing.active_children()}
        record = json.loads((reverse_job.directory / "job.json").read_text(encoding="utf-8"))
        assert record["direction"] == "reverse" and record["reason"] == "tab_closed"
        await manager.stop("forward-job")
        with pytest.raises(QueryCancelled):
            await forward_task
        assert not manager.active
    asyncio.run(scenario())
