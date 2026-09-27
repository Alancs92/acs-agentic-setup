#!/usr/bin/env python3
"""Aggregate report: results/REPORT.md (committed, aggregates only) + ~/.cache/acs-laya-bench/report.html
(same content plus precision–coverage curves). Also writes results/metrics.json (aggregates only)."""
import html
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "systems"))
import metrics as M  # noqa: E402
from common import CACHE, DATA, SETUP  # noqa: E402

TASKS = ["T1", "T3", "T5", "T2", "T4"]
NAMES = {"T1": "Duplicate of task X", "T2": "Triage action", "T3": "Handbook note type",
         "T4": "Task domain", "T5": "Related-note relevance", "T6": "Broken link real/placeholder"}
CLAUDE = ("haiku", "sonnet", "opus")


def f(x, pct=False, nd=2):
    if x is None:
        return "–"
    return f"{x:.0%}" if pct else f"{x:.{nd}f}"


def cov_cell(s):
    if not s:
        return "not run"
    lb = "–" if s["wilson_lb"] is None else f"{s['wilson_lb']:.2f}"
    return f"{s['coverage']:.0%} (LB {lb})"


def build():
    all_m, verdicts = {}, {}
    for t in TASKS:
        if (DATA / t).exists():
            all_m[t] = M.task_metrics(t)
            verdicts[t] = M.verdict(all_m[t])
    return all_m, verdicts


