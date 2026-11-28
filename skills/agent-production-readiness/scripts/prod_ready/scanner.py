"""Static Scanner & Compliance Gate for the Production-Readiness Checklist v3.

- Items come from ``catalog_v3.json`` via :mod:`prod_ready.checklist` (per-target ids such as ``CR-F1``).
- Conditional items are scored only when their workload condition is detected in code
  (multi-turn sessions, long-term memory, AI code execution, MCP/external tools, tools called on behalf of
  users with credentials); otherwise ``NA``.
- Preview items are reported but never block the overall Production Ready verdict.
- General platform best practices are delegated to platform skills (google/skills) and are not scored.
- Gemini call retry · endpoint is ADK optimization (agent code): out of scope, never scored.
- Categories with no item for the target (e.g. Runtime Lifecycle on Cloud Run / Agent Runtime) are omitted from domains.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from prod_ready.checklist import (
    CATALOG_VERSION,
    CATEGORY_BY_CODE,
    CATEGORY_ORDER,
    CONDITIONS,
    ChecklistItemSpec,
    CustomFieldSpec,
    RAGStatus,
    TargetType,
    advisories_for,
    delegated_bps,
    get_items_for_target,
    platform_skills_for,
    resolve_item_ids,
)

ProjectMode = Literal["prototype", "agents_cli_single", "agents_cli_cicd"]


@dataclass
class ItemScanResult:
    id: str
    code_alias: str
    domain_id: str
    domain_name: str
    title: str
    severity: str
    status: RAGStatus
    managed_by_platform: bool
    evidence: str
    remediation: str
    target_files: list[str]
    why_it_matters: str
    verify_cmd: str
    cloud_run_desc: str
    agent_runtime_desc: str
    gke_desc: str
    custom_fields: list[CustomFieldSpec]
    # ---- v3 fields ----
    category: str = ""
    category_name: str = ""
    grade: str = "required"
    condition: str | None = None
    condition_label: str = ""
    condition_met: bool | None = None
    preview: bool = False
    new: bool = False
    check: str = ""
    best_practice: str = ""
    references: list[dict[str, str]] = field(default_factory=list)
    legacy_ids: list[str] = field(default_factory=list)
    blocking: bool = True
    auto_patch: bool = True
    scope: str = "runtime"


@dataclass
class DomainSummary:
    domain_id: str
    domain_name: str
    total_count: int
    green_count: int
    amber_count: int
    red_count: int
    na_count: int = 0


@dataclass
class ConditionResult:
    key: str
    label: str
    met: bool
    source: str  # "auto" | "override"
    evidence: str


@dataclass
class ProjectScanReport:
    project_dir: str
    project_name: str
    project_mode: ProjectMode
    deployment_target: TargetType
    verdict: str
    total_count: int
    green_count: int
    amber_count: int
    red_count: int
    managed_native_count: int
    domains: list[DomainSummary]
    items: list[ItemScanResult]
    skipped_item_ids: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    platform_guarantees: list[str] = field(default_factory=list)  # kept empty for API compatibility (v3)
    # ---- v3 fields ----
    catalog_version: str = CATALOG_VERSION
    na_count: int = 0
    preview_open_count: int = 0
    blocking_open_ids: list[str] = field(default_factory=list)
    conditions: list[ConditionResult] = field(default_factory=list)
    platform_skills: list[dict[str, str]] = field(default_factory=list)
    delegated_bps: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read_text(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


@dataclass
class McpA2aProfile:
    """Context-aware MCP Server / MCP Client / A2A Sub-Agent signals detected in a local project."""

    is_mcp_server: bool = False
    hardcoded_mcp_urls: list[str] = field(default_factory=list)
    hardcoded_a2a_urls: list[str] = field(default_factory=list)
    uses_agent_registry: bool = False


def _strip_py_comments(src: str) -> str:
    """Drops full-line Python comments so commented-out examples don't trigger false positives."""
    return "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))


def resolve_terraform_rel_dir(project_dir: Path) -> str:
    """Resolves the active Terraform root across agents-cli layouts.

    - ``agents-cli`` v1.5+ single-project mode: ``deployment/terraform/single-project``
    - Classic ``agents-cli`` / prototype-upgraded mode: ``deployment/terraform``
    """
    single_proj = project_dir / "deployment" / "terraform" / "single-project"
    if (single_proj / "service.tf").exists():
        return "deployment/terraform/single-project"
    return "deployment/terraform"


