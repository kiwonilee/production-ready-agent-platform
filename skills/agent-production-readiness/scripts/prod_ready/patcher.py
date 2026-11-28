"""In-Place Patcher, Report Generator & Zip Exporter — Production-Readiness Checklist v3.

v3 principles (see docs/checklist-v3-spec.md §7):
- v3 item ids (e.g. ``CR-F1``, ``AR-S3``, ``GKE-S2``) select patches; legacy ``C*`` ids resolve via
  :data:`prod_ready.checklist.LEGACY_ID_MAP`.
- Each v3 item maps to internal patch flags that gate the existing ``_prepare_*`` builders
  (:data:`V3_PATCH_FLAGS`). Delegated general best practices (startup CPU boost, Cloud Run HTTP probes,
  PDB / topology spread / HPA, Non-Root ``USER``, PSS, NetworkPolicy, Secret version pinning,
  Direct VPC / PSC-I, ``alerts.tf``, Gen2) are **never** triggered by v3 item selection.
- With ``ALL`` (implicit selection), Preview items and conditional items whose workload condition is
  not detected are skipped; they are applied only when explicitly selected.
- Reports use v3 ids / categories, Check · Best Practice · References text from ``catalog_v3.json``
  and include a platform-skills (google/skills) section.
"""

from __future__ import annotations

import difflib
import html as html_lib
import io
import re
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prod_ready.checklist import (
    ITEM_BY_ID,
    CustomParams,
    TargetType,
    resolve_item_selection,
)
from prod_ready.scanner import (
    _has_adk_otel_gcp_extras,
    detect_mcp_a2a_profile,
    detect_project_mode_and_target,
    detect_workload_conditions,
    resolve_terraform_rel_dir,
    scan_project,
)


@dataclass
class FilePatch:
    relative_path: str
    is_new: bool
    applied_items: list[str]
    unified_diff: str
    updated_content: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _make_diff(rel_path: str, old: str, new: str) -> str:
    old_lines = old.splitlines(keepends=True) if old else []
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{rel_path}" if old else "/dev/null",
        tofile=f"b/{rel_path}",
        n=3,
    )
    return "".join(diff)


# v3 item -> internal patch flags consumed by the `_prepare_*` builders.
# Internal flags keep the historical C-codes for the builders that still implement them;
# delegated flags (C1.2 / C3.1 / C4.3 / C5.3 / SECRET_PIN) are intentionally never produced here.
# Gemini call retry · endpoint (legacy C6.2: ``retry_options`` / ``GOOGLE_CLOUD_LOCATION``) is ADK agent-code
# territory (ADK optimization) and was removed from the v3 checklist — no v3 selection (including ALL) produces it.
V3_PATCH_FLAGS: dict[str, frozenset[str]] = {
    # Common (all runtimes)
    "COMMON-DO1": frozenset(),  # agents-cli CI/CD pipeline — guidance only (never generated here)
    "COMMON-DO2": frozenset({"C7.1"}),  # eval dataset/config + score gate inserted into an existing pipeline
    "COMMON-DO3": frozenset({"BQ_ANALYTICS"}),  # BigQuery Agent Analytics (Tier 3 Optional)
    "COMMON-ID1": frozenset(),  # user-delegated auth (Agent Identity auth manager, Preview) — guidance only
    "COMMON-SF1": frozenset({"MODEL_ARMOR"}),  # Model Armor standalone template (Tier 3 Optional)
    "COMMON-SF2": frozenset(),  # ADK Tool Confirmation (HITL, Tier 3 Optional) — guidance only
    # Agent Runtime
    "AR-RS1": frozenset(),  # platform default VertexAiSessionService
    "AR-RS2": frozenset({"MEMBANK"}),  # platform default Memory Bank + ADK preload_memory & after_agent_callback
    "AR-ID1": frozenset({"C4.1"}),
    "AR-ID2": frozenset({"C4.2"}),
    "AR-SF1": frozenset({"C4.4"}),
    "AR-SF2": frozenset(),  # Agent Platform Code Execution — agent-code wiring, guidance only
    "AR-DO1": frozenset({"C5.1"}),
    "AR-DO2": frozenset(),  # Online Evaluation Monitor (Agent Runtime exclusive) — guidance only
    # Cloud Run
    "CR-RS1": frozenset({"C2.1"}),
    "CR-RS2": frozenset({"MEMBANK"}),
    "CR-ID1": frozenset({"AUTHN"}),
    "CR-ID2": frozenset({"C4.1"}),
    "CR-ID3": frozenset({"C4.2"}),
    "CR-SF1": frozenset(),  # Agent Gateway for Cloud Run (Preview) — manual guidance only
    "CR-SF2": frozenset(),  # Cloud Run sandboxes (Preview) — guidance only
    "CR-DO1": frozenset({"C5.1"}),
    # GKE
    "GKE-RS1": frozenset({"C2.1"}),
    "GKE-RS2": frozenset({"MEMBANK"}),
    "GKE-RS3": frozenset({"GKE_DRAIN"}),
    "GKE-RS4": frozenset({"SUBSTRATE"}),  # idle suspend/resume → reuses Agent Substrate scaffolding
    "GKE-ID1": frozenset({"C4.1"}),
    "GKE-ID2": frozenset({"C4.2"}),
    "GKE-SF1": frozenset({"SANDBOX"}),
    "GKE-SF2": frozenset({"C4.4"}),
    "GKE-DO1": frozenset({"C5.1"}),
}

# Base Terraform scaffold for prototypes (no service.tf yet) so selected v3 items have a place to land.
BASE_FLAG = "C1.1"
BASE_LABEL = "BASE"
DELEGATED_PATCH_FLAGS: frozenset[str] = frozenset({"C1.2", "C3.1", "C4.3", "C5.3", "SECRET_PIN"})


def select_patch_items(
    root: Path,
    target: TargetType,
    selected_items: list[str] | None,
    conditions: dict[str, bool] | None = None,
) -> list[str]:
    """Returns the v3 ids whose patches should run.

    Implicit selection (``None`` / ``ALL``) skips Preview items and conditional items whose workload
    condition is not met (auto-detected, or ``conditions`` overrides such as ``{"idle": True}``).
    Explicitly listed ids are always honoured.
    """
    sel = resolve_item_selection(selected_items, target=target)
    if sel.explicit:
        return list(sel.ids)
    conds = detect_workload_conditions(root, overrides=conditions)
    chosen: list[str] = []
    for item_id in sel.ids:
        spec = ITEM_BY_ID[item_id]
        if spec.preview or spec.grade == "optional":
            continue
        if spec.grade == "conditional" and spec.condition:
            cres = conds.get(spec.condition)
            if cres is not None and not cres.met:
                continue
        chosen.append(item_id)
    return chosen


def _flags_for(item_ids: list[str]) -> set[str]:
    flags: set[str] = set()
    for item_id in item_ids:
        flags |= set(V3_PATCH_FLAGS.get(item_id, frozenset()))
    return flags


def _flags_to_v3(flags: list[str], v3_ids: list[str]) -> list[str]:
    out: list[str] = []
    for flag in flags:
        if flag == BASE_FLAG:
            label = [BASE_LABEL]
        else:
            label = [i for i in v3_ids if flag in V3_PATCH_FLAGS.get(i, frozenset())]
        for lab in label:
            if lab not in out:
                out.append(lab)
    return out


def preview_patches(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
    custom_params: CustomParams | None = None,
    conditions: dict[str, bool] | None = None,
) -> list[FilePatch]:
    """Computes all file creations and in-place patches without mutating the disk."""
    root = Path(project_dir).resolve()
    mode, target, project_name = detect_project_mode_and_target(root, target_override)
    v3_ids = select_patch_items(root, target, selected_items, conditions)
    item_ids = _flags_for(v3_ids)
    if item_ids and mode == "prototype":
        item_ids.add(BASE_FLAG)
    params = custom_params or CustomParams()

    planned_files: dict[str, tuple[str, list[str]]] = {}

    def record(rel_path: str, new_content: str, items: list[str]) -> None:
        active = [i for i in items if i in item_ids]
        if not active:
            return
        orig_path = root / rel_path
        orig_content = orig_path.read_text(encoding="utf-8") if orig_path.exists() else ""
        if orig_content == new_content:
            return
        planned_files[rel_path] = (new_content, active)

    # 1. Base Terraform files (created if missing in Mode A Prototype, or patched in Mode B)
    _prepare_variables_tf(root, project_name, target, item_ids, params, record)
    _prepare_apis_and_iam_and_storage_tf(root, project_name, target, item_ids, params, record)
    _prepare_service_tf(root, project_name, target, item_ids, params, record)

    # 2. Drop-in Terraform modules, CI/CD Terraform mirror (Option A) & GKE Kubernetes manifests
    _prepare_production_ready_tf(root, project_name, target, item_ids, params, record)
    _prepare_terraform_cicd(root, project_name, target, item_ids, params, record)
    _prepare_alerts_tf(root, project_name, target, item_ids, params, record)  # delegated (C5.3) — no-op for v3
    _prepare_gke_k8s_manifests(root, project_name, target, item_ids, params, record)

    # 3. Application code (services.py, agent.py, fast_api_app.py, pyproject.toml)
    _prepare_session_and_memory_services_py(root, project_name, target, item_ids, record)
    _prepare_memory_bank_services_py(root, target, item_ids, planned_files)
    _prepare_agent_and_fastapi_py(root, project_name, target, item_ids, params, record)
    _prepare_pyproject_toml(root, target, item_ids, record)

    # 4. Dockerfile (Agent Runtime Agent Gateway CA trust; Non-Root is delegated)
    _prepare_dockerfile(root, target, item_ids, record)

    # 5. agents-cli CI/CD evaluation gate (*-D1)
    _prepare_eval_and_cicd(root, project_name, target, item_ids, params, record)

    # 6. Cloud Run IAM authentication (CR-S1) — merges onto already-planned Terraform content
    _prepare_cloud_run_authn(root, target, item_ids, planned_files)

    # 7. Tier 3 Optional Production Add-ons (opt-in only: BigQuery Agent Analytics & Model Armor)
    _prepare_optional_addons(root, project_name, target, item_ids, params, record)

    patches: list[FilePatch] = []
    for rel_path, (new_content, applied) in sorted(planned_files.items()):
        orig_path = root / rel_path
        is_new = not orig_path.exists()
        orig_content = orig_path.read_text(encoding="utf-8") if not is_new else ""
        diff_txt = _make_diff(rel_path, orig_content, new_content)
        v3_applied = _flags_to_v3(sorted(set(applied)), v3_ids)
        if diff_txt and v3_applied:
            patches.append(
                FilePatch(
                    relative_path=rel_path,
                    is_new=is_new,
                    applied_items=v3_applied,
                    unified_diff=diff_txt,
                    updated_content=new_content,
                )
            )
    return patches


CICD_SCAFFOLD_CMD = "agents-cli scaffold enhance . --cicd-runner google_cloud_build"
CICD_INFRA_CMD = "agents-cli infra cicd"
_PIPELINE_FILES = (".cloudbuild/staging.yaml", ".github/workflows/staging.yaml")


def has_cicd_pipeline(project_dir: str | Path) -> bool:
    """True when an agents-cli CI/CD staging pipeline exists (Cloud Build or GitHub Actions)."""
    root = Path(project_dir)
    return any((root / rel).is_file() for rel in _PIPELINE_FILES)


def cicd_guidance_notices(project_dir: str | Path, v3_ids: list[str]) -> list[str]:
    """Guidance when COMMON-DO1 / COMMON-DO2 are selected but no CI/CD pipeline exists (the engine never creates one)."""
    has_d1 = "COMMON-DO1" in v3_ids or any(i.endswith("-D1") for i in v3_ids)
    has_d2 = "COMMON-DO2" in v3_ids or any(i.endswith("-D2") for i in v3_ids)
    if not (has_d1 or has_d2) or has_cicd_pipeline(project_dir):
        return []
    notices = [
        "COMMON-DO1 CI/CD 파이프라인(.cloudbuild/staging.yaml)이 없습니다. 엔진은 파이프라인을 생성하지 않으니 "
        f"먼저 `{CICD_SCAFFOLD_CMD}`로 생성하세요."
    ]
    if has_d2:
        notices.append(
            "COMMON-DO2 평가 데이터셋·설정은 생성했지만 평가 게이트는 건너뛰었습니다 — COMMON-DO1(파이프라인)을 먼저 만든 뒤 다시 적용하세요."
        )
    notices.append(
        f"`{CICD_INFRA_CMD}`(staging/prod CI/CD 인프라 생성)는 운영 환경 구성이므로 직접 수행하세요 — 체크리스트 범위 밖입니다."
    )
    return notices


