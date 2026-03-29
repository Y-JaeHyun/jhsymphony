from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from jhsymphony.providers.base import AgentEvent, EventType, ProviderCapabilities, RunContext

logger = logging.getLogger(__name__)


class CodexProvider:
    def __init__(self, command: str = "codex", model: str = "o4-mini", sandbox: str = "read-only", max_turns: int = 30) -> None:
        self._command = command
        self._model = model
        self._sandbox = sandbox
        self._max_turns = max_turns

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_shell=True,
        )

    async def start_session(self, ctx: RunContext) -> dict[str, Any]:
        return {
            "workspace_path": ctx.workspace_path,
            "branch": ctx.branch,
            "issue_title": ctx.issue_title,
            "issue_body": ctx.issue_body,
            "env": ctx.env,
            "max_turns": min(ctx.max_turns, self._max_turns),
            "timeout_sec": ctx.timeout_sec,
            "process": None,
        }

    async def run_turn(self, session: dict[str, Any], prompt: str) -> AsyncIterator[AgentEvent]:
        cmd = [
            self._command,
            "exec",
            "--model", self._model,
            "--sandbox", self._sandbox,
            prompt,
        ]
        try:
            import os
            run_env = os.environ.copy()
            if session.get("env"):
                run_env.update(session["env"])
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=session["workspace_path"],
                env=run_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            session["process"] = proc
            yield AgentEvent(type=EventType.SESSION_STARTED, data={"pid": proc.pid})

            async for line in proc.stdout:
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                    msg_type = msg.get("type", "")
                    if msg_type == "tool_call":
                        yield AgentEvent(
                            type=EventType.TOOL_CALL,
                            data={"tool": msg.get("name", ""), "input": msg.get("input", {})},
                        )
                    elif msg_type == "tool_result":
                        yield AgentEvent(
                            type=EventType.TOOL_RESULT,
                            data={"content": msg.get("content", "")},
                        )
                    elif msg_type in ("error", "exception"):
                        yield AgentEvent(
                            type=EventType.ERROR,
                            data={"error": msg.get("message", str(msg))},
                        )
                    else:
                        content = msg.get("content") or msg.get("text") or msg.get("message", "")
                        if content:
                            yield AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": str(content)})
                except json.JSONDecodeError:
                    yield AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": text})

            await proc.wait()
            yield AgentEvent(
                type=EventType.COMPLETED,
                data={"reason": "done" if proc.returncode == 0 else "error", "exit_code": proc.returncode},
            )
        except asyncio.TimeoutError:
            if session.get("process"):
                session["process"].kill()
            yield AgentEvent(type=EventType.ERROR, data={"error": "timeout"})
        except Exception as e:
            logger.exception("CodexProvider error")
            yield AgentEvent(type=EventType.ERROR, data={"error": str(e)})

    async def cancel(self, session: dict[str, Any]) -> None:
        proc = session.get("process")
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
