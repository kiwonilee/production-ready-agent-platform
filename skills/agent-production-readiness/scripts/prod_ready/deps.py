"""Dependency preflight for the Production-Readiness Agent Skill.

Required (missing → fail, exit 1):
- ``agents-cli`` binary >= 1.5.0 (install: ``uv tool install google-agents-cli``)
- agents-cli skills ``google-agents-cli-deploy`` · ``google-agents-cli-eval`` · ``google-agents-cli-scaffold``
  (install: ``agents-cli setup``)

Recommended (missing → prominent warning, never blocking):
- Google Cloud platform skills for the target (catalog ``platform_skills``,
  https://github.com/google/skills/tree/main/skills/cloud). Installed only with explicit consent
  (``prod-ready doctor --install-platform-skills``).

Skill discovery: ``<dir>/<name>/SKILL.md`` across well-known skill roots, plus the
``PROD_READY_SKILL_DIRS`` env override (``os.pathsep``-separated; when set, only those dirs are used).
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from prod_ready.checklist import ALL_TARGETS, CATALOG, PLATFORM_SKILLS_REPO, TargetType

AGENTS_CLI_MIN_VERSION = (1, 5, 0)
AGENTS_CLI_MIN_VERSION_STR = ".".join(str(x) for x in AGENTS_CLI_MIN_VERSION)
REQUIRED_SKILLS: tuple[str, ...] = (
    "google-agents-cli-deploy",
    "google-agents-cli-eval",
    "google-agents-cli-scaffold",
)
AGENTS_CLI_INSTALL_CMD = "uv tool install google-agents-cli"
AGENTS_CLI_UPGRADE_CMD = "uv tool upgrade google-agents-cli"
AGENTS_CLI_SKILLS_INSTALL_CMD = "agents-cli setup"
GOOGLE_SKILLS_REPO = "google/skills"
GOOGLE_SKILLS_TARBALL_URL = "https://codeload.github.com/google/skills/tar.gz/refs/heads/main"
DEFAULT_INSTALL_DIR = Path.home() / ".agents" / "skills"
SKIP_ENV = "PROD_READY_SKIP_DEPS_CHECK"
SKILL_DIRS_ENV = "PROD_READY_SKILL_DIRS"
AGENTS_CLI_ENV = "PROD_READY_AGENTS_CLI"  # optional explicit binary path (tests / custom installs)

PLATFORM_BANNER = "⚠️ 플랫폼 Skill 미설치 — 설치를 강력히 권장합니다"


def platform_skills_for_target(target: TargetType | None) -> list[str]:
    skills = CATALOG.get("platform_skills", {})
    if target:
        return list(skills.get(target, []))
    seen: list[str] = []
    for t in ALL_TARGETS:
        for name in skills.get(t, []):
            if name not in seen:
                seen.append(name)
    return seen


def skill_search_dirs(project_dir: str | Path | None = None) -> list[Path]:
    """Skill roots to search (env override wins)."""
    override = os.environ.get(SKILL_DIRS_ENV)
    if override is not None:
        return [Path(p).expanduser() for p in override.split(os.pathsep) if p.strip()]
    home = Path.home()
    dirs = [
        home / ".gemini" / "config" / "skills",
        home / ".gemini" / "skills",
        home / ".gemini" / ".agents" / "skills",
        home / ".agents" / "skills",
        home / ".claude" / "skills",
        home / ".codex" / "skills",
        home / ".cursor" / "skills",
    ]
    plugins = home / ".gemini" / "config" / "plugins"
    if plugins.is_dir():
        dirs.extend(sorted(p / "skills" for p in plugins.iterdir() if (p / "skills").is_dir()))
    if project_dir:
        root = Path(project_dir).expanduser().resolve()
        dirs.extend([root / ".agents" / "skills", root / ".claude" / "skills", root / ".gemini" / "skills"])
    out: list[Path] = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def find_skill(name: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        candidate = d / name / "SKILL.md"
        if candidate.is_file():
            return candidate.parent
    return None


def _parse_version(text: str) -> tuple[int, ...] | None:
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", text or "")
    if not m:
        return None
    return tuple(int(x) for x in m.groups(default="0"))


def check_agents_cli(runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Returns {'found', 'path', 'version', 'ok'} for the agents-cli binary."""
    binary = os.environ.get(AGENTS_CLI_ENV) or shutil.which("agents-cli")
    if not binary:
        return {"found": False, "path": None, "version": None, "ok": False}
    run = runner or subprocess.run
    try:
        proc = run([binary, "--version"], capture_output=True, text=True, timeout=20)
        out = f"{proc.stdout}\n{proc.stderr}"
    except Exception as exc:  # noqa: BLE001 — broken binary == not usable
        return {"found": True, "path": binary, "version": None, "ok": False, "error": str(exc)}
    ver = _parse_version(out.split("\n")[0] if out.strip() else "") or _parse_version(out)
    ok = bool(ver) and ver >= AGENTS_CLI_MIN_VERSION
    return {"found": True, "path": binary, "version": ".".join(map(str, ver)) if ver else None, "ok": ok}


