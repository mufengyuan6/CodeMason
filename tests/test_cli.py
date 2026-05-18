"""Phase 1 测试：headless CLI（run + --mode rpc）。"""

import json

import pytest

from src.cli.main import main
from src.protocol import parse_event


class TestCli:
    def test_run_act_mode(self, tmp_path, capsys):
        code = main(["run", "--task", "修复登录 bug", "--session", "cli-test"])
        assert code == 0
        out = capsys.readouterr().out
        assert "cli-test" in out
        assert "TurnStarted" in out or "ItemCompleted" in out

    def test_run_rpc_mode_output(self, tmp_path, capsys):
        code = main(["run", "--task", "修 bug", "--session", "cli-rpc", "--mode", "rpc"])
        assert code == 0
        lines = capsys.readouterr().out.strip().splitlines()
        # 结构化事件行可解析
        parsed = [json.loads(l) for l in lines[:-1] if l.strip().startswith("{")]
        assert len(parsed) >= 1
        ev = parse_event(parsed[0])
        assert ev.type.value == "TurnStarted"

    def test_rpc_mode_stdin(self, tmp_path, capsys, monkeypatch):
        """--mode rpc：stdin Op → stdout Event。"""
        import io

        op_line = json.dumps({"protocol_version": "v1", "op_id": "op1", "type": "UserTurnStart", "content": "hello", "mode": "act"})
        monkeypatch.setattr("sys.stdin", io.StringIO(op_line + "\n"))
        code = main(["rpc", "--session", "rpc-test"])
        assert code == 0
        out = capsys.readouterr().out
        lines = [json.loads(l) for l in out.strip().splitlines()]
        types = {l.get("type") for l in lines}
        assert "TurnStarted" in types
