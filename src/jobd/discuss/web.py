"""The reading room: a standalone page that serves the debate transcript.

Run with `python -m jobd.discuss.web` (env: DATABASE_URL; port via
JOBD_DISCUSS_PORT, default 8200). Separate app on a separate port on
purpose — the dashboard is the product, this is an instrument pointed at
the product, and the two should not share a process or a look.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from jobd.discuss import store

app = FastAPI(title="jobd panel")


@app.get("/api/transcript")
def transcript(run: str | None = None) -> dict[str, Any]:
    conn = store.connect()
    try:
        rows = store.transcript(conn, run)
        return {
            "runs": store.runs(conn),
            "messages": [
                {
                    "seq": r["seq"],
                    "round": r["round"],
                    "model": r["model"],
                    "display": r["display"],
                    "role": r["role"],
                    "content": r["content"],
                    "meta": r["meta"],
                    "at": r["created_at"].isoformat(),
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Panel — jobd</title>
<style>
:root{
  --ink:#0f1216; --panel:#161b22; --line:#252c36; --paper:#e8e3d8;
  --dim:#8a94a3; --mod:#b7c0cc;
  --c1:#5aa9e6; --c2:#e6b45a; --c3:#9a7ff0; --c4:#4ecdc4; --c5:#ef7b9b;
}
*{box-sizing:border-box;margin:0}
body{background:var(--ink);color:var(--paper);
  font:15px/1.62 Georgia,'Times New Roman',serif;padding:0 0 12vh}
.wrap{max-width:60rem;margin:0 auto;padding:0 1.25rem}
header{padding:3.2rem 0 1.6rem;border-bottom:1px solid var(--line)}
.eyebrow{font:600 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.22em;
  color:var(--dim);text-transform:uppercase}
h1{font-size:2.3rem;font-weight:400;letter-spacing:-.01em;margin:.5rem 0 .3rem}
h1 em{font-style:italic;color:var(--dim)}
.sub{color:var(--dim);font-size:.95rem}
.legend{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem;margin:1.2rem 0 0}
.legend span{font:12px ui-monospace,Menlo,monospace;color:var(--dim);
  display:flex;align-items:center;gap:.45rem}
.dot{width:9px;height:9px;border-radius:50%;display:inline-block}
.round{margin:2.6rem 0 1.1rem;display:flex;align-items:center;gap:1rem}
.round b{font:600 11px/1 ui-monospace,Menlo,monospace;letter-spacing:.22em;
  color:var(--dim);text-transform:uppercase;white-space:nowrap}
.round i{flex:1;height:1px;background:var(--line)}
.turn{border-left:3px solid var(--edge,#3a4350);background:var(--panel);
  border-radius:0 10px 10px 0;padding:1rem 1.3rem 1.1rem;margin:0 0 1rem}
.who{display:flex;align-items:baseline;gap:.6rem;margin-bottom:.45rem}
.who b{font:600 13px ui-monospace,Menlo,monospace;color:var(--edge)}
.who small{font:11px ui-monospace,Menlo,monospace;color:var(--dim)}
.body{font-size:.95rem}
.body p{margin:.55rem 0}
.body h3,.body h4{font:600 12px ui-monospace,Menlo,monospace;
  letter-spacing:.12em;text-transform:uppercase;color:var(--dim);margin:.9rem 0 .3rem}
.body ul,.body ol{padding-left:1.2rem;margin:.5rem 0}
.body li{margin:.25rem 0}
.body code{font:12.5px ui-monospace,Menlo,monospace;background:#0c0f13;
  border:1px solid var(--line);padding:.08rem .35rem;border-radius:4px}
.body pre{background:#0c0f13;border:1px solid var(--line);border-radius:8px;
  padding: .8rem 1rem;overflow-x:auto;margin:.6rem 0}
.body pre code{border:0;padding:0;background:none}
.body strong{color:#fff;font-weight:600}
.body a{color:var(--c1)}
.voteline{font:600 12.5px ui-monospace,Menlo,monospace;margin-top:.6rem;
  padding:.4rem .6rem;background:#0c0f13;border-radius:6px;color:var(--c2)}
.goat{margin:3rem 0 0;border:1px solid var(--c2);border-radius:12px;
  padding:1.5rem 1.6rem;background:linear-gradient(140deg,#1d1810,#161b22 65%)}
.goat .eyebrow{color:var(--c2)}
.goat h2{font-size:1.6rem;font-weight:400;margin:.5rem 0 .6rem}
.goat pre{white-space:pre-wrap;font:12.5px/1.7 ui-monospace,Menlo,monospace;
  color:var(--paper)}
.mod{border-left-color:var(--mod)}
details.brief{margin:1.4rem 0 0;border:1px solid var(--line);border-radius:10px}
details.brief summary{cursor:pointer;padding: .7rem 1rem;
  font:600 11px ui-monospace,Menlo,monospace;letter-spacing:.18em;
  text-transform:uppercase;color:var(--dim)}
details.brief pre{white-space:pre-wrap;font:12.5px/1.7 ui-monospace,Menlo,monospace;
  color:var(--dim);padding:0 1rem 1rem}
.empty{color:var(--dim);padding:4rem 0;text-align:center}
@media(max-width:640px){h1{font-size:1.7rem}.turn{padding:.8rem .9rem}}
</style></head><body>
<div class="wrap">
  <header>
    <div class="eyebrow">jobd · the instrument room</div>
    <h1>Five models walk into a <em>review queue</em></h1>
    <div class="sub" id="sub">Loading the session…</div>
    <div class="legend" id="legend"></div>
  </header>
  <div id="feed"><div class="empty">No discussion recorded yet.</div></div>
</div>
<script>
const HUES = ["var(--c1)","var(--c2)","var(--c3)","var(--c4)","var(--c5)"];
const ROUNDS = {1:"Round 1 — Diagnose",2:"Round 2 — Debate",3:"Round 3 — Verdict"};
function md(s){
  s = s.replace(/&/g,"&amp;").replace(/</g,"&lt;");
  s = s.replace(/```([\\s\\S]*?)```/g,(m,c)=>"<pre><code>"+c.trim()+"</code></pre>");
  s = s.replace(/`([^`\\n]+)`/g,"<code>$1</code>");
  s = s.replace(/^#{3,4}\\s*(.+)$/gm,"<h4>$1</h4>");
  s = s.replace(/^#{1,2}\\s*(.+)$/gm,"<h3>$1</h3>");
  s = s.replace(/\\*\\*([^*]+)\\*\\*/g,"<strong>$1</strong>");
  s = s.replace(/^(VOTE|EXPERIMENT):\\s*(.+)$/gm,'<div class="voteline">$1: $2</div>');
  s = s.replace(/^[-*]\\s+(.+)$/gm,"<li>$1</li>");
  s = s.replace(/(<li>[\\s\\S]*?<\\/li>)(?!\\s*<li>)/g,"<ul>$1</ul>");
  s = s.replace(/^\\d+\\.\\s+(.+)$/gm,"<li>$1</li>");
  return s.split(/\\n{2,}/).map(b=>/^<(h3|h4|ul|pre|div)/.test(b.trim())?b:"<p>"+b.replace(/\\n/g,"<br>")+"</p>").join("");
}
async function load(){
  const r = await fetch("/api/transcript"); const d = await r.json();
  const msgs = d.messages||[];
  if(!msgs.length){setTimeout(load,4000);return}
  const panel=[...new Set(msgs.filter(m=>m.role==="panelist").map(m=>m.display))];
  const hue=n=>HUES[panel.indexOf(n)%HUES.length];
  document.getElementById("legend").innerHTML = panel.map(n=>
    `<span><i class="dot" style="background:${hue(n)}"></i>${n}</span>`).join("");
  const done = msgs.some(m=>m.role==="verdict");
  document.getElementById("sub").textContent =
    `${msgs[0].at.slice(0,10)} · ${panel.length} panelists · ` +
    (done ? "session closed" : "session in progress…");
  let html="", lastRound=-1;
  for(const m of msgs){
    if(m.role==="moderator" && m.round===0){
      html += `<details class="brief"><summary>The brief handed to every panelist</summary><pre>${m.content.replace(/</g,"&lt;")}</pre></details>`;
      continue;
    }
    if(m.role==="verdict"){
      const v=(m.meta&&m.meta.votes)?m.meta.votes:{};
      const goat=Object.keys(v).sort((a,b)=>v[b]-v[a])[0];
      html += `<div class="goat"><div class="eyebrow">The moderator tallies</div>
        <h2>${goat?goat+" — the GOAT 🐐":"No verdict"}</h2>
        <pre>${m.content.replace(/</g,"&lt;")}</pre></div>`;
      continue;
    }
    if(m.round!==lastRound){
      html += `<div class="round"><b>${ROUNDS[m.round]||("Round "+m.round)}</b><i></i></div>`;
      lastRound=m.round;
    }
    html += `<div class="turn" style="--edge:${hue(m.display)}">
      <div class="who"><b>${m.display}</b><small>${m.model.replace("openrouter/","")}</small></div>
      <div class="body">${md(m.content)}</div></div>`;
  }
  document.getElementById("feed").innerHTML=html;
  if(!done) setTimeout(load,5000);
}
load();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
def page() -> str:
    return _PAGE


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("JOBD_DISCUSS_PORT", "8200")))
