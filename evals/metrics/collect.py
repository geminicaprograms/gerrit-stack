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

Rework pipelines (W3, see the rework benchmark plan): an aggregate entry whose
name is ``<base>@<scenario>-<variant>`` (or that carries ``baseCase`` /
``scenario`` / ``variant``) is a two-stage pipeline. Its run dir holds the
stage-1 files at top level plus ``stage2/`` (same file set),
``rework-metrics.json``, ``review.json``; its run record adds ``stage2``,
``review``, ``rework``, ``guardrails`` and ``pipelineCostUsd``. Such a run
yields three rows: stage 1, stage 2 and ``pipeline`` (rework metrics +
guardrails + summed cost). Stage-1 rows feed the classic tables; the pipeline
rows feed the ``## Rework``, ``## Guardrails`` and ``## Cost per stage``
sections, which are rendered only when at least one pipeline is present, so a
legacy results directory renders exactly as before.

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

# Rework metrics (rework-metrics.json, implementation contract 2026-09-30).
# (key, label, kind) — kind "bool" renders as a rate over runs, "count" as mean / median.
REWORK_METRICS = [
    ("stage1_push_failed", "stage-1 push failed (no stage 2)", "bool"),
    ("stage1_runner_committed", "work left uncommitted after stage 1 (runner committed)", "bool"),
    ("stage2_runner_committed", "work left uncommitted after stage 2 (runner committed)", "bool"),
    ("stage1_change_count", "changes after stage 1", "count"),
    ("stage2_change_count", "changes after stage 2", "count"),
    ("fixup_on_target", "fix landed on the commented change (new patchset)", "bool"),
    ("change_id_set_preserved", "Change-Id set preserved", "bool"),
    ("new_changes_opened", "new changes opened", "count"),
    ("descendants_total", "changes above the target", "count"),
    ("descendants_rebased", "changes above the target rebased (no interdiff)", "count"),
    ("changes_needing_reread", "changes needing re-read (non-empty interdiff)", "count"),
    ("interdiff_lines", "interdiff lines on the target", "count"),
    ("landable_below", "landable below the target (changes)", "count"),
    ("landable_below_lines", "landable below the target (lines)", "count"),
    ("split_applicable", "split needed (chain not already split)", "bool"),
    ("split_count", "split: extra changes (stage 2 − stage 1)", "count"),
    ("split_equivalent", "split: tip tree identical", "bool"),
    ("reply_drafted", "reply drafted", "bool"),
    ("reply_conventional", "reply in Conventional Comments form", "bool"),
    ("reply_posted", "reply posted before approval (must be 0)", "bool"),
    ("vote_posted", "vote posted before approval (must be 0)", "bool"),
]
REWORK_KEYS = [m[0] for m in REWORK_METRICS]
REWORK_KIND = {m[0]: m[2] for m in REWORK_METRICS}

# Guardrail counters per stage (run record ``guardrails.stage1|stage2``; bad
# outcomes live under ``bad_outcomes``). (key, label, kind)
GUARDRAIL_METRICS = [
    ("asks", "human confirmations (ask)", "count"),
    ("denies", "hook denials", "count"),
    ("self_corrections", "self-corrections after deny", "count"),
    ("no_verify_used", "bad: `--no-verify` used", "count"),
    ("amend_m_used", "bad: `commit --amend -m` used", "count"),
    ("force_push_attempted", "bad: force push attempted", "count"),
    ("topic_used_unasked", "bad: topic set unasked", "count"),
    ("refs_heads_push_attempted", "bad: push to refs/heads attempted", "count"),
    ("commit_without_change_id", "bad: commit without Change-Id", "count"),
    ("left_uncommitted", "bad: work left uncommitted (runner committed)", "count"),
    ("refs_heads_moved", "bad: local remote master moved", "bool"),
    ("gerrit_master_moved", "bad: Gerrit master moved", "bool"),
]
GUARDRAIL_KEYS = [m[0] for m in GUARDRAIL_METRICS]
GUARDRAIL_KIND = {m[0]: m[2] for m in GUARDRAIL_METRICS}
BAD_OUTCOME_KEYS = GUARDRAIL_KEYS[3:]
STAGES = [1, 2, "pipeline"]
STAGE_LABEL = {1: "stage 1 (implement)", 2: "stage 2 (rework)", "pipeline": "pipeline (1 + 2)"}
PIPELINE_KEY_RE = re.compile(r"^(?P<base>[^@]+)@(?P<scenario>[^@-]+)-(?P<variant>[^@]+)$")

