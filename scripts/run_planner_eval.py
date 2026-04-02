"""Planner evaluation minimal chain: fixtures -> plan_sticker_pack -> save; optional batch anti-template analysis.

Usage:
  # Validate fixtures and brief fields only (no API calls)
  python -m scripts.run_planner_eval --dry-run

  # Run first 3 (requires OPENAI_API_KEY)
  python -m scripts.run_planner_eval --limit 3

  # Specify category and output directory
  python -m scripts.run_planner_eval --categories aesthetic risk_boundary --out output/planner_eval

  # Run anti-template analysis on existing run directory
  python -m scripts.run_planner_eval --analyze-only output/planner_eval/20260330_120000

  # Append a failure sample to failures.jsonl
  python -m scripts.run_planner_eval --record-failure output/planner_eval/run1/aes_cherry_coded \\
      --failure-type false_positive --note "should not proceed but planned fully"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from scripts.script_utils import PROJECT_ROOT

FAILURES_REL = Path("data/planner_eval/failures.jsonl")


def _repo_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)


def load_fixtures(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("items", []))


def main() -> None:
    parser = argparse.ArgumentParser(description="Planner V3 evaluation batch run and analysis")
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=_repo_path("data", "planner_eval", "fixtures.json"),
        help="Path to fixtures.json",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_repo_path("output", "planner_eval"),
        help="Output root directory",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max number of cases to run")
    parser.add_argument(
        "--only-ids",
        nargs="*",
        default=None,
        help="Only run these fixture_ids",
    )
    parser.add_argument(
        "--categories",
        nargs="*",
        default=None,
        help="Only run these categories (e.g. aesthetic unsuitable)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate briefs and fixtures only, no model calls",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Delay between requests in seconds",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.7,
        help="Planner temperature",
    )
    parser.add_argument(
        "--analyze-only",
        type=Path,
        default=None,
        metavar="RUN_DIR",
        help="Only generate batch_anti_template_report.json for existing run directory",
    )
    parser.add_argument(
        "--record-failure",
        type=Path,
        default=None,
        metavar="CASE_DIR",
        help="Record a case directory to failures.jsonl (requires --failure-type)",
    )
    parser.add_argument(
        "--failure-type",
        choices=("false_positive", "false_template", "other"),
        default="other",
        help="Used with --record-failure",
    )
    parser.add_argument("--note", default="", help="Failure sample note")
    args = parser.parse_args()

    if args.analyze_only:
        from src.services.batch.planner_eval_analysis import write_batch_report

        run_dir = args.analyze_only.resolve()
        if not run_dir.is_dir():
            print(f"Error: not a directory: {run_dir}", file=sys.stderr)
            sys.exit(1)
        out = write_batch_report(run_dir)
        print(f"Batch report: {out}")
        return

    if args.record_failure:
        case_dir = args.record_failure.resolve()
        rec = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "case_dir": str(case_dir),
            "failure_type": args.failure_type,
            "note": args.note,
        }
        fail_path = (PROJECT_ROOT / FAILURES_REL).resolve()
        fail_path.parent.mkdir(parents=True, exist_ok=True)
        with open(fail_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"Appended failure record to {fail_path}")
        return

    from src.services.batch.planner_plan import plan_sticker_pack
    from src.services.batch.planner_rubric import empty_score_record
    from src.services.batch.trend_brief_schema import validate_trend_brief
    from src.services.batch.planner_eval_analysis import write_batch_report
    from src.services.ai.openai_service import OpenAIService

    fx_path = args.fixtures.resolve()
    if not fx_path.exists():
        print(f"Error: fixtures not found: {fx_path}", file=sys.stderr)
        sys.exit(1)

    items = load_fixtures(fx_path)
    if args.only_ids:
        only = set(args.only_ids)
        items = [x for x in items if x.get("fixture_id") in only]
    if args.categories:
        cat = set(args.categories)
        items = [x for x in items if x.get("category") in cat]
    if args.limit is not None:
        items = items[: args.limit]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = (args.out.resolve() / ts)
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "started_at": ts,
        "fixtures_file": str(fx_path),
        "dry_run": args.dry_run,
        "cases": [],
    }

    openai_svc = None
    if not args.dry_run:
        try:
            openai_svc = OpenAIService()
        except Exception as e:
            print(f"Error: failed to initialize OpenAI: {e}", file=sys.stderr)
            sys.exit(1)

    for item in items:
        fid = item["fixture_id"]
        brief = item["brief"]
        category = item.get("category", "")
        errors, warnings = validate_trend_brief(brief)

        case_dir = run_dir / fid
        case_dir.mkdir(parents=True, exist_ok=True)
        (case_dir / "brief.json").write_text(
            json.dumps(
                {"fixture_id": fid, "category": category, "brief": brief},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (case_dir / "validation.json").write_text(
            json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        score_stub = empty_score_record()
        score_stub["fixture_id"] = fid
        score_stub["category"] = category
        (case_dir / "scores.json").write_text(
            json.dumps(score_stub, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        row = {
            "fixture_id": fid,
            "category": category,
            "validation_errors": errors,
            "validation_warnings": warnings,
            "planning_saved": False,
        }

        if errors:
            row["skipped_reason"] = "validation_errors"
            manifest["cases"].append(row)
            continue

        if args.dry_run:
            manifest["cases"].append(row)
            continue

        assert openai_svc is not None
        try:
            result = plan_sticker_pack(
                brief,
                openai_service=openai_svc,
                temperature=args.temperature,
            )
            text = result.get("text") or ""
            (case_dir / "planning.md").write_text(text, encoding="utf-8")
            usage = result.get("usage")
            (case_dir / "usage.json").write_text(
                json.dumps(usage, ensure_ascii=False, indent=2) if usage else "{}",
                encoding="utf-8",
            )
            row["planning_saved"] = True
            row["chars"] = len(text)
        except Exception as e:
            row["error"] = str(e)

        manifest["cases"].append(row)
        if args.sleep > 0:
            time.sleep(args.sleep)

    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    # Copy blank scoring template to run root for reference
    blank = _repo_path("data", "planner_eval", "scores_blank.json")
    if blank.exists():
        (run_dir / "scores_blank_reference.json").write_text(
            blank.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    if not args.dry_run and any(
        (run_dir / fid / "planning.md").exists() for fid in [c["fixture_id"] for c in manifest["cases"]]
    ):
        try:
            rep = write_batch_report(run_dir)
            print(f"Batch anti-template report: {rep}")
        except Exception as e:
            print(f"Warning: batch analysis failed: {e}", file=sys.stderr)

    print(f"Run directory: {run_dir}")
    print(f"Manifest: {run_dir / 'manifest.json'}")


if __name__ == "__main__":
    main()
