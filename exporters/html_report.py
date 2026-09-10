# Generates a standalone HTML investigation report
from pathlib import Path
from datetime import datetime
from core.target_model import Target


def generate(target: Target, cases_dir: Path = Path("cases")) -> Path:
    slug = target.primary.replace("@", "_").replace(".", "_")
    folder = cases_dir / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "report.html"

    entities_rows = ""
    for e in target.entities:
        plat = f'<span class="tag g">{e.platform}</span>' if e.platform else ""
        entities_rows += f"""
        <tr>
          <td>{e.entity_type}</td>
          <td>{e.value} {plat}</td>
          <td>{e.sources[0] if e.sources else ''}</td>
          <td>{e.confidence:.0%}</td>
        </tr>"""

    breach_rows = ""
    for b in target.breaches:
        fields = ", ".join(b.exposed_fields)
        breach_rows += f"""
        <tr>
          <td><span class="tag r">{b.name}</span></td>
          <td>{b.date}</td>
          <td>{fields}</td>
        </tr>"""

    timeline_items = ""
    for item in target.timeline:
        ts = item["at"][:19].replace("T", " ")
        timeline_items += f"""
        <div class="tl-item">
          <span class="tl-time">{ts}</span>
          <span class="tl-event">{item['event']}</span>
          <span class="tl-data">{item.get('data', {})}</span>
        </div>"""

    notes_html = ""
    for note in target.notes:
        notes_html += f'<div class="note">{note}</div>'

    risk_pct = int(target.risk_score * 100)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>J.A.R.V.I.S. — {target.primary}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    background:#f5f0e5;color:#3a2010;padding:40px;}}
  .header{{background:#ece6d8;border:1px solid #d8cdb8;border-radius:12px;
    padding:28px 32px;margin-bottom:24px;display:flex;
    justify-content:space-between;align-items:flex-start}}
  .header-left h1{{font-size:22px;font-weight:700;color:#2c1810}}
  .header-left .sub{{font-size:12px;color:#a08060;margin-top:4px;
    letter-spacing:1px;text-transform:uppercase}}
  .risk-badge{{background:#fdf0e8;border:1px solid #e8b090;
    border-radius:8px;padding:12px 20px;text-align:center}}
  .risk-num{{font-size:28px;font-weight:800;color:#8b2010}}
  .risk-label{{font-size:10px;color:#a08060;text-transform:uppercase;
    letter-spacing:1px}}
  .card{{background:#fdfaf4;border:1px solid #e0d4c0;border-radius:10px;
    padding:24px;margin-bottom:20px}}
  .card h2{{font-size:13px;font-weight:800;letter-spacing:2px;
    text-transform:uppercase;color:#b09070;margin-bottom:16px}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th{{text-align:left;font-size:9px;font-weight:800;letter-spacing:2px;
    text-transform:uppercase;color:#b09070;padding:0 0 8px;
    border-bottom:1px solid #e8dfc8}}
  td{{padding:8px 0;border-bottom:1px solid #f5f0e5;color:#5a3820;
    vertical-align:top}}
  .tag{{display:inline-block;font-size:9px;font-weight:700;
    padding:2px 7px;border-radius:10px;margin-left:4px}}
  .tag.r{{background:#fdf0e8;color:#9a2a0a;border:1px solid #e8b090}}
  .tag.g{{background:#eef6ee;color:#2a7a3a;border:1px solid #b8dfc0}}
  .rbar-wrap{{display:flex;align-items:center;gap:10px;margin-top:8px}}
  .rbar{{height:6px;width:200px;background:#e8dfc8;border-radius:3px}}
  .rfill{{height:100%;background:linear-gradient(90deg,#c8760a,#c0392b);
    border-radius:3px;width:{risk_pct}%}}
  .tl-item{{display:flex;gap:16px;padding:8px 0;
    border-bottom:1px solid #f5f0e5;font-size:11px}}
  .tl-time{{color:#b09070;min-width:140px;font-family:monospace}}
  .tl-event{{color:#5a2810;font-weight:600;min-width:140px}}
  .tl-data{{color:#a09070}}
  .note{{background:#fdf6ec;border-left:3px solid #c8945a;
    padding:8px 12px;border-radius:0 6px 6px 0;
    margin-bottom:8px;font-size:12px;color:#7a5030;font-style:italic}}
  .footer{{text-align:center;font-size:10px;color:#b09878;
    margin-top:32px;padding-top:16px;border-top:1px solid #e0d4c0}}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1>{target.primary}</h1>
    <div class="sub">{target.target_type} ·
      opened {target.opened_at[:10]} ·
      {len(target.entities)} entities ·
      {len(target.breaches)} breaches</div>
  </div>
  <div class="risk-badge">
    <div class="risk-num">{target.risk_score:.2f}</div>
    <div class="risk-label">risk score</div>
    <div class="rbar-wrap">
      <div class="rbar"><div class="rfill"></div></div>
    </div>
  </div>
</div>

<div class="card">
  <h2>Entities</h2>
  <table>
    <tr><th>Type</th><th>Value</th><th>Source</th><th>Confidence</th></tr>
    {entities_rows or '<tr><td colspan="4">No entities found</td></tr>'}
  </table>
</div>

<div class="card">
  <h2>Breaches</h2>
  <table>
    <tr><th>Breach</th><th>Date</th><th>Exposed Fields</th></tr>
    {breach_rows or '<tr><td colspan="3">No breaches found</td></tr>'}
  </table>
</div>

{f'<div class="card"><h2>Notes</h2>{notes_html}</div>'
  if target.notes else ''}

<div class="card">
  <h2>Timeline</h2>
  {timeline_items or '<div style="color:#b09070;font-size:12px">No events</div>'}
</div>

<div class="footer">
  Generated by J.A.R.V.I.S. · {generated}
</div>

</body>
</html>"""

    path.write_text(html)
    return path