"""Command-Line Interface (CLI) for the Production-Readiness Workbench."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from prod_ready.checklist import CustomParams
from prod_ready.patcher import (
    apply_patches,
    generate_html_report,
    generate_readiness_report,
    preview_patches,
    reset_project_to_baseline,
)
from prod_ready.scanner import ProjectScanReport, scan_project


def _parse_items(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [x.strip() for x in raw.split(",") if x.strip()]
    return items or None


def _parse_params(raw: str | None) -> CustomParams | None:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("@"):
        text = Path(text[1:]).read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("--params must be a JSON object (e.g. '{\"min_instances\": 2}')")
    return CustomParams.from_dict(data)


def _print_report(report: ProjectScanReport) -> None:
    print("=" * 84)
    print(f"🛡️  Production-Readiness Scan Report — {report.project_name}")
    print("=" * 84)
    print(f"  Project Directory : {report.project_dir}")
    print(f"  Detected Mode     : {report.project_mode}")
    print(f"  Deployment Target : {report.deployment_target}")
    print(f"  Weighted Score    : {report.weighted_score} / 100.0")
    print(f"  Readiness Verdict : {report.verdict}")
    print(
        f"  Item Breakdown    : 🟢 {report.green_count} Green "
        f"(Managed-Auto: {report.managed_native_count}) | "
        f"🟡 {report.amber_count} Amber | 🔴 {report.red_count} Red"
    )
    if report.skipped_item_ids:
        print(f"  Skipped (N/A)     : {', '.join(report.skipped_item_ids)}")
    print("-" * 84)
    for d in report.domains:
        print(
            f"  {d.domain_name:<22} (Weight {d.weight}x) : {d.score:>5.1f}%  "
            f"[🟢 {d.green_count}  🟡 {d.amber_count}  🔴 {d.red_count}]"
        )
    print("-" * 84)
    for item in report.items:
        badge = (
            "🟢 AUTO "
            if item.managed_by_platform
            else ("🟢 GREEN" if item.status == "GREEN" else ("🟡 AMBER" if item.status == "AMBER" else "🔴 RED  "))
        )
        print(f"  {badge} [{item.id}/{item.code_alias}] {item.title}")
        print(f"           └─ {item.evidence}")
    if report.advisories:
        print("-" * 84)
        print("  📋 Non-Scoring Advisories (사용자 직접 확인 / 부하 테스트 후 조정):")
        for adv in report.advisories:
            print(f"     • {adv}")
    print("=" * 84)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prod-ready",
        description="7-Domain, 15-Item Production-Readiness Scanner, Patcher & Workbench (v2.1) for Google Cloud AI Agents",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scan
    p_scan = sub.add_parser("scan", help="Scan a local ADK / agents-cli project directory")
    p_scan.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_scan.add_argument("--target", choices=["auto", "cloud_run", "agent_runtime"], default="auto")
    p_scan.add_argument("--items", default="ALL", help="Comma-separated item IDs to evaluate (e.g. C1.1,C4.1 or ALL)")
    p_scan.add_argument("--json", action="store_true", help="Output JSON report")

    # diff
    p_diff = sub.add_parser("diff", help="Preview unified diffs for production hardening")
    p_diff.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_diff.add_argument("--target", choices=["auto", "cloud_run", "agent_runtime"], default="auto")
    p_diff.add_argument("--items", default="ALL", help="Comma-separated item IDs (e.g. C1.2,C5.2 or ALL)")
    p_diff.add_argument("--params", default=None, help="JSON object (or @file.json) of CustomParams overrides")

    # apply
    p_apply = sub.add_parser("apply", help="Apply production hardening patches in-place")
    p_apply.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_apply.add_argument("--target", choices=["auto", "cloud_run", "agent_runtime"], default="auto")
    p_apply.add_argument("--items", default="ALL", help="Comma-separated item IDs (e.g. C1.2,C5.2 or ALL)")
    p_apply.add_argument("--params", default=None, help="JSON object (or @file.json) of CustomParams overrides")

    # report
    p_report = sub.add_parser("report", help="Export Enterprise Security Review Report (.md or .html)")
    p_report.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_report.add_argument("--target", choices=["auto", "cloud_run", "agent_runtime"], default="auto")
    p_report.add_argument("--items", default="ALL", help="Comma-separated item IDs to include (e.g. C1.1,C4.1 or ALL)")
    p_report.add_argument("--format", choices=["md", "html", "both"], default="both", help="Report format (md, html, or both)")
    p_report.add_argument("--output", "-o", default="READINESS_REPORT.md", help="Output path")

    # reset
    p_reset = sub.add_parser("reset", help="Reset a sample project back to its unhardened baseline for demos")
    p_reset.add_argument("project_dir", nargs="?", default="samples/02_cloud_run_agent", help="Path to local agent project")
    p_reset.add_argument("--target", choices=["auto", "cloud_run", "agent_runtime"], default="auto")

    # demo
    p_demo = sub.add_parser("demo", help="Run the 3-stage interactive Before-vs-After live demo showcase")
    p_demo.add_argument("project_dir", nargs="?", default="samples/02_cloud_run_agent", help="Path to local agent project")
    p_demo.add_argument("--target", choices=["auto", "cloud_run", "agent_runtime"], default="auto")
    p_demo.add_argument("--reset-after", action="store_true", help="Reset project back to unhardened state after demo")

    # ui
    p_ui = sub.add_parser("ui", help="Launch the interactive localhost Workbench UI")
    p_ui.add_argument("project_dir", nargs="?", default=".", help="Path to local agent project")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8090)

    args = parser.parse_args(argv)

    if args.command == "scan":
        items = _parse_items(args.items)
        report = scan_project(Path(args.project_dir), target_override=args.target, selected_items=items)
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_report(report)
        return 0

    if args.command == "diff":
        items = _parse_items(args.items)
        custom_params = _parse_params(args.params)
        patches = preview_patches(
            Path(args.project_dir),
            target_override=args.target,
            selected_items=items,
            custom_params=custom_params,
        )
        if not patches:
            print("No diffs required — all selected checklist items are already satisfied.")
            return 0
        for p in patches:
            tag = "NEW FILE" if p.is_new else "PATCH"
            print(f"# === [{tag}] {p.relative_path} (Items: {', '.join(p.applied_items)}) ===")
            print(p.unified_diff)
        return 0

    if args.command == "apply":
        items = _parse_items(args.items)
        custom_params = _parse_params(args.params)
        res = apply_patches(
            Path(args.project_dir),
            target_override=args.target,
            selected_items=items,
            custom_params=custom_params,
        )
        print(
            f"✅ Applied {res['applied_file_count']} file patches! "
            f"Readiness Score: {res['before']['weighted_score']} -> {res['after']['weighted_score']} "
            f"({res['after']['verdict']})"
        )
        return 0

    if args.command == "report":
        proj = Path(args.project_dir)
        out_path = Path(args.output)
        items = _parse_items(args.items)
        if args.format == "html" or out_path.suffix.lower() == ".html":
            html_path = out_path if out_path.suffix.lower() == ".html" else out_path.with_suffix(".html")
            html_path.write_text(
                generate_html_report(proj, target_override=args.target, selected_items=items),
                encoding="utf-8",
            )
            print(f"📊 Exported Visual HTML Dashboard Report to {html_path.resolve()}")
        elif args.format == "md":
            out_path.write_text(
                generate_readiness_report(proj, target_override=args.target, selected_items=items),
                encoding="utf-8",
            )
            print(f"📄 Exported Markdown Security Review Report to {out_path.resolve()}")
        else:
            md_path = out_path if out_path.suffix.lower() == ".md" else out_path.with_suffix(".md")
            html_path = md_path.with_suffix(".html")
            md_path.write_text(
                generate_readiness_report(proj, target_override=args.target, selected_items=items),
                encoding="utf-8",
            )
            html_path.write_text(
                generate_html_report(proj, target_override=args.target, selected_items=items),
                encoding="utf-8",
            )
            print(f"📄 Exported Markdown Report : {md_path.resolve()}")
            print(f"📊 Exported HTML Dashboard  : {html_path.resolve()}")
        return 0

    if args.command == "reset":
        res = reset_project_to_baseline(Path(args.project_dir), target_override=args.target)
        rep = res["report"]
        print(f"🔄 Reset {res['project_dir']} to baseline -> Score: {rep['weighted_score']} / 100.0 ({rep['verdict']})")
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
        print(f"  • 준비도 점수   : {before_rep.weighted_score} / 100.0  -->  {before_rep.verdict}")
        print(f"  • 항목 요약     : 🟢 {before_rep.green_count} 통과 | 🟡 {before_rep.amber_count} 미흡 | 🔴 {before_rep.red_count} 취약")

        print("\n" + "━" * 86)
        print("✨ [STAGE 2 / 3] 7대 영역 · 15개 핵심 프로덕션 & 거버넌스 하드닝 패치 원클릭 적용 (`prod-ready apply`)")
        print("━" * 86)
        patch_res = apply_patches(proj, target_override=args.target, selected_items=["ALL"])
        for p in patch_res["patches"]:
            mode_tag = "NEW  " if p["is_new"] else "PATCH"
            print(f"  ✅ [{mode_tag}] {p['relative_path']:<42} (항목: {', '.join(p['applied_items'])})")
        after_score = patch_res["after"]["weighted_score"]
        after_verdict = patch_res["after"]["verdict"]
        print(f"\n  🎯 점수 변화: {before_rep.weighted_score} / 100.0  ===>  {after_score} / 100.0 ({after_verdict})")

        print("\n" + "━" * 86)
        print("🛡️  [STAGE 3 / 3] 패치 후(AFTER) 최종 재검증(`scan`) & 시각적 심의 성적표 발급(`report`)")
        print("━" * 86)
        after_rep = scan_project(proj, target_override=args.target)
        print(f"  • 최종 점수     : {after_rep.weighted_score} / 100.0  -->  {after_rep.verdict}")
        print(f"  • 항목 요약     : 🟢 {after_rep.green_count} 통과 | 🟡 {after_rep.amber_count} 미흡 | 🔴 {after_rep.red_count} 취약")

        md_out = proj / "READINESS_REPORT.md"
        html_out = proj / "READINESS_REPORT.html"
        md_out.write_text(generate_readiness_report(proj, target_override=args.target), encoding="utf-8")
        html_out.write_text(generate_html_report(proj, target_override=args.target), encoding="utf-8")
        print("\n  📦 생성된 결과물 (브라우저 또는 에디터로 즉시 확인 가능):")
        print(f"     1. 시각적 HTML 심의 성적표 : {html_out}")
        print(f"     2. 마크다운 심의 리포트    : {md_out}")
        print("     3. 웹 워크벤치 UI 실행     : uv run prod-ready ui " + str(args.project_dir) + " --port 8090")
        print("━" * 86 + "\n")

        if args.reset_after:
            reset_project_to_baseline(proj, target_override=args.target)
        return 0

    if args.command == "ui":
        import uvicorn
        from prod_ready.server import create_workbench_app

        app = create_workbench_app(Path(args.project_dir))
        print(f"🚀 Production-Readiness Workbench running at http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