def md(all_m, verdicts, extra):
    adopted = [t for t, v in verdicts.items() if v["verdict"].startswith("ADOPT")]
    L = []
    if adopted:
        L.append(f"**Laya is worth it for {', '.join(adopted)}** — wired in shadow mode; see the last section.")
    else:
        L.append("**Laya is not worth adopting: no task reached ADOPT on our own gold decisions.** Phase 6 skipped.")
    L.append(extra["headline2"])
    L.append(extra["headline3"])
    L += ["", "## Verdict table", "",
          "Coverage = share of the work auto-actioned at the task's required precision, threshold chosen on "
          "`cal` and applied to gold `test` (LB = Wilson 95% lower bound on precision at that point). For "
          "yes/no tasks coverage is the share of true positives auto-actioned.", "",
          "| Task | Req. prec. | Verdict | Best Laya | Laya coverage | Heuristic | Haiku | Sonnet | Opus |",
          "|---|---|---|---|---|---|---|---|---|"]
    for t in TASKS + ["T6"]:
        if t == "T6":
            L.append("| T6 Broken link | 0.95 | EXPLORATORY (not run) | – | – | – | – | – | – |")
            continue
        m, v = all_m[t], verdicts[t]
        S = m["systems"]
        best = v.get("best") or max((s for s in ("layaT", "laya-head") if s in S),
                                    key=lambda s: S[s]["coverage"], default=None)
        L.append(f"| {t} {NAMES[t]} | {m['required_precision']:.2f} | **{v['verdict']}** | {best or '–'} | "
                 f"{cov_cell(S.get(best))} | {cov_cell(S.get('heuristic')) if 'heuristic' in S else 'n/a'} | "
                 + " | ".join(cov_cell(S.get(c)) for c in CLAUDE) + " |")
    L += ["", "**Why, per task:**", ""]
    for t in TASKS:
        L.append(f"- **{t}** — {verdicts[t]['reason']}.")
    L.append("- **T6** — not run: history-derived labels are single-class (no broken link was ever fixed by "
             "creating its target in the snapshot window). See `cards/T6.md`.")
    L += ["", "## Per-task metrics (gold test)", ""]
    for t in TASKS:
        m = all_m[t]
        L += [f"### {t} — {NAMES[t]} (n gold test = {m['n_gold_test']}"
              + (f", silver = {m['n_silver_test']}" if m["n_silver_test"] else "") + ")", "",
              "| System | n | Accuracy [95% CI] | Macro-F1 [95% CI] | ECE | Brier | Threshold | Coverage | "
              "Precision (LB) | Abstain |", "|---|---|---|---|---|---|---|---|---|---|"]
        for s, v in m["systems"].items():
            ci = lambda a, c: "–" if a is None else f"{a:.3f} [{c[0]:.2f}, {c[1]:.2f}]"  # noqa: E731
            L.append(f"| {s} | {v['n']} | {ci(v['accuracy'], v['accuracy_ci'])} | {ci(v['macro_f1'], v['macro_f1_ci'])} | "
                     f"{f(v['ece'], nd=3)} | {f(v['brier'], nd=3)} | {f(v['threshold'], nd=3)} | {v['coverage']:.0%} | "
                     f"{f(v['precision'])} ({f(v['wilson_lb'])}) | {v['abstain']} |")
        if m.get("flip_rate") is not None:
            L.append(f"\nOption-order flip rate (laya0, 3 shuffles): **{m['flip_rate']:.0%}**.")
        sil = {s: v["silver_accuracy"] for s, v in m["systems"].items() if "silver_accuracy" in v}
        if sil:
            L.append("\nSilver-only accuracy (Claude-labelled, circular for Claude baselines): "
                     + ", ".join(f"{s} {a:.2f}" for s, a in sil.items()) + ".")
        L.append("")
    L += ["## Cost and latency per 1,000 decisions", "",
          "| System | p50 latency / decision | p95 | API-equivalent $ / 1k | Where it runs |", "|---|---|---|---|---|"]
    for s in ("laya0", "laya-head") + CLAUDE:
        lat = [all_m[t]["systems"][s] for t in ("T1", "T3", "T5") if s in all_m[t]["systems"]]
        if not lat:
            continue
        p50 = sorted(v["latency_p50_ms"] or 0 for v in lat)[len(lat) // 2]
        p95 = max(v["latency_p95_ms"] or 0 for v in lat)
        usd = sum(v["usd_per_1k"] for v in lat) / len(lat)
        where = "MacBook (MPS), $0 marginal" if s.startswith("laya") else "Claude subscription (notional $)"
        L.append(f"| {s} | {p50:.0f} ms | {p95:.0f} ms | ${usd:.2f} | {where} |")
    L += ["", "Claude latency is the envelope `duration_ms`; CLI start-up overhead adds "
          f"~{extra['cli_overhead_ms']:.0f} ms wall per call. Per-call input overhead (system prompt, "
          f"cached) ≈ {extra['input_tokens']:.0f} tokens. Laya peak RSS: 1.38 GB on MPS, 2.39 GB on CPU "
          "(`results/microbench.json`).", "",
          "## Calibration", "", "| Task | laya0 ECE | layaT ECE | fitted T | laya-head ECE |", "|---|---|---|---|---|"]
    for t in TASKS:
        S = all_m[t]["systems"]
        L.append(f"| {t} | {f(S.get('laya0', {}).get('ece'), nd=3)} | {f(S.get('layaT', {}).get('ece'), nd=3)} | "
                 f"{f(S.get('layaT', {}).get('T'), nd=2)} | {f(S.get('laya-head', {}).get('ece'), nd=3)} |")
    L += ["", "## Option-order flip rate", "",
          "Share of (item, shuffle) pairs where zero-shot Laya changed its answer when only the option order "
          "changed: " + ", ".join(f"{t} {all_m[t]['flip_rate']:.0%}" for t in TASKS
                                  if all_m[t].get("flip_rate") is not None) + ".", ""]
    L += extra["sections"]
    return "\n".join(L) + "\n"


def svg_curves(m):
    W, H, P = 360, 220, 34
    colors = {"heuristic": "#8a8a8a", "laya0": "#c98a2b", "layaT": "#e0a030", "laya-head": "#b5541c",
              "haiku": "#3b7dd8", "sonnet": "#2a5aa8", "opus": "#18336b"}
    parts = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="precision coverage curves">',
             f'<rect x="{P}" y="10" width="{W-P-10}" height="{H-P-10}" fill="none" stroke="var(--line)"/>']
    req = m["required_precision"]
    y = lambda p: 10 + (1 - p) * (H - P - 10)  # noqa: E731
    x = lambda c: P + c * (W - P - 10)  # noqa: E731
    parts.append(f'<line x1="{P}" x2="{W-10}" y1="{y(req)}" y2="{y(req)}" stroke="var(--accent)" stroke-dasharray="4 3"/>')
    for s, v in m["systems"].items():
        if s not in colors or not v["curve"]:
            continue
        pts = " ".join(f"{x(c):.1f},{y(p):.1f}" for c, p in v["curve"])
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{colors[s]}" stroke-width="1.6"><title>{s}</title></polyline>')
    parts.append(f'<text x="{P}" y="{H-8}" font-size="10" fill="var(--muted)">coverage 0 → 1</text>')
    parts.append(f'<text x="2" y="18" font-size="10" fill="var(--muted)">prec</text>')
    legend = " ".join(f'<span style="color:{c}">■ {s}</span>' for s, c in colors.items() if s in m["systems"])
    return "".join(parts) + "</svg>" + f'<div class="legend">{legend} <span style="color:var(--accent)">- - required</span></div>'


