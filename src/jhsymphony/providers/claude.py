from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from jhsymphony.providers.base import AgentEvent, EventType, ProviderCapabilities, RunContext

logger = logging.getLogger(__name__)


class ClaudeProvider:
    def __init__(self, command: str = "claude", model: str = "claude-opus-4-5", max_turns: int = 50) -> None:
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
            "max_turns": self._max_turns,
            "timeout_sec": ctx.timeout_sec,
            "process": None,
        }

    def _parse_events(self, msg: dict[str, Any]) -> list[AgentEvent]:
        """Parse a stream-json message into one or more AgentEvents.

        Claude CLI stream-json format nests tool_use/tool_result blocks inside
        assistant/user messages. We extract them as separate events so that
        health-check and verification logic can detect tool usage.
        """
        msg_type = msg.get("type", "")
        events: list[AgentEvent] = []

        if msg_type in ("assistant", "message"):
            message = msg.get("message", msg)
            content = message.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        text_parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        events.append(AgentEvent(
                            type=EventType.TOOL_CALL,
                            data={"tool": block.get("name", ""), "input": block.get("input", {})},
                        ))
                    elif btype == "tool_result":
                        events.append(AgentEvent(
                            type=EventType.TOOL_RESULT,
                            data={"content": block.get("content", "")},
                        ))
                text = "".join(text_parts)
            else:
                text = str(content)
            if text.strip():
                events.insert(0, AgentEvent(type=EventType.MESSAGE_DELTA, data={"text": text}))
            return events

        if msg_type == "user":
            # user messages may contain tool_result blocks
            message = msg.get("message", msg)
            content = message.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        events.append(AgentEvent(
                            type=EventType.TOOL_RESULT,
                            data={"content": block.get("content", "")},
                        ))
            return events

        if msg_type == "result":
            text = msg.get("result", "")
            return [AgentEvent(type=EventType.COMPLETED, data={"text": str(text) if text else "", "reason": "result"})]

        # Top-level tool_use/tool_result (rare but possible)
        if msg_type == "tool_use":
            return [AgentEvent(
                type=EventType.TOOL_CALL,
                data={"tool": msg.get("name", ""), "input": msg.get("input", {})},
            )]
        if msg_type == "tool_result":
            return [AgentEvent(
                type=EventType.TOOL_RESULT,
                data={"content": msg.get("content", "")},
            )]

        if msg_type == "usage":
            return [AgentEvent(type=EventType.USAGE, data=msg)]
        if msg_type in ("error", "exception"):
            return [AgentEvent(type=EventType.ERROR, data={"error": msg.get("message", str(msg))})]

        return []

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

            # Drain stderr concurrently to prevent pipe buffer deadlock
            stderr_task = asyncio.create_task(self._drain_stderr(proc))

            async for line in proc.stdout:
                text = line.decode(errors="replace").strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                    for event in self._parse_events(msg):
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
