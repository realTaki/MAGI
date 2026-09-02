"""ToolsWorker dispatches workspace file tools through RunToolJob."""

from __future__ import annotations

from bus import Bus, JobStatus, LLMToolCall, ListToolsJob, RunToolJob
from tools.registry import configure
from tools.worker import ToolsWorker


def _run(board, *, name: str, arguments: dict, tool_call_id: str = "1"):
    job_id = board.publish(
        RunToolJob(
            publisher="test",
            call=LLMToolCall(tool_call_id=tool_call_id, name=name, arguments=arguments),
        )
    )
    return board.get_result(job_id, timeout=5.0)


def test_tools_worker_runs_filesystem_tools(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    with Bus("@tools-fs", workspace=workspace) as bus:
        assert bus.attach(ToolsWorker)
        board = bus.board(RunToolJob)
        listed = bus.board(ListToolsJob)
        assert board is not None and listed is not None
        names = {tool.definition.name for tool in listed.publish(ListToolsJob(publisher="test")).tools or []}
        assert {"read_file", "write_file", "edit_file", "list_files"} <= names

        written = _run(
            board, name="write_file", arguments={"path": "notes/a.txt", "content": "hello"}
        )
        assert written is not None
        assert written.status is JobStatus.COMPLETED
        assert (workspace / "notes" / "a.txt").read_text(encoding="utf-8") == "hello"

        read = _run(board, name="read_file", arguments={"path": "notes/a.txt"})
        assert read is not None
        assert read.status is JobStatus.COMPLETED
        assert read.content == "hello"

        listed_dir = _run(board, name="list_files", arguments={"path": "notes"})
        assert listed_dir is not None
        assert listed_dir.status is JobStatus.COMPLETED
        assert "a.txt" in (listed_dir.content or "")

        edited = _run(
            board,
            name="edit_file",
            arguments={"path": "notes/a.txt", "old_str": "hello", "new_str": "hi"},
        )
        assert edited is not None
        assert edited.status is JobStatus.COMPLETED
        assert (workspace / "notes" / "a.txt").read_text(encoding="utf-8") == "hi"

        missing = _run(board, name="no_such_tool", arguments={})
        assert missing is not None
        assert missing.status is JobStatus.FAILED
        assert missing.error is not None
        assert "unknown tool" in missing.error
    configure(bus=None)
