#!/usr/bin/env python3
"""Render cards/<task>.md from the private stats.json. Counts only; examples are hand-paraphrased so no raw
work content lands in the repo."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import DATA, SETUP  # noqa: E402

META = {
    "T1": ("Signal is a duplicate of task X", "noul", "1.00", [
        ("Signal: get PR #N reviewed and merged · Task: get PR #N reviewed and merged (CI green)", "yes"),
        ("Signal: resolve the sprint rollover in the state file · Task: resolve the sprint-NN rollover", "yes"),
        ("Signal: update Jira for ticket K · Task: ticket K — commit the fix, open the PR, update Jira", "no (hard negative)"),
        ("Signal: review PR #N · Task: do the tests-one-by-one review walkthrough for the PR", "no (hard negative)"),
        ("Signal: [label] add retry to the mail fetcher · Task: [label] add retry to the mail fetcher", "yes")]),
    "T2": ("Triage action", "choice (5)", "0.95 on add/move/duplicate/idea; dismiss never automatable", [
        ("A request to review a PR that the evidence shows was merged with an approved review", "dismiss"),
        ("A suggestion restating an open board task nearly verbatim", "duplicate"),
        ("Four signals about the same account-attribution problem, each adding a detail", "idea"),
        ("A 'switch the gh account back' housekeeping note", "dismiss"),
        ("A request for a research doc that already exists at the named path", "dismiss")]),
    "T3": ("Handbook note type", "choice (4)", "0.90", [
        ("E2E specs fail locally before the first assertion because an auth env var is missing", "gotcha"),
        ("We chose SQLite over a hosted DB for the registry because it is single-user", "decision"),
        ("Idea notes cannot be edited or deleted; every idea write is permanent", "invariant"),
        ("If the scheduled job keeps parking items, the review file will hit its guard", "risk"),
        ("A shared ticket label can span two unrelated undertakings; check the branch name", "gotcha")]),
    "T4": ("Task domain", "choice (5)", "0.90", [
        ("Review PR #N for the viewer service", "harrison"),
        ("Compare energy plans before the contract renews", "life_admin"),
        ("Finish module 3 of the course", "learning"),
        ("Prepare the roster for Sunday", "church"),
        ("Write the monthly summary for the committee", "jpc")]),
    "T5": ("Related-note relevance", "noul", "0.85", [
        ("A: ticket analysis note · B: the same ticket's implementation plan", "yes"),
        ("A: a site-naming decision · B: a module-provenance invariant in the same project", "no (suggested, not kept)"),
        ("A: a gotcha about wrangler auth · B: the handbook entry on account switching", "yes"),
        ("A: a course index · B: an unrelated sibling note in the same folder", "no (suggested, not kept)"),
        ("A: a verification plan · B: its execution log", "yes")]),
}


def main():
    for task, (title, qtype, req, examples) in META.items():
        p = DATA / task / "stats.json"
        if not p.exists():
            continue
        st = json.loads(p.read_text())
        rows = "\n".join(f"| {s} | {v['n']} | {v['gold']} | {v['silver']} | "
                         + ", ".join(f"{k}: {n}" for k, n in v["labels"].items()) + " |"
                         for s, v in st["splits"].items())
        cut = ", ".join(f"{s} {d[0]}→{d[1]}" for s, d in st["cut"].items() if d)
        extra = "\n".join(f"- **{k.replace('_', ' ')}:** {v}" for k, v in st.items()
                          if k in ("negatives", "gold_rule", "category_skipped", "provenance_note",
                                   "exploratory_reason", "label_rule", "label_quality", "split_note"))
        ex = "\n".join(f"{i}. {t} → **{l}**" for i, (t, l) in enumerate(examples, 1))
        (SETUP / "cards" / f"{task}.md").write_text(f"""# {task} — {title}

- **Question type:** {qtype}
- **Required precision:** {req}
- **Source:** {st['source']}
{extra}
- **Split:** by time (oldest 70% → train + cal, cal = last 20% of that window; newest 30% → test). Cuts: {cut}
- **Truncation rate:** {st['truncation_rate']:.1%} (head + tail to 300 tokens)
- **PHI gate drops:** {st['phi_dropped']}

| Split | n | gold | silver | Class balance |
|---|---|---|---|---|
{rows}

## Five example records (paraphrased)

{ex}
""")
        print("card", task)


if __name__ == "__main__":
    main()
