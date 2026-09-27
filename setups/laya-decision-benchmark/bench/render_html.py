"""Render the aggregate report as a standalone HTML page (no CDN, no build step, aggregates only).

Tokens come from setups/claude-code-usage-stats/THEME.md (ground, panel, ink, muted, line, grid, chip,
accent). The two series hues are validated for this page with the dataviz validator (light on #ffffff,
dark on #1e2024: CVD ΔE ≥ 24, normal-vision ΔE ≥ 31, contrast ≥ 3:1). Family is the hue
(--s1 Claude, --s2 Laya, --muted heuristic); the variant is the line style, so identity is never
colour alone.
"""
import html
import json
import re

import markdown

NAMES = {"T1": "Duplicate of task X", "T2": "Triage action", "T3": "Handbook note type",
         "T4": "Task domain", "T5": "Related-note relevance"}
# system -> (css colour var, dasharray, label)
STYLE = {
    "heuristic": ("var(--muted)", "", "Existing rule"),
    "laya0": ("var(--s2)", "2 3", "Laya zero-shot"),
    "laya-head": ("var(--s2)", "", "Laya + trained head"),
    "haiku": ("var(--s1)", "2 3", "Haiku"),
    "sonnet": ("var(--s1)", "7 4", "Sonnet"),
    "opus": ("var(--s1)", "", "Opus"),
}
W, H, ML, MR, MT, MB = 440, 270, 44, 22, 14, 38


def _x(c):
    return ML + c * (W - ML - MR)


def _y(p):
    return MT + (1 - p) * (H - MT - MB)


def _downsample(curve, n=160):
    if len(curve) <= n:
        return curve
    step = len(curve) / n
    return [curve[int(i * step)] for i in range(n)] + [curve[-1]]


def chart(task, m):
    req = m["required_precision"]
    S = m["systems"]
    unit = "true positives auto-actioned" if m["type"] == "noul" else "items auto-decided"
    g = []
    for t in (0, .25, .5, .75, 1):
        g.append(f'<line class="grid" x1="{ML}" x2="{W-MR}" y1="{_y(t):.1f}" y2="{_y(t):.1f}"/>'
                 f'<text class="tick" x="{ML-6}" y="{_y(t)+3:.1f}" text-anchor="end">{t:.2f}</text>'
                 f'<text class="tick" x="{_x(t):.1f}" y="{H-MB+14}" '
                 f'text-anchor="{"end" if t == 1 else "start" if t == 0 else "middle"}">{t:.0%}</text>')
    g.append(f'<line class="axis" x1="{ML}" x2="{W-MR}" y1="{_y(0):.1f}" y2="{_y(0):.1f}"/>')
    g.append(f'<line class="req" x1="{ML}" x2="{W-MR}" y1="{_y(req):.1f}" y2="{_y(req):.1f}"/>'
             f'<text class="reqlabel" x="{W-MR-4}" y="{_y(req)-5:.1f}" text-anchor="end">required {req:.2f}</text>')
    data, dots = {}, []
    for s, (col, dash, label) in STYLE.items():
        v = S.get(s)
        if not v or not v["curve"]:
            continue
        pts = _downsample(v["curve"])
        data[s] = pts
        d = " ".join(f"{_x(c):.1f},{_y(p):.1f}" for c, p in pts)
        g.append(f'<polyline points="{d}" fill="none" stroke="{col}" stroke-width="2" '
                 f'stroke-dasharray="{dash}" stroke-linejoin="round"/>')
        if v["acted"] and v["precision"] is not None:
            dots.append(f'<circle cx="{_x(v["coverage"]):.1f}" cy="{_y(v["precision"]):.1f}" r="4.5" '
                        f'fill="{col}" class="op"><title>{label}: operating point chosen on cal — '
                        f'{v["coverage"]:.0%} coverage at {v["precision"]:.2f} precision on test</title></circle>')
    g += dots
    g.append(f'<line class="cross" x1="0" x2="0" y1="{MT}" y2="{H-MB}" visibility="hidden"/>')
    g.append(f'<text class="axlabel" x="{(ML+W-MR)/2:.0f}" y="{H-6}" text-anchor="middle">coverage ({unit})</text>')
    g.append(f'<text class="axlabel" transform="rotate(-90)" x="{-(MT+H-MB)/2:.0f}" y="11" '
             f'text-anchor="middle">precision</text>')
    labels = {s: STYLE[s][2] for s in data}
    tag = " · exploratory" if task in ("T2", "T4") else ""
    return (f'<figure class="chart" data-series=\'{html.escape(json.dumps({"pts": data, "labels": labels}))}\'>'
            f'<figcaption><strong>{task}</strong> {NAMES[task]}<span class="muted">{tag} · n gold test = '
            f'{m["n_gold_test"]}</span></figcaption><div class="plotwrap">'
            f'<svg viewBox="0 0 {W} {H}" style="width:100%;min-width:280px" role="img" '
            f'aria-label="{task} precision against coverage per system">{"".join(g)}</svg>'
            f'<div class="tip" hidden></div></div></figure>')


