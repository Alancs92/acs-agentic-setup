#!/usr/bin/env python3
"""Build the benchmark datasets (one builder per task) into ~/.cache/acs-laya-bench/data/<task>/.

Strictly read-only against every source: vault files, the vault's git history, the task-manager
JSON backups and fixtures. Never talks to the live board (stop condition S4).

    python bench/build_datasets.py [--tasks T1,T3] [--vault PATH] [--tm PATH]
"""
import argparse
import collections
import hashlib
import json
import os
import pathlib
import random
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import DATA, head_tail, phi_hit, runlog, validate_record, write_jsonl  # noqa: E402

HOME = pathlib.Path.home()
PRE = json.load(open(pathlib.Path(__file__).resolve().parent.parent / "preflight.json")) \
    if (pathlib.Path(__file__).resolve().parent.parent / "preflight.json").exists() else {}


# ---- shared ------------------------------------------------------------------------------------
def time_split(items, key):
    """Oldest 70% -> train+cal (cal = last 20% of that window), newest 30% -> test."""
    items = sorted(items, key=key)
    n = len(items)
    n_test = round(n * 0.3)
    older, test = items[: n - n_test], items[n - n_test:]
    n_cal = round(len(older) * 0.2)
    train, cal = older[: len(older) - n_cal], older[len(older) - n_cal:]
    return {"train": train, "cal": cal, "test": test}


def cut_dates(splits, key):
    return {s: [key(v[0]), key(v[-1])] if v else None for s, v in splits.items()}


def finish(task, records_by_split, meta):
    """PHI-gate, truncate-once-checked, validate, write. Returns card stats."""
    stats = {"task": task, **meta, "splits": {}, "phi_dropped": 0}
    trunc = total = 0
    for split, recs in records_by_split.items():
        kept = []
        for r in recs:
            blob = json.dumps(r["state"], ensure_ascii=False)
            if phi_hit(blob):
                stats["phi_dropped"] += 1
                continue
            trunc += "truncated" in r["tags"]
            total += 1
            validate_record(r)
            kept.append(r)
        write_jsonl(DATA / task / f"{split}.jsonl", kept)
        lab = collections.Counter(str(list(r["expected"].values())[0]) for r in kept)
        src = collections.Counter("gold" if "label:gold" in r["tags"] else "silver" for r in kept)
        stats["splits"][split] = {"n": len(kept), "gold": src["gold"], "silver": src["silver"],
                                  "labels": dict(sorted(lab.items()))}
    stats["truncation_rate"] = round(trunc / total, 3) if total else 0
    (DATA / task / "stats.json").write_text(json.dumps(stats, indent=2))
    runlog(f"build {task}: " + json.dumps({k: v["n"] for k, v in stats["splits"].items()})
           + f" gold-test={stats['splits']['test']['gold']} trunc={stats['truncation_rate']} "
           f"phi_dropped={stats['phi_dropped']}")
    return stats


def rec(rid, text, qk, q, exp, gold, ts, src, extra_tags=()):
    t, was = head_tail(text)
    tags = ["label:gold" if gold else "label:silver", f"src:{src}", *extra_tags]
    if was:
        tags.append("truncated")
    return {"id": rid, "state": {"text": t}, "questions": {qk: q}, "expected": {qk: exp},
            "tags": tags, "ts": ts[:10]}


def hid(*parts):
    return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:10]


def strip_frontmatter(t):
    return re.sub(r"\A---\n.*?\n---\n", "", t, flags=re.S)


def git_first_add_dates(repo):
    """path -> first date the path was added (no rename following; migration-era adds tie)."""
    out = subprocess.run(["git", "-C", str(repo), "log", "--reverse", "--diff-filter=A",
                          "--name-only", "--format=@@%ad", "--date=short"],
                         capture_output=True, text=True, check=True).stdout
    first, cur = {}, None
    for line in out.splitlines():
        if line.startswith("@@"):
            cur = line[2:]
        elif line.strip() and line not in first:
            first[line] = cur
    return first


# ---- T1: signal is a duplicate of task X (noul) ------------------------------------------------
Q_T1 = {"type": "noul", "instructions": "Is the signal asking for the same work as this existing task?"}


