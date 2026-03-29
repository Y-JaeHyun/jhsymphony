from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class SubprocessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


async def run_subprocess(
    command: list[str],
    cwd: str,
    env: dict[str, str] | None,
    timeout_sec: int = 1800,
) -> SubprocessResult:
    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=cwd,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
        return SubprocessResult(
            returncode=proc.returncode or 0,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return SubprocessResult(
            returncode=-1,
            stdout="",
            stderr="Process timed out",
            timed_out=True,
        )