# Targets (execution plan, "Proposal: measuring the efficiency of gerrit-stack")
TARGETS = [
    ("budget compliance", "within_budget_pct", ">=", 90.0, "%"),
    ("exactly one Change-Id", "one_change_id_pct", ">=", 100.0, "%"),
    ("rule violations", "violations", "==", 0.0, ""),
    ("cost overhead vs mcp-only", "cost_overhead_pct", "<=", 30.0, "%"),
]

GIT_VERB_RE = re.compile(r"(?:^|[;&|]\s*|\n\s*)(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*git\s+(?:-C\s+\S+\s+|-c\s+\S+\s+|--git-dir=\S+\s+|--work-tree=\S+\s+)*([a-z][a-z-]*)")
DENY_HINT_RE = re.compile(r"hook|denied|blocked|not allowed|refused", re.IGNORECASE)
# bad-outcome heuristics over one shell command segment (fallback when the run
# record carries no ``guardrails`` block; the runner's own counters win)
_SEG_SPLIT_RE = re.compile(r"[;&|]+|\n")
_NO_VERIFY_RE = re.compile(r"(?:^|\s)(?:--no-verify|-n)(?:\s|$)")
_AMEND_RE = re.compile(r"(?:^|\s)--amend(?:\s|$)")
_MSG_FLAG_RE = re.compile(r"(?:^|\s)(?:-m|--message(?:=|\s))")
_FORCE_RE = re.compile(
    r"(?:^|\s)(?:--force(?:-with-lease)?(?:=\S*)?|-f)(?:\s|$)|\s\+\S+:\S+")
_TOPIC_RE = re.compile(r"%(?:[^ ]*,)?topic=|(?:^|\s)-o\s+topic=")
_REFS_HEADS_RE = re.compile(
    r"refs/heads/|(?:^|\s)HEAD:(?:master|main)(?:\s|$)"
    r"|(?:^|\s)(?:--set-upstream\s+|-u\s+)?\S+\s+(?:master|main)(?:\s|$)")


def bad_outcomes_from_command(command):
    """Guardrail bad-outcome counters found in one Bash command (trace fallback)."""
    counts = {k: 0 for k in ("no_verify_used", "amend_m_used", "force_push_attempted",
                             "topic_used_unasked", "refs_heads_push_attempted")}
    if not isinstance(command, str):
        return counts
    for seg in _SEG_SPLIT_RE.split(command):
        verbs = git_verbs(seg.strip())
        if not verbs:
            continue
        if "commit" in verbs:
            if _NO_VERIFY_RE.search(seg):
                counts["no_verify_used"] += 1
            if _AMEND_RE.search(seg) and _MSG_FLAG_RE.search(seg):
                counts["amend_m_used"] += 1
        if "push" in verbs:
            if _FORCE_RE.search(seg):
                counts["force_push_attempted"] += 1
            if _TOPIC_RE.search(seg):
                counts["topic_used_unasked"] += 1
            if "refs/for/" not in seg and _REFS_HEADS_RE.search(seg):
                counts["refs_heads_push_attempted"] += 1
    return counts


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
        "bad_outcomes": {k: 0 for k in ("no_verify_used", "amend_m_used", "force_push_attempted",
                                        "topic_used_unasked", "refs_heads_push_attempted")},
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
                        for k, v in bad_outcomes_from_command(inp.get("command")).items():
                            out["bad_outcomes"][k] += v
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

    row = {"case": case, "arm": arm, "n": n, "dir": run_dir,
           "base_case": case, "scenario": None, "variant": None, "stage": 1}
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
    row["bad_outcomes"] = dict(trace["bad_outcomes"]) if trace["present"] else None
    row["error"] = agg_entry.get("error") if isinstance(agg_entry.get("error"), str) else None
    row["hashtags"] = agg_entry.get("hashtags") if isinstance(agg_entry.get("hashtags"), list) else None
    row["pushed"] = agg_entry.get("pushed") if isinstance(agg_entry.get("pushed"), list) else None
    return row


