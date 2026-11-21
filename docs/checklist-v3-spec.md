# AI Agent Production-Readiness 체크리스트 v3 — 내용 정의서

> **상태**: 확정 · `/checklist` · Agent Skill(엔진·문서) · `/workbench` · `/lab` · `/deck` 반영 완료
> **목적**: 각 항목이 무엇인지 명확히 정리하고, 이후 Agent Skill(`agent-production-readiness`)을 이 문서 기준으로 바꾼다.
> 이 문서의 1~2장 표는 `/checklist` 페이지와 같은 데이터에서 생성된다. 3장(조건부 적용 기준)과 5장(권고 사항)은 **Agent Skill 전용**이며 `/checklist` 페이지에는 표시하지 않는다.

---

## 0. 항목 선정 기준

체크리스트는 "할 수 있는 모든 설정"이 아니다. 아래를 **모두** 만족하는 것만 넣는다.

1. **에이전트 특성에서 나온다** — "에이전트의 X 특성 때문에 Y를 해야 한다"로 설명할 수 있어야 한다. 모든 컨테이너 서비스에 해당하는 일반 플랫폼 BP는 넣지 않고 플랫폼 Skill에 맡긴다 (4-1)
2. **근거가 명확하다** — Google Cloud·Kubernetes 공식 문서, Google SRE 등에 권장 사항으로 명시
3. **범용적이다** — 대부분의 에이전트에 그대로 적용. 일부에만 해당하면 **조건부**로 두고 조건을 명시
4. **구체적이다** — 무엇을 쓰는지 GCP 서비스·설정 이름으로 명시 (예: "외부 세션 저장소"가 아니라 `VertexAiSessionService`)
5. **판정할 수 있다** — 했는지 안 했는지 코드·설정에서 확인 가능
6. **선택 사항이 아니다** — 아래는 체크하지 않고 5장 권고 사항으로 안내만 한다
   - 값 (인스턴스 수, 타임아웃, 리소스 크기)
   - 아키텍처 선택 (실행 패턴, 세대 선택)
   - **비용이 드는 선택형 제품** (Model Armor, Cloud Armor, Secure Web Proxy 등)
   - **앱 설계 영역**이라 인프라·설정으로 판정할 수 없는 것 (예: 위험 작업 사람 승인)
   - 서로 충돌하는 방식은 하나의 항목으로 정리한다 (예: 전용 서비스 계정 / Agent Identity → "에이전트 전용 신원" 한 항목)

**Preview 기능**은 제외하지 않는다. 위 기준을 만족하면 체크리스트에 넣고 `Preview`로 표시하며, 점검·안내는 하되 Production Ready 판정을 막지 않는다. 정식 출시되면 표시를 떼고 일반 등급으로 전환한다.

---

## 1. 한눈에 보기

환경마다 항목을 따로 묶는다. 세 환경에 공통 영역을 두지 않는다. 순서는 Agent Runtime → Cloud Run → GKE (Fully Managed → 직접 구성·높은 제어권).

**🛡️ Agent Runtime**

- Features: `AR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `AR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️
- Delivery & Eval: `AR-D1` CI/CD 파이프라인 (agents-cli) · `AR-D2` 평가 데이터셋 · 평가 게이트
- Observability: `AR-OB1` 프롬프트 미기록
- Security: `AR-S1` 에이전트 전용 신원 · 최소 권한 · `AR-S2` Agent Registry 등록 🅿️ · `AR-S3` Agent Gateway (외부 호출 통제) ⚙️ 🅿️ · `AR-S4` AI 생성 코드 격리 (Code Execution) ⚙️ · `AR-S5` 사용자 위임 인증 (Agent Identity auth manager) ⚙️ 🅿️

**⚡ Cloud Run**

- Features: `CR-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `CR-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️
- Delivery & Eval: `CR-D1` CI/CD 파이프라인 (agents-cli) · `CR-D2` 평가 데이터셋 · 평가 게이트
- Observability: `CR-OB1` Cloud Trace · 프롬프트 미기록
- Security: `CR-S1` 인증된 호출만 허용 · `CR-S2` 에이전트 전용 신원 · 최소 권한 · `CR-S3` Agent Registry 등록 🅿️ · `CR-S4` Agent Gateway (외부 호출 통제) ⚙️ 🅿️ · `CR-S5` AI 생성 코드 격리 (Cloud Run sandboxes) ⚙️ 🅿️ · `CR-S6` 사용자 위임 인증 (Agent Identity auth manager) ⚙️ 🅿️

**☸️ GKE**

- Features: `GKE-F1` 관리형 영속 세션 저장 (VertexAiSessionService) ⚙️ · `GKE-F2` 관리형 장기 기억 저장 (VertexAiMemoryBankService) ⚙️
- Optimize: `GKE-O1` 무중단 종료 (긴 응답 보호) · `GKE-O2` 에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox) ⚙️
- Delivery & Eval: `GKE-D1` CI/CD 파이프라인 (agents-cli) · `GKE-D2` 평가 데이터셋 · 평가 게이트
- Observability: `GKE-OB1` Cloud Trace · 프롬프트 미기록
- Security: `GKE-S1` 에이전트 전용 신원 · 최소 권한 · `GKE-S2` AI 생성 코드 격리 (Agent Substrate · Agent Sandbox) ⚙️ · `GKE-S3` Agent Registry 등록 🅿️ · `GKE-S4` Agent Gateway for GKE (외부 호출 통제) ⚙️ 🅿️ · `GKE-S5` 사용자 위임 인증 (Agent Identity auth manager) ⚙️ 🅿️


