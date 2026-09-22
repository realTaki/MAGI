from __future__ import annotations

import subprocess
import sys


def test_python_m_magi_is_the_one_command() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "magi", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "handle" in result.stdout
    assert "base" in result.stdout
    assert "token" in result.stdout
