"""Laya variants: laya0 (zero-shot), laya0-perm (option-order flips), layaT (temperature-scaled on cal),
laya-head (frozen encoder + logistic-regression head).

Every variant returns rows shaped {"id", "label", "p", "latency_ms", "cost_usd"} where `p` is a
{label: prob} dict for `choice` and P(yes) for `noul`.
"""
import math
import os
import random
import time

import numpy as np

from common import question

_ROUTER = None
DEVICE = os.environ.get("LAYA_DEVICE", "mps")


def router():
    global _ROUTER
    if _ROUTER is None:
        import torch
        pc = int(os.popen("sysctl -n hw.perflevel0.physicalcpu").read())
        torch.set_num_threads(pc)
        torch.set_num_interop_threads(1)
        from laya import Router
        _ROUTER = Router(max_loaded=1, device=DEVICE)
        _ROUTER.load("english")
    return _ROUTER


def _one(r, questions=None):
    qk, q = question(r)
    q = questions or q
    t = time.perf_counter()
    ans = router().predict(r["state"]["text"], {qk: q}, model="english")["answers"][qk]
    ms = (time.perf_counter() - t) * 1000
    if q["type"] == "noul":
        p = float(ans["noul"])
        return {"id": r["id"], "label": p >= 0.5, "p": p, "latency_ms": ms, "cost_usd": 0.0}
    probs = {k: float(v) for k, v in ans["probabilities"].items()}
    return {"id": r["id"], "label": ans["choice"], "p": probs, "latency_ms": ms, "cost_usd": 0.0,
            "answer_confidence": float(ans.get("answer_confidence", max(probs.values())))}


def laya0(records):
    return [_one(r) for r in records]


def laya0_perm(records, seeds=(1, 2, 3)):
    out = []
    for r in records:
        qk, q = question(r)
        if q["type"] != "choice":
            continue
        labels = []
        for s in seeds:
            items = list(q["criteria"].items())
            random.Random(f"{s}:{r['id']}").shuffle(items)
            labels.append(_one(r, {**q, "criteria": dict(items)})["label"])
        out.append({"id": r["id"], "label": labels[0], "perm_labels": labels, "p": None,
                    "latency_ms": 0.0, "cost_usd": 0.0})
    return out


# ---- temperature scaling (model-agnostic: works on any probability output) ----------------------
def _as_matrix(preds, records):
    qk, q = question(records[0])
    if q["type"] == "noul":
        labels = [True, False]
        P = np.array([[p["p"], 1 - p["p"]] for p in preds])
    else:
        labels = list(q["criteria"])
        P = np.array([[p["p"].get(l, 0.0) for l in labels] for p in preds])
    y = np.array([labels.index(r["expected"][qk]) for r in records])
    return labels, np.clip(P, 1e-6, 1.0), y


def _scale(P, T):
    L = np.log(P) / T
    L -= L.max(axis=1, keepdims=True)
    E = np.exp(L)
    return E / E.sum(axis=1, keepdims=True)


def fit_temperature(cal_preds, cal_records):
    _, P, y = _as_matrix(cal_preds, cal_records)
    best = (math.inf, 1.0)
    for T in np.exp(np.linspace(math.log(0.05), math.log(20), 400)):
        nll = -np.mean(np.log(_scale(P, T)[np.arange(len(y)), y]))
        best = min(best, (nll, float(T)))
    return best[1]


def apply_temperature(preds, records, T):
    labels, P, _ = _as_matrix(preds, records)
    S = _scale(P, T)
    out = []
    for p, row in zip(preds, S):
        if labels == [True, False]:
            q = float(row[0])
            out.append({**p, "p": q, "label": q >= 0.5})
        else:
            d = {l: float(v) for l, v in zip(labels, row)}
            out.append({**p, "p": d, "label": max(d, key=d.get)})
    return out


# ---- laya-head: frozen encoder features + logistic regression ------------------------------------
def embed(records, batch=16):
    import torch
    agent = router().load("english")
    enc, tok = agent.model.encoder, agent.tok
    feats, lat = [], []
    with torch.no_grad():
        for i in range(0, len(records), batch):
            texts = [r["state"]["text"] for r in records[i:i + batch]]
            t = time.perf_counter()
            b = tok(texts, padding=True, truncation=True, max_length=512, return_tensors="pt").to(agent.device)
            h = enc(**b).last_hidden_state
            m = b["attention_mask"].unsqueeze(-1).to(h.dtype)
            feats.append(((h * m).sum(1) / m.sum(1)).float().cpu().numpy())
            lat += [(time.perf_counter() - t) * 1000 / len(texts)] * len(texts)
    return np.concatenate(feats), lat


def laya_head(train, cal, test):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    qk, q = question(train[0])
    Xtr, _ = embed(train)
    Xc, lc = embed(cal)
    Xte, lte = embed(test)
    sc = StandardScaler().fit(Xtr)
    ytr = [str(r["expected"][qk]) for r in train]
    yc = [str(r["expected"][qk]) for r in cal]
    best = None
    for C in (0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0):
        m = LogisticRegression(C=C, max_iter=5000).fit(sc.transform(Xtr), ytr)
        P = m.predict_proba(sc.transform(Xc))
        idx = [list(m.classes_).index(y) if y in m.classes_ else None for y in yc]
        nll = -np.mean([math.log(max(P[i, j], 1e-6)) if j is not None else math.log(1e-6)
                        for i, j in enumerate(idx)])
        if best is None or nll < best[0]:
            best = (nll, C, m)
    _, C, m = best

    def rows(X, recs, lat):
        P = m.predict_proba(sc.transform(X))
        out = []
        for r, row, ms in zip(recs, P, lat):
            d = {c: float(v) for c, v in zip(m.classes_, row)}
            if q["type"] == "noul":
                py = d.get("True", 0.0)
                out.append({"id": r["id"], "label": py >= 0.5, "p": py, "latency_ms": ms, "cost_usd": 0.0})
            else:
                full = {l: d.get(l, 0.0) for l in q["criteria"]}
                out.append({"id": r["id"], "label": max(full, key=full.get), "p": full, "latency_ms": ms,
                            "cost_usd": 0.0})
        return out

    cal_rows, test_rows = rows(Xc, cal, lc), rows(Xte, test, lte)
    T = fit_temperature(cal_rows, cal)
    return apply_temperature(cal_rows, cal, T), apply_temperature(test_rows, test, T), {"C": C, "T": T}
