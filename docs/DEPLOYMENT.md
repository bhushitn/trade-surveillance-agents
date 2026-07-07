# Deployment guide: connecting a real AWS account

The repository runs fully offline by default. Every AgentCore service wrapper
in `src/services/` reads its configuration from the environment and falls back
to a documented local behavior when unset:

| Service       | Env variable                  | Local fallback                                   |
|---------------|-------------------------------|--------------------------------------------------|
| Bedrock model | `BEDROCK_MODEL_ID`            | No model; offline template memos                 |
| Memory        | `AGENTCORE_MEMORY_ID`         | JSONL files under `.local_state/memory/`         |
| Gateway       | `AGENTCORE_GATEWAY_URL`       | In-process mock APIs (`src/mockapis/`)           |
| Identity      | `AGENTCORE_IDENTITY_PROVIDER` | No bearer token attached                         |
| Observability | `OTEL_EXPORTER_OTLP_ENDPOINT` | Tracing is a no-op                               |

To run against a real account, complete the steps below and populate `.env`
from `.env.example`. Each step is independent; you can enable one service at
a time and leave the rest on their local fallbacks.

## Prerequisites

- An AWS account with Amazon Bedrock and Bedrock AgentCore available in your
  region (`us-east-1` assumed below).
- Credentials on the machine that will run the pipeline. Use your normal
  credential tooling (SSO, instance role, or access keys in `.env`).
- `pip install -e ".[serve,aws,dev]"` to pull in `bedrock-agentcore` and the
  starter toolkit dependencies.

## 1. Bedrock model access

Enable access to the Claude model family in the Bedrock console, then set:

```
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=claude-sonnet-4-6
```

With a model configured, `build_cases` runs the four-agent graph per alert
instead of the offline template, and cases appear in the review queue with
`generated_by=agent-graph`.

## 2. AgentCore Memory

Create one memory store; the pipeline records each agent's transcript on its
own branch within a per-case session:

```python
from bedrock_agentcore.memory import MemoryClient

client = MemoryClient(region_name="us-east-1")
memory = client.create_memory(name="trade-surveillance-cases")
print(memory["id"])  # -> AGENTCORE_MEMORY_ID
```

## 3. Mock APIs and Gateway

The Gateway fronts the two mock APIs as MCP tools. Host them anywhere that
serves HTTP (an API Gateway + Lambda pair is the smallest footprint), with
routes matching `deploy/gateway_targets.json`:

- `GET /accounts/{account_id}/profile` and `GET /accounts/{account_id}/alerts`
  backed by `src/mockapis/account_service.py`
- `GET /instruments/{symbol}` and `GET /instruments/{symbol}/related`
  backed by `src/mockapis/market_reference.py`

Then create the Gateway and register both targets:

```python
import json
from bedrock_agentcore_starter_toolkit.operations.gateway import GatewayClient

client = GatewayClient(region_name="us-east-1")
gateway = client.create_mcp_gateway(name="trade-surveillance-gateway")
spec = json.loads(open("deploy/gateway_targets.json").read())
for target in spec["targets"]:
    client.create_mcp_gateway_target(
        gateway=gateway,
        name=target["name"],
        target_type="openApiSchema",
        target_payload={"inlinePayload": json.dumps(target["openapi"])},
    )
print(gateway["gatewayUrl"])  # -> AGENTCORE_GATEWAY_URL
```

Replace the `REPLACE_ME` server URLs in `deploy/gateway_targets.json` with
your hosted API base URL before registering.

## 4. AgentCore Identity

Create an OAuth2 client-credentials provider so agents can obtain a bearer
token for the Gateway (the console flow under AgentCore > Identity works as
well):

```python
from bedrock_agentcore.services.identity import IdentityClient

client = IdentityClient(region="us-east-1")
provider = client.create_oauth2_credential_provider(
    name="trade-surveillance-m2m",
    # client id/secret from your IdP (Cognito user pool client, for example)
)
```

Set `AGENTCORE_IDENTITY_PROVIDER=trade-surveillance-m2m`. The wrapper in
`src/services/identity.py` exchanges it for a token with scope
`gateway:invoke` at call time.

## 5. AgentCore Runtime

`deploy/agent_runtime.py` wraps the case graph in a `BedrockAgentCoreApp`
entrypoint. Deploy it with the starter toolkit:

```
agentcore configure --entrypoint deploy/agent_runtime.py --region us-east-1
agentcore launch
```

Invoke with one detection hit per request:

```
agentcore invoke '{"case_id": "CASE-0001", "hit": {...}}'
```

The response is the drafted `CaseMemo` as JSON. The Runtime never files
anything; route its output into the review queue.

## 6. Observability

Inside AgentCore Runtime the ADOT sidecar sets `OTEL_EXPORTER_OTLP_ENDPOINT`
automatically and spans land in CloudWatch under the GenAI Observability
pages. To trace local runs, point the variable at any OTLP collector. Every
case is wrapped in a span carrying `case.id`, `case.pattern`, and
`case.account_id`, so one case can be followed across all four agents.

## 7. Verify

```
cp .env.example .env   # fill in the values you created above
docker compose up --build
```

Open http://localhost:8000 and confirm the queue shows cases with
`generated_by=agent-graph`. `pytest -q` still passes in this configuration
because the tests pin their own local settings.
