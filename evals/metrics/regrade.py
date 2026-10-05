#!/usr/bin/env python3
"""regrade.py — re-run the deterministic graders (regex, tool_used, tool_order, file_exists) of
finished runs against their saved stream-json traces, after a grader file changed.

LLM graders are not re-run (that would cost money and re-roll the judge); their stored verdicts
are kept. For every run the script reloads the case's current graders, grades the deterministic
ones on the run's trace, recomputes score / passed exactly as the runner does, and rewrites
aggregate-result.json (original kept once as aggregate-result.orig.json). Only the stage-1 trace
of each run is used (the current runner has one session per run).

Usage: python3 evals/metrics/regrade.py RESULTS_DIR...   (prints every verdict that changed)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
_spec = importlib.util.spec_from_file_location("gs_eval_run", os.path.join(ROOT, "evals", "run.py"))
run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run)  # type: ignore[union-attr]

DETERMINISTIC = {"regex", "tool_used", "tool_order", "file_exists"}


def regrade_dir(results_dir: str, threshold: float) -> int:
    agg_path = os.path.join(results_dir, "aggregate-result.json")
    with open(agg_path, encoding="utf-8") as fh:
        agg = json.load(fh)
    eval_dir = agg.get("evalDir") or ""
    if not os.path.isabs(eval_dir):
        eval_dir = os.path.join(ROOT, eval_dir)
    changed = 0
    for case_entry in agg.get("cases") or []:
        base = case_entry.get("baseCase") or case_entry.get("name", "").split("@")[0]
        case_dir = os.path.join(eval_dir, base)
        if not os.path.isdir(case_dir):
            continue
        case = run.Case(case_dir)
        graders = {g.name: g for g in case.graders}
        for arm, runs in (case_entry.get("arms") or {}).items():
            for i, rec in enumerate(runs, 1):
                n = rec.get("run") if isinstance(rec.get("run"), int) else i
                trace_path = os.path.join(results_dir, "runs", case_entry["name"], arm, str(n), "trace.jsonl")
                if not os.path.exists(trace_path):
                    continue
                trace = run.parse_trace_file(trace_path)
                with open(trace_path, encoding="utf-8", errors="replace") as fh:
                    trace_text = fh.read()
                ctx = run.GradeContext(trace, trace_text, None, rec.get("changedFiles") or [], None)
                new_results = []
                for old in rec.get("graders") or []:
                    g = graders.get(old.get("name"))
                    if g is None or g.type not in DETERMINISTIC or g.type == "file_exists":
                        new_results.append(old)
                        continue
                    res = run.grade(g, ctx, arm)
                    if bool(res.get("passed")) != bool(old.get("passed")):
                        changed += 1
                        print(f"  {case_entry['name']}/{arm}/{n}: {g.name} "
                              f"{'PASS' if old.get('passed') else 'FAIL'} -> {'PASS' if res.get('passed') else 'FAIL'}")
                    new_results.append(res)
                rec["graders"] = new_results
                rec["score"] = run.score_graders(new_results)
                rec["passed"] = rec["score"] >= threshold and not rec.get("error")
    orig = os.path.join(results_dir, "aggregate-result.orig.json")
    if not os.path.exists(orig):
        shutil.copyfile(agg_path, orig)
    with open(agg_path, "w", encoding="utf-8") as fh:
        json.dump(agg, fh, indent=2)
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Re-run deterministic graders on saved traces.")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("results", nargs="+", metavar="RESULTS_DIR")
    opts = ap.parse_args(argv)
    total = 0
    for d in opts.results:
        print(d)
        total += regrade_dir(d, opts.threshold)
    print(f"{total} verdict(s) changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
