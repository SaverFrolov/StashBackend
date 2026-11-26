import asyncio
import os
import signal
from dataclasses import dataclass

ENV_ALLOWLIST = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


class ToolTimeout(Exception):
    pass


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


def _clean_env() -> dict:
    # No backend secrets reach yt-dlp/ffmpeg/whisper children.
    return {k: os.environ[k] for k in ENV_ALLOWLIST if k in os.environ}


async def run_tool(
    argv: list[str],
    *,
    timeout: float | None,
    cwd: str | None = None,
    nice: int | None = None,
) -> ProcResult:
    """Arg-array subprocess, sanitized env, process-group kill on timeout/cancel."""

    def _preexec():
        if nice:
            os.nice(nice)

    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=_clean_env(),
        start_new_session=True,
        preexec_fn=_preexec if nice else None,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await proc.wait()
        raise ToolTimeout(argv[0])
    return ProcResult(proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace"))
