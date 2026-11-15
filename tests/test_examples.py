# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""The README example must be the example file, and the example file must run."""

from __future__ import annotations

import json
import os
import runpy
import socket
import textwrap
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "quickstart.py"


def test_readme_embeds_the_quickstart_example() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    body = EXAMPLE.read_text(encoding="utf-8").split('"""', 2)[2].strip()
    assert body in readme


def test_quickstart_example_runs(capsys: pytest.CaptureFixture[str]) -> None:
    runpy.run_path(str(EXAMPLE), run_name="__main__")
    out = capsys.readouterr().out
    assert "get_weather: ran: Sunny in Paris" in out
    assert "send_email: rejected:" in out
    assert "ACGS-006" in out


def test_deny_and_audit_runs_offline_without_api_keys(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for key in list(os.environ):
        if key.endswith("API_KEY"):
            monkeypatch.delenv(key)
    # Register the example's environment change so monkeypatch restores the original value.
    monkeypatch.setenv("HAYSTACK_TELEMETRY_ENABLED", "False")

    def no_network(*args: Any, **kwargs: Any) -> None:
        pytest.fail("The offline example attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    runpy.run_path(str(ROOT / "examples" / "deny_and_audit.py"), run_name="__main__")
    output = capsys.readouterr().out
    execution, audit = output.split("Audit trail (application decisions):\n")
    assert execution == "Executed record_note: The meeting starts at noon.\n"
    decisions = [json.loads(line) for line in audit.splitlines()]
    assert [decision["verdict"] for decision in decisions] == ["allow", "deny"]
    assert [decision["tool_name"] for decision in decisions] == ["record_note", "record_note"]
    assert decisions[0]["rule_ids"] == []
    assert decisions[1]["rule_ids"] == ["ACGS-006"]
    assert "Blocked by" in decisions[1]["reason"]
    assert all(decision["error"] is None for decision in decisions)
    assert "demo-secret" not in output


def _python_blocks(markdown: str) -> list[str]:
    blocks, current, inside = [], [], False
    for line in markdown.splitlines():
        if line.strip() == "```python":
            inside, current = True, []
        elif inside and line.strip() == "```":
            inside = False
            blocks.append(textwrap.dedent("\n".join(current)))
        elif inside:
            current.append(line)
    return blocks


def test_every_readme_python_block_runs(capsys: pytest.CaptureFixture[str]) -> None:
    blocks = _python_blocks((ROOT / "README.md").read_text(encoding="utf-8"))
    assert len(blocks) >= 4
    for block in blocks:
        exec(compile(block, "README.md", "exec", dont_inherit=True), {"__name__": "__readme__"})  # noqa: S102
    assert "send_email: rejected:" in capsys.readouterr().out
