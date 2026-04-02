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
            # Claude CLI nests content: msg["message"]["content"]
            message = msg.get("message", msg)
            content = message.get("content", "")
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
        if msg_type == "result":
            # Claude CLI puts final text in msg["result"], but this duplicates
            # the text already emitted via assistant/message events.
            # Map to COMPLETED so _collect_agent_response can use it as fallback only.
            text = msg.get("result", "")
            return AgentEvent(type=EventType.COMPLETED, data={"text": str(text) if text else "", "reason": "result"})
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

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process) -> list[str]:
        """Read stderr to prevent pipe buffer deadlock. Returns collected lines."""
        lines: list[str] = []
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                text = chunk.decode(errors="replace").strip()
                if text:
                    lines.append(text)
        except Exception:
            pass
        return lines

    async def run_turn(self, session: dict[str, Any], prompt: str) -> AsyncIterator[AgentEvent]:
        cmd = [self._command]

        # Session resumption: --resume <name> reuses Phase 1 context
        resume = session.get("resume_session")
        if resume:
            cmd.extend(["--resume", resume, "--fork-session"])

        cmd.extend(["-p", prompt])
        cmd.extend([
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", "acceptEdits",
            "--model", self._model,
            "--max-turns", str(session.get("max_turns", self._max_turns)),
        ])

        # Name the session for later resumption
        session_name = session.get("session_name")
        if session_name:
            cmd.extend(["--name", session_name])
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

            # Drain stderr concurrently to prevent pipe buffer deadlock
            stderr_task = asyncio.create_task(self._drain_stderr(proc))

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
            stderr_lines = []
            try:
                stderr_lines = await stderr_task
            except asyncio.CancelledError:
                pass
            stderr_text = "\n".join(stderr_lines[-10:]) if stderr_lines else ""
            if proc.returncode != 0 and stderr_text:
                logger.warning("Claude CLI exited %d, stderr: %s", proc.returncode, stderr_text[:500])
            yield AgentEvent(
                type=EventType.COMPLETED,
                data={
                    "reason": "done" if proc.returncode == 0 else "error",
                    "exit_code": proc.returncode,
                    "stderr": stderr_text[:1000] if stderr_text else "",
                },
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