@dataclass
class DepsReport:
    target: str | None
    agents_cli: dict[str, Any]
    required_found: dict[str, str] = field(default_factory=dict)
    required_missing: list[str] = field(default_factory=list)
    platform_found: dict[str, str] = field(default_factory=dict)
    platform_missing: list[str] = field(default_factory=list)
    search_dirs: list[str] = field(default_factory=list)

    @property
    def required_ok(self) -> bool:
        return bool(self.agents_cli.get("ok")) and not self.required_missing

    @property
    def platform_ok(self) -> bool:
        return not self.platform_missing

    def platform_status(self) -> list[dict[str, Any]]:
        names = list(self.platform_found) + [n for n in self.platform_missing if n not in self.platform_found]
        ordered = [n for n in platform_skills_for_target(self.target) if n in names] or names
        return [
            {
                "name": n,
                "installed": n in self.platform_found,
                "path": self.platform_found.get(n, ""),
                "url": f"{PLATFORM_SKILLS_REPO}/{n}",
            }
            for n in ordered
        ]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(required_ok=self.required_ok, platform_ok=self.platform_ok, platform_status=self.platform_status())
        return d


def check_dependencies(
    target: TargetType | None = None,
    project_dir: str | Path | None = None,
    runner: Callable[..., Any] | None = None,
    check_cli: bool = True,
) -> DepsReport:
    dirs = skill_search_dirs(project_dir)
    cli = check_agents_cli(runner) if check_cli else {"found": None, "path": None, "version": None, "ok": True, "skipped": True}
    rep = DepsReport(target=target, agents_cli=cli, search_dirs=[str(d) for d in dirs])
    for name in REQUIRED_SKILLS:
        path = find_skill(name, dirs)
        if path:
            rep.required_found[name] = str(path)
        else:
            rep.required_missing.append(name)
    for name in platform_skills_for_target(target):
        path = find_skill(name, dirs)
        if path:
            rep.platform_found[name] = str(path)
        else:
            rep.platform_missing.append(name)
    return rep


# ---------------------------------------------------------------------------
# Guidance text
# ---------------------------------------------------------------------------
def required_guidance(rep: DepsReport) -> list[str]:
    lines: list[str] = []
    cli = rep.agents_cli
    if not cli.get("found"):
        lines.append(f"❌ agents-cli 미설치 (>= {AGENTS_CLI_MIN_VERSION_STR} 필요) → `{AGENTS_CLI_INSTALL_CMD}`")
    elif not cli.get("ok"):
        lines.append(
            f"❌ agents-cli {cli.get('version') or '버전 확인 불가'} (>= {AGENTS_CLI_MIN_VERSION_STR} 필요) → `{AGENTS_CLI_UPGRADE_CMD}`"
        )
    if rep.required_missing:
        lines.append(
            f"❌ agents-cli Skill 미설치: {', '.join(rep.required_missing)} → `{AGENTS_CLI_SKILLS_INSTALL_CMD}`"
        )
    return lines


def platform_install_commands(rep: DepsReport) -> list[str]:
    target_flag = f" --target {rep.target}" if rep.target else ""
    skills = " ".join(f"--skill {n}" for n in rep.platform_missing)
    return [
        f"prod-ready doctor{target_flag} --install-platform-skills",
        f"npx skills add {GOOGLE_SKILLS_REPO} {skills} -g -y".replace("  ", " "),
    ]


def platform_banner_lines(rep: DepsReport) -> list[str]:
    if rep.platform_ok:
        return []
    bar = "!" * 84
    lines = [
        bar,
        f"  {PLATFORM_BANNER}",
        f"  대상 환경: {rep.target or '전체'} — 일반 플랫폼 Best Practice는 이 체크리스트가 점검하지 않습니다.",
        "  미설치 Skill:",
    ]
    lines += [f"    ❌ {n}  ({PLATFORM_SKILLS_REPO}/{n})" for n in rep.platform_missing]
    lines.append("  설치 명령:")
    lines += [f"    $ {c}" for c in platform_install_commands(rep)]
    lines.append(bar)
    return lines