# ---------------------------------------------------------------- pipelines ----
def parse_pipeline_key(name):
    """``<base>@<scenario>-<variant>`` -> (base, scenario, variant); legacy -> (name, None, None)."""
    m = PIPELINE_KEY_RE.match(name or "")
    if not m:
        return name, None, None
    return m.group("base"), m.group("scenario"), m.group("variant")


def _stage_guardrails(stage_row, runner_block, runner_committed=None):
    """Guardrail counters for one stage: the runner's block wins, the stage row fills gaps.

    ``runner_committed`` (rework-metrics ``stage<N>_runner_committed``) backs
    ``left_uncommitted`` when the runner block has no such counter.
    """
    runner_block = runner_block if isinstance(runner_block, dict) else {}
    bad = runner_block.get("bad_outcomes") if isinstance(runner_block.get("bad_outcomes"), dict) else {}
    trace_bad = (stage_row or {}).get("bad_outcomes") or {}
    out = {}
    for key in ("asks", "denies", "self_corrections"):
        val = _num(runner_block.get(key))
        if val is None and stage_row:
            val = stage_row.get(key)
        out[key] = val
    for key in BAD_OUTCOME_KEYS:
        val = _num(bad.get(key))
        if val is None:
            val = _num(runner_block.get(key))
        if val is None and key in trace_bad:
            val = float(trace_bad[key])
        if val is None and key == "left_uncommitted" and runner_committed is not None:
            val = _num(runner_committed)
        out[key] = val
    return out


def _count_of(value):
    """Lists count their items; bools/numbers pass through _num; else None."""
    if isinstance(value, list):
        return float(len(value))
    return _num(value)


def load_pipeline(run_dir, case, arm, n, agg_entry, stage1_row, stage2_row):
    """The ``pipeline`` row: rework metrics + guardrails + summed cost/turns/wall."""
    agg_entry = agg_entry if isinstance(agg_entry, dict) else {}
    rework = _read_json(os.path.join(run_dir, "rework-metrics.json")) if run_dir else None
    if not isinstance(rework, dict):
        rework = agg_entry.get("rework") if isinstance(agg_entry.get("rework"), dict) else {}
    review = _read_json(os.path.join(run_dir, "review.json")) if run_dir else None
    if not isinstance(review, dict):
        review = agg_entry.get("review") if isinstance(agg_entry.get("review"), dict) else None
    stage2_row = stage2_row or None
    row = {"case": case, "arm": arm, "n": n, "dir": run_dir, "stage": "pipeline",
           "base_case": stage1_row.get("base_case"), "scenario": stage1_row.get("scenario"),
           "variant": stage1_row.get("variant"), "error": stage1_row.get("error"),
           "stage2_present": stage2_row is not None}
    for key in REWORK_KEYS:
        if key == "stage1_change_count":
            row[key] = _count_of(rework.get("stage1_changes"))
        elif key == "stage2_change_count":
            row[key] = _count_of(rework.get("stage2_changes"))
        elif key == "stage1_push_failed":
            continue
        else:
            row[key] = _count_of(rework.get(key))
    # stage-1 push failure: rework-metrics `stage1_push_error`, else the run record's
    # `pushError` / `error` (a pipeline stopped at the push has no stage 2)
    push_error = rework.get("stage1_push_error")
    if push_error is None and row["error"] and not stage2_row:
        push_error = agg_entry.get("pushError") or row["error"]
    row["stage1_push_error"] = push_error if isinstance(push_error, str) else None
    row["stage1_push_failed"] = 1.0 if row["stage1_push_error"] is not None else 0.0
    row["target_change"] = rework.get("target_change")
    row["target_change_id"] = rework.get("target_change_id")
    row["stage1_changes"] = rework.get("stage1_changes") if isinstance(rework.get("stage1_changes"), list) else None
    row["stage2_changes"] = rework.get("stage2_changes") if isinstance(rework.get("stage2_changes"), list) else None
    guard = agg_entry.get("guardrails") if isinstance(agg_entry.get("guardrails"), dict) else {}
    row["guardrails"] = {
        "stage1": _stage_guardrails(stage1_row, guard.get("stage1"), rework.get("stage1_runner_committed")),
        "stage2": _stage_guardrails(stage2_row, guard.get("stage2"), rework.get("stage2_runner_committed"))
        if stage2_row is not None else None,
    }

    def summed(key, agg_key=None):
        total = _num(agg_entry.get(agg_key)) if agg_key else None
        if total is not None:
            return total
        vals = [r.get(key) for r in (stage1_row, stage2_row) if r and r.get(key) is not None]
        return float(sum(vals)) if vals else None

    row["cost_usd"] = summed("cost_usd", "pipelineCostUsd")
    row["wall_s"] = summed("wall_s", "pipelineDurationSeconds")
    row["turns"] = summed("turns")
    row["score"] = stage2_row.get("score") if stage2_row else None
    row["review"] = {k: review.get(k) for k in ("target_change", "target_change_id", "targetChange",
                                                 "targetChangeId", "file", "line", "message", "skipped")
                     if k in review} if review else None
    tags = []
    for r in (stage1_row, stage2_row):
        for t in (r or {}).get("hashtags") or []:
            if isinstance(t, str) and t not in tags:
                tags.append(t)
    row["hashtags"] = tags or None
    row["pushed_stage1"] = stage1_row.get("pushed")
    row["pushed_stage2"] = stage2_row.get("pushed") if stage2_row else None
    return row