⚙️ 조건부 · 🅿️ Preview

---

## 2. 환경별 항목 정의

ID 규칙: `<환경>-<범주><번호>` — 환경 `CR`·`AR`·`GKE` / 범주 `F`·`O`·`D`·`OB`·`S`
**†** 표시는 구현 전에 참고 링크를 확인할 것.

### 2-1. 🛡️ Agent Runtime

| ID | 범주 | 항목 | 등급 | Check (에이전트 특성 → 확인 사항) | Best Practice (Reference) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| AR-F1 | Features | 관리형 영속 세션 저장 (VertexAiSessionService) | 조건부 (멀티턴 대화) | Agent Runtime에는 `VertexAiSessionService`가 기본으로 연결되지만, 코드에서 `InMemorySessionService`로 덮어쓰면 재시작·스케일아웃 때 대화가 끊깁니다. 기본 관리형 세션을 그대로 쓰는지 확인합니다. | Agent Runtime에 기본으로 연결되는 `VertexAiSessionService`를 그대로 사용하고, 코드에서 다른 세션 서비스로 바꾸지 않습니다. ([Agent Platform Sessions](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/sessions/overview) · [ADK Sessions](https://google.github.io/adk-docs/sessions/session/)) |
| AR-F2 | Features | 관리형 장기 기억 저장 (VertexAiMemoryBankService) | 조건부 (장기 기억 필요) | 개인화 에이전트는 세션이 끝난 뒤에도 사용자의 선호와 사실을 기억해야 합니다. 이런 에이전트에 Memory Bank가 연결되어 있는지 확인합니다. | `VertexAiMemoryBankService`(Memory Bank)로 세션 간 기억을 관리합니다. 같은 Agent Runtime 인스턴스의 Memory Bank를 사용합니다. ([Memory Bank](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory-bank/overview) · [ADK Memory](https://google.github.io/adk-docs/sessions/memory/)) |
| AR-D1 | Delivery & Eval | CI/CD 파이프라인 (agents-cli) | 필수 | 에이전트 변경은 배포 전에 자동으로 테스트·평가되어야 하는데, 사람이 직접 배포하면 이 검증을 건너뛰게 됩니다. agents-cli로 생성한 CI/CD 파이프라인(`.cloudbuild/`)이 있는지 확인합니다. | `agents-cli scaffold enhance . --cicd-runner google_cloud_build`로 파이프라인(`.cloudbuild/`)을 생성해 PR 테스트 → staging 배포 → prod 승인 흐름을 코드로 둡니다. CI/CD 인프라 생성(`agents-cli infra cicd`)은 운영 환경 구성이므로 직접 수행합니다. ([agents-cli CI/CD](https://google.github.io/agents-cli/guide/deployment/)) |
| AR-D2 | Delivery & Eval | 평가 데이터셋 · 평가 게이트 | 필수 | LLM 출력은 비결정적이라 프롬프트·모델·도구를 바꾸면 단위 테스트로 잡히지 않는 품질 저하가 생깁니다. 평가 데이터셋이 있고, 파이프라인 staging 단계에서 평가 점수가 기준에 못 미치면 prod 배포로 넘어가지 않는지 확인합니다. | `agents-cli eval`용 평가 데이터셋을 두고, 파이프라인 staging 단계에서 `agents-cli eval run` 점수를 기준값과 비교해 미달이면 실패하게 합니다(`eval run`은 점수와 무관하게 성공으로 끝나므로 비교 단계가 필요). ([ADK Evaluate](https://google.github.io/adk-docs/evaluate/)) |
| AR-OB1 | Observability | 프롬프트 미기록 | 필수 | 트레이스는 플랫폼이 기본으로 수집하지만, 프롬프트·응답에는 사용자 개인정보가 섞입니다. 트레이스에 메시지 본문이 기록되지 않는지 확인합니다. | GenAI 메시지 본문 수집은 끈 상태로 둡니다. ([OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)) |
| AR-S1 | Security | 에이전트 전용 신원 · 최소 권한 | 필수 | 에이전트는 스스로 판단해 도구·API를 호출하므로, 권한이 넓으면 프롬프트 인젝션 한 번으로도 피해가 커집니다. 기본 서비스 에이전트 권한에 의존하지 않고 에이전트 전용 신원으로 실행되고 필요한 권한만 갖는지 확인합니다. | Agent Identity(Preview)로 에이전트 고유 신원을 부여하고, 사용할 수 없으면 전용 서비스 계정을 지정합니다. 어느 쪽이든 필요한 최소 역할만 부여합니다. ([Agent Identity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview) · [IAM 보안 가이드](https://cloud.google.com/iam/docs/using-iam-securely)) |
| AR-S2 | Security | Agent Registry 등록 | 필수 `Preview` | 팀마다 에이전트·MCP 서버를 만들면 조직은 어떤 에이전트가 무엇을 호출하는지 파악하기 어려워집니다. 에이전트가 Agent Registry에 등록되어 있는지 확인합니다. | Agent Registry에 에이전트를 등록해 조직 단위로 가시성과 거버넌스를 확보합니다. (참고 링크 확인 중 †) |
| AR-S3 | Security | Agent Gateway (외부 호출 통제) | 조건부 (MCP·외부 도구 호출) `Preview` | 에이전트가 무엇을 호출할지는 LLM이 실행 시점에 정하므로, 코드 리뷰만으로는 외부 호출을 통제할 수 없습니다. MCP·외부 도구 호출이 Agent Gateway를 거쳐 정책으로 통제되는지 확인합니다. | Agent Gateway를 연결해 에이전트의 외부 호출에 접근 정책을 적용하고, 정책은 시험 모드로 검증한 뒤 강제 모드로 전환합니다. ([Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview)) |
| AR-S4 | Security | AI 생성 코드 격리 (Code Execution) | 조건부 (AI 생성 코드 실행) | AI가 만든 코드는 신뢰할 수 없어, Agent Runtime 컨테이너에서 그대로 실행하면 에이전트 신원·자격증명이 탈취될 수 있습니다. 이런 코드가 관리형 Code Execution 샌드박스에서 격리되어 실행되는지 확인합니다. | 에이전트의 코드 실행을 Agent Platform Code Execution(관리형 샌드박스)에 맡깁니다. 샌드박스는 1초 이내에 생성되고 실행 상태를 TTL 동안 유지합니다. ([Agent Platform Code Execution](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale/sandbox/code-execution-overview)) |
| AR-S5 | Security | 사용자 위임 인증 (Agent Identity auth manager) | 조건부 (사용자 대신 외부 도구 호출) `Preview` | 에이전트는 사용자를 대신해 Jira·GitHub 같은 외부 시스템을 호출하는데, 정적 API 키나 개인 토큰을 쓰면 모든 사용자가 같은 권한으로 접근하고 키가 유출되면 피해가 큽니다. 사용자 대신 호출하는 외부 도구가 auth manager로 위임받은 사용자 OAuth 토큰을 쓰는지 확인합니다. | auth manager에 auth provider(3-legged OAuth)를 구성하고, ADK가 도구·MCP 호출 시 사용자 위임 토큰을 받아 인증 헤더를 붙이게 합니다. 정적 API 키·PAT는 코드와 환경변수에 두지 않습니다. ([Agent Identity auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth with auth manager](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)) |

### 2-2. ⚡ Cloud Run

| ID | 범주 | 항목 | 등급 | Check (에이전트 특성 → 확인 사항) | Best Practice (Reference) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| CR-F1 | Features | 관리형 영속 세션 저장 (VertexAiSessionService) | 조건부 (멀티턴 대화) | 에이전트는 대화 맥락을 상태로 갖기 때문에, 세션이 인스턴스 메모리(`InMemorySessionService`)에만 있으면 인스턴스 재시작·스케일아웃 때 대화가 끊깁니다. 세션이 `VertexAiSessionService`에 저장되는지 확인합니다. | `VertexAiSessionService`로 세션을 저장해 인스턴스 재시작·스케일아웃 후에도 대화를 이어갑니다. Agent Platform 인스턴스 ID로 세션 저장소에 연결합니다. ([ADK Sessions](https://google.github.io/adk-docs/sessions/session/) · [Agent Platform Sessions](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/sessions/overview)) |
| CR-F2 | Features | 관리형 장기 기억 저장 (VertexAiMemoryBankService) | 조건부 (장기 기억 필요) | 개인화 에이전트는 세션이 끝난 뒤에도 사용자의 선호와 사실을 기억해야 합니다. 이런 에이전트에 Memory Bank가 연결되어 있는지 확인합니다. | `VertexAiMemoryBankService`(Memory Bank)로 세션 간 기억을 관리합니다. Agent Platform 인스턴스 ID로 Memory Bank에 연결합니다. ([Memory Bank](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory-bank/overview) · [ADK Memory](https://google.github.io/adk-docs/sessions/memory/)) |
| CR-D1 | Delivery & Eval | CI/CD 파이프라인 (agents-cli) | 필수 | 에이전트 변경은 배포 전에 자동으로 테스트·평가되어야 하는데, 사람이 직접 배포하면 이 검증을 건너뛰게 됩니다. agents-cli로 생성한 CI/CD 파이프라인(`.cloudbuild/`)이 있는지 확인합니다. | `agents-cli scaffold enhance . --cicd-runner google_cloud_build`로 파이프라인(`.cloudbuild/`)을 생성해 PR 테스트 → staging 배포 → prod 승인 흐름을 코드로 둡니다. CI/CD 인프라 생성(`agents-cli infra cicd`)은 운영 환경 구성이므로 직접 수행합니다. ([agents-cli CI/CD](https://google.github.io/agents-cli/guide/deployment/)) |
| CR-D2 | Delivery & Eval | 평가 데이터셋 · 평가 게이트 | 필수 | LLM 출력은 비결정적이라 프롬프트·모델·도구를 바꾸면 단위 테스트로 잡히지 않는 품질 저하가 생깁니다. 평가 데이터셋이 있고, 파이프라인 staging 단계에서 평가 점수가 기준에 못 미치면 prod 배포로 넘어가지 않는지 확인합니다. | `agents-cli eval`용 평가 데이터셋을 두고, 파이프라인 staging 단계에서 `agents-cli eval run` 점수를 기준값과 비교해 미달이면 실패하게 합니다(`eval run`은 점수와 무관하게 성공으로 끝나므로 비교 단계가 필요). ([ADK Evaluate](https://google.github.io/adk-docs/evaluate/)) |
| CR-OB1 | Observability | Cloud Trace · 프롬프트 미기록 | 필수 | 에이전트는 한 요청 안에서 추론과 도구 호출을 여러 번 반복하므로, 단계별 트레이스가 있어야 실패·지연 원인을 찾을 수 있습니다. 에이전트·도구 호출이 Cloud Trace에 수집되고, 개인정보가 섞인 프롬프트·응답 본문은 기록되지 않는지 확인합니다. | ADK 텔레메트리를 Cloud Trace로 내보내고(`otel_to_cloud`), GenAI 메시지 본문 수집은 끈 상태로 둡니다. ([ADK Cloud Trace](https://google.github.io/adk-docs/observability/cloud-trace/) · [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)) |
| CR-S1 | Security | 인증된 호출만 허용 | 필수 | 에이전트는 호출될 때마다 토큰 비용이 들고, 공개된 엔드포인트는 그대로 프롬프트 인젝션의 공격 대상이 됩니다. 서비스에 `allUsers` 호출 권한이 없어 인증된 호출만 받는지 확인합니다. | Cloud Run IAM 인증을 요구하고, 프런트엔드나 다른 서비스는 전용 서비스 계정으로 호출합니다. ([Cloud Run 인증](https://cloud.google.com/run/docs/authenticating/overview)) |
| CR-S2 | Security | 에이전트 전용 신원 · 최소 권한 | 필수 | 에이전트는 스스로 판단해 도구·API를 호출하므로, 권한이 넓으면 프롬프트 인젝션 한 번으로도 피해가 커집니다. 기본 Compute 서비스 계정이 아닌 에이전트 전용 신원으로 실행되고 필요한 권한만 갖는지 확인합니다. | Agent Identity(Preview)로 에이전트 고유 신원을 부여하고, 사용할 수 없으면 전용 서비스 계정을 씁니다. 어느 쪽이든 필요한 최소 역할만 부여합니다. ([Agent Identity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview) · [Cloud Run service identity](https://cloud.google.com/run/docs/securing/service-identity) · [IAM 보안 가이드](https://cloud.google.com/iam/docs/using-iam-securely)) |
| CR-S3 | Security | Agent Registry 등록 | 필수 `Preview` | 팀마다 에이전트·MCP 서버를 만들면 조직은 어떤 에이전트가 무엇을 호출하는지 파악하기 어려워집니다. 에이전트(또는 MCP 서버)가 Agent Registry에 등록되어 있는지 확인합니다. | 서비스를 에이전트(또는 MCP 서버) 유형으로 배포해 Agent Registry에 자동 등록합니다. ([Cloud Run Agent Platform 기능](https://docs.cloud.google.com/run/docs/ai/agent-platform-features)) |
| CR-S4 | Security | Agent Gateway (외부 호출 통제) | 조건부 (MCP·외부 도구 호출) `Preview` | 에이전트가 무엇을 호출할지는 LLM이 실행 시점에 정하므로, 코드 리뷰만으로는 외부 호출을 통제할 수 없습니다. MCP·외부 도구 호출이 Agent Gateway를 거쳐 정책으로 통제되는지 확인합니다. | Agent Gateway for Cloud Run으로 에이전트의 외부 호출에 접근 정책을 적용합니다. ([Agent Gateway for Cloud Run](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/cloud-run-overview)) |
| CR-S5 | Security | AI 생성 코드 격리 (Cloud Run sandboxes) | 조건부 (AI 생성 코드 실행) `Preview` | AI가 만든 코드는 신뢰할 수 없어, 에이전트 컨테이너에서 그대로 실행하면 서비스 계정 토큰이나 비밀값이 탈취될 수 있습니다. 이런 코드가 Cloud Run sandbox에서 격리되어 실행되는지 확인합니다. | 서비스에 `--sandbox-launcher`(2세대 실행 환경)를 켜고, 에이전트의 코드 실행 도구가 Cloud Run sandbox에서 코드를 실행하게 합니다. 샌드박스는 비밀값·환경변수·메타데이터 서버에 접근할 수 없고 외부 네트워크도 기본 차단됩니다. ([Code execution in Cloud Run](https://docs.cloud.google.com/run/docs/code-execution)) |
| CR-S6 | Security | 사용자 위임 인증 (Agent Identity auth manager) | 조건부 (사용자 대신 외부 도구 호출) `Preview` | 에이전트는 사용자를 대신해 Jira·GitHub 같은 외부 시스템을 호출하는데, 정적 API 키나 개인 토큰을 쓰면 모든 사용자가 같은 권한으로 접근하고 키가 유출되면 피해가 큽니다. 사용자 대신 호출하는 외부 도구가 auth manager로 위임받은 사용자 OAuth 토큰을 쓰는지 확인합니다. | auth manager에 auth provider(3-legged OAuth)를 구성하고, ADK가 도구·MCP 호출 시 사용자 위임 토큰을 받아 인증 헤더를 붙이게 합니다. 정적 API 키·PAT는 코드와 환경변수에 두지 않습니다. ([Agent Identity auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth with auth manager](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)) |

### 2-3. ☸️ GKE

| ID | 범주 | 항목 | 등급 | Check (에이전트 특성 → 확인 사항) | Best Practice (Reference) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| GKE-F1 | Features | 관리형 영속 세션 저장 (VertexAiSessionService) | 조건부 (멀티턴 대화) | 에이전트는 대화 맥락을 상태로 갖기 때문에, 세션이 Pod 메모리(`InMemorySessionService`)에만 있으면 Pod 재시작·스케일아웃 때 대화가 끊깁니다. 세션이 `VertexAiSessionService`에 저장되는지 확인합니다. | `VertexAiSessionService`로 세션을 저장해 Pod 재시작·스케일아웃 후에도 대화를 이어갑니다. Agent Platform 인스턴스 ID로 세션 저장소에 연결합니다. ([ADK Sessions](https://google.github.io/adk-docs/sessions/session/) · [Agent Platform Sessions](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/sessions/overview)) |
| GKE-F2 | Features | 관리형 장기 기억 저장 (VertexAiMemoryBankService) | 조건부 (장기 기억 필요) | 개인화 에이전트는 세션이 끝난 뒤에도 사용자의 선호와 사실을 기억해야 합니다. 이런 에이전트에 Memory Bank가 연결되어 있는지 확인합니다. | `VertexAiMemoryBankService`(Memory Bank)로 세션 간 기억을 관리합니다. Agent Platform 인스턴스 ID로 Memory Bank에 연결합니다. ([Memory Bank](https://cloud.google.com/vertex-ai/generative-ai/docs/agent-engine/memory-bank/overview) · [ADK Memory](https://google.github.io/adk-docs/sessions/memory/)) |
| GKE-O1 | Optimize | 무중단 종료 (긴 응답 보호) | 필수 | 에이전트 한 턴은 LLM 스트리밍과 도구 호출로 수십 초 이상 걸려, 기본 종료 유예 시간(30초)으로는 배포·노드 업그레이드 때 응답이 잘릴 수 있습니다. `terminationGracePeriodSeconds`가 최대 응답 시간보다 길고, 종료 중에는 새 요청을 받지 않는지 확인합니다. | `terminationGracePeriodSeconds`를 최대 응답 시간 이상으로 두고, `preStop`과 readiness 프로브로 진행 중인 응답을 마친 뒤 종료합니다. ([Pod termination](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#pod-termination) · [Kubernetes Probes](https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/)) |
| GKE-O2 | Optimize | 에이전트 일시정지 · 복원 (Agent Substrate · Agent Sandbox) | 조건부 (자주 일시정지·재개하는 상태 유지형 에이전트) | 세션별 실행 환경을 갖는 상태 유지형 에이전트는 대부분의 시간을 입력이나 트리거를 기다리며 보내는데, 그동안 Pod가 CPU·메모리를 계속 점유합니다. 유휴 에이전트가 일시정지되어 컴퓨팅을 반납하고, 요청이 오면 메모리·작업 파일 상태가 복원되는지 확인합니다. | Agent Substrate on GKE에서 실행해 유휴 actor를 Cloud Storage 스냅샷으로 일시정지하고 워커의 CPU·메모리를 반납하며, 요청 시 1초 이내에 복원합니다(GKE Standard 1.37 이상, x86 노드). Agent Sandbox on GKE를 써야 하는 경우에는 Sandbox를 `Suspended`로 전환하기 전에 Pod snapshot(gVisor 전용, 복원 약 3~5초)으로 상태를 저장합니다. ([Plan an architecture for AI agent workloads on GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/agent-workloads) · [Agent Substrate on GKE](https://docs.cloud.google.com/kubernetes-engine/ai-ml/about-agent-substrate) · [Agent Sandbox on GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/agent-sandbox)) |
| GKE-D1 | Delivery & Eval | CI/CD 파이프라인 (agents-cli) | 필수 | 에이전트 변경은 배포 전에 자동으로 테스트·평가되어야 하는데, 사람이 직접 배포하면 이 검증을 건너뛰게 됩니다. agents-cli로 생성한 CI/CD 파이프라인(`.cloudbuild/`)이 있는지 확인합니다. | `agents-cli scaffold enhance . --cicd-runner google_cloud_build`로 파이프라인(`.cloudbuild/`)을 생성해 PR 테스트 → staging 배포 → prod 승인 흐름을 코드로 둡니다. CI/CD 인프라 생성(`agents-cli infra cicd`)은 운영 환경 구성이므로 직접 수행합니다. ([agents-cli CI/CD](https://google.github.io/agents-cli/guide/deployment/)) |
| GKE-D2 | Delivery & Eval | 평가 데이터셋 · 평가 게이트 | 필수 | LLM 출력은 비결정적이라 프롬프트·모델·도구를 바꾸면 단위 테스트로 잡히지 않는 품질 저하가 생깁니다. 평가 데이터셋이 있고, 파이프라인 staging 단계에서 평가 점수가 기준에 못 미치면 prod 배포로 넘어가지 않는지 확인합니다. | `agents-cli eval`용 평가 데이터셋을 두고, 파이프라인 staging 단계에서 `agents-cli eval run` 점수를 기준값과 비교해 미달이면 실패하게 합니다(`eval run`은 점수와 무관하게 성공으로 끝나므로 비교 단계가 필요). ([ADK Evaluate](https://google.github.io/adk-docs/evaluate/)) |
| GKE-OB1 | Observability | Cloud Trace · 프롬프트 미기록 | 필수 | 에이전트는 한 요청 안에서 추론과 도구 호출을 여러 번 반복하므로, 단계별 트레이스가 있어야 실패·지연 원인을 찾을 수 있습니다. 에이전트·도구 호출이 Cloud Trace에 수집되고, 개인정보가 섞인 프롬프트·응답 본문은 기록되지 않는지 확인합니다. | ADK 텔레메트리를 Cloud Trace로 내보내고(`otel_to_cloud`), GenAI 메시지 본문 수집은 끈 상태로 둡니다. ([ADK Cloud Trace](https://google.github.io/adk-docs/observability/cloud-trace/) · [OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)) |
| GKE-S1 | Security | 에이전트 전용 신원 · 최소 권한 | 필수 | 에이전트는 스스로 판단해 도구·API를 호출하므로, 권한이 넓으면 프롬프트 인젝션 한 번으로도 피해가 커집니다. Pod가 노드 기본 서비스 계정이 아닌 에이전트 전용 신원으로 실행되고 필요한 권한만 갖는지 확인합니다. | Agent Identity(Preview)로 에이전트 고유 신원을 부여하고, 사용할 수 없으면 Workload Identity로 전용 서비스 계정을 연결합니다. 어느 쪽이든 필요한 최소 역할만 부여합니다. ([Agent Identity](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/agent-identity-overview) · [Workload Identity](https://cloud.google.com/kubernetes-engine/docs/concepts/workload-identity) · [IAM 보안 가이드](https://cloud.google.com/iam/docs/using-iam-securely)) |
| GKE-S2 | Security | AI 생성 코드 격리 (Agent Substrate · Agent Sandbox) | 조건부 (AI 생성 코드 실행) | AI가 만든 코드는 신뢰할 수 없어, 에이전트 Pod 안에서 그대로 실행하면 자격증명 탈취나 노드 침해로 이어질 수 있습니다. 이런 코드가 에이전트 Pod가 아닌 격리된 샌드박스(Agent Substrate 또는 Agent Sandbox)에서 실행되는지 확인합니다. | AI 생성 코드는 에이전트 Pod가 아닌 격리된 샌드박스에서 실행합니다. Agent Substrate on GKE를 기본으로 사용하고(GKE Standard 1.37 이상, x86 노드), 상시 실행 에이전트, 사이드카 등 전체 PodSpec, Arm 노드, Autopilot이 필요하면 Agent Sandbox on GKE를 사용합니다. ([Plan an architecture for AI agent workloads on GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/agent-workloads) · [Agent Substrate on GKE](https://docs.cloud.google.com/kubernetes-engine/ai-ml/about-agent-substrate) · [Agent Sandbox on GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/machine-learning/agent-sandbox)) |
| GKE-S3 | Security | Agent Registry 등록 | 필수 `Preview` | 팀마다 에이전트·MCP 서버를 만들면 조직은 어떤 에이전트가 무엇을 호출하는지 파악하기 어려워집니다. 에이전트 워크로드가 Agent Registry에 등록되어 있는지 확인합니다. | Deployment에 에이전트 유형 레이블을 지정해 Agent Registry에 자동 등록합니다. (참고 링크 확인 중 †) |
| GKE-S4 | Security | Agent Gateway for GKE (외부 호출 통제) | 조건부 (MCP·외부 도구 호출) `Preview` | 에이전트가 무엇을 호출할지는 LLM이 실행 시점에 정하므로, 코드 리뷰만으로는 외부 호출을 통제할 수 없습니다. MCP·외부 도구 호출이 Agent Gateway를 거쳐 정책으로 통제되는지 확인합니다. | Agent Gateway for GKE로 에이전트의 외부 호출에 접근 정책을 적용합니다. ([Agent Gateway](https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/agent-gateway-overview)) |
| GKE-S5 | Security | 사용자 위임 인증 (Agent Identity auth manager) | 조건부 (사용자 대신 외부 도구 호출) `Preview` | 에이전트는 사용자를 대신해 Jira·GitHub 같은 외부 시스템을 호출하는데, 정적 API 키나 개인 토큰을 쓰면 모든 사용자가 같은 권한으로 접근하고 키가 유출되면 피해가 큽니다. 사용자 대신 호출하는 외부 도구가 auth manager로 위임받은 사용자 OAuth 토큰을 쓰는지 확인합니다. | auth manager에 auth provider(3-legged OAuth)를 구성하고, ADK가 도구·MCP 호출 시 사용자 위임 토큰을 받아 인증 헤더를 붙이게 합니다. 정적 API 키·PAT는 코드와 환경변수에 두지 않습니다. ([Agent Identity auth manager](https://docs.cloud.google.com/iam/docs/auth-manager-overview) · [3-legged OAuth with auth manager](https://docs.cloud.google.com/iam/docs/auth-with-3lo-v2)) |

---

## 3. 조건부 적용 기준

| 조건 | 판단 기준 | 적용 항목 |
| :--- | :--- | :--- |
| 멀티턴 대화 | 이전 대화를 이어가야 하는가 | 관리형 영속 세션 저장 (VertexAiSessionService) · 전 환경 |
| 장기 기억 필요 | 세션을 넘어 사용자를 기억해야 하는가 | 관리형 장기 기억 저장 (VertexAiMemoryBankService) · 전 환경 |
| AI 생성 코드 실행 | AI가 만든 코드·셸을 실행하는가 | AI 생성 코드 격리 — Code Execution (Agent Runtime) · Cloud Run sandboxes (Cloud Run) · Agent Sandbox · Agent Substrate (GKE) |
| 자주 일시정지·재개하는 상태 유지형 에이전트 | 세션별 실행 환경(메모리·작업 파일)을 유지하면서 대부분의 시간을 입력·트리거를 기다리는가 | 에이전트 일시정지 · 복원 (GKE) |
| 사용자 대신 외부 도구 호출 | 사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하는가 | 사용자 위임 인증 (Agent Identity auth manager) · 전 환경 |
| MCP·외부 도구 호출 | MCP 서버·외부 API·다른 에이전트를 호출하는가 | Agent Gateway (외부 호출 통제) |

- Agent Skill은 코드에서 먼저 판단하고(세션 서비스 설정, Memory Bank 사용, 사설 IP·VPC 설정, 코드 실행 도구, Dockerfile 존재), 판단하지 못한 것만 질문한다.
- 이전 초안의 "사용자 범위 · 개인정보 · 도구 권한"은 연결된 항목이 권고 사항으로 옮겨져 조건에서 뺐다. 권고 사항 안내(5장)를 맞춤화하는 데만 쓴다.

---

## 4. 이번 재검토로 체크리스트에서 뺀 것

| 항목 | 뺀 이유 | 처리 |
| :--- | :--- | :--- |
| 인바운드 접근 통제 (Cloud Armor 등) | 모호하고 Cloud Armor는 선택형 유료 제품. Agent Runtime은 플랫폼이 IAM 인증을 강제 | Cloud Run만 "인증된 호출만 허용"으로 구체화, Cloud Armor·IAP는 권고 |
| 민감정보 노출 방지 (Model Armor) | 유료 선택형 제품 | 권고 |
| 위험 작업 사람 승인 | 앱 설계 영역이라 설정으로 판정 불가, 실행 방법이 모호 | 삭제 |
| Secure Web Proxy 단독 이그레스 통제 | 유료 선택형 | 권고 (Agent Gateway는 `Preview` 조건부 항목으로 유지) |
| 전용 서비스 계정 / Agent Identity 별도 항목 | 같은 목적의 두 방식이 충돌 | "에이전트 전용 신원 · 최소 권한" 한 항목으로 통합 (Agent Identity 우선, 불가 시 전용 서비스 계정) |
| Gemini 호출 재시도 · 엔드포인트 | ADK 최적화(에이전트 코드) 영역 | 삭제 |
| 시작 CPU 부스트 · 2세대 실행 환경 · 타임아웃 · 스케일링 · HPA | 값·아키텍처 선택 | 권고 |

### 4-1. 일반 플랫폼 BP → 플랫폼 Skill에 위임

에이전트가 아니어도 모든 컨테이너 서비스에 해당하는 BP다. 체크리스트에서 빼고, [google/skills · cloud](https://github.com/google/skills/tree/main/skills/cloud)의 플랫폼 Skill을 **함께 참조하도록 적극 권장**한다. Agent Skill(`SKILL.md`)과 리포트에도 같은 안내를 넣는다.

| 일반 BP | 이전 ID | 참조 Skill |
| :--- | :--- | :--- |
| 헬스 프로브 (startup·liveness) | CR-O1 · GKE-O2 일부 | `cloud-run-basics` · `gke-reliability` |
| 멀티존 · PodDisruptionBudget | GKE-O1 | `gke-reliability` · `gke-productionize` |
| Terraform IaC · 드리프트 방지 | CR/AR/GKE-D1 | `google-cicd-terraform` |
| 지연·오류율 알림 정책 | CR/AR/GKE-OB2 | `cloud-run-alert-configuration` · `agent-platform-alert-configuration` · `gke-alert-configuration` |
| Secret Manager 버전 고정 | CR-S3 | `cloud-run-basics` |
| Non-Root 컨테이너 | CR-S4 · AR-S2 | `cloud-run-basics` · `gke-workload-security` |
| Direct VPC Egress · PSC Interface (사설 연결) | CR-S5 · AR-S3 | `cloud-run-basics` · `google-cloud-waf-security` |
| Private Nodes · NetworkPolicy | GKE-S1 | `gke-networking` · `gke-workload-security` |
| Pod Security Standards | GKE-S3 | `gke-workload-security` |
| 서비스 계정 키(JSON) 미사용 · Workload Identity Federation | — | `gke-workload-identity` · `iam-helper-for-policy-management` |

환경별 함께 참조 권장 Skill:

- **Agent Runtime**: [`agent-platform-deploy`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-deploy) · [`agent-platform-agent-registry`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-agent-registry) · [`agent-platform-access-audit`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-access-audit) · [`agent-platform-alert-configuration`](https://github.com/google/skills/tree/main/skills/cloud/agent-platform-alert-configuration)
- **Cloud Run**: [`cloud-run-basics`](https://github.com/google/skills/tree/main/skills/cloud/cloud-run-basics) · [`cloud-run-alert-configuration`](https://github.com/google/skills/tree/main/skills/cloud/cloud-run-alert-configuration) · [`google-cicd-terraform`](https://github.com/google/skills/tree/main/skills/cloud/google-cicd-terraform) · [`google-cloud-waf-security`](https://github.com/google/skills/tree/main/skills/cloud/google-cloud-waf-security)
- **GKE**: [`gke-productionize`](https://github.com/google/skills/tree/main/skills/cloud/gke-productionize) · [`gke-reliability`](https://github.com/google/skills/tree/main/skills/cloud/gke-reliability) · [`gke-workload-security`](https://github.com/google/skills/tree/main/skills/cloud/gke-workload-security) · [`gke-workload-identity`](https://github.com/google/skills/tree/main/skills/cloud/gke-workload-identity) · [`gke-networking`](https://github.com/google/skills/tree/main/skills/cloud/gke-networking) · [`gke-alert-configuration`](https://github.com/google/skills/tree/main/skills/cloud/gke-alert-configuration)

## 5. 권고 사항 (체크리스트 아님)

| 대상 | 권고 사항 | 검토 시점 |
| :--- | :--- | :--- |
| Cloud · Run | 시작 CPU 부스트 | 기동이 느린 런타임에서 콜드스타트를 줄여야 할 때 |
| Cloud · Run | 요청 타임아웃 · 최소 인스턴스 | 에이전트 최대 응답 시간과 지연 민감도 (부하 테스트로 결정) |
| Cloud · Run | 1세대 / 2세대 실행 환경 | 콜드스타트가 우선이면 1세대, 성능·호환성이 우선이면 2세대 |
| Agent · Runtime | 최소/최대 인스턴스 · 동시성 | 트래픽 패턴과 지연 요구 |
| GKE | HPA · 리소스 요청/한도 | 트래픽 변동 폭과 부하 테스트 결과 |
| Cloud · Run · Agent · Runtime · GKE | Secure Web Proxy (이그레스 프록시) | Agent Gateway를 쓸 수 없는 환경에서 외부 호출 통제가 필요할 때 (비용 발생) |
| Cloud · Run · Agent · Runtime · GKE | Model Armor (프롬프트·응답 검사) | 외부 사용자 대상이거나 민감정보를 다룰 때 (비용 발생) |
| Cloud · Run · GKE | Cloud Armor · IAP (외부 노출 보호) | 에이전트를 인터넷에 직접 노출할 때 (비용 발생) |
| Cloud · Run · Agent · Runtime · GKE | 모델 용량 확보 (Provisioned Throughput 등) | 레지던시로 리전을 고정해 429 위험이 크거나 SLA가 필요할 때 |

---

## 6. 참고: 에이전트 코드 영역은 체크하지 않는다

- Gemini 호출 재시도(`Gemini(retry_options=HttpRetryOptions(...))`)와 엔드포인트 위치는 ADK 에이전트 코드·앱 설정 영역이다. ADK 최적화 영역이라 체크리스트·권고 사항 모두에서 다루지 않는다. (세션·Memory Bank·트레이스 연결 코드는 플랫폼 서비스 연결이라 항목으로 유지)
- 그래서 Cloud Run·Agent Runtime에는 Optimize 범주 항목이 없고, 페이지에서도 해당 범주를 숨긴다.

---

## 7. 반영 현황

| 대상 | 내용 | 상태 |
| :--- | :--- | :--- |
| [`catalog_v3.json`](../skills/agent-production-readiness/scripts/prod_ready/catalog_v3.json) | 이 문서와 `/checklist`를 만드는 생성기가 함께 출력하는 단일 기준 데이터 | 반영 |
| [`checklist.py`](../skills/agent-production-readiness/scripts/prod_ready/checklist.py) | 카탈로그 로드, 환경별 v3 ID, 기존 `C*` 별칭(`LEGACY_ID_MAP`), 위임·범위 밖 안내 | 반영 |
| [`scanner.py`](../skills/agent-production-readiness/scripts/prod_ready/scanner.py) | v3 ID별 점검, 조건 자동 판단(`--conditions`로 지정 가능), 조건 미해당은 N/A, `Preview`는 판정 비차단 | 반영 |
| [`patcher.py`](../skills/agent-production-readiness/scripts/prod_ready/patcher.py) | v3 ID → 기존 패치 로직 매핑. 위임된 일반 BP·권고 사항은 자동 패치하지 않음. 안내 전용 항목은 diff 없음 | 반영 |
| [`deps.py`](../skills/agent-production-readiness/scripts/prod_ready/deps.py) · `prod-ready doctor` | 필수 의존성(agents-cli·agents-cli Skill) 점검, 플랫폼 Skill 설치 강력 권장 | 반영 |
| 리포트 | 조건 판단 → 범주별 항목(Check · Best Practice · Reference) → 권고 사항 → 플랫폼 Skill 참조 권장 | 반영 |
| [`web/runtime_inspector.py`](../web/runtime_inspector.py) | 라이브 점검을 v3 항목으로 분류, 일반 점검은 "플랫폼 Skill 참조 권장"(점수 미반영) | 반영 |
| `SKILL.md` · `references/*.md` | 이 문서 기준 | 반영 |
| `/workbench` · `/lab` · `/deck` | 이 문서 기준 | 반영 |

## 8. 남은 결정 사항

1. (결정됨) `NEW` 표시는 삭제. v3 항목은 모두 정식 항목으로 취급
2. (결정됨) Delivery & Eval은 D1 CI/CD 파이프라인(`agents-cli scaffold enhance --cicd-runner google_cloud_build`) + D2 평가 데이터셋·게이트 두 항목으로 체크한다. `agents-cli infra cicd`(staging/prod 인프라)는 운영 환경 구성이라 범위 밖 — 사용자가 직접 수행
3. 에이전트 특화 알림(토큰 사용량·모델 429)을 Observability 항목으로 둘지 (현재는 `agent-platform-alert-configuration` Skill에 위임)
