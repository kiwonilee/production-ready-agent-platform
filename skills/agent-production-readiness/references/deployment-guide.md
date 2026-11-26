# 설치 · 점검 · 배포 가이드 (`deployment-guide.md`, 체크리스트 v3)

> 항목 정의: [`checklist-rubric.md`](checklist-rubric.md) · 조건 판단: [`intake-questionnaire.md`](intake-questionnaire.md)

## 1. GitHub에서 Skill 설치

```bash
rm -rf ~/.gemini/skills/agent-production-readiness && mkdir -p ~/.gemini/skills
curl -fsSL "https://github.com/kiwonilee/production-ready-agent-platform/archive/refs/heads/main.tar.gz" | tar -xz -C ~/.gemini/skills/ --strip-components=2 production-ready-agent-platform-main/skills/agent-production-readiness
```

## 2. 점검 · 적용 · 리포트

```bash
export SKILL_BIN="$HOME/.gemini/skills/agent-production-readiness/scripts/prod_ready.py"

# 0) 의존성 점검 — 필수: agents-cli >= 1.5.0 + agents-cli Skill (deploy · eval · scaffold), 미설치 시 exit 1
#    권장: 대상 환경의 플랫폼 Skill (google/skills) — 미설치여도 진행하지만 설치를 강력히 권장
python3 "$SKILL_BIN" doctor . --target auto
#    필수 누락 시:   uv tool install google-agents-cli   →   agents-cli setup
#    플랫폼 Skill:   python3 "$SKILL_BIN" doctor . --target gke --install-platform-skills   (사용자 동의 시에만)

# 전체 항목 (조건부 항목은 엔진이 조건을 판단해 N/A 처리)
python3 "$SKILL_BIN" scan . --target auto --items ALL

# 특정 항목만 (v3 ID · 기존 C* ID는 별칭으로 동작)
python3 "$SKILL_BIN" scan . --target cloud_run --items CR-D1,CR-OB1,CR-S1,CR-S2

python3 "$SKILL_BIN" diff  . --target auto --items ALL
python3 "$SKILL_BIN" apply . --target auto --items ALL      # diff 확인 · 사용자 승인 후
# GKE에서 AI 생성 코드를 실행하면 (GKE-S2):
# python3 "$SKILL_BIN" apply . --target gke --items ALL --params '{"gke_execution_pattern":"agent_substrate","gke_termination_grace_seconds":180}'
uv lock
python3 "$SKILL_BIN" report . --target auto --items ALL --format both -o READINESS_REPORT.md
```

### 2-1. CI/CD 파이프라인 (`*-D1`) · 평가 데이터셋 · 평가 게이트 (`*-D2`)

CI/CD 파이프라인 파일은 agents-cli가 만듭니다. 엔진은 파이프라인을 생성하지 않고, 있는 파이프라인의 staging 단계에 평가 게이트만 넣습니다.

```bash
# 1) *-D1: 파이프라인이 없으면 (D1 🔴 "CI/CD 파이프라인 없음") 먼저 생성 — Agent Skill은 사용자 승인 후 실행
agents-cli scaffold enhance . --cicd-runner google_cloud_build

# 2) *-D2: 평가 데이터셋·설정 + staging 단계 agent-eval-gate 적용 (eval run 점수를 기준값과 비교해 미달이면 실패)
python3 "$SKILL_BIN" apply . --target auto --items CR-D2 --params '{"eval_threshold": 3.5}'

# 3) (운영 환경 구성 · 사용자가 직접) 트리거와 staging/prod CI/CD 인프라 생성 — 체크리스트 범위 밖
# agents-cli infra cicd
```

## 3. GCP 배포 (`Terraform` + `agents-cli deploy` + GKE `kubectl apply`)

