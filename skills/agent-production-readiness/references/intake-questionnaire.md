# 사전 확인: 조건 판단 · 권고 사항 맞춤화 (`intake-questionnaire.md`, 체크리스트 v3)

> **기준**: [`catalog_v3.json`](../scripts/prod_ready/catalog_v3.json)의 `conditions` · `condition_rules` · `advisories` / 정의서 [`docs/checklist-v3-spec.md`](../../../docs/checklist-v3-spec.md) §3 · §5
> 판정 규칙은 [`checklist-rubric.md`](checklist-rubric.md) §2를 따릅니다.

체크리스트 v3에서 사용자에게 묻는 것은 **조건부 항목이 해당하는지**뿐입니다. 필수 항목은 항상 점검하고, Preview 항목은 점검·표시하되 판정을 막지 않습니다. 따라서 "풀 프로덕션 / PoC" 같은 프리셋 선택은 더 이상 쓰지 않습니다.

---

## 1. 원칙 — 코드에서 먼저 판단하고, 판단하지 못한 것만 묻는다

```mermaid
flowchart LR
  A["1. 대상 환경 판별 (cloud_run · agent_runtime · gke)"] --> B["2. 조건 6개 코드 자동 판단"]
  B --> C{"판단 불가 조건?"}
  C -- "없음" --> S["scan 실행"]
  C -- "있음" --> Q["해당 조건만 질문 (최대 6개)"]
  Q --> S
  S --> F["3. 권고 사항 후속 질문 (선택)"]
```

1. **대상 환경**: 사용자가 말하지 않았으면 작업 공간에서 판별합니다 (`google_cloud_run_v2_service` → `cloud_run`, `google_vertex_ai_reasoning_engine` / `agent_engine_app.py` → `agent_runtime`, `kubernetes_deployment_v1` / `deployment/k8s/` → `gke`). 엔진의 `--target auto`도 같은 판별을 합니다.
2. **조건 자동 판단**: 아래 §2의 신호를 코드·설정에서 찾습니다. 신호가 분명하면 묻지 않고 결과만 한 줄로 알려 줍니다 (예: `멀티턴 대화: 해당 (VertexAiSessionService 사용 감지)`).
3. **질문은 판단하지 못한 조건만**, 한 번에 묶어서 짧게 묻습니다. 사용자가 이미 말한 내용은 다시 묻지 않습니다.

---

## 2. 조건과 적용 항목

| 조건 (`conditions` 키) | 판단 기준 (질문) | 적용 항목 (v3 ID) | 코드 자동 판단 신호 |
| :--- | :--- | :--- | :--- |
| **멀티턴 대화** (`conv`) | 이전 대화를 이어가야 하는가 | 관리형 영속 세션 저장 (VertexAiSessionService) — `AR-F1` · `CR-F1` · `GKE-F1` | `SessionService` 사용, `session_id`를 요청 간 전달, 채팅 UI·`/run_sse` 대화 엔드포인트, `get_fast_api_app(session_service_uri=...)` |
| **장기 기억 필요** (`memory`) | 세션을 넘어 사용자를 기억해야 하는가 | 관리형 장기 기억 저장 (VertexAiMemoryBankService) — `AR-F2` · `CR-F2` · `GKE-F2` | `MemoryService` · `VertexAiMemoryBankService`, `load_memory` / `PreloadMemoryTool`, `add_session_to_memory`, 사용자 프로필·선호 저장 로직 |
| **AI 생성 코드 실행** (`code`) | AI가 만든 코드·셸을 실행하는가 | Code Execution — `AR-S4` / Cloud Run sandboxes (Preview) — `CR-S5` / Agent Substrate on GKE — `GKE-S2` (AI 생성 코드 격리) | `BuiltInCodeExecutor` · `UnsafeLocalCodeExecutor` · `ContainerCodeExecutor`, `subprocess` / `exec(` / `eval(`를 도구로 노출, 셸·브라우저·computer-use 도구 |
| **자주 일시정지·재개하는 상태 유지형 에이전트** (`idle`) | 사용자 입력·도구 응답을 기다리는 시간이 대부분인가 | 에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox) — `GKE-O2` (GKE 전용) | 코드로 판단 불가 → Agent Substrate(`ate.dev/v1alpha1` `WorkerPool` / `ActorTemplate`)를 이미 쓰면 해당, 아니면 **사용자에게 질문**. 해당이면 `--conditions idle=true` |
| **MCP·외부 도구 호출** (`tools`) | MCP 서버·외부 API·다른 에이전트를 호출하는가 | Agent Gateway (외부 호출 통제) — `CR-S4` · `AR-S3` / Agent Gateway for GKE (외부 호출 통제) — `GKE-S4` | `McpToolset` · `StreamableHTTPConnectionParams` · `SseConnectionParams`, `RemoteA2aAgent`, `AgentRegistry.get_mcp_toolset()`, `OpenAPIToolset`, 외부 URL로의 `httpx`/`requests` 호출 도구 |
| **사용자 대신 외부 도구 호출** (`delegated`) | 사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하는가 | 사용자 위임 인증 (Agent Identity auth manager) — `AR-S5` · `CR-S6` · `GKE-S5` (Preview · 전 환경) | auth manager 사용(`google.adk.integrations.agent_identity`, `GcpAuthProvider` / `GcpAuthProviderScheme`, `…/authProviders/…`), 인증 헤더(`headers=` · `Authorization` · `auth_scheme` · `auth_credential`)를 붙인 MCP·OpenAPI·서드파티 API 도구, `*_TOKEN` · `*_API_KEY` · `GITHUB_PAT` 같은 환경변수 (Gemini 키 제외). 감지되지 않으면 **사용자에게 질문**하고, 해당이면 `--conditions delegated=true` |

