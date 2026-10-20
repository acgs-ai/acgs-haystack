# SPDX-FileCopyrightText: 2026-present ACGS Contributors
#
# SPDX-License-Identifier: Apache-2.0
"""The README example must be the example file, and the example file must run."""

from __future__ import annotations

import runpy
import textwrap
from pathlib import Path

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