```bash
# agents-cli v1.5+ single-project 구조면 deployment/terraform/single-project 사용
cd deployment/terraform
terraform init
# vars/env.tfvars 가 있으면 -var-file=vars/env.tfvars 추가
terraform apply -var="project_id=${PROJECT_ID}" -var="region=${REGION}"
cd ../..

# Agent Runtime — Agent Identity 사용 시 (--service-account 와 함께 쓸 수 없음) (AR-S1)
uvx google-agents-cli deploy --project="${PROJECT_ID}" --region="${REGION}" --agent-identity --no-confirm-project

# Cloud Run — 전용 서비스 계정 사용 시 (CR-S2 대안)
uvx google-agents-cli deploy --project="${PROJECT_ID}" --region="${REGION}" --service-account="${SA_EMAIL}" --no-confirm-project

# GKE
gcloud container clusters get-credentials "${CLUSTER_NAME}" --region="${REGION}" --project="${PROJECT_ID}"
uvx google-agents-cli deploy --project="${PROJECT_ID}" --region="${REGION}" --no-confirm-project
# GKE-S4 Agent Gateway for GKE (Preview · GKE >= 1.37.2-gke.1042000 Rapid · 일반 Pod):
#   kubectl label namespace "${NAMESPACE}" networking.gke.io/dataplane-mode=ambient --overwrite
#   kubectl apply -f deployment/k8s/agent-gateway.yaml
# GKE-S2 Agent Substrate on GKE (GKE Standard >= 1.37):
#   kubectl apply -f deployment/k8s/agent-substrate.yaml
# GKE-S2 대안 Agent Sandbox on GKE (상시 실행 · 사이드카 · Arm · Autopilot):
#   kubectl apply -f deployment/k8s/agent-sandbox.yaml
```

## 4. 배포 후 확인

```bash
# CR-S3 · AR-S2 · GKE-S3 Agent Registry 등록 (Preview)
gcloud agent-registry agents list --location="${REGION}" --project="${PROJECT_ID}"

# CR-S1 인증된 호출만 허용 — 미인증 호출은 403 이어야 함
curl -s -o /dev/null -w "%{http_code}\n" "${SERVICE_URL}/list-apps"
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $(gcloud auth print-identity-token)" "${SERVICE_URL}/list-apps"
```

> [!NOTE]
> - **Agent Runtime `AR-S3`**: `google_vertex_ai_reasoning_engine`의 `agent_gateway_config` 및 `google_network_services_agent_gateway`는 `provider = google-beta`가 필요합니다. 정책은 `DRY_RUN`으로 시작해 검토 후 `ENFORCED`로 전환합니다.
> - **GKE / Cloud Run `agent_engine_id` 유지**: `agents-cli deploy` on GKE가 내부적으로 `terraform apply`를 재실행하므로, `agent_engine_id`(및 Cloud Run `organization_id`)는 일회성 `-var` 대신 `terraform.tfvars`에 저장하세요.
> - **Cloud Run `CR-S2` Agent Identity(Preview) 세션 `401`**: Preview 워크로드 인증서(mTLS) 제약으로 `VertexAiSessionService` 호출이 `401`을 반환하면 전용 최소 권한 서비스 계정(`app_sa`)으로 전환하세요 (`CR-S2` 기준 유지).
> - **Agent Runtime 정리(`terraform destroy`)**: 세션·Memory Bank 하위 리소스가 생성된 엔진은 `DELETE .../reasoningEngines/<ID>?force=true` 실행 후 `terraform destroy`를 수행하세요.

## 5. 플랫폼 Skill로 일반 BP 점검 (적극 권장)

이 Skill은 에이전트 특성에서 나온 항목만 점검합니다. 배포 전후로 [google/skills · cloud](https://github.com/google/skills/tree/main/skills/cloud)의 플랫폼 Skill로 헬스 프로브·IaC·알림·Non-Root·사설 연결·PSS 등을 함께 점검하세요 (환경별 목록: [`checklist-rubric.md`](checklist-rubric.md) §5). 도메인 품질 평가는 `google-agents-cli-eval` Skill(`uvx google-agents-cli eval run --dataset <path> --metrics ...`)을 사용합니다.