def build_t1(vault, tm):
    sys.path.insert(0, str(tm / "tide"))
    from match import compare  # the matcher's own score = the heuristic baseline
    bk = vault / "Projects/acs-task-manager/backups"
    S = {s["id"]: s for s in json.load(open(bk / "2026-08-26-pre-purge-signals.json"))}
    T = json.load(open(bk / "2026-08-26-pre-purge-tasks.json"))
    fx = json.load(open(tm / "tools/fixtures/backtest-1127-1246.json"))
    dup = {int(k): (v if isinstance(v, list) else [v]) for k, v in fx["duplicates"].items()}
    # Only signals with a confirmed target. `no_task` signals were mostly dismissed because the work was
    # DONE, not because it differs from the task, so "not the same work" is not a safe label for them.
    sig_ids = sorted(dup)
    # Tasks created from these runs' own reviews (T08-0xx, 2026-08-19+) are the signal restated: leakage.
    T = [t for t in T if t["created_at"][:10] < "2026-08-19"]

    def sig_text(s):
        d = json.loads(s["detail"]) if isinstance(s["detail"], str) else s["detail"]
        return d.get("task", ""), d.get("evidence", "")

    def task_text(t):
        return t["text"] + (f" — {t['description']}" if t.get("description") else "")

    splits = time_split(sig_ids, key=lambda i: (S[i]["ts"], i))
    out = {}
    for split, ids in splits.items():
        recs = []
        for i in ids:
            sug, ev = sig_text(S[i])
            positives = set(dup.get(i, []))
            byid = {t["id"]: t for t in T}
            # A board near-copy of the confirmed target is ambiguous, not a negative (the board held
            # duplicate tasks; the human picked one). Drop candidates within Jaccard 0.5 of any target.
            scored = sorted(((compare(sug, t["text"]), t["id"]) for t in T if t["id"] not in positives
                             and all(compare(t["text"], byid[p]["text"]) < 0.5 for p in positives if p in byid)),
                            reverse=True)
            negs = [tid for _, tid in scored[:3]]
            positives = {p for p in positives if p in byid}
            for tid, lab in [(p, True) for p in sorted(positives)] + [(n, False) for n in negs]:
                t = byid[tid]
                text = f"Signal: {sug}\nEvidence: {ev}\n\nExisting task: {task_text(t)}"
                r = rec(f"t1-{i}-{tid}", text, "same_work", Q_T1, lab, True, S[i]["ts"], "backtest-fixture",
                        [f"sig:{i}"])
                r["heuristic"] = round(compare(sug, t["text"]), 4)
                recs.append(r)
        out[split] = recs
    return finish("T1", out, {"cut": cut_dates(splits, lambda i: S[i]["ts"][:10]),
                              "source": "tools/fixtures/backtest-1127-1246.json + pre-purge backups",
                              "split_note": "all signals were captured on 2026-07-31, so the time order is the signal id (ids are assigned in capture order); pairs never straddle splits",
                              "negatives": "top-3 non-matching tasks by matcher Jaccard (hard negatives), excluding near-copies (Jaccard >= 0.5) of the confirmed target and tasks created 2026-08-19+ (restated signals)"})


# ---- T2: triage action (choice, 5) — runs 4–5 only, the only runs whose signal text survives ---
Q_T2 = {"type": "choice", "instructions": "What should the triage agent do with this signal?",
        "criteria": {"dismiss": "not worth tracking, already done, or in-flight housekeeping",
                     "duplicate": "the work is already covered by an existing board task",
                     "move": "an existing task's status/column should change",
                     "add": "genuinely new work that deserves a new board task",
                     "idea": "a note or sub-idea to attach to an existing task"}}


