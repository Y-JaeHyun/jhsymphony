from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from jhsymphony.providers.base import AgentEvent, EventType, ProviderCapabilities, RunContext

logger = logging.getLogger(__name__)


class ClaudeProvider:
    def __init__(self, command: str = "claude", model: str = "claude-opus-4-5", max_turns: int = 30) -> None:
        self._command = command
        self._model = model
        self._max_turns = max_turns

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_tools=True,
            supports_streaming=True,
            supports_shell=True,
            supports_interrupt=True,
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

    def _parse_event(self, msg: dict[str, Any]) -> AgentEvent | None:
        msg_type = msg.get("type", "")
        if msg_type in ("assistant", "message"):
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    block.get("text", "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text = "".join(text_parts)
            else:
                text = str(content)
            return AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": text})
        if msg_type == "tool_use":
            return AgentEvent(
                type=EventType.TOOL_CALL,
                data={"tool": msg.get("name", ""), "input": msg.get("input", {})},
            )
        if msg_type == "tool_result":
            return AgentEvent(
                type=EventType.TOOL_RESULT,
                data={"content": msg.get("content", "")},
            )
        if msg_type == "usage":
            return AgentEvent(type=EventType.USAGE, data=msg)
        if msg_type in ("error", "exception"):
            return AgentEvent(type=EventType.ERROR, data={"error": msg.get("message", str(msg))})
        return None

    async def run_turn(self, session: dict[str, Any], prompt: str) -> AsyncIterator[AgentEvent]:
        cmd = [
            self._command,
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "acceptEdits",
            "--model", self._model,
            "--max-turns", str(session.get("max_turns", self._max_turns)),
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
                    event = self._parse_event(msg)
                    if event is not None:
                        yield event
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
            logger.exception("ClaudeProvider error")
            yield AgentEvent(type=EventType.ERROR, data={"error": str(e)})

    async def cancel(self, session: dict[str, Any]) -> None:
        proc = session.get("process")
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