def detect_mcp_a2a_profile(project_dir: Path, service_tf: str = "") -> McpA2aProfile:
    """Detects MCP Server workloads and hardcoded remote MCP/A2A endpoints without false positives on single agents."""
    if not service_tf:
        tf_rel = resolve_terraform_rel_dir(project_dir)
        service_tf = _read_text(project_dir / tf_rel / "service.tf")

    py_sources: list[str] = []
    seen_paths: set[Path] = set()
    app_dir = project_dir / "app"
    if app_dir.is_dir():
        for py_file in sorted(app_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            seen_paths.add(py_file)
            py_sources.append(_strip_py_comments(_read_text(py_file)))
    for py_file in sorted(project_dir.glob("*.py")):
        if py_file not in seen_paths:
            py_sources.append(_strip_py_comments(_read_text(py_file)))

    combined_py = "\n".join(py_sources)

    has_mcp_server_code = bool(
        re.search(
            r"\bFastMCP\s*\(|from\s+mcp\.server\b|import\s+mcp\.server\b|mcp\.server\.fastmcp|from\s+fastmcp\s+import\b",
            combined_py,
        )
    )
    has_adk_agent_code = bool(
        re.search(r"from\s+google\.adk\b|\bLlmAgent\s*\(|\bAgent\s*\(", combined_py)
    )
    explicit_mcp_tf = bool(re.search(r'functional_type\s*=\s*"FUNCTIONAL_TYPE_MCP_SERVER"', service_tf))
    is_mcp_server = explicit_mcp_tf or (has_mcp_server_code and not has_adk_agent_code)

    uses_agent_registry = bool(
        re.search(
            r"google\.adk\.integrations\.agent_registry|\bAgentRegistry\s*\(|\bget_mcp_toolset\s*\(|\bget_remote_a2a_agent\s*\(",
            combined_py,
        )
    )

    mcp_urls: list[str] = []
    a2a_urls: list[str] = []

    def _add_unique(bucket: list[str], val: str) -> None:
        cleaned = val.strip()
        if cleaned and cleaned not in bucket:
            bucket.append(cleaned)

    # 1. Python hardcoded remote MCP URLs
    mcp_py_patterns = [
        r"""(?:StreamableHTTPConnectionParams|SseConnectionParams)\s*\([^)]*?\burl\s*=\s*(?:os\.(?:getenv|environ\.get)\(\s*["'][^"']+["']\s*,\s*)?["'](https?://[^"']+)["']""",
        r"""\b(?:MCP_SERVER_URL|MCP_URL)\s*=\s*["'](https?://[^"']+)["']""",
        r"""os\.(?:getenv|environ\.get)\(\s*["'](?:MCP_SERVER_URL|MCP_URL)["']\s*,\s*["'](https?://[^"']+)["']""",
    ]
    for pat in mcp_py_patterns:
        for m in re.finditer(pat, combined_py, flags=re.DOTALL):
            _add_unique(mcp_urls, m.group(1))

    # 2. Terraform env vars with hardcoded MCP URLs
    for m in re.finditer(
        r'name\s*=\s*"(?:MCP_SERVER_URL|MCP_URL|[A-Z0-9_]*_MCP(?:_SERVER)?_URL)"\s*\n\s*value\s*=\s*"(https?://[^"]+)"',
        service_tf,
    ):
        _add_unique(mcp_urls, m.group(1))

    # 3. Python hardcoded remote A2A Agent Card URLs
    a2a_py_patterns = [
        r"""\bRemoteA2aAgent\s*\([^)]*?\bagent_card\s*=\s*(?:os\.(?:getenv|environ\.get)\(\s*["'][^"']+["']\s*,\s*)?["'](https?://[^"']+)["']""",
        r"""\b(?:A2A_AGENT_URL|A2A_SERVER_URL|AGENT_CARD_URL)\s*=\s*["'](https?://[^"']+)["']""",
        r"""os\.(?:getenv|environ\.get)\(\s*["'](?:A2A_AGENT_URL|A2A_SERVER_URL|AGENT_CARD_URL)["']\s*,\s*["'](https?://[^"']+)["']""",
    ]
    for pat in a2a_py_patterns:
        for m in re.finditer(pat, combined_py, flags=re.DOTALL):
            _add_unique(a2a_urls, m.group(1))

    # 4. Terraform env vars with hardcoded A2A URLs
    for m in re.finditer(
        r'name\s*=\s*"(?:A2A_AGENT_URL|A2A_SERVER_URL|AGENT_CARD_URL|[A-Z0-9_]*_(?:A2A|AGENT_CARD)_URL)"\s*\n\s*value\s*=\s*"(https?://[^"]+)"',
        service_tf,
    ):
        _add_unique(a2a_urls, m.group(1))

    return McpA2aProfile(
        is_mcp_server=is_mcp_server,
        hardcoded_mcp_urls=mcp_urls,
        hardcoded_a2a_urls=a2a_urls,
        uses_agent_registry=uses_agent_registry,
    )


def detect_project_mode_and_target(
    project_dir: Path, target_override: str = "auto"
) -> tuple[ProjectMode, TargetType, str]:
    """Detects whether the project is a pure prototype or an agents-cli project, and its target."""
    tf_rel = resolve_terraform_rel_dir(project_dir)
    tf_service = project_dir / tf_rel / "service.tf"
    tf_dev_service = project_dir / "deployment" / "terraform" / "dev" / "service.tf"
    staging_yaml = project_dir / ".cloudbuild" / "staging.yaml"

    if not tf_service.exists():
        mode: ProjectMode = "prototype"
    elif tf_dev_service.exists() or staging_yaml.exists():
        mode = "agents_cli_cicd"
    else:
        mode = "agents_cli_single"

    service_txt = _read_text(tf_service)
    manifest_txt = _read_text(project_dir / "agents-cli-manifest.yaml")
    if target_override in ("cloud_run", "agent_runtime", "gke"):
        target: TargetType = target_override  # type: ignore[assignment]
    elif "google_vertex_ai_reasoning_engine" in service_txt:
        target = "agent_runtime"
    elif (
        "google_container_cluster" in service_txt
        or "kubernetes_deployment_v1" in service_txt
        or bool(re.search(r"deployment_target:\s*['\"]?gke['\"]?", manifest_txt))
    ):
        target = "gke"
    else:
        target = "cloud_run"

    project_name = project_dir.name.replace("_", "-")
    pyproject_txt = _read_text(project_dir / "pyproject.toml")
    for line in pyproject_txt.splitlines():
        if line.strip().startswith("name") and "=" in line:
            val = line.split("=", 1)[1].strip().strip('"').strip("'")
            if val:
                project_name = val.replace("_", "-")
                break

    return mode, target, project_name

def _has_otel_to_cloud_enabled(fast_api_py: str) -> bool:
    """Checks whether fast_api_app.py actively enables otel_to_cloud on Cloud Run."""
    if not fast_api_py:
        return False
    if re.search(r"otel_to_cloud\s*=\s*False\b", fast_api_py):
        return False
    # Agent Runtime telemetry gate evaluates to False on Cloud Run where that env var isn't set
    if "GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY" in fast_api_py:
        return False
    return bool(
        re.search(r"otel_to_cloud\s*=\s*True\b", fast_api_py)
        or re.search(r"otel_to_cloud\s*=\s*otel_to_cloud\b", fast_api_py)
    )


def _has_adk_otel_gcp_extras(pyproject_toml: str) -> bool:
    """Returns False when pyproject.toml declares google-adk without [gcp,otel-gcp] (or [all]) extras."""
    if not pyproject_toml or not re.search(r'["\']google-adk\b', pyproject_toml):
        return True
    if (
        "opentelemetry-exporter-otlp-proto-http" in pyproject_toml
        and "opentelemetry-instrumentation-google-genai" in pyproject_toml
    ):
        return True
    for m in re.finditer(r'["\']google-adk(?:\[([^\]]+)\])?(?:[^"\']*)["\']', pyproject_toml):
        extras = {e.strip().lower() for e in (m.group(1) or "").split(",") if e.strip()}
        if "all" in extras or ({"gcp", "otel-gcp"} <= extras):
            return True
    return False



# ---------------------------------------------------------------------------
# Project context & workload condition detection
# ---------------------------------------------------------------------------


def _collect_app_python(project_dir: Path) -> str:
    """Concatenates app/**/*.py and top-level *.py (comments stripped)."""
    sources: list[str] = []
    seen: set[Path] = set()
    app_dir = project_dir / "app"
    if app_dir.is_dir():
        for py_file in sorted(app_dir.rglob("*.py")):
            if "__pycache__" in py_file.parts:
                continue
            seen.add(py_file)
            sources.append(_strip_py_comments(_read_text(py_file)))
    for py_file in sorted(project_dir.glob("*.py")):
        if py_file not in seen:
            sources.append(_strip_py_comments(_read_text(py_file)))
    return "\n".join(sources)


@dataclass
class _Ctx:
    root: Path
    target: TargetType
    has_tf: bool
    service_tf: str
    variables_tf: str
    apis_tf: str
    iam_tf: str
    prod_ready_tf: str
    all_tf: str
    dockerfile: str
    pyproject_toml: str
    agent_py: str
    fast_api_py: str
    services_py: str
    agent_engine_app_py: str
    app_py: str
    deploy_scripts: str
    k8s_yaml: str
    k8s_agent_gateway_yaml: str
    k8s_agent_sandbox_yaml: str
    k8s_agent_substrate_yaml: str
    mcp_a2a: McpA2aProfile
    env_files: str = ""


def _build_ctx(root: Path, target: TargetType) -> _Ctx:
    tf_dir = root / resolve_terraform_rel_dir(root)
    service_tf = _read_text(tf_dir / "service.tf")
    all_tf_parts: list[str] = []
    for d in {tf_dir, root / "deployment" / "terraform"}:
        if d.is_dir():
            for tf in sorted(d.glob("*.tf")):
                all_tf_parts.append(_read_text(tf))
    deploy_parts: list[str] = [_read_text(root / "Makefile")]
    for pattern in (".cloudbuild/*.yaml", ".github/workflows/*.yaml", ".github/workflows/*.yml", "*.sh", "scripts/*.sh", "deployment/*.sh"):
        for f in sorted(root.glob(pattern)):
            deploy_parts.append(_read_text(f))
    k8s_dir = root / "deployment" / "k8s"
    k8s_parts = [_read_text(f) for f in sorted(k8s_dir.glob("*.y*ml"))] if k8s_dir.is_dir() else []
    return _Ctx(
        root=root,
        target=target,
        has_tf=bool(service_tf),
        service_tf=service_tf,
        variables_tf=_read_text(tf_dir / "variables.tf"),
        apis_tf=_read_text(tf_dir / "apis.tf"),
        iam_tf=_read_text(tf_dir / "iam.tf"),
        prod_ready_tf=_read_text(tf_dir / "production_ready.tf"),
        all_tf="\n".join(all_tf_parts),
        dockerfile=_read_text(root / "Dockerfile"),
        pyproject_toml=_read_text(root / "pyproject.toml"),
        agent_py=_read_text(root / "app" / "agent.py"),
        fast_api_py=_read_text(root / "app" / "fast_api_app.py"),
        services_py=_read_text(root / "app" / "app_utils" / "services.py"),
        agent_engine_app_py=_read_text(root / "app" / "agent_engine_app.py"),
        app_py=_collect_app_python(root),
        deploy_scripts="\n".join(deploy_parts),
        k8s_yaml="\n".join(k8s_parts),
        k8s_agent_gateway_yaml=_read_text(k8s_dir / "agent-gateway.yaml"),
        k8s_agent_sandbox_yaml=_read_text(k8s_dir / "agent-sandbox.yaml"),
        k8s_agent_substrate_yaml=_read_text(k8s_dir / "agent-substrate.yaml"),
        mcp_a2a=detect_mcp_a2a_profile(root, service_tf),
        env_files="\n".join(
            _read_text(f) for pat in (".env", ".env.*", "app/.env") for f in sorted(root.glob(pat)) if f.is_file()
        ),
    )


_CONV_RE = re.compile(
    r"SessionService\b|get_session_service|session_service_uri|SESSION_SERVICE_URI|get_fast_api_app|"
    r"\bAdkApp\s*\(|\bRunner\s*\(|from\s+google\.adk\b|\bLlmAgent\s*\(|\bAgent\s*\("
)
_MEMORY_RE = re.compile(
    r"MemoryService\b|memory_service|load_memory|PreloadMemoryTool|LoadMemoryTool|add_session_to_memory|MemoryBank|memory_bank"
)
_CODE_RE = re.compile(
    r"code_executor\s*=|\w*CodeExecutor\b|subprocess\.(?:run|Popen|call|check_output|check_call)\s*\(|\bos\.system\s*\("
)
_TOOLS_RE = re.compile(
    r"McpToolset|MCPToolset|MCPTool\b|StreamableHTTPConnectionParams|SseConnectionParams|StdioConnectionParams|"
    r"RemoteA2aAgent|AgentRegistry\s*\(|OpenAPIToolset|APIHubToolset|ApplicationIntegrationToolset|"
    r"\brequests\.(?:get|post|put|patch|delete|request)\s*\(|\bhttpx\.|\baiohttp\b|urllib\.request"
)

# ---- delegated: tools / MCP servers called on behalf of users (credential-bearing external calls) ----
# Google model keys (Gemini / AI Studio) are not external-tool credentials.
_NON_TOOL_SECRET_ENV = frozenset({"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY"})
_SECRET_ENV_RE = re.compile(
    r"\b(?:GITHUB_PAT|GH_TOKEN|[A-Z][A-Z0-9_]*_(?:TOKEN|API_KEY|APIKEY|PAT|ACCESS_KEY|SECRET_KEY|CLIENT_SECRET))\b"
)
_HARDCODED_TOKEN_RE = re.compile(
    r"""["'](?:ghp_|gho_|github_pat_|glpat-|xox[bp]-|sk-|ATATT)[A-Za-z0-9_\-]{8,}["']"""
)
_AUTH_HEADER_RE = re.compile(
    r"""\bheaders\s*=|["']Authorization["']|["'](?:X-API-Key|x-api-key|PRIVATE-TOKEN|Api-Key)["']|\bBearer\b|"""
    r"""\bauth_credential\s*=|\bauth_scheme\s*=|\bheader_provider\s*="""
)
_AUTH_MANAGER_CODE_RE = re.compile(
    r"google\.adk\.integrations\.agent_identity|\bGcpAuthProvider(?:Scheme)?\b|/authProviders/|"
    r"agentidentity\.googleapis\.com|\bauth_manager\b|\bAuthManager\b"
)


def _tool_secret_envs(ctx: _Ctx) -> list[str]:
    """Non-Google secret-like env var names referenced by app code, Terraform env or .env files."""
    src = "\n".join((ctx.app_py, ctx.service_tf, ctx.k8s_yaml, ctx.env_files))
    names: list[str] = []
    for m in _SECRET_ENV_RE.finditer(src):
        name = m.group(0)
        if name not in _NON_TOOL_SECRET_ENV and name not in names:
            names.append(name)
    return names


def _has_adk_agent_identity_extra(pyproject_toml: str) -> bool:
    for m in re.finditer(r'["\']google-adk\[([^\]]+)\]', pyproject_toml or ""):
        extras = {e.strip().lower() for e in m.group(1).split(",")}
        if "agent-identity" in extras or "agent_identity" in extras or "all" in extras:
            return True
    return False


def _auth_manager_code_hit(ctx: _Ctx) -> str:
    return _first_match(_AUTH_MANAGER_CODE_RE, _strip_py_comments(ctx.app_py))


def _static_secret_in_tool_headers(ctx: _Ctx) -> str:
    """Returns evidence when static API keys / PATs are wired into tool / MCP auth headers (else '')."""
    code = _strip_py_comments(ctx.app_py)
    if not _AUTH_HEADER_RE.search(code):
        return ""
    literal = _HARDCODED_TOKEN_RE.search(code)
    if literal:
        return "코드에 하드코딩된 토큰 리터럴"
    for m in _SECRET_ENV_RE.finditer(code):
        if m.group(0) not in _NON_TOOL_SECRET_ENV:
            return f"환경변수 `{m.group(0)}`"
    return ""


def _first_match(regex: re.Pattern[str], text: str) -> str:
    m = regex.search(text)
    return m.group(0).strip() if m else ""


def detect_workload_conditions(
    project_dir: str | Path,
    overrides: dict[str, bool] | None = None,
    ctx: _Ctx | None = None,
) -> dict[str, ConditionResult]:
    """Auto-detects v3 workload conditions (conv / memory / code / tools / idle / delegated) from application code.

    ``overrides`` (e.g. ``{"memory": True}``) take precedence over auto-detection.
    """
    root = Path(project_dir).resolve()
    if ctx is None:
        _, target, _ = detect_project_mode_and_target(root)
        ctx = _build_ctx(root, target)
    code = ctx.app_py
    profile = ctx.mcp_a2a

    detected: dict[str, tuple[bool, str]] = {}
    hit = _first_match(_CONV_RE, code)
    detected["conv"] = (bool(hit), f"코드에서 `{hit}` 감지" if hit else "세션·ADK 에이전트 코드 미감지")
    hit = _first_match(_MEMORY_RE, code)
    detected["memory"] = (bool(hit), f"코드에서 `{hit}` 감지" if hit else "메모리 서비스·도구 사용 미감지")
    hit = _first_match(_CODE_RE, code)
    detected["code"] = (bool(hit), f"코드에서 `{hit}` 감지" if hit else "코드 실행 도구 미감지")
    hit = _first_match(_TOOLS_RE, code)
    remote_urls = profile.hardcoded_mcp_urls + profile.hardcoded_a2a_urls
    if not hit and remote_urls:
        hit = remote_urls[0]
    if not hit and profile.uses_agent_registry:
        hit = "AgentRegistry"
    detected["tools"] = (bool(hit), f"`{hit}` 감지" if hit else "MCP·외부 API·원격 에이전트 호출 미감지")
    # idle: "mostly waiting for user input / tool responses" cannot be inferred from code. It is met only when
    # Agent Substrate is already in use (the user opted into suspend/resume) or the user says so (override).
    if _has_agent_substrate(ctx):
        detected["idle"] = (True, "Agent Substrate(ate.dev WorkerPool/ActorTemplate) 사용 감지 — 유휴 일시정지·복원 대상")
    else:
        detected["idle"] = (
            False,
            "코드로 판단 불가 — 사용자 입력·도구 응답 대기 시간이 대부분이면 조건 지정 "
            "(`--conditions idle=true`, Agent Skill은 사용자에게 질문)",
        )
    # delegated: external tools / MCP servers called with credentials (on behalf of users). Detected from
    # auth manager usage, credential-bearing tool configs (auth headers / auth_scheme) or secret-like env vars.
    am_hit = _auth_manager_code_hit(ctx)
    secret_envs = _tool_secret_envs(ctx)
    if am_hit:
        detected["delegated"] = (True, f"Agent Identity auth manager 사용 감지 (`{am_hit}`)")
    elif secret_envs:
        detected["delegated"] = (True, f"외부 도구 자격 증명 환경변수 감지 ({', '.join(secret_envs[:3])})")
    elif detected["tools"][0] and _AUTH_HEADER_RE.search(code):
        detected["delegated"] = (True, "인증 헤더를 붙여 외부 도구·MCP 호출 감지")
    else:
        detected["delegated"] = (
            False,
            "코드로 판단 불가 — 사용자 계정으로 외부 시스템(Jira·GitHub 등)을 호출하면 조건 지정 "
            "(`--conditions delegated=true`, Agent Skill은 사용자에게 질문)",
        )

    results: dict[str, ConditionResult] = {}
    for key, label in CONDITIONS.items():
        met, evidence = detected.get(key, (False, "미감지"))
        source = "auto"
        if overrides and key in overrides and overrides[key] is not None:
            met = bool(overrides[key])
            source = "override"
            evidence = f"사용자 지정 ({'해당' if met else '해당 없음'})"
        results[key] = ConditionResult(key=key, label=label, met=met, source=source, evidence=evidence)
    return results


def _collect_advisories(target: TargetType, mcp_a2a: McpA2aProfile | None = None) -> list[str]:
    """Catalog advisories (value / architecture / paid options) + context-aware MCP/A2A notes."""
    advisories = [f"[권고 · {a['title']}] {a['when']}" for a in advisories_for(target)]
    if mcp_a2a is not None:
        if mcp_a2a.is_mcp_server:
            advisories.append(
                "[참고 · MCP 서버 워크로드] MCP 서버로 감지되었습니다. Agent Registry 등록 시 에이전트 대신 MCP 서버 유형"
                "(Cloud Run: `FUNCTIONAL_TYPE_MCP_SERVER`)으로 배포하세요 (references/agent-registry-mcp-a2a.md)."
            )
        remote = mcp_a2a.hardcoded_mcp_urls + mcp_a2a.hardcoded_a2a_urls
        if remote and not mcp_a2a.uses_agent_registry:
            advisories.append(
                f"[참고 · MCP/A2A 엔드포인트 하드코딩] 원격 MCP/A2A URL 하드코딩({', '.join(remote[:3])})이 감지되었습니다. "
                "ADK `AgentRegistry`의 `get_mcp_toolset()` / `get_remote_a2a_agent()`로 전환하면 엔드포인트 해석·인증이 통합됩니다 "
                "(references/agent-registry-mcp-a2a.md)."
            )
    return advisories


# ---------------------------------------------------------------------------
# Scan entry point
# ---------------------------------------------------------------------------


def scan_project(
    project_dir: str | Path,
    target_override: str = "auto",
    selected_items: list[str] | None = None,
    conditions: dict[str, bool] | None = None,
) -> ProjectScanReport:
    """Evaluates the v3 checklist items for the detected/selected target runtime."""
    root = Path(project_dir).resolve()
    mode, target, project_name = detect_project_mode_and_target(root, target_override)

    target_specs = get_items_for_target(target)
    active_ids = set(resolve_item_ids(selected_items, target=target))
    skipped_item_ids = [spec.id for spec in target_specs if spec.id not in active_ids]

    ctx = _build_ctx(root, target)
    cond_results = detect_workload_conditions(root, overrides=conditions, ctx=ctx)

    results: list[ItemScanResult] = []
    for spec in target_specs:
        if spec.id not in active_ids:
            continue
        condition_met: bool | None = None
        if spec.grade == "conditional" and spec.condition:
            cres = cond_results.get(spec.condition)
            condition_met = bool(cres and cres.met)
        if condition_met is False:
            cres = cond_results.get(spec.condition or "")
            status: RAGStatus = "NA"
            managed = False
            evidence = f"N/A — 조건 '{spec.condition_label}' 해당 없음 ({cres.evidence if cres else '미감지'})"
        else:
            status, managed, evidence = _evaluate_item(spec=spec, ctx=ctx)
            if spec.preview:
                evidence = f"[Preview] {evidence}"
        results.append(
            ItemScanResult(
                id=spec.id,
                code_alias=spec.code_alias,
                domain_id=spec.domain_id,
                domain_name=spec.domain_name,
                title=spec.title,
                severity=spec.severity,
                status=status,
                managed_by_platform=managed,
                evidence=evidence,
                remediation=spec.best_practice,
                target_files=spec.target_files_for(target),
                why_it_matters=spec.check,
                verify_cmd=spec.verify_cmd,
                cloud_run_desc=spec.best_practice if target == "cloud_run" else "",
                agent_runtime_desc=spec.best_practice if target == "agent_runtime" else "",
                gke_desc=spec.best_practice if target == "gke" else "",
                custom_fields=spec.custom_fields_for(target),
                category=spec.category,
                category_name=spec.category_name,
                grade=spec.grade,
                condition=spec.condition,
                condition_label=spec.condition_label,
                condition_met=condition_met,
                preview=spec.preview,
                new=spec.new,
                check=spec.check,
                best_practice=spec.best_practice,
                references=spec.references_list,
                legacy_ids=list(spec.legacy_ids),
                blocking=(status != "NA" and not spec.preview and spec.grade != "optional"),
                auto_patch=spec.auto_patch,
                scope=spec.scope,
            )
        )

    # Category summaries (F · O · D · OB · S) — N/A items excluded from totals
    domain_summaries: list[DomainSummary] = []
    for code in CATEGORY_ORDER:
        c_items = [r for r in results if r.category == code]
        if not c_items:
            continue
        scored = [r for r in c_items if r.status != "NA"]
        cat = CATEGORY_BY_CODE.get(code, {})
        domain_summaries.append(
            DomainSummary(
                domain_id=code,
                domain_name=f"{cat.get('name', code)} ({cat.get('sub', '')})" if cat.get("sub") else cat.get("name", code),
                total_count=len(scored),
                green_count=sum(1 for i in scored if i.status == "GREEN"),
                amber_count=sum(1 for i in scored if i.status == "AMBER"),
                red_count=sum(1 for i in scored if i.status == "RED"),
                na_count=len(c_items) - len(scored),
            )
        )

    scored_all = [r for r in results if r.status != "NA"]
    blocking = [r for r in scored_all if r.blocking]
    total_count = len(scored_all)
    green_count = sum(1 for r in scored_all if r.status == "GREEN")
    amber_count = sum(1 for r in scored_all if r.status == "AMBER")
    red_count = sum(1 for r in scored_all if r.status == "RED")
    managed_count = sum(1 for r in scored_all if r.managed_by_platform)
    blocking_open = [r.id for r in blocking if r.status != "GREEN"]
    preview_open = sum(1 for r in scored_all if r.preview and r.status != "GREEN")

    if blocking and not blocking_open:
        verdict = "🟢 Production Ready"
    elif blocking and not any(r.status == "RED" for r in blocking):
        verdict = "🟡 Ready with Follow-ups"
    elif not blocking:
        verdict = "🟡 Ready with Follow-ups" if results else "🔴 Not Production Ready"
    else:
        verdict = "🔴 Not Production Ready"

    return ProjectScanReport(
        project_dir=str(root),
        project_name=project_name,
        project_mode=mode,
        deployment_target=target,
        verdict=verdict,
        total_count=total_count,
        green_count=green_count,
        amber_count=amber_count,
        red_count=red_count,
        managed_native_count=managed_count,
        domains=domain_summaries,
        items=results,
        skipped_item_ids=skipped_item_ids,
        advisories=_collect_advisories(target, ctx.mcp_a2a),
        platform_guarantees=[],
        na_count=len(results) - total_count,
        preview_open_count=preview_open,
        blocking_open_ids=blocking_open,
        conditions=list(cond_results.values()),
        platform_skills=platform_skills_for(target),
        delegated_bps=delegated_bps(),
    )


# ---------------------------------------------------------------------------
# Per-item evaluators (v3)
# ---------------------------------------------------------------------------

Eval = tuple[RAGStatus, bool, str]


def _eval_session_store(ctx: _Ctx) -> Eval:
    """CR-F1 / GKE-F1: VertexAiSessionService instead of InMemorySessionService."""
    has_managed_code = "VertexAiSessionService" in ctx.services_py or bool(
        re.search(r"session_service_uri\s*=\s*[\"']agentengine://", ctx.app_py)
    )
    has_session_env = '"AGENT_ENGINE_SESSION_ID"' in ctx.service_tf
    has_managed_tf = (
        has_session_env
        or '"GOOGLE_CLOUD_AGENT_ENGINE_ID"' in ctx.service_tf
        or '"ADK_SESSION_SERVICE_URI"' in ctx.service_tf
    )
    if has_managed_code and has_managed_tf:
        note = "" if has_session_env else " (참고: AGENT_ENGINE_SESSION_ID 사용 권장)"
        return ("GREEN", False, f"VertexAiSessionService 연동 및 Agent Platform 인스턴스 ID 환경변수 구성 완료{note}")
    if has_managed_code or has_managed_tf:
        return ("AMBER", False, "services.py(VertexAiSessionService) 또는 service.tf(AGENT_ENGINE_SESSION_ID) 설정 일부 누락")
    return ("RED", False, "InMemorySessionService 사용 중 — 재시작·스케일아웃 시 대화 맥락 소실")


def _eval_ar_session(ctx: _Ctx) -> Eval:
    """AR-F1: Agent Runtime binds VertexAiSessionService by default unless the app forces InMemory."""
    forced = "InMemorySessionService" in (ctx.agent_engine_app_py + "\n" + _strip_py_comments(ctx.agent_py))
    if forced:
        return ("RED", False, "agent_engine_app.py/agent.py에서 InMemorySessionService를 강제 — 관리형 영속 세션 저장 대신 인메모리 세션 사용")
    return ("GREEN", False, "Agent Runtime 기본 관리형 영속 세션 저장(VertexAiSessionService) 사용 (InMemory 강제 없음)")


_MEM_READ_RE = re.compile(r"\b(?:PreloadMemoryTool|preload_memory|LoadMemoryTool|load_memory)\b")
_MEM_WRITE_RE = re.compile(r"\b(?:add_session_to_memory|add_events_to_memory|add_memory)\b")


def _eval_memory_bank(ctx: _Ctx) -> Eval:
    """CR-F2 / GKE-F2: VertexAiMemoryBankService + serving wiring + ADK read/write hooks."""
    code = _strip_py_comments(ctx.app_py)
    has_bank = "VertexAiMemoryBankService" in code or bool(
        re.search(r"memory_service_uri\s*=\s*[\"']agentengine://", code)
    )
    if not has_bank:
        if "InMemoryMemoryService" in code:
            return ("RED", False, "InMemoryMemoryService 사용 중 — 재시작 시 장기 기억 소실 (Memory Bank 연결 필요)")
        return ("RED", False, "메모리 사용이 감지되었으나 Memory Bank(VertexAiMemoryBankService) 미연결")

    serving_src = _strip_py_comments(ctx.fast_api_py + "\n" + ctx.agent_engine_app_py)
    has_serving = bool(re.search(r"\bmemory_service\s*=|\bmemory_service_uri\s*=", serving_src))
    has_read = bool(_MEM_READ_RE.search(code))
    has_write = bool(_MEM_WRITE_RE.search(code))
    missing: list[str] = []
    if not has_serving:
        missing.append("서빙 연결(fast_api_app.py memory_service)")
    if not has_read:
        missing.append("ADK 읽기 도구(preload_memory / load_memory)")
    if not has_write:
        missing.append("ADK 저장 콜백(add_session_to_memory)")
    if not missing:
        return (
            "GREEN",
            False,
            "VertexAiMemoryBankService + 서빙 연결(memory_service) + ADK 읽기 도구(preload_memory) · 저장 콜백(add_session_to_memory) 구성 완료",
        )
    return (
        "AMBER",
        False,
        f"VertexAiMemoryBankService는 정의되었으나 실제 동작에 필요한 구성 누락: {', '.join(missing)}",
    )


def _eval_ar_memory(ctx: _Ctx) -> Eval:
    """AR-F2: Agent Runtime connects Memory Bank by default; app/agent.py (and custom fast_api_app.py) still need read/write/serving wiring."""
    code = _strip_py_comments(ctx.app_py)
    if "InMemoryMemoryService" in code and "VertexAiMemoryBankService" not in code:
        return ("RED", False, "InMemoryMemoryService 강제 사용 — Memory Bank(VertexAiMemoryBankService)로 전환 필요")
    has_read = bool(_MEM_READ_RE.search(code))
    has_write = bool(_MEM_WRITE_RE.search(code))
    missing: list[str] = []
    serving_src = _strip_py_comments(ctx.fast_api_py)
    if "Runner(" in serving_src and not re.search(r"\bmemory_service\s*=|\bmemory_service_uri\s*=", serving_src):
        missing.append("서빙 연결(fast_api_app.py memory_service)")
    if not has_read:
        missing.append("ADK 읽기 도구(preload_memory / load_memory)")
    if not has_write:
        missing.append("ADK 저장 콜백(add_session_to_memory)")
    if not missing:
        return (
            "GREEN",
            False,
            "Agent Runtime Memory Bank + ADK 읽기 도구(preload_memory) · 저장 콜백(add_session_to_memory) 구성 완료",
        )
    return (
        "AMBER",
        False,
        f"Agent Runtime Memory Bank 실제 동작에 필요한 구성 누락: {', '.join(missing)}",
    )


def _eval_gke_drain(ctx: _Ctx) -> Eval:
    """GKE-O1: terminationGracePeriodSeconds >= response time + preStop + readiness probe."""
    if not ctx.has_tf and not ctx.k8s_yaml:
        return ("RED", False, "GKE service.tf / Kubernetes 매니페스트 없음")
    src = ctx.service_tf + "\n" + ctx.k8s_yaml
    m = re.search(r"termination_grace_period_seconds\s*=\s*(\d+)", src) or re.search(
        r"terminationGracePeriodSeconds:\s*(\d+)", src
    )
    grace = int(m.group(1)) if m else 0
    has_grace = grace >= 60
    has_pre_stop = bool(re.search(r"\bpre_stop\b|\bpreStop:", src))
    has_readiness = bool(re.search(r"\breadiness_probe\b|\breadinessProbe:", src))
    if has_grace and has_pre_stop and has_readiness:
        return ("GREEN", False, f"terminationGracePeriodSeconds={grace}s · preStop · readiness 프로브 구성 완료")
    missing: list[str] = []
    if not has_grace:
        missing.append(f"terminationGracePeriodSeconds >= 60 (현재 {grace or '기본 30'}s)")
    if not has_pre_stop:
        missing.append("preStop")
    if not has_readiness:
        missing.append("readiness 프로브")
    status: RAGStatus = "AMBER" if (has_grace or has_pre_stop or has_readiness) else "RED"
    return (status, False, f"무중단 종료 설정 누락: {', '.join(missing)}")


def _pipeline_state(ctx: _Ctx) -> tuple[bool, str]:
    cb_staging = _read_text(ctx.root / ".cloudbuild" / "staging.yaml")
    gh_staging = _read_text(ctx.root / ".github" / "workflows" / "staging.yaml")
    return bool(cb_staging or gh_staging), cb_staging + "\n" + gh_staging


_SCAFFOLD_HINT = "`agents-cli scaffold enhance . --cicd-runner google_cloud_build`"


def _eval_cicd_pipeline(ctx: _Ctx) -> Eval:
    """*-D1: an agents-cli generated CI/CD pipeline exists (guidance only — never generated by the engine)."""
    has_cicd, _ = _pipeline_state(ctx)
    if has_cicd:
        return ("GREEN", False, "agents-cli CI/CD 파이프라인 확인 (.cloudbuild/staging.yaml 또는 .github/workflows/staging.yaml)")
    return (
        "RED",
        False,
        f"CI/CD 파이프라인 없음 — {_SCAFFOLD_HINT}로 생성 (엔진은 파이프라인을 만들지 않음 · "
        "`agents-cli infra cicd`는 운영 환경 구성이라 직접 수행)",
    )


def _eval_eval_gate(ctx: _Ctx) -> Eval:
    """*-D2: eval dataset/config exists AND the staging pipeline compares the agents-cli eval score to a threshold."""
    eval_dir = ctx.root / "tests" / "eval"
    has_eval_config = (eval_dir / "eval_config.yaml").is_file() or (eval_dir / "eval_config.json").is_file()
    datasets_dir = eval_dir / "datasets"
    has_eval_dataset = datasets_dir.is_dir() and any(datasets_dir.glob("*.json"))
    has_cicd, staging_txt = _pipeline_state(ctx)
    has_eval_gate = bool(
        "agent-eval-gate" in staging_txt
        or "agent_eval_gate" in staging_txt
        or ("eval" in staging_txt and "grade_results" in staging_txt)
    )
    if has_eval_config and has_eval_dataset and has_eval_gate:
        return ("GREEN", False, "tests/eval/ 평가 데이터셋·설정 및 staging 단계 평가 점수 게이트(기준 미달 시 중단) 확인")
    if not (has_eval_config or has_eval_dataset or has_eval_gate):
        return ("RED", False, "평가 데이터셋(tests/eval/)과 파이프라인 평가 게이트 모두 없음")
    missing: list[str] = []
    if not has_eval_config:
        missing.append("tests/eval/eval_config.yaml")
    if not has_eval_dataset:
        missing.append("tests/eval/datasets/*.json")
    if not has_eval_gate:
        missing.append("staging 평가 점수 게이트" + ("" if has_cicd else " (파이프라인 없음 — D1 먼저)"))
    return ("AMBER", False, f"평가 데이터셋 · 평가 게이트 일부 누락: {', '.join(missing)}")


def _eval_trace_privacy(ctx: _Ctx) -> Eval:
    """*-OB1: Cloud Trace export (otel_to_cloud) + GenAI message content capture disabled."""
    stf = ctx.service_tf
    has_no_content = bool(
        re.search(r'name\s*=\s*"OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"\s*\n[ \t]*value\s*=\s*"NO_CONTENT"', stf)
        or re.search(r'OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT\s*=\s*"NO_CONTENT"', stf)
        or re.search(r'value\s*=\s*"NO_CONTENT"', stf)
    )
    has_adk_span_off = bool(
        re.search(r'name\s*=\s*"ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS"\s*\n[ \t]*value\s*=\s*"false"', stf)
        or re.search(r'ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS\s*=\s*"false"', stf)
    )
    if ctx.target == "agent_runtime":
        if has_no_content and has_adk_span_off:
            return ("GREEN", False, "NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false — 프롬프트 미기록 구성 완료")
        if has_no_content or has_adk_span_off:
            return ("AMBER", False, "NO_CONTENT 또는 ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false 중 일부 누락")
        return ("RED", False, "GenAI 메시지 본문 수집 차단(NO_CONTENT) 미구성")

    has_otel_tf = "OTEL_SEMCONV_STABILITY_OPT_IN" in stf
    has_otel_code = _has_otel_to_cloud_enabled(ctx.fast_api_py)
    has_otel_deps = _has_adk_otel_gcp_extras(ctx.pyproject_toml)
    has_metric_writer = (not ctx.variables_tf) or ("roles/monitoring.metricWriter" in ctx.variables_tf)
    if has_otel_tf and has_otel_code and has_otel_deps and has_no_content and has_adk_span_off and has_metric_writer:
        return ("GREEN", False, "Cloud Trace 내보내기(otel_to_cloud=True · google-adk[gcp,otel-gcp]) + NO_CONTENT 프롬프트 미기록 구성 완료")
    if has_otel_tf or has_otel_code or has_no_content:
        missing: list[str] = []
        if not has_otel_tf:
            missing.append("service.tf OTEL_SEMCONV_STABILITY_OPT_IN")
        if not has_otel_code:
            missing.append("fast_api_app.py otel_to_cloud=True")
        if not has_otel_deps:
            missing.append("pyproject.toml google-adk[gcp,otel-gcp]")
        if not has_no_content or not has_adk_span_off:
            missing.append("NO_CONTENT + ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS=false")
        if not has_metric_writer:
            missing.append("variables.tf roles/monitoring.metricWriter")
        return ("AMBER", False, f"Cloud Trace / 프롬프트 미기록 설정 일부 누락: {', '.join(missing)}")
    return ("RED", False, "Cloud Trace 내보내기 및 프롬프트 미기록(NO_CONTENT) 미구성")


_PUBLIC_INVOKER_RE = re.compile(
    r'members?\s*=\s*\[?[^\n\]]*"allUsers"|invoker_iam_disabled\s*=\s*true', re.MULTILINE
)
_ALLOW_UNAUTH_RE = re.compile(r"(?<!no-)--allow-unauthenticated\b")


def _eval_cr_authn(ctx: _Ctx) -> Eval:
    """CR-S1: Cloud Run requires IAM authentication (no allUsers invoker / --allow-unauthenticated)."""
    public_tf = bool(_PUBLIC_INVOKER_RE.search(ctx.all_tf))
    public_cli = bool(_ALLOW_UNAUTH_RE.search(ctx.deploy_scripts))
    if public_tf or public_cli:
        where = []
        if public_tf:
            where.append("Terraform allUsers invoker / invoker_iam_disabled = true")
        if public_cli:
            where.append("배포 스크립트 --allow-unauthenticated")
        return ("RED", False, f"인증 없는 공개 호출 허용: {', '.join(where)}")
    if "--no-allow-unauthenticated" in ctx.deploy_scripts:
        return ("GREEN", False, "배포 스크립트 --no-allow-unauthenticated 명시 · Terraform allUsers invoker 없음 (IAM 인증 필수)")
    if ctx.has_tf:
        return ("GREEN", False, "Terraform에 allUsers invoker / invoker_iam_disabled 없음 — Cloud Run IAM 인증 필수 (기본값)")
    return ("AMBER", False, "배포 설정(service.tf / 배포 스크립트) 없음 — `--no-allow-unauthenticated` 또는 IAM invoker 바인딩 확인 필요")


_OVER_PRIV_RE = re.compile(r'"roles/(?:storage\.admin|editor|owner)"')


def _eval_identity(ctx: _Ctx) -> Eval:
    """CR-S2 / AR-S1 / GKE-S1: dedicated agent identity (Agent Identity or dedicated SA) + least privilege."""
    if not ctx.has_tf:
        return ("RED", False, "service.tf 없음 — 에이전트 전용 신원·최소 권한 확인 불가")
    role_src = ctx.variables_tf + "\n" + ctx.iam_tf
    over_priv = _OVER_PRIV_RE.search(role_src)
    if ctx.target == "cloud_run":
        has_agent_identity = bool(re.search(r'identity_type\s*=\s*"IDENTITY_TYPE_AGENT_IDENTITY"', ctx.service_tf))
        has_dedicated_sa = bool(re.search(r"^\s*service_account\s*=", ctx.service_tf, flags=re.MULTILINE))
        ident = "Agent Identity" if has_agent_identity else ("전용 서비스 계정" if has_dedicated_sa else "")
    elif ctx.target == "agent_runtime":
        has_agent_identity = bool(re.search(r'identity_type\s*=\s*"AGENT_IDENTITY"', ctx.service_tf))
        has_dedicated_sa = bool(re.search(r"^\s*service_account\s*=", ctx.service_tf, flags=re.MULTILINE))
        ident = "Agent Identity" if has_agent_identity else ("전용 서비스 계정" if has_dedicated_sa else "")
    else:
        has_agent_identity = "iam.gke.io/spiffe-identity-type" in ctx.service_tf
        has_wi = "roles/iam.workloadIdentityUser" in (ctx.service_tf + ctx.iam_tf) and "iam.gke.io/gcp-service-account" in ctx.service_tf
        ident = "Agent Identity" if has_agent_identity else ("Workload Identity 전용 서비스 계정" if has_wi else "")
    if over_priv:
        return ("RED", False, f"과도한 역할 부여: {over_priv.group(0)} — 필요한 최소 역할(예: roles/storage.objectUser)로 축소 필요")
    if not ident:
        return ("AMBER", False, "에이전트 전용 신원(Agent Identity / 전용 서비스 계정 / Workload Identity) 미지정 — 기본 서비스 계정 사용 위험")
    if ctx.target == "agent_runtime" and '"roles/cloudsql.client"' in role_src:
        return ("AMBER", False, f"{ident} 사용 중이나 불필요한 roles/cloudsql.client가 남아 있음")
    return ("GREEN", False, f"{ident} + 최소 권한 역할 구성 확인")


def _eval_registry(ctx: _Ctx) -> Eval:
    """CR-S3 / AR-S2 / GKE-S3 (Preview): Agent Registry registration."""
    profile = ctx.mcp_a2a
    has_registry_api = "agentregistry.googleapis.com" in (ctx.apis_tf + ctx.prod_ready_tf + ctx.service_tf)
    if ctx.target == "cloud_run":
        func = "FUNCTIONAL_TYPE_MCP_SERVER" if profile.is_mcp_server else "FUNCTIONAL_TYPE_AGENT"
        has_func = bool(re.search(rf'functional_type\s*=\s*"{func}"', ctx.service_tf))
        if has_func and has_registry_api:
            status: RAGStatus = "GREEN"
            evidence = f"{func} + agentregistry.googleapis.com — Agent Registry 자동 등록 구성 완료"
        elif has_func or has_registry_api:
            status, evidence = "AMBER", f"functional_type = \"{func}\" 또는 agentregistry.googleapis.com 활성화 일부 누락"
        else:
            status, evidence = "RED", f"Agent Registry 자동 등록(functional_type = \"{func}\") 미구성"
    elif ctx.target == "gke":
        has_label = "registry.gke.io/functional-type" in ctx.service_tf or "registry.gke.io/functional-type" in ctx.k8s_yaml
        has_apphub = "apphub.googleapis.com" in (ctx.apis_tf + ctx.prod_ready_tf)
        if has_label and has_registry_api and has_apphub:
            status, evidence = "GREEN", 'Deployment registry.gke.io/functional-type="AGENT" + agentregistry/apphub API — 자동 등록 구성 완료'
        elif has_label or has_registry_api:
            missing = []
            if not has_label:
                missing.append('registry.gke.io/functional-type="AGENT"')
            if not (has_registry_api and has_apphub):
                missing.append("agentregistry/apphub API")
            status, evidence = "AMBER", f"Agent Registry 등록 일부 누락: {', '.join(missing)}"
        else:
            status, evidence = "RED", 'Agent Registry 자동 등록(registry.gke.io/functional-type="AGENT") 미구성'
    else:
        if has_registry_api:
            status, evidence = "GREEN", "agentregistry.googleapis.com 활성화 — Agent Registry 등록 구성 완료"
        elif re.search(r'identity_type\s*=\s*"AGENT_IDENTITY"', ctx.service_tf):
            status, evidence = "AMBER", "Agent Identity는 있으나 agentregistry.googleapis.com 미활성화"
        else:
            status, evidence = "RED", "Agent Registry 등록(agentregistry.googleapis.com) 미구성"

    remote = profile.hardcoded_mcp_urls + profile.hardcoded_a2a_urls
    if remote and not profile.uses_agent_registry:
        status = "AMBER" if status == "GREEN" else status
        evidence += (
            f" | ⚠️ 원격 MCP/A2A URL 하드코딩({', '.join(remote[:3])}) — AgentRegistry(get_mcp_toolset / get_remote_a2a_agent) 전환 권장"
        )
    elif status == "GREEN" and profile.uses_agent_registry:
        evidence += " + ADK AgentRegistry 연동 확인"
    return (status, False, evidence)


def _collect_agw_configs(root: Path) -> str:
    """Reads optional Agent Gateway / Model Armor / IAP policy YAML/JSON files in the repo."""
    parts: list[str] = []
    for pat in (
        "agw-*.y*ml",
        "model-armor*.y*ml",
        "sgp-policy*.y*ml",
        "iap-policy*.json",
        "deployment/agent-gateway/*.y*ml",
        "deployment/agent-gateway/*.json",
        "deployment/gateway/*.y*ml",
    ):
        for f in sorted(root.glob(pat)):
            if f.is_file():
                parts.append(_read_text(f))
    return "\n".join(parts)


def _eval_gateway(ctx: _Ctx) -> Eval:
    """CR-S4 / AR-S3 / GKE-S4 (Preview): Agent Gateway egress policy."""
    if ctx.target == "cloud_run":
        cr_agw_src = "\n".join((ctx.all_tf, _collect_agw_configs(ctx.root)))
        has_gw = bool(re.search(r"agent_gateway|agent-gateways|AGENT_TO_ANYWHERE", cr_agw_src))
        has_pol = bool(
            re.search(
                r"authz_policy|AuthzPolicy|authz_extension|AuthzExtension|iap\.egressor|model_armor|modelarmor|piAndJailbreakFilterSettings",
                cr_agw_src,
            )
        )
        if has_gw and has_pol:
            return ("GREEN", False, "Agent Gateway for Cloud Run + 접근 정책(AuthzPolicy · IAP/Model Armor) 구성 확인")
        if has_gw:
            return (
                "AMBER",
                False,
                "Agent Gateway 리소스만 감지되고 AuthzPolicy(IAP V2 DRY_RUN) · roles/iap.egressor CEL · Model Armor 정책이 누락됨 — 단순 게이트웨이 생성만으로는 외부 호출이 통제되지 않음",
            )
        return (
            "RED",
            False,
            "Agent Gateway for Cloud Run 미구성 (자동 패치 없음 — references/agent-registry-mcp-a2a.md §6 가이드에 따라 신원·Registry·PSC/DNS·IAP V2 DRY_RUN·Model Armor 세트로 구성)",
        )
    if ctx.target == "gke":
        gke_src = ctx.k8s_agent_gateway_yaml + "\n" + ctx.k8s_yaml
        has_crd_gw = "GCPAgentGateway" in gke_src
        has_crd_pol = "GCPAuthzPolicy" in gke_src or "GCPEgressPolicy" in gke_src
        if has_crd_gw and has_crd_pol:
            return ("GREEN", False, "Agent Gateway for GKE (GCPAgentGateway · GCPAuthzPolicy/GCPEgressPolicy · Model Armor 바인딩) 매니페스트 구성 완료")
        if has_crd_gw:
            return ("AMBER", False, "GCPAgentGateway만 감지되고 GCPAuthzPolicy · GCPEgressPolicy · Model Armor 바인딩이 누락됨")
        if ctx.k8s_agent_sandbox_yaml or ctx.k8s_agent_substrate_yaml:
            return ("AMBER", False, "Agent Sandbox/Substrate 사용 중 — Agent Gateway for GKE는 표준 Pod 전용이므로 표준 Pod 경로에 별도 구성 필요")
        return ("RED", False, "Agent Gateway for GKE 매니페스트(deployment/k8s/agent-gateway.yaml) 미구성")
    has_agw_res = (
        "google_network_services_agent_gateway" in ctx.prod_ready_tf
        and "google_network_security_authz_policy" in ctx.prod_ready_tf
    )
    has_agw_wired = "agent_gateway_config" in ctx.service_tf
    has_ca_bundle = "AGENT_GATEWAY_ROOT_CERTIFICATES" in ctx.dockerfile
    if has_agw_res and has_agw_wired and has_ca_bundle:
        return (
            "GREEN",
            False,
            "Agent Gateway(AGENT_TO_ANYWHERE · DRY_RUN) + AuthzPolicy + agent_gateway_config + Dockerfile CA 신뢰 구성 완료 (ENFORCED 전환 전 roles/iap.egressor CEL · Model Armor 템플릿 확인)",
        )
    if has_agw_res or has_agw_wired:
        return ("AMBER", False, "Agent Gateway 리소스·AuthzPolicy·service.tf 바인딩·Dockerfile CA 신뢰 중 일부 누락")
    return ("RED", False, "Agent Gateway(AGENT_TO_ANYWHERE · 시험 모드 DRY_RUN) 미구성")


_CR_SANDBOX_RE = re.compile(r"sandbox[-_]launcher|sandboxLauncher|run\.googleapis\.com/sandbox", re.IGNORECASE)
_AR_CODE_EXEC_RE = re.compile(
    r"AgentEngineSandboxCodeExecutor|sandboxes\.(?:create|execute_code)|execute_code\s*\(|sandbox_environment|"
    r"code_execution_environment|SandboxEnvironment",
)


def _code_exec_kind(ctx: _Ctx) -> str:
    """Classifies how AI-generated code is executed: 'model' (BuiltInCodeExecutor only) or 'local'."""
    code = _strip_py_comments(ctx.app_py)
    local = re.search(
        r"UnsafeLocalCodeExecutor|ContainerCodeExecutor|subprocess\.(?:run|Popen|call|check_output|check_call)\s*\(|\bos\.system\s*\(",
        code,
    )
    if local:
        return "local"
    return "model" if "BuiltInCodeExecutor" in code else "local"


def _eval_cr_sandbox(ctx: _Ctx) -> Eval:
    """CR-S5 (Preview, conditional code): AI-generated code runs in Cloud Run sandboxes (--sandbox-launcher)."""
    src = "\n".join((ctx.service_tf, ctx.deploy_scripts, ctx.app_py))
    if _CR_SANDBOX_RE.search(src):
        return ("GREEN", False, "Cloud Run sandbox(--sandbox-launcher) 구성 확인 — AI 생성 코드가 샌드박스에서 격리 실행")
    if _code_exec_kind(ctx) == "model":
        return ("AMBER", False, "BuiltInCodeExecutor(모델 측 실행)만 감지 — 컨테이너에서 코드를 실행한다면 Cloud Run sandbox(--sandbox-launcher) 사용")
    return ("RED", False, "AI 생성 코드 실행이 감지되었으나 Cloud Run sandbox(--sandbox-launcher) 미구성 — 컨테이너에서 직접 실행")


def _eval_ar_code_execution(ctx: _Ctx) -> Eval:
    """AR-S4 (conditional code): AI-generated code runs in Agent Platform Code Execution (managed sandbox)."""
    if _AR_CODE_EXEC_RE.search(_strip_py_comments(ctx.app_py)):
        return ("GREEN", False, "Agent Platform Code Execution(관리형 샌드박스)에서 AI 생성 코드 실행 확인")
    if _code_exec_kind(ctx) == "model":
        return ("AMBER", False, "BuiltInCodeExecutor(모델 측 실행)만 감지 — 런타임에서 코드를 실행한다면 Agent Platform Code Execution 사용")
    return ("RED", False, "AI 생성 코드 실행이 감지되었으나 Agent Platform Code Execution 미사용 — Agent Runtime 컨테이너에서 직접 실행")


def _has_agent_substrate(ctx: _Ctx) -> bool:
    src = ctx.k8s_agent_substrate_yaml + "\n" + ctx.k8s_yaml
    return bool(re.search(r"ate\.dev/v1alpha1|kind:\s*WorkerPool|kind:\s*ActorTemplate", src))


def _eval_gke_idle_suspend(ctx: _Ctx) -> Eval:
    """GKE-O2: idle conversational agents run on Agent Substrate (snapshot suspend · fast resume)."""
    autopilot = bool(re.search(r"\benable_autopilot\s*=\s*true\b", ctx.service_tf))
    if _has_agent_substrate(ctx):
        if autopilot:
            return ("AMBER", False, "Agent Substrate 매니페스트가 있으나 클러스터가 Autopilot — Agent Substrate는 GKE Standard 1.37 이상 필요")
        return ("GREEN", False, "Agent Substrate on GKE(ate.dev WorkerPool) 사용 — 유휴 에이전트 스냅샷 일시정지 · 요청 시 복원")
    if "SandboxTemplate" in (ctx.k8s_agent_sandbox_yaml + ctx.k8s_yaml):
        if re.search(r"kind:\s*PodSnapshot(Policy|ManualTrigger)|PodSnapshotStorageConfig", ctx.k8s_agent_sandbox_yaml + ctx.k8s_yaml):
            return ("GREEN", False, "Agent Sandbox + GKE Pod snapshot 구성 — Sandbox Suspended 전환 전 상태 저장 · 복원(약 3~5초)")
        return ("AMBER", False, "Agent Sandbox만 구성 — 일시정지 전에 Pod snapshot(PodSnapshotPolicy)으로 상태 저장 필요, 또는 Agent Substrate on GKE 사용(권장)")
    return ("RED", False, "자주 일시정지·재개하는 상태 유지형 에이전트이나 Agent Substrate 미사용 — 유휴 Pod가 CPU·메모리 계속 점유")


def _eval_gke_code_sandbox(ctx: _Ctx) -> Eval:
    """GKE-S2: AI-generated code runs in Agent Substrate (or Agent Sandbox) on GKE."""
    autopilot = bool(re.search(r"\benable_autopilot\s*=\s*true\b", ctx.service_tf))
    has_substrate = bool(re.search(r"kind:\s*WorkerPool|ActorTemplate", ctx.k8s_agent_substrate_yaml + ctx.k8s_yaml))
    has_sandbox = "SandboxTemplate" in (ctx.k8s_agent_sandbox_yaml + ctx.k8s_yaml)
    if has_substrate and autopilot:
        return ("AMBER", False, "Agent Substrate 매니페스트가 있으나 현재 클러스터가 Autopilot — Autopilot은 Agent Sandbox on GKE 사용")
    if has_substrate:
        return ("GREEN", False, "Agent Substrate on GKE 샌드박스에서 AI 생성 코드 실행 (ate.dev WorkerPool)")
    if has_sandbox:
        return ("GREEN", False, "Agent Sandbox on GKE(SandboxTemplate)에서 AI 생성 코드 실행")
    if re.search(r'runtime_class_name\s*=\s*"gvisor"|runtimeClassName:\s*gvisor', ctx.service_tf + ctx.k8s_yaml):
        return ("AMBER", False, "gVisor만 적용 — AI 생성 코드는 Agent Substrate / Agent Sandbox on GKE로 격리 권장")
    return ("RED", False, "AI 생성 코드 실행이 감지되었으나 Agent Substrate / Agent Sandbox 격리 미구성")


def _eval_delegated_auth(ctx: _Ctx) -> Eval:
    """AR-S5 / CR-S6 / GKE-S5 (Preview, conditional delegated): user-delegated OAuth via Agent Identity auth manager."""
    static = _static_secret_in_tool_headers(ctx)
    if static:
        return (
            "RED",
            False,
            f"외부 도구·MCP 인증 헤더에 정적 자격 증명 사용 ({static}) — 모든 사용자가 같은 권한으로 호출 · "
            "Agent Identity auth manager의 사용자 위임 OAuth 토큰으로 전환",
        )
    has_extra = _has_adk_agent_identity_extra(ctx.pyproject_toml)
    am_hit = _auth_manager_code_hit(ctx)
    if has_extra and am_hit:
        return ("GREEN", False, f"google-adk[agent-identity] + auth manager auth provider 연동 확인 (`{am_hit}`) — 사용자 위임 토큰 사용")
    if am_hit:
        return ("AMBER", False, f"auth manager 코드(`{am_hit}`)는 있으나 pyproject.toml에 `google-adk[agent-identity]` extra 누락")
    if has_extra:
        return ("AMBER", False, "`google-adk[agent-identity]` extra는 있으나 도구에 auth provider(GcpAuthProviderScheme) 연결 미감지")
    return (
        "AMBER",
        False,
        "사용자 대신 외부 도구를 호출하지만 Agent Identity auth manager 미사용 — auth provider(3-legged OAuth) 구성 후 "
        "`google-adk[agent-identity]`로 도구·MCP 호출에 사용자 위임 토큰 연결",
    )


def _has_adk_bq_analytics_extra(pyproject_toml: str) -> bool:
    for m in re.finditer(r'["\']google-adk\[([^\]]+)\]', pyproject_toml or ""):
        extras = {e.strip().lower() for e in m.group(1).split(",")}
        if "bigquery-analytics" in extras or "bigquery_analytics" in extras or "all" in extras:
            return True
    return False


def _eval_bq_analytics(ctx: _Ctx) -> Eval:
    """AR-OB2 / CR-OB2 / GKE-OB2 (Tier 3 Optional): BigQueryAgentAnalyticsPlugin + BigQuery dataset."""
    code = _strip_py_comments(ctx.app_py)
    has_plugin = "BigQueryAgentAnalyticsPlugin" in code
    has_extra = _has_adk_bq_analytics_extra(ctx.pyproject_toml) or not bool(
        re.search(r'["\']google-adk\b', ctx.pyproject_toml or "")
    )
    has_dataset = "google_bigquery_dataset" in ctx.all_tf
    if has_plugin and has_dataset and has_extra:
        return ("GREEN", False, "BigQueryAgentAnalyticsPlugin + google-adk[bigquery-analytics] + BigQuery 데이터셋 구성 완료")
    if has_plugin or has_dataset:
        missing = []
        if not has_plugin:
            missing.append("BigQueryAgentAnalyticsPlugin 코드")
        if not has_extra:
            missing.append("pyproject.toml google-adk[bigquery-analytics]")
        if not has_dataset:
            missing.append("BigQuery 데이터셋 Terraform(bq_analytics.tf)")
        return ("AMBER", False, f"BigQuery Agent Analytics 일부 구성 — 보완 필요: {', '.join(missing)}")
    return (
        "NA",
        False,
        "선택 항목(미적용) — 장기 토큰 비용·도구 출처(Provenance)·SQL 분석이 필요할 때 선택 적용 "
        "(근거: ADK BigQuery Agent Analytics · agents-cli --bq-analytics)",
    )


def _eval_model_armor(ctx: _Ctx) -> Eval:
    """AR-S6 / CR-S7 / GKE-S6 (Tier 3 Optional): Model Armor template & inline/gateway guardrails."""
    all_src = "\n".join((ctx.all_tf, ctx.k8s_yaml, _strip_py_comments(ctx.app_py)))
    has_tpl = "google_model_armor_template" in ctx.all_tf or "modelArmorTemplate" in all_src
    has_filter = (
        "pi_and_jailbreak_filter_settings" in ctx.all_tf
        or "piAndJailbreakFilterSettings" in all_src
        or "sanitize_user_prompt" in all_src
        or "ModelArmorClient" in all_src
    )
    if has_tpl and has_filter:
        return ("GREEN", False, "Model Armor 템플릿(프롬프트 인젝션·탈옥·SDP 필터) 구성 완료")
    if has_tpl or "modelarmor.googleapis.com" in ctx.all_tf:
        return ("AMBER", False, "Model Armor API 또는 일부 설정만 감지 — google_model_armor_template(pi_and_jailbreak · sdp) 완성 필요")
    return (
        "NA",
        False,
        "선택 항목(미적용 · 유료) — 외부 입력 프롬프트 인젝션·탈옥·민감정보(SDP) 차단이 필요할 때 선택 적용 "
        "(근거: Model Armor · Well-Architected AI & ML · SAIF)",
    )


_HIGH_RISK_TOOL_RE = re.compile(
    r"def\s+((?:delete|remove|drop|pay|charge|transfer|refund|cancel|write|update|send_email|execute_sql)[a-zA-Z0-9_]*)\s*\("
)


def _eval_tool_confirmation(ctx: _Ctx) -> Eval:
    """AR-S7 / CR-S8 / GKE-S7 (Tier 3 Optional, guidance-only): ADK Tool Confirmation (HITL)."""
    code = _strip_py_comments(ctx.app_py)
    hit = _first_match(re.compile(r"\brequire_confirmation\s*=|\bToolConfirmation\b|adk_request_confirmation"), code)
    if hit:
        return ("GREEN", False, f"ADK Tool Confirmation(HITL) 승인 게이트 감지 (`{hit}`)")
    risky = sorted({m.group(1) for m in _HIGH_RISK_TOOL_RE.finditer(code)})
    if risky:
        return (
            "NA",
            False,
            f"선택 항목(미적용 · 안내 전용) — 쓰기/삭제성 후보 도구({', '.join(risky[:4])}) 감지됨 · "
            "고위험 도구에 `FunctionTool(..., require_confirmation=True)` 적용 권장 (근거: ADK Tool Confirmation · ADK Safety)",
        )
    return (
        "NA",
        False,
        "선택 항목(미적용 · 안내 전용) — 결제·삭제·외부 쓰기 도구 실행 전 사람 승인(HITL)이 필요할 때 선택 적용 "
        "(근거: ADK Tool Confirmation · ADK Safety)",
    )


def _eval_ar_online_monitor(ctx: _Ctx) -> Eval:
    """AR-DO2 (Agent Runtime exclusive · Tier 3 Optional, guidance-only): Online Evaluation Monitor."""
    src = "\n".join((ctx.all_tf, _strip_py_comments(ctx.app_py), ctx.deploy_scripts))
    hit = _first_match(
        re.compile(r"aiplatform\.googleapis\.com/online_evaluator|\bOnlineEvaluator\b|create_online_monitor"),
        src,
    )
    if hit:
        return ("GREEN", False, f"Agent Runtime Online Evaluation Monitor 구성 감지 (`{hit}`)")
    return (
        "NA",
        False,
        "선택 항목(미적용 · Agent Runtime 전용 · 추가 LLM 평가 과금) — 운영 트래픽의 최종 응답 품질·도구 사용 품질·환각을 "
        "실시간 감시하려면 `agent-platform-alert-configuration` 스킬로 Online Monitor 구성",
    )


_EVALUATORS: dict[str, Any] = {
    # Common (all runtimes)
    "COMMON-DO1": _eval_cicd_pipeline,
    "COMMON-DO2": _eval_eval_gate,
    "COMMON-DO3": _eval_bq_analytics,
    "COMMON-ID1": _eval_delegated_auth,
    "COMMON-SF1": _eval_model_armor,
    "COMMON-SF2": _eval_tool_confirmation,
    # Agent Runtime
    "AR-RS1": _eval_ar_session,
    "AR-RS2": _eval_ar_memory,
    "AR-ID1": _eval_identity,
    "AR-ID2": _eval_registry,
    "AR-SF1": _eval_gateway,
    "AR-SF2": _eval_ar_code_execution,
    "AR-DO1": _eval_trace_privacy,
    "AR-DO2": _eval_ar_online_monitor,
    # Cloud Run
    "CR-RS1": _eval_session_store,
    "CR-RS2": _eval_memory_bank,
    "CR-ID1": _eval_cr_authn,
    "CR-ID2": _eval_identity,
    "CR-ID3": _eval_registry,
    "CR-SF1": _eval_gateway,
    "CR-SF2": _eval_cr_sandbox,
    "CR-DO1": _eval_trace_privacy,
    # GKE
    "GKE-RS1": _eval_session_store,
    "GKE-RS2": _eval_memory_bank,
    "GKE-RS3": _eval_gke_drain,
    "GKE-RS4": _eval_gke_idle_suspend,
    "GKE-ID1": _eval_identity,
    "GKE-ID2": _eval_registry,
    "GKE-SF1": _eval_gke_code_sandbox,
    "GKE-SF2": _eval_gateway,
    "GKE-DO1": _eval_trace_privacy,
}


def _evaluate_item(*, spec: ChecklistItemSpec, ctx: _Ctx) -> Eval:
    """Dispatches a v3 item to its evaluator, returning (status, managed_by_platform, evidence)."""
    fn = _EVALUATORS.get(spec.id)
    if fn is None:
        return ("AMBER", False, "점검 필요 (평가 로직 미정의)")
    return fn(ctx)
