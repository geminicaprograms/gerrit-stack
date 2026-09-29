#!/usr/bin/env python3
"""collect.py — merge benchmark results into docs/benchmark.md (WP A11).

Usage:
    python3 evals/metrics/collect.py [--results DIR ...] [--out docs/benchmark.md]
                                     [--json PATH]

Reads any number of result directories (default: every ``evals/results/*``
that holds an ``aggregate-result.json`` or a ``runs/`` tree), each laid out as

    <results>/aggregate-result.json                       official eval schema
    <results>/runs/<case>/<arm>/<n>/trace.jsonl           claude stream-json
    <results>/runs/<case>/<arm>/<n>/hook-trace.log        GERRIT_STACK_TRACE
    <results>/runs/<case>/<arm>/<n>/chain-metrics.json    chain-metrics.sh --json

and computes per run: turns, tool_calls, bash_calls, git_commit_calls,
git_push_calls, cost_usd, wall_s, asks, denies, self_corrections (+ score from
the aggregate), merged with the chain metrics. Aggregates per case x arm
(mean / median), per arm overall, deltas ``with - mcp-only`` and
``with - without``, target pass/fail, and renders ``docs/benchmark.md``.
With no runs at all it renders the "no runs yet" placeholder.

Arms: ``with`` (gerrit-mcp + gerrit-stack), ``without`` (no plugins),
``mcp-only`` (gerrit-mcp only). Stdlib only; tolerant of missing fields.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import os
import re
import statistics
import sys

ARMS = ["with", "mcp-only", "without"]
ARM_LABEL = {
    "with": "C: with (gerrit-mcp + gerrit-stack)",
    "mcp-only": "B: mcp-only (gerrit-mcp only)",
    "without": "A: without (no plugins)",
}
ARM_SHORT = {"with": "C with", "mcp-only": "B mcp-only", "without": "A without"}

# (key, label, unit, lower-is-better?) — order = table order
PROCESS_METRICS = [
    ("score", "eval score", "", False),
    ("turns", "turns", "", True),
    ("tool_calls", "tool calls", "", True),
    ("bash_calls", "Bash calls", "", True),
    ("git_commit_calls", "git commit calls", "", True),
    ("git_push_calls", "git push calls", "", True),
    ("asks", "human confirmations (ask)", "", True),
    ("denies", "hook denials", "", True),
    ("self_corrections", "self-corrections after deny", "", False),
    ("cost_usd", "cost", "USD", True),
    ("wall_s", "wall time", "s", True),
]
CHAIN_METRICS = [
    ("chain_length", "chain length", "", None),
    ("lines_median", "lines / change (median)", "", True),
    ("lines_p75", "lines / change (p75)", "", True),
    ("lines_max", "lines / change (max)", "", True),
    ("files_median", "files / change (median)", "", True),
    ("within_budget_pct", "within budget", "%", False),
    ("one_change_id_pct", "exactly one Change-Id", "%", False),
    ("conventional_pct", "Conventional Commit subject", "%", False),
    ("single_concern_pct", "single concern (proxy)", "%", False),
    ("builds_alone_pct", "builds alone", "%", False),
    ("fixups_present", "fixup!/squash! left in chain", "", True),
    ("violations", "rule violations (hook deny)", "", True),
    ("refs_for_pushed", "commits pushed to refs/for", "", None),
]
ALL_METRICS = PROCESS_METRICS + CHAIN_METRICS
METRIC_KEYS = [m[0] for m in ALL_METRICS]

# Targets (execution plan, "Proposal: measuring the efficiency of gerrit-stack")
TARGETS = [
    ("budget compliance", "within_budget_pct", ">=", 90.0, "%"),
    ("exactly one Change-Id", "one_change_id_pct", ">=", 100.0, "%"),
    ("rule violations", "violations", "==", 0.0, ""),
    ("cost overhead vs mcp-only", "cost_overhead_pct", "<=", 30.0, "%"),
]

GIT_VERB_RE = re.compile(r"(?:^|[;&|]\s*|\n\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--git-dir=\S+\s+|--work-tree=\S+\s+)*([a-z][a-z-]*)")
DENY_HINT_RE = re.compile(r"hook|denied|blocked|not allowed|refused", re.IGNORECASE)


# ----------------------------------------------------------------- helpers ----
def _num(value):
    """Return value as float when it is a finite number (bool -> 0/1), else None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def git_verbs(command):
    """All git sub-command verbs found in a shell command string."""
    if not isinstance(command, str):
        return []
    return GIT_VERB_RE.findall(command)


