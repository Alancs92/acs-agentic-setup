#!/usr/bin/env python3
"""Run systems × tasks. Resumable: every prediction is cached to preds/<task>/<system>.jsonl.

    python bench/run.py --tasks T1,T3,T5 --systems majority,heuristic,laya0,layaT,laya-head
    python bench/run.py --tasks T1 --systems haiku,sonnet,opus
    python bench/run.py --fit-only         # refit temperatures/heads and write ~/.local/share/acs-laya
"""
import argparse
import collections
import hashlib
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "systems"))
from common import DATA, PREDS, load_split, question, read_jsonl, runlog  # noqa: E402

VERDICT_TASKS = ["T1", "T3", "T5"]
EXPLORATORY = ["T2", "T4"]
OPUS_TEST_CAP, OPUS_CAL_CAP = 150, 60


def cache_path(task, system):
    return PREDS / task / f"{system}.jsonl"


def cached(task, system):
    return {(r["split"], r["id"]): r for r in read_jsonl(cache_path(task, system))}


def append(task, system, rows, split):
    p = cache_path(task, system)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        for r in rows:
            f.write(json.dumps({**r, "split": split}, default=str) + "\n")


def stratified(records, cap, seed=11):
    if len(records) <= cap:
        return records
    by = collections.defaultdict(list)
    for r in records:
        by[str(list(r["expected"].values())[0])].append(r)
    rng = random.Random(seed)
    out = []
    for k, v in by.items():
        take = max(1, round(cap * len(v) / len(records)))
        out += rng.sample(v, min(take, len(v)))
    return sorted(out, key=lambda r: r["id"])[:cap]


def run_task(task, systems):
    tr, cal, te = (load_split(task, s) for s in ("train", "cal", "test"))
    qk, q = question(te[0])
    gold_test = [r for r in te if "label:gold" in r["tags"]]
    for system in systems:
        have = cached(task, system)
        if system == "majority":
            lab = collections.Counter(r["expected"][qk] for r in tr).most_common(1)[0][0]
            for split, recs in (("cal", cal), ("test", te)):
                rows = [{"id": r["id"], "label": lab,
                         "p": (1.0 if lab else 0.0) if q["type"] == "noul" else {l: float(l == lab) for l in q["criteria"]},
                         "latency_ms": 0.0, "cost_usd": 0.0} for r in recs if (split, r["id"]) not in have]
                append(task, system, rows, split)
        elif system == "heuristic":
            if "heuristic" not in te[0]:
                continue
            hi = max(r["heuristic"] for r in tr + cal + te) or 1
            for split, recs in (("cal", cal), ("test", te)):
                rows = [{"id": r["id"], "label": r["heuristic"] / hi >= 0.5, "p": r["heuristic"] / hi,
                         "raw": r["heuristic"], "latency_ms": 0.0, "cost_usd": 0.0}
                        for r in recs if (split, r["id"]) not in have]
                append(task, system, rows, split)
        elif system in ("laya0", "laya0-perm"):
            import laya_systems as L
            fn = L.laya0 if system == "laya0" else L.laya0_perm
            if system == "laya0-perm" and q["type"] != "choice":
                continue
            for split, recs in (("train", tr), ("cal", cal), ("test", te)):
                if system == "laya0-perm" and split != "test":
                    continue
                todo = [r for r in recs if (split, r["id"]) not in have]
                if todo:
                    append(task, system, fn(todo), split)
        elif system == "layaT":
            import laya_systems as L
            base = cached(task, "laya0")
            cp = [base[("cal", r["id"])] for r in cal]
            T = L.fit_temperature(cp, cal)
            cache_path(task, system).unlink(missing_ok=True)
            append(task, system, L.apply_temperature(cp, cal, T), "cal")
            append(task, system, L.apply_temperature([base[("test", r["id"])] for r in te], te, T), "test")
            runlog(f"{task} layaT fitted T={T:.3f}")
            (PREDS / task / "layaT.params.json").write_text(json.dumps({"T": T}))
        elif system == "laya-head":
            import laya_systems as L
            if len({str(r["expected"][qk]) for r in tr}) < 2:
                runlog(f"{task} laya-head not run: single-class train split")
                continue
            c, t, params = L.laya_head(tr, cal, te)
            cache_path(task, system).unlink(missing_ok=True)
            append(task, system, c, "cal")
            append(task, system, t, "test")
            (PREDS / task / "laya-head.params.json").write_text(json.dumps(params))
            runlog(f"{task} laya-head fitted {params}")
        elif system in ("haiku", "sonnet", "opus"):
            import claude_cli as C
            gold_cal = [r for r in cal if "label:gold" in r["tags"]] or cal
            test_set = gold_test if task not in EXPLORATORY else te
            if system == "opus":
                test_set, gold_cal = stratified(test_set, OPUS_TEST_CAP), stratified(gold_cal, OPUS_CAL_CAP)
            for split, recs in (("cal", gold_cal), ("test", test_set)):
                todo = [r for r in recs if (split, r["id"]) not in have]
                if todo:
                    C.run(todo, system, on_row=lambda row, s=split: append(task, system, [row], s))
        else:
            raise SystemExit(f"unknown system {system}")
        runlog(f"{task} {system}: cached {len(read_jsonl(cache_path(task, system)))} rows")
        print(task, system, "ok", flush=True)


def fit_only():
    """Regenerate the shadow-mode artefacts (temperatures, thresholds) from cached predictions."""
    import metrics
    out = pathlib.Path.home() / ".local/share/acs-laya"
    out.mkdir(parents=True, exist_ok=True)
    manifest = {"tasks": {}}
    for task in VERDICT_TASKS + EXPLORATORY:
        if not (DATA / task / "stats.json").exists():
            continue
        m = metrics.task_metrics(task)
        card = hashlib.sha1((DATA / task / "stats.json").read_bytes()).hexdigest()
        manifest["tasks"][task] = {"card_sha1": card, "systems": {
            s: {"threshold": v.get("threshold"), "T": v.get("T")} for s, v in m["systems"].items()
            if s.startswith("laya")}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(out / "manifest.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=",".join(VERDICT_TASKS + EXPLORATORY))
    ap.add_argument("--systems", default="majority,heuristic,laya0,laya0-perm,layaT,laya-head")
    ap.add_argument("--fit-only", action="store_true")
    a = ap.parse_args()
    if a.fit_only:
        return fit_only()
    for t in a.tasks.split(","):
        run_task(t, a.systems.split(","))


if __name__ == "__main__":
    main()