def format_doctor_table(rep: DepsReport) -> str:
    out = ["=" * 84, "🩺 prod-ready doctor — 의존성 점검", "=" * 84, "[필수] 미설치 시 중단"]
    cli = rep.agents_cli
    cli_state = (
        f"✅ {cli.get('version')} ({cli.get('path')})"
        if cli.get("ok")
        else ("❌ 미설치" if not cli.get("found") else f"❌ {cli.get('version') or '버전 확인 불가'} (>= {AGENTS_CLI_MIN_VERSION_STR})")
    )
    out.append(f"  {'agents-cli (>= ' + AGENTS_CLI_MIN_VERSION_STR + ')':<42} {cli_state}")
    for n in REQUIRED_SKILLS:
        out.append(f"  {n:<42} {'✅ ' + rep.required_found[n] if n in rep.required_found else '❌ 미설치'}")
    out.append("-" * 84)
    out.append(f"[권장] 플랫폼 Skill ({rep.target or '전체'}) — 미설치여도 진행하지만 설치를 강력히 권장")
    for s in rep.platform_status():
        out.append(f"  {s['name']:<42} {'✅ ' + s['path'] if s['installed'] else '❌ 미설치'}")
    out.append("-" * 84)
    if rep.required_ok:
        out.append("✅ 필수 의존성 충족")
    else:
        out.append("🛑 필수 의존성 누락 — 설치 후 다시 실행하세요:")
        out += [f"   {l}" for l in required_guidance(rep)]
    if not rep.platform_ok:
        out += platform_banner_lines(rep)
    out.append("=" * 84)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Opt-in platform skill installation (explicit --install-platform-skills only)
# ---------------------------------------------------------------------------
def _download_tarball(url: str = GOOGLE_SKILLS_TARBALL_URL, timeout: int = 60) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — fixed https URL
        return resp.read()


def extract_cloud_skills(tar_bytes: bytes, names: list[str], dest: Path) -> list[str]:
    """Extracts only ``skills/cloud/<name>/`` folders from a google/skills tarball into ``dest/<name>``."""
    dest.mkdir(parents=True, exist_ok=True)
    wanted = set(names)
    installed: set[str] = set()
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts
            # <repo-root>/skills/cloud/<name>/...
            if len(parts) < 4 or parts[1:3] != ("skills", "cloud") or parts[3] not in wanted:
                continue
            rel = Path(*parts[3:])
            if ".." in rel.parts or rel.is_absolute():
                continue
            target = dest / rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tar.extractfile(member)
                if src is not None:
                    target.write_bytes(src.read())
                    installed.add(parts[3])
    return sorted(n for n in installed if (dest / n / "SKILL.md").is_file())


def install_platform_skills(
    names: list[str],
    skills_dir: str | Path | None = None,
    use_npx: bool | None = None,
    downloader: Callable[[], bytes] | None = None,
    runner: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Installs the given platform skills. Tries `npx skills add` (non-interactive) then the GitHub tarball."""
    result: dict[str, Any] = {"requested": list(names), "installed": [], "method": None, "errors": []}
    if not names:
        result["method"] = "none"
        return result
    dest = Path(skills_dir).expanduser() if skills_dir else DEFAULT_INSTALL_DIR
    npx = shutil.which("npx") if use_npx is not False else None
    if npx and skills_dir is None:
        cmd = [npx, "-y", "skills", "add", GOOGLE_SKILLS_REPO]
        for n in names:
            cmd += ["--skill", n]
        cmd += ["-g", "-y"]
        try:
            proc = (runner or subprocess.run)(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode == 0:
                result.update(method="npx", installed=list(names), command=" ".join(cmd))
                return result
            result["errors"].append(f"npx skills add 실패 (exit {proc.returncode}) — GitHub 다운로드로 대체")
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"npx skills add 실행 실패: {exc} — GitHub 다운로드로 대체")
    try:
        data = (downloader or _download_tarball)()
    except Exception as exc:  # noqa: BLE001 — offline / proxy
        result["method"] = "github-tarball"
        result["errors"].append(f"google/skills 다운로드 실패 (네트워크 확인): {exc}")
        return result
    result["method"] = "github-tarball"
    result["installed"] = extract_cloud_skills(data, names, dest)
    result["skills_dir"] = str(dest)
    missing = [n for n in names if n not in result["installed"]]
    if missing:
        result["errors"].append(f"google/skills 저장소에서 찾지 못함: {', '.join(missing)}")
    return result


def skip_requested(flag: bool = False) -> bool:
    return flag or os.environ.get(SKIP_ENV, "").strip().lower() in ("1", "true", "yes", "on")