# ------------------------------------------------------------- trace parse ----
def parse_trace(path):
    """Process metrics from a claude ``--output-format stream-json`` trace.

    Returns a dict (all keys present; None when the trace lacks the info) plus
    ``tool_uses`` (list of {id, name, input}) for callers that need detail.
    """
    out = {
        "turns": None, "tool_calls": 0, "bash_calls": 0, "git_commit_calls": 0,
        "git_push_calls": 0, "cost_usd": None, "wall_s": None, "asks_tool": 0,
        "denies_trace": 0, "self_corrections": 0, "usage": None,
    }
    tool_uses = []          # in order: {id, name, input}
    results = {}            # tool_use_id -> {"is_error": bool, "text": str}
    if not os.path.isfile(path):
        out["tool_uses"] = tool_uses
        out["present"] = False
        return out
    out["present"] = True
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            etype = ev.get("type")
            msg = ev.get("message") if isinstance(ev.get("message"), dict) else {}
            content = msg.get("content")
            if etype == "assistant" and isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_use":
                        continue
                    name = item.get("name") or ""
                    inp = item.get("input") if isinstance(item.get("input"), dict) else {}
                    tool_uses.append({"id": item.get("id"), "name": name, "input": inp})
                    out["tool_calls"] += 1
                    if name == "AskUserQuestion":
                        out["asks_tool"] += 1
                    if name == "Bash":
                        out["bash_calls"] += 1
                        verbs = git_verbs(inp.get("command"))
                        if "commit" in verbs:
                            out["git_commit_calls"] += 1
                        if "push" in verbs:
                            out["git_push_calls"] += 1
            elif etype == "user" and isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict) or item.get("type") != "tool_result":
                        continue
                    text = item.get("content")
                    if isinstance(text, list):
                        text = " ".join(
                            str(t.get("text", "")) for t in text if isinstance(t, dict))
                    results[item.get("tool_use_id")] = {
                        "is_error": bool(item.get("is_error")),
                        "text": text if isinstance(text, str) else "",
                    }
            elif etype == "result":
                out["turns"] = _num(ev.get("num_turns"))
                out["cost_usd"] = _num(ev.get("total_cost_usd"))
                ms = _num(ev.get("duration_ms"))
                out["wall_s"] = ms / 1000.0 if ms is not None else None
                if isinstance(ev.get("usage"), dict):
                    out["usage"] = ev["usage"]
    # denials seen in the trace: a Bash call whose result is an error that
    # mentions a hook / denial; self-correction = a later Bash call with the
    # same git verb that succeeded.
    denied = []
    for idx, tu in enumerate(tool_uses):
        if tu["name"] != "Bash":
            continue
        res = results.get(tu["id"])
        if res and res["is_error"] and DENY_HINT_RE.search(res["text"] or ""):
            denied.append((idx, set(git_verbs(tu["input"].get("command")))))
    out["denies_trace"] = len(denied)
    for idx, verbs in denied:
        fixed = False
        for later in tool_uses[idx + 1:]:
            if later["name"] != "Bash":
                continue
            res = results.get(later["id"])
            if res is None or res["is_error"]:
                continue
            if not verbs or verbs & set(git_verbs(later["input"].get("command"))):
                fixed = True
                break
        if fixed:
            out["self_corrections"] += 1
    out["tool_uses"] = tool_uses
    return out


def parse_hook_trace(path):
    """Count decisions in a GERRIT_STACK_TRACE file (``script<TAB>verb<TAB>decision``)."""
    counts = {"deny": 0, "ask": 0, "feedback": 0, "lines": 0, "present": False}
    if not os.path.isfile(path):
        return counts
    counts["present"] = True
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.rstrip("\r\n")
            if not raw.strip():
                continue
            parts = raw.split("\t") if "\t" in raw else raw.split()
            counts["lines"] += 1
            decision = parts[2].strip() if len(parts) >= 3 else ""
            if decision in counts:
                counts[decision] += 1
    return counts