def build_t2(vault, tm):
    bk = vault / "Projects/acs-task-manager/backups"
    S = {s["id"]: s for s in json.load(open(bk / "2026-08-26-pre-purge-signals.json"))}
    fx = json.load(open(tm / "tools/fixtures/backtest-1127-1246.json"))
    # Labels from decisions-log run 4 + run 5 entries (itemised ids only; unitemised ids dropped).
    auto = {**{int(k): "duplicate" for k in fx["duplicates"]},
            **{i: "idea" for i in (1234, 1235, 1236, 1237)},
            **{i: "dismiss" for i in (1153, 1171, 1191, 1200, 1212, 1226, 1238, 1239)}}
    review = {1164: "dismiss", **{i: "dismiss" for i in (1210, 1217, 1225, 1241, 1243, 1245, 1246)}}
    labels = {**auto, **review}
    ids = sorted(labels)
    splits = time_split(ids, key=lambda i: (S[i]["ts"], i))
    out = {}
    for split, sids in splits.items():
        out[split] = []
        for i in sids:
            d = json.loads(S[i]["detail"]) if isinstance(S[i]["detail"], str) else S[i]["detail"]
            text = f"Signal: {d.get('task', '')}\nEvidence: {d.get('evidence', '')}"
            out[split].append(rec(f"t2-{i}", text, "action", Q_T2, labels[i], i in review, S[i]["ts"],
                                  "decisions-log-run4-5"))
    return finish("T2", out, {"cut": cut_dates(splits, lambda i: S[i]["ts"][:10]),
                              "source": "decisions-log runs 4–5 itemised outcomes; auto side = silver",
                              "exploratory_reason": "only 8 review-side (gold) signals survive the "
                                                    "2026-08-26 purge; live D1 unreachable"})


# ---- T3: handbook note type (choice, 4) ---------------------------------------------------------
Q_T3 = {"type": "choice", "instructions": "What kind of handbook note is this?",
        "criteria": {"gotcha": "a trap or surprising behaviour that bit someone, and how to avoid it",
                     "decision": "a choice that was made between alternatives, and why",
                     "risk": "something that could go wrong in future and is being watched",
                     "invariant": "a rule that must always hold; breaking it is a bug"}}


def build_t3(vault, tm):
    first = git_first_add_dates(vault)
    items = []
    for p in vault.rglob("*.md"):
        rel = str(p.relative_to(vault))
        if "/." in rel or "node_modules" in rel:
            continue
        raw = p.read_text(errors="ignore")
        m = re.match(r"---\n(.*?)\n---", raw, re.S)
        if not m:
            continue
        tm_ = re.search(r"^type:\s*[\"']?(\w+)", m.group(1), re.M)
        if not tm_ or tm_.group(1) not in Q_T3["criteria"]:
            continue
        body = strip_frontmatter(raw)
        body = re.sub(r"^#+\s*Related.*", "", body, flags=re.S | re.M | re.I)
        # Template sub-headings ("## Context / Symptom", "## Decision") are chosen BY the type, so they leak it.
        body = re.sub(r"^#{2,}\s.*$", "", body, flags=re.M)
        body = re.sub(r"\n{3,}", "\n\n", body)
        title = re.search(r"^title:\s*(.+)$", m.group(1), re.M)
        title = title.group(1).strip("\"' ") if title else p.stem.replace("-", " ")
        date = (re.search(r"^(?:created|date):\s*(\d{4}-\d{2}-\d{2})", m.group(1), re.M) or [None, None])[1] \
            or first.get(rel) or "2026-01-01"
        items.append((date, rel, title, body, tm_.group(1), "/handbook/" in rel))
    splits = time_split(items, key=lambda x: (x[0], x[1]))
    def head250(body):  # the spec's "first ~250 tokens of body" is a design cut, not truncation
        from common import tokenizer
        ids = tokenizer().encode(body.strip(), add_special_tokens=False)[:250]
        return tokenizer().decode(ids)

    out = {s: [rec(f"t3-{hid(x[1])}", f"{x[2]}\n\n{head250(x[3])}", "note_type", Q_T3, x[4], x[5],
                   x[0], "vault-handbook") for x in v] for s, v in splits.items()}
    return finish("T3", out, {"cut": cut_dates(splits, lambda x: x[0]),
                              "source": "vault notes with frontmatter type in {gotcha,decision,risk,invariant}; "
                                        "frontmatter and folder path stripped; date = frontmatter or git first-add"})


# ---- T4: task domain (choice) -------------------------------------------------------------------
DOMAINS = {"harrison": "day job at Harrison.ai: engineering tickets, PRs, platform and product work",
           "life_admin": "personal admin: bills, accounts, home, personal projects and tooling",
           "learning": "courses, study and learning goals",
           "church": "church community, ministry and events",
           "jpc": "JPC commitments"}
Q_T4 = {"type": "choice", "instructions": "Which area of life does this task belong to?", "criteria": DOMAINS}


