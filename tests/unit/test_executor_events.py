"""Short event summaries for streaming execution progress."""

import json

from bridge.executor import describe_event
from bridge import cli
from bridge.service import _write_attempt_log_line


# @pt.id KIT-UNIT-LOG-001-event-summaries
# @pt.item Codex and Claude execution events have concise progress summaries
# @pt.condition known supported event shapes are described and unknown events are ignored
# @pt.expected summaries cover session, command, message, tool use and unknown event behavior
# @pt.target bridge.executor.describe_event
# @pt.method unit
# @pt.legacy none (new kit)
def test_describe_event_supported_shapes() -> None:
    assert "session started" in describe_event({"type": "thread.started",
                                                  "thread_id": "abc"})
    assert "exit 0" in describe_event({"type": "item.completed", "item": {
        "type": "command_execution", "command": "pytest", "exit_code": 0}})
    assert "agent:" in describe_event({"type": "item.completed", "item": {
        "type": "agent_message", "text": "done"}})
    assert "Read" in describe_event({"type": "assistant", "message": {"content": [{
        "type": "tool_use", "name": "Read", "input": {"file_path": "src/app.py"}}]}})
    assert describe_event({"type": "unrecognized"}) is None


# @pt.id KIT-UNIT-CLI-001-progress-stderr
# @pt.item Execute progress is written to stderr and final output remains on stdout
# @pt.condition CLI execute bridge emits one progress event and returns a summary
# @pt.expected stderr contains progress; stdout contains only the serialized summary
# @pt.target bridge.cli.main
# @pt.method unit
# @pt.legacy none (new kit)
def test_execute_cli_routes_progress_to_stderr(monkeypatch, capsys) -> None:
    class FakeBridge:
        progress = None

        def recover(self):
            pass

        async def execute_task(self, _task_id, wait=False):
            self.progress("[task #1] session started")
            return {"state": "REVIEW"}

    monkeypatch.setattr(cli, "Bridge", FakeBridge)
    cli.main(["execute", "task"])
    captured = capsys.readouterr()
    assert "[task #1] session started" in captured.err
    assert json.loads(captured.out) == {"state": "REVIEW"}
    assert "session started" not in captured.out


# @pt.id KIT-UNIT-LOG-002-attempt-log-redaction
# @pt.item Attempt log writer masks credential-shaped agent output
# @pt.condition a line containing an sk key is passed through the attempt log writer
# @pt.expected JSONL contains the redacted marker and omits the credential
# @pt.target bridge.service._write_attempt_log_line
# @pt.method unit
# @pt.legacy none (new kit)
def test_attempt_log_writer_redacts_credentials(tmp_path) -> None:
    secret = "sk-1234567890abcdefghijklmnop"
    path = tmp_path / "attempt.jsonl"
    with path.open("w", encoding="utf-8") as log:
        _write_attempt_log_line(log, "stdout", f"agent output {secret}")
    row = json.loads(path.read_text())
    assert "[REDACTED]" in row["line"]
    assert secret not in path.read_text()
