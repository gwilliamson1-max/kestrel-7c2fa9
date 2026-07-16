"""Outputs: docs/signals.json (dashboard data) and reports/memo HTML (email body)."""
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _ranked(signals):
    scored = [s for s in signals if s.score]
    return sorted(scored, key=lambda s: s.score.get("viability", 0), reverse=True)


def write_signals_json(signals: list, path=None):
    path = Path(path or ROOT / "docs" / "signals.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated": date.today().isoformat(),
        "signals": [s.to_dict() for s in _ranked(signals)] +
                   [s.to_dict() for s in signals if not s.score],
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
        rows.append(f"""
        <tr><td colspan="6" style="background:#f0f2f5;font-weight:bold;
            padding:8px 6px">{i}. {s.product} — {s.event}
            <span style="float:right;color:#1a5276">viability {sc.get('viability')}/100
            · {sc.get('recommendation', '').upper()}</span></td></tr>
        <tr>
          <td>{s.source.upper()}</td>
          <td>{s.a} cases</td>
          <td>PRR {s.prr} (slope {s.prr_slope:+}/q)</td>
          <td>χ² {s.chi2}</td>
          <td>PubMed {pm.get('count', '?')}</td>
          <td>Dockets {lit.get('docket_hits', '?')} · warning {warn}</td>
        </tr>
        <tr><td colspan="6" style="padding:4px 6px;color:#333">
            PRR by quarter: {traj}<br>
            <i>{sc.get('rationale', '')}</i><br>
            Likely defendants: {', '.join(sc.get('likely_defendants') or []) or 'n/a'}
        </td></tr>""")

    html = f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;max-width:860px">
    <h2>Mass Tort Signal Memo — {today}</h2>
    <p>{len(signals)} signals passed the statistical screen; top
    {len(top)} by litigation-viability score below. Full detail in the dashboard.</p>
    <table cellspacing="0" cellpadding="4" style="border-collapse:collapse;width:100%;
        font-size:13px" border="1" bordercolor="#ddd">{''.join(rows)}</table>
    <p style="color:#888;font-size:11px">Generated automatically. Statistics are
    disproportionality screens, not causation. Attorney review required before
    any action.</p></body></html>"""

    path = Path(path or ROOT / "reports" / f"memo_{today}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    # stable name the email workflow reads
    (path.parent / "latest.html").write_text(html, encoding="utf-8")
    return path