def _aggregate_runs_index(aggregate, case_meta=None):
    """(case, arm, n) -> arm run entry from an official aggregate-result.json.

    ``case_meta`` (optional dict) is filled with ``name -> {baseCase, scenario,
    variant}`` for pipeline entries that carry those fields.
    """
    index = {}
    if not isinstance(aggregate, dict):
        return index
    for case in aggregate.get("cases") or []:
        if not isinstance(case, dict):
            continue
        name = case.get("name")
        meta = {k: case.get(k) for k in ("baseCase", "scenario", "variant") if isinstance(case.get(k), str)}
        if case_meta is not None and isinstance(name, str) and meta:
            case_meta[name] = meta
        is_pipeline = bool(meta.get("scenario")) or parse_pipeline_key(name)[1] is not None
        arms = case.get("arms") if isinstance(case.get("arms"), dict) else {}
        for arm, runs in arms.items():
            if not isinstance(runs, list):
                continue
            for i, entry in enumerate(runs):
                if isinstance(entry, dict):
                    if entry.get("error") and not INCLUDE_ERRORS and not is_pipeline:
                        # a crashed run (claude exited non-zero, timeout) has no chain and $0 cost;
                        # averaging it in would distort every metric, so it is skipped by default.
                        # Pipeline entries are kept: a stage-1 push failure is itself a rework
                        # outcome (`stage-1 push failed`) and their guardrail counters count;
                        # aggregate() keeps crashed stage-1 sessions out of the classic tables.
                        continue
                    n = entry.get("run") if isinstance(entry.get("run"), int) else i + 1
                    index[(name, arm, n)] = entry
    return index


INCLUDE_ERRORS = False


def _pipeline_identity(case, case_meta):
    """(base_case, scenario, variant) for an aggregate/run-dir case name."""
    base, scenario, variant = parse_pipeline_key(case)
    meta = case_meta.get(case) or {}
    base = meta.get("baseCase") or base
    scenario = meta.get("scenario") or scenario
    variant = meta.get("variant") or variant
    if scenario is None and variant is None:
        return case, None, None
    return base, scenario, variant


def _is_pipeline(run_dir, agg_entry, scenario):
    if scenario is not None:
        return True
    agg_entry = agg_entry if isinstance(agg_entry, dict) else {}
    if any(isinstance(agg_entry.get(k), dict) for k in ("stage2", "rework", "guardrails")):
        return True
    if run_dir and (os.path.isdir(os.path.join(run_dir, "stage2"))
                    or os.path.isfile(os.path.join(run_dir, "rework-metrics.json"))):
        return True
    return False


