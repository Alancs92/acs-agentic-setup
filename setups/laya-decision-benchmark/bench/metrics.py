#!/usr/bin/env python3
"""Metrics on the gold test split: accuracy / macro-F1 (bootstrap CIs), ECE / Brier, coverage at the task's
required precision (threshold picked on cal, applied to test, Wilson lower bound), flip rate, cost/latency.

Coverage definition:
  noul   — the auto-action is "yes" (consume as duplicate / accept link). Coverage = share of the test set's
           true positives that get auto-actioned (i.e. recall at the required precision). Measuring it as a
           share of all items would cap T1 at ~19%, because it carries 3 hard negatives per positive.
  choice — coverage = share of test items auto-decided. For T2, predicted `dismiss` is never automatable.
"""
import collections
import json
import math
import pathlib
import random
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import DATA, PREDS, load_split, question, read_jsonl  # noqa: E402

REQUIRED = {"T1": 1.00, "T2": 0.95, "T3": 0.90, "T4": 0.90, "T5": 0.85, "T6": 0.95}
SYSTEMS = ["majority", "heuristic", "laya0", "layaT", "laya-head", "haiku", "sonnet", "opus"]
LAYA_RSS_GB = {"mps": 1.38, "cpu": 2.39}  # peak RSS from the Phase 1 micro-benchmark


