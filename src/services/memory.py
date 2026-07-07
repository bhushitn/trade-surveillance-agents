"""Case memory: one session per case, one branch per agent.

Against AWS this uses AgentCore Memory's CreateEvent with an explicit branch
per agent, so each specialist's working record stays isolated while sharing
the case session (actor = the pipeline, session = the case). Locally it
appends to JSONL files with the same layout so the backend and tests exercise
identical code paths.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from services.settings import Settings


class CaseMemory:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any = None
        if settings.memory_enabled:
            from bedrock_agentcore.memory import MemoryClient

            self._client = MemoryClient(region_name=settings.aws_region)

    def record(self, case_id: str, agent_name: str, role: str, text: str) -> None:
        """Append one message to the agent's branch of the case session."""
        if self._client is not None:
            self._client.create_event(
                memory_id=self._settings.memory_id,
                actor_id="surveillance-pipeline",
                session_id=case_id,
                messages=[(text, role)],
                branch={"name": agent_name, "rootEventId": None},
            )
            return
        path = Path(self._settings.local_state_dir) / "memory" / case_id
        path.mkdir(parents=True, exist_ok=True)
        with (path / f"{agent_name}.jsonl").open("a") as f:
            f.write(json.dumps({"ts": time.time(), "role": role, "text": text}) + "\n")

    def record_graph_result(self, case_id: str, result: Any) -> None:
        """Persist each node's final output after a case graph run."""
        for node_id, node_result in result.results.items():
            self.record(case_id, node_id, "assistant", str(node_result.result))

    def branch_history(self, case_id: str, agent_name: str) -> list[dict[str, Any]]:
        """Read one agent's branch back, oldest first."""
        if self._client is not None:
            events = self._client.list_events(
                memory_id=self._settings.memory_id,
                actor_id="surveillance-pipeline",
                session_id=case_id,
                branch_name=agent_name,
            )
            return list(events)
        path = Path(self._settings.local_state_dir) / "memory" / case_id / f"{agent_name}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]