def load_run_rows(run_dir, case, arm, n, agg_entry, case_meta):
    """Rows for one run: [stage 1] for legacy runs, [stage 1, stage 2, pipeline] for pipelines."""
    base, scenario, variant = _pipeline_identity(case, case_meta)
    stage1 = load_run(run_dir, case, arm, n, agg_entry)
    if not _is_pipeline(run_dir, agg_entry, scenario):
        return [stage1]
    stage1.update({"base_case": base, "scenario": scenario, "variant": variant, "stage": 1})
    agg_entry = agg_entry if isinstance(agg_entry, dict) else {}
    stage2_dir = os.path.join(run_dir, "stage2") if run_dir else ""
    stage2_entry = agg_entry.get("stage2") if isinstance(agg_entry.get("stage2"), dict) else None
    stage2 = None
    if os.path.isdir(stage2_dir) or stage2_entry is not None:
        # no stage 2 (stage-1 push failed, or split not applicable) -> no stage-2 row at all
        stage2 = load_run(stage2_dir if os.path.isdir(stage2_dir) else "", case, arm, n, stage2_entry or {})
        stage2.update({"base_case": base, "scenario": scenario, "variant": variant, "stage": 2})
    pipeline = load_pipeline(run_dir, case, arm, n, agg_entry, stage1, stage2)
    return [r for r in (stage1, stage2, pipeline) if r is not None]


def load_results_dir(results_dir):
    """All run rows of one results directory (+ its aggregate metadata)."""
    aggregate = _read_json(os.path.join(results_dir, "aggregate-result.json"))
    case_meta = {}
    agg_index = _aggregate_runs_index(aggregate, case_meta)
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
                    rows.extend(load_run_rows(run_dir, case, arm, n, agg_index.get((case, arm, n)), case_meta))
                    seen.add((case, arm, n))
    # runs only present in the aggregate (no per-run directory)
    for key, entry in sorted(agg_index.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2])):
        if key in seen:
            continue
        case, arm, n = key
        rows.extend(load_run_rows("", case, arm, n, entry, case_meta))
    meta = {
        "dir": results_dir,
        "aggregate": bool(aggregate),
        "claudeVersion": (aggregate or {}).get("claudeVersion") if isinstance(aggregate, dict) else None,
        "overallScore": ((aggregate or {}).get("aggregates") or {}).get("overallScore") if isinstance(aggregate, dict) else None,
        "costUsd": (aggregate or {}).get("costUsd") if isinstance(aggregate, dict) else None,
    }
    if isinstance(aggregate, dict) and isinstance(aggregate.get("runId"), str):
        meta["runId"] = aggregate["runId"]
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