# ----------------------------------------------------------------- loading ----
def load_run(run_dir, case, arm, n, agg_entry=None):
    """One run's metric row from its directory (any file may be missing)."""
    trace = parse_trace(os.path.join(run_dir, "trace.jsonl")) if run_dir else parse_trace("")
    hook = parse_hook_trace(os.path.join(run_dir, "hook-trace.log")) if run_dir else parse_hook_trace("")
    chain = _read_json(os.path.join(run_dir, "chain-metrics.json")) if run_dir else None
    if not isinstance(chain, dict):
        chain = {}
    agg_entry = agg_entry if isinstance(agg_entry, dict) else {}

    row = {"case": case, "arm": arm, "n": n, "dir": run_dir}
    row["score"] = _num(agg_entry.get("score"))
    if row["score"] is None and agg_entry.get("passed") is not None:
        row["score"] = 1.0 if agg_entry.get("passed") else 0.0
    row["turns"] = trace["turns"] if trace["turns"] is not None else _num(agg_entry.get("turns"))
    row["tool_calls"] = float(trace["tool_calls"]) if trace["present"] else None
    row["bash_calls"] = float(trace["bash_calls"]) if trace["present"] else None
    row["git_commit_calls"] = float(trace["git_commit_calls"]) if trace["present"] else None
    row["git_push_calls"] = float(trace["git_push_calls"]) if trace["present"] else None
    row["cost_usd"] = trace["cost_usd"] if trace["cost_usd"] is not None else _num(agg_entry.get("costUsd"))
    row["wall_s"] = trace["wall_s"] if trace["wall_s"] is not None else _num(agg_entry.get("durationSeconds"))
    # asks: hook `ask` decisions + AskUserQuestion tool calls
    asks = (hook["ask"] if hook["present"] else 0) + (trace["asks_tool"] if trace["present"] else 0)
    row["asks"] = float(asks) if (hook["present"] or trace["present"]) else None
    if hook["present"]:
        row["denies"] = float(hook["deny"])
    elif trace["present"]:
        row["denies"] = float(trace["denies_trace"])
    else:
        row["denies"] = None
    row["self_corrections"] = float(trace["self_corrections"]) if trace["present"] else None
    # chain metrics (chain-metrics.sh --json)
    for key, _label, _unit, _lower in CHAIN_METRICS:
        val = chain.get(key)
        if key == "violations" and val is None and hook["present"]:
            val = hook["deny"]
        row[key] = _num(val)
    if row.get("chain_length") is None and chain:
        row["chain_length"] = 0.0
    row["change_id_set"] = chain.get("change_id_set") if isinstance(chain.get("change_id_set"), list) else None
    row["usage"] = trace.get("usage")
    return row


def _aggregate_runs_index(aggregate):
    """(case, arm, n) -> arm run entry from an official aggregate-result.json."""
    index = {}
    if not isinstance(aggregate, dict):
        return index
    for case in aggregate.get("cases") or []:
        if not isinstance(case, dict):
            continue
        name = case.get("name")
        arms = case.get("arms") if isinstance(case.get("arms"), dict) else {}
        for arm, runs in arms.items():
            if not isinstance(runs, list):
                continue
            for i, entry in enumerate(runs):
                if isinstance(entry, dict):
                    n = entry.get("run") if isinstance(entry.get("run"), int) else i + 1
                    index[(name, arm, n)] = entry
    return index