def build_t4(vault, tm):
    bk = vault / "Projects/acs-task-manager/backups"
    T = [t for t in json.load(open(bk / "2026-08-26-pre-purge-tasks.json")) if t.get("domain") in DOMAINS]
    E = json.load(open(bk / "2026-08-26-pre-purge-events.json"))
    revisited = {e["task_id"] for e in E if e["action"] in ("done", "move", "prio", "edit", "idea")}
    splits = time_split(T, key=lambda t: (t["created_at"], t["id"]))
    out = {s: [rec(f"t4-{t['id']}", t["text"] + (f"\n{t['description']}" if t.get("description") else ""),
                   "domain", Q_T4, t["domain"], t["id"] in revisited, t["created_at"], "pre-purge-tasks")
               for t in v] for s, v in splits.items()}
    return finish("T4", out, {"cut": cut_dates(splits, lambda t: t["created_at"][:10]),
                              "source": "2026-08-26 pre-purge task dump (live D1 unreachable from this account)",
                              "gold_rule": "task revisited later (done/move/prio/edit/idea event) with its domain unchanged",
                              "category_skipped": "`category` is a kanban column (next/backlog/in_progress…), a "
                                                  "state over time, not a property of the text"})


# ---- T5: related-note relevance (noul) ----------------------------------------------------------
Q_T5 = {"type": "noul", "instructions": "Should note B be listed as a related note of note A?"}


def build_t5(vault, tm, max_notes=220, seed=7):
    sys.path.insert(0, str(vault / "Projects/claude-skills/vault-related/scripts"))
    import related_suggest as rs
    md = rs.discover(str(vault))
    by_stem = collections.defaultdict(list)
    for m in md:
        by_stem[os.path.splitext(os.path.basename(m))[0].lower()].append(m)
    first = git_first_add_dates(vault)
    rel_re = re.compile(r"^##\s+Related\s*$(.*?)(?=^##\s|\Z)", re.S | re.M)

    def desc(rel):
        raw = strip_frontmatter(open(vault / rel, errors="ignore").read())
        raw = rel_re.sub("", raw)
        raw = re.sub(r"\[\[([^\]|#]+)(?:[^\]]*)\]\]", lambda m: os.path.basename(m.group(1)), raw)
        return re.sub(r"\s+", " ", raw).strip()[:700]

    def heur(target, cand):
        orig = rs.existing_links
        rs.existing_links = lambda v, r: set()
        try:
            for c, sc, _ in rs.suggest_for(str(vault), target, [cand], 1):
                return sc
            return 0
        finally:
            rs.existing_links = orig

    notes = []
    for m in md:
        txt = open(vault / m, errors="ignore").read()
        sec = rel_re.search(txt)
        if not sec:
            continue
        links = []
        for l in re.findall(r"\[\[([^\]]+)\]\]", sec.group(1)):
            stem = os.path.basename(l.split("|")[0].split("#")[0].strip()).lower().removesuffix(".md")
            if len(by_stem.get(stem, [])) == 1 and by_stem[stem][0] != m:
                links.append(by_stem[stem][0])
        if links:
            notes.append((first.get(m, "2026-01-01"), m, links))
    random.Random(seed).shuffle(notes)
    notes = notes[:max_notes]
    rng = random.Random(seed)
    pairs = []
    for date, m, links in notes:
        pos = rng.sample(links, min(2, len(links)))
        negs = [c for c, _, _ in rs.suggest_for(str(vault), m, md, 6)][:2]
        for c, lab in [(p, True) for p in pos] + [(n, False) for n in negs]:
            pairs.append((date, m, c, lab))
    splits = time_split(pairs, key=lambda x: (x[0], x[1]))
    out = {}
    for s, v in splits.items():
        out[s] = []
        for date, a, b, lab in v:
            text = f"Note A: {os.path.basename(a)[:-3]}\n{desc(a)[:420]}\n\nNote B: {os.path.basename(b)[:-3]}\n{desc(b)[:300]}"
            r = rec(f"t5-{hid(a, b)}", text, "related", Q_T5, lab, True, date, "vault-related")
            r["heuristic"] = heur(a, b)
            out[s].append(r)
    return finish("T5", out, {"cut": cut_dates(splits, lambda x: x[0]),
                              "source": "curated `## Related` sections (positives) vs related_suggest top "
                                        "candidates not linked (negatives); Related section stripped from state",
                              "provenance_note": "Related sections were written by the vault-related skill "
                                                 "and kept by Alan; treated as gold per the runbook"})