def legend():
    items = []
    for s, (col, dash, label) in STYLE.items():
        items.append(f'<span class="key"><svg width="26" height="10" aria-hidden="true"><line x1="1" x2="25" '
                     f'y1="5" y2="5" stroke="{col}" stroke-width="2" stroke-dasharray="{dash}"/></svg>{label}</span>')
    items.append('<span class="key"><svg width="26" height="10" aria-hidden="true"><line class="req" x1="1" '
                 'x2="25" y1="5" y2="5"/></svg>Required precision</span>')
    items.append('<span class="key"><svg width="14" height="12" aria-hidden="true"><circle cx="7" cy="6" r="4.5" '
                 'class="op" fill="var(--muted)"/></svg>Operating point (threshold picked on cal)</span>')
    return f'<div class="legend">{"".join(items)}</div>'


def body_html(md_text):
    out = markdown.markdown(md_text, extensions=["tables", "sane_lists"])
    return re.sub(r"<table>(.*?)</table>", r'<div class="tablewrap"><table>\1</table></div>', out, flags=re.S)


CSS = """
:root{
  --bg:#f6f5f2;--panel:#ffffff;--ink:#1c1b18;--muted:#66645c;--line:#e3e0d9;--grid:#ece9e2;--chip:#efeee9;
  --accent:#2f6690;--s1:#2a78d6;--s2:#eb6834;color-scheme:light dark}
@media (prefers-color-scheme:dark){:root{
  --bg:#16171a;--panel:#1e2024;--ink:#e9e7e2;--muted:#9a9791;--line:#2f3237;--grid:#2a2d32;--chip:#282b30;
  --accent:#7fb3d5;--s1:#3987e5;--s2:#d95926}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif}
main{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
.eyebrow{color:var(--muted);font-size:13px;letter-spacing:.02em}
h1{font-size:30px;line-height:1.2;margin:.2em 0 .3em}
h2{font-size:20px;margin:2.2em 0 .6em}
h3{font-size:16px;margin:1.8em 0 .5em}
.lede{color:var(--muted);max-width:70ch;margin:0}
.verdict{margin:24px 0;padding:18px 20px;background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--ink);border-radius:8px}
.verdict .badge{display:inline-block;font-weight:700;font-size:12px;letter-spacing:.06em;padding:2px 8px;border-radius:4px;background:var(--chip);margin-bottom:6px}
.verdict p{margin:.4em 0;max-width:80ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.tile .num{font-size:30px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.1}
.tile .lbl{color:var(--muted);font-size:13px;margin-top:4px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--muted);margin:8px 0 12px}
.key{display:inline-flex;align-items:center;gap:6px}
.charts{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:12px}
.chart{margin:0;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 12px 6px}
.chart figcaption{font-size:14px;margin-bottom:4px}
.muted{color:var(--muted);font-weight:400}
.plotwrap{position:relative;overflow-x:auto}
svg text{font-family:inherit}
.grid{stroke:var(--grid);stroke-width:1}
.axis{stroke:var(--line);stroke-width:1}
.tick,.axlabel{fill:var(--muted);font-size:10px}
.req{stroke:var(--ink);stroke-width:1;stroke-dasharray:4 3;opacity:.55}
.reqlabel{fill:var(--muted);font-size:10px}
.op{stroke:var(--panel);stroke-width:2}
.cross{stroke:var(--muted);stroke-width:1;opacity:.6}
.tip{position:absolute;top:8px;pointer-events:none;background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;line-height:1.5;box-shadow:0 2px 8px rgba(0,0,0,.12);white-space:nowrap}
.tip b{font-variant-numeric:tabular-nums}
.note{color:var(--muted);font-size:13px;max-width:80ch}
.report{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 24px 24px;margin-top:12px}
.report p,.report li{max-width:85ch}
.tablewrap{overflow-x:auto;margin:12px 0}
table{border-collapse:collapse;font-size:13px;font-variant-numeric:tabular-nums;min-width:100%}
th,td{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;background:var(--chip)}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--chip);padding:1px 4px;border-radius:4px;overflow-wrap:anywhere}
a{color:var(--accent)}
@media (max-width:600px){h1{font-size:24px}.report{padding:4px 14px 16px}}
"""

