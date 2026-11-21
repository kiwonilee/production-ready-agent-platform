# Agent Runtime 에이전트 프로덕션 가이드 (`agent-runtime-hardening.md`, 체크리스트 v3)

> 파일 이름은 링크 호환을 위해 유지합니다. 항목 정의는 [`checklist-rubric.md`](checklist-rubric.md) §3-1 · [`catalog_v3.json`](../scripts/prod_ready/catalog_v3.json)이 기준입니다.
> Agent Runtime(`google_vertex_ai_reasoning_engine`)은 컨테이너 헬스 관리, 관리형 영속 세션 저장, 트레이스 수집, IAM 인증을 플랫폼이 기본 제공합니다. 체크리스트는 **그 기본값을 코드·설정이 깨지 않는지**와 에이전트 고유 거버넌스만 점검합니다.

---

## 1. 항목 요약

| ID | 항목 | 등급 | 충족 방법 (요약) |
| :--- | :--- | :--- | :--- |
| `AR-F1` | 관리형 영속 세션 저장 (VertexAiSessionService) | 조건부 (멀티턴 대화) | 기본 연결되는 `VertexAiSessionService`를 그대로 사용 — 코드에서 `InMemorySessionService`로 덮어쓰지 않음 |
| `AR-F2` | 관리형 장기 기억 저장 (VertexAiMemoryBankService) | 조건부 (장기 기억 필요) | `VertexAiMemoryBankService` 연결 (`PreloadMemoryTool` / `load_memory`, 세션 종료 시 `add_session_to_memory`) |
| `AR-D1` | CI/CD 파이프라인 (agents-cli) | 필수 | `agents-cli scaffold enhance . --cicd-runner google_cloud_build`로 `.cloudbuild/` 생성 (엔진은 생성하지 않음 · 안내만) |
| `AR-D2` | 평가 데이터셋 · 평가 게이트 | 필수 | `tests/eval/` 평가 데이터셋 + staging 단계에서 `agents-cli eval run` 점수를 기준값과 비교 — 미달이면 prod 승격 차단 |
| `AR-OB1` | 프롬프트 미기록 | 필수 | GenAI 메시지 본문 수집 끔 (트레이스 수집 자체는 플랫폼 기본) |
| `AR-S1` | 에이전트 전용 신원 · 최소 권한 | 필수 | Agent Identity(Preview) 우선, 불가 시 전용 서비스 계정 + 최소 역할 |
| `AR-S2` | Agent Registry 등록 | 필수 · 🅿️ Preview | Agent Registry에 에이전트 등록 |
| `AR-S3` | Agent Gateway (외부 호출 통제) | 조건부 (MCP·외부 도구 호출) · 🅿️ Preview | Agent Gateway 연결, 정책은 시험 모드(`DRY_RUN`) 검증 후 강제 모드로 전환 |
| `AR-S4` | AI 생성 코드 격리 (Code Execution) | 조건부 (AI 생성 코드 실행) | 코드 실행을 Agent Platform Code Execution(관리형 샌드박스)에 위임 (예: ADK `AgentEngineSandboxCodeExecutor`, 엔진은 점검·안내만) |
| `AR-S5` | 사용자 위임 인증 (Agent Identity auth manager) | 조건부 (사용자 대신 외부 도구 호출) · 🅿️ Preview | auth manager auth provider(3-legged OAuth)로 받은 사용자 위임 토큰을 도구·MCP 호출에 사용 — 정적 API 키·PAT 미사용 (`google-adk[agent-identity]`, 엔진은 점검·안내만) |

🅿️ Preview 항목은 점검·안내하지만 Production Ready 판정을 막지 않습니다.

---

## 2. 항목별 구현

### `AR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) · `AR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService)

```python
# ❌ AR-F1 위반: 관리형 영속 세션 저장을 InMemory로 덮어씀
from google.adk.sessions import InMemorySessionService
app = AdkApp(agent=root_agent, session_service_builder=lambda: InMemorySessionService())

# ✅ 기본값 유지 (AR-F1) + Memory Bank 연결 (AR-F2, 필요할 때만)
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
root_agent = Agent(name="root_agent", model=model, tools=[PreloadMemoryTool()], instruction="...")
app = AdkApp(agent=root_agent)   # 배포 후 VertexAiSessionService · VertexAiMemoryBankService 기본 연결
```

