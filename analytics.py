"""Analytics Dashboard (stretch feature) — metrics computed from the audit log.

Exposes aggregate stats over everything Provenance Guard has decided. All metrics are
derived from the same structured audit log that powers /log, so the dashboard is always
consistent with the canonical record.

Metrics returned:
  * detection_pattern   — counts + ratio of likely_ai / likely_human / uncertain verdicts
  * appeal_rate         — appeals filed per classification
  * average_confidence  — mean confidence across classifications
  * uncertain_rate      — share of classifications the system declined to call (the
                          asymmetry guard's footprint)
  * verified_creators   — number of creators holding a provenance certificate
"""

from audit import _conn, get_log


def compute_metrics():
    entries = get_log(limit=100000)
    classifications = [e for e in entries if e.get("type") == "classification"]
    appeals = [e for e in entries if e.get("type") == "appeal"]

    n = len(classifications)
    verdicts = {"likely_ai": 0, "likely_human": 0, "uncertain": 0}
    conf_sum = 0.0
    for e in classifications:
        v = e.get("attribution")
        if v in verdicts:
            verdicts[v] += 1
        conf_sum += e.get("confidence") or 0.0

    def ratio(x):
        return round(x / n, 3) if n else 0.0

    with _conn() as c:
        verified = c.execute("SELECT COUNT(*) AS n FROM certificates").fetchone()["n"]

    ai_vs_human = (
        round(verdicts["likely_ai"] / verdicts["likely_human"], 3)
        if verdicts["likely_human"]
        else None
    )

    return {
        "total_classifications": n,
        "detection_pattern": {
            "likely_ai": verdicts["likely_ai"],
            "likely_human": verdicts["likely_human"],
            "uncertain": verdicts["uncertain"],
            "ai_ratio": ratio(verdicts["likely_ai"]),
            "human_ratio": ratio(verdicts["likely_human"]),
            "ai_to_human_ratio": ai_vs_human,
        },
        "appeal_rate": round(len(appeals) / n, 3) if n else 0.0,
        "total_appeals": len(appeals),
        "average_confidence": round(conf_sum / n, 3) if n else 0.0,
        "uncertain_rate": ratio(verdicts["uncertain"]),
        "verified_creators": verified,
    }


DASHBOARD_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Provenance Guard — Analytics</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;color:#222}
 h1{font-size:1.4rem} .grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1rem}
 .card{border:1px solid #ddd;border-radius:10px;padding:1rem;background:#fafafa}
 .num{font-size:1.8rem;font-weight:700} .lbl{color:#666;font-size:.85rem}
 .bar{height:10px;border-radius:5px;background:#eee;overflow:hidden;margin-top:.4rem;display:flex}
 .ai{background:#e0653a}.hu{background:#3a8de0}.un{background:#bbb}
</style></head><body>
<h1>📊 Provenance Guard — Analytics</h1>
<div id="root">loading…</div>
<script>
fetch('/analytics').then(r=>r.json()).then(d=>{
 const dp=d.detection_pattern;
 const tot=Math.max(1,d.total_classifications);
 document.getElementById('root').innerHTML=`
  <div class="grid">
   <div class="card"><div class="num">${d.total_classifications}</div><div class="lbl">classifications</div></div>
   <div class="card"><div class="num">${(d.appeal_rate*100).toFixed(1)}%</div><div class="lbl">appeal rate (${d.total_appeals} appeals)</div></div>
   <div class="card"><div class="num">${d.average_confidence}</div><div class="lbl">avg confidence</div></div>
   <div class="card"><div class="num">${(d.uncertain_rate*100).toFixed(1)}%</div><div class="lbl">uncertain rate</div></div>
   <div class="card"><div class="num">${dp.ai_to_human_ratio ?? '—'}</div><div class="lbl">AI : human ratio</div></div>
   <div class="card"><div class="num">${d.verified_creators}</div><div class="lbl">verified creators</div></div>
  </div>
  <h3 style="margin-top:1.5rem">Detection pattern</h3>
  <div class="bar">
   <div class="ai" style="width:${dp.likely_ai/tot*100}%"></div>
   <div class="hu" style="width:${dp.likely_human/tot*100}%"></div>
   <div class="un" style="width:${dp.uncertain/tot*100}%"></div>
  </div>
  <p class="lbl">🟧 ${dp.likely_ai} likely AI &nbsp; 🟦 ${dp.likely_human} likely human &nbsp; ⬜ ${dp.uncertain} uncertain</p>`;
});
</script></body></html>"""