JS = """
document.querySelectorAll('.chart').forEach(fig=>{
  const d=JSON.parse(fig.dataset.series), svg=fig.querySelector('svg'), tip=fig.querySelector('.tip'),
        cross=svg.querySelector('.cross'), W=%d, ML=%d, MR=%d;
  const at=(pts,c)=>{let p=null;for(const [x,y] of pts){if(x<=c+1e-9)p=y;else break}return p};
  svg.addEventListener('pointermove',e=>{
    const r=svg.getBoundingClientRect(), vx=(e.clientX-r.left)/r.width*W,
          c=Math.min(1,Math.max(0,(vx-ML)/(W-ML-MR)));
    cross.setAttribute('x1',vx);cross.setAttribute('x2',vx);cross.setAttribute('visibility','visible');
    let rows=`<div>coverage <b>${Math.round(c*100)}%%</b></div>`;
    for(const s in d.pts){const p=at(d.pts[s],c);
      rows+=`<div>${d.labels[s]}: <b>${p==null?'–':p.toFixed(2)}</b></div>`}
    tip.innerHTML=rows;tip.hidden=false;
    const left=e.clientX-r.left; tip.style.left=(left>r.width/2?left-tip.offsetWidth-12:left+12)+'px';
  });
  svg.addEventListener('pointerleave',()=>{tip.hidden=true;cross.setAttribute('visibility','hidden')});
});
""" % (W, ML, MR)


def page(md_text, all_m, extra, tiles):
    body = md_text.split("\n## ", 1)[1] if "\n## " in md_text else md_text
    verdict_lines = [l for l in md_text.splitlines()[:3] if l.strip()]
    verdict = "".join(f"<p>{markdown.markdown(l)[3:-4]}</p>" for l in verdict_lines)
    tiles_html = "".join(f'<div class="tile"><div class="num">{html.escape(n)}</div>'
                         f'<div class="lbl">{html.escape(l)}</div></div>' for n, l in tiles)
    main_charts = "".join(chart(t, all_m[t]) for t in ("T1", "T3", "T5") if t in all_m)
    expl_charts = "".join(chart(t, all_m[t]) for t in ("T2", "T4") if t in all_m)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Laya Benchmark Report</title><style>{CSS}</style></head>
<body><main>
<div class="eyebrow">acs-agentic-setup · research · executed {html.escape(extra.get("date", ""))}</div>
<h1>Laya decision-model benchmark</h1>
<p class="lede">Can an open-weight, one-forward-pass decision model take triage and vault decisions off Claude at
the precision we require? Measured on our own human-labelled decisions, against the existing rules and Claude
Haiku, Sonnet and Opus.</p>
<section class="verdict"><span class="badge">VERDICT · DROP</span>{verdict}</section>
<div class="tiles">{tiles_html}</div>
<h2>Precision vs coverage</h2>
<p class="note">Each line sweeps a system's confidence threshold over the gold test split: further right means
more work automated, higher means fewer mistakes. A system is useful only where its line stays above the dashed
required-precision line. Dots mark the operating point actually used, with its threshold picked on the
calibration split and never tuned on test. Hover a chart to read every system at one coverage.</p>
{legend()}
<div class="charts">{main_charts}</div>
<h3>Exploratory (too few gold items for a verdict)</h3>
<div class="charts">{expl_charts}</div>
<h2>Full report</h2>
<div class="report">{body_html("## " + body)}</div>
</main><script>{JS}</script></body></html>
"""