def apply_patches(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
    custom_params: CustomParams | None = None,
    conditions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Applies selected checklist patches in-place and returns before/after scan reports."""
    root = Path(project_dir).resolve()
    before_report = scan_project(
        root, target_override=target_override, selected_items=selected_items, conditions=conditions
    )
    patches = preview_patches(
        root,
        target_override=target_override,
        selected_items=selected_items,
        custom_params=custom_params,
        conditions=conditions,
    )

    for patch in patches:
        dest = root / patch.relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(patch.updated_content, encoding="utf-8")
        if patch.relative_path.endswith(".sh"):
            dest.chmod(0o755)

    after_report = scan_project(
        root,
        target_override=before_report.deployment_target,
        selected_items=selected_items,
        conditions=conditions,
    )
    return {
        "before": before_report.to_dict(),
        "after": after_report.to_dict(),
        "applied_file_count": len(patches),
        "patches": [p.to_dict() for p in patches],
        "notices": cicd_guidance_notices(
            root, select_patch_items(root, before_report.deployment_target, selected_items, conditions)
        ),
    }


_ADVISORY_HOW: dict[str, str] = {
    "시작 CPU 부스트": "콜드스타트를 측정한 뒤 `resources.startup_cpu_boost = true` 적용 여부를 결정하세요 (cloud-run-basics).",
    "요청 타임아웃 · 최소 인스턴스": "부하 테스트(`tests/load_test/`)로 최대 응답 시간을 측정해 `timeout`과 `min_instance_count`를 정하세요.",
    "1세대 / 2세대 실행 환경": "콜드스타트 vs 성능·호환성 우선순위에 따라 `execution_environment`를 지정하세요.",
    "최소/최대 인스턴스 · 동시성": "트래픽 패턴에 맞춰 `deployment_spec`의 `min_instances` · `max_instances` · `container_concurrency`를 조정하세요.",
    "HPA · 리소스 요청/한도": "부하 테스트 결과로 `requests`/`limits`와 HPA `min_replicas`/`max_replicas`를 조정하세요 (gke-reliability).",
    "Secure Web Proxy (이그레스 프록시)": "Secure Web Proxy에 허용 도메인 목록을 두고 에이전트 이그레스를 프록시로 보내세요.",
    "Model Armor (프롬프트·응답 검사)": "Model Armor 템플릿(또는 Floor Settings)으로 프롬프트·응답 검사를 적용하세요.",
    "Cloud Armor · IAP (외부 노출 보호)": "외부 HTTPS 부하 분산기 + Cloud Armor 정책 또는 IAP로 앞단을 보호하세요.",
    "모델 용량 확보 (Provisioned Throughput 등)": "Provisioned Throughput 또는 Priority PayGo로 모델 용량을 확보하세요.",
}

_STATUS_BADGE = {"GREEN": "🟢 충족", "AMBER": "🟡 보완", "RED": "🔴 미충족", "NA": "⚪ N/A"}


def _status_badge(item: Any) -> str:
    if getattr(item, "grade", "") == "optional" and item.status == "NA":
        return "⚪ 선택 (미적용)"
    badge = _STATUS_BADGE.get(item.status, item.status)
    if getattr(item, "preview", False) and item.status != "NA":
        badge += " · Preview"
    return badge


def _build_user_decision_items(report: Any) -> list[dict[str, str]]:
    """Builds the target-aware advisory table (values · architecture · paid options) from catalog_v3.json."""
    from prod_ready.checklist import advisories_for

    rows: list[dict[str, str]] = []
    for adv in advisories_for(report.deployment_target):
        rows.append(
            {
                "topic": adv["title"],
                "when": adv["when"],
                "how": _ADVISORY_HOW.get(adv["title"], "워크로드 요건을 확인한 뒤 직접 설정하세요."),
            }
        )
    return rows


def _refs_md(item: Any) -> str:
    return " · ".join(f"[{r['label']}]({r['url']})" for r in getattr(item, "references", []) or [])


_OK_BADGE = "✅ 설치됨"
_MISSING_BADGE = "<strong style='color:#C5221F;'>❌ 미설치</strong>"


def _report_deps(report: Any) -> Any:
    """Platform-skill install status for reports (skill dirs only; agents-cli version is not needed here)."""
    from prod_ready.deps import check_dependencies

    return check_dependencies(report.deployment_target, report.project_dir, check_cli=False)


def _platform_md_banner(deps: Any) -> list[str]:
    from prod_ready.deps import PLATFORM_BANNER, platform_install_commands

    if not deps.platform_missing:
        return ["> ✅ 이 환경의 플랫폼 Skill이 모두 설치되어 있습니다. 일반 플랫폼 BP도 함께 점검하세요.", ""]
    return [
        f"> [!WARNING]",
        f"> **{PLATFORM_BANNER}** — 미설치: {', '.join(f'`{n}`' for n in deps.platform_missing)}",
        "> 설치 명령:",
        *[f"> `{c}`  " for c in platform_install_commands(deps)],
        "",
    ]


def generate_readiness_report(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
    conditions: dict[str, bool] | None = None,
    deps: Any | None = None,
) -> str:
    """Generates the Markdown readiness report (READINESS_REPORT.md) for checklist v3."""
    report = scan_project(
        project_dir, target_override=target_override, selected_items=selected_items, conditions=conditions
    )
    deps = deps or _report_deps(report)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# 🛡️ AI Agent Production-Readiness Report (Checklist v3)",
        "",
        f"- **Project Name**: `{report.project_name}`",
        f"- **Project Directory**: `{report.project_dir}`",
        f"- **Detected Scaffolding Mode**: `{report.project_mode}`",
        f"- **Deployment Runtime Target**: `{report.deployment_target}`",
        f"- **Overall Gate Verdict**: **{report.verdict}**",
        *(
            [f"- **⚠️ 플랫폼 Skill 미설치 {len(deps.platform_missing)}개** — 일반 플랫폼 BP 점검을 위해 설치를 강력히 권장합니다 (§5)"]
            if deps.platform_missing
            else []
        ),
        f"- **Compliance Summary**: **🟢 {report.green_count} / {report.total_count} 충족** | 🟡 {report.amber_count} 보완 | 🔴 {report.red_count} 미충족 | ⚪ N/A {report.na_count}",
        f"- **Preview 항목**: 미충족 {report.preview_open_count}개 (판정에 영향 없음)",
        *(
            [f"- **판정을 막는 항목**: `{', '.join(report.blocking_open_ids)}`"]
            if report.blocking_open_ids
            else []
        ),
        *(
            [f"- **선택 제외 항목**: `{', '.join(report.skipped_item_ids)}`"]
            if report.skipped_item_ids
            else []
        ),
        f"- **Generated At**: {now_str}",
        "",
        "---",
        "",
        "## 1. 워크로드 조건 판단",
        "",
        "> 조건부 항목은 아래 조건이 코드에서 확인될 때만 점검합니다. 해당 없으면 N/A로 표시합니다.",
        "",
        "| 조건 | 판단 | 근거 |",
        "| :--- | :---: | :--- |",
    ]
    for c in report.conditions:
        lines.append(f"| {c.label} | {'✅ 해당' if c.met else '— 해당 없음'} | {c.evidence} |")

    lines.extend(
        [
            "",
            "## 2. 범주별 요약",
            "",
            "| 범주 | 상태 | 충족 / 점검 | 🟢 | 🟡 | 🔴 | ⚪ N/A |",
            "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
        ]
    )
    for d in report.domains:
        d_badge = (
            "⚪ N/A"
            if d.total_count == 0
            else ("🟢 PASS" if d.red_count == 0 and d.amber_count == 0 else ("🟡 WARN" if d.red_count == 0 else "🔴 FAIL"))
        )
        lines.append(
            f"| **{d.domain_name}** | {d_badge} | **{d.green_count} / {d.total_count}** | {d.green_count} | {d.amber_count} | {d.red_count} | {d.na_count} |"
        )

    lines.extend(["", "---", "", f"## 3. 상세 점검 (`{report.deployment_target}` · {len(report.items)}개 항목)", ""])
    for d in report.domains:
        cat_items = [i for i in report.items if i.category == d.domain_id]
        if not cat_items:
            continue
        lines.extend([f"### {d.domain_name}", "", "| ID | 항목 | 등급 | 상태 | 근거 |", "| :--- | :--- | :---: | :---: | :--- |"])
        for item in cat_items:
            grade = item.severity + (f" ({item.condition_label})" if item.condition_label else "")
            lines.append(f"| `{item.id}` | **{item.title}** | {grade} | {_status_badge(item)} | {item.evidence} |")
        lines.append("")
        for item in cat_items:
            lines.append(f"#### [{item.id}] {item.title}")
            lines.append(f"- **Check**: {item.check}")
            lines.append(f"- **Best Practice**: {item.best_practice}")
            refs = _refs_md(item)
            if refs:
                lines.append(f"- **References**: {refs}")
            if item.verify_cmd:
                lines.append("```bash")
                lines.append(item.verify_cmd)
                lines.append("```")
            lines.append("")

    decision_items = _build_user_decision_items(report)
    lines.extend(
        [
            "---",
            "",
            "## 4. 권고 사항 (값 · 아키텍처 · 유료 선택형)",
            "",
            "> 체크리스트로 점검하지 않고, 워크로드 특성과 조직 정책에 따라 **필요한 항목만 선택해 직접 설정**하세요.",
            "",
            "| 선택 | 영역 | 언제 선택하나요? | 설정 가이드 |",
            "| :---: | :--- | :--- | :--- |",
        ]
    )
    for row in decision_items:
        lines.append(f"| ☐ | **{row['topic']}** | {row['when']} | {row['how']} |")
    for adv in report.advisories:
        if adv.startswith("[참고"):
            lines.append(f"\n- {adv}")

    lines.extend(
        [
            "",
            "---",
            "",
            "## 5. 플랫폼 Skill 참조 권장",
            "",
            *(_platform_md_banner(deps)),
            "> 헬스 프로브, 멀티존·PDB, Terraform IaC, 알림 정책, Secret 버전 고정, Non-Root, 사설 연결, NetworkPolicy, PSS 같은 "
            "일반 플랫폼 모범 사례는 이 체크리스트에서 점수화하지 않고 아래 플랫폼 Skill(google/skills)에 위임합니다.",
            "",
        ]
    )
    lines.extend(["| 플랫폼 Skill | 설치 상태 |", "| :--- | :---: |"])
    for st in deps.platform_status():
        lines.append(f"| [`{st['name']}`]({st['url']}) | {'✅ 설치됨' if st['installed'] else '❌ 미설치'} |")
    if report.delegated_bps:
        lines.extend(["", "| 위임된 모범 사례 | 참조 Skill |", "| :--- | :--- |"])
        for bp in report.delegated_bps:
            lines.append(f"| {bp.get('bp', '')} | {bp.get('skills', '')} |")
    lines.append("")
    return "\n".join(lines)


def create_zip_bundle(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
    custom_params: CustomParams | None = None,
) -> bytes:
    """Creates an in-memory .zip archive containing all patched/generated files and READINESS_REPORT.md/.html."""
    root = Path(project_dir).resolve()
    patches = preview_patches(
        root,
        target_override=target_override,
        selected_items=selected_items,
        custom_params=custom_params,
    )
    patch_map = {p.relative_path: p.updated_content for p in patches}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        existing_rels: set[str] = set()
        for fpath in root.rglob("*"):
            if fpath.is_file() and "__pycache__" not in fpath.parts and ".git" not in fpath.parts:
                rel = fpath.relative_to(root).as_posix()
                existing_rels.add(rel)
                if rel in patch_map:
                    zf.writestr(rel, patch_map[rel])
                else:
                    zf.write(fpath, arcname=rel)

        for rel, content in patch_map.items():
            if rel not in existing_rels:
                zf.writestr(rel, content)

        zf.writestr(
            "READINESS_REPORT.md",
            generate_readiness_report(root, target_override=target_override, selected_items=selected_items),
        )
        zf.writestr(
            "READINESS_REPORT.html",
            generate_html_report(root, target_override=target_override, selected_items=selected_items),
        )

    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. Base Terraform Preparation (variables.tf, apis.tf, iam.tf, storage.tf)
# ---------------------------------------------------------------------------


def _prepare_variables_tf(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    tf_rel = resolve_terraform_rel_dir(root)
    rel = f"{tf_rel}/variables.tf"
    path = root / rel
    service_path = root / f"{tf_rel}/service.tf"
    existing_service_tf = service_path.read_text(encoding="utf-8") if service_path.exists() else ""
    mcp_profile = detect_mcp_a2a_profile(root, existing_service_tf) if target == "cloud_run" else None
    want_cr_agent_identity = (params.cloud_run_identity_mode or "agent_identity").strip().lower() != "service_account"
    use_cr_agent_identity = (
        target == "cloud_run"
        and "C4.2" in item_ids
        and want_cr_agent_identity
        and not (mcp_profile and mcp_profile.is_mcp_server)
    )
    want_cr_agent_identity = (params.cloud_run_identity_mode or "agent_identity").strip().lower() != "service_account"
    use_agent_identity = (
        "C4.2" in item_ids
        and target != "gke"
        and not (target == "cloud_run" and (not want_cr_agent_identity or (mcp_profile and mcp_profile.is_mcp_server)))
    )

    if path.exists():
        content = path.read_text(encoding="utf-8")
        applied: list[str] = []
        if "C4.1" in item_ids:
            if '"roles/storage.admin"' in content:
                content = content.replace('"roles/storage.admin"', '"roles/storage.objectUser"')
                applied.append("C4.1")
            if target == "agent_runtime" and '"roles/cloudsql.client"' in content:
                content = re.sub(r'\s*"roles/cloudsql\.client",?\n', "\n", content)
                applied.append("C4.1")
        if "C5.1" in item_ids and '"roles/cloudtrace.agent"' in content and '"roles/monitoring.metricWriter"' not in content:
            content = content.replace(
                '"roles/cloudtrace.agent",',
                '"roles/cloudtrace.agent",\n    "roles/monitoring.metricWriter",',
                1,
            )
            applied.append("C5.1")
        if ("C2.1" in item_ids or "MEMBANK" in item_ids) and target in ("cloud_run", "gke"):
            if 'variable "agent_engine_id"' not in content:
                content = content.rstrip() + f'''

variable "agent_engine_id" {{
  type        = string
  description = "Agent Platform Reasoning Engine ID for Managed Sessions (VertexAiSessionService) & Memory Bank"
  default     = "{params.agent_engine_id}"
}}
'''
                applied.append("C2.1" if "C2.1" in item_ids else "MEMBANK")
            elif params.agent_engine_id:
                updated_var = re.sub(
                    r'(variable\s+"agent_engine_id"\s*\{[^}]*?\bdefault\s*=\s*")[^"]*(")',
                    rf'\g<1>{params.agent_engine_id}\2',
                    content,
                    count=1,
                    flags=re.DOTALL,
                )
                if updated_var != content:
                    content = updated_var
                    applied.append("C2.1" if "C2.1" in item_ids else "MEMBANK")
        if "C3.1" in item_ids and target != "gke" and 'variable "vpc_network_name"' not in content:
            content = content.rstrip() + f'''

variable "vpc_network_name" {{
  type        = string
  description = "Target VPC network name for private egress (supports Shared VPC / custom VPC)"
  default     = "{params.vpc_network_name}"
}}

variable "vpc_subnet_name" {{
  type        = string
  description = "Target VPC subnet name for Cloud Run Direct VPC Egress"
  default     = "{params.vpc_subnet_name}"
}}
'''
            applied.append("C3.1")
        if use_cr_agent_identity and 'variable "organization_id"' not in content:
            org_id_default = (params.organization_id or "").replace('"', '\\"')
            content = content.rstrip() + f'''

variable "organization_id" {{
  type        = string
  description = "Optional Google Cloud Organization ID for Cloud Run Agent Identity SPIFFE principal binding (leave empty for org-less projects)"
  default     = "{org_id_default}"
}}
'''
            applied.append("C4.2")
        if target == "agent_runtime" and "C4.4" in item_ids and 'variable "agent_gateway_enforcement_mode"' not in content:
            content = content.rstrip() + f'''

variable "agent_gateway_enforcement_mode" {{
  type        = string
  description = "Agent Gateway IAP/UAP V2 enforcement mode (DRY_RUN prevents HTTP 498 before essential APIs are allowlisted; switch to ENFORCED for production enforcement)"
  default     = "{params.agent_gateway_enforcement_mode}"
}}
'''
            applied.append("C4.4")
        record(rel, content, applied or ["C4.1"])
    else:
        storage_role = '"roles/storage.objectUser"' if "C4.1" in item_ids else '"roles/storage.admin"'
        roles = [
            '    "roles/aiplatform.user",',
            '    "roles/logging.logWriter",',
            '    "roles/cloudtrace.agent",',
            '    "roles/monitoring.metricWriter",',
            f"    {storage_role},",
            '    "roles/serviceusage.serviceUsageConsumer",',
        ]
        if target == "cloud_run":
            roles.append('    "roles/secretmanager.secretAccessor",')

        extra_vars = ""
        if target in ("cloud_run", "gke") and ("C2.1" in item_ids or "MEMBANK" in item_ids):
            extra_vars += f'''
variable "agent_engine_id" {{
  type        = string
  description = "Agent Platform Reasoning Engine ID for Managed Sessions (VertexAiSessionService) & Memory Bank"
  default     = "{params.agent_engine_id}"
}}
'''
        if "C3.1" in item_ids and target != "gke":
            extra_vars += f'''
variable "vpc_network_name" {{
  type        = string
  description = "Target VPC network name for private egress (supports Shared VPC / custom VPC)"
  default     = "{params.vpc_network_name}"
}}

variable "vpc_subnet_name" {{
  type        = string
  description = "Target VPC subnet name for Cloud Run Direct VPC Egress"
  default     = "{params.vpc_subnet_name}"
}}
'''
        if use_cr_agent_identity:
            org_id_default = (params.organization_id or "").replace('"', '\\"')
            extra_vars += f'''
variable "organization_id" {{
  type        = string
  description = "Optional Google Cloud Organization ID for Cloud Run Agent Identity SPIFFE principal binding (leave empty for org-less projects)"
  default     = "{org_id_default}"
}}
'''
        if target == "agent_runtime" and "C4.4" in item_ids:
            extra_vars += f'''
variable "agent_gateway_enforcement_mode" {{
  type        = string
  description = "Agent Gateway IAP/UAP V2 enforcement mode (DRY_RUN prevents HTTP 498 before essential APIs are allowlisted; switch to ENFORCED for production enforcement)"
  default     = "{params.agent_gateway_enforcement_mode}"
}}
'''

        roles_desc = (
            "Least-privilege IAM roles assigned to the Agent Identity (SPIFFE ID)"
            if use_agent_identity
            else "Least-privilege IAM roles assigned to the dedicated agent service account"
        )
        content = f'''variable "project_name" {{
  type        = string
  description = "Project name used as a base for resource naming"
  default     = "{project_name}"
}}

variable "project_id" {{
  type        = string
  description = "Google Cloud Project ID for resource deployment."
}}

variable "region" {{
  type        = string
  description = "Google Cloud region for resource deployment."
  default     = "us-east1"
}}

variable "app_sa_roles" {{
  description = "{roles_desc}"
  type        = list(string)
  default = [
{chr(10).join(roles)}
  ]
}}
{extra_vars}'''
        record(rel, content, ["C1.1", "C4.1", "C4.2"])


def _prepare_apis_and_iam_and_storage_tf(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    tf_rel = resolve_terraform_rel_dir(root)
    apis_rel = f"{tf_rel}/apis.tf"
    apis_path = root / apis_rel
    if not apis_path.exists():
        api_list = [
            "aiplatform.googleapis.com",
            "cloudbuild.googleapis.com",
            "cloudresourcemanager.googleapis.com",
            "iam.googleapis.com",
            "serviceusage.googleapis.com",
            "logging.googleapis.com",
            "cloudtrace.googleapis.com",
            "telemetry.googleapis.com",
        ]
        if target == "cloud_run":
            api_list.extend(["run.googleapis.com", "secretmanager.googleapis.com"])
        elif target == "gke":
            api_list.extend(
                [
                    "compute.googleapis.com",
                    "container.googleapis.com",
                    "artifactregistry.googleapis.com",
                ]
            )
            if "C5.3" not in item_ids:
                api_list.append("monitoring.googleapis.com")
        if "C4.2" in item_ids:
            api_list.extend(["agentregistry.googleapis.com", "apphub.googleapis.com"])
        formatted_apis = "\n".join(f'    "{a}",' for a in api_list)
        apis_content = f'''locals {{
  services = [
{formatted_apis}
  ]
}}

resource "google_project_service" "services" {{
  count              = length(local.services)
  project            = var.project_id
  service            = local.services[count.index]
  disable_on_destroy = false
}}

resource "google_project_service_identity" "vertex_sa" {{
  provider = google-beta
  project  = var.project_id
  service  = "aiplatform.googleapis.com"
}}
'''
        record(apis_rel, apis_content, ["C1.1", "C4.1", "C4.2"])
    elif "C4.2" in item_ids:
        existing_apis = apis_path.read_text(encoding="utf-8")
        missing = [
            api
            for api in ("agentregistry.googleapis.com", "apphub.googleapis.com")
            if api not in existing_apis
        ]
        if missing:
            inject_lines = "\n".join(f'    "{api}",' for api in missing)

            def _append_to_hcl_list(match: re.Match[str]) -> str:
                body = match.group(2).rstrip()
                if body and not body.endswith(","):
                    body += ","
                return f"{match.group(1)}{body}\n{inject_lines}\n{match.group(3)}"

            if re.search(r"services\s*=\s*\[", existing_apis):
                # Append to the END of local.services so existing count.index [0..N] resources are NEVER replaced!
                updated_apis = re.sub(
                    r"(services\s*=\s*\[)([\s\S]*?)(\n[ \t]*\])",
                    _append_to_hcl_list,
                    existing_apis,
                    count=1,
                )
            elif re.search(r"toset\(\s*\[", existing_apis):
                updated_apis = re.sub(
                    r"(toset\(\s*\[)([\s\S]*?)(\n[ \t]*\]\s*\))",
                    _append_to_hcl_list,
                    existing_apis,
                    count=1,
                )
            else:
                extra_block = (
                    '\n# [C4.2] Agent Registry & App Hub APIs\n'
                    'resource "google_project_service" "agent_registry_apis" {\n'
                    '  for_each = toset([\n'
                    f'{inject_lines}\n'
                    '  ])\n'
                    '  project            = var.project_id\n'
                    '  service            = each.value\n'
                    '  disable_on_destroy = false\n'
                    '}\n'
                )
                updated_apis = existing_apis.rstrip() + "\n" + extra_block
            record(apis_rel, updated_apis, ["C4.2"])

    iam_rel = f"{tf_rel}/iam.tf"
    iam_path = root / iam_rel
    service_path = root / f"{tf_rel}/service.tf"
    existing_service_tf = service_path.read_text(encoding="utf-8") if service_path.exists() else ""
    mcp_profile = detect_mcp_a2a_profile(root, existing_service_tf) if target == "cloud_run" else None
    want_cr_agent_identity = (params.cloud_run_identity_mode or "agent_identity").strip().lower() != "service_account"
    use_agent_identity = (
        "C4.2" in item_ids
        and target != "gke"
        and not (target == "cloud_run" and (not want_cr_agent_identity or (mcp_profile and mcp_profile.is_mcp_server)))
    )
    is_multi_env_service = bool(
        re.search(
            r'resource\s+"(?:google_vertex_ai_reasoning_engine|google_cloud_run_v2_service)"\s+"app"\s*\{[^}]*for_each',
            existing_service_tf,
        )
    )

    if target == "agent_runtime":
        agent_identity_iam_block = '''# [C4.2] Least-privilege IAM roles bound directly to the Agent Runtime Agent Identity (SPIFFE ID).
# Replaces custom runtime Service Account (identity_type = "AGENT_IDENTITY" is mutually exclusive with service_account).
resource "google_project_iam_member" "agent_identity_roles" {
  for_each   = toset(var.app_sa_roles)
  project    = var.project_id
  role       = each.value
  member     = "principal://${google_vertex_ai_reasoning_engine.app.spec[0].effective_identity}"
  depends_on = [resource.google_project_service.services, google_vertex_ai_reasoning_engine.app]
}
'''
    else:
        agent_identity_iam_block = '''# [C4.2] Least-privilege IAM roles bound directly to the Cloud Run Agent Identity (SPIFFE ID).
# Replaces custom runtime Service Account (IDENTITY_TYPE_AGENT_IDENTITY is mutually exclusive with service_account).
# Pass -var="organization_id=<ORG_ID>" for org-backed projects; leave empty ("") for org-less projects.
locals {
  agent_identity_trust_domain = (
    var.organization_id != ""
    ? "agents.global.org-${var.organization_id}.system.id.goog"
    : "agents.global.project-${data.google_project.project.number}.system.id.goog"
  )
  agent_identity_principal = "principal://${local.agent_identity_trust_domain}/resources/run/projects/${data.google_project.project.number}/locations/${var.region}/services/${google_cloud_run_v2_service.app.name}"
}

resource "google_project_iam_member" "agent_identity_roles" {
  for_each   = toset(var.app_sa_roles)
  project    = var.project_id
  role       = each.value
  member     = local.agent_identity_principal
  depends_on = [resource.google_project_service.services, google_cloud_run_v2_service.app]
}
'''

    if not iam_path.exists():
        if use_agent_identity:
            iam_content = (
                'data "google_project" "project" {\n'
                '  project_id = var.project_id\n'
                '}\n\n'
                + agent_identity_iam_block
            )
            record(iam_rel, iam_content, ["C1.1", "C4.1", "C4.2"])
        else:
            iam_content = '''data "google_project" "project" {
  project_id = var.project_id
}

# Dedicated least-privilege Service Account for the AI agent / MCP server (never use default Compute Engine SA)
resource "google_service_account" "app_sa" {
  account_id   = "${var.project_name}-app"
  display_name = "${var.project_name} Agent Service Account"
  project      = var.project_id
  depends_on   = [resource.google_project_service.services]
}

resource "google_project_iam_member" "app_sa_roles" {
  for_each   = toset(var.app_sa_roles)
  project    = var.project_id
  role       = each.value
  member     = "serviceAccount:${google_service_account.app_sa.email}"
  depends_on = [resource.google_project_service.services]
}
'''
            record(iam_rel, iam_content, ["C1.1", "C4.1"])
    elif use_agent_identity:
        existing_iam = iam_path.read_text(encoding="utf-8")
        if "agent_identity_roles" not in existing_iam and not is_multi_env_service:
            updated_iam = existing_iam.rstrip()
            if 'data "google_project" "project"' not in updated_iam:
                updated_iam = (
                    'data "google_project" "project" {\n'
                    '  project_id = var.project_id\n'
                    '}\n\n'
                    + updated_iam
                )
            updated_iam = (
                updated_iam
                + "\n\n# Note: Existing google_service_account.app_sa (if scaffolded) is preserved for outputs.tf compatibility,\n"
                + "# while live agent runtime permissions are bound directly to the Agent Identity (SPIFFE ID) below.\n"
                + agent_identity_iam_block
            )
            record(iam_rel, updated_iam, ["C4.2"])

    storage_rel = f"{tf_rel}/storage.tf"
    storage_path = root / storage_rel
    if not storage_path.exists():
        record(
            storage_rel,
            '''provider "google" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
}

resource "google_storage_bucket" "logs_data_bucket" {
  name                        = "${var.project_id}-${var.project_name}-logs"
  location                    = var.region
  project                     = var.project_id
  uniform_bucket_level_access = true

  depends_on = [resource.google_project_service.services]
}
''',
            ["C1.1"],
        )

    if target == "gke":
        providers_rel = f"{tf_rel}/providers.tf"
        if not (root / providers_rel).exists():
            record(
                providers_rel,
                '''terraform {
  required_version = ">= 1.0.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.28.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 7.28.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.37.0"
    }
  }
}

data "google_client_config" "default" {}

provider "kubernetes" {
  host                   = "https://${google_container_cluster.app.endpoint}"
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(google_container_cluster.app.master_auth[0].cluster_ca_certificate)
}
''',
                ["C1.1"],
            )


# ---------------------------------------------------------------------------
# 2. service.tf Preparation (Strictly Separated Cloud Run vs Agent Runtime vs GKE)
# ---------------------------------------------------------------------------


_RESOURCES_BLOCK_RE = re.compile(
    r"([ \t]*resources\s*\{(?:[^{}]|\{[^{}]*\})*?)([ \t]*\})",
    flags=re.DOTALL,
)
_STARTUP_CPU_BOOST_LINE_RE = re.compile(
    r"^[ \t]*startup_cpu_boost\s*=\s*(?:true|false)[^\n]*\n?",
    flags=re.MULTILINE,
)
_CPU_IDLE_LINE_RE = re.compile(
    r"^[ \t]*cpu_idle\s*=\s*(?:true|false)[^\n]*\n?",
    flags=re.MULTILINE,
)


def _ensure_startup_cpu_boost_in_resources(txt: str) -> str:
    """Places `startup_cpu_boost = true` strictly inside `template.containers.resources {}`.

    Also self-heals any misplaced `startup_cpu_boost` line sitting outside `resources {}`
    (which causes Terraform `Unsupported argument` on `google_cloud_run_v2_service`).
    """
    m = _RESOURCES_BLOCK_RE.search(txt)
    if m:
        before = _STARTUP_CPU_BOOST_LINE_RE.sub("", txt[: m.start()])
        block_head, block_close = m.group(1), m.group(2)
        after = _STARTUP_CPU_BOOST_LINE_RE.sub("", txt[m.end() :])
        close_indent = re.match(r"[ \t]*", block_close).group(0)  # type: ignore[union-attr]
        inner_indent = close_indent + "  "

        if not re.search(r"\bstartup_cpu_boost\s*=\s*true\b", block_head):
            if not block_head.endswith("\n"):
                block_head += "\n"
            block_head += f"{inner_indent}startup_cpu_boost = true\n"
        return before + block_head + block_close + after

    cleaned = _STARTUP_CPU_BOOST_LINE_RE.sub("", txt)
    return re.sub(
        r'([ \t]*)(image\s*=\s*"[^"]+")',
        r"\1\2\n\n\1resources {\n\1  startup_cpu_boost = true\n\1}",
        cleaned,
        count=1,
    )


def _inject_env_before(txt: str, anchor: str, env_name: str, env_expr: str) -> str:
    """Safely inserts a Terraform `env { ... }` block right before an existing `env {` containing `anchor`."""
    if f'name  = "{env_name}"' in txt or f'name = "{env_name}"' in txt:
        return txt
    pattern = re.compile(rf"([ \t]*env\s*\{{\s*\n[ \t]*name\s*=\s*\"{re.escape(anchor)}\")")
    m = pattern.search(txt)
    if not m:
        return txt
    indent = re.match(r"[ \t]*", m.group(1)).group(0)  # type: ignore[union-attr]
    block = f'{indent}env {{\n{indent}  name  = "{env_name}"\n{indent}  value = {env_expr}\n{indent}}}\n\n'
    return txt[: m.start()] + block + txt[m.start() :]


def _prepare_service_tf(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    tf_rel = resolve_terraform_rel_dir(root)
    rel = f"{tf_rel}/service.tf"
    path = root / rel

    # === TARGET A: AGENT RUNTIME ===
    if target == "agent_runtime":
        if path.exists():
            txt = path.read_text(encoding="utf-8")
            applied: list[str] = []

            if ("C4.2" in item_ids or "C4.4" in item_ids) and not re.search(r"provider\s*=\s*google-beta", txt):
                txt = txt.replace(
                    'resource "google_vertex_ai_reasoning_engine" "app" {',
                    'resource "google_vertex_ai_reasoning_engine" "app" {\n  provider     = google-beta',
                    1,
                )
                applied.extend([i for i in ("C4.2", "C4.4") if i in item_ids])

            if "C4.2" in item_ids and not re.search(r'identity_type\s*=\s*"AGENT_IDENTITY"', txt):
                id_block = (
                    '    # [C4.2] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (immutable at creation)\n'
                    '    # Grants default roles/aiplatform.agentDefaultAccess & roles/aiplatform.agentContextEditor automatically.\n'
                    '    identity_type   = "AGENT_IDENTITY"\n'
                    '    # service_account = google_service_account.app_sa.email # Replaced by per-agent SPIFFE ID'
                )
                if re.search(r'^[ \t]*service_account\s*=\s*.+$', txt, flags=re.MULTILINE):
                    txt = re.sub(
                        r'^[ \t]*service_account\s*=\s*.+$',
                        id_block,
                        txt,
                        count=1,
                        flags=re.MULTILINE,
                    )
                else:
                    txt = txt.replace(
                        'agent_framework = "google-adk"',
                        'agent_framework = "google-adk"\n' + id_block,
                        1,
                    )
                applied.append("C4.2")

            if "C3.1" in item_ids and "psc_interface_config" not in txt:
                psc_block = """
      # [C3.1] Wire Agent Runtime outbound traffic to PSC Interface Network Attachment
      psc_interface_config {
        network_attachment = google_compute_network_attachment.agent_psc_interface.id
      }
"""
                if "resource_limits" in txt:
                    txt = re.sub(
                        r"(resource_limits\s*=\s*\{[^}]*\})",
                        r"\1\n" + psc_block.rstrip(),
                        txt,
                        count=1,
                    )
                    applied.append("C3.1")

            agw_dir = (params.agent_gateway_direction or "egress").strip().lower()
            if "C4.4" in item_ids and agw_dir in ("egress", "both") and "agent_gateway_config" not in txt:
                agw_block = """
      # [C4.4] Central Agent Gateway (Agent-to-Anywhere Egress · requires identity_type = "AGENT_IDENTITY")
      # IAP/UAP V2 policy defaults to DRY_RUN in production_ready.tf to avoid HTTP 498 before API allowlisting.
      agent_gateway_config {
        agent_to_anywhere_config {
          agent_gateway = google_network_services_agent_gateway.egress_gateway.id
        }
      }
"""
                if "psc_interface_config" in txt:
                    txt = re.sub(
                        r"(psc_interface_config\s*\{[^}]*\})",
                        r"\1\n" + agw_block.rstrip(),
                        txt,
                        count=1,
                    )
                elif "resource_limits" in txt:
                    txt = re.sub(
                        r"(resource_limits\s*=\s*\{[^}]*\})",
                        r"\1\n" + agw_block.rstrip(),
                        txt,
                        count=1,
                    )
                applied.append("C4.4")

            if "C5.1" in item_ids:
                before = txt
                txt = _inject_env_before(
                    txt,
                    "OTEL_SEMCONV_STABILITY_OPT_IN",
                    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
                    '"NO_CONTENT"',
                )
                txt = _inject_env_before(
                    txt,
                    "OTEL_SEMCONV_STABILITY_OPT_IN",
                    "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS",
                    '"false"',
                )
                if txt != before:
                    applied.append("C5.1")


            if "C1.1" in item_ids and "ignore_changes" not in txt:
                lifecycle_block = """
  lifecycle {
    ignore_changes = [
      spec[0].container_spec,
      spec[0].source_code_spec,
      spec[0].deployment_spec,
    ]
  }
"""
                txt = txt.replace(
                    "  depends_on = [google_project_service.services]",
                    lifecycle_block + "\n  depends_on = [google_project_service.services]",
                )
                applied.append("C1.1")

            record(rel, txt, applied or ["C1.1"])
        else:
            # Mode A Prototype -> Agent Runtime service.tf (neutral defaults; sizing tuned via load test)
            provider_line = "  provider     = google-beta\n" if ("C4.2" in item_ids or "C4.4" in item_ids) else ""
            identity_block = (
                '    # [C4.2] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (immutable at creation)\n'
                '    identity_type   = "AGENT_IDENTITY"\n'
                '    # service_account = google_service_account.app_sa.email # Replaced by per-agent SPIFFE ID'
                if "C4.2" in item_ids
                else "    service_account = google_service_account.app_sa.email"
            )
            psc_cfg = (
                """
      # [C3.1] PSC Interface Private VPC Egress
      psc_interface_config {
        network_attachment = google_compute_network_attachment.agent_psc_interface.id
      }
"""
                if "C3.1" in item_ids
                else ""
            )
            agw_dir_fresh = (params.agent_gateway_direction or "egress").strip().lower()
            agw_cfg = (
                """
      # [C4.4] Central Agent Gateway (Agent-to-Anywhere Egress · UAP V2 starts in DRY_RUN)
      agent_gateway_config {
        agent_to_anywhere_config {
          agent_gateway = google_network_services_agent_gateway.egress_gateway.id
        }
      }
"""
                if ("C4.4" in item_ids and agw_dir_fresh in ("egress", "both"))
                else ""
            )
            env_blocks = [
                '      env {\n        name  = "LOGS_BUCKET_NAME"\n        value = google_storage_bucket.logs_data_bucket.name\n      }',
                f'      env {{\n        name  = "GOOGLE_CLOUD_LOCATION"\n        value = "global"\n      }}',
                '      env {\n        name  = "GOOGLE_GENAI_USE_VERTEXAI"\n        value = "True"\n      }',
                f'      env {{\n        name  = "OTEL_SERVICE_NAME"\n        value = "{project_name}"\n      }}',
                '      env {\n        name  = "OTEL_SEMCONV_STABILITY_OPT_IN"\n        value = "gen_ai_latest_experimental"\n      }',
                '      env {\n        name  = "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY"\n        value = "true"\n      }',
            ]
            if "C5.1" in item_ids:
                env_blocks.append(
                    '      env {\n        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\n        value = "NO_CONTENT"\n      }'
                )
                env_blocks.append(
                    '      env {\n        name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"\n        value = "false"\n      }'
                )

            record(
                rel,
                f'''# Generated by Production-Readiness Workbench v2.4 — Agent Runtime Target
# Sizing note: Uses official agents-cli neutral defaults; adjust cpu/memory/concurrency after load testing (`tests/load_test/`).

resource "google_vertex_ai_reasoning_engine" "app" {{
{provider_line}  display_name = var.project_name
  description  = "Enterprise Production Agent deployed via Terraform"
  region       = var.region
  project      = var.project_id

  spec {{
    agent_framework = "google-adk"
{identity_block}

    deployment_spec {{
      # Tune scaling & concurrency after running `tests/load_test/`
      min_instances         = 0
      max_instances         = 10
      container_concurrency = 8

      resource_limits = {{
        cpu    = "1"
        memory = "4Gi"
      }}
{psc_cfg}{agw_cfg}
{chr(10).join(env_blocks)}
    }}
  }}

  lifecycle {{
    ignore_changes = [
      spec[0].container_spec,
      spec[0].source_code_spec,
      spec[0].deployment_spec,
    ]
  }}

  depends_on = [google_project_service.services]
}}
''',
                ["C1.1", "C3.1", "C4.2", "C4.4", "C5.1"],
            )
        return

    # === TARGET C: GKE (Standard / Autopilot + Agent Sandbox / Agent Substrate + Agent Gateway for GKE) ===
    if target == "gke":
        pattern = params.gke_execution_pattern if params.gke_execution_pattern in ("standard_pods", "agent_sandbox", "agent_substrate") else "standard_pods"
        if pattern == "agent_sandbox":
            gke_c44_comment = (
                "# [C4.4 · GKE Agent Sandbox 격리 가이드]\n"
                "# 비신뢰 AI 생성 코드 실행 워크로드는 deployment/k8s/agent-sandbox.yaml (kubernetes-sigs/agent-sandbox,\n"
                "# gVisor/microVM + SandboxWarmPool + Pod Snapshots + Zero-Metadata/RFC1918 Egress NetworkPolicy)을 적용하세요.\n"
                "# ※ Agent Gateway for GKE는 현재 표준 GKE Pod 전용이므로, Agent Sandbox Egress는 Dataplane V2 NetworkPolicy + SWP로 통제합니다.\n"
            )
        elif pattern == "agent_substrate":
            gke_c44_comment = (
                "# [C4.4 · GKE Agent Substrate 고밀도 가이드]\n"
                "# 대규모 장기 유휴 에이전트는 deployment/k8s/agent-substrate.yaml (ate.dev/v1alpha1 WorkerPool + kubectl ate ActorTemplate,\n"
                "# 10x 집적도 + <500ms suspend/resume + Filestore 볼륨 + 내장 Egress Gateway 크리덴셜 주입)을 적용하세요.\n"
                "# ※ 제약: Agent Substrate는 GKE Standard >= 1.37 전용(Autopilot · E2 머신타입 미지원)이며,\n"
                "#   Agent Gateway for GKE 미지원에 따라 Dataplane V2 Default-Deny Egress + SWP(또는 내장 EgressPolicy)로 Egress를 통제합니다.\n"
            )
        else:
            gke_c44_comment = (
                "# [C4.4 · Agent Gateway for GKE 가이드 (표준 GKE Pod · GKE >= 1.37.2-gke.1042000 Rapid)]\n"
                "# 인바운드(Client-to-Agent: Gateway + HTTPRoute + GCPAgentGateway + GCPAuthzPolicy L7 + GCPExtensionBinding Model Armor) 및\n"
                "# 아웃바운드(Agent-to-Anywhere: networking.gke.io/dataplane-mode=ambient + GCPBackend + GCPEgressPolicy -> SWP/IAP/Model Armor)\n"
                "# 매니페스트가 deployment/k8s/agent-gateway.yaml에 생성되었습니다.\n"
                "# ※ 비신뢰 코드 실행(Agent Sandbox) 또는 초고밀도 유휴(Agent Substrate) 필요 시 references/gke-hardening.md를 참고하세요.\n"
            )

        gke_egress_netpol = '''
# [C3.1] Dataplane V2 Default-Deny Egress NetworkPolicy for Standard GKE ADK Pods
# Explicitly allows ONLY required GKE system paths + HTTPS (443):
#   1. kube-system CoreDNS/kube-dns (UDP/TCP 53 & post-DNAT 5353)
#   2. NodeLocal DNSCache (169.254.20.10/32 UDP/TCP 53 — mandatory on Autopilot & Standard >= 1.34.1)
#   3. GKE Cloud DNS (169.254.169.254/32 UDP/TCP 53) & Dataplane V2 Metadata Server (169.254.169.254/32 TCP 80, 8080)
#   4. Non-DPv2 GKE Metadata Server fallback (169.254.169.252/32 TCP 987, 988)
#   5. Outbound HTTPS (TCP 443) for Google Cloud APIs & authorized enterprise endpoints
# Optional: Add TCP 5432 (Cloud SQL/AlloyDB), 6379 (Memorystore Redis), or 15008 (Ambient HBONE) if used.
# Note: For untrusted code execution sandboxes, use the Zero-Metadata/Zero-RFC1918 NetworkPolicy in deployment/k8s/agent-sandbox.yaml.
resource "kubernetes_network_policy_v1" "agent_egress_guard" {
  metadata {
    name      = "${var.project_name}-egress-guard"
    namespace = kubernetes_namespace_v1.app.metadata[0].name
  }

  spec {
    pod_selector {
      match_labels = {
        app = var.project_name
      }
    }

    policy_types = ["Egress"]

    # 1. kube-system DNS (kube-dns / CoreDNS including post-DNAT port 5353)
    egress {
      to {
        namespace_selector {
          match_labels = {
            "kubernetes.io/metadata.name" = "kube-system"
          }
        }
      }
      ports {
        protocol = "UDP"
        port     = "53"
      }
      ports {
        protocol = "TCP"
        port     = "53"
      }
      ports {
        protocol = "UDP"
        port     = "5353"
      }
      ports {
        protocol = "TCP"
        port     = "5353"
      }
    }

    # 2. NodeLocal DNSCache (mandatory on GKE Autopilot & Standard >= 1.34.1)
    egress {
      to {
        ip_block {
          cidr = "169.254.20.10/32"
        }
      }
      ports {
        protocol = "UDP"
        port     = "53"
      }
      ports {
        protocol = "TCP"
        port     = "53"
      }
    }

    # 3. GKE Cloud DNS (53) + Dataplane V2 Workload Identity Metadata Server (TCP 80, 8080)
    egress {
      to {
        ip_block {
          cidr = "169.254.169.254/32"
        }
      }
      ports {
        protocol = "UDP"
        port     = "53"
      }
      ports {
        protocol = "TCP"
        port     = "53"
      }
      ports {
        protocol = "TCP"
        port     = "80"
      }
      ports {
        protocol = "TCP"
        port     = "8080"
      }
    }

    # 4. Non-Dataplane V2 GKE Metadata Server compatibility (TCP 987, 988)
    egress {
      to {
        ip_block {
          cidr = "169.254.169.252/32"
        }
      }
      ports {
        protocol = "TCP"
        port     = "987"
      }
      ports {
        protocol = "TCP"
        port     = "988"
      }
    }

    # 5. Outbound HTTPS (443) for Google Cloud APIs & approved enterprise HTTPS endpoints
    egress {
      ports {
        protocol = "TCP"
        port     = "443"
      }
    }
  }

  depends_on = [kubernetes_namespace_v1.app]
}
'''

        if path.exists():
            txt = path.read_text(encoding="utf-8")
            applied = []

            # [C4.4] Header advisory (standard_pods / agent_sandbox / agent_substrate)
            if "C4.4" in item_ids and "# [C4.4" not in txt:
                compat_warn = ""
                if pattern == "agent_substrate" and re.search(r"\benable_autopilot\s*=\s*true\b", txt):
                    compat_warn = (
                        "# ⚠️ [호환성 경고] 현재 클러스터는 GKE Autopilot(enable_autopilot = true)입니다.\n"
                        "# Agent Substrate는 GKE Standard >= 1.37 전용이므로, 인플레이스 전환은 불가하며\n"
                        "# 별도의 GKE Standard >= 1.37 클러스터(비-E2 노드 풀)를 생성한 뒤 deployment/k8s/agent-substrate.yaml을 적용하세요.\n"
                    )
                txt = gke_c44_comment + compat_warn + "\n" + txt
                applied.append("C4.4")

            # [C3.1] Enable Private Google Access on gke_subnet + append Dataplane V2 Egress NetworkPolicy
            if "C3.1" in item_ids:
                before_c31 = txt
                if "private_ip_google_access" not in txt:
                    txt = re.sub(
                        r'(resource\s+"google_compute_subnetwork"\s+"gke_subnet"\s*\{[\s\S]*?ip_cidr_range\s*=\s*"[^"]+")',
                        r'\1\n  private_ip_google_access = true # [C3.1] Private Google Access for Gemini / Agent Platform / Artifact Registry',
                        txt,
                        count=1,
                    )
                if "kubernetes_network_policy_v1" not in txt:
                    txt = txt.rstrip() + "\n" + gke_egress_netpol
                if txt != before_c31:
                    applied.append("C3.1")

            # [C4.3] Namespace PSS restricted + Pod & Container security_context (+ optional gVisor)
            if "C4.3" in item_ids:
                before_c43 = txt
                if "pod-security.kubernetes.io/enforce" not in txt:
                    txt = re.sub(
                        r'(resource\s+"kubernetes_namespace_v1"\s+"app"\s*\{\s*\n\s*metadata\s*\{\s*\n\s*name\s*=\s*(?:var\.project_name|"[^"]+"))',
                        (
                            r'\1\n'
                            '    # [C4.3] Pod Security Standards (PSS) Restricted enforcement\n'
                            '    labels = {\n'
                            '      "pod-security.kubernetes.io/enforce"         = "restricted"\n'
                            '      "pod-security.kubernetes.io/enforce-version" = "latest"\n'
                            '      "pod-security.kubernetes.io/audit"           = "restricted"\n'
                            '      "pod-security.kubernetes.io/warn"            = "restricted"\n'
                            '    }'
                        ),
                        txt,
                        count=1,
                    )
                if params.gke_enable_gvisor and 'runtime_class_name' not in txt:
                    txt = re.sub(
                        r'(service_account_name\s*=\s*kubernetes_service_account_v1\.app\.metadata\[0\]\.name)',
                        r'\1\n        runtime_class_name   = "gvisor" # [C4.3] GKE Sandbox (gVisor) kernel isolation',
                        txt,
                        count=1,
                    )
                if "run_as_non_root" not in txt:
                    pod_sec_block = (
                        "        # [C4.3] PSS Restricted Pod Security Context (UID 10001 non-root + RuntimeDefault seccomp)\n"
                        "        security_context {\n"
                        "          run_as_non_root = true\n"
                        "          run_as_user     = 10001\n"
                        "          run_as_group    = 10001\n"
                        "          fs_group        = 10001\n"
                        "          seccomp_profile {\n"
                        '            type = "RuntimeDefault"\n'
                        "          }\n"
                        "        }"
                    )
                    txt = re.sub(
                        r'(service_account_name\s*=\s*kubernetes_service_account_v1\.app\.metadata\[0\]\.name(?:\n[ \t]*runtime_class_name[^\n]*)?)',
                        r'\1\n\n' + pod_sec_block,
                        txt,
                        count=1,
                    )
                if "allow_privilege_escalation" not in txt:
                    container_sec_block = (
                        "\n          # [C4.3] PSS Restricted Container Security Context\n"
                        "          security_context {\n"
                        "            allow_privilege_escalation = false\n"
                        "            read_only_root_filesystem  = false # Set true if app writes only to emptyDir /tmp\n"
                        "            capabilities {\n"
                        '              drop = ["ALL"]\n'
                        "            }\n"
                        "          }"
                    )
                    txt = re.sub(
                        r'(port\s*\{[^}]*container_port\s*=\s*\d+[^}]*\})',
                        r'\1' + container_sec_block,
                        txt,
                        count=1,
                    )
                if txt != before_c43:
                    applied.append("C4.3")

            # [C4.2] Official GKE Agent Registry label (Deployment) + Agent Identity SPIFFE annotation (Pod template)
            if "C4.2" in item_ids:
                before_c42 = txt
                dep_marker = 'resource "kubernetes_deployment_v1" "app"'
                idx = txt.find(dep_marker)
                if idx != -1:
                    # Scope replacement strictly to kubernetes_deployment_v1.app (stop before next top-level resource)
                    next_res = re.search(r'\nresource\s+"', txt[idx + len(dep_marker):])
                    end_idx = (idx + len(dep_marker) + next_res.start()) if next_res else len(txt)
                    head, dep_blk, tail = txt[:idx], txt[idx:end_idx], txt[end_idx:]

                    if "registry.gke.io/functional-type" not in dep_blk:
                        dep_blk = re.sub(
                            r'(metadata\s*\{[\s\S]*?\blabels\s*=\s*\{\s*\n)([ \t]*)(app\s*=\s*var\.project_name)',
                            r'\1\2\3\n\2"registry.gke.io/functional-type" = "AGENT"',
                            dep_blk,
                            count=1,
                        )
                    if "iam.gke.io/spiffe-identity-type" not in dep_blk:
                        # Inject official Pod template annotation for GKE Agent Identity (SPIFFE)
                        if re.search(r'template\s*\{\s*\n[ \t]*metadata\s*\{[\s\S]*?annotations\s*=\s*\{', dep_blk):
                            dep_blk = re.sub(
                                r'(template\s*\{\s*\n[ \t]*metadata\s*\{[\s\S]*?annotations\s*=\s*\{\s*\n)',
                                r'\1          "iam.gke.io/spiffe-identity-type" = "agent-identity"\n',
                                dep_blk,
                                count=1,
                            )
                        else:
                            dep_blk = re.sub(
                                r'(template\s*\{\s*\n[ \t]*metadata\s*\{[\s\S]*?labels\s*=\s*\{[^}]*\})',
                                (
                                    r'\1\n'
                                    '        annotations = {\n'
                                    '          # [C4.2] Official GKE Agent Identity (SPIFFE) annotation\n'
                                    '          "iam.gke.io/spiffe-identity-type" = "agent-identity"\n'
                                    '          # Optional for bound GCP API tokens / direct A2A mTLS:\n'
                                    '          # "iam.gke.io/identity"             = "spiffe://agents.global.proj-PROJECT_NUMBER.system.id.goog/*"\n'
                                    '          # "iam.gke.io/inject-podcertificates" = "true"\n'
                                    '        }'
                                ),
                                dep_blk,
                                count=1,
                            )
                    txt = head + dep_blk + tail
                if txt != before_c42:
                    applied.append("C4.2")

            # [C1.2] Multi-Zone HA Pod Spread (topology_spread_constraint across topology.kubernetes.io/zone)
            if "C1.2" in item_ids and "topology_spread_constraint" not in txt:
                before_spread = txt
                spread_block = (
                    "        # [C1.2] Multi-Zone HA Pod Spread across GCE zones\n"
                    "        topology_spread_constraint {\n"
                    "          max_skew           = 1\n"
                    '          topology_key       = "topology.kubernetes.io/zone"\n'
                    '          when_unsatisfiable = "ScheduleAnyway"\n'
                    "          label_selector {\n"
                    "            match_labels = {\n"
                    "              app = var.project_name\n"
                    "            }\n"
                    "          }\n"
                    "        }\n\n"
                )
                txt = re.sub(
                    r"(^[ \t]*container\s*\{)",
                    spread_block + r"\1",
                    txt,
                    count=1,
                    flags=re.MULTILINE,
                )
                if txt != before_spread:
                    applied.append("C1.2")

            # [GKE-O1] Graceful drain for long responses: terminationGracePeriod + preStop + readiness probe
            # (tcp_socket probes are upgraded to HTTP GET so readiness reflects the ADK app, not just the port)
            drain_flag = "GKE_DRAIN" if "GKE_DRAIN" in item_ids else ("C1.2" if "C1.2" in item_ids else "")
            if drain_flag:
                before_c12 = txt
                if "tcp_socket" in txt:
                    txt = re.sub(
                        r'([ \t]*)tcp_socket\s*\{\s*\n[ \t]*port\s*=\s*(\d+)\s*\n[ \t]*\}',
                        r'\1http_get {\n\1  path = "/list-apps"\n\1  port = \2\n\1}',
                        txt,
                    )
                grace_sec = max(60, int(params.gke_termination_grace_seconds))
                if "termination_grace_period_seconds" not in txt:
                    grace_line = f"        termination_grace_period_seconds = {grace_sec} # [C1.2] Drain active SSE/A2A agent streams before SIGKILL\n\n"
                    txt = re.sub(
                        r"(^[ \t]*container\s*\{)",
                        grace_line + r"\1",
                        txt,
                        count=1,
                        flags=re.MULTILINE,
                    )
                if "pre_stop" not in txt:
                    pre_stop_block = (
                        "          # [C1.2] Wait for GKE LB / Gateway EndpointSlice deregistration before SIGTERM hits uvicorn\n"
                        "          lifecycle {\n"
                        "            pre_stop {\n"
                        "              exec {\n"
                        '                command = ["/bin/sh", "-c", "sleep 10"]\n'
                        "              }\n"
                        "            }\n"
                        "          }\n\n"
                    )
                    if not re.search(r"^[ \t]*(startup_probe|readiness_probe)\s*\{", txt, flags=re.MULTILINE):
                        pre_stop_block += (
                            "          readiness_probe {\n"
                            "            http_get {\n"
                            '              path = "/list-apps"\n'
                            "              port = 8080\n"
                            "            }\n"
                            "            initial_delay_seconds = 10\n"
                            "            period_seconds        = 10\n"
                            "          }\n\n"
                        )
                    if re.search(r"^[ \t]*startup_probe\s*\{", txt, flags=re.MULTILINE):
                        txt = re.sub(
                            r"(^[ \t]*startup_probe\s*\{)",
                            pre_stop_block + r"\1",
                            txt,
                            count=1,
                            flags=re.MULTILINE,
                        )
                    else:
                        txt = re.sub(
                            r"(^[ \t]*resources\s*\{(?:[^{}]|\{[^{}]*\})*\})",
                            r"\1\n\n" + pre_stop_block.rstrip("\n"),
                            txt,
                            count=1,
                            flags=re.MULTILINE | re.DOTALL,
                        )
                if txt != before_c12:
                    applied.append(drain_flag)

            # [C2.1 / MEMBANK] Managed Sessions & Memory Bank env vars (AGENT_ENGINE_SESSION_ID + GOOGLE_CLOUD_AGENT_ENGINE_LOCATION)
            if "C2.1" in item_ids or "MEMBANK" in item_ids:
                before_c21 = txt
                if "GOOGLE_CLOUD_AGENT_ENGINE_ID" in txt and "AGENT_ENGINE_SESSION_ID" not in txt:
                    txt = txt.replace('"GOOGLE_CLOUD_AGENT_ENGINE_ID"', '"AGENT_ENGINE_SESSION_ID"')
                else:
                    txt = _inject_env_before(
                        txt,
                        "LOGS_BUCKET_NAME",
                        "AGENT_ENGINE_SESSION_ID",
                        "var.agent_engine_id",
                    )
                if "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION" not in txt:
                    txt = _inject_env_before(
                        txt,
                        "LOGS_BUCKET_NAME",
                        "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION",
                        "var.region",
                    )
                if txt != before_c21:
                    applied.append("C2.1" if "C2.1" in item_ids else "MEMBANK")

            # [C5.1] Unified OTel GenAI Semconv + NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false
            if "C5.1" in item_ids:
                before_c51 = txt
                txt = _inject_env_before(
                    txt,
                    "LOGS_BUCKET_NAME",
                    "OTEL_SEMCONV_STABILITY_OPT_IN",
                    '"gen_ai_latest_experimental"',
                )
                txt = _inject_env_before(
                    txt,
                    "OTEL_SEMCONV_STABILITY_OPT_IN",
                    "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
                    '"NO_CONTENT"',
                )
                txt = _inject_env_before(
                    txt,
                    "OTEL_SEMCONV_STABILITY_OPT_IN",
                    "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS",
                    '"false"',
                )
                if txt != before_c51:
                    applied.append("C5.1")

            record(rel, txt, applied or ["C1.1"])
        else:
            # Mode A Prototype -> Clean GKE service.tf (supports Autopilot default & Standard mode!)
            # GKE-O2 (idle suspend/resume) needs Agent Substrate → GKE Standard >= 1.37 + non-E2 worker pool
            substrate_nodes = pattern == "agent_substrate" or "SUBSTRATE" in item_ids
            use_standard = params.gke_cluster_mode == "standard" or substrate_nodes
            gvisor_node_cfg = (
                """
    sandbox_config {
      sandbox_type = "gvisor"
    }"""
                if params.gke_enable_gvisor
                else ""
            )
            if use_standard:
                node_machine_type = "n2-standard-4" if substrate_nodes else "e2-standard-4"
                node_labels_cfg = (
                    '\n    labels = {\n      "agent-substrate.gke.io/pool" = "workers"\n    }'
                    if substrate_nodes
                    else ""
                )
                cluster_block = f'''# GKE Standard Regional Cluster (Dataplane V2 + Workload Identity + Shielded Nodes + GMP)
# Note: Required for Agent Substrate (GKE Standard >= 1.37, non-E2 nodes) or custom GPU/TPU/gVisor node pools.
resource "google_container_cluster" "app" {{
  name     = var.project_name
  location = var.region
  project  = var.project_id

  network    = google_compute_network.gke_network.name
  subnetwork = google_compute_subnetwork.gke_subnet.name

  remove_default_node_pool = true
  initial_node_count       = 1
  datapath_provider        = "ADVANCED_DATAPATH" # Dataplane V2 (eBPF NetworkPolicy + logging)
  enable_shielded_nodes    = true

  release_channel {{
    channel = "REGULAR"
  }}

  workload_identity_config {{
    workload_pool = "${{var.project_id}}.svc.id.goog"
  }}

  monitoring_config {{
    managed_prometheus {{
      enabled = true
    }}
  }}

  private_cluster_config {{
    enable_private_nodes    = true
    enable_private_endpoint = false
  }}

  ip_allocation_policy {{}}

  deletion_protection = false

  depends_on = [resource.google_project_service.services]
}}

resource "google_container_node_pool" "agent_nodes" {{
  name     = "${{var.project_name}}-pool"
  project  = var.project_id
  location = var.region
  cluster  = google_container_cluster.app.name

  autoscaling {{
    min_node_count = 1
    max_node_count = 5
  }}

  node_config {{
    machine_type = "{node_machine_type}"
    image_type   = "COS_CONTAINERD"
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]{node_labels_cfg}
    shielded_instance_config {{
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }}
    workload_metadata_config {{
      mode = "GKE_METADATA"
    }}{gvisor_node_cfg}
  }}
}}'''
            else:
                cluster_block = '''# GKE Autopilot Regional Cluster (Dataplane V2, Workload Identity, Shielded Nodes & GMP built-in)
resource "google_container_cluster" "app" {
  name     = var.project_name
  location = var.region
  project  = var.project_id

  network    = google_compute_network.gke_network.name
  subnetwork = google_compute_subnetwork.gke_subnet.name

  enable_autopilot = true

  release_channel {
    channel = "REGULAR"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  ip_allocation_policy {}

  deletion_protection = false

  depends_on = [resource.google_project_service.services]
}'''

            pga_line = "\n  private_ip_google_access = true # [C3.1] Private Google Access" if "C3.1" in item_ids else ""
            pss_block = (
                """
    # [C4.3] Pod Security Standards (PSS) Restricted enforcement
    labels = {
      "pod-security.kubernetes.io/enforce"         = "restricted"
      "pod-security.kubernetes.io/enforce-version" = "latest"
      "pod-security.kubernetes.io/audit"           = "restricted"
      "pod-security.kubernetes.io/warn"            = "restricted"
    }"""
                if "C4.3" in item_ids
                else ""
            )
            reg_labels = (
                '\n      "registry.gke.io/functional-type" = "AGENT"'
                if "C4.2" in item_ids
                else ""
            )
            pod_spiffe_annotations = (
                """
        annotations = {
          # [C4.2] Official GKE Agent Identity (SPIFFE) annotation
          "iam.gke.io/spiffe-identity-type" = "agent-identity"
        }"""
                if "C4.2" in item_ids
                else ""
            )
            dep_depends_on = (
                "[kubernetes_namespace_v1.app, google_container_node_pool.agent_nodes]"
                if use_standard
                else "[kubernetes_namespace_v1.app]"
            )
            pod_gvisor_line = '\n        runtime_class_name   = "gvisor" # [C4.3] GKE Sandbox (gVisor) kernel isolation' if ("C4.3" in item_ids and params.gke_enable_gvisor) else ""
            pod_sec = (
                """
        # [C4.3] PSS Restricted Pod Security Context
        security_context {
          run_as_non_root = true
          run_as_user     = 10001
          run_as_group    = 10001
          fs_group        = 10001
          seccomp_profile {
            type = "RuntimeDefault"
          }
        }"""
                if "C4.3" in item_ids
                else ""
            )
            cont_sec = (
                """
          # [C4.3] PSS Restricted Container Security Context
          security_context {
            allow_privilege_escalation = false
            read_only_root_filesystem  = false # Set true if app writes only to emptyDir /tmp
            capabilities {
              drop = ["ALL"]
            }
          }"""
                if "C4.3" in item_ids
                else ""
            )
            grace_sec = max(60, int(params.gke_termination_grace_seconds))
            pod_grace_line = (
                f"\n        termination_grace_period_seconds = {grace_sec} # [GKE-O1] Drain active SSE/A2A agent streams before SIGKILL"
                if ("C1.2" in item_ids or "GKE_DRAIN" in item_ids)
                else ""
            )
            pod_spread_block = (
                """
        # [C1.2] Multi-Zone HA Pod Spread across GCE zones
        topology_spread_constraint {
          max_skew           = 1
          topology_key       = "topology.kubernetes.io/zone"
          when_unsatisfiable = "ScheduleAnyway"
          label_selector {
            match_labels = {
              app = var.project_name
            }
          }
        }"""
                if "C1.2" in item_ids
                else ""
            )
            drain_gke = (
                """
          # [GKE-O1] Wait for GKE LB / Gateway EndpointSlice deregistration before SIGTERM hits uvicorn
          lifecycle {
            pre_stop {
              exec {
                command = ["/bin/sh", "-c", "sleep 10"]
              }
            }
          }

          readiness_probe {
            http_get {
              path = "/list-apps"
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }"""
                if ("C1.2" in item_ids or "GKE_DRAIN" in item_ids)
                else ""
            )
            # Startup / liveness health probes are delegated to platform skills (gke-reliability).
            probes_gke = drain_gke + (
                """

          startup_probe {
            http_get {
              path = "/list-apps"
              port = 8080
            }
            initial_delay_seconds = 10
            period_seconds        = 10
            failure_threshold     = 18
          }

          liveness_probe {
            http_get {
              path = "/list-apps"
              port = 8080
            }
            initial_delay_seconds = 15
            period_seconds        = 20
          }"""
                if "C1.2" in item_ids
                else ""
            )
            gke_envs = [
                '          env {\n            name  = "GOOGLE_CLOUD_PROJECT"\n            value = var.project_id\n          }',
                f'          env {{\n            name  = "GOOGLE_CLOUD_LOCATION"\n            value = "global"\n          }}',
                '          env {\n            name  = "GOOGLE_GENAI_USE_VERTEXAI"\n            value = "True"\n          }',
                '          env {\n            name  = "LOGS_BUCKET_NAME"\n            value = google_storage_bucket.logs_data_bucket.name\n          }',
            ]
            if "C2.1" in item_ids:
                gke_envs.append(
                    '          env {\n            name  = "AGENT_ENGINE_SESSION_ID"\n            value = var.agent_engine_id\n          }'
                )
                gke_envs.append(
                    '          env {\n            name  = "GOOGLE_CLOUD_AGENT_ENGINE_LOCATION"\n            value = var.region\n          }'
                )
            if "C5.1" in item_ids:
                gke_envs.append(
                    f'          env {{\n            name  = "OTEL_SERVICE_NAME"\n            value = "{project_name}"\n          }}'
                )
                gke_envs.append(
                    '          env {\n            name  = "OTEL_SEMCONV_STABILITY_OPT_IN"\n            value = "gen_ai_latest_experimental"\n          }'
                )
                gke_envs.append(
                    '          env {\n            name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\n            value = "NO_CONTENT"\n          }'
                )
                gke_envs.append(
                    '          env {\n            name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"\n            value = "false"\n          }'
                )

            # Multi-zone HA (HPA / PDB) is delegated to platform skills (gke-reliability / gke-productionize).
            hpa_pdb_block = (
                '''resource "kubernetes_horizontal_pod_autoscaler_v2" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      app = var.project_name
    }
  }
  spec {
    scale_target_ref {
      api_version = "apps/v1"
      kind        = "Deployment"
      name        = var.project_name
    }
    min_replicas = 2
    max_replicas = 10
    metric {
      type = "Resource"
      resource {
        name = "cpu"
        target {
          type                = "Utilization"
          average_utilization = 70
        }
      }
    }
  }
}

resource "kubernetes_pod_disruption_budget_v1" "app" {
  metadata {
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {
      app = var.project_name
    }
  }
  spec {
    min_available = 1
    selector {
      match_labels = {
        app = var.project_name
      }
    }
  }
}

'''
                if "C1.2" in item_ids
                else ""
            )
            c44_hdr = (gke_c44_comment + "\n") if "C4.4" in item_ids else ""
            netpol_tail = gke_egress_netpol if "C3.1" in item_ids else ""

            record(
                rel,
                f'''{c44_hdr}# Generated by Production-Readiness Workbench v2.4 — GKE Target (100% compatible with google-agents-cli v1.5.0)
# Sizing note: Uses official agents-cli neutral Pod defaults; tune requests/limits & HPA after load testing (`tests/load_test/`).

resource "google_compute_network" "gke_network" {{
  name                    = "${{var.project_name}}-network"
  project                 = var.project_id
  auto_create_subnetworks = false

  depends_on = [resource.google_project_service.services]
}}

resource "google_compute_subnetwork" "gke_subnet" {{
  name          = "${{var.project_name}}-subnet"
  project       = var.project_id
  region        = var.region
  network       = google_compute_network.gke_network.id
  ip_cidr_range = "10.0.0.0/20"{pga_line}
}}

resource "google_compute_firewall" "allow_internal" {{
  name    = "${{var.project_name}}-allow-internal"
  project = var.project_id
  network = google_compute_network.gke_network.name

  allow {{
    protocol = "tcp"
  }}
  allow {{
    protocol = "udp"
  }}
  allow {{
    protocol = "icmp"
  }}

  source_ranges = ["10.0.0.0/20"]
}}

{cluster_block}

resource "google_compute_router" "router" {{
  name    = "${{var.project_name}}-router"
  project = var.project_id
  region  = var.region
  network = google_compute_network.gke_network.id
}}

resource "google_compute_router_nat" "nat" {{
  name                               = "${{var.project_name}}-nat"
  project                            = var.project_id
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}}

resource "google_artifact_registry_repository" "docker_repo" {{
  location      = var.region
  repository_id = var.project_name
  format        = "DOCKER"
  project       = var.project_id

  depends_on = [resource.google_project_service.services]
}}

# [C4.1] Workload Identity Federation binding (KSA -> dedicated least-privilege GSA)
resource "google_service_account_iam_member" "workload_identity_binding" {{
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${{var.project_id}}.svc.id.goog[${{var.project_name}}/${{var.project_name}}]"

  depends_on = [google_container_cluster.app]
}}

resource "kubernetes_namespace_v1" "app" {{
  metadata {{
    name = var.project_name{pss_block}
  }}
  depends_on = [google_container_cluster.app]
}}

resource "kubernetes_service_account_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    annotations = {{
      "iam.gke.io/gcp-service-account" = google_service_account.app_sa.email
    }}
  }}
}}

resource "kubernetes_service_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {{
      app = var.project_name
    }}
    annotations = {{
      "cloud.google.com/load-balancer-type" = "Internal"
    }}
  }}
  spec {{
    type = "LoadBalancer"
    port {{
      port        = 8080
      target_port = 8080
      protocol    = "TCP"
    }}
    selector = {{
      app = var.project_name
    }}
  }}
}}

{hpa_pdb_block}resource "kubernetes_deployment_v1" "app" {{
  metadata {{
    name      = var.project_name
    namespace = kubernetes_namespace_v1.app.metadata[0].name
    labels = {{
      app = var.project_name{reg_labels}
    }}
  }}

  spec {{
    selector {{
      match_labels = {{
        app = var.project_name
      }}
    }}

    template {{
      metadata {{
        labels = {{
          app = var.project_name
        }}{pod_spiffe_annotations}
      }}

      spec {{
        service_account_name = kubernetes_service_account_v1.app.metadata[0].name{pod_gvisor_line}{pod_grace_line}{pod_sec}{pod_spread_block}

        container {{
          name  = var.project_name
          image = "us-docker.pkg.dev/cloudrun/container/hello"

          port {{
            container_port = 8080
            protocol       = "TCP"
          }}{cont_sec}

{chr(10).join(gke_envs)}

          resources {{
            requests = {{
              cpu    = "0.5"
              memory = "1Gi"
            }}
            limits = {{
              cpu    = "1"
              memory = "2Gi"
            }}
          }}{probes_gke}
        }}
      }}
    }}
  }}

  lifecycle {{
    ignore_changes = [
      spec[0].template[0].spec[0].container[0].image,
    ]
  }}

  depends_on = {dep_depends_on}
}}
{netpol_tail}''',
                ["C1.1", "C1.2", "GKE_DRAIN", "C2.1", "C3.1", "C4.1", "C4.2", "C4.3", "C4.4", "C5.1", "SUBSTRATE"],
            )
        return

    # === TARGET B: CLOUD RUN (11 native items — NO fake C4.4 Agent Gateway comment!) ===
    existing_cr_tf = path.read_text(encoding="utf-8") if path.exists() else ""
    mcp_profile = detect_mcp_a2a_profile(root, existing_cr_tf)

    if path.exists():
        txt = existing_cr_tf
        applied = []

        # [C4.2] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration on Cloud Run (google-beta)
        # Context-aware: MCP Servers receive FUNCTIONAL_TYPE_MCP_SERVER and keep dedicated least-privilege SA.
        if "C4.2" in item_ids:
            before_c42 = txt
            if not re.search(r"provider\s*=\s*google-beta", txt):
                txt = txt.replace(
                    'resource "google_cloud_run_v2_service" "app" {',
                    'resource "google_cloud_run_v2_service" "app" {\n  provider            = google-beta',
                    1,
                )
            if mcp_profile.is_mcp_server:
                if "FUNCTIONAL_TYPE_MCP_SERVER" not in txt:
                    func_line = '  functional_type     = "FUNCTIONAL_TYPE_MCP_SERVER" # [C4.2] Auto-register MCP Server in Agent Registry (immutable)'
                    if 'ingress             = "INGRESS_TRAFFIC_ALL"' in txt:
                        txt = txt.replace(
                            'ingress             = "INGRESS_TRAFFIC_ALL"',
                            'ingress             = "INGRESS_TRAFFIC_ALL"\n' + func_line,
                            1,
                        )
                    elif "deletion_protection = false" in txt:
                        txt = txt.replace(
                            "deletion_protection = false",
                            "deletion_protection = false\n" + func_line,
                            1,
                        )
                    else:
                        txt = re.sub(
                            r'(^[ \t]*project\s*=\s*.+$)',
                            r'\1\n' + func_line,
                            txt,
                            count=1,
                            flags=re.MULTILINE,
                        )
            else:
                want_cr_agent_identity = (params.cloud_run_identity_mode or "agent_identity").strip().lower() != "service_account"
                if want_cr_agent_identity:
                    if "FUNCTIONAL_TYPE_AGENT" not in txt:
                        txt = txt.replace(
                            'ingress             = "INGRESS_TRAFFIC_ALL"',
                            'ingress             = "INGRESS_TRAFFIC_ALL"\n  functional_type     = "FUNCTIONAL_TYPE_AGENT" # [CR-ID3] Auto-register in Agent Registry (immutable; requires IDENTITY_TYPE_AGENT_IDENTITY)',
                            1,
                        )
                        if "FUNCTIONAL_TYPE_AGENT" not in txt:
                            txt = re.sub(
                                r'(^[ \t]*(?:ingress|deletion_protection)\s*=\s*.+$)',
                                r'\1\n  functional_type     = "FUNCTIONAL_TYPE_AGENT" # [CR-ID3] Auto-register in Agent Registry (immutable; requires IDENTITY_TYPE_AGENT_IDENTITY)',
                                txt,
                                count=1,
                                flags=re.MULTILINE,
                            )
                    if "IDENTITY_TYPE_AGENT_IDENTITY" not in txt:
                        wi_block = (
                            '    # [CR-ID3] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (Preview, immutable)\n'
                            '    # Pre-grant least-privilege IAM roles to the SPIFFE principal before migrating live traffic (--no-traffic).\n'
                            '    workload_identity_config {\n'
                            '      identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"\n'
                            '    }\n'
                            '    # service_account = google_service_account.app_sa.email # Replaced by Agent Identity (SPIFFE ID)'
                        )
                        if re.search(r'^[ \t]*service_account\s*=\s*.+$', txt, flags=re.MULTILINE):
                            txt = re.sub(
                                r'^[ \t]*service_account\s*=\s*.+$',
                                wi_block,
                                txt,
                                count=1,
                                flags=re.MULTILINE,
                            )
                        else:
                            txt = txt.replace(
                                "    max_instance_request_concurrency",
                                wi_block + "\n    max_instance_request_concurrency",
                                1,
                            )
            if txt != before_c42:
                applied.append("C4.2")

        # Never upsize user CPU/Memory/Concurrency/min_instances!
        # [C1.2] Cloud Run HTTP Startup & Liveness Probes (/list-apps:8080) + startup_cpu_boost = true inside resources{}
        if "C1.2" in item_ids:
            before_c12 = txt
            txt = _ensure_startup_cpu_boost_in_resources(txt)
            if "startup_probe" not in txt:
                probe_block = """
      # [C1.2] Container Startup & Liveness Health Probes
      startup_probe {
        http_get {
          path = "/list-apps"
          port = 8080
        }
        initial_delay_seconds = 2
        period_seconds        = 5
        failure_threshold     = 6
      }

      liveness_probe {
        http_get {
          path = "/list-apps"
          port = 8080
        }
        period_seconds    = 15
        failure_threshold = 3
      }
"""
                txt = re.sub(
                    r'(image\s*=\s*"[^"]+")',
                    r"\1" + probe_block,
                    txt,
                    count=1,
                )
            if txt != before_c12:
                applied.append("C1.2")

        # [C2.1 / MEMBANK] Agent Platform Managed Sessions & Memory Bank env var (AGENT_ENGINE_SESSION_ID)
        if "C2.1" in item_ids or "MEMBANK" in item_ids:
            before = txt
            if "GOOGLE_CLOUD_AGENT_ENGINE_ID" in txt and "AGENT_ENGINE_SESSION_ID" not in txt:
                txt = txt.replace('"GOOGLE_CLOUD_AGENT_ENGINE_ID"', '"AGENT_ENGINE_SESSION_ID"')
            else:
                txt = _inject_env_before(
                    txt,
                    "LOGS_BUCKET_NAME",
                    "AGENT_ENGINE_SESSION_ID",
                    "var.agent_engine_id",
                )
            if txt != before:
                applied.append("C2.1" if "C2.1" in item_ids else "MEMBANK")

        # [C3.1] Direct VPC Egress (binds existing VPC/subnet variables; no hardcoded CIDR subnet creation)
        if "C3.1" in item_ids and "vpc_access" not in txt:
            vpc_block = f"""    # [C3.1] Direct VPC Egress for Private Enterprise Tools & Internal APIs
    vpc_access {{
      egress = "{params.egress_mode}"
      network_interfaces {{
        network    = var.vpc_network_name
        subnetwork = var.vpc_subnet_name
      }}
    }}"""
            if "    workload_identity_config" in txt:
                txt = txt.replace(
                    "    workload_identity_config",
                    vpc_block + "\n\n    workload_identity_config",
                )
            elif "    service_account" in txt:
                txt = txt.replace(
                    "    service_account",
                    vpc_block + "\n\n    service_account",
                )
            elif "    max_instance_request_concurrency" in txt:
                txt = txt.replace(
                    "    max_instance_request_concurrency",
                    vpc_block + "\n\n    max_instance_request_concurrency",
                )
            applied.append("C3.1")

        # Secret Manager version pinning — delegated to platform skills (cloud-run-basics); never triggered by v3 items
        if "SECRET_PIN" in item_ids:
            before = txt
            if 'version = "latest"' in txt:
                if "google_secret_manager_secret_version.db_password" in txt:
                    txt = txt.replace(
                        'version = "latest"',
                        "version = google_secret_manager_secret_version.db_password.version",
                    )
                else:
                    txt = txt.replace('version = "latest"', 'version = "1"')
            if txt != before:
                applied.append("SECRET_PIN")

        # [C4.3] Gen2 Kernel Sandbox
        if "C4.3" in item_ids and "EXECUTION_ENVIRONMENT_GEN2" not in txt:
            if "    workload_identity_config" in txt:
                txt = txt.replace(
                    "    workload_identity_config",
                    '    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"\n    workload_identity_config',
                )
            elif "    service_account" in txt:
                txt = txt.replace(
                    "    service_account",
                    '    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"\n    service_account',
                )
            else:
                txt = txt.replace(
                    "    max_instance_request_concurrency",
                    '    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"\n    max_instance_request_concurrency',
                )
            applied.append("C4.3")

        # [C5.1] Unified OTel GenAI Semconv + NO_CONTENT Trace Privacy
        if "C5.1" in item_ids:
            before = txt
            txt = _inject_env_before(
                txt,
                "LOGS_BUCKET_NAME",
                "OTEL_SEMCONV_STABILITY_OPT_IN",
                '"gen_ai_latest_experimental"',
            )
            txt = _inject_env_before(
                txt,
                "OTEL_SEMCONV_STABILITY_OPT_IN",
                "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT",
                '"NO_CONTENT"',
            )
            txt = _inject_env_before(
                txt,
                "OTEL_SEMCONV_STABILITY_OPT_IN",
                "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS",
                '"false"',
            )
            if txt != before:
                applied.append("C5.1")


        record(rel, txt, applied or ["C1.1"])
    else:
        # Mode A Prototype -> Clean Cloud Run service.tf (strictly tailored to selected item_ids; NO forced GCLB/WAF/deletion_protection/sizing!)
        ingress_lines = '  ingress              = "INGRESS_TRAFFIC_ALL"'
        del_prot = "false"
        boost_line = "\n        startup_cpu_boost = true" if "C1.2" in item_ids else ""
        probes_block = (
            """
      # [C1.2] Container Startup & Liveness Health Probes
      startup_probe {
        http_get {
          path = "/list-apps"
          port = 8080
        }
        initial_delay_seconds = 2
        period_seconds        = 5
        failure_threshold     = 6
      }

      liveness_probe {
        http_get {
          path = "/list-apps"
          port = 8080
        }
        period_seconds    = 15
        failure_threshold = 3
      }
"""
            if "C1.2" in item_ids
            else ""
        )

        env_blocks = [
            '      env {\n        name  = "GOOGLE_CLOUD_PROJECT"\n        value = var.project_id\n      }',
            f'      env {{\n        name  = "GOOGLE_CLOUD_LOCATION"\n        value = "global"\n      }}',
            '      env {\n        name  = "GOOGLE_GENAI_USE_VERTEXAI"\n        value = "True"\n      }',
            '      env {\n        name  = "LOGS_BUCKET_NAME"\n        value = google_storage_bucket.logs_data_bucket.name\n      }',
        ]
        if "C2.1" in item_ids:
            env_blocks.append(
                '      # [C2.1] Agent Platform Managed Sessions (VertexAiSessionService)\n'
                '      # Uses AGENT_ENGINE_SESSION_ID so ADK OTel keeps native Cloud Run resource detection.\n'
                '      env {\n        name  = "AGENT_ENGINE_SESSION_ID"\n        value = var.agent_engine_id\n      }'
            )
        if "C5.1" in item_ids:
            env_blocks.append(
                f'      env {{\n        name  = "OTEL_SERVICE_NAME"\n        value = "{project_name}"\n      }}'
            )
            env_blocks.append(
                '      env {\n        name  = "OTEL_SEMCONV_STABILITY_OPT_IN"\n        value = "gen_ai_latest_experimental"\n      }'
            )
            env_blocks.append(
                '      env {\n        name  = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\n        value = "NO_CONTENT"\n      }'
            )
            env_blocks.append(
                '      env {\n        name  = "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"\n        value = "false"\n      }'
            )

        vpc_block = (
            f"""
    # [C3.1] Direct VPC Egress for Private Enterprise Tools & Internal APIs
    vpc_access {{
      egress = "{params.egress_mode}"
      network_interfaces {{
        network    = var.vpc_network_name
        subnetwork = var.vpc_subnet_name
      }}
    }}
"""
            if "C3.1" in item_ids
            else ""
        )
        gen2_line = '    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"\n' if "C4.3" in item_ids else ""
        cr_provider_line = "  provider             = google-beta\n" if "C4.2" in item_ids else ""
        if "C4.2" in item_ids and mcp_profile.is_mcp_server:
            cr_func_line = '  functional_type      = "FUNCTIONAL_TYPE_MCP_SERVER" # [C4.2] Auto-register MCP Server in Agent Registry (immutable)\n'
            cr_identity_block = (
                '    # [C4.2] Cloud Run MCP Server retains dedicated least-privilege Service Account (Agent Identity not required)\n'
                '    service_account                  = google_service_account.app_sa.email\n'
            )
        elif "C4.2" in item_ids:
            want_cr_agent_identity = (params.cloud_run_identity_mode or "agent_identity").strip().lower() != "service_account"
            if want_cr_agent_identity:
                cr_func_line = '  functional_type      = "FUNCTIONAL_TYPE_AGENT" # [CR-ID3] Auto-register in Agent Registry (immutable; requires IDENTITY_TYPE_AGENT_IDENTITY)\n'
                cr_identity_block = (
                    '    # [CR-ID3] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (Preview, immutable)\n'
                    '    workload_identity_config {\n'
                    '      identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"\n'
                    '    }\n'
                    '    # service_account = google_service_account.app_sa.email # Replaced by Agent Identity (SPIFFE ID)\n'
                )
            else:
                cr_func_line = ""
                cr_identity_block = (
                    '    # [CR-ID3] Dedicated least-privilege Service Account retained per cloud_run_identity_mode="service_account"\n'
                    '    # (Cloud Run API requires IDENTITY_TYPE_AGENT_IDENTITY when functional_type=FUNCTIONAL_TYPE_AGENT)\n'
                    '    service_account                  = google_service_account.app_sa.email\n'
                )
        else:
            cr_func_line = ""
            cr_identity_block = "    service_account                  = google_service_account.app_sa.email\n"

        record(
            rel,
            f'''# Generated by Production-Readiness Workbench v2.4 — 100% compatible with google-agents-cli v1.5.0
# Sizing note: Uses official agents-cli neutral defaults (1 vCPU, 4Gi, concurrency=8, min=0).
# Run `tests/load_test/` (Locust) against your staging agent and tune cpu/memory/concurrency/min_instances to match your SLA.

resource "google_cloud_run_v2_service" "app" {{
{cr_provider_line}  name                 = var.project_name
  location             = var.region
  project              = var.project_id
  deletion_protection  = {del_prot}
{ingress_lines}
{cr_func_line}  labels = {{
    "created-by" = "agents-cli"
  }}

  template {{
    containers {{
      image = "us-docker.pkg.dev/cloudrun/container/hello"
{probes_block}
      resources {{
        limits = {{
          cpu    = "1"
          memory = "4Gi"
        }}{boost_line}
      }}

{chr(10).join(env_blocks)}
    }}
{vpc_block}
{cr_identity_block}{gen2_line}    max_instance_request_concurrency = 8

    scaling {{
      min_instance_count = 0
      max_instance_count = 10
    }}
  }}

  lifecycle {{
    ignore_changes = [
      template[0].containers[0].image,
    ]
  }}

  depends_on = [resource.google_project_service.services]
}}
''',
            ["C1.1", "C1.2", "C2.1", "C3.1", "C4.1", "C4.2", "C4.3", "C5.1"],
        )


# ---------------------------------------------------------------------------
# 3. Drop-In Production Terraform Modules (production_ready.tf for Agent Runtime PSC-I & Agent Gateway, alerts.tf)
# ---------------------------------------------------------------------------


def _render_agent_gateway_tf(params: CustomParams, *, multi_env: bool = False) -> str:
    """Render Agent Runtime Agent Gateway Terraform resources (single-project or multi-env cicd).

    Supports per-feature configuration selected interactively by the user:
    - ``agent_gateway_direction``: ``egress`` (AGENT_TO_ANYWHERE), ``ingress`` (CLIENT_TO_AGENT), or ``both``
    - ``agent_gateway_enforcement_mode``: ``DRY_RUN`` (default) or ``ENFORCED``
    - ``enable_model_armor``: optional active Model Armor template + CONTENT_AUTHZ policy
    """
    direction = (params.agent_gateway_direction or "egress").strip().lower()
    if direction not in ("egress", "ingress", "both"):
        direction = "egress"
    want_egress = direction in ("egress", "both")
    want_ingress = direction in ("ingress", "both")
    enable_ma = bool(params.enable_model_armor)

    if not multi_env:
        gw_blocks: list[str] = []
        target_refs: list[str] = []
        dep_refs: list[str] = []
        if want_egress:
            target_refs.append("      google_network_services_agent_gateway.egress_gateway.id,")
            dep_refs.append("    google_network_services_agent_gateway.egress_gateway,")
            gw_blocks.append('''resource "google_network_services_agent_gateway" "egress_gateway" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "${var.project_name}-egress-gw"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.region}",
  ]

  depends_on = [google_project_service.agent_gateway_apis]
}''')
        if want_ingress:
            target_refs.append("      google_network_services_agent_gateway.ingress_gateway.id,")
            dep_refs.append("    google_network_services_agent_gateway.ingress_gateway,")
            gw_blocks.append('''resource "google_network_services_agent_gateway" "ingress_gateway" {
  provider = google-beta
  project  = var.project_id
  location = var.region
  name     = "${var.project_name}-ingress-gw"

  google_managed {
    governed_access_path = "CLIENT_TO_AGENT"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${var.project_id}/locations/${var.region}",
  ]

  depends_on = [google_project_service.agent_gateway_apis]
}''')

        targets_hcl = "\n".join(target_refs)
        gw_deps_hcl = "\n".join(dep_refs)
        ma_block = ""
        if enable_ma:
            ma_block = f'''
# [C4.4 · Model Armor] Prompt Injection, Jailbreak & Sensitive Data Protection (SDP) inspection
resource "google_model_armor_template" "agent_gateway_armor" {{
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  template_id = "${{var.project_name}}-agw-armor"

  filter_config {{
    pi_and_jailbreak_filter_settings {{
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }}
    sdp_settings {{
      basic_config {{
        filter_enforcement = "ENABLED"
      }}
    }}
  }}

  depends_on = [google_project_service.agent_gateway_apis]
}}

output "agent_gateway_model_armor_template" {{
  description = "Model Armor template for Agent Gateway prompt/response inspection (bind via Service Extensions)"
  value       = google_model_armor_template.agent_gateway_armor.name
}}

# Attach Model Armor template to Agent Gateway via Service Extensions ExtensionBinding (Preview):
#   gcloud beta service-extensions extension-bindings create ${{var.project_name}}-ma-binding \\
#     --location=${{var.region}} --project=${{var.project_id}} \\
#     --target-resource=projects/${{var.project_id}}/locations/${{var.region}}/agentGateways/${{var.project_name}}-egress-gw \\
#     --producer-extension="modelarmor.${{var.region}}.rep.googleapis.com"
'''
        ma_followup = (
            "# Model Armor (pi_and_jailbreak + sdp_settings) is ENABLED above.\n"
            if enable_ma
            else "# Optionally add Model Armor templates (piAndJailbreak + sdp) for prompt/response inspection.\n"
        )
        return f'''# [C4.4] Central Agent Gateway (direction={direction}, enforcement={params.agent_gateway_enforcement_mode}, model_armor={str(enable_ma).lower()})
# Note: iamEnforcementMode defaults to "DRY_RUN" to prevent HTTP 498 disruption before essential APIs are allowlisted.
resource "google_project_service" "agent_gateway_apis" {{
  for_each = toset([
    "networkservices.googleapis.com",
    "networksecurity.googleapis.com",
    "serviceextensions.googleapis.com",
    "modelarmor.googleapis.com",
    "iap.googleapis.com",
  ])
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}}

{chr(10).join(gw_blocks)}

resource "google_network_services_authz_extension" "iap_request_authz_ext" {{
  project   = var.project_id
  location  = var.region
  name      = "${{var.project_name}}-iap-authz-ext"
  service   = "iap.googleapis.com"
  timeout   = "1s"
  fail_open = false

  metadata = {{
    iapPolicyVersion   = "V2"
    iamEnforcementMode = var.agent_gateway_enforcement_mode
  }}

  depends_on = [google_project_service.agent_gateway_apis]
}}

resource "google_network_security_authz_policy" "iap_request_authz_policy" {{
  project        = var.project_id
  location       = var.region
  name           = "${{var.project_name}}-iap-authz-policy"
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  target {{
    resources = [
{targets_hcl}
    ]
  }}

  custom_provider {{
    authz_extension {{
      resources = [
        google_network_services_authz_extension.iap_request_authz_ext.id,
      ]
    }}
  }}

  depends_on = [
{gw_deps_hcl}
    google_network_services_authz_extension.iap_request_authz_ext,
  ]
}}
{ma_block}
# ------------------------------------------------------------------------------
# [AR-S3 Follow-up] After auditing DRY_RUN logs, add roles/iap.egressor CEL
# allowlist bindings before promoting var.agent_gateway_enforcement_mode = "ENFORCED".
{ma_followup}# ------------------------------------------------------------------------------
'''

    # Multi-env CI/CD mode (for_each = local.deploy_project_ids; never references var.project_id)
    gw_blocks_ci: list[str] = []
    target_refs_ci: list[str] = []
    dep_refs_ci: list[str] = []
    if want_egress:
        target_refs_ci.append("      google_network_services_agent_gateway.egress_gateway[each.key].id,")
        dep_refs_ci.append("    google_network_services_agent_gateway.egress_gateway,")
        gw_blocks_ci.append('''resource "google_network_services_agent_gateway" "egress_gateway" {
  provider = google-beta
  for_each = local.deploy_project_ids
  project  = each.value
  location = var.region
  name     = "${var.project_name}-egress-gw"

  google_managed {
    governed_access_path = "AGENT_TO_ANYWHERE"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${each.value}/locations/${var.region}",
  ]

  depends_on = [
    google_project_service.agent_gateway_apis,
    google_project_service.deploy_project_services,
  ]
}''')
    if want_ingress:
        target_refs_ci.append("      google_network_services_agent_gateway.ingress_gateway[each.key].id,")
        dep_refs_ci.append("    google_network_services_agent_gateway.ingress_gateway,")
        gw_blocks_ci.append('''resource "google_network_services_agent_gateway" "ingress_gateway" {
  provider = google-beta
  for_each = local.deploy_project_ids
  project  = each.value
  location = var.region
  name     = "${var.project_name}-ingress-gw"

  google_managed {
    governed_access_path = "CLIENT_TO_AGENT"
  }

  registries = [
    "//agentregistry.googleapis.com/projects/${each.value}/locations/${var.region}",
  ]

  depends_on = [
    google_project_service.agent_gateway_apis,
    google_project_service.deploy_project_services,
  ]
}''')

    targets_ci_hcl = "\n".join(target_refs_ci)
    deps_ci_hcl = "\n".join(dep_refs_ci)
    ma_ci_block = ""
    if enable_ma:
        ma_ci_block = f'''
resource "google_model_armor_template" "agent_gateway_armor" {{
  provider    = google-beta
  for_each    = local.deploy_project_ids
  project     = each.value
  location    = var.region
  template_id = "${{var.project_name}}-agw-armor"

  filter_config {{
    pi_and_jailbreak_filter_settings {{
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }}
    sdp_settings {{
      basic_config {{
        filter_enforcement = "ENABLED"
      }}
    }}
  }}

  depends_on = [google_project_service.agent_gateway_apis]
}}

# Attach Model Armor template to Agent Gateway per env via Service Extensions ExtensionBinding (Preview):
#   gcloud beta service-extensions extension-bindings create <project_name>-ma-binding \\
#     --location=<region> --project=<env_project_id> \\
#     --target-resource=projects/<env_project_id>/locations/<region>/agentGateways/<project_name>-egress-gw \\
#     --producer-extension="modelarmor.<region>.rep.googleapis.com"
'''
    return f'''# deployment/terraform/cicd/production_ready.tf
# Isolated Production-Readiness add-on resources for multi-project CI/CD (staging & prod via local.deploy_project_ids)
# Direction={direction}, enforcement={params.agent_gateway_enforcement_mode}, model_armor={str(enable_ma).lower()}
# Note: iamEnforcementMode defaults to "DRY_RUN" to prevent HTTP 498 disruption before essential APIs are allowlisted.

locals {{
  agent_gateway_api_list = [
    "networkservices.googleapis.com",
    "networksecurity.googleapis.com",
    "serviceextensions.googleapis.com",
    "modelarmor.googleapis.com",
    "iap.googleapis.com",
  ]
}}

resource "google_project_service" "agent_gateway_apis" {{
  for_each = {{
    for pair in setproduct(keys(local.deploy_project_ids), local.agent_gateway_api_list) :
    "${{pair[0]}}_${{replace(pair[1], ".", "_")}}" => {{
      project = local.deploy_project_ids[pair[0]]
      service = pair[1]
    }}
  }}
  project            = each.value.project
  service            = each.value.service
  disable_on_destroy = false
}}

{chr(10).join(gw_blocks_ci)}

resource "google_network_services_authz_extension" "iap_request_authz_ext" {{
  for_each  = local.deploy_project_ids
  project   = each.value
  location  = var.region
  name      = "${{var.project_name}}-iap-authz-ext"
  service   = "iap.googleapis.com"
  timeout   = "1s"
  fail_open = false

  metadata = {{
    iapPolicyVersion   = "V2"
    iamEnforcementMode = var.agent_gateway_enforcement_mode
  }}

  depends_on = [google_project_service.agent_gateway_apis]
}}

resource "google_network_security_authz_policy" "iap_request_authz_policy" {{
  for_each       = local.deploy_project_ids
  project        = each.value
  location       = var.region
  name           = "${{var.project_name}}-iap-authz-policy"
  policy_profile = "REQUEST_AUTHZ"
  action         = "CUSTOM"

  target {{
    resources = [
{targets_ci_hcl}
    ]
  }}

  custom_provider {{
    authz_extension {{
      resources = [
        google_network_services_authz_extension.iap_request_authz_ext[each.key].id,
      ]
    }}
  }}

  depends_on = [
{deps_ci_hcl}
    google_network_services_authz_extension.iap_request_authz_ext,
  ]
}}
{ma_ci_block}
# ------------------------------------------------------------------------------
# [AR-S3 Follow-up] After auditing DRY_RUN logs in staging/prod, add per-env
# roles/iap.egressor CEL bindings + Model Armor templates (piAndJailbreak + sdp)
# before promoting var.agent_gateway_enforcement_mode = "ENFORCED".
# ------------------------------------------------------------------------------
'''


def _prepare_production_ready_tf(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    tf_rel = resolve_terraform_rel_dir(root)
    rel = f"{tf_rel}/production_ready.tf"

    # Strictly Agent Runtime ONLY (never create production_ready.tf / Agent Gateway on Cloud Run!)
    if target != "agent_runtime":
        return

    need_psc = "C3.1" in item_ids
    need_agw = "C4.4" in item_ids
    if not (need_psc or need_agw):
        return

    blocks: list[str] = [
        "# deployment/terraform/production_ready.tf",
        "# Generated by Production-Readiness Workbench v2.1 — Agent Runtime (PSC Interface & Agent Gateway)",
        "",
    ]
    applied_items: list[str] = []

    if need_psc:
        applied_items.append("C3.1")
        blocks.append(f'''resource "google_project_service" "compute_api" {{
  project            = var.project_id
  service            = "compute.googleapis.com"
  disable_on_destroy = false
}}

# [C3.1] Private Service Connect (PSC) Interface Subnetwork & Network Attachment
# Connected to google_vertex_ai_reasoning_engine.app via spec.deployment_spec.psc_interface_config
data "google_compute_network" "agent_vpc" {{
  name    = var.vpc_network_name
  project = var.project_id
}}

resource "google_compute_subnetwork" "agent_psc_subnet" {{
  name                     = "${{var.project_name}}-psc-egress"
  project                  = var.project_id
  region                   = var.region
  network                  = data.google_compute_network.agent_vpc.id
  ip_cidr_range            = "{params.subnet_cidr}"
  private_ip_google_access = true
  depends_on               = [google_project_service.compute_api]
}}

resource "google_compute_network_attachment" "agent_psc_interface" {{
  name                  = "${{var.project_name}}-psc-attachment"
  project               = var.project_id
  region                = var.region
  description           = "PSC Interface network attachment for Agent Runtime private VPC egress"
  connection_preference = "ACCEPT_AUTOMATIC"
  subnetworks           = [google_compute_subnetwork.agent_psc_subnet.self_link]
  depends_on            = [google_compute_subnetwork.agent_psc_subnet]
}}
''')

    if need_agw:
        applied_items.append("C4.4")
        blocks.append(_render_agent_gateway_tf(params, multi_env=False))

    record(rel, "\n".join(blocks).rstrip() + "\n", applied_items)


def _prepare_terraform_cicd(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    """Option A: mirror production patches into ``deployment/terraform/cicd/`` when present.

    Keeps standalone new resources (Agent Runtime Agent Gateway) isolated in
    ``cicd/production_ready.tf`` using multi-project ``for_each = local.deploy_project_ids``,
    and applies minimal in-place resource attribute patches to ``cicd/service.tf``,
    ``cicd/variables.tf``, and ``cicd/locals.tf``.
    """
    cicd_rel = "deployment/terraform/cicd"
    cicd_dir = root / cicd_rel
    cicd_service_path = cicd_dir / "service.tf"
    if not cicd_service_path.is_file() or resolve_terraform_rel_dir(root) == cicd_rel:
        return

    existing_service_tf = cicd_service_path.read_text(encoding="utf-8")
    mcp_profile = detect_mcp_a2a_profile(root, existing_service_tf) if target == "cloud_run" else None

    # 1. cicd/variables.tf
    vars_path = cicd_dir / "variables.tf"
    if vars_path.is_file():
        vtxt = vars_path.read_text(encoding="utf-8")
        vapplied: list[str] = []
        if "C4.1" in item_ids:
            if '"roles/storage.admin"' in vtxt:
                vtxt = vtxt.replace('"roles/storage.admin"', '"roles/storage.objectUser"')
                vapplied.append("C4.1")
            if target == "agent_runtime" and '"roles/cloudsql.client"' in vtxt:
                vtxt = re.sub(r'\s*"roles/cloudsql\.client",?\n', "\n", vtxt)
                vapplied.append("C4.1")
        if "C5.1" in item_ids and '"roles/cloudtrace.agent"' in vtxt and '"roles/monitoring.metricWriter"' not in vtxt:
            vtxt = vtxt.replace(
                '"roles/cloudtrace.agent",',
                '"roles/cloudtrace.agent",\n    "roles/monitoring.metricWriter",',
                1,
            )
            vapplied.append("C5.1")
        if "C2.1" in item_ids and target in ("cloud_run", "gke") and 'variable "agent_engine_id"' not in vtxt:
            vtxt = vtxt.rstrip() + f'''

variable "agent_engine_id" {{
  type        = string
  description = "Agent Platform Reasoning Engine ID for Managed Sessions (VertexAiSessionService)"
  default     = "{params.agent_engine_id}"
}}
'''
            vapplied.append("C2.1")
        if target == "agent_runtime" and "C4.4" in item_ids and 'variable "agent_gateway_enforcement_mode"' not in vtxt:
            vtxt = vtxt.rstrip() + f'''

variable "agent_gateway_enforcement_mode" {{
  type        = string
  description = "Agent Gateway IAP/UAP V2 enforcement mode (DRY_RUN prevents HTTP 498 before essential APIs are allowlisted; switch to ENFORCED for production enforcement)"
  default     = "{params.agent_gateway_enforcement_mode}"
}}
'''
            vapplied.append("C4.4")
        if vapplied:
            record(f"{cicd_rel}/variables.tf", vtxt, vapplied)

    # 2. cicd/locals.tf — append Agent Registry / AppHub APIs to deploy_project_services
    locals_path = cicd_dir / "locals.tf"
    if locals_path.is_file() and "C4.2" in item_ids:
        ltxt = locals_path.read_text(encoding="utf-8")
        missing = [
            api
            for api in ("agentregistry.googleapis.com", "apphub.googleapis.com")
            if api not in ltxt
        ]
        if missing and re.search(r"deploy_project_services\s*=\s*\[", ltxt):
            inject_lines = "\n".join(f'    "{api}",' for api in missing)

            def _append_deploy_apis(match: re.Match[str]) -> str:
                body = match.group(2).rstrip()
                if body and not body.endswith(","):
                    body += ","
                return f"{match.group(1)}{body}\n{inject_lines}\n{match.group(3)}"

            updated_locals = re.sub(
                r"(deploy_project_services\s*=\s*\[)([\s\S]*?)(\n[ \t]*\])",
                _append_deploy_apis,
                ltxt,
                count=1,
            )
            if updated_locals != ltxt:
                record(f"{cicd_rel}/locals.tf", updated_locals, ["C4.2"])

    # 3. cicd/service.tf — minimal in-place resource attribute patches
    txt = existing_service_tf
    applied: list[str] = []

    if target == "cloud_run":
        if "C4.2" in item_ids:
            before = txt
            if not re.search(r"provider\s*=\s*google-beta", txt):
                txt = txt.replace(
                    'resource "google_cloud_run_v2_service" "app" {',
                    'resource "google_cloud_run_v2_service" "app" {\n  provider            = google-beta',
                    1,
                )
            if mcp_profile and mcp_profile.is_mcp_server:
                if "FUNCTIONAL_TYPE_MCP_SERVER" not in txt:
                    txt = txt.replace(
                        'ingress             = "INGRESS_TRAFFIC_ALL"',
                        'ingress             = "INGRESS_TRAFFIC_ALL"\n  functional_type     = "FUNCTIONAL_TYPE_MCP_SERVER" # [C4.2] Auto-register MCP server in Agent Registry (immutable)',
                        1,
                    )
            else:
                want_cr_agent_identity = (params.cloud_run_identity_mode or "agent_identity").strip().lower() != "service_account"
                if want_cr_agent_identity:
                    if "FUNCTIONAL_TYPE_AGENT" not in txt:
                        txt = txt.replace(
                            'ingress             = "INGRESS_TRAFFIC_ALL"',
                            'ingress             = "INGRESS_TRAFFIC_ALL"\n  functional_type     = "FUNCTIONAL_TYPE_AGENT" # [CR-ID3] Auto-register in Agent Registry (immutable; requires IDENTITY_TYPE_AGENT_IDENTITY)',
                            1,
                        )
                    if "IDENTITY_TYPE_AGENT_IDENTITY" not in txt:
                        wi_block = (
                            '    # [CR-ID3] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (Preview, immutable)\n'
                            '    workload_identity_config {\n'
                            '      identity_type = "IDENTITY_TYPE_AGENT_IDENTITY"\n'
                            '    }\n'
                            '    # service_account = google_service_account.app_sa[each.key].email # Replaced by Agent Identity (SPIFFE ID)'
                        )
                        if re.search(r'^[ \t]*service_account\s*=\s*.+$', txt, flags=re.MULTILINE):
                            txt = re.sub(
                                r'^[ \t]*service_account\s*=\s*.+$',
                                wi_block,
                                txt,
                                count=1,
                                flags=re.MULTILINE,
                            )
            if txt != before:
                applied.append("C4.2")
        if "C2.1" in item_ids:
            before = txt
            if "GOOGLE_CLOUD_AGENT_ENGINE_ID" in txt and "AGENT_ENGINE_SESSION_ID" not in txt:
                txt = txt.replace('"GOOGLE_CLOUD_AGENT_ENGINE_ID"', '"AGENT_ENGINE_SESSION_ID"')
            else:
                txt = _inject_env_before(txt, "LOGS_BUCKET_NAME", "AGENT_ENGINE_SESSION_ID", "var.agent_engine_id")
            if txt != before:
                applied.append("C2.1")
        if "C5.1" in item_ids:
            before = txt
            txt = _inject_env_before(txt, "LOGS_BUCKET_NAME", "OTEL_SEMCONV_STABILITY_OPT_IN", '"gen_ai_latest_experimental"')
            txt = _inject_env_before(txt, "LOGS_BUCKET_NAME", "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", '"NO_CONTENT"')
            txt = _inject_env_before(txt, "LOGS_BUCKET_NAME", "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", '"false"')
            if txt != before:
                applied.append("C5.1")

    elif target == "agent_runtime":
        if ("C4.2" in item_ids or "C4.4" in item_ids) and not re.search(r"provider\s*=\s*google-beta", txt):
            txt = txt.replace(
                'resource "google_vertex_ai_reasoning_engine" "app" {',
                'resource "google_vertex_ai_reasoning_engine" "app" {\n  provider     = google-beta',
                1,
            )
            applied.extend([i for i in ("C4.2", "C4.4") if i in item_ids])
        if "C4.2" in item_ids and not re.search(r'identity_type\s*=\s*"AGENT_IDENTITY"', txt):
            id_block = (
                '    # [C4.2] Agent Identity (SPIFFE ID) & Agent Registry Auto-Registration (immutable at creation)\n'
                '    identity_type   = "AGENT_IDENTITY"\n'
                '    # service_account = google_service_account.app_sa[each.key].email # Replaced by per-agent SPIFFE ID'
            )
            if re.search(r'^[ \t]*service_account\s*=\s*.+$', txt, flags=re.MULTILINE):
                txt = re.sub(r'^[ \t]*service_account\s*=\s*.+$', id_block, txt, count=1, flags=re.MULTILINE)
            else:
                txt = txt.replace('agent_framework = "google-adk"', 'agent_framework = "google-adk"\n' + id_block, 1)
            applied.append("C4.2")
        agw_dir_cicd = (params.agent_gateway_direction or "egress").strip().lower()
        if "C4.4" in item_ids and agw_dir_cicd in ("egress", "both") and "agent_gateway_config" not in txt:
            agw_block = """
      # [C4.4] Central Agent Gateway (Agent-to-Anywhere Egress · per-env via for_each)
      # IAP/UAP V2 policy defaults to DRY_RUN in cicd/production_ready.tf to avoid HTTP 498 before API allowlisting.
      agent_gateway_config {
        agent_to_anywhere_config {
          agent_gateway = google_network_services_agent_gateway.egress_gateway[each.key].id
        }
      }
"""
            if "resource_limits" in txt:
                txt = re.sub(r"(resource_limits\s*=\s*\{[^}]*\})", r"\1\n" + agw_block.rstrip(), txt, count=1)
                applied.append("C4.4")
        if "C5.1" in item_ids:
            before = txt
            txt = _inject_env_before(txt, "OTEL_SEMCONV_STABILITY_OPT_IN", "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", '"NO_CONTENT"')
            txt = _inject_env_before(txt, "OTEL_SEMCONV_STABILITY_OPT_IN", "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", '"false"')
            if txt != before:
                applied.append("C5.1")

    elif target == "gke":
        # GKE cicd/service.tf defines separate kubernetes_deployment_v1 blocks (app_staging / app_prod)
        dep_spans = [m.start() for m in re.finditer(r'resource\s+"kubernetes_deployment_v1"\s+"[^"]+"', txt)]
        if dep_spans:
            parts: list[str] = [txt[:dep_spans[0]]]
            for idx_i, start in enumerate(dep_spans):
                end = dep_spans[idx_i + 1] if idx_i + 1 < len(dep_spans) else len(txt)
                # Stop at the next top-level resource if any comes before next deployment
                chunk = txt[start:end]
                next_res = re.search(r'\nresource\s+"', chunk[10:])
                if next_res:
                    dep_blk = chunk[: 10 + next_res.start()]
                    rest_blk = chunk[10 + next_res.start() :]
                else:
                    dep_blk, rest_blk = chunk, ""

                if "C4.2" in item_ids:
                    b0 = dep_blk
                    if "registry.gke.io/functional-type" not in dep_blk:
                        dep_blk = re.sub(
                            r'(metadata\s*\{[\s\S]*?\blabels\s*=\s*\{\s*\n)([ \t]*)(app\s*=\s*var\.project_name)',
                            r'\1\2\3\n\2"registry.gke.io/functional-type" = "AGENT"',
                            dep_blk,
                            count=1,
                        )
                    if "iam.gke.io/spiffe-identity-type" not in dep_blk:
                        if re.search(r'template\s*\{\s*\n[ \t]*metadata\s*\{[\s\S]*?annotations\s*=\s*\{', dep_blk):
                            dep_blk = re.sub(
                                r'(template\s*\{\s*\n[ \t]*metadata\s*\{[\s\S]*?annotations\s*=\s*\{\s*\n)',
                                r'\1          "iam.gke.io/spiffe-identity-type" = "agent-identity"\n',
                                dep_blk,
                                count=1,
                            )
                        else:
                            dep_blk = re.sub(
                                r'(template\s*\{\s*\n[ \t]*metadata\s*\{[\s\S]*?labels\s*=\s*\{[^}]*\})',
                                (
                                    r'\1\n'
                                    '        annotations = {\n'
                                    '          # [C4.2] Official GKE Agent Identity (SPIFFE) annotation\n'
                                    '          "iam.gke.io/spiffe-identity-type" = "agent-identity"\n'
                                    '        }'
                                ),
                                dep_blk,
                                count=1,
                            )
                    if dep_blk != b0 and "C4.2" not in applied:
                        applied.append("C4.2")

                drain_flag = "GKE_DRAIN" if "GKE_DRAIN" in item_ids else ("C1.2" if "C1.2" in item_ids else "")
                if drain_flag:
                    b0 = dep_blk
                    if "tcp_socket" in dep_blk:
                        dep_blk = re.sub(
                            r'([ \t]*)tcp_socket\s*\{\s*\n[ \t]*port\s*=\s*(\d+)\s*\n[ \t]*\}',
                            r'\1http_get {\n\1  path = "/list-apps"\n\1  port = \2\n\1}',
                            dep_blk,
                        )
                    grace_sec = max(60, int(params.gke_termination_grace_seconds))
                    if "termination_grace_period_seconds" not in dep_blk:
                        dep_blk = re.sub(
                            r"(^[ \t]*container\s*\{)",
                            f"        termination_grace_period_seconds = {grace_sec} # [GKE-O1] Drain active agent streams before SIGKILL\n\n" + r"\1",
                            dep_blk,
                            count=1,
                            flags=re.MULTILINE,
                        )
                    if "pre_stop" not in dep_blk:
                        pre_stop_blk = (
                            "          # [GKE-O1] Wait for LB/Gateway EndpointSlice deregistration before SIGTERM\n"
                            "          lifecycle {\n"
                            "            pre_stop {\n"
                            "              exec {\n"
                            '                command = ["/bin/sh", "-c", "sleep 10"]\n'
                            "              }\n"
                            "            }\n"
                            "          }\n\n"
                        )
                        if re.search(r"^[ \t]*startup_probe\s*\{", dep_blk, flags=re.MULTILINE):
                            dep_blk = re.sub(r"(^[ \t]*startup_probe\s*\{)", pre_stop_blk + r"\1", dep_blk, count=1, flags=re.MULTILINE)
                        elif re.search(r"^[ \t]*readiness_probe\s*\{", dep_blk, flags=re.MULTILINE):
                            dep_blk = re.sub(r"(^[ \t]*readiness_probe\s*\{)", pre_stop_blk + r"\1", dep_blk, count=1, flags=re.MULTILINE)
                    if dep_blk != b0 and drain_flag not in applied:
                        applied.append(drain_flag)

                if "C2.1" in item_ids:
                    b0 = dep_blk
                    dep_blk = _inject_env_before(dep_blk, "LOGS_BUCKET_NAME", "AGENT_ENGINE_SESSION_ID", "var.agent_engine_id")
                    if dep_blk != b0 and "C2.1" not in applied:
                        applied.append("C2.1")

                if "C5.1" in item_ids:
                    b0 = dep_blk
                    dep_blk = _inject_env_before(dep_blk, "LOGS_BUCKET_NAME", "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", '"NO_CONTENT"')
                    dep_blk = _inject_env_before(dep_blk, "LOGS_BUCKET_NAME", "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", '"false"')
                    if dep_blk != b0 and "C5.1" not in applied:
                        applied.append("C5.1")

                parts.append(dep_blk + rest_blk)
            txt = "".join(parts)

    if applied and txt != existing_service_tf:
        record(f"{cicd_rel}/service.tf", txt, applied)

    # 4. cicd/production_ready.tf — standalone new resources for Agent Runtime (AR-S3) across staging & prod
    if target == "agent_runtime" and "C4.4" in item_ids:
        cicd_pr_tf = _render_agent_gateway_tf(params, multi_env=True)
        record(f"{cicd_rel}/production_ready.tf", cicd_pr_tf, ["C4.4"])


def _prepare_alerts_tf(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    if "C5.3" not in item_ids:
        return
    tf_rel = resolve_terraform_rel_dir(root)
    rel = f"{tf_rel}/alerts.tf"

    if target == "agent_runtime":
        content = f'''# deployment/terraform/alerts.tf
# [C5.3] Production SRE Golden Signal Alert Policies — Agent Runtime (ReasoningEngine)

resource "google_project_service" "monitoring_api" {{
  project            = var.project_id
  service            = "monitoring.googleapis.com"
  disable_on_destroy = false
}}

variable "alert_notification_channels" {{
  type        = list(string)
  description = "Cloud Monitoring notification channel IDs (PagerDuty, Slack, Email)"
  default     = []
}}

variable "latency_p95_threshold_ms" {{
  type        = number
  description = "P95 turn latency threshold in ms for multi-step AI agent alerts"
  default     = {params.latency_p95_ms}
}}

# 1. High Agent Runtime Turn Latency (P95 > threshold)
resource "google_monitoring_alert_policy" "agent_high_latency_p95" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (Agent Runtime) - High Turn Latency (P95)"
  combiner     = "OR"

  conditions {{
    display_name = "ReasoningEngine P95 Latency > ${{var.latency_p95_threshold_ms}}ms"
    condition_threshold {{
      filter          = "resource.type = \\"aiplatform.googleapis.com/ReasoningEngine\\" AND metric.type = \\"aiplatform.googleapis.com/reasoning_engine/request_latencies\\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = var.latency_p95_threshold_ms
      aggregations {{
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
      }}
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}

# 2. High Agent Runtime 5xx Error Ratio (> 2% over 5m via PromQL)
resource "google_monitoring_alert_policy" "agent_error_rate_high" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (Agent Runtime) - High 5xx Error Ratio (> 2%)"
  combiner     = "OR"

  conditions {{
    display_name = "ReasoningEngine 5xx Ratio > 2%"
    condition_prometheus_query_language {{
      query                     = "sum(rate(aiplatform_googleapis_com:reasoning_engine_request_count{{response_code_class=\\"5xx\\"}}[5m])) / clamp_min(sum(rate(aiplatform_googleapis_com:reasoning_engine_request_count[5m])), 1) > 0.02"
      duration                  = "180s"
      disable_metric_validation = true
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}
'''
    elif target == "gke":
        content = f'''# deployment/terraform/alerts.tf
# [C5.3] Production SRE Golden Signal Alert Policies — GKE (k8s_container + Managed Prometheus)

resource "google_project_service" "monitoring_api" {{
  project            = var.project_id
  service            = "monitoring.googleapis.com"
  disable_on_destroy = false
}}

variable "alert_notification_channels" {{
  type        = list(string)
  description = "Cloud Monitoring notification channel IDs (PagerDuty, Slack, Email)"
  default     = []
}}

variable "latency_p95_threshold_ms" {{
  type        = number
  description = "P95 turn latency threshold in ms for multi-step AI agent alerts"
  default     = {params.latency_p95_ms}
}}

# 1. GKE Pod CrashLoopBackOff / High Container Restart Count (> 3 restarts in 15m)
resource "google_monitoring_alert_policy" "agent_pod_crashloop_restarts" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (GKE) - High Container Restart Count (CrashLoopBackOff)"
  combiner     = "OR"

  conditions {{
    display_name = "GKE k8s_container Restarts > 3 in 15m"
    condition_prometheus_query_language {{
      query                     = "sum by (namespace_name, pod_name, container_name) (increase(kubernetes_io:container_restart_count{{monitored_resource=\\"k8s_container\\",namespace_name=\\"${{var.project_name}}\\"}}[15m])) > 3"
      duration                  = "0s"
      disable_metric_validation = true
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}

# 2. GKE Container Memory Saturation (> 85% of memory limit — early OOMKilled warning)
resource "google_monitoring_alert_policy" "agent_memory_saturation" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (GKE) - High Container Memory Saturation (> 85%)"
  combiner     = "OR"

  conditions {{
    display_name = "GKE k8s_container Memory Utilization > 85%"
    condition_prometheus_query_language {{
      query                     = "max by (namespace_name, pod_name, container_name) (kubernetes_io:container_memory_limit_utilization{{monitored_resource=\\"k8s_container\\",namespace_name=\\"${{var.project_name}}\\",memory_type=\\"non-evictable\\"}}) > 0.85"
      duration                  = "300s"
      disable_metric_validation = true
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}

# 3. GKE Agent Invocation Error Ratio (> 2% over 5m via OTel GenAI PromQL)
resource "google_monitoring_alert_policy" "agent_error_rate_high" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (GKE) - High Agent Error Ratio (> 2%)"
  combiner     = "OR"

  conditions {{
    display_name = "GKE OTel GenAI Error Ratio > 2%"
    condition_prometheus_query_language {{
      query                     = "sum(rate(workload_googleapis_com:gen_ai_invoke_agent_duration_count{{error_type!=\\"\\"}}[5m])) / clamp_min(sum(rate(workload_googleapis_com:gen_ai_invoke_agent_duration_count[5m])), 1) > 0.02"
      duration                  = "180s"
      disable_metric_validation = true
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}

# 4. GKE Agent Turn Latency P95 (> latency_p95_threshold_ms via OTel GenAI PromQL)
resource "google_monitoring_alert_policy" "agent_high_latency_p95" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (GKE) - High Agent Turn Latency (P95)"
  combiner     = "OR"

  conditions {{
    display_name = "GKE OTel GenAI Turn Latency P95 > ${{var.latency_p95_threshold_ms}}ms"
    condition_prometheus_query_language {{
      query                     = "histogram_quantile(0.95, sum by (le) (rate(workload_googleapis_com:gen_ai_invoke_agent_duration_bucket[5m]))) * 1000 > ${{var.latency_p95_threshold_ms}}"
      duration                  = "300s"
      disable_metric_validation = true
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}
'''
    else:
        content = f'''# deployment/terraform/alerts.tf
# [C5.3] Production SRE Golden Signal Alert Policies — Cloud Run

resource "google_project_service" "monitoring_api" {{
  project            = var.project_id
  service            = "monitoring.googleapis.com"
  disable_on_destroy = false
}}

variable "alert_notification_channels" {{
  type        = list(string)
  description = "Cloud Monitoring notification channel IDs (PagerDuty, Slack, Email)"
  default     = []
}}

variable "latency_p95_threshold_ms" {{
  type        = number
  description = "P95 turn latency threshold in ms for multi-step AI agent alerts"
  default     = {params.latency_p95_ms}
}}

# 1. High End-to-End Agent Turn Latency (P95 > threshold)
resource "google_monitoring_alert_policy" "agent_high_latency_p95" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (Cloud Run) - High Agent Turn Latency (P95)"
  combiner     = "OR"

  conditions {{
    display_name = "Request Latency P95 > ${{var.latency_p95_threshold_ms}}ms"
    condition_threshold {{
      filter          = "resource.type = \\"cloud_run_revision\\" AND resource.labels.service_name = \\"${{var.project_name}}\\" AND metric.type = \\"run.googleapis.com/request_latencies\\""
      duration        = "300s"
      comparison      = "COMPARISON_GT"
      threshold_value = var.latency_p95_threshold_ms
      aggregations {{
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_PERCENTILE_95"
        cross_series_reducer = "REDUCE_MAX"
      }}
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}

# 2. High 5xx Error Ratio (> 2% over 5m via PromQL ratio — never raw count!)
resource "google_monitoring_alert_policy" "agent_error_rate_high" {{
  project      = var.project_id
  display_name = "${{var.project_name}} (Cloud Run) - High 5xx Error Ratio (> 2%)"
  combiner     = "OR"

  conditions {{
    display_name = "Cloud Run 5xx Error Ratio > 2%"
    condition_prometheus_query_language {{
      query                     = "sum(rate(run_googleapis_com:request_count{{service_name=\\"${{var.project_name}}\\",response_code_class=\\"5xx\\"}}[5m])) / clamp_min(sum(rate(run_googleapis_com:request_count{{service_name=\\"${{var.project_name}}\\"}}[5m])), 1) > 0.02"
      duration                  = "180s"
      disable_metric_validation = true
    }}
  }}

  notification_channels = var.alert_notification_channels
  depends_on            = [google_project_service.monitoring_api]
}}
'''
    record(rel, content, ["C5.3"])


def _prepare_gke_k8s_manifests(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    """Generates drop-in GKE Kubernetes YAML manifests under `deployment/k8s/` without breaking `terraform apply`."""
    if target != "gke":
        return

    pattern = (
        params.gke_execution_pattern
        if params.gke_execution_pattern in ("standard_pods", "agent_sandbox", "agent_substrate")
        else "standard_pods"
    )

    # [GKE-S2] AI-generated code isolation: Agent Substrate (GKE Standard) or Agent Sandbox (Autopilot)
    code_pattern = pattern
    if "SANDBOX" in item_ids and pattern == "standard_pods":
        tf_txt = ""
        svc_path = root / resolve_terraform_rel_dir(root) / "service.tf"
        if svc_path.exists():
            tf_txt = svc_path.read_text(encoding="utf-8")
        autopilot = (
            bool(re.search(r"\benable_autopilot\s*=\s*true\b", tf_txt))
            if tf_txt
            else params.gke_cluster_mode != "standard"
        )
        code_pattern = "agent_sandbox" if autopilot else "agent_substrate"
    # [GKE-O2] Idle suspend/resume is an Agent Substrate capability → always the Substrate manifest
    if "SUBSTRATE" in item_ids:
        code_pattern = "agent_substrate"
    want_code_isolation = bool(item_ids & {"SANDBOX", "C4.3", "SUBSTRATE"})

    # 1. [GKE-S4] Standard GKE Pods -> Official Agent Gateway for GKE (Ingress + Egress)
    # Spec grounded verbatim in:
    #   https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/gke-overview
    #   https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-gke-ingress
    #   https://docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/set-up-gke-egress
    if pattern == "standard_pods" and "C4.4" in item_ids:
        enf_mode = params.agent_gateway_enforcement_mode or "DRY_RUN"
        record(
            "deployment/k8s/agent-gateway.yaml",
            f'''# [C4.4] Agent Gateway for GKE — Official Ingress & Egress Reference Manifest (Standard GKE Pods)
#
# Official Prerequisites (docs.cloud.google.com/gemini-enterprise-agent-platform/govern/gateways/gke-overview):
#   1. GKE cluster >= 1.37.2-gke.1042000 (Rapid release channel; Preview regions: us-central1, europe-west1):
#      gcloud beta container clusters create CLUSTER_NAME \\
#        --enable-ambient-networking --enable-dataplane-v2 --enable-fleet \\
#        --gateway-api=standard --location=us-central1 --release-channel=rapid \\
#        --workload-pool=PROJECT_ID.svc.id.goog
#   2. Regional proxy-only subnet in VPC (REQUIRED for gke-l7-rilb / gke-l7-regional-external-managed):
#      gcloud compute networks subnets create proxy-only-subnet --purpose=REGIONAL_MANAGED_PROXY \\
#        --role=ACTIVE --region=us-central1 --network=VPC_NETWORK --range=10.129.0.0/23
#   3. 9 Required APIs:
#      agentregistry.googleapis.com, certificatemanager.googleapis.com, gkehub.googleapis.com,
#      iap.googleapis.com, modelarmor.googleapis.com, networksecurity.googleapis.com,
#      networkservices.googleapis.com, privateca.googleapis.com, serviceextensions.googleapis.com
#   4. Compatibility: Agent Gateway for GKE supports Standard GKE Pods ONLY (NOT Agent Sandbox or Agent Substrate).
#   5. Before applying Egress (GCPBackend + GCPEgressPolicy), replace PROJECT_ID / REGION / SWP_NAME / REQUEST_TEMPLATE / RESPONSE_TEMPLATE.
#
# Apply when prerequisites are ready:
#   kubectl label namespace {project_name} networking.gke.io/dataplane-mode=ambient --overwrite
#   kubectl apply -f deployment/k8s/agent-gateway.yaml
---
# ==============================================================================
# PART 1: INGRESS (Client-to-Agent / MCP Server) — set-up-gke-ingress
# ==============================================================================
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: {project_name}-agent-gw
  namespace: {project_name}
spec:
  # Use gke-l7-regional-external-managed (+ Certificate Manager TLS 443) for external clients,
  # or gke-l7-rilb for internal VPC/hybrid clients.
  gatewayClassName: gke-l7-rilb
  listeners:
    - name: http
      protocol: HTTP
      port: 80
    # For HTTPS external access (gke-l7-regional-external-managed), switch listener to:
    # - name: https
    #   protocol: HTTPS
    #   port: 443
    #   tls:
    #     mode: Terminate
    #     options:
    #       networking.gke.io/cert-manager-certs: "REPLACE_WITH_CERT_NAME"
---
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: {project_name}-agent-route
  namespace: {project_name}
spec:
  parentRefs:
    - name: {project_name}-agent-gw
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: {project_name}
          port: 8080
---
# Official CRD that enables agentic protocol awareness (MCP / A2A) on the Gateway
apiVersion: networking.gke.io/v1
kind: GCPAgentGateway
metadata:
  name: {project_name}-ingress-agentgateway
  namespace: {project_name}
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: {project_name}-agent-gw
---
# Official L7 Authorization Policy (GCPAuthzPolicy v1 — enforcementLevel: L7 + plural targetRefs + MCP RBAC rules)
# Valid action enum values: ALLOW | DENY | CUSTOM | DENY_BY_DEFAULT
# (Note: IAM / IAP enforcement mode `{enf_mode}` is configured on the control-plane ExtensionBinding / SWP policy.)
apiVersion: networking.gke.io/v1
kind: GCPAuthzPolicy
metadata:
  name: {project_name}-ingress-authz
  namespace: {project_name}
spec:
  enforcementLevel: L7
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: {project_name}-agent-gw
  rules:
    - to:
        operations:
          - mcp:
              methods:
                - name: "tools/list"
                - name: "tools/call"
                  # Optionally restrict to specific tool names:
                  # params:
                  #   - type: Exact
                  #     value: "get_exchange_rate"
  action: ALLOW
---
# Official Model Armor Extension Binding (GCPExtensionBinding v1 — MUST target GCPAgentGateway, NOT Gateway!)
# Replace PROJECT_ID, REGION (us-central1 / europe-west1), REQUEST_TEMPLATE, and RESPONSE_TEMPLATE before applying.
apiVersion: networking.gke.io/v1
kind: GCPExtensionBinding
metadata:
  name: {project_name}-model-armor-binding
  namespace: {project_name}
spec:
  targetRefs:
    - group: networking.gke.io
      kind: GCPAgentGateway
      name: {project_name}-ingress-agentgateway
  producerExtension: "modelarmor.us-central1.rep.googleapis.com"
  # Set failOpen: false once Model Armor templates & IAM roles (roles/modelarmor.calloutUser) are verified.
  failOpen: true
  producerMetadata:
    model_armor_settings: '[{{"request_template_id":"projects/PROJECT_ID/locations/us-central1/templates/REQUEST_TEMPLATE","response_template_id":"projects/PROJECT_ID/locations/us-central1/templates/RESPONSE_TEMPLATE"}}]'
---
# ==============================================================================
# PART 2: EGRESS (Agent-to-Anywhere) — set-up-gke-egress
# Requires namespace label: kubectl label namespace {project_name} networking.gke.io/dataplane-mode=ambient
# ==============================================================================
# Routes GKE Ambient Proxy outbound traffic to Secure Web Proxy (SWP) with TLS inspection & Agent Registry discovery.
apiVersion: networking.gke.io/v1
kind: GCPBackend
metadata:
  name: {project_name}-swp-backend
  namespace: {project_name}
spec:
  serviceUris:
    - "//networkservices.googleapis.com/projects/PROJECT_ID/locations/us-central1/gateways/SWP_NAME"
---
apiVersion: networking.gke.io/v1
kind: GCPEgressPolicy
metadata:
  name: {project_name}-egress-policy
  namespace: {project_name}
spec:
  proxyRef:
    group: networking.gke.io
    kind: GCPBackend
    name: {project_name}-swp-backend
  to:
    # Exclude in-cluster Service CIDR & VPC CIDR so internal cluster traffic bypasses SWP
    excludeCIDRRanges:
      - "10.0.0.0/20"
      - "34.118.224.0/20"
#
# Control-Plane Egress Governance CLI steps (run once per SWP gateway — set-up-gke-egress):
#   1. Register SWP as Self-Managed Agent Gateway:
#      gcloud beta network-services agent-gateways import {project_name}-egress-gw \\
#        --location=us-central1 --source=egress-agw.yaml
#      (egress-agw.yaml: protocols: [MCP], registries: [...], selfManaged.resourceUris: ["projects/PROJECT_ID/locations/us-central1/gateways/SWP_NAME"])
#   2. Attach IAP Authorization ExtensionBinding (`producerExtension: iap.googleapis.com`, mode `{enf_mode}`):
#      gcloud beta service-extensions extension-bindings import {project_name}-iap-binding \\
#        --location=us-central1 --source=iap-binding.yaml
#   3. Attach Model Armor Egress Inspection ExtensionBinding:
#      gcloud beta service-extensions extension-bindings create {project_name}-ma-egress \\
#        --location=us-central1 --target-resource=projects/PROJECT_ID/locations/us-central1/agentGateways/{project_name}-egress-gw \\
#        --producer-extension="modelarmor.us-central1.rep.googleapis.com"
''',
            ["C4.4"],
        )

    # 3. [C4.3 / C4.4] Agent Sandbox on GKE (kubernetes-sigs/agent-sandbox — extensions.agents.x-k8s.io/v1beta1)
    if want_code_isolation and code_pattern == "agent_sandbox":
        record(
            "deployment/k8s/agent-sandbox.yaml",
            f'''# [C4.3 / C4.4] Agent Sandbox on GKE (kubernetes-sigs/agent-sandbox, extensions.agents.x-k8s.io/v1beta1)
#
# Use Case: Isolated execution of untrusted AI-generated code, shell tools, or browser/computer-use sessions.
# Supported Modes: GKE Autopilot & GKE Standard (with gVisor / microVM + SandboxWarmPool + Pod Snapshots + optional GPU/TPU).
# Compatibility Note: Agent Gateway for GKE is NOT yet supported on Agent Sandbox Pods.
#   Egress governance is enforced below via Zero-Metadata/Zero-RFC1918 Dataplane V2 NetworkPolicy + Secure Web Proxy (SWP).
#
# Production Best Practices:
#   - Enable GKE `secure-sandbox-policy` ValidatingAdmissionPolicy to enforce gVisor + non-root + limits cluster-wide.
#   - Provision per-tenant SandboxWarmPools for strict multi-tenant blast-radius isolation.
#   - For sub-second checkpoint/restore (GKE >= 1.35.3-gke.1234000, gVisor-only, non-E2 ComputeClass),
#     enable `--enable-pod-snapshots` on the cluster and create `PodSnapshotStorageConfig` + `PodSnapshotPolicy`
#     (`apiVersion: podsnapshot.gke.io/v1`) CRDs (Pod Snapshots are CRD-driven, NOT Pod annotations).
#
# Prerequisite: Install kubernetes-sigs/agent-sandbox controller (CRD extensions.agents.x-k8s.io/v1beta1) in cluster.
---
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxTemplate
metadata:
  name: {project_name}-code-sandbox
  namespace: {project_name}
spec:
  podTemplate:
    metadata:
      labels:
        app: {project_name}-sandbox
        sandbox-tier: untrusted-code
    spec:
      runtimeClassName: gvisor # Required
      automountServiceAccountToken: false # Required: never expose K8s API tokens to untrusted code
      securityContext:
        runAsNonRoot: true # Required
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile:
          type: RuntimeDefault
      nodeSelector:
        sandbox.gke.io/runtime: gvisor # Required on GKE Autopilot & Standard gVisor node pools
      tolerations:
        - key: "sandbox.gke.io/runtime"
          value: "gvisor"
          effect: "NoSchedule" # Required
      restartPolicy: OnFailure
      containers:
        - name: sandbox-runner
          image: python:3.12-slim
          command: ["sleep", "3600"]
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"] # Required
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "1"
              memory: "2Gi" # Required
          volumeMounts:
            - name: tmp-workspace
              mountPath: /tmp
      volumes:
        - name: tmp-workspace
          emptyDir:
            sizeLimit: 512Mi
---
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxWarmPool
metadata:
  name: {project_name}-sandbox-warmpool
  namespace: {project_name}
  labels:
    app: {project_name}-sandbox-warmpool
spec:
  replicas: 2
  sandboxTemplateRef:
    name: {project_name}-code-sandbox
---
apiVersion: extensions.agents.x-k8s.io/v1beta1
kind: SandboxClaim
metadata:
  name: {project_name}-sandbox-sample-claim
  namespace: {project_name}
spec:
  # Official v1beta1 schema: claims a pre-warmed Sandbox from the SandboxWarmPool
  warmPoolRef:
    name: {project_name}-sandbox-warmpool
---
# Strict Zero-Metadata & Zero-RFC1918 Egress NetworkPolicy for Untrusted Sandboxes
# Allows NodeLocal DNSCache (169.254.20.10) + kube-system DNS (53/5353),
# while explicitly blocking GKE Metadata Server (169.254.169.254/32) and all private RFC 1918 networks.
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {project_name}-sandbox-zero-trust-egress
  namespace: {project_name}
spec:
  podSelector:
    matchLabels:
      app: {project_name}-sandbox
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
        - protocol: UDP
          port: 5353
        - protocol: TCP
          port: 5353
    - to:
        - ipBlock:
            cidr: 169.254.20.10/32 # NodeLocal DNSCache
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 169.254.169.254/32 # Block GKE Metadata Server & Workload Identity credential theft
              - 169.254.169.252/32 # Block non-DPv2 GKE Metadata Server
              - 10.0.0.0/8         # Block private cluster/VPC lateral movement
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
''',
            ["C4.3", "SANDBOX"],
        )

    # 4. [C4.3 / C4.4] Agent Substrate on GKE (ate.dev/v1alpha1 WorkerPool + kubectl ate ActorTemplate)
    elif want_code_isolation and code_pattern == "agent_substrate":
        record(
            "deployment/k8s/agent-substrate.yaml",
            f'''# [C4.3 / C4.4] Agent Substrate on GKE (ate.dev/v1alpha1)
#
# CRITICAL REQUIREMENTS & LIMITATIONS (docs.cloud.google.com/kubernetes-engine/ai-ml/about-agent-substrate):
#   1. GKE Standard >= 1.37 ONLY — Autopilot, Extended release channel, and E2 mixed-CPU machine types are NOT supported.
#   2. Provision a dedicated system node pool (tainted for Agent Substrate control plane) + worker node pools (N2/C3/N4).
#   3. Delivers 10x agent density and <500ms RAM+disk suspend/resume (open TCP sockets do NOT survive suspend).
#      NOTE: Agent Substrate does NOT auto-suspend idle actors; your orchestrator/app triggers suspend/resume explicitly via `kubectl ate` / Substrate API.
#   4. Agent Gateway for GKE is NOT yet supported on Agent Substrate; use Agent Substrate's built-in Egress Gateway
#      (default-deny FQDN EgressPolicy + in-transit credential injection) and the Zero-Metadata/RFC1918 NetworkPolicy below.
#
# Step 1: Apply Kubernetes CRDs below via `kubectl apply -f deployment/k8s/agent-substrate.yaml`
---
apiVersion: ate.dev/v1alpha1
kind: WorkerPool
metadata:
  name: {project_name}-substrate-pool
  namespace: {project_name}
  labels:
    workload: {project_name}-agent
spec:
  replicas: 3
  # Replace with your Agent Substrate gVisor worker image (see docs.cloud.google.com/filestore/docs/agent-substrate)
  workerImage: ko://github.com/agent-substrate/substrate/cmd/ateom-gvisor
---
# Strict Zero-Metadata & Zero-RFC1918 Egress NetworkPolicy for Agent Substrate Workers/Actors
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: {project_name}-substrate-egress-guard
  namespace: {project_name}
spec:
  podSelector:
    matchLabels:
      workload: {project_name}-agent
  policyTypes:
    - Egress
  egress:
    - to:
        - namespaceSelector:
            matchLabels:
              kubernetes.io/metadata.name: kube-system
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
        - protocol: UDP
          port: 5353
        - protocol: TCP
          port: 5353
    - to:
        - ipBlock:
            cidr: 169.254.20.10/32 # NodeLocal DNSCache
      ports:
        - protocol: UDP
          port: 53
        - protocol: TCP
          port: 53
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 169.254.169.254/32
              - 169.254.169.252/32
              - 10.0.0.0/8
              - 172.16.0.0/12
              - 192.168.0.0/16
      ports:
        - protocol: TCP
          port: 443
#
# ==============================================================================
# Step 2: ActorTemplate (NOT a Kubernetes CRD! Managed via `kubectl ate` CLI + PostgreSQL)
# Save the snippet below as `actor-template.yaml` and register with:
#   kubectl ate create atespace {project_name}
#   kubectl ate create actor-template -f actor-template.yaml
# ==============================================================================
# metadata:
#   atespace: {project_name}
#   name: {project_name}-actor-template
# workerSelector:
#   matchLabels:
#     workload: {project_name}-agent
# containers:
# - name: agent
#   image: REGION-docker.pkg.dev/PROJECT_ID/{project_name}/{project_name}:latest
#   readyz:
#     httpGet:
#       path: /list-apps
#       port: 8080
#   # Optional: Mount Filestore Agent Volumes (CSIDriverConfig + substrate-volumepool-sc)
#   # volumeMounts:
#   # - name: shared-storage
#   #   mountPath: /mnt/shared
# sandboxConfig:
#   sandboxClass: SANDBOX_CLASS_GVISOR
#   configName: gvisor-default
# snapshotsConfig:
#   storageLocation: gs://REPLACE_WITH_SNAPSHOT_BUCKET/{project_name}
# # volumes:
# # - name: shared-storage
# #   externalVolumeTemplate:
# #     capacity: 5Gi
# #     storageClassName: substrate-volumepool-sc
''',
            ["C4.3", "SANDBOX", "SUBSTRATE"],
        )


# ---------------------------------------------------------------------------
# 4. Application Code: Managed Session & Memory Bank, Guardrails, CostGuard
# ---------------------------------------------------------------------------


def _prepare_session_and_memory_services_py(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    record: Any,
) -> None:
    if "C2.1" not in item_ids:
        return

    services_rel = "app/app_utils/services.py"
    services_content = '''"""Process-wide ADK Session & Artifact services shared by all serving surfaces.

[C2.1] Unified Session Architecture — Agent Platform Managed Sessions:
- Session Store : `VertexAiSessionService` via `AGENT_ENGINE_SESSION_ID` (fallback: `GOOGLE_CLOUD_AGENT_ENGINE_ID`).
- Artifacts     : `GcsArtifactService` via `LOGS_BUCKET_NAME`.

Note (Cloud Run OTel Safety):
  On Cloud Run, prefer `AGENT_ENGINE_SESSION_ID` instead of `GOOGLE_CLOUD_AGENT_ENGINE_ID`
  so ADK's `get_gcp_resource()` keeps native Cloud Run OTel resource detection.

Note (Custom Session URI):
  If `SESSION_SERVICE_URI` is explicitly provided, `create_session_service_from_options` is used.
  Cross-session long-term Memory Bank is an optional architecture Advisory.
"""

from __future__ import annotations

import functools
import os

from google.adk.artifacts import GcsArtifactService, InMemoryArtifactService
from google.adk.cli.service_registry import get_service_registry
from google.adk.cli.utils.service_factory import create_session_service_from_options

SESSION_SERVICE_URI = "shared://session"
ARTIFACT_SERVICE_URI = "shared://artifact"

_AGENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def _managed_agent_engine_id() -> str:
    """Resolves Reasoning Engine ID for Managed Sessions without breaking Cloud Run OTel detection."""
    return (
        os.environ.get("AGENT_ENGINE_SESSION_ID", "").strip()
        or os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "").strip()
    )


def _agent_engine_location() -> str:
    """Resolves regional location for Agent Platform Managed Sessions (never 'global')."""
    loc = (
        os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
        or os.environ.get("GOOGLE_CLOUD_REGION")
        or os.environ.get("GOOGLE_CLOUD_LOCATION")
        or "us-central1"
    )
    return "us-central1" if loc == "global" else loc


@functools.cache
def get_session_service():
    """Returns Agent Platform Managed `VertexAiSessionService` (or custom SESSION_SERVICE_URI / InMemory fallback)."""
    if uri := os.environ.get("SESSION_SERVICE_URI"):
        return create_session_service_from_options(
            base_dir=_AGENT_DIR, session_service_uri=uri
        )
    agent_engine_id = _managed_agent_engine_id()
    if agent_engine_id:
        from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService

        return VertexAiSessionService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=_agent_engine_location(),
            agent_engine_id=agent_engine_id,
        )

    from google.adk.sessions.in_memory_session_service import InMemorySessionService

    return InMemorySessionService()


@functools.cache
def get_artifact_service():
    """Process-wide artifact service: GCS when LOGS_BUCKET_NAME is set, else in-memory."""
    if bucket := os.environ.get("LOGS_BUCKET_NAME"):
        return GcsArtifactService(bucket_name=bucket)
    return InMemoryArtifactService()


_registry = get_service_registry()
_registry.register_session_service("shared", lambda uri, **kw: get_session_service())
_registry.register_artifact_service("shared", lambda uri, **kw: get_artifact_service())
'''
    record(services_rel, services_content, ["C2.1"])


_MEMORY_BANK_BLOCK = '''

# --- [BEGIN prod-ready: *-F2 Memory Bank] ---
# Cross-session long-term memory (`VertexAiMemoryBankService`) registered in ADK service registry.
MEMORY_SERVICE_URI = "shared://memory"


@functools.cache
def get_memory_service():
    """Returns Memory Bank (`VertexAiMemoryBankService`) when an Agent Platform instance ID is set, else in-memory (local dev)."""
    agent_engine_id = (
        os.environ.get("AGENT_ENGINE_SESSION_ID", "").strip()
        or os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID", "").strip()
    )
    if agent_engine_id:
        from google.adk.memory import VertexAiMemoryBankService

        location = (
            os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_LOCATION")
            or os.environ.get("GOOGLE_CLOUD_REGION")
            or "us-central1"
        )
        return VertexAiMemoryBankService(
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location="us-central1" if location == "global" else location,
            agent_engine_id=agent_engine_id,
        )
    from google.adk.memory import InMemoryMemoryService

    return InMemoryMemoryService()


try:
    from google.adk.cli.service_registry import get_service_registry as _get_mem_registry

    _get_mem_registry().register_memory_service("shared", lambda uri, **kw: get_memory_service())
except Exception:
    pass
# --- [END prod-ready: *-F2 Memory Bank] ---
'''


def _prepare_memory_bank_services_py(
    root: Path,
    target: TargetType,
    item_ids: set[str],
    planned_files: dict[str, tuple[str, list[str]]],
) -> None:
    """[AR-F2 / CR-F2 / GKE-F2] Adds Memory Bank (`VertexAiMemoryBankService`) + registry wiring to services.py."""
    if "MEMBANK" not in item_ids:
        return
    services_rel = "app/app_utils/services.py"
    path = root / services_rel
    if target == "agent_runtime" and services_rel not in planned_files and not path.exists():
        return
    if services_rel in planned_files:
        content, items = planned_files[services_rel]
    else:
        content, items = (path.read_text(encoding="utf-8") if path.exists() else ""), []
    if "VertexAiMemoryBankService" in content:
        return
    header = ""
    if "import functools" not in content:
        header += "import functools\n"
    if "import os" not in content:
        header += "import os\n"
    if not content.strip():
        content = '"""Process-wide ADK services (Memory Bank)."""\n\n' + header
    elif header:
        if content.lstrip().startswith(("from __future__", '"""')) and "\nfrom __future__ import annotations\n" in content:
            content = content.replace(
                "\nfrom __future__ import annotations\n",
                "\nfrom __future__ import annotations\n\n" + header,
                1,
            )
        else:
            content = header + content
    new_content = content.rstrip() + "\n" + _MEMORY_BANK_BLOCK
    planned_files[services_rel] = (new_content, sorted(set(items) | {"MEMBANK"}))


_PUBLIC_IAM_RES_RE = re.compile(
    r'resource\s+"google_cloud_run(?:_v2)?_service_iam_(?:member|binding)"\s+"[^"]+"\s*\{'
)


def _strip_public_invoker_blocks(txt: str) -> str:
    """Removes Cloud Run IAM member/binding resources granting allUsers / allAuthenticatedUsers."""
    out = txt
    pos = 0
    while True:
        m = _PUBLIC_IAM_RES_RE.search(out, pos)
        if not m:
            break
        depth, i = 0, m.end() - 1
        while i < len(out):
            if out[i] == "{":
                depth += 1
            elif out[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        block = out[m.start() : i + 1]
        if re.search(r'"all(?:Authenticated)?Users"', block):
            replacement = (
                "# [CR-S1] Removed public invoker binding (allUsers). Cloud Run requires IAM authentication;\n"
                "# grant roles/run.invoker only to the caller's dedicated service account."
            )
            out = out[: m.start()] + replacement + out[i + 1 :]
            pos = m.start() + len(replacement)
        else:
            pos = i + 1
    return re.sub(r"(invoker_iam_disabled\s*=\s*)true\b", r"\1false", out)


def _prepare_cloud_run_authn(
    root: Path,
    target: TargetType,
    item_ids: set[str],
    planned_files: dict[str, tuple[str, list[str]]],
) -> None:
    """[CR-S1] Enforce IAM-authenticated invocation (no allUsers invoker / --allow-unauthenticated)."""
    if "AUTHN" not in item_ids or target != "cloud_run":
        return
    candidates: list[str] = []
    for d in {resolve_terraform_rel_dir(root), "deployment/terraform", "deployment/terraform/cicd"}:
        if (root / d).is_dir():
            candidates.extend(f"{d}/{p.name}" for p in sorted((root / d).glob("*.tf")))
    candidates.append("Makefile")
    for pattern in (".cloudbuild/*.yaml", ".github/workflows/*.yaml", ".github/workflows/*.yml", "*.sh", "scripts/*.sh", "deployment/*.sh"):
        candidates.extend(p.relative_to(root).as_posix() for p in sorted(root.glob(pattern)))
    for rel in dict.fromkeys(candidates):
        if rel in planned_files:
            content, items = planned_files[rel]
        else:
            path = root / rel
            if not path.is_file():
                continue
            content, items = path.read_text(encoding="utf-8"), []
        if rel.endswith(".tf"):
            updated = _strip_public_invoker_blocks(content)
        else:
            updated = re.sub(r"(?<!no-)--allow-unauthenticated\b", "--no-allow-unauthenticated", content)
        if updated != content:
            planned_files[rel] = (updated, sorted(set(items) | {"AUTHN"}))


_AGENT_MEMBANK_HELPERS = '''# --- [BEGIN prod-ready: *-F2 Memory Bank] ---
from google.adk.tools import preload_memory


async def auto_save_session_to_memory_callback(callback_context):
    """[prod-ready: *-F2] Persist completed session turns into Memory Bank (ADK official callback pattern)."""
    if hasattr(callback_context, "add_session_to_memory"):
        try:
            await callback_context.add_session_to_memory()
        except ValueError:
            # Skip silently when memory_service is not wired (e.g., local unit tests without Runner memory_service)
            pass
# --- [END prod-ready: *-F2 Memory Bank] ---
'''


def _split_inline_comment(line: str) -> tuple[str, str]:
    """Splits a single Python line into (code, comment) without misreading '#' inside string literals."""
    in_q: str | None = None
    i = 0
    while i < len(line):
        ch = line[i]
        if in_q:
            if ch == "\\" and i + 1 < len(line):
                i += 2
                continue
            if line.startswith(in_q, i):
                i += len(in_q)
                in_q = None
                continue
        else:
            if line[i : i + 3] in ('"""', "'''"):
                in_q = line[i : i + 3]
                i += 3
                continue
            if ch in ('"', "'"):
                in_q = ch
            elif ch == "#":
                return line[:i], line[i:]
        i += 1
    return line, ""


def _append_kwarg_block(body: str, block_lines: list[str]) -> str:
    """Appends keyword-argument lines inside a Python call body, ensuring a trailing comma on the last code line."""
    lines = body.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        raw = lines[idx]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        code_part, comment_part = _split_inline_comment(raw)
        code_r = code_part.rstrip()
        if code_r and not code_r.endswith(","):
            pad = code_part[len(code_r) :]
            lines[idx] = f"{code_r},{pad}{comment_part}" if comment_part else f"{code_r},"
        break
    prefix = "\n".join(lines).rstrip("\n")
    return (prefix + "\n" if prefix else "\n") + "\n".join(block_lines) + "\n"


def _patch_agent_py_memory_bank(txt: str) -> str:
    """Non-destructively wires ADK Memory Bank read tool (`preload_memory`) & write callback (`add_session_to_memory`).

    Preserves all original lines as `# [ORIGINAL before prod-ready *-F2] ...` comments and wraps
    every injected/modified region with `# --- [BEGIN/END prod-ready: *-F2 Memory Bank] ---`.
    """
    has_read = bool(re.search(r"\b(?:PreloadMemoryTool|preload_memory|LoadMemoryTool|load_memory)\b", txt))
    has_write = bool(re.search(r"\b(?:add_session_to_memory|add_events_to_memory|add_memory)\b", txt))
    if has_read and has_write:
        return txt

    out = txt
    # 1. Add helper import + callback right before the first `Agent(` / `LlmAgent(` assignment (or after imports)
    need_import = not has_read
    need_cb = not has_write
    if need_import or need_cb:
        helper_lines = ["# --- [BEGIN prod-ready: *-F2 Memory Bank] ---"]
        if need_import:
            helper_lines.append("from google.adk.tools import preload_memory")
        if need_cb:
            if need_import:
                helper_lines.append("")
                helper_lines.append("")
            helper_lines.extend([
                "async def auto_save_session_to_memory_callback(callback_context):",
                '    """[prod-ready: *-F2] Persist completed session turns into Memory Bank (ADK official callback pattern)."""',
                '    if hasattr(callback_context, "add_session_to_memory"):',
                "        try:",
                "            await callback_context.add_session_to_memory()",
                "        except ValueError:",
                "            pass  # Skip when memory_service is not wired (e.g., local unit tests)",
            ])
        helper_lines.append("# --- [END prod-ready: *-F2 Memory Bank] ---")
        helper_block = "\n".join(helper_lines) + "\n\n\n"

        agent_assign = re.search(r"^[ \t]*\w+\s*=\s*(?:LlmAgent|Agent)\s*\(", out, flags=re.MULTILINE)
        if agent_assign:
            out = out[: agent_assign.start()] + helper_block + out[agent_assign.start() :]
        else:
            out = helper_block + out

    # 2. Locate the first `Agent(` / `LlmAgent(` call span cleanly (balanced parens)
    m_call = re.search(r"\b(?:LlmAgent|Agent)\s*\(", out)
    if not m_call:
        return out
    open_idx = m_call.end() - 1
    depth, i = 0, open_idx
    in_str: str | None = None
    while i < len(out):
        ch = out[i]
        if in_str:
            if ch == "\\" and i + 1 < len(out):
                i += 2
                continue
            if out.startswith(in_str, i):
                i += len(in_str)
                in_str = None
                continue
            i += 1
            continue
        if out[i : i + 3] in ('"""', "'''"):
            in_str = out[i : i + 3]
            i += 3
            continue
        if ch in ('"', "'"):
            in_str = ch
            i += 1
            continue
        if ch == "#":
            nl = out.find("\n", i)
            i = len(out) if nl == -1 else nl + 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    if depth != 0:
        return out

    call_body = out[open_idx + 1 : i]
    updated_body = call_body
    m_indent = re.search(r"^([ \t]+)\w+\s*=", call_body, flags=re.MULTILINE)
    kw_indent = m_indent.group(1) if m_indent else "    "

    # 2a. Wire `preload_memory` into `tools=[...]` (commenting out original line(s)!)
    if not has_read:
        m_tools = re.search(r"(^[ \t]*)(tools\s*=\s*\[)([^\]]*)(\][ \t]*,?)", updated_body, flags=re.MULTILINE)
        if m_tools:
            indent, prefix, inner, suffix = m_tools.group(1), m_tools.group(2), m_tools.group(3), m_tools.group(4)
            suffix_comma = suffix if suffix.rstrip().endswith(",") else suffix.rstrip() + ","
            orig_lines = m_tools.group(0).splitlines()
            commented_orig = "\n".join(f"{indent}# [ORIGINAL before prod-ready *-F2] {ln.lstrip()}" for ln in orig_lines)
            inner_stripped = inner.strip()
            if "\n" in inner:
                # Multiline tools list
                item_indent = indent + "    "
                cleaned_inner = inner.rstrip()
                if cleaned_inner and not cleaned_inner.endswith(","):
                    cleaned_inner += ","
                new_tools = f"{indent}{prefix}{cleaned_inner}\n{item_indent}preload_memory,\n{indent}{suffix_comma}"
            else:
                new_inner = f"{inner_stripped.rstrip(',')}, preload_memory" if inner_stripped else "preload_memory"
                new_tools = f"{indent}{prefix}{new_inner}{suffix_comma}"
            replacement = (
                f"{indent}# --- [BEGIN prod-ready: *-F2 Memory Bank (read tool)] ---\n"
                f"{commented_orig}\n"
                f"{new_tools}\n"
                f"{indent}# --- [END prod-ready: *-F2 Memory Bank (read tool)] ---"
            )
            updated_body = updated_body[: m_tools.start()] + replacement + updated_body[m_tools.end() :]
        else:
            # No tools= kwarg yet -> inject before closing paren
            updated_body = _append_kwarg_block(
                updated_body,
                [
                    f"{kw_indent}# --- [BEGIN prod-ready: *-F2 Memory Bank (read tool)] ---",
                    f"{kw_indent}tools=[preload_memory],",
                    f"{kw_indent}# --- [END prod-ready: *-F2 Memory Bank (read tool)] ---",
                ],
            )

    # 2b. Wire `after_agent_callback=auto_save_session_to_memory_callback` (preserving any existing callback!)
    if not has_write:
        m_cb = re.search(r"(^[ \t]*)(after_agent_callback\s*=\s*)([^\n,]+)(,?)", updated_body, flags=re.MULTILINE)
        if m_cb:
            indent, prefix, expr, comma = m_cb.group(1), m_cb.group(2), m_cb.group(3).strip(), m_cb.group(4) or ","
            commented_orig = f"{indent}# [ORIGINAL before prod-ready *-F2] {m_cb.group(0).lstrip()}"
            if expr.startswith("[") and expr.endswith("]"):
                inner_cb = expr[1:-1].strip().rstrip(",")
                new_expr = f"[{inner_cb}, auto_save_session_to_memory_callback]" if inner_cb else "[auto_save_session_to_memory_callback]"
            else:
                new_expr = f"[{expr}, auto_save_session_to_memory_callback]"
            replacement = (
                f"{indent}# --- [BEGIN prod-ready: *-F2 Memory Bank (write hook)] ---\n"
                f"{commented_orig}\n"
                f"{indent}{prefix}{new_expr}{comma}\n"
                f"{indent}# --- [END prod-ready: *-F2 Memory Bank (write hook)] ---"
            )
            updated_body = updated_body[: m_cb.start()] + replacement + updated_body[m_cb.end() :]
        else:
            updated_body = _append_kwarg_block(
                updated_body,
                [
                    f"{kw_indent}# --- [BEGIN prod-ready: *-F2 Memory Bank (write hook)] ---",
                    f"{kw_indent}after_agent_callback=auto_save_session_to_memory_callback,",
                    f"{kw_indent}# --- [END prod-ready: *-F2 Memory Bank (write hook)] ---",
                ],
            )

    return out[: open_idx + 1] + updated_body + out[i:]


def _wire_memory_service_in_fastapi(txt: str) -> str:
    """Wires `memory_service=services.get_memory_service()` and `memory_service_uri=services.MEMORY_SERVICE_URI` cleanly."""
    out = txt
    if "Runner(" in out and "memory_service=" not in out:
        out = re.sub(
            r"(^[ \t]*)(session_service\s*=\s*services\.get_session_service\(\),)",
            (
                r"\1\2\n"
                r"\1# --- [BEGIN prod-ready: *-F2 Memory Bank] ---\n"
                r"\1memory_service=services.get_memory_service(),\n"
                r"\1# --- [END prod-ready: *-F2 Memory Bank] ---"
            ),
            out,
            count=1,
            flags=re.MULTILINE,
        )
    if "get_fast_api_app(" in out and "memory_service_uri=" not in out:
        before_sub = out
        out = re.sub(
            r"(^[ \t]*)(session_service_uri\s*=\s*services\.SESSION_SERVICE_URI,)",
            (
                r"\1\2\n"
                r"\1# --- [BEGIN prod-ready: *-F2 Memory Bank] ---\n"
                r"\1memory_service_uri=services.MEMORY_SERVICE_URI,\n"
                r"\1# --- [END prod-ready: *-F2 Memory Bank] ---"
            ),
            out,
            count=1,
            flags=re.MULTILINE,
        )
        if out == before_sub:
            if "from app.app_utils import services" not in out:
                if "from google.adk.cli.fast_api import get_fast_api_app" in out:
                    out = out.replace(
                        "from google.adk.cli.fast_api import get_fast_api_app",
                        "from google.adk.cli.fast_api import get_fast_api_app\nfrom app.app_utils import services",
                        1,
                    )
                else:
                    out = "from app.app_utils import services\n" + out
            out = re.sub(
                r"(^[ \t]*)(agents_dir\s*=\s*[^,\n]+,)",
                (
                    r"\1\2\n"
                    r"\1# --- [BEGIN prod-ready: *-RS2 Memory Bank] ---\n"
                    r"\1memory_service_uri=services.MEMORY_SERVICE_URI,\n"
                    r"\1# --- [END prod-ready: *-RS2 Memory Bank] ---"
                ),
                out,
                count=1,
                flags=re.MULTILINE,
            )
    return out


def _prepare_agent_and_fastapi_py(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    agent_rel = "app/agent.py"
    agent_path = root / agent_rel
    if agent_path.exists():
        txt = agent_path.read_text(encoding="utf-8")
        applied_agent: list[str] = []

        # NOTE: Gemini retry_options (legacy C6.2) is ADK agent-code territory — never injected by the checklist.
        # [*-F2] Memory Bank requires ADK read tool (`preload_memory`) + write callback (`add_session_to_memory`).
        # Original lines are preserved as `# [ORIGINAL before prod-ready *-F2]` comments; user approves diff before apply.
        if "MEMBANK" in item_ids:
            patched_mem = _patch_agent_py_memory_bank(txt)
            if patched_mem != txt:
                txt = patched_mem
                applied_agent.append("MEMBANK")

        if applied_agent:
            record(agent_rel, txt, applied_agent)

    fastapi_rel = "app/fast_api_app.py"
    fastapi_path = root / fastapi_rel
    if fastapi_path.exists():
        # Surgical in-place patch: preserves existing A2A routes (`attach_a2a_routes`), `app.description`, & `__main__`!
        existing_fa = fastapi_path.read_text(encoding="utf-8")
        updated_fa = existing_fa
        applied_fa: list[str] = []

        if target in ("cloud_run", "gke") and (
            ("C2.1" in item_ids and "get_session_service" not in updated_fa and "SESSION_SERVICE_URI" not in updated_fa)
            or ("MEMBANK" in item_ids and "get_fast_api_app(" not in updated_fa and "memory_service" not in updated_fa)
        ):
            # Minimal stub without services.py wiring: upgrade to official agents-cli v1.5.0 lifespan + VertexAiSessionService
            if "attach_a2a_routes" not in updated_fa:
                updated_fa = f'''"""FastAPI entrypoint compatible with google-agents-cli v1.5.0 (VertexAiSessionService)."""

import contextlib
import os
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from app.app_utils import services

load_dotenv()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)
otel_to_cloud = True

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=otel_to_cloud,
    lifespan=lifespan,
)
app.title = "{project_name}"
'''
                for _it in ("C1.2", "C2.1", "C5.1"):
                    if _it in item_ids and _it not in applied_fa:
                        applied_fa.append(_it)

        if target in ("cloud_run", "gke") and "C5.1" in item_ids:
            before_otel = updated_fa
            if re.search(r"otel_to_cloud\s*=\s*False\b", updated_fa):
                updated_fa = re.sub(r"(otel_to_cloud\s*=\s*)False\b", r"\1True", updated_fa)
            elif "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY" in updated_fa:
                updated_fa = re.sub(
                    r"otel_to_cloud\s*=\s*os\.environ\.get\([\s\S]*?\)\.lower\(\)\s*in\s*\([^)]*\)",
                    "otel_to_cloud = True",
                    updated_fa,
                )
            elif "otel_to_cloud" not in updated_fa and "get_fast_api_app(" in updated_fa:
                updated_fa = updated_fa.replace(
                    "get_fast_api_app(",
                    "get_fast_api_app(\n    otel_to_cloud=True,",
                    1,
                )
            if updated_fa != before_otel and "C5.1" not in applied_fa:
                applied_fa.append("C5.1")

        if "MEMBANK" in item_ids:
            before_mem_fa = updated_fa
            updated_fa = _wire_memory_service_in_fastapi(updated_fa)
            if updated_fa != before_mem_fa and "MEMBANK" not in applied_fa:
                applied_fa.append("MEMBANK")

        if updated_fa != existing_fa:
            record(fastapi_rel, updated_fa, applied_fa or ["C5.1"])
    elif target in ("cloud_run", "gke"):
        use_services = "C2.1" in item_ids or "MEMBANK" in item_ids
        if use_services:
            fastapi_content = f'''"""FastAPI entrypoint compatible with google-agents-cli v1.5.0 (VertexAiSessionService)."""

import contextlib
import os
from collections.abc import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.runners import Runner

from app.app_utils import services

load_dotenv()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)
otel_to_cloud = True

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from app.agent import app as adk_app

    runner = Runner(
        app=adk_app,
        session_service=services.get_session_service(),
        artifact_service=services.get_artifact_service(),
        auto_create_session=True,
    )
    app.state.runner = runner
    app.state.agent_app_name = adk_app.name
    yield


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=services.ARTIFACT_SERVICE_URI,
    allow_origins=allow_origins,
    session_service_uri=services.SESSION_SERVICE_URI,
    otel_to_cloud=otel_to_cloud,
    lifespan=lifespan,
)
app.title = "{project_name}"
'''
        else:
            fastapi_content = f'''"""FastAPI entrypoint compatible with google-agents-cli v1.5.0."""

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

load_dotenv()
allow_origins = (
    os.getenv("ALLOW_ORIGINS", "").split(",") if os.getenv("ALLOW_ORIGINS") else None
)
otel_to_cloud = True

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    allow_origins=allow_origins,
    otel_to_cloud=otel_to_cloud,
)
app.title = "{project_name}"
'''
        if "MEMBANK" in item_ids:
            fastapi_content = _wire_memory_service_in_fastapi(fastapi_content)
        record(fastapi_rel, fastapi_content, ["C1.2", "C2.1", "C5.1"] + (["MEMBANK"] if "MEMBANK" in item_ids else []))


def _prepare_pyproject_toml(
    root: Path, target: TargetType, item_ids: set[str], record: Any
) -> None:
    """Ensures google-adk extras ([gcp,otel-gcp] for C5.1, [bigquery-analytics] for BQ_ANALYTICS)."""
    need_otel = target in ("cloud_run", "gke") and "C5.1" in item_ids
    need_bq = "BQ_ANALYTICS" in item_ids
    if not (need_otel or need_bq):
        return
    pyproj_rel = "pyproject.toml"
    pyproj_path = root / pyproj_rel
    if not pyproj_path.exists():
        return
    txt = pyproj_path.read_text(encoding="utf-8")

    req_extras: list[str] = []
    applied: list[str] = []
    if need_otel and not _has_adk_otel_gcp_extras(txt):
        req_extras.extend(["gcp", "otel-gcp"])
        applied.append("C5.1")
    if need_bq:
        has_bq = any(
            "bigquery-analytics" in {e.strip().lower() for e in m.group(1).split(",")}
            or "all" in {e.strip().lower() for e in m.group(1).split(",")}
            for m in re.finditer(r'["\']google-adk\[([^\]]+)\]', txt)
        )
        if not has_bq:
            req_extras.append("bigquery-analytics")
            applied.append("BQ_ANALYTICS")
    if not req_extras:
        return

    pattern = re.compile(r'(["\'])google-adk(?:\[([^\]]+)\])?([^"\']*)\1')
    matches = list(pattern.finditer(txt))
    if not matches:
        return

    chosen = next(
        (
            m
            for m in matches
            if {e.strip().lower() for e in (m.group(2) or "").split(",") if e.strip()} != {"eval"}
        ),
        matches[0],
    )
    quote = chosen.group(1)
    existing_extras = [e.strip() for e in (chosen.group(2) or "").split(",") if e.strip()]
    for req_extra in req_extras:
        if req_extra not in {e.lower() for e in existing_extras}:
            existing_extras.append(req_extra)
    spec = chosen.group(3) or ""
    replacement = f'{quote}google-adk[{",".join(existing_extras)}]{spec}{quote}'
    updated = txt[: chosen.start()] + replacement + txt[chosen.end() :]
    if updated != txt:
        record(pyproj_rel, updated, applied)


def _prepare_optional_addons(
    root: Path,
    project_name: str,
    target: TargetType,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    """Tier 3 Optional Production Add-ons (explicit opt-in only):
    - BQ_ANALYTICS (*-OB2): app/app_utils/bq_analytics.py + deployment/terraform/.../bq_analytics.tf
    - MODEL_ARMOR (AR-S6 / CR-S7 / GKE-S6): deployment/terraform/.../model_armor.tf
    """
    tf_rel = resolve_terraform_rel_dir(root)

    if "BQ_ANALYTICS" in item_ids:
        ds = (params.bq_analytics_dataset or "agent_analytics").strip()
        tbl = (params.bq_analytics_table or "agent_events").strip()
        bq_py = f'''"""[Tier 3 Optional · *-OB2] BigQuery Agent Analytics Plugin helper.

Official Reference:
- https://adk.dev/integrations/bigquery-agent-analytics/
- https://google.github.io/agents-cli/guide/deployment/

Attach ``build_bq_analytics_plugin()`` to your ADK ``App(plugins=[...])`` or
``AdkApp(plugins=[...])`` to stream structured agent events asynchronously via
the BigQuery Storage Write API.
"""

from __future__ import annotations

import os
from typing import Any


def build_bq_analytics_plugin(
    project_id: str | None = None,
    dataset_id: str | None = None,
    table_id: str | None = None,
) -> Any:
    """Returns a configured ADK BigQueryAgentAnalyticsPlugin instance."""
    from google.adk.plugins.bigquery_agent_analytics_plugin import (
        BigQueryAgentAnalyticsPlugin,
    )

    resolved_project = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID", "")
    resolved_dataset = dataset_id or os.getenv("BQ_ANALYTICS_DATASET", "{ds}")
    resolved_table = table_id or os.getenv("BQ_ANALYTICS_TABLE", "{tbl}")
    return BigQueryAgentAnalyticsPlugin(
        project_id=resolved_project,
        dataset_id=resolved_dataset,
        table_id=resolved_table,
    )
'''
        record("app/app_utils/bq_analytics.py", bq_py, ["BQ_ANALYTICS"])

        bq_tf = f'''# {tf_rel}/bq_analytics.tf
# [Tier 3 Optional · *-OB2] BigQuery Agent Analytics dataset & API enablement
# Official Reference: https://adk.dev/integrations/bigquery-agent-analytics/

variable "bq_analytics_dataset" {{
  type        = string
  description = "BigQuery dataset ID for ADK BigQueryAgentAnalyticsPlugin structured agent events"
  default     = "{ds}"
}}

resource "google_project_service" "bigquery_analytics_api" {{
  project            = var.project_id
  service            = "bigquery.googleapis.com"
  disable_on_destroy = false
}}

resource "google_bigquery_dataset" "agent_analytics" {{
  project       = var.project_id
  dataset_id    = var.bq_analytics_dataset
  friendly_name = "${{var.project_name}} Agent Analytics"
  description   = "Structured ADK agent events streamed via BigQueryAgentAnalyticsPlugin (Storage Write API)"
  location      = var.region

  depends_on = [google_project_service.bigquery_analytics_api]
}}

output "bq_analytics_dataset_id" {{
  description = "BigQuery dataset ID for ADK BigQueryAgentAnalyticsPlugin"
  value       = google_bigquery_dataset.agent_analytics.dataset_id
}}
'''
        record(f"{tf_rel}/bq_analytics.tf", bq_tf, ["BQ_ANALYTICS"])

    if "MODEL_ARMOR" in item_ids:
        conf = (params.model_armor_confidence or "MEDIUM_AND_ABOVE").strip().upper()
        if conf not in ("LOW_AND_ABOVE", "MEDIUM_AND_ABOVE", "HIGH"):
            conf = "MEDIUM_AND_ABOVE"
        sdp_block = (
            '''    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }'''
            if params.model_armor_enable_sdp
            else "    # sdp_settings disabled by user configuration"
        )
        uri_block = (
            '''    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }'''
            if params.model_armor_enable_malicious_uri
            else "    # malicious_uri_filter_settings disabled by user configuration"
        )
        ma_tf = f'''# {tf_rel}/model_armor.tf
# [Tier 3 Optional · Model Armor] Prompt Injection, Jailbreak, SDP & Malicious URI guardrails
# Official References:
# - https://cloud.google.com/security-command-center/docs/model-armor-overview
# - https://cloud.google.com/architecture/framework/perspectives/ai-ml

resource "google_project_service" "model_armor_api" {{
  project            = var.project_id
  service            = "modelarmor.googleapis.com"
  disable_on_destroy = false
}}

resource "google_model_armor_template" "agent_guardrail" {{
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  template_id = "${{var.project_name}}-guardrail"

  filter_config {{
    pi_and_jailbreak_filter_settings {{
      filter_enforcement = "ENABLED"
      confidence_level   = "{conf}"
    }}
{sdp_block}
{uri_block}
  }}

  depends_on = [google_project_service.model_armor_api]
}}

output "model_armor_template_name" {{
  description = "Model Armor template resource name for Gateway or inline API sanitization"
  value       = google_model_armor_template.agent_guardrail.name
}}
'''
        record(f"{tf_rel}/model_armor.tf", ma_tf, ["MODEL_ARMOR"])


# ---------------------------------------------------------------------------
# 5. Dockerfile, CI/CD Eval Gate & Cloud Run Canary Rollout
# ---------------------------------------------------------------------------


def _prepare_dockerfile(
    root: Path, target: TargetType, item_ids: set[str], record: Any
) -> None:
    need_non_root = "C4.3" in item_ids
    need_agw_ca = target == "agent_runtime" and "C4.4" in item_ids
    if not (need_non_root or need_agw_ca):
        return

    agw_ca_snippet = """# [C4.4] Trust Agent Gateway TLS inspection root CA certificates for BYOC Agent Runtime egress
ARG AGENT_GATEWAY_ROOT_CERTIFICATES
RUN if [ -n "$AGENT_GATEWAY_ROOT_CERTIFICATES" ]; then \\
      echo "Installing Agent Gateway root certificates..."; \\
      printf "%b" "$AGENT_GATEWAY_ROOT_CERTIFICATES" | awk 'BEGIN {c=0} /BEGIN CERTIFICATE/ {c++} c > 0 { print > "/usr/local/share/ca-certificates/agw-" c ".crt" }'; \\
      update-ca-certificates; \\
    fi
ENV GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}
ENV REQUESTS_CA_BUNDLE=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}
ENV SSL_CERT_FILE=${AGENT_GATEWAY_ROOT_CERTIFICATES:+/etc/ssl/certs/ca-certificates.crt}
"""

    non_root_only = """# [C4.3] Create and switch to non-root user (UID 10001) for container runtime security
RUN groupadd --gid 10001 appuser && \\
    useradd --uid 10001 --gid 10001 --create-home --shell /sbin/nologin appuser && \\
    chown -R appuser:appuser /code
USER 10001:10001"""

    rel = "Dockerfile"
    path = root / rel
    if path.exists():
        txt = path.read_text(encoding="utf-8")
        applied: list[str] = []

        if need_agw_ca and "AGENT_GATEWAY_ROOT_CERTIFICATES" not in txt:
            if "# [C4.3]" in txt:
                txt = txt.replace("# [C4.3]", agw_ca_snippet + "\n# [C4.3]", 1)
            elif "USER 10001" in txt:
                txt = txt.replace("USER 10001", agw_ca_snippet + "\nUSER 10001", 1)
            elif "EXPOSE 8080" in txt:
                txt = txt.replace("EXPOSE 8080", agw_ca_snippet + "\nEXPOSE 8080", 1)
            else:
                txt += "\n" + agw_ca_snippet + "\n"
            applied.append("C4.4")

        if need_non_root and "USER 10001" not in txt:
            if "EXPOSE 8080" in txt:
                txt = txt.replace("EXPOSE 8080", non_root_only + "\n\nEXPOSE 8080", 1)
            else:
                txt += "\n" + non_root_only + "\n"
            applied.append("C4.3")

        if applied:
            record(rel, txt, applied)
    else:
        sections = [
            '''FROM python:3.12-slim

RUN pip install --no-cache-dir uv==0.8.13

WORKDIR /code

COPY ./pyproject.toml ./README.md* ./uv.lock* ./
COPY ./app ./app

RUN uv sync --frozen

ARG AGENT_VERSION=0.0.0
ENV AGENT_VERSION=${AGENT_VERSION}'''
        ]
        applied_new: list[str] = []
        if need_agw_ca:
            sections.append(agw_ca_snippet.rstrip())
            applied_new.append("C4.4")
        if need_non_root:
            sections.append(non_root_only)
            applied_new.append("C4.3")
        sections.append(
            'EXPOSE 8080\n\nCMD ["uv", "run", "uvicorn", "app.fast_api_app:app", "--host", "0.0.0.0", "--port", "8080"]\n'
        )
        record(rel, "\n\n".join(sections), applied_new)


def _prepare_eval_and_cicd(
    root: Path,
    project_name: str,
    target: str,
    item_ids: set[str],
    params: CustomParams,
    record: Any,
) -> None:
    """[*-D2 / C7.1] Generates agents-cli v1.5.0 evaluation assets and inserts the eval gate into an existing staging pipeline.

    The pipeline itself is never generated here (use ``agents-cli scaffold enhance``).
    """
    if "C7.1" not in item_ids:
        return

    threshold = float(getattr(params, "eval_threshold", 3.5) or 3.5)

    # 1. Official agents-cli v1.5.0 tests/eval/eval_config.yaml
    eval_cfg_rel = "tests/eval/eval_config.yaml"
    eval_cfg_path = root / eval_cfg_rel
    if not eval_cfg_path.exists() or "metrics_to_run" not in eval_cfg_path.read_text(encoding="utf-8"):
        eval_cfg_content = """# [C7.1] Official agents-cli v1.5.0 Evaluation Configuration
# Run locally or in CI:
#   uvx google-agents-cli@1.5.0 eval run \\
#     --config tests/eval/eval_config.yaml \\
#     --dataset tests/eval/datasets/basic-dataset.json
metrics_to_run:
  - custom_response_quality

custom_metrics:
  - name: custom_response_quality
    custom_function_file: response_quality.py
  - name: agent_turn_count
    custom_function: |
      def evaluate(instance):
          turns = (instance.get("agent_data") or {}).get("turns", [])
          return {'score': len(turns)}
"""
        record(eval_cfg_rel, eval_cfg_content, ["C7.1"])

    # 2. Official agents-cli v1.5.0 tests/eval/response_quality.py (Local LLM-as-Judge)
    rq_rel = "tests/eval/response_quality.py"
    rq_path = root / rq_rel
    if not rq_path.exists():
        rq_content = '''"""[C7.1] Official agents-cli v1.5.0 local LLM-as-judge metric for agent response quality."""

from typing import Any
from google import genai
from google.genai import types
import pydantic

JUDGE_MODEL = "gemini-2.5-flash"

RUBRIC = """\\
Evaluate the AI assistant's response to the user's prompt.

User prompt: {prompt}
Assistant response: {response}

Score on a 1-5 scale:
5 - Accurate, helpful, well-structured, directly addresses the prompt.
4 - Mostly accurate and helpful, minor improvements possible.
3 - Partially addresses the prompt, some inaccuracies or gaps.
2 - Poorly addresses the prompt, significant issues.
1 - Completely off-topic, wrong, or unhelpful.
"""


class _Verdict(pydantic.BaseModel):
    score: int = pydantic.Field(description="Quality score from 1 to 5")
    explanation: str = pydantic.Field(description="Brief justification for the score")


def _extract_prompt(instance: dict[str, Any]) -> str:
    """Pulls user text from a GEMS Content dict or plain string."""
    raw = instance.get("prompt", "")
    if isinstance(raw, dict):
        parts = raw.get("parts") or []
        return " ".join(p.get("text", "") for p in parts if isinstance(p, dict))
    return str(raw)


def evaluate(instance: dict[str, Any]) -> dict[str, Any]:
    """Scores a single eval instance using an LLM judge."""
    prompt = _extract_prompt(instance)
    response = instance.get("response", "")

    client = genai.Client()
    result = client.models.generate_content(
        model=JUDGE_MODEL,
        contents=RUBRIC.format(prompt=prompt, response=response),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_Verdict,
            temperature=0.0,
        ),
    )
    verdict = _Verdict.model_validate_json(result.text)
    return {"score": verdict.score, "explanation": verdict.explanation}
'''
        record(rq_rel, rq_content, ["C7.1"])

    # 3. Official agents-cli v1.5.0 tests/eval/datasets/basic-dataset.json
    ds_dir = root / "tests" / "eval" / "datasets"
    has_dataset = ds_dir.exists() and any(ds_dir.glob("*.json"))
    if not has_dataset:
        ds_rel = "tests/eval/datasets/basic-dataset.json"
        ds_content = """{
  "eval_cases": [
    {
      "eval_case_id": "case_1_production_readiness",
      "prompt": {
        "role": "user",
        "parts": [
          {
            "text": "Summarize the key steps to verify our enterprise AI agent before production rollout."
          }
        ]
      }
    },
    {
      "eval_case_id": "case_2_security_and_telemetry",
      "prompt": {
        "role": "user",
        "parts": [
          {
            "text": "How does our agent prevent prompt PII leakage into Cloud Trace spans while keeping full observability?"
          }
        ]
      }
    }
  ]
}
"""
        record(ds_rel, ds_content, ["C7.1"])

    # 4. CI/CD Staging Pipeline Promotion Gate (.cloudbuild/staging.yaml or .github/workflows/staging.yaml)
    cb_rel = ".cloudbuild/staging.yaml"
    cb_path = root / cb_rel
    gh_rel = ".github/workflows/staging.yaml"
    gh_path = root / gh_rel

    eval_gate_step = f"""  # [C7.1] Official agents-cli v1.5.0 Automated Evaluation Promotion Gate
  # Runs `agents-cli eval run` against the staging deployment and blocks production promotion (`exit 1`)
  # if the average custom_response_quality score drops below the configured gate threshold ({threshold}).
  - name: "us-central1-docker.pkg.dev/cloud-sdk-docker/google/cloud-sdk:slim"
    id: agent-eval-gate
    entrypoint: /bin/bash
    args:
      - "-c"
      - |
        set -e
        pip install uv --user
        export PATH="/root/.local/bin:$$PATH"
        export GOOGLE_CLOUD_PROJECT=${{_STAGING_PROJECT_ID}}
        export GOOGLE_CLOUD_LOCATION=${{_REGION}}
        export GOOGLE_GENAI_USE_VERTEXAI=TRUE
        uvx --from google-agents-cli==1.5.0 agents-cli eval run \\
          --config tests/eval/eval_config.yaml \\
          --dataset tests/eval/datasets/basic-dataset.json \\
          --project-id $${{_STAGING_PROJECT_ID}} \\
          --location $${{_REGION}}
        python3 -c '
        import glob, json, sys
        files = sorted(glob.glob("artifacts/grade_results/results_*.json"))
        if not files:
            print("ERROR: No evaluation grade_results found!")
            sys.exit(1)
        data = json.load(open(files[-1]))
        scores = [
            m.get("score", 0)
            for case in data
            for m in (case.get("metrics") or [])
            if m.get("score") is not None
        ]
        avg = sum(scores) / max(len(scores), 1)
        print(f"[agent-eval-gate] Average score: {{avg:.2f}} (required >= {threshold})")
        if avg < {threshold}:
            print("BLOCKING PRODUCTION PROMOTION: Agent quality below threshold!")
            sys.exit(1)
        '
"""

    if cb_path.exists():
        cb_txt = cb_path.read_text(encoding="utf-8")
        if "agent-eval-gate" not in cb_txt and "agents-cli eval run" not in cb_txt:
            if "  # Trigger Prod Deployment" in cb_txt:
                cb_txt = cb_txt.replace(
                    "  # Trigger Prod Deployment",
                    eval_gate_step + "\n  # Trigger Prod Deployment",
                    1,
                )
            elif "id: trigger-prod-deployment" in cb_txt:
                cb_txt = re.sub(
                    r"(  - name:[^\n]+\n    id: trigger-prod-deployment)",
                    eval_gate_step + "\n\\1",
                    cb_txt,
                    count=1,
                )
            elif "\nsubstitutions:" in cb_txt:
                cb_txt = cb_txt.replace(
                    "\nsubstitutions:",
                    "\n" + eval_gate_step.rstrip() + "\n\nsubstitutions:",
                    1,
                )
            else:
                cb_txt = cb_txt.rstrip() + "\n\n" + eval_gate_step
            record(cb_rel, cb_txt, ["C7.1"])
    # No pipeline yet → never generate pipeline files here. CI/CD pipelines are created by
    # `agents-cli scaffold enhance . --cicd-runner google_cloud_build`; `agents-cli infra cicd` (staging/prod
    # CI/CD infrastructure) is a manual production-environment step. See `cicd_guidance_notices()`.



# ---------------------------------------------------------------------------
# 5. Demo Reset & Visual HTML Report Generation
# ---------------------------------------------------------------------------


def reset_project_to_baseline(
    project_dir: str | Path, target_override: str = "auto"
) -> dict[str, Any]:
    """Resets a sample or patched project back to its unhardened baseline state for repeatable live demos."""
    import shutil
    from prod_ready.fixtures import (
        create_agents_cli_agent_runtime_fixture,
        create_agents_cli_cloud_run_fixture,
        create_agents_cli_gke_fixture,
        create_prototype_fixture,
    )

    root = Path(project_dir).resolve()
    mode, target, project_name = detect_project_mode_and_target(root, target_override)

    tf_rel = resolve_terraform_rel_dir(root)
    extra_files = [
        f"{tf_rel}/production_ready.tf",
        f"{tf_rel}/alerts.tf",
        f"{tf_rel}/apis.tf",
        f"{tf_rel}/iam.tf",
        f"{tf_rel}/budget.tf",
        "deployment/terraform/production_ready.tf",
        "deployment/terraform/alerts.tf",
        "deployment/terraform/apis.tf",
        "deployment/terraform/iam.tf",
        "deployment/terraform/budget.tf",
        "app/app_utils/tiered_session.py",
        "app/app_utils/model_armor_plugin.py",
        "app/app_utils/cost_guard_plugin.py",
        "tests/eval/golden_dataset.json",
        "tests/eval/eval_config.yaml",
        "tests/eval/response_quality.py",
        "tests/eval/datasets/smoke_dataset.json",
        "tests/eval/datasets/basic-dataset.json",
        "scripts/canary_rollout.sh",
        "READINESS_REPORT.md",
        "READINESS_REPORT.html",
    ]
    for rel in extra_files:
        p = root / rel
        if p.exists():
            p.unlink()
    if (root / "deployment" / "k8s").exists():
        shutil.rmtree(root / "deployment" / "k8s")

    if "01_prototype" in root.name or mode == "prototype":
        if (root / "deployment").exists():
            shutil.rmtree(root / "deployment")
        if (root / ".cloudbuild").exists():
            shutil.rmtree(root / ".cloudbuild")
        if (root / "Dockerfile").exists():
            (root / "Dockerfile").unlink()
        if (root / "app" / "app_utils").exists():
            shutil.rmtree(root / "app" / "app_utils")
        if (root / "app" / "fast_api_app.py").exists():
            (root / "app" / "fast_api_app.py").unlink()
        create_prototype_fixture(root, name=project_name)
    elif target == "agent_runtime" or "03_agent_runtime" in root.name:
        if (root / "app" / "app_utils").exists():
            shutil.rmtree(root / "app" / "app_utils")
        if (root / "app" / "fast_api_app.py").exists():
            (root / "app" / "fast_api_app.py").unlink()
        create_agents_cli_agent_runtime_fixture(root, name=project_name)
    elif target == "gke" or "04_gke" in root.name:
        if (root / "app" / "app_utils").exists():
            shutil.rmtree(root / "app" / "app_utils")
        create_agents_cli_gke_fixture(root, name=project_name)
    else:
        create_agents_cli_cloud_run_fixture(root, name=project_name)

    after_reset = scan_project(root, target_override=target_override)
    return {
        "status": "reset_complete",
        "project_dir": str(root),
        "report": after_reset.to_dict(),
    }


def generate_html_report(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
    conditions: dict[str, bool] | None = None,
    deps: Any | None = None,
) -> str:
    """Generates a standalone visual HTML Executive & InfoSec Dashboard Report (READINESS_REPORT.html)."""
    from prod_ready.deps import PLATFORM_BANNER, platform_install_commands

    root = Path(project_dir).resolve()
    report = scan_project(root, target_override=target_override, selected_items=selected_items, conditions=conditions)
    deps = deps or _report_deps(report)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if report.verdict.startswith("🟢"):
        status_color, status_bg, status_bd = "#137333", "#E6F4EA", "rgba(52,168,83,0.32)"
    elif report.verdict.startswith("🟡"):
        status_color, status_bg, status_bd = "#B06000", "#FEF7E0", "rgba(251,188,4,0.40)"
    else:
        status_color, status_bg, status_bd = "#C5221F", "#FCE8E6", "rgba(234,67,53,0.32)"

    domain_cards = []
    for d in report.domains:
        pass_pct = round(d.green_count / d.total_count * 100) if d.total_count else 0
        all_pass = d.red_count == 0 and d.amber_count == 0
        bar_col = "#34A853" if all_pass else ("#FBBC04" if d.red_count == 0 else "#EA4335")
        txt_col = "#137333" if all_pass else ("#B06000" if d.red_count == 0 else "#C5221F")
        domain_cards.append(
            f"""
            <div class="card" style="border-top:4px solid {bar_col};">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;gap:8px;">
                <span style="font-weight:700;font-size:13.5px;color:#0F172A;">{html_lib.escape(d.domain_name)}</span>
                <span style="font-family:'Google Sans Mono',monospace;font-weight:700;color:{txt_col};">{d.green_count} / {d.total_count} 충족</span>
              </div>
              <div style="width:100%;height:8px;background:#E8EEF5;border-radius:999px;overflow:hidden;">
                <div style="width:{pass_pct}%;height:100%;background:{bar_col};border-radius:999px;"></div>
              </div>
              <div style="margin-top:8px;font-size:12px;color:#475569;font-weight:600;">
                🟢 {d.green_count} &nbsp;|&nbsp; 🟡 {d.amber_count} &nbsp;|&nbsp; 🔴 {d.red_count} &nbsp;|&nbsp; ⚪ N/A {d.na_count}
              </div>
            </div>
            """
        )

    cond_items = "".join(
        f"<li style='margin:4px 0;'>{'✅' if c.met else '—'} <strong>{html_lib.escape(c.label)}</strong>: "
        f"{'해당' if c.met else '해당 없음'} <span style='color:#64748B;'>({html_lib.escape(c.evidence)})</span></li>"
        for c in report.conditions
    )
    conditions_html = f"""
        <div class="card" style="margin-top:16px;border-left:4px solid #34A853;background:#F3FBF6;">
          <strong style="font-size:13.5px;color:#137333;">🧭 워크로드 조건 판단 (조건부 항목은 해당될 때만 점검, 아니면 N/A)</strong>
          <ul style="margin:8px 0 0 18px;padding:0;font-size:12.5px;color:#1E293B;list-style:none;">{cond_items}</ul>
        </div>
        """

    skill_items = "".join(
        f"<li style='margin:4px 0;'><a href='{html_lib.escape(st['url'])}' style='color:#1967D2;font-weight:600;'>{html_lib.escape(st['name'])}</a> "
        f"{_OK_BADGE if st['installed'] else _MISSING_BADGE}</li>"
        for st in deps.platform_status()
    )
    if deps.platform_missing:
        cmds = "<br/>".join(f"<code>{html_lib.escape(c)}</code>" for c in platform_install_commands(deps))
        platform_banner_html = (
            "<div class='card' style='border-left:4px solid #D93025;background:#FCE8E6;margin-bottom:12px;'>"
            f"<strong style='color:#C5221F;font-size:14px;'>{html_lib.escape(PLATFORM_BANNER)}</strong>"
            f"<div style='font-size:12.5px;color:#3C4043;margin-top:6px;'>미설치: {html_lib.escape(', '.join(deps.platform_missing))}</div>"
            f"<div style='font-size:12.5px;margin-top:6px;'>{cmds}</div></div>"
        )
        verdict_note_html = (
            f"<div style='font-size:11.5px;font-weight:700;color:#C5221F;margin-top:4px;'>⚠️ 플랫폼 Skill 미설치 {len(deps.platform_missing)}개 — 설치 강력 권장</div>"
        )
    else:
        platform_banner_html = ""
        verdict_note_html = ""
    bp_rows = "".join(
        f"<tr><td>{html_lib.escape(bp.get('bp', ''))}</td><td style='font-family:monospace;font-size:12px;'>{html_lib.escape(bp.get('skills', ''))}</td></tr>"
        for bp in report.delegated_bps
    )
    platform_skills_html = f"""
    <h2>5. 플랫폼 Skill 참조 권장</h2>
    {platform_banner_html}
    <p style="margin:0 0 12px;font-size:13px;color:#475569;">
      일반 플랫폼 모범 사례는 이 체크리스트에서 점수화하지 않고 플랫폼 Skill(google/skills)에 위임합니다.
    </p>
    <div class="card"><ul style="margin:0 0 0 18px;padding:0;font-size:13px;">{skill_items}</ul></div>
    <div class="table-wrap" style="margin-top:12px;"><table><thead><tr><th>위임된 모범 사례</th><th>참조 Skill</th></tr></thead><tbody>{bp_rows}</tbody></table></div>
    """

    advisory_html = ""
    if report.advisories:
        adv_items = "".join(f"<li style='margin:4px 0;'>{html_lib.escape(a)}</li>" for a in report.advisories)
        advisory_html = f"""
        <div class="card" style="margin-top:16px;border-left:4px solid #4285F4;background:#F8FBFF;">
          <strong style="font-size:13.5px;color:#1967D2;">🔵 권고 사항 (값 · 아키텍처 · 유료 선택형 — 점수화하지 않음)</strong>
          <ul style="margin:8px 0 0 18px;padding:0;font-size:12.5px;color:#334155;">{adv_items}</ul>
        </div>
        """

    item_rows = []
    for item in report.items:
        if item.status == "NA":
            b_bg, b_fg, b_txt = "#F1F3F4", "#5F6368", "⚪ N/A"
        elif item.status == "GREEN":
            b_bg, b_fg, b_txt = "#E6F4EA", "#137333", "🟢 충족"
        elif item.status == "AMBER":
            b_bg, b_fg, b_txt = "#FEF7E0", "#B06000", "🟡 보완"
        else:
            b_bg, b_fg, b_txt = "#FCE8E6", "#C5221F", "🔴 미충족"
        if item.preview and item.status != "NA":
            b_txt += " · Preview"
        refs_html = " · ".join(
            f"<a href='{html_lib.escape(r['url'])}' style='color:#1967D2;'>{html_lib.escape(r['label'])}</a>"
            for r in item.references
        )
        grade_txt = item.severity + (f" · {item.condition_label}" if item.condition_label else "")

        item_rows.append(
            f"""
            <tr>
              <td style="font-family:'Google Sans Mono',monospace;font-weight:700;color:#1967D2;">{html_lib.escape(item.id)}<br/><span style="font-size:11px;color:#64748B;">{html_lib.escape(grade_txt)}</span></td>
              <td style="font-weight:600;color:#334155;">{html_lib.escape(item.category_name)}</td>
              <td>
                <strong style="color:#0F172A;">{html_lib.escape(item.title)}</strong>
                <div style="font-size:12.5px;color:#475569;margin-top:3px;"><strong>Check</strong> · {html_lib.escape(item.check)}</div>
                <div style="font-size:12.5px;color:#334155;margin-top:3px;"><strong>Best Practice</strong> · {html_lib.escape(item.best_practice)}</div>
                <div style="font-size:12px;margin-top:3px;">{refs_html}</div>
              </td>
              <td><span style="background:{b_bg};color:{b_fg};padding:4px 9px;border-radius:8px;font-size:11.5px;font-weight:700;white-space:nowrap;">{b_txt}</span></td>
              <td style="font-size:12.5px;color:#334155;">{html_lib.escape(item.evidence)}</td>
              <td style="font-family:'Google Sans Mono',monospace;font-size:11.5px;color:#475569;">{html_lib.escape(", ".join(item.target_files))}</td>
            </tr>
            """
        )

    decision_rows = []
    for row in _build_user_decision_items(report):
        decision_rows.append(
            f"""
            <tr>
              <td style="text-align:center;font-size:16px;color:#1967D2;">☐</td>
              <td style="font-weight:700;color:#0F172A;white-space:nowrap;">{html_lib.escape(row["topic"])}</td>
              <td style="font-size:12.5px;color:#334155;">{html_lib.escape(row["when"])}</td>
              <td style="font-size:12.5px;color:#334155;">{html_lib.escape(row["how"])}</td>
            </tr>
            """
        )

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Production-Readiness Assessment Report — {html_lib.escape(report.project_name)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link
    href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600;700&family=Google+Sans+Mono:wght@400;500;600&family=Noto+Sans+KR:wght@400;500;600;700&display=swap"
    rel="stylesheet"
  />
  <style>
    * {{ box-sizing: border-box; }}
    html {{
      font-family: 'Google Sans', 'Noto Sans KR', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
      background-color: #F7F9FC;
      background-image:
        radial-gradient(circle at 10% 8%, rgba(66, 133, 244, 0.12), transparent 34%),
        radial-gradient(circle at 90% 6%, rgba(52, 168, 83, 0.11), transparent 34%),
        radial-gradient(circle at 52% 26%, rgba(251, 188, 4, 0.08), transparent 40%),
        radial-gradient(circle at 78% 68%, rgba(234, 67, 53, 0.05), transparent 36%),
        radial-gradient(rgba(15, 23, 42, 0.055) 1px, transparent 1px);
      background-size: 100% 100%, 100% 100%, 100% 100%, 100% 100%, 24px 24px;
      background-repeat: no-repeat, no-repeat, no-repeat, no-repeat, repeat;
      color: #0F172A;
    }}
    body {{ margin: 0; padding: 36px 24px 64px; line-height: 1.6; }}
    .container {{ max-width: 1220px; margin: 0 auto; }}
    .google-dots {{ display: inline-flex; align-items: center; gap: 4px; margin-right: 6px; }}
    .google-dots span {{ width: 7px; height: 7px; border-radius: 999px; display: inline-block; }}
    .google-dots span:nth-child(1) {{ background: #4285F4; }}
    .google-dots span:nth-child(2) {{ background: #EA4335; }}
    .google-dots span:nth-child(3) {{ background: #FBBC04; }}
    .google-dots span:nth-child(4) {{ background: #34A853; }}
    .header {{
      position: relative; overflow: hidden; background: #FFFFFF;
      border: 1.5px solid rgba(66, 133, 244, 0.16); border-radius: 22px;
      padding: 28px 30px; box-shadow: 0 12px 34px rgba(15, 23, 42, 0.06);
      display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 20px;
    }}
    .header::before {{
      content: ""; position: absolute; inset: 0 0 auto 0; height: 5px;
      background: linear-gradient(90deg, #4285F4 0%, #EA4335 34%, #FBBC04 68%, #34A853 100%);
    }}
    .eyebrow {{
      display: inline-flex; align-items: center; font-family: 'Google Sans Mono', monospace;
      font-size: 11.5px; font-weight: 700; color: #1967D2; background: #E8F0FE;
      padding: 4px 11px; border-radius: 999px; text-transform: uppercase; letter-spacing: 0.05em;
    }}
    .status-box {{ text-align: right; background: {status_bg}; border: 1.5px solid {status_bd}; padding: 16px 24px; border-radius: 16px; }}
    .grid-domains {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-top: 16px; }}
    .card {{ background: #FFFFFF; border: 1.5px solid rgba(66, 133, 244, 0.14); border-radius: 16px; padding: 18px; box-shadow: 0 8px 24px rgba(15, 23, 42, 0.045); }}
    h2 {{ font-size: 16.5px; font-weight: 700; letter-spacing: -0.015em; color: #0F172A; margin: 32px 0 12px 0; border-bottom: 1.5px solid rgba(66, 133, 244, 0.14); padding-bottom: 8px; }}
    .table-wrap {{ background: #FFFFFF; border: 1.5px solid rgba(66, 133, 244, 0.14); border-radius: 18px; overflow: hidden; box-shadow: 0 12px 34px rgba(15, 23, 42, 0.055); }}
    table {{ width: 100%; border-collapse: collapse; }}
    th, td {{ padding: 13px 15px; text-align: left; border-bottom: 1px solid rgba(66, 133, 244, 0.1); vertical-align: top; }}
    th {{ background: #F4F7FB; font-size: 12px; font-weight: 700; text-transform: uppercase; color: #475569; letter-spacing: 0.04em; }}
    tr:hover td {{ background: #F7FAFF; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div>
        <div class="eyebrow">
          <span class="google-dots" aria-hidden="true"><span></span><span></span><span></span><span></span></span>
          Enterprise AI Agent Production-Readiness Assessment Report
        </div>
        <h1 style="margin:10px 0 6px;font-size:26px;font-weight:700;color:#0F172A;letter-spacing:-0.025em;">🛡️ {html_lib.escape(report.project_name)}</h1>
        <div style="font-size:13px;color:#475569;font-family:'Google Sans Mono',monospace;">
          Target: <strong style="color:#1967D2;">{html_lib.escape(report.deployment_target)}</strong> &nbsp;|&nbsp;
          Mode: <strong style="color:#137333;">{html_lib.escape(report.project_mode)}</strong> &nbsp;|&nbsp;
          Generated: {now_str}
        </div>
      </div>
      <div class="status-box">
        <div style="font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;letter-spacing:0.05em;">Production-Readiness Gate</div>
        <div style="font-size:30px;font-weight:800;color:{status_color};font-family:'Google Sans Mono',monospace;">{report.green_count} / {report.total_count} 충족</div>
        <div style="font-size:13px;font-weight:700;color:{status_color};margin-top:2px;">{html_lib.escape(report.verdict)}</div>
        {verdict_note_html}
        <div style="font-size:11.5px;font-weight:600;color:#475569;margin-top:4px;">🟢 {report.green_count} &nbsp;·&nbsp; 🟡 {report.amber_count} &nbsp;·&nbsp; 🔴 {report.red_count}</div>
      </div>
    </div>

    <h2>1. 범주별 충족 현황 ({html_lib.escape(report.deployment_target)} · Checklist v3)</h2>
    <div class="grid-domains">
      {"".join(domain_cards)}
    </div>
    {conditions_html}

    <h2>2. 상세 점검 ({html_lib.escape(report.deployment_target)} · {len(report.items)}개 항목 · Preview는 판정에 영향 없음)</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:90px;">ID</th>
            <th style="width:120px;">Category</th>
            <th>Check · Best Practice · References</th>
            <th style="width:110px;">Status</th>
            <th>Detected Evidence</th>
            <th style="width:180px;">Target Files</th>
          </tr>
        </thead>
        <tbody>
          {"".join(item_rows)}
        </tbody>
      </table>
    </div>

    <h2>3. 🧭 권고 사항 (값 · 아키텍처 · 유료 선택형)</h2>
    <p style="margin:0 0 12px;font-size:13px;color:#475569;">
      체크리스트로 점검하지 않는 항목입니다. 워크로드와 조직 정책에 따라 <strong>필요한 항목만 선택해 직접 설정</strong>하세요.
    </p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th style="width:56px;text-align:center;">선택</th>
            <th style="width:210px;">영역</th>
            <th style="width:320px;">언제 선택하나요? (적용 기준)</th>
            <th>직접 설정 가이드</th>
          </tr>
        </thead>
        <tbody>
          {"".join(decision_rows)}
        </tbody>
      </table>
    </div>
    {advisory_html}
    {platform_skills_html}
  </div>
</body>
</html>
"""
