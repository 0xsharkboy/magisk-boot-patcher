"""Thin wrapper around external tools."""

import subprocess
from collections.abc import Sequence

from .errors import ToolError


def run_tool(args: Sequence[object], *, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    """Run an external tool capturing stdout/stderr as bytes.

    Raises ToolError (exit code 2) if the tool is missing or, when ``check``
    is true, exits non-zero. ``stderr`` is attached to the error.
    """
    cmd = [str(a) for a in args]
    try:
        proc = subprocess.run(cmd, capture_output=True, **kwargs)
    except FileNotFoundError as e:
        raise ToolError(f"tool not found: {cmd[0]}", cmd, str(e)) from e
    if check and proc.returncode != 0:
        err = proc.stderr
        if isinstance(err, bytes):
            err = err.decode(errors="replace")
        raise ToolError(f"{cmd[0]} failed with exit code {proc.returncode}", cmd, err or "")
    return proc
