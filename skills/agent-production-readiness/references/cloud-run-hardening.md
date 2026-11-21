# Cloud Run 에이전트 프로덕션 가이드 (`cloud-run-hardening.md`, 체크리스트 v3)

> 파일 이름은 링크 호환을 위해 유지합니다. 항목 정의는 [`checklist-rubric.md`](checklist-rubric.md) §3-2 · [`catalog_v3.json`](../scripts/prod_ready/catalog_v3.json)이 기준입니다.
> 이 문서는 **에이전트 특성에서 나온 항목**(`CR-*`)을 Cloud Run에서 어떻게 충족하는지만 다룹니다. 헬스 프로브·IaC·알림·Non-Root·사설 연결 같은 일반 BP는 §3의 플랫폼 Skill을 함께 참조하세요.

---

## 1. 항목 요약

| ID | 항목 | 등급 | 충족 방법 (요약) |
| :--- | :--- | :--- | :--- |
| `CR-F1` | 관리형 영속 세션 저장 (VertexAiSessionService) | 조건부 (멀티턴 대화) | `get_fast_api_app(session_service_uri="agentengine://<AGENT_ENGINE_ID>")` 또는 `VertexAiSessionService` 직접 생성 |
| `CR-F2` | 관리형 장기 기억 저장 (VertexAiMemoryBankService) | 조건부 (장기 기억 필요) | `memory_service_uri="agentengine://<AGENT_ENGINE_ID>"` (`VertexAiMemoryBankService`) |
| `CR-D1` | CI/CD 파이프라인 (agents-cli) | 필수 | `agents-cli scaffold enhance . --cicd-runner google_cloud_build`로 `.cloudbuild/` 생성 (엔진은 생성하지 않음 · 안내만) |
| `CR-D2` | 평가 데이터셋 · 평가 게이트 | 필수 | `tests/eval/` 평가 데이터셋 + staging 단계에서 `agents-cli eval run` 점수를 기준값과 비교 — 미달이면 prod 승격 차단 |
| `CR-OB1` | Cloud Trace · 프롬프트 미기록 | 필수 | `otel_to_cloud=True` + GenAI 메시지 본문 수집 끔 |
| `CR-S1` | 인증된 호출만 허용 | 필수 | `allUsers`에 `roles/run.invoker` 부여 금지, 호출자는 전용 서비스 계정 |
| `CR-S2` | 에이전트 전용 신원 · 최소 권한 | 필수 | Agent Identity(Preview) 우선, 불가 시 전용 서비스 계정 + 최소 역할 |
| `CR-S3` | Agent Registry 등록 | 필수 · 🅿️ Preview | 에이전트(또는 MCP 서버) 유형으로 배포해 자동 등록 |
| `CR-S4` | Agent Gateway (외부 호출 통제) | 조건부 (MCP·외부 도구 호출) · 🅿️ Preview | Agent Gateway for Cloud Run으로 외부 호출에 접근 정책 적용 |
| `CR-S5` | AI 생성 코드 격리 (Cloud Run sandboxes) | 조건부 (AI 생성 코드 실행) · 🅿️ Preview | 서비스에 `--sandbox-launcher`(2세대 실행 환경) 사용, 코드 실행 도구는 Cloud Run sandbox에서 실행 (엔진은 점검·안내만) |
| `CR-S6` | 사용자 위임 인증 (Agent Identity auth manager) | 조건부 (사용자 대신 외부 도구 호출) · 🅿️ Preview | auth manager auth provider(3-legged OAuth)로 받은 사용자 위임 토큰을 도구·MCP 호출에 사용 — 정적 API 키·PAT 미사용 (`google-adk[agent-identity]`, 엔진은 점검·안내만) |

🅿️ Preview 항목은 점검·안내하지만 Production Ready 판정을 막지 않습니다.

---

## 2. 항목별 구현

### `CR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) · `CR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService)

