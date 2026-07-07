"""AgentCore Runtime entrypoint for the case-drafting graph.

Deployed with the AgentCore starter toolkit (see docs/DEPLOYMENT.md). This
module is not imported by the library or the tests: it requires the aws
extra (bedrock-agentcore) and live Bedrock access.

The entrypoint drafts one case package per invocation and returns it. There
is no code path here, or anywhere upstream, that files a report or acts on
an account; the caller places the draft in the human review queue.
"""

from __future__ import annotations

from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel

from agents.graph import build_case_graph, case_task, extract_memo
from backend.pipeline import default_session
from schemas.detection import DetectionHit
from services.memory import CaseMemory
from services.observability import case_span, configure_tracing
from services.settings import Settings

settings = Settings()
configure_tracing(settings)
events, tick_sizes = default_session()
memory = CaseMemory(settings)
model = BedrockModel(model_id=settings.bedrock_model_id, region_name=settings.aws_region)

app = BedrockAgentCoreApp()


@app.entrypoint
def draft_case(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the case graph for one detection hit and return the memo draft."""
    hit = DetectionHit(**payload["hit"])
    case_id = payload["case_id"]
    with case_span(case_id, hit.pattern, hit.account_id):
        graph = build_case_graph(model, events, tick_sizes)
        result = graph(case_task(hit, case_id))
        memory.record_graph_result(case_id, result)
        return extract_memo(result, case_id).model_dump()


if __name__ == "__main__":
    app.run()