# ---- T6: broken wikilink real vs placeholder (noul) ---------------------------------------------
Q_T6 = {"type": "noul", "instructions": "Is this wikilink meant to point at a real note (as opposed to a "
                                        "template placeholder or documentation example)?"}


def build_t6(vault, tm, snapshots=("2026-08-10", "2026-08-24", "2026-09-07", "2026-09-14")):
    sys.path.insert(0, str(vault / "Projects/claude-skills/vault-maintenance/scripts"))
    import vault_audit as va

    def at(rev, path):
        p = subprocess.run(["git", "-C", str(vault), "show", f"{rev}:{path}"], capture_output=True, text=True)
        return p.stdout if p.returncode == 0 else None

    def md_at(rev):
        out = subprocess.run(["git", "-C", str(vault), "ls-tree", "-r", "--name-only", rev],
                             capture_output=True, text=True, check=True).stdout.split("\n")
        return [p for p in out if p.endswith(".md")]

    head_md = md_at("HEAD")
    hb, hr = va.build_indexes(head_md)
    seen = {}
    for day in snapshots:
        rev = subprocess.run(["git", "-C", str(vault), "rev-list", "-1", f"--before={day}T23:59", "HEAD"],
                             capture_output=True, text=True).stdout.strip()
        if not rev:
            continue
        mds = md_at(rev)
        b, r = va.build_indexes(mds)
        for p in mds:
            txt = at(rev, p)
            if not txt:
                continue
            for m in va.WIKILINK_RE.finditer(va.strip_code(txt)):
                raw = m.group(1)
                if va.resolve(raw, b, r) or (p, raw) in seen:
                    continue
                s, e = max(0, m.start() - 160), m.end() + 160
                seen[(p, raw)] = (day, txt[s:e])
    items = []
    for (p, raw), (day, ctx) in seen.items():
        now = at("HEAD", p)
        if now is None:
            continue
        if raw in now:
            # target created later -> real; still unresolved at HEAD -> left as a template
            # (the runbook's placeholder rule; noisy, since some are real breaks nobody fixed)
            lab = bool(va.resolve(raw, hb, hr))
        else:
            lab = None               # link text gone: fixed (real) or deleted (placeholder) — ambiguous
            continue
        items.append((day, p, raw, ctx, lab))
    splits = time_split(items, key=lambda x: (x[0], x[1]))
    out = {s: [dict(rec(f"t6-{hid(x[1], x[2])}", f"Link: [[{x[2]}]]\nContext: {x[3]}", "real",
                        Q_T6, x[4], True, x[0], "vault-git-history"),
                    heuristic=0.0 if va.is_placeholder(x[2]) else 1.0) for x in v] for s, v in splits.items()}
    return finish("T6", out, {"cut": cut_dates(splits, lambda x: x[0]),
                              "source": f"broken links at vault snapshots {snapshots}, labelled at HEAD",
                              "label_rule": "real = target exists at HEAD; placeholder = link still unresolved at HEAD; "
                                            "removed links dropped as ambiguous (fixed vs deleted)",
                              "label_quality": "history-derived, not a human decision per link"})


BUILDERS = {"T1": build_t1, "T2": build_t2, "T3": build_t3, "T4": build_t4, "T5": build_t5, "T6": build_t6}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=",".join(BUILDERS))
    ap.add_argument("--vault", default=PRE.get("paths", {}).get("vault", str(HOME / "repos/textx-v2")))
    ap.add_argument("--tm", default=PRE.get("paths", {}).get("task_manager", str(HOME / "repos/acs-task-manager")))
    a = ap.parse_args()
    for t in a.tasks.split(","):
        st = BUILDERS[t](pathlib.Path(a.vault).expanduser(), pathlib.Path(a.tm).expanduser())
        print(t, json.dumps({k: v for k, v in st["splits"].items()}), "trunc", st["truncation_rate"],
              "phi_dropped", st["phi_dropped"])


if __name__ == "__main__":
    main()