Cloud Run 인스턴스는 언제든 재시작·스케일아웃되므로 `InMemorySessionService`로는 대화가 끊깁니다. Agent Platform 인스턴스(구 Agent Engine) ID로 관리형 영속 세션 저장·Memory Bank를 연결합니다.

```python
# app/fast_api_app.py
import os
from google.adk.cli.fast_api import get_fast_api_app

AGENT_ENGINE_ID = os.environ["AGENT_ENGINE_ID"]  # Agent Platform 인스턴스 ID

app = get_fast_api_app(
    agents_dir=os.path.dirname(os.path.abspath(__file__)),
    session_service_uri=f"agentengine://{AGENT_ENGINE_ID}",   # CR-F1
    memory_service_uri=f"agentengine://{AGENT_ENGINE_ID}",    # CR-F2 (장기 기억이 필요할 때만)
    otel_to_cloud=True,                                        # CR-OB1
    web=False,
)
```

- 참고: [ADK Sessions](https://google.github.io/adk-docs/sessions/session/) · [Agent Runtime Sessions](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/sessions/overview) · [Memory Bank](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory-bank/overview) · [ADK Memory](https://google.github.io/adk-docs/sessions/memory/)

### `CR-D1` CI/CD 파이프라인 (agents-cli) · `CR-D2` 평가 데이터셋 · 평가 게이트

CI/CD 파이프라인 파일은 **agents-cli가 생성**합니다. 엔진은 `CR-D1`(파이프라인 존재)을 점검·안내만 하고 파일을 만들지 않으며, `CR-D2`에서 평가 데이터셋·설정을 만들고 이미 있는 파이프라인 안에 **평가 게이트**를 넣습니다.

1. `CR-D1` — 파이프라인이 없으면(`.cloudbuild/` · `.github/workflows/` 없음 → `CR-D1` 🔴) 먼저 생성합니다. Agent Skill 워크플로는 사용자 승인 후 이 명령을 실행할 수 있습니다.
   ```bash
   agents-cli scaffold enhance . --cicd-runner google_cloud_build
   ```
2. `CR-D2` — `prod-ready apply --items CR-D2`가 `tests/eval/` 평가 자산과 staging 단계의 `agent-eval-gate`를 넣습니다. 파이프라인이 없으면 데이터셋·설정만 만들고 게이트는 건너뜁니다(`CR-D2` 🟡, "D1 먼저").
3. CI/CD 인프라(트리거·staging/prod 프로젝트) 생성인 `agents-cli infra cicd`는 운영 환경 구성이므로 **사용자가 직접** 수행합니다 (체크리스트 범위 밖).

`agents-cli` 스캐폴드의 `tests/eval/` 자산으로 평가를 실행하고, 평균 점수가 기준(`eval_threshold`, 기본 `3.5`) 미만이면 파이프라인을 실패시켜 다음 단계(prod) 배포를 막습니다. `agents-cli eval run`은 점수가 낮아도 종료 코드 `0`을 반환하므로 별도 게이트 단계가 필요합니다.

```yaml
# .cloudbuild/staging.yaml — trigger-prod-deployment 직전 (엔진 apply가 주입하는 단계의 요약)
- name: python:3.12-slim
  id: agent-eval-gate
  entrypoint: /bin/bash
  args:
    - "-c"
    - |
      set -e
      uvx --from google-agents-cli agents-cli eval run \
        --config tests/eval/eval_config.yaml \
        --dataset tests/eval/datasets/basic-dataset.json
      python3 -c '
      import glob, json, sys
      files = sorted(glob.glob("artifacts/grade_results/results_*.json"))
      if not files: sys.exit(1)
      data = json.load(open(files[-1]))
      scores = [m["score"] for case in data for m in (case.get("metrics") or []) if m.get("score") is not None]
      avg = sum(scores) / max(len(scores), 1)
      sys.exit(1 if avg < 3.5 else 0)   # eval_threshold 미달 → prod 승격 차단
      '
```

- 참고: [ADK Evaluate](https://google.github.io/adk-docs/evaluate/) · `google-agents-cli-eval` Skill

### `CR-OB1` Cloud Trace · 프롬프트 미기록

```hcl
env {
  name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"
  value = "NO_CONTENT"
}
env {
  name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"
  value = "false"
}
```

`get_fast_api_app(otel_to_cloud=True)`로 에이전트·도구 호출 스팬을 Cloud Trace에 내보냅니다 (`google-adk[gcp]`, 서비스 계정에 `roles/cloudtrace.agent`).

- 참고: [ADK Cloud Trace](https://google.github.io/adk-docs/observability/cloud-trace/) · [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

### `CR-S1` 인증된 호출만 허용

```bash
# 점검: allUsers / allAuthenticatedUsers 에 roles/run.invoker 가 없어야 함
gcloud run services get-iam-policy "${SERVICE}" --region="${REGION}" --format=json \
  | jq '.bindings[] | select(.role=="roles/run.invoker") | .members'

# 호출자(프런트엔드·다른 서비스)는 전용 서비스 계정에만 권한 부여
gcloud run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:frontend-sa@${PROJECT_ID}.iam.gserviceaccount.com" --role="roles/run.invoker"
```

Terraform에서는 `member = "allUsers"`인 `google_cloud_run_v2_service_iam_member`와 `invoker_iam_disabled = true`가 없어야 합니다. 외부 사용자에게 직접 노출해야 하면 Cloud Armor · IAP를 권고 사항으로 검토합니다.

- 참고: [Cloud Run 인증](https://cloud.google.com/run/docs/authenticating/overview)

### `CR-S2` 에이전트 전용 신원 · 최소 권한

- **Agent Identity (Preview)**: 서비스에 에이전트 고유 신원(SPIFFE 기반)을 부여합니다. Agent Identity와 `service_account`는 함께 지정할 수 없습니다.
- **대안**: 기본 Compute 서비스 계정 대신 전용 서비스 계정을 `template.service_account`에 지정합니다.
- 어느 쪽이든 필요한 최소 역할만 부여합니다 (예: `roles/aiplatform.user`, `roles/cloudtrace.agent`, 필요한 버킷에 한정한 `roles/storage.objectUser`). `roles/editor` · `roles/owner` 같은 기본 역할은 쓰지 않습니다.
- 참고: [Agent Identity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview) · [Cloud Run service identity](https://cloud.google.com/run/docs/securing/service-identity) · [IAM 보안 가이드](https://cloud.google.com/iam/docs/using-iam-securely)

### `CR-S3` Agent Registry 등록 (🅿️ Preview)

```hcl
resource "google_cloud_run_v2_service" "app" {
  provider        = google-beta
  # ...
  functional_type = "FUNCTIONAL_TYPE_AGENT"   # MCP 서버면 "FUNCTIONAL_TYPE_MCP_SERVER"
}
```

- `functional_type`은 한 번 지정하면 바꿀 수 없습니다 (변경하려면 서비스를 다시 만들어야 함).
- `agentregistry.googleapis.com` · `apphub.googleapis.com` API를 사용 설정합니다. 확인: `gcloud agent-registry agents list --location=${REGION}`
- MCP 서버·A2A 연동은 [`agent-registry-mcp-a2a.md`](agent-registry-mcp-a2a.md)를 참고합니다.
- 참고: [Cloud Run Agent Platform 기능](https://docs.cloud.google.com/run/docs/ai/agent-platform-features)

### `CR-S4` Agent Gateway (외부 호출 통제) (🅿️ Preview · 조건부)

에이전트가 MCP 서버·외부 API·다른 에이전트를 호출할 때만 적용합니다. Agent Gateway for Cloud Run으로 외부 호출에 접근 정책을 적용하고, Agent Registry에 등록된 대상과 Agent Identity를 기준으로 정책을 정의합니다. Agent Gateway를 쓸 수 없는 환경이면 Secure Web Proxy를 권고 사항으로 검토합니다.

- 참고: [Agent Gateway for Cloud Run](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/cloud-run-overview)

---

### `CR-S5` AI 생성 코드 격리 (Cloud Run sandboxes) (🅿️ Preview · 조건부)

AI가 만든 코드·셸을 실행할 때만 적용합니다. 에이전트 컨테이너에서 그대로 실행하면 서비스 계정 토큰이나 비밀값이 탈취될 수 있으므로, 서비스에 `--sandbox-launcher`(2세대 실행 환경)를 켜고 코드 실행 도구가 Cloud Run sandbox에서 실행하게 합니다. 샌드박스는 비밀값·환경변수·메타데이터 서버에 접근할 수 없고 외부 네트워크도 기본 차단됩니다.

- 엔진 판정: `--sandbox-launcher` / `sandbox_launcher` 구성 → 🟢, `BuiltInCodeExecutor`(모델 측 실행)만 → 🟡, 컨테이너에서 직접 실행(`subprocess`·`UnsafeLocalCodeExecutor` 등) → 🔴. Preview라 판정을 막지 않습니다.
- 자동 패치는 하지 않습니다 (Preview · 에이전트 코드 연결 필요). 위 설정을 직접 적용하세요.
- 참고: [Code execution in Cloud Run](https://docs.cloud.google.com/run/docs/code-execution)

---

### `CR-S6` 사용자 위임 인증 (Agent Identity auth manager) (🅿️ Preview · 조건부)

사용자 대신 외부 도구를 호출할 때(조건 `delegated`)만 적용합니다. 에이전트가 사용자를 대신해 Jira·GitHub 같은 외부 시스템이나 MCP 서버를 호출할 때 정적 API 키나 개인 토큰(PAT)을 쓰면 모든 사용자가 같은 권한으로 접근하고, 키가 유출되면 피해가 큽니다. Agent Identity auth manager에 auth provider(3-legged OAuth)를 구성하고, ADK가 도구·MCP 호출 시 사용자 위임 OAuth 토큰을 받아 인증 헤더를 붙이게 합니다. Cloud Run 서비스 계정(또는 Agent Identity)에 auth manager 사용 권한을 부여합니다.

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

아래 BP는 체크리스트에서 점검하지 않습니다. [google/skills · cloud](https://github.com/google/skills/tree/main/skills/cloud)의 플랫폼 Skill로 함께 점검하세요.

| 일반 BP | 참조 Skill |
| :--- | :--- |
| 헬스 프로브 (startup·liveness) · Secret Manager 버전 고정 · Non-Root 컨테이너 | [`cloud-run-basics`](https://github.com/google/skills/tree/main/skills/cloud/cloud-run-basics) |
| Direct VPC Egress (사설 연결) | [`cloud-run-basics`](https://github.com/google/skills/tree/main/skills/cloud/cloud-run-basics) · [`google-cloud-waf-security`](https://github.com/google/skills/tree/main/skills/cloud/google-cloud-waf-security) |
| Terraform IaC · 드리프트 방지 | [`google-cicd-terraform`](https://github.com/google/skills/tree/main/skills/cloud/google-cicd-terraform) |
| 지연·오류율 알림 정책 | [`cloud-run-alert-configuration`](https://github.com/google/skills/tree/main/skills/cloud/cloud-run-alert-configuration) |

## 4. 권고 사항 (체크리스트 아님)

시작 CPU 부스트 · 요청 타임아웃 · 최소 인스턴스 · 1세대 / 2세대 실행 환경 · Secure Web Proxy · Model Armor · Cloud Armor · IAP · 모델 용량 확보(Provisioned Throughput 등). 검토 시점은 [`checklist-rubric.md`](checklist-rubric.md) §6을 참고합니다. 엔진은 CPU·메모리·동시성·최소 인스턴스 같은 기존 값을 바꾸지 않습니다.