def load_results_dir(results_dir):
    """All run rows of one results directory (+ its aggregate metadata)."""
    aggregate = _read_json(os.path.join(results_dir, "aggregate-result.json"))
    agg_index = _aggregate_runs_index(aggregate)
    rows = []
    seen = set()
    runs_root = os.path.join(results_dir, "runs")
    if os.path.isdir(runs_root):
        for case in sorted(os.listdir(runs_root)):
            case_dir = os.path.join(runs_root, case)
            if not os.path.isdir(case_dir):
                continue
            for arm in sorted(os.listdir(case_dir)):
                arm_dir = os.path.join(case_dir, arm)
                if not os.path.isdir(arm_dir):
                    continue
                for nname in sorted(os.listdir(arm_dir)):
                    run_dir = os.path.join(arm_dir, nname)
                    if not os.path.isdir(run_dir):
                        continue
                    try:
                        n = int(nname)
                    except ValueError:
                        continue
                    rows.append(load_run(run_dir, case, arm, n, agg_index.get((case, arm, n))))
                    seen.add((case, arm, n))
    # runs only present in the aggregate (no per-run directory)
    for key, entry in sorted(agg_index.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2])):
        if key in seen:
            continue
        case, arm, n = key
        rows.append(load_run("", case, arm, n, entry))
    meta = {
        "dir": results_dir,
        "aggregate": bool(aggregate),
        "claudeVersion": (aggregate or {}).get("claudeVersion") if isinstance(aggregate, dict) else None,
        "overallScore": ((aggregate or {}).get("aggregates") or {}).get("overallScore") if isinstance(aggregate, dict) else None,
        "costUsd": (aggregate or {}).get("costUsd") if isinstance(aggregate, dict) else None,
    }
    return rows, meta


def default_results_dirs(repo_root):
    dirs = []
    for path in sorted(glob.glob(os.path.join(repo_root, "evals", "results", "*"))):
        if os.path.isdir(path) and (
            os.path.isfile(os.path.join(path, "aggregate-result.json"))
            or os.path.isdir(os.path.join(path, "runs"))
        ):
            dirs.append(path)
    return dirs


# ------------------------------------------------------------- aggregation ----
def _stats(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "values": []}
    return {
        "n": len(vals),
        "mean": statistics.fmean(vals),
        "median": statistics.median(vals),
        "values": vals,
    }


def aggregate(rows):
    """Per case x arm and overall per-arm statistics, deltas and targets."""
    cases = sorted({r["case"] for r in rows if r.get("case")})
    arms_seen = sorted({r["arm"] for r in rows if r.get("arm")}, key=lambda a: (ARMS.index(a) if a in ARMS else 99, a))
    per_case = {}
    for case in cases:
        per_case[case] = {}
        for arm in arms_seen:
            subset = [r for r in rows if r["case"] == case and r["arm"] == arm]
            if not subset:
                continue
            per_case[case][arm] = {
                "runs": len(subset),
                "metrics": {k: _stats([r.get(k) for r in subset]) for k in METRIC_KEYS},
            }
    overall = {}
    for arm in arms_seen:
        subset = [r for r in rows if r["arm"] == arm]
        overall[arm] = {
            "runs": len(subset),
            "metrics": {k: _stats([r.get(k) for r in subset]) for k in METRIC_KEYS},
        }

    def delta(block, a, b):
        out = {}
        if a not in block or b not in block:
            return out
        for k in METRIC_KEYS:
            ma = block[a]["metrics"][k]["mean"]
            mb = block[b]["metrics"][k]["mean"]
            out[k] = (ma - mb) if (ma is not None and mb is not None) else None
        return out

    deltas = {
        "overall": {
            "with-mcp-only": delta(overall, "with", "mcp-only"),
            "with-without": delta(overall, "with", "without"),
        },
        "cases": {
            case: {
                "with-mcp-only": delta(per_case[case], "with", "mcp-only"),
                "with-without": delta(per_case[case], "with", "without"),
            }
            for case in cases
        },
    }
    targets = evaluate_targets(overall)
    return {
        "cases": cases,
        "arms": arms_seen,
        "per_case": per_case,
        "overall": overall,
        "deltas": deltas,
        "targets": targets,
        "runs_total": len(rows),
    }