- 조건이 **해당하지 않으면** 그 항목은 `N/A`로 표시하고 판정에서 제외합니다.
- Agent Gateway 항목(`AR-S3` · `CR-S4` · `GKE-S4`)과 사용자 위임 인증 항목(`AR-S5` · `CR-S6` · `GKE-S5`)은 **Preview**라 조건이 해당해도 판정을 막지 않습니다.
- `GKE-O2`는 조건을 코드로 판단할 수 없어 기본은 `N/A`입니다. 사용자가 "해당"이라고 답하면 `scan`/`report`에 `--conditions idle=true`를 붙이고, 적용은 `--items GKE-O2`(명시 선택)로 합니다.
- AI 생성 코드 격리는 환경별 항목입니다: Agent Runtime `AR-S4`(Code Execution) · Cloud Run `CR-S5`(Cloud Run sandboxes, Preview) · GKE `GKE-S2`(Agent Substrate). `AR-S4` · `CR-S5`는 점검·안내만 하고 자동 패치하지 않습니다.
- 사용자 위임 인증(`AR-S5` · `CR-S6` · `GKE-S5`)도 점검·안내만 합니다: 🟢 `google-adk[agent-identity]` extra + auth provider 연결 · 🔴 정적 API 키·PAT를 도구 헤더에 전달 · 🟡 그 외. auth manager에 auth provider(3-legged OAuth)를 만들고 ADK 도구·MCP에 연결하도록 안내합니다 ([Agent Identity auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth with auth manager](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)).

> [!NOTE]
> 엔진이 조건을 자동 판단하므로 대부분은 질문 없이 `scan`을 실행할 수 있습니다. 사용자가 "해당 없음"이라고 답한 조건부 항목은 `--items`에서 빼고 실행합니다 (예: Cloud Run에서 멀티턴 대화가 아니면 `CR-F1`을 제외).

---

## 3. 질문 템플릿 (판단하지 못한 조건만)

### Mode A — 선택형 도구(`ask_question` / `ask_user`)가 있을 때

판단하지 못한 조건마다 질문 하나, 선택지는 `예` / `아니오` 두 개로 묶어서 한 번에 묻습니다.