def wilson_lb(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def score_of(pred, qtype):
    if pred["label"] is None or pred["p"] is None:
        return None
    return pred["p"] if qtype == "noul" else max(pred["p"].values())


def acts(pred, qtype, task):
    if pred["label"] is None:
        return False
    if qtype == "noul":
        return True  # thresholded on p_yes; only items with p_yes >= thr act (as "yes")
    return not (task == "T2" and pred["label"] == "dismiss")


def correct(pred, exp, qtype):
    if qtype == "noul":
        return exp is True  # acting = saying yes
    return pred["label"] == exp


def pick_threshold(rows, qtype, task, req):
    """rows: [(pred, expected)] on cal. Largest confidence prefix with precision >= req."""
    cand = sorted(((score_of(p, qtype), correct(p, e, qtype)) for p, e in rows
                   if acts(p, qtype, task) and score_of(p, qtype) is not None), key=lambda x: -x[0])
    best, k_ok = math.inf, 0
    ok = n = 0
    i = 0
    while i < len(cand):
        s = cand[i][0]
        while i < len(cand) and cand[i][0] == s:  # ties go in together
            ok += cand[i][1]
            n += 1
            i += 1
        if ok / n >= req - 1e-12:
            best, k_ok = s, n
    return best, k_ok


def apply_threshold(rows, qtype, task, thr):
    acted = [(p, e) for p, e in rows if acts(p, qtype, task) and score_of(p, qtype) is not None
             and score_of(p, qtype) >= thr]
    k = sum(correct(p, e, qtype) for p, e in acted)
    if qtype == "noul":
        denom = sum(1 for _, e in rows if e is True)
        cov = k / denom if denom else 0.0
    else:
        cov = len(acted) / len(rows) if rows else 0.0
    prec = k / len(acted) if acted else None
    return {"coverage": cov, "acted": len(acted), "correct": k, "precision": prec,
            "wilson_lb": wilson_lb(k, len(acted)) if acted else None}


def curve(rows, qtype, task):
    cand = sorted(((score_of(p, qtype), correct(p, e, qtype)) for p, e in rows
                   if acts(p, qtype, task) and score_of(p, qtype) is not None), key=lambda x: -x[0])
    denom = sum(1 for _, e in rows if e is True) if qtype == "noul" else len(rows)
    out, ok = [], 0
    for i, (_, c) in enumerate(cand, 1):
        ok += c
        out.append((round(ok / denom if qtype == "noul" else i / denom, 4), round(ok / i, 4)))
    return out


def macro_f1(y, yhat, labels):
    f = []
    for l in labels:
        tp = sum(a == l and b == l for a, b in zip(y, yhat))
        fp = sum(a != l and b == l for a, b in zip(y, yhat))
        fn = sum(a == l and b != l for a, b in zip(y, yhat))
        f.append(0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return sum(f) / len(f)


def boot_ci(fn, n, B=1000, seed=3):
    rng = random.Random(seed)
    vals = sorted(fn([rng.randrange(n) for _ in range(n)]) for _ in range(B))
    return round(vals[int(0.025 * B)], 3), round(vals[int(0.975 * B) - 1], 3)


def ece_brier(rows, qtype, labels, bins=15):
    if qtype == "noul":
        pts = [(p["p"], float(e is True)) for p, e in rows if p["p"] is not None]
        brier = statistics.mean((a - b) ** 2 for a, b in pts) if pts else None
    else:
        pts = [(max(p["p"].values()), float(p["label"] == e)) for p, e in rows if p["p"] is not None]
        brier = statistics.mean(sum((p["p"].get(l, 0) - (l == e)) ** 2 for l in labels)
                                for p, e in rows if p["p"] is not None) if pts else None
    if not pts:
        return None, None
    B = collections.defaultdict(list)
    for c, y in pts:
        B[min(int(c * bins), bins - 1)].append((c, y))
    ece = sum(len(v) / len(pts) * abs(statistics.mean(c for c, _ in v) - statistics.mean(y for _, y in v))
              for v in B.values())
    return round(ece, 4), round(brier, 4)


def pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def task_metrics(task):
    cal, te = load_split(task, "cal"), load_split(task, "test")
    qk, q = question(te[0])
    labels = [True, False] if q["type"] == "noul" else list(q["criteria"])
    req = REQUIRED[task]
    gold_te = {r["id"]: r for r in te if "label:gold" in r["tags"]}
    gold_cal = {r["id"]: r for r in cal if "label:gold" in r["tags"]} or {r["id"]: r for r in cal}
    silver_te = {r["id"]: r for r in te if "label:silver" in r["tags"]}
    out = {"task": task, "type": q["type"], "required_precision": req, "n_gold_test": len(gold_te),
           "n_silver_test": len(silver_te), "systems": {}}
    for s in SYSTEMS:
        rows = read_jsonl(PREDS / task / f"{s}.jsonl")
        if not rows:
            continue
        P = {(r["split"], r["id"]): r for r in rows}
        te_rows = [(P[("test", i)], r["expected"][qk]) for i, r in gold_te.items() if ("test", i) in P]
        cal_rows = [(P[("cal", i)], r["expected"][qk]) for i, r in gold_cal.items() if ("cal", i) in P]
        if not te_rows:
            continue
        thr, _ = pick_threshold(cal_rows, q["type"], task, req)
        at = apply_threshold(te_rows, q["type"], task, thr)
        y = [e for _, e in te_rows]
        yhat = [p["label"] for p, _ in te_rows]
        valid = [i for i, v in enumerate(yhat) if v is not None]
        acc = sum(y[i] == yhat[i] for i in valid) / len(valid) if valid else None
        f1 = macro_f1([y[i] for i in valid], [yhat[i] for i in valid], labels) if valid else None
        acc_ci = boot_ci(lambda idx: sum(y[valid[j]] == yhat[valid[j]] for j in idx) / len(idx), len(valid)) \
            if valid else None
        f1_ci = boot_ci(lambda idx: macro_f1([y[valid[j]] for j in idx], [yhat[valid[j]] for j in idx], labels),
                        len(valid)) if valid else None
        ece, brier = ece_brier(te_rows, q["type"], labels)
        lat = [p.get("latency_ms") for p, _ in te_rows]
        cost = [p.get("cost_usd") or 0.0 for p, _ in te_rows]
        m = {"n": len(te_rows), "abstain": len(yhat) - len(valid), "accuracy": acc, "accuracy_ci": acc_ci,
             "macro_f1": f1, "macro_f1_ci": f1_ci, "ece": ece, "brier": brier,
             "threshold": None if thr == math.inf else thr, **at,
             "latency_p50_ms": pct(lat, .5), "latency_p95_ms": pct(lat, .95),
             "usd_per_1k": round(1000 * statistics.mean(cost), 2),
             "curve": curve(te_rows, q["type"], task)}
        if s in ("haiku", "sonnet", "opus"):
            m["wall_p50_ms"] = pct([p.get("wall_ms") for p, _ in te_rows], .5)
            m["model_id"] = next((p.get("model_id") for p, _ in te_rows if p.get("model_id")), None)
            m["input_tokens_p50"] = pct([p.get("input_tokens") for p, _ in te_rows], .5)
        if silver_te:
            srows = [(P[("test", i)], r["expected"][qk]) for i, r in silver_te.items() if ("test", i) in P]
            if srows:
                m["silver_accuracy"] = round(sum(p["label"] == e for p, e in srows) / len(srows), 3)
        par = PREDS / task / f"{s}.params.json"
        if par.exists():
            m.update(json.loads(par.read_text()))
        out["systems"][s] = m
    perm = read_jsonl(PREDS / task / "laya0-perm.jsonl")
    if perm:
        base = {r["id"]: r["label"] for r in read_jsonl(PREDS / task / "laya0.jsonl") if r["split"] == "test"}
        flips = [l != base[r["id"]] for r in perm if r["id"] in base for l in r["perm_labels"]]
        out["flip_rate"] = round(sum(flips) / len(flips), 3) if flips else None
    return out


def verdict(m, laya_device="mps"):
    """Phase 5 rules. Laya variant chosen on cal coverage, never on test."""
    task, req = m["task"], m["required_precision"]
    S = m["systems"]
    if m["n_gold_test"] < 40 or task in ("T2", "T4", "T6"):
        return {"verdict": "EXPLORATORY", "reason": f"{m['n_gold_test']} gold test items (< 40, S3)"}
    cands = [s for s in ("layaT", "laya-head") if s in S]
    best = max(cands, key=lambda s: (_cal_cov(task, s), S[s]["coverage"])) if cands else None
    b = S[best]
    heur = S.get("heuristic", {}).get("coverage")
    r1 = b["coverage"] >= 0.30 and (b["wilson_lb"] or 0) >= req - 0.05
    r2 = heur is None or b["coverage"] - heur >= 0.10
    r3 = (b["latency_p50_ms"] or 0) <= 1000 and LAYA_RSS_GB[laya_device] <= 3
    fails = [n for n, ok in (("rule 1: >=30% coverage with LB >= req-0.05", r1), ("rule 2: +10 pts over the heuristic", r2), ("rule 3: latency/RSS", r3)) if not ok]
    prec = "n/a" if b["precision"] is None else f"{b['precision']:.2f}"
    lb = "n/a" if b["wilson_lb"] is None else f"{b['wilson_lb']:.2f}"
    nums = (f"{best} covers {b['coverage']:.0%} at precision {prec} (LB {lb}) vs required {req:.2f}"
            + (f"; heuristic {heur:.0%}" if heur is not None else ""))
    claude = {s: S[s]["coverage"] for s in ("haiku", "sonnet", "opus") if s in S}
    if not fails:
        kind = "ADOPT (trained head)" if best == "laya-head" else "ADOPT (zero-shot + calibration)"
        role = "outright (within 10 pts of Haiku)" if claude.get("haiku") is None or \
            claude["haiku"] - b["coverage"] <= 0.10 else "as a pre-filter (Haiku covers >10 pts more)"
        return {"verdict": kind, "best": best, "reason": f"{nums}; adopt {role}"}
    tier = [s for s in ("haiku", "sonnet", "opus") if s in S and S[s]["coverage"] >= 0.30
            and (S[s]["wilson_lb"] or 0) >= req - 0.05]
    return {"verdict": "DROP", "best": best,
            "reason": f"fails {', '.join(fails)}: {nums}"
                      + (f". Cheapest Claude tier that passes: {tier[0]}" if tier else ". No Claude tier passes either")}


def _cal_cov(task, system):
    cal = load_split(task, "cal")
    qk, q = question(cal[0])
    P = {r["id"]: r for r in read_jsonl(PREDS / task / f"{system}.jsonl") if r["split"] == "cal"}
    rows = [(P[r["id"]], r["expected"][qk]) for r in cal if r["id"] in P]
    thr, _ = pick_threshold(rows, q["type"], task, REQUIRED[task])
    return apply_threshold(rows, q["type"], task, thr)["coverage"]


if __name__ == "__main__":
    for t in sys.argv[1:] or ["T1", "T3", "T5", "T2", "T4"]:
        if not (DATA / t).exists():
            continue
        m = task_metrics(t)
        print(t, json.dumps(verdict(m)))
        for s, v in m["systems"].items():
            print(f"  {s:10s} n={v['n']:4d} acc={v['accuracy'] if v['accuracy'] is None else round(v['accuracy'],3)} "
                  f"f1={v['macro_f1'] if v['macro_f1'] is None else round(v['macro_f1'],3)} ece={v['ece']} "
                  f"cov={v['coverage']:.2f} prec={v['precision']} lb={v['wilson_lb'] if v['wilson_lb'] is None else round(v['wilson_lb'],3)} "
                  f"thr={v['threshold']} p50={v['latency_p50_ms']} $1k={v['usd_per_1k']}")
        if "flip_rate" in m:
            print("  flip_rate", m["flip_rate"])