def render_html(md_text, all_m):
    body = []
    for line in md_text.splitlines():
        body.append(html.escape(line))
    curves = "".join(f"<section><h3>{t} — {NAMES[t]}</h3>{svg_curves(all_m[t])}</section>" for t in TASKS)
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laya Benchmark Report</title><style>
:root{{--bg:#fbfaf7;--fg:#1d1c1a;--muted:#6b675f;--line:#d9d4ca;--accent:#b5541c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161513;--fg:#ece8e1;--muted:#a39e94;--line:#3a3833;--accent:#e08a50}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px}}
pre{{white-space:pre-wrap;font:13px/1.45 ui-monospace,Menlo,monospace}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}
.legend{{font-size:12px;color:var(--muted)}} h3{{margin:.2em 0}}
</style></head><body><h1>Laya decision-model benchmark</h1>
<h2>Precision–coverage curves (gold test)</h2><div class="grid">{curves}</div>
<h2>Report</h2><pre>{chr(10).join(body)}</pre></body></html>"""


def main(extra_path=None):
    all_m, verdicts = build()
    extra = json.loads(pathlib.Path(extra_path).read_text()) if extra_path else {
        "headline2": "", "headline3": "", "sections": [], "cli_overhead_ms": 0, "input_tokens": 0}
    c = [v for t in all_m for s, v in all_m[t]["systems"].items() if s in CLAUDE]
    if c:
        extra["cli_overhead_ms"] = sorted((v.get("wall_p50_ms") or 0) - (v.get("latency_p50_ms") or 0) for v in c)[len(c) // 2]
        extra["input_tokens"] = sorted(v.get("input_tokens_p50") or 0 for v in c)[len(c) // 2]
    text = md(all_m, verdicts, extra)
    (SETUP / "results").mkdir(exist_ok=True)
    (SETUP / "results" / "REPORT.md").write_text("# Laya decision-model benchmark — results\n\n" + text)
    agg = {t: {"verdict": verdicts[t], **{k: v for k, v in m.items() if k != "systems"},
               "systems": {s: {k: v for k, v in sv.items() if k != "curve"} for s, sv in m["systems"].items()}}
           for t, m in all_m.items()}
    (SETUP / "results" / "metrics.json").write_text(json.dumps(agg, indent=2, default=str))
    (CACHE / "report.html").write_text(render_html(text, all_m))
    print(SETUP / "results" / "REPORT.md", CACHE / "report.html")
    for t, v in verdicts.items():
        print(t, v["verdict"], "—", v["reason"])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