def evaluate_targets(overall):
    """Target pass/fail for arm ``with`` (cost overhead relative to ``mcp-only``)."""
    with_m = overall.get("with", {}).get("metrics", {})
    mcp_m = overall.get("mcp-only", {}).get("metrics", {})
    results = []
    for label, key, op, threshold, unit in TARGETS:
        if key == "cost_overhead_pct":
            cw = (with_m.get("cost_usd") or {}).get("mean")
            cb = (mcp_m.get("cost_usd") or {}).get("mean")
            value = ((cw - cb) / cb * 100.0) if (cw is not None and cb not in (None, 0)) else None
        else:
            value = (with_m.get(key) or {}).get("mean")
        if value is None:
            status = None
        elif op == ">=":
            status = value >= threshold
        elif op == "<=":
            status = value <= threshold
        else:
            status = abs(value - threshold) < 1e-9
        results.append({
            "label": label, "key": key, "op": op, "threshold": threshold,
            "unit": unit, "value": value, "pass": status,
        })
    return results


# --------------------------------------------------------------- rendering ----
def fmt(value, unit=""):
    if value is None:
        return "–"
    if unit == "USD":
        return "$%.3f" % value
    if unit == "%":
        return "%.1f %%" % value if abs(value - round(value)) > 1e-9 else "%d %%" % round(value)
    if abs(value - round(value)) < 1e-9:
        return "%d%s" % (round(value), (" " + unit) if unit else "")
    return "%.1f%s" % (value, (" " + unit) if unit else "")


def fmt_delta(value, unit=""):
    if value is None:
        return "–"
    sign = "+" if value > 0 else ""
    return sign + fmt(value, unit)


def _metric_table(block, arms, deltas):
    lines = ["| metric | " + " | ".join(ARM_SHORT.get(a, a) + " (mean / median)" for a in arms)
             + " | Δ C−B | Δ C−A |",
             "|---|" + "---|" * len(arms) + "---|---|"]
    for key, label, unit, _lower in ALL_METRICS:
        cells = []
        any_value = False
        for arm in arms:
            st = block.get(arm, {}).get("metrics", {}).get(key)
            if st and st["mean"] is not None:
                any_value = True
                cells.append("%s / %s" % (fmt(st["mean"], unit), fmt(st["median"], unit)))
            else:
                cells.append("–")
        if not any_value:
            continue
        d1 = fmt_delta((deltas.get("with-mcp-only") or {}).get(key), unit)
        d2 = fmt_delta((deltas.get("with-without") or {}).get(key), unit)
        lines.append("| %s | %s | %s | %s |" % (label, " | ".join(cells), d1, d2))
    return lines


def _arms_section():
    return [
        "## Arms",
        "",
        "Same prompts, same fixture repos (`evals/bench/*/fixture.sh` on the demo skeleton, real commit-msg hook, local bare remote), three arms:",
        "",
        "- **A — `without`**: vanilla Claude Code, no plugins (the floor).",
        "- **B — `mcp-only`**: vanilla + the official `gerrit@gerrit-mcp` plugin (its `gerrit-workflow` skill).",
        "- **C — `with`**: B + `gerrit-stack` (this plugin). **C vs B is the honest claim**; C vs A shows the floor.",
        "",
        "Outcome metrics come from `scripts/chain-metrics.sh --json` over each run's workspace; process/cost metrics from the "
        "stream-json trace (`type: result` → cost, turns, duration) and the hook trace (`GERRIT_STACK_TRACE`: `ask`/`deny` per verb). "
        "Cells are `mean / median` over runs; deltas are differences of means.",
        "",
    ]


def _targets_table(targets):
    lines = ["| target | required | arm C value | result |", "|---|---|---|---|"]
    for t in targets:
        req = "%s %s" % (t["op"], fmt(t["threshold"], t["unit"]))
        val = fmt(t["value"], t["unit"]) if t["unit"] != "%" or t["value"] is None else "%.1f %%" % t["value"]
        res = "–" if t["pass"] is None else ("PASS" if t["pass"] else "FAIL")
        lines.append("| %s | %s | %s | %s |" % (t["label"], req, val, res))
    return lines


def _footer():
    return [
        "## Reproduce",
        "",
        "```",
        "make bench          # python3 evals/run.py --bench --ablation --runs 3",
        "python3 evals/metrics/collect.py --out docs/benchmark.md",
        "```",
        "",
        "`make bench` runs the 3 `evals/bench/` cases (plus the 7 main cases when the runner is asked to) across the arms, "
        "writes `evals/results/<timestamp>/{aggregate-result.json,runs/…}`, and `collect.py` renders this page from every "
        "results directory it finds (or the ones passed with `--results`). Targets: budget compliance ≥ 90 %, "
        "exactly-one-Change-Id 100 %, rule violations 0, cost overhead ≤ +30 % vs arm B.",
        "",
    ]


