"""Command-Line Interface (CLI) for the Production-Readiness Workbench."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prod_ready.checklist import (
    DELEGATED_LEGACY_NOTES,
    OUT_OF_SCOPE_LEGACY_NOTES,
    CustomParams,
    platform_skills_for,
    resolve_item_selection,
)
from prod_ready.patcher import (
    apply_patches,
    cicd_guidance_notices,
    generate_html_report,
    generate_readiness_report,
    preview_patches,
    reset_project_to_baseline,
    select_patch_items,
)
from prod_ready.scanner import ProjectScanReport, detect_project_mode_and_target, scan_project
from prod_ready import deps as deps_mod

_ITEMS_HELP = (
    "Comma-separated v3 item IDs (e.g. CR-F1,CR-S2 · AR-S3 · GKE-O1) or ALL. "
    "Legacy v2 ids (C2.1, C4.4, ...) are accepted as aliases; delegated ids (C1.1, C3.1, C4.3, C5.3) map to platform skills; "
    "C6.2 (Gemini retry · endpoint) is ADK optimization (agent code) — out of scope."
)


def _parse_items(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or None


_CONDITIONS_HELP = (
    "Workload condition overrides, e.g. 'idle=true,memory=false' (keys: conv, memory, code, tools, idle, delegated). "
    "`idle` (자주 일시정지·재개하는 상태 유지형 에이전트 → GKE-O2) cannot be detected from code: opt in with idle=true. "
    "`delegated` (사용자 대신 외부 도구 호출 → AR-S5 · CR-S6 · GKE-S5) is auto-detected from tool credentials; "
    "opt in with delegated=true when undetectable."
)


def _parse_conditions(raw: str | None) -> dict[str, bool] | None:
    if not raw:
        return None
    out: dict[str, bool] = {}
    for part in raw.split(","):
        if not part.strip():
            continue
        key, _, val = part.partition("=")
        out[key.strip().lower()] = val.strip().lower() in ("", "1", "true", "yes", "y", "on")
    return out or None


def _parse_params(raw: str | None) -> CustomParams | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("@"):
        text = Path(text[1:]).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("--params must be a JSON object (e.g. '{\"gke_execution_pattern\": \"agent_sandbox\", \"gke_enable_gvisor\": true}')")
    return CustomParams.from_dict(data)


def _notify_selection(project_dir: Path, target_override: str, items: list[str] | None) -> None:
    """Prints legacy-alias resolution and delegated/unknown id notices to stderr."""
    if not items:
        return
    _, target, _ = detect_project_mode_and_target(project_dir.resolve(), target_override)
    sel = resolve_item_selection(items, target=target)
    for legacy, mapped in sel.legacy_resolved.items():
        print(f"ℹ️  {legacy} → {', '.join(mapped)} (v3 별칭)", file=sys.stderr)
    if sel.delegated_legacy:
        skills = ", ".join(s["name"] for s in platform_skills_for(target))
        for legacy in sel.delegated_legacy:
            note = DELEGATED_LEGACY_NOTES.get(legacy.upper(), "일반 플랫폼 모범 사례")
            print(
                f"ℹ️  {legacy} ({note})는 v3에서 플랫폼 Skill(google/skills)로 위임되어 점검·패치 대상이 아닙니다. "
                f"참조: {skills}",
                file=sys.stderr,
            )
    for legacy in sel.out_of_scope_legacy:
        canonical = legacy.upper()
        note = OUT_OF_SCOPE_LEGACY_NOTES.get(canonical) or next(iter(OUT_OF_SCOPE_LEGACY_NOTES.values()))
        print(
            f"ℹ️  {legacy} ({note})는 ADK 최적화(에이전트 코드) 영역이라 범위 밖입니다 — 점검·패치하지 않습니다.",
            file=sys.stderr,
        )
    if sel.unknown:
        print(f"⚠️  알 수 없는 항목 ID: {', '.join(sel.unknown)} ({target})", file=sys.stderr)


def _print_notices(notices: list[str]) -> None:
    for note in notices:
        print(f"ℹ️  {note}", file=sys.stderr)


def _cicd_notices(
    project_dir: Path, target_override: str, items: list[str] | None, conditions: dict[str, bool] | None = None
) -> list[str]:
    root = project_dir.resolve()
    _, target, _ = detect_project_mode_and_target(root, target_override)
    return cicd_guidance_notices(root, select_patch_items(root, target, items, conditions))


def _preflight(project_dir: Path, target_override: str, skip: bool) -> tuple[int | None, object | None]:
    """Dependency preflight before scan/diff/apply/report.

    Returns (exit_code, deps). exit_code is 1 when required deps are missing (unless skipped).
    Missing platform skills never block — a prominent banner is printed to stderr instead.
    """
    if deps_mod.skip_requested(skip):
        return None, None
    _, target, _ = detect_project_mode_and_target(project_dir.resolve(), target_override)
    rep = deps_mod.check_dependencies(target, project_dir)
    if not rep.required_ok:
        print("🛑 필수 의존성이 없어 중단합니다 (설치 후 다시 실행하거나 --skip-deps-check):", file=sys.stderr)
        for line in deps_mod.required_guidance(rep):
            print(f"   {line}", file=sys.stderr)
        print(f"   자세히: prod-ready doctor --target {target}", file=sys.stderr)
        return 1, rep
    for line in deps_mod.platform_banner_lines(rep):
        print(line, file=sys.stderr)
    return None, rep


def _run_doctor(args: argparse.Namespace) -> int:
    target = None if args.target in (None, "auto", "all") else args.target
    rep = deps_mod.check_dependencies(target, Path(args.project_dir))
    if args.json:
        print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(deps_mod.format_doctor_table(rep))
    if args.install_platform_skills:
        if rep.platform_missing:
            print(f"📦 플랫폼 Skill 설치 (사용자 요청): {', '.join(rep.platform_missing)}")
            res = deps_mod.install_platform_skills(rep.platform_missing, skills_dir=args.skills_dir)
            for err in res.get("errors", []):
                print(f"⚠️  {err}", file=sys.stderr)
            if res.get("installed"):
                where = res.get("skills_dir") or "npx skills 기본 위치"
                print(f"✅ 설치 완료 ({res['method']}): {', '.join(res['installed'])} → {where}")
            rep = deps_mod.check_dependencies(target, Path(args.project_dir))
            extra = [Path(res["skills_dir"])] if res.get("skills_dir") else []
            still = [n for n in rep.platform_missing if not deps_mod.find_skill(n, extra)]
            outside = [n for n in rep.platform_missing if n not in still]
            if outside:
                print(
                    f"ℹ️  {', '.join(outside)}: {extra[0]}에 설치됨 — 코딩 에이전트의 Skill 경로가 아니라면 해당 경로로 옮기거나 "
                    f"PROD_READY_SKILL_DIRS에 추가하세요."
                )
            print("✅ 플랫폼 Skill 설치 완료" if not still else f"⚠️  아직 미설치: {', '.join(still)}")
        else:
            print("✅ 설치할 플랫폼 Skill이 없습니다 (모두 설치됨)")
    return 0 if rep.required_ok else 1


def _print_report(report: ProjectScanReport) -> None:
    print("=" * 84)
    print(f"🛡️  Production-Readiness Scan Report — {report.project_name}")
    print("=" * 84)
    print(f"  Project Directory : {report.project_dir}")
    print(f"  Detected Mode     : {report.project_mode}")
    print(f"  Deployment Target : {report.deployment_target}")
    print(f"  Readiness Verdict : {report.verdict}")
    print(
        f"  Compliance Status : 🟢 {report.green_count} / {report.total_count} 충족 | "
        f"🟡 {report.amber_count} 보완 | 🔴 {report.red_count} 미충족 | ⚪ N/A {report.na_count} "
        f"(Checklist {report.catalog_version}, Preview 미충족 {report.preview_open_count}개는 판정 제외)"
    )
    if report.skipped_item_ids:
        print(f"  Not Selected      : {', '.join(report.skipped_item_ids)}")
    print("-" * 84)
    print("  🧭 워크로드 조건: " + " · ".join(f"{c.label} {'✅' if c.met else '—'}" for c in report.conditions))
    print("-" * 84)
    for d in report.domains:
        if d.total_count == 0 and d.na_count == 0:
            continue  # skip categories with no item for this target (e.g. Optimize on Cloud Run / Agent Runtime)
        d_icon = (
            "⚪" if d.total_count == 0 else ("🟢" if d.red_count == 0 and d.amber_count == 0 else ("🟡" if d.red_count == 0 else "🔴"))
        )
        print(
            f"  {d_icon} {d.domain_name:<26} : {d.green_count:>2}/{d.total_count:<2} 충족  "
            f"[🟢 {d.green_count}  🟡 {d.amber_count}  🔴 {d.red_count}  ⚪ {d.na_count}]"
        )
    print("-" * 84)
    badges = {"GREEN": "🟢 GREEN", "AMBER": "🟡 AMBER", "RED": "🔴 RED  ", "NA": "⚪ N/A  "}
    for item in report.items:
        badge = badges.get(item.status, item.status)
        tags = []
        if item.grade == "conditional":
            tags.append(f"조건부:{item.condition_label}")
        if item.preview:
            tags.append("Preview")
        tag_str = f" ({' · '.join(tags)})" if tags else ""
        print(f"  {badge} [{item.id}] {item.title}{tag_str}")
        print(f"           └─ {item.evidence}")
    if report.advisories:
        print("-" * 84)
        print("  📋 권고 사항 (값 · 아키텍처 · 유료 선택형 — 점수화하지 않음):")
        for adv in report.advisories:
            print(f"     • {adv}")
    if report.platform_skills:
        print("-" * 84)
        print("  🔗 플랫폼 Skill 참조 권장 (일반 플랫폼 모범 사례 위임):")
        for sk in report.platform_skills:
            print(f"     • {sk['name']}: {sk['url']}")
    print("=" * 84)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prod-ready",
        description="AI Agent Production-Readiness Scanner, Patcher & Workbench (Checklist v3 — Agent Runtime 10 · Cloud Run 11 · GKE 12)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan a local ADK / agents-cli project directory")
    p_scan.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_scan.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")
    p_scan.add_argument("--items", default="ALL", help=_ITEMS_HELP)
    p_scan.add_argument("--conditions", default=None, help=_CONDITIONS_HELP)
    p_scan.add_argument("--skip-deps-check", action="store_true", help="Skip the dependency preflight (agents-cli · agents-cli skills · platform skills)")
    p_scan.add_argument("--json", action="store_true", help="Output JSON report")

    # diff
    p_diff = sub.add_parser("diff", help="Preview unified diffs for the selected checklist items")
    p_diff.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_diff.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")
    p_diff.add_argument("--items", default="ALL", help=_ITEMS_HELP + " ALL skips Preview / N/A conditional items.")
    p_diff.add_argument("--conditions", default=None, help=_CONDITIONS_HELP)
    p_diff.add_argument("--skip-deps-check", action="store_true", help="Skip the dependency preflight (agents-cli · agents-cli skills · platform skills)")
    p_diff.add_argument("--params", default=None, help="JSON object (or @file.json) of CustomParams overrides")

    # apply
    p_apply = sub.add_parser("apply", help="Apply checklist patches in-place")
    p_apply.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_apply.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")
    p_apply.add_argument("--items", default="ALL", help=_ITEMS_HELP + " ALL skips Preview / N/A conditional items.")
    p_apply.add_argument("--conditions", default=None, help=_CONDITIONS_HELP)
    p_apply.add_argument("--skip-deps-check", action="store_true", help="Skip the dependency preflight (agents-cli · agents-cli skills · platform skills)")
    p_apply.add_argument("--params", default=None, help="JSON object (or @file.json) of CustomParams overrides")

    # report
    p_report = sub.add_parser("report", help="Export the readiness report (.md or .html)")
    p_report.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_report.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")
    p_report.add_argument("--items", default="ALL", help=_ITEMS_HELP)
    p_report.add_argument("--conditions", default=None, help=_CONDITIONS_HELP)
    p_report.add_argument("--skip-deps-check", action="store_true", help="Skip the dependency preflight (agents-cli · agents-cli skills · platform skills)")
    p_report.add_argument("--format", choices=["md", "html", "both"], default="both", help="Report format (md, html, or both)")
    p_report.add_argument("--output", "-o", default="READINESS_REPORT.md", help="Output path")

    # doctor
    p_doc = sub.add_parser("doctor", help="Check dependencies: agents-cli (>=1.5.0) + agents-cli skills (required), platform skills (recommended)")
    p_doc.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project (also searches <project>/.agents/skills)")
    p_doc.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")
    p_doc.add_argument("--install-platform-skills", action="store_true", help="Install missing platform skills (explicit consent only)")
    p_doc.add_argument("--skills-dir", default=None, help="Install destination for platform skills (default: ~/.agents/skills; forces GitHub download)")
    p_doc.add_argument("--json", action="store_true", help="Output JSON")

    # reset
    p_reset = sub.add_parser("reset", help="Reset a sample project back to its baseline for demos")
    p_reset.add_argument("project_dir", nargs="?", default="samples/02_cloud_run_agent", help="Path to local agent project")
    p_reset.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")

    # demo
    p_demo = sub.add_parser("demo", help="Run the 3-stage interactive Before-vs-After live demo showcase")
    p_demo.add_argument("project_dir", nargs="?", default="samples/02_cloud_run_agent", help="Path to local agent project")
    p_demo.add_argument("--target", choices=["auto", "agent_runtime", "cloud_run", "gke"], default="auto")
    p_demo.add_argument("--reset-after", action="store_true", help="Reset project back to its baseline after demo")

    # ui
    p_ui = sub.add_parser("ui", help="Launch the interactive localhost Workbench UI")
    p_ui.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8090)

    args = parser.parse_args(argv)

    if args.command == "doctor":
        if args.target == "auto":
            _, args.target, _ = detect_project_mode_and_target(Path(args.project_dir).resolve(), "auto")
        return _run_doctor(args)

    if args.command in ("scan", "diff", "apply", "report"):
        code, _deps = _preflight(Path(args.project_dir), args.target, args.skip_deps_check)
        if code is not None:
            return code

    if args.command == "scan":
        items = _parse_items(args.items)
        _notify_selection(Path(args.project_dir), args.target, items)
        report = scan_project(
            Path(args.project_dir), target_override=args.target, selected_items=items,
            conditions=_parse_conditions(args.conditions),
        )
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_report(report)
        return 0

    if args.command == "diff":
        items = _parse_items(args.items)
        _notify_selection(Path(args.project_dir), args.target, items)
        custom_params = _parse_params(args.params)
        patches = preview_patches(
            Path(args.project_dir),
            target_override=args.target,
            selected_items=items,
            custom_params=custom_params,
            conditions=_parse_conditions(args.conditions),
        )
        _print_notices(_cicd_notices(Path(args.project_dir), args.target, items, _parse_conditions(args.conditions)))
        if not patches:
            print("No diffs generated for the selected items — they are either already satisfied, "
                  "not applicable (condition not met), or guidance-only (no automatic patch; "
                  "see `prod-ready scan` / the report for how to fix them).")
            return 0
        for p in patches:
            tag = "NEW FILE" if p.is_new else "PATCH"
            print(f"# === [{tag}] {p.relative_path} (Items: {', '.join(p.applied_items)}) ===")
            print(p.unified_diff)
        return 0

    if args.command == "apply":
        items = _parse_items(args.items)
        _notify_selection(Path(args.project_dir), args.target, items)
        custom_params = _parse_params(args.params)
        res = apply_patches(
            Path(args.project_dir),
            target_override=args.target,
            selected_items=items,
            custom_params=custom_params,
            conditions=_parse_conditions(args.conditions),
        )
        print(
            f"✅ Applied {res['applied_file_count']} file patches! "
            f"Compliance: 🟢 {res['before']['green_count']}/{res['before']['total_count']} -> "
            f"{res['after']['green_count']}/{res['after']['total_count']} "
            f"({res['after']['verdict']})"
        )
        _print_notices(res.get("notices", []))
        return 0

    if args.command == "report":
        proj = Path(args.project_dir)
        out_path = Path(args.output)
        items = _parse_items(args.items)
        if args.format == "html" or out_path.suffix.lower() == ".html":
            html_path = out_path if out_path.suffix.lower() == ".html" else out_path.with_suffix(".html")
            html_path.write_text(
                generate_html_report(proj, target_override=args.target, selected_items=items, conditions=_parse_conditions(args.conditions)),
                encoding="utf-8",
            )
            print(f"📊 Exported Visual HTML Dashboard Report to {html_path.resolve()}")
        elif args.format == "md":
            out_path.write_text(
                generate_readiness_report(proj, target_override=args.target, selected_items=items, conditions=_parse_conditions(args.conditions)),
                encoding="utf-8",
            )
            print(f"📄 Exported Markdown Security Review Report to {out_path.resolve()}")
        else:
            md_path = out_path if out_path.suffix.lower() == ".md" else out_path.with_suffix(".md")
            html_path = md_path.with_suffix(".html")
            md_path.write_text(
                generate_readiness_report(proj, target_override=args.target, selected_items=items, conditions=_parse_conditions(args.conditions)),
                encoding="utf-8",
            )
            html_path.write_text(
                generate_html_report(proj, target_override=args.target, selected_items=items, conditions=_parse_conditions(args.conditions)),
                encoding="utf-8",
            )
            print(f"📄 Exported Markdown Report : {md_path.resolve()}")
            print(f"📊 Exported HTML Dashboard  : {html_path.resolve()}")
        return 0

    if args.command == "reset":
        res = reset_project_to_baseline(Path(args.project_dir), target_override=args.target)
        rep = res["report"]
        print(
            f"🔄 Reset {res['project_dir']} to baseline -> "
            f"🟢 {rep['green_count']}/{rep['total_count']} Passed | "
            f"🟡 {rep['amber_count']} Amber | 🔴 {rep['red_count']} Red ({rep['verdict']})"
        )
        return 0

    if args.command == "demo":
        proj = Path(args.project_dir).resolve()
        reset_project_to_baseline(proj, target_override=args.target)
        before_rep = scan_project(proj, target_override=args.target)

        print("\n" + "━" * 86)
        print("🎬 [STAGE 1 / 3] 패치 전(BEFORE) 기본 에이전트 상태 스캔 (`agents-cli` 기본값)")
        print("━" * 86)
        print(f"  • 대상 프로젝트 : {proj}")
        print(f"  • 배포 타겟     : {before_rep.deployment_target}")
        print(f"  • 게이트 판정   : {before_rep.verdict}")
        print(
            f"  • 항목 충족     : 🟢 {before_rep.green_count}/{before_rep.total_count} 충족 "
            f"| 🟡 {before_rep.amber_count} 보완 | 🔴 {before_rep.red_count} 미충족"
        )

        print("\n" + "━" * 86)
        print(f"✨ [STAGE 2 / 3] {before_rep.deployment_target} Checklist v3 항목 일괄 적용 (`prod-ready apply` · Preview·N/A 항목 제외)")
        print("━" * 86)
        patch_res = apply_patches(proj, target_override=args.target, selected_items=["ALL"])
        for p in patch_res["patches"]:
            mode_tag = "NEW  " if p["is_new"] else "PATCH"
            print(f"  ✅ [{mode_tag}] {p['relative_path']:<42} (항목: {', '.join(p['applied_items'])})")
        after_green = patch_res["after"]["green_count"]
        after_total = patch_res["after"]["total_count"]
        after_verdict = patch_res["after"]["verdict"]
        print(
            f"\n  🎯 충족 변화: 🟢 {before_rep.green_count}/{before_rep.total_count}  ===>  "
            f"🟢 {after_green}/{after_total} ({after_verdict})"
        )

        print("\n" + "━" * 86)
        print("🛡️  [STAGE 3 / 3] 패치 후(AFTER) 최종 재검증(`scan`) & 시각적 점검 리포트 발급(`report`)")
        print("━" * 86)
        after_rep = scan_project(proj, target_override=args.target)
        print(f"  • 최종 판정     : {after_rep.verdict}")
        print(
            f"  • 항목 충족     : 🟢 {after_rep.green_count}/{after_rep.total_count} 충족 "
            f"| 🟡 {after_rep.amber_count} 보완 | 🔴 {after_rep.red_count} 미충족"
        )

        md_out = proj / "READINESS_REPORT.md"
        html_out = proj / "READINESS_REPORT.html"
        md_out.write_text(generate_readiness_report(proj, target_override=args.target), encoding="utf-8")
        html_out.write_text(generate_html_report(proj, target_override=args.target), encoding="utf-8")
        print("\n  📦 생성된 결과물 (브라우저 또는 에디터로 즉시 확인 가능):")
        print(f"     1. 시각적 HTML 점검 리포트 : {html_out}")
        print(f"     2. 마크다운 점검 리포트    : {md_out}")
        print("     3. 웹 워크벤치 UI 실행     : uv run prod-ready ui " + str(args.project_dir) + " --port 8090")
        print("━" * 86 + "\n")

        if args.reset_after:
            reset_project_to_baseline(proj, target_override=args.target)
        return 0

    if args.command == "ui":
        repo_root = Path(__file__).resolve().parents[4] if len(Path(__file__).resolve().parents) > 4 else None
        if repo_root and (repo_root / "web" / "server.py").is_file() and str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        try:
            import uvicorn
            from web.server import create_workbench_app
        except ImportError as exc:
            print(
                "⚠️  The Web Workbench & Hands-On Lab UI lives separately in the repository's `web/` package "
                "(not bundled inside the standalone Agent Skill).\n"
                "   • Run from the repository root with `uv run prod-ready ui <project_dir>`, OR\n"
                "   • Open the hosted Web Workbench directly in your browser.\n"
                f"   (Import details: {exc})",
                file=sys.stderr,
            )
            return 1

        app = create_workbench_app(Path(args.project_dir))
        print(f"🚀 Production-Readiness Workbench running at http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