- `이 에이전트는 이전 대화를 이어가야 하나요? (멀티턴 대화)`
- `세션이 끝난 뒤에도 사용자의 선호·사실을 기억해야 하나요? (장기 기억)`
- `AI가 만든 코드나 셸 명령을 실행하나요?`
- `사용자 입력·도구 응답을 기다리는 시간이 대부분인 대화형 에이전트인가요? (유휴 일시정지 · 복원, GKE만 해당)`
- `MCP 서버·외부 API·다른 에이전트를 호출하나요?`
- `사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하나요? (사용자 위임 인증)`

### Mode B — 텍스트 대체 (선택형 도구가 없을 때)

```text
코드에서 판단하지 못한 조건만 확인합니다 (예/아니오):
  1. 이전 대화를 이어가야 하나요? (멀티턴 대화)
  2. 세션을 넘어 사용자를 기억해야 하나요? (장기 기억)
  3. AI가 만든 코드·셸을 실행하나요?
  4. 사용자 입력·도구 응답을 기다리는 시간이 대부분인가요? (GKE만 해당 · 유휴 일시정지 · 복원)
  5. MCP 서버·외부 API·다른 에이전트를 호출하나요?
  6. 사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하나요? (사용자 위임 인증)
```

목록에는 판단하지 못한 조건만 남기고 번호를 다시 매깁니다.

---

## 4. 권고 사항 후속 질문 (선택 · 판정과 무관)

`scan` 결과를 보여 준 뒤, 사용자에게 관련이 있어 보일 때만 짧게 덧붙입니다. 답에 따라 [`checklist-rubric.md`](checklist-rubric.md) §6의 권고 사항을 골라 안내합니다. 체크리스트 판정에는 영향을 주지 않습니다.

| 후속 질문 | 안내할 권고 사항 (대상) |
| :--- | :--- |
| 기동이 느린 런타임인가요? 콜드스타트가 문제인가요? | 시작 CPU 부스트 · 1세대 / 2세대 실행 환경 (Cloud Run) |
| 한 턴의 최대 응답 시간과 지연 민감도는? | 요청 타임아웃 · 최소 인스턴스 (Cloud Run) / 최소/최대 인스턴스 · 동시성 (Agent Runtime) / HPA · 리소스 요청/한도 (GKE) |
| 외부 호출을 통제해야 하는데 Agent Gateway를 쓸 수 없나요? | Secure Web Proxy (이그레스 프록시) (전 환경, 비용 발생) |
| 외부 사용자 대상이거나 민감정보를 다루나요? | Model Armor (프롬프트·응답 검사) (전 환경, 비용 발생) |
| 에이전트를 인터넷에 직접 노출하나요? | Cloud Armor · IAP (외부 노출 보호) (Cloud Run · GKE, 비용 발생) |
| 데이터 레지던시로 리전을 고정하나요? SLA가 필요한가요? | 모델 용량 확보 (Provisioned Throughput 등) (전 환경) |

---

## 5. 선택 `--params` (diff / apply 단계)

엔진 버전에 따라 키가 바뀔 수 있으니 실제 값은 `python3 <SKILL_DIR>/scripts/prod_ready.py diff --help`와 diff 결과로 확인합니다.

| 키 | 기본값 | 대상 | 관련 항목 |
| :--- | :--- | :---: | :--- |
| `agent_engine_id` | `""` | `cloud_run`, `gke` | Agent Platform 인스턴스 ID — `VertexAiSessionService` · Memory Bank 연결 (`CR-F1`·`CR-F2`·`GKE-F1`·`GKE-F2`) |
| `eval_threshold` | `3.5` | 전체 | 평가 게이트 기준 점수 (`CR-D2`·`AR-D2`·`GKE-D2`) |
| `gke_termination_grace_seconds` | `120` | `gke` | `terminationGracePeriodSeconds` — 최대 응답 시간 이상 (`GKE-O1`) |
| `gke_execution_pattern` | `"standard_pods"` | `gke` | `agent_substrate`(Agent Substrate on GKE) 또는 `agent_sandbox`(상시 실행 · 사이드카 · Arm · Autopilot) (`GKE-S2`) |
| `agent_gateway_enforcement_mode` | `"DRY_RUN"` | `agent_runtime` | Agent Gateway 정책을 시험 모드로 검증한 뒤 `ENFORCED`로 전환 (`AR-S3`, Preview) |
