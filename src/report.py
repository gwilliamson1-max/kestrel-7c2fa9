"""Outputs: docs/signals.json (dashboard data) and reports/memo HTML (email body)."""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ranked(signals):
    scored = [s for s in signals if s.score]
    return sorted(scored, key=lambda s: s.score.get("viability", 0), reverse=True)


def _row(s):
    d = s.to_dict()
    r = getattr(s, "review", None)
    if r:
        d["review"] = r
    g = getattr(s, "generic_flag", None)
    if g:
        d["generic_flag"] = g
    p = getattr(s, "preemption_flag", None)
    if p:
        d["preemption_flag"] = p
    return d


def write_signals_json(signals: list, path=None):
    path = Path(path or ROOT / "docs" / "signals.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": date.today().isoformat(),
        "signals": [_row(s) for s in _ranked(signals)] +
                   [_row(s) for s in signals if not s.score],
    }
    path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return path


def write_memo_html(signals: list, cfg, path=None):
    """Self-contained HTML memo — used as the weekly email body."""
    top = _ranked(signals)[:cfg["thresholds"]["memo_top_n"]]
    today = date.today().isoformat()
    rows = []
    for i, s in enumerate(top, 1):
        sc = s.score
        lit = (s.enrichment or {}).get("litigation") or {}
        pm = (s.enrichment or {}).get("pubmed") or {}
        lbl = (s.enrichment or {}).get("label") or {}
        warn = {True: "in label", False: "NOT in label", None: "n/a"}[
            lbl.get("event_in_warnings")]
        traj = " → ".join(f"{q['prr']}" for q in (s.trajectory or []))
        review = getattr(s, "review", None)
        flag_line = f"\n            ⚑ REVIEW FLAG — {review['note']}\n" if review else ""
        gen = getattr(s, "generic_flag", None)
        if gen:
            vr = sc.get("viability_raw")
            adj = f" (raw {vr} → {sc.get('viability')})" if vr is not None else ""
            flag_line += f"\n            ⚑ GENERIC — preemption risk{adj}; {gen['note']}\n"
        pre = getattr(s, "preemption_flag", None)
        if pre:
            vr = sc.get("viability_raw")
            adj = f" (raw {vr} → {sc.get('viability')})" if vr is not None else ""
            flag_line += f"\n            ⚑ DEVICE PREEMPTION{adj}; {pre['note']}\n"
        rows.append(f"""
        \t{i}. {s.product} — {s.event}
            viability {sc.get('viability')}/100
            · {sc.get('recommendation', '').upper()}
\t{s.source.upper()}\t{s.a} cases\tPRR {s.prr} (slope {s.prr_slope:+}/q)\tχ² {s.chi2}\tPubMed {pm.get('count', '?')}\tDockets {lit.get('docket_hits', '?')} · warning {warn}
\t
            PRR by quarter: {traj}

            {sc.get('rationale', '')}
{flag_line}
            Likely defendants: {', '.join(sc.get('likely_defendants') or []) or 'n/a'}


""")

    html = f"""
    Mass Tort Signal Memo — {today}

    {len(signals)} signals passed the statistical screen; top
    {len(top)} by litigation-viability score below. Full detail in the dashboard.


{''.join(rows)}
    Generated automatically. Statistics are
    disproportionality screens, not causation. Attorney review required before
    any action.
"""

    path = Path(path or ROOT / "reports" / f"memo_{today}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    # stable name the email workflow reads
    (path.parent / "latest.html").write_text(html, encoding="utf-8")
    return path