def render_markdown(report, sources, generated=None):
    generated = generated or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# gerrit-stack efficiency benchmark", ""]
    lines.append("*Generated by `evals/metrics/collect.py` on %s.*" % generated)
    lines.append("")
    lines.append(
        "Does the plugin make an agent produce Gerrit relation chains that are smaller, correct, and cheaper to get "
        "to review than the same agent without it? Three arms on identical tasks; see targets at the bottom."
    )
    lines.append("")
    if report["runs_total"] == 0:
        lines += ["## Results", "", "**No runs yet.** Run `make bench` and re-render (see below).", ""]
        lines += _arms_section()
        lines += ["## Targets", ""] + _targets_table(report["targets"]) + [""]
        lines += _footer()
        return "\n".join(lines)

    lines += _arms_section()
    lines += ["## Sources", ""]
    for meta in sources:
        extra = []
        if meta.get("claudeVersion"):
            extra.append("claude %s" % meta["claudeVersion"])
        if meta.get("overallScore") is not None:
            extra.append("overall score %s" % fmt(_num(meta["overallScore"])))
        if meta.get("costUsd") is not None:
            extra.append("cost %s" % fmt(_num(meta["costUsd"]), "USD"))
        lines.append("- `%s`%s" % (meta["dir"], (" — " + ", ".join(extra)) if extra else ""))
    lines.append("")
    arms = report["arms"]
    lines += ["## Overall (all cases, %d runs)" % report["runs_total"], ""]
    lines += _metric_table(report["overall"], arms, report["deltas"]["overall"])
    lines.append("")
    lines += ["## Targets (arm C)", ""] + _targets_table(report["targets"]) + [""]
    lines += ["## Per case", ""]
    for case in report["cases"]:
        block = report["per_case"][case]
        runs = ", ".join("%s: %d" % (ARM_SHORT.get(a, a), block[a]["runs"]) for a in arms if a in block)
        lines += ["### %s" % case, "", "Runs — %s" % runs, ""]
        lines += _metric_table(block, arms, report["deltas"]["cases"][case])
        lines.append("")
    lines += _footer()
    return "\n".join(lines)


# -------------------------------------------------------------------- main ----
def collect(results_dirs):
    rows, sources = [], []
    for d in results_dirs:
        r, meta = load_results_dir(d)
        rows.extend(r)
        sources.append(meta)
    return rows, sources


def _json_ready(report, rows, sources):
    def clean_row(r):
        return {k: v for k, v in r.items() if k not in ("usage",)}
    return {
        "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "sources": sources,
        "runs": [clean_row(r) for r in rows],
        "cases": report["cases"],
        "arms": report["arms"],
        "per_case": report["per_case"],
        "overall": report["overall"],
        "deltas": report["deltas"],
        "targets": report["targets"],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render docs/benchmark.md from eval results.")
    parser.add_argument("--results", nargs="+", metavar="DIR", default=None,
                        help="results directories (default: evals/results/*)")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="markdown output (default: docs/benchmark.md)")
    parser.add_argument("--json", default=None, metavar="PATH", help="also write the merged JSON report")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
    results_dirs = args.results if args.results else default_results_dirs(repo_root)
    missing = [d for d in results_dirs if not os.path.isdir(d)]
    if missing:
        print("collect.py: not a directory: %s" % ", ".join(missing), file=sys.stderr)
        return 2
    rows, sources = collect(results_dirs)
    report = aggregate(rows)
    out_path = args.out or os.path.join(repo_root, "docs", "benchmark.md")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(render_markdown(report, sources))
        fh.write("\n")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(_json_ready(report, rows, sources), fh, indent=2, sort_keys=True)
            fh.write("\n")
    print("collect.py: %d run(s) from %d result dir(s) -> %s" % (len(rows), len(results_dirs), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
