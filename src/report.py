"""Outputs: docs/signals.json (dashboard data) and reports/memo HTML (email body)."""
import html as _htmllib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD_URL = "https://gwilliamson1-max.github.io/kestrel-7c2fa9/"

REC_COLOR = {"pursue": "#1e8449", "monitor": "#b7950b", "pass": "#909497"}


def _esc(t):
    return _htmllib.escape(str(t if t is not None else ""))


def _ranked(signals):
    scored = [s for s in signals if s.score]
    return sorted(scored, key=lambda s: s.score.get("viability", 0), reverse=True)


def _ranked_mixed(signals, cfg):
    """Memo selection with a per-source quota. EB05/viability are not comparable
    across FAERS and MAUDE (device expected counts are structurally tiny), so a
    global sort lets devices crowd drugs out entirely. Take the top N from each
    source separately, then order the combined set by viability."""
    mix = (cfg or {}).get("source_mix", {}) or {}
    want = {"faers": int(mix.get("memo_faers", 7)),
            "maude": int(mix.get("memo_maude", 3))}
    picked = []
    for src, k in want.items():
        picked += [s for s in _ranked(signals) if s.source == src][:k]
    # backfill from anything left if a source came up short
    total = int(cfg["thresholds"]["memo_top_n"])
    if len(picked) < total:
        rest = [s for s in _ranked(signals) if s not in picked]
        picked += rest[:total - len(picked)]
    return sorted(picked, key=lambda s: s.score.get("viability", 0), reverse=True)


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
    dv = getattr(s, "device_flag", None)
    if dv:
        d["device_flag"] = dv
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


def _flag_blocks(s, sc):
    """Coloured review / generic / preemption flag blocks (email-safe HTML)."""
    out = []
    review = getattr(s, "review", None)
    if review:
        out.append(("#7d3c98", "REVIEW", review.get("note", "")))
    gen = getattr(s, "generic_flag", None)
    if gen:
        out.append(("#7d3c98", "GENERIC · PREEMPTION RISK", gen.get("note", "")))
    pre = getattr(s, "preemption_flag", None)
    if pre:
        out.append(("#922b21", "PMA · PREEMPTION", pre.get("note", "")))
    dv = getattr(s, "device_flag", None)
    if dv:
        out.append(("#b9770e", "KNOWN COMPLICATION", dv.get("note", "")))
    html = ""
    for color, label, note in out:
        html += (f'<div style="margin:8px 0;padding:8px 10px;border-left:4px solid '
                 f'{color};background:#f6f0f4;font-size:13px;color:#333;line-height:1.5">'
                 f'<b style="color:{color}">&#9873; {label}</b> &mdash; {_esc(note)}</div>')
    return html