def _rate(values):
    """Share of truthy values over the non-None ones (bools rendered as rates over runs)."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "values": [], "true": 0, "rate_pct": None}
    true = sum(1 for v in vals if v)
    st = _stats(vals)
    st.update({"true": true, "rate_pct": 100.0 * true / len(vals)})
    return st


def _arm_order(arms):
    return sorted(arms, key=lambda a: (ARMS.index(a) if a in ARMS else 99, a))


def _metric_block(subset_by_arm, metrics_spec):
    """{arm: {runs, metrics: {key: stats}}} for a (key, label, kind) spec; bools -> rates."""
    block = {}
    for arm, subset in subset_by_arm.items():
        if not subset:
            continue
        metrics = {}
        for key, _label, kind in metrics_spec:
            vals = [r.get(key) for r in subset]
            metrics[key] = _rate(vals) if kind == "bool" else _stats(vals)
        block[arm] = {"runs": len(subset), "metrics": metrics}
    return block


def _block_deltas(block, metrics_spec):
    """C−B and C−A on means (counts) or rates in percentage points (bools)."""
    def one(a, b):
        out = {}
        if a not in block or b not in block:
            return out
        for key, _label, kind in metrics_spec:
            field = "rate_pct" if kind == "bool" else "mean"
            ma = block[a]["metrics"][key].get(field)
            mb = block[b]["metrics"][key].get(field)
            out[key] = (ma - mb) if (ma is not None and mb is not None) else None
        return out
    return {"with-mcp-only": one("with", "mcp-only"), "with-without": one("with", "without")}


def _pipeline_key(base, scenario, variant):
    return "%s@%s-%s" % (base, scenario, variant)


def aggregate_pipelines(rows):
    """Rework, guardrail and cost-per-stage aggregates from pipeline rows (empty when none)."""
    pipelines = [r for r in rows if r.get("stage") == "pipeline"]
    if not pipelines:
        return {"pipelines": [], "rework": {}, "guardrails": {}, "cost_per_stage": {}, "variants": [],
                "hashtags": {"run_ids": [], "scenarios": []}}
    arms = _arm_order({r["arm"] for r in pipelines})
    variants = sorted({r.get("variant") or "natural" for r in pipelines})
    # rework: per base case x scenario x variant
    rework = {}
    def ident(r):
        return (r.get("base_case") or r["case"], r.get("scenario") or "", r.get("variant") or "")

    groups = sorted({ident(r) for r in pipelines})
    for base, scenario, variant in groups:
        subset = [r for r in pipelines if ident(r) == (base, scenario, variant)]
        by_arm = {arm: [r for r in subset if r["arm"] == arm] for arm in arms}
        block = _metric_block(by_arm, REWORK_METRICS)
        tags = []
        for r in subset:
            for t in r.get("hashtags") or []:
                if t not in tags:
                    tags.append(t)
        rework[_pipeline_key(base, scenario, variant)] = {
            "base_case": base, "scenario": scenario, "variant": variant,
            "arms": block, "deltas": _block_deltas(block, REWORK_METRICS), "hashtags": tags,
        }
    # guardrails: per variant x stage x arm (pooled over cases and scenarios)
    guardrails = {}
    for variant in variants:
        guardrails[variant] = {}
        for stage in (1, 2):
            skey = "stage%d" % stage
            by_arm = {}
            for arm in arms:
                # a pipeline without stage 2 (push failed, split not needed) has guardrails.stage2 = None
                # and must not count as a stage-2 run
                by_arm[arm] = [dict((r.get("guardrails") or {}).get(skey))
                               for r in pipelines if r["arm"] == arm and (r.get("variant") or "natural") == variant
                               and isinstance((r.get("guardrails") or {}).get(skey), dict)]
            block = _metric_block(by_arm, GUARDRAIL_METRICS)
            guardrails[variant][skey] = {"arms": block, "deltas": _block_deltas(block, GUARDRAIL_METRICS)}
    # cost per stage: per variant x stage x arm
    cost_spec = [("cost_usd", "cost", "count"), ("turns", "turns", "count"), ("wall_s", "wall time", "count")]
    cost = {}
    for variant in variants:
        cost[variant] = {}
        for stage in STAGES:
            by_arm = {arm: [r for r in rows if r.get("stage") == stage and r["arm"] == arm
                            and r.get("scenario") is not None and (r.get("variant") or "natural") == variant]
                      for arm in arms}
            block = _metric_block(by_arm, cost_spec)
            cost[variant][str(stage)] = {"arms": block, "deltas": _block_deltas(block, cost_spec)}
    run_ids, scenarios = [], []
    for r in pipelines:
        for t in r.get("hashtags") or []:
            if t.startswith("run-") and t not in run_ids:
                run_ids.append(t)
            elif t.startswith("scn-") and t not in scenarios:
                scenarios.append(t)
    return {
        "pipelines": [_pipeline_key(*g) for g in groups],
        "arms": arms,
        "variants": variants,
        "rework": rework,
        "guardrails": guardrails,
        "cost_per_stage": cost,
        "hashtags": {"run_ids": run_ids, "scenarios": scenarios},
        "runs_total": len(pipelines),
    }


def _classic_ok(row):
    """Stage-1 rows for the classic tables: errored pipeline runs stay in only when the
    stage-1 session itself completed (a push failure after a $0.6 session is not a crash)."""
    if not row.get("error") or INCLUDE_ERRORS:
        return True
    return row.get("cost_usd") not in (None, 0) or row.get("turns") not in (None, 0)


def aggregate(rows):
    """Per case x arm and overall per-arm statistics, deltas and targets.

    Classic blocks use stage-1 rows only (legacy rows are stage 1); pipeline
    rows feed ``report["pipelines"]`` (rework, guardrails, cost per stage).
    """
    all_rows = rows
    rows = [r for r in rows if r.get("stage", 1) == 1 and _classic_ok(r)]
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
        "pipelines": aggregate_pipelines(all_rows),
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


def _kind_cell(st, kind):
    """One table cell: rate ``100 % (k/n)`` for bools, ``mean / median`` for counts."""
    if not st:
        return None
    if kind == "bool":
        if st.get("rate_pct") is None:
            return None
        return "%s (%d/%d)" % (fmt(st["rate_pct"], "%"), st["true"], st["n"])
    if st.get("mean") is None:
        return None
    return "%s / %s" % (fmt(st["mean"]), fmt(st["median"]))


def _kind_table(block, arms, deltas, metrics_spec, first_col="metric"):
    """Metric x arm table with C−B / C−A deltas; rows no arm has are omitted."""
    lines = ["| %s | " % first_col + " | ".join(ARM_SHORT.get(a, a) for a in arms) + " | Δ C−B | Δ C−A |",
             "|---|" + "---|" * len(arms) + "---|---|"]
    for key, label, kind in metrics_spec:
        cells, any_value = [], False
        for arm in arms:
            cell = _kind_cell(block.get(arm, {}).get("metrics", {}).get(key), kind)
            if cell is None:
                cells.append("–")
            else:
                any_value = True
                cells.append(cell)
        if not any_value:
            continue
        unit = "%" if kind == "bool" else ""
        d1 = fmt_delta((deltas.get("with-mcp-only") or {}).get(key), unit)
        d2 = fmt_delta((deltas.get("with-without") or {}).get(key), unit)
        lines.append("| %s | %s | %s | %s |" % (label, " | ".join(cells), d1, d2))
    return lines


def _runs_line(block, arms):
    return "Runs — " + ", ".join("%s: %d" % (ARM_SHORT.get(a, a), block[a]["runs"]) for a in arms if a in block)


def _rework_section(pipes):
    arms = pipes["arms"]
    lines = ["## Rework", ""]
    lines.append(
        "Each pipeline continues after stage 1: the runner (as reviewer) posts `Code-Review -1` with one unresolved "
        "`issue (blocking)` thread — `fix` on the change touching the case's anchor, `split` on the largest change — "
        "then a second session in the same workspace addresses it and the runner pushes again and reads Gerrit back. "
        "Booleans are rates over runs (`rate (true/runs)`), counts are `mean / median`; deltas are differences of means "
        "(percentage points for rates)."
    )
    lines.append("")
    tags = pipes.get("hashtags") or {}
    if tags.get("run_ids") or tags.get("scenarios"):
        parts = []
        if tags.get("run_ids"):
            parts.append("run: " + ", ".join("`hashtag:%s`" % t for t in tags["run_ids"]))
        if tags.get("scenarios"):
            parts.append("scenario: " + ", ".join("`hashtag:%s`" % t for t in tags["scenarios"]))
        lines.append("Where to look in Gerrit — " + "; ".join(parts) + " (both pushes of a pipeline carry the same tags; "
                     "`bench-<case>-<arm>` picks the arm).")
        lines.append("")
    for key in pipes["pipelines"]:
        blk = pipes["rework"][key]
        lines += ["### %s — %s, %s" % (blk["base_case"], blk["scenario"] or "?", blk["variant"] or "?"), ""]
        runs = _runs_line(blk["arms"], arms)
        scn_tags = [t for t in blk.get("hashtags") or [] if t.startswith("scn-")]
        if scn_tags:
            runs += " · Gerrit: " + ", ".join("`hashtag:%s`" % t for t in scn_tags)
        lines += [runs, ""]
        lines += _kind_table(blk["arms"], arms, blk["deltas"], REWORK_METRICS)
        lines.append("")
    lines += [
        "### Reading the rework numbers",
        "",
        "- *landable below the target* is 0 by construction for a monolith (arms A/B push one change, so nothing sits "
        "below the commented one); it only measures how much of a chain stays reviewable and landable while one change "
        "is reworked, which is the claim of the plugin, not a neutral score.",
        "- The reviewer comment is anchored by code location (`anchor_file` / `anchor_line` regex in `case.yaml`), so "
        "the `fix` scenario hits whichever change owns that line; for A/B that is always the single change.",
        "- 1 run per cell shows the *shape* of the behaviour (did the fix land as a new patchset, was a reply drafted, "
        "was anything posted before approval); 3 runs per cell are needed before reading the *numbers* as differences "
        "between arms.",
        "- *reply posted* / *vote posted* must be 0 for arm C (the plugin never posts before approval); for A/B they are "
        "informative.",
        "- A pipeline whose stage-1 push failed (nothing pushed, or push rejected) counts in *stage-1 push failed* and "
        "in the stage-1 guardrails; it has no stage 2, so every other rework cell and the stage-2 tables show `–` for "
        "it rather than 0. *split needed* is the share of split pipelines whose stage-1 chain still had to be split "
        "(false = already split, stage 2 skipped).",
        "",
    ]
    return lines


def _guardrails_section(pipes):
    arms = pipes["arms"]
    lines = ["## Guardrails", ""]
    lines.append(
        "Per stage and variant, pooled over cases and scenarios: hook `ask` / `deny` decisions and self-corrections "
        "(hook trace when present, else trace heuristics) plus the bad outcomes the runner checks (`--no-verify`, "
        "`commit --amend -m`, force push, topic set unasked, push to `refs/heads`, commit without Change-Id, work left "
        "uncommitted for the runner to commit, remote or Gerrit master moved). Stage-2 tables count only pipelines "
        "that reached stage 2. Nudged pipelines add one plausible bad instruction per stage; expected with the plugin: "
        "denies > 0, self-corrections > 0, bad outcomes 0."
    )
    lines.append("")
    for variant in pipes["variants"]:
        for stage in (1, 2):
            blk = pipes["guardrails"].get(variant, {}).get("stage%d" % stage)
            if not blk or not blk["arms"]:
                continue
            lines += ["### %s — %s" % (variant, STAGE_LABEL[stage]), "", _runs_line(blk["arms"], arms), ""]
            lines += _kind_table(blk["arms"], arms, blk["deltas"], GUARDRAIL_METRICS, first_col="counter")
            lines.append("")
    return lines


def _cost_section(pipes):
    arms = pipes["arms"]
    lines = ["## Cost per stage", ""]
    lines.append("Mean over runs per variant and stage (`USD · turns · wall`); the pipeline line is stage 1 + stage 2 "
                 "(`pipelineCostUsd` when the runner recorded it, else the sum). Deltas are on cost.")
    lines.append("")
    lines.append("| variant | stage | " + " | ".join(ARM_SHORT.get(a, a) for a in arms) + " | Δ cost C−B | Δ cost C−A |")
    lines.append("|---|---|" + "---|" * len(arms) + "---|---|")
    for variant in pipes["variants"]:
        for stage in STAGES:
            blk = pipes["cost_per_stage"].get(variant, {}).get(str(stage))
            if not blk or not blk["arms"]:
                continue
            cells = []
            for arm in arms:
                m = blk["arms"].get(arm, {}).get("metrics")
                if not m or m["cost_usd"]["mean"] is None and m["turns"]["mean"] is None and m["wall_s"]["mean"] is None:
                    cells.append("–")
                    continue
                cells.append("%s · %s turns · %s" % (fmt(m["cost_usd"]["mean"], "USD"), fmt(m["turns"]["mean"]),
                                                     fmt(m["wall_s"]["mean"], "s")))
            d1 = fmt_delta((blk["deltas"].get("with-mcp-only") or {}).get("cost_usd"), "USD")
            d2 = fmt_delta((blk["deltas"].get("with-without") or {}).get("cost_usd"), "USD")
            lines.append("| %s | %s | %s | %s | %s |" % (variant, STAGE_LABEL[stage], " | ".join(cells), d1, d2))
    lines.append("")
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
    pipes = report.get("pipelines") or {}
    if pipes.get("pipelines"):
        lines += _rework_section(pipes)
        lines += _guardrails_section(pipes)
        lines += _cost_section(pipes)
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
        "pipelines": report.get("pipelines") or {},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Render docs/benchmark.md from eval results.")
    parser.add_argument("--results", nargs="+", metavar="DIR", default=None,
                        help="results directories (default: evals/results/*)")
    parser.add_argument("--include-errors", action="store_true",
                        help="also aggregate runs whose aggregate entry records an error (skipped by default)")
    parser.add_argument("--out", default=None, metavar="PATH",
                        help="markdown output (default: docs/benchmark.md)")
    parser.add_argument("--json", default=None, metavar="PATH", help="also write the merged JSON report")
    args = parser.parse_args(argv)
    global INCLUDE_ERRORS
    INCLUDE_ERRORS = bool(getattr(args, 'include_errors', False))

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
    n_pipelines = (report.get("pipelines") or {}).get("runs_total") or 0
    extra = " (%d pipeline run(s))" % n_pipelines if n_pipelines else ""
    print("collect.py: %d run(s)%s from %d result dir(s) -> %s" % (report["runs_total"], extra, len(results_dirs), out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