- 참고: [Agent Runtime Sessions](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/sessions/overview) · [Memory Bank](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory-bank/overview) · [ADK Memory](https://google.github.io/adk-docs/sessions/memory/)

### `AR-D1` CI/CD 파이프라인 (agents-cli) · `AR-D2` 평가 데이터셋 · 평가 게이트

`AR-D1`: 파이프라인이 없으면 먼저 `agents-cli scaffold enhance . --cicd-runner google_cloud_build`로 생성합니다(엔진은 안내만). `AR-D2`: `tests/eval/` 데이터셋을 두고 그 `.cloudbuild/staging.yaml`의 prod 승격 직전에 `agent-eval-gate` 단계를 둡니다 (구성은 `CR-D2`와 동일, 기준 점수 `eval_threshold` 기본 `3.5`). 엔진은 파이프라인 파일을 만들지 않으며, `agents-cli infra cicd`(CI/CD 인프라)는 사용자가 직접 수행합니다.

- 참고: [ADK Evaluate](https://google.github.io/adk-docs/evaluate/) · `google-agents-cli-eval` Skill

### `AR-OB1` 프롬프트 미기록

```hcl
# google_vertex_ai_reasoning_engine.app — spec.deployment_spec.env
env {
  name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
  value = "NO_CONTENT"
}
env {
  name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"
  value = "false"
}
```

트레이스 수집(`GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY`)은 플랫폼 기본이므로 별도 항목으로 점검하지 않습니다.

- 참고: [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

### `AR-S1` 에이전트 전용 신원 · 최소 권한

- **Agent Identity (Preview)**: `spec.identity_type = "AGENT_IDENTITY"` (또는 `agents-cli deploy --agent-identity`). Agent Identity와 `service_account`는 함께 지정할 수 없습니다.
- **대안**: 기본 서비스 에이전트 권한에 의존하지 않고 전용 서비스 계정(`spec.service_account`, `agents-cli deploy --service-account`)을 지정합니다.
- 어느 쪽이든 필요한 최소 역할만 부여합니다. 접근 권한 점검은 [`agent-platform-access-audit`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-access-audit) Skill을 함께 사용합니다.
- 참고: [Agent Identity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview) · [IAM 보안 가이드](https://cloud.google.com/iam/docs/using-iam-securely)

### `AR-S2` Agent Registry 등록 (🅿️ Preview)

`agentregistry.googleapis.com` · `apphub.googleapis.com`을 사용 설정하면 Agent Runtime 에이전트가 Agent Registry에 등록됩니다. 확인: `gcloud agent-registry agents list --location=${REGION}`. 등록·조회 절차는 [`agent-platform-agent-registry`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-agent-registry) Skill과 [`agent-registry-mcp-a2a.md`](agent-registry-mcp-a2a.md)를 참고합니다.

### `AR-S3` Agent Gateway (외부 호출 통제) (🅿️ Preview · 조건부)

에이전트가 MCP 서버·외부 API·다른 에이전트를 호출할 때만 적용합니다.

- `google_network_services_agent_gateway`(`AGENT_TO_ANYWHERE`)를 만들고 Reasoning Engine의 `agent_gateway_config`로 연결합니다. 이 블록은 최신 `google-beta` 프로바이더가 필요하며, 구버전이면 `agents-cli deploy` 또는 Python SDK로 연결합니다.
- 정책(`AuthzExtension` · `AuthzPolicy`)은 **`DRY_RUN`으로 먼저 배포**해 Cloud Logging에서 허용/거부 결정을 검토한 뒤 `ENFORCED`로 전환합니다. 바로 강제하면 등록되지 않은 플랫폼 API·MCP 호출이 HTTP 498로 차단될 수 있습니다.
- Agent Gateway는 사설·자체 서명 CA를 신뢰하지 않으므로 업스트림 MCP/A2A 엔드포인트는 공인 CA 인증서를 사용합니다. 도구 단위 정책(CEL) 예시는 [`agent-registry-mcp-a2a.md`](agent-registry-mcp-a2a.md) §5를 참고합니다.
- 참고: [Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview)

---

### `AR-S4` AI 생성 코드 격리 (Code Execution) — 조건부

AI가 만든 코드·셸을 실행할 때만 적용합니다. Agent Runtime 컨테이너에서 그대로 실행하면 에이전트 신원·자격증명이 탈취될 수 있으므로, 코드 실행을 Agent Platform Code Execution(관리형 샌드박스)에 맡깁니다. 샌드박스는 1초 이내에 생성되고 실행 상태를 TTL 동안 유지합니다.

- 엔진 판정: Code Execution 사용(`AgentEngineSandboxCodeExecutor` · `execute_code` 등) → 🟢, `BuiltInCodeExecutor`(모델 측 실행)만 → 🟡, 컨테이너에서 직접 실행 → 🔴.
- 에이전트 코드 연결이 필요해 자동 패치는 하지 않습니다.
- 참고: [Agent Platform Code Execution](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview)

---

### `AR-S5` 사용자 위임 인증 (Agent Identity auth manager) (🅿️ Preview · 조건부)

사용자 대신 외부 도구를 호출할 때(조건 `delegated`)만 적용합니다. 에이전트가 사용자를 대신해 Jira·GitHub 같은 외부 시스템이나 MCP 서버를 호출할 때 정적 API 키나 개인 토큰(PAT)을 쓰면 모든 사용자가 같은 권한으로 접근하고, 키가 유출되면 피해가 큽니다. Agent Identity auth manager에 auth provider(3-legged OAuth)를 구성하고, ADK가 도구·MCP 호출 시 사용자 위임 OAuth 토큰을 받아 인증 헤더를 붙이게 합니다. Agent Runtime에서는 `set_up()` 등 초기화 단계에서 auth provider를 등록합니다.

```toml
# pyproject.toml
dependencies = ["google-adk[agent-identity]"]
```

```python
# ❌ 위반: 정적 PAT를 MCP 헤더에 직접 전달 (모든 사용자가 같은 권한)
McpToolset(connection_params=StreamableHTTPConnectionParams(
    url="https://api.githubcopilot.com/mcp/",
    headers={"Authorization": f"Bearer {os.environ['GITHUB_PAT']}"},
))

# ✅ 권장: auth manager의 auth provider로 사용자 위임 토큰 사용
from google.adk.auth.credential_manager import CredentialManager
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme

CredentialManager.register_auth_provider(GcpAuthProvider())
McpToolset(
    connection_params=StreamableHTTPConnectionParams(url="https://api.githubcopilot.com/mcp/"),
    auth_scheme=GcpAuthProviderScheme(name="projects/<PROJECT>/locations/<LOCATION>/authProviders/<PROVIDER>"),
)
```

- 조건 판단: auth manager 사용, 인증 헤더를 붙인 MCP·OpenAPI·서드파티 API 도구, `*_TOKEN` · `*_API_KEY` · `GITHUB_PAT` 같은 환경변수(Gemini 키 제외)가 감지되면 해당. 감지되지 않으면 `N/A` — 사용자 계정으로 외부 시스템을 호출한다면 `--conditions delegated=true`.
- 엔진 판정: `google-adk[agent-identity]` extra + auth provider 연결(`GcpAuthProvider` / `GcpAuthProviderScheme`) → 🟢, 정적 API 키·PAT를 도구 헤더에 전달 → 🔴, 그 외 → 🟡. Preview라 판정을 막지 않습니다.
- 자동 패치는 하지 않습니다 (Preview · 에이전트 코드 연결 필요). 정적 API 키·PAT는 코드와 환경변수에 두지 않습니다.
- 참고: [Agent Identity auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth with auth manager](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)

## 3. 함께 참조할 플랫폼 Skill (일반 BP · 적극 권장)

| 일반 BP | 참조 Skill |
| :--- | :--- |
| 배포 · 엔드포인트 관리 | [`agent-platform-deploy`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-deploy) |
| Agent Registry 운영 | [`agent-platform-agent-registry`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-agent-registry) |
| 접근 권한 감사 | [`agent-platform-access-audit`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-access-audit) |
| 지연·오류율 알림 정책 | [`agent-platform-alert-configuration`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-alert-configuration) |
| Terraform IaC · 드리프트 방지 | [`google-cicd-terraform`](https://github.com/google/skills/tree/main/skills/cloud/google-cicd-terraform) |
| Non-Root 컨테이너 · PSC Interface (사설 연결) | [`cloud-run-basics`](https://github.com/google/skills/tree/main/skills/cloud/cloud-run-basics) · [`google-cloud-waf-security`](https://github.com/google/skills/tree/main/skills/cloud/google-cloud-waf-security) |

## 4. 권고 사항 (체크리스트 아님)

최소/최대 인스턴스 · 동시성 · Secure Web Proxy · Model Armor · 모델 용량 확보(Provisioned Throughput 등). 검토 시점은 [`checklist-rubric.md`](checklist-rubric.md) §6을 참고합니다.