def write_memo_html(signals: list, cfg, path=None):
    """Self-contained, email-safe HTML memo (inline styles only)."""
    top = _ranked_mixed(signals, cfg)
    today = date.today().isoformat()
    cards = []
    for i, s in enumerate(top, 1):
        sc = s.score or {}
        lit = (s.enrichment or {}).get("litigation") or {}
        pm = (s.enrichment or {}).get("pubmed") or {}
        lbl = (s.enrichment or {}).get("label") or {}
        warn = {True: "in label", False: "NOT in label", None: "n/a"}[
            lbl.get("event_in_warnings")]
        traj = " &rarr; ".join(f"{q['prr']}" for q in (s.trajectory or [])) or "&mdash;"
        rec = sc.get("recommendation") or "unscored"
        rec_color = REC_COLOR.get(rec, "#909497")
        vr = sc.get("viability_raw")
        viab = (f"{sc.get('viability')}/100"
                + (f' <span style="color:#8a97a5;font-weight:400">(raw {vr})</span>'
                   if vr is not None else ""))
        defs = ", ".join(sc.get("likely_defendants") or []) or "n/a"

        cards.append(f"""
      <tr><td style="padding:16px 0;border-top:1px solid #e3e8ee">
        <table width="100%" cellpadding="0" cellspacing="0"><tr>
          <td style="font-size:16px;font-weight:700;color:#1b2733;vertical-align:top">
            {i}. {_esc(s.product)} &mdash; {_esc(s.event)}</td>
          <td align="right" style="vertical-align:top;white-space:nowrap;padding-left:12px">
            <span style="color:#1a5276;font-size:16px;font-weight:700">{viab}</span>
            <span style="background:{rec_color};color:#fff;border-radius:12px;
              padding:2px 10px;font-size:12px;font-weight:700;margin-left:6px">
              {_esc(rec.upper())}</span></td>
        </tr></table>
        <div style="font-size:13px;color:#5b6b7b;margin:8px 0">
          {s.source.upper()} &middot; {s.a} cases &middot; PRR {s.prr}
          (slope {s.prr_slope:+}/q) &middot; &chi;&sup2; {s.chi2}
          &middot; PubMed {pm.get('count', '?')}
          &middot; Dockets {lit.get('docket_hits', '?')} &middot; warning {warn}</div>
        <div style="font-size:13px;color:#5b6b7b;margin:4px 0">
          <b style="color:#1a5276">EB05 {getattr(s, 'eb05', 0)}</b>
          &middot; EBGM {getattr(s, 'ebgm', 0)} &middot; RRR {getattr(s, 'rrr', 0)}
          (exp {getattr(s, 'expected', 0)}) &middot; IC025 {getattr(s, 'ic025', 0)}
          &middot; ROR025 {getattr(s, 'ror025', 0)}</div>
        <div style="font-size:13px;color:#1a5276;font-family:Consolas,monospace;margin:4px 0">
          PRR by quarter: {traj}</div>
        <div style="font-size:14px;color:#222;margin:8px 0;line-height:1.55">
          {_esc(sc.get('rationale', ''))}</div>
        {_flag_blocks(s, sc)}
        <div style="font-size:13px;color:#5b6b7b;margin-top:6px">
          <b>Likely defendants:</b> {_esc(defs)}</div>
      </td></tr>""")

    html_doc = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f7f9fb">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f7f9fb">
<tr><td align="center">
  <table width="720" cellpadding="0" cellspacing="0" style="max-width:720px;
    background:#ffffff;font-family:'Segoe UI',Arial,sans-serif;margin:16px;
    border:1px solid #e3e8ee;border-radius:10px">
    <tr><td style="background:#1b2733;padding:18px 22px;border-radius:10px 10px 0 0">
      <div style="font-size:20px;font-weight:700;color:#ffffff">Mass Tort Signal Memo</div>
      <div style="color:#aab7c4;font-size:13px;margin-top:2px">{today}</div></td></tr>
    <tr><td style="padding:16px 22px 0">
      <p style="font-size:14px;color:#333;margin:0;line-height:1.5">
        {len(signals)} signals passed the statistical screen; top {len(top)} by
        litigation-viability score below.
        <a href="{DASHBOARD_URL}" style="color:#1a5276;font-weight:600">Open the live dashboard &rarr;</a></p></td></tr>
    <tr><td style="padding:0 22px">
      <table width="100%" cellpadding="0" cellspacing="0">{''.join(cards)}</table></td></tr>
    <tr><td style="padding:14px 22px 20px;color:#909497;font-size:11px;
      border-top:1px solid #e3e8ee;line-height:1.5">
      Generated automatically. Disproportionality statistics are hypothesis-generating
      screens, not evidence of causation. Attorney review required before any action.</td></tr>
  </table>
</td></tr></table>
</body></html>"""

    path = Path(path or ROOT / "reports" / f"memo_{today}.html")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_doc, encoding="utf-8")
    # stable name the email workflow reads
    (path.parent / "latest.html").write_text(html_doc, encoding="utf-8")
    return path
