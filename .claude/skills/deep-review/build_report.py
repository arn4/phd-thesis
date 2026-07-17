#!/usr/bin/env python3
"""Regenerate report.html + report.md for a Claude review.

Version-agnostic: all instance identity (name, thesis title, page/word/agent
counts, "start here" corroborations) is read from the review's
`meta.json`, so this same script serves v1, v2, … Operates on the `claude-review/`
folder it lives in (or that it can locate). Edit the `solved` field on any finding
in `findings/merged.json`, then re-run:  python3 build_report.py
"""
import json
from pathlib import Path
from datetime import date


def locate():
    here = Path(__file__).resolve().parent
    if (here / "findings" / "merged.json").exists():
        return here
    for base in [Path.cwd(), *Path.cwd().parents, *here.parents]:
        c = base / "claude-review"
        if (c / "findings" / "merged.json").exists():
            return c
    raise SystemExit("error: could not locate claude-review/findings/merged.json")


HERE = locate()
meta = {}
mp = HERE / "meta.json"
if mp.exists():
    meta = json.loads(mp.read_text())

NAME = meta.get("name", "Claude Review")
THESIS_TITLE = meta.get("thesis_title", "Thesis")
PAGES = str(meta.get("pages", "—"))
WORDS = str(meta.get("words", "—"))
AGENTS = str(meta.get("agents", "—"))
GENERATED = meta.get("generated", date.today().isoformat())
CORRO = meta.get("corroborations", [])

data = json.loads((HERE / "findings" / "merged.json").read_text())
for f in data:
    f.setdefault("solved", False)
SEV_ORDER = {"Critical": 0, "Major": 1, "Minor": 2, "Nitpick": 3}
data.sort(key=lambda f: (SEV_ORDER.get(f["severity"], 9), f["unit"], f["source"]))
TOTAL = len(data)
SOLVED = sum(1 for f in data if f.get("solved"))


def hsafe(s):
    return s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


blob = hsafe(json.dumps(data, ensure_ascii=False))
corro_blob = hsafe(json.dumps(CORRO, ensure_ascii=False))


def esc_html(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


TEMPLATE = r"""<title>__NAME__ — Thesis Audit</title>
<style>
:root{
  --paper:#F4F6F8; --surface:#FFFFFF; --surface-2:#EDF0F4; --surface-3:#E4E8ED;
  --border:#D9DEE5; --border-strong:#C3CAD4;
  --ink:#1B212A; --ink-soft:#39424E; --muted:#616C7A; --faint:#8B95A2;
  --accent:#2C5F8B; --accent-soft:#e7eef5;
  --crit:#B3261E; --crit-bg:#fbeae8; --maj:#A5620A; --maj-bg:#f8eede;
  --min:#4C5A6B; --min-bg:#eceff3; --nit:#78818E; --nit-bg:#eef0f2;
  --done:#2E7D5B; --done-bg:#e6f2ec;
  --shadow:0 1px 2px rgba(20,28,40,.04),0 3px 12px rgba(20,28,40,.05);
  --serif:ui-serif,"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
  --sans:ui-sans-serif,-apple-system,"SF Pro Text","Segoe UI",system-ui,Roboto,sans-serif;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){
  :root{
    --paper:#0E1116; --surface:#161A20; --surface-2:#1E242C; --surface-3:#252C35;
    --border:#2A313B; --border-strong:#3A424E;
    --ink:#E7EBF0; --ink-soft:#C4CCD6; --muted:#949EAC; --faint:#6C7683;
    --accent:#6CA6D9; --accent-soft:#1a2733;
    --crit:#F0645A; --crit-bg:#2a1613; --maj:#E19A3E; --maj-bg:#2a2010;
    --min:#9BA6B4; --min-bg:#20262e; --nit:#7E8896; --nit-bg:#1c2129;
    --done:#57B08A; --done-bg:#122019;
    --shadow:0 1px 2px rgba(0,0,0,.3),0 3px 14px rgba(0,0,0,.25);
  }
}
:root[data-theme="light"]{
  --paper:#F4F6F8; --surface:#FFFFFF; --surface-2:#EDF0F4; --surface-3:#E4E8ED;
  --border:#D9DEE5; --border-strong:#C3CAD4; --ink:#1B212A; --ink-soft:#39424E;
  --muted:#616C7A; --faint:#8B95A2; --accent:#2C5F8B; --accent-soft:#e7eef5;
  --crit:#B3261E; --crit-bg:#fbeae8; --maj:#A5620A; --maj-bg:#f8eede;
  --min:#4C5A6B; --min-bg:#eceff3; --nit:#78818E; --nit-bg:#eef0f2;
  --done:#2E7D5B; --done-bg:#e6f2ec;
  --shadow:0 1px 2px rgba(20,28,40,.04),0 3px 12px rgba(20,28,40,.05);
}
:root[data-theme="dark"]{
  --paper:#0E1116; --surface:#161A20; --surface-2:#1E242C; --surface-3:#252C35;
  --border:#2A313B; --border-strong:#3A424E; --ink:#E7EBF0; --ink-soft:#C4CCD6;
  --muted:#949EAC; --faint:#6C7683; --accent:#6CA6D9; --accent-soft:#1a2733;
  --crit:#F0645A; --crit-bg:#2a1613; --maj:#E19A3E; --maj-bg:#2a2010;
  --min:#9BA6B4; --min-bg:#20262e; --nit:#7E8896; --nit-bg:#1c2129;
  --done:#57B08A; --done-bg:#122019;
  --shadow:0 1px 2px rgba(0,0,0,.3),0 3px 14px rgba(0,0,0,.25);
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased;}
.wrap{max-width:1080px;margin:0 auto;padding:0 22px 96px;}
.mast{padding:40px 0 24px;border-bottom:1px solid var(--border);}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:600;margin:0 0 12px;}
h1.title{font-family:var(--serif);font-weight:600;font-size:clamp(26px,4vw,38px);line-height:1.12;
  margin:0 0 6px;letter-spacing:-.01em;text-wrap:balance;color:var(--ink);}
.subtitle{font-family:var(--serif);font-style:italic;color:var(--muted);font-size:19px;margin:0 0 20px;}
.metarow{display:flex;flex-wrap:wrap;gap:8px 22px;color:var(--muted);font-size:13px;}
.metarow b{color:var(--ink-soft);font-variant-numeric:tabular-nums;font-weight:600;}
.progress-wrap{margin-top:18px;}
.progress-lbl{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-bottom:6px;}
.progress-lbl b{color:var(--done);font-variant-numeric:tabular-nums;}
.progress-track{height:7px;background:var(--surface-2);border-radius:6px;overflow:hidden;}
.progress-fill{height:100%;background:var(--done);border-radius:6px;transition:width .3s;}
.mast-note{margin-top:18px;font-size:13.5px;color:var(--muted);max-width:74ch;line-height:1.6;}
.mast-note b{color:var(--ink-soft);font-weight:600;} .mast-note code{font-family:var(--mono);font-size:12px;}
.tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:26px 0 8px;}
@media(max-width:640px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 16px 14px;
  cursor:pointer;text-align:left;position:relative;overflow:hidden;
  transition:border-color .12s,box-shadow .12s,transform .06s;box-shadow:var(--shadow);}
.tile:hover{border-color:var(--border-strong);transform:translateY(-1px);}
.tile:focus-visible{outline:2px solid var(--accent);outline-offset:2px;}
.tile[aria-pressed="true"]{box-shadow:0 0 0 1px currentColor,var(--shadow);}
.tile .strip{position:absolute;left:0;top:0;bottom:0;width:4px;background:currentColor;}
.tile .n{font-size:30px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1;color:var(--ink);letter-spacing:-.02em;}
.tile .lbl{margin-top:7px;font-size:12.5px;letter-spacing:.06em;text-transform:uppercase;font-weight:600;color:currentColor;}
.tile .open{font-size:11.5px;color:var(--muted);margin-top:3px;font-variant-numeric:tabular-nums;}
.tile.s-crit{color:var(--crit)} .tile.s-maj{color:var(--maj)} .tile.s-min{color:var(--min)} .tile.s-nit{color:var(--nit)}
.breakdowns{display:grid;grid-template-columns:1fr 1fr;gap:26px;margin:22px 0 4px;}
@media(max-width:720px){.breakdowns{grid-template-columns:1fr}}
.bd h3{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;font-weight:600;}
.bar{display:grid;grid-template-columns:130px 1fr 34px;align-items:center;gap:10px;margin-bottom:7px;font-size:13px;
  cursor:pointer;border:none;background:none;width:100%;text-align:left;color:var(--ink-soft);padding:2px 0;font-family:inherit;}
.bar:hover .bar-name{color:var(--accent)}
.bar-name{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.bar-track{height:8px;background:var(--surface-2);border-radius:6px;overflow:hidden;}
.bar-fill{height:100%;background:var(--accent);border-radius:6px;opacity:.72;}
.bar-n{font-variant-numeric:tabular-nums;color:var(--muted);text-align:right;}
.corro{margin:30px 0 4px;background:var(--surface);border:1px solid var(--border);border-left:3px solid var(--accent);
  border-radius:12px;padding:18px 20px;box-shadow:var(--shadow);}
.corro.hidden{display:none;}
.corro h3{font-family:var(--serif);font-size:17px;margin:0 0 4px;color:var(--ink);font-weight:600;}
.corro p.sub{margin:0 0 14px;color:var(--muted);font-size:13px;}
.corro ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px;}
.corro li{display:flex;gap:12px;align-items:flex-start;}
.corro button.jump{flex:1;text-align:left;background:var(--surface-2);border:1px solid var(--border);border-radius:9px;
  padding:10px 12px;cursor:pointer;font-family:inherit;font-size:13.5px;color:var(--ink);
  transition:border-color .12s,background .12s;line-height:1.45;}
.corro button.jump:hover{border-color:var(--accent);background:var(--accent-soft);}
.corro .jump b{color:var(--ink)} .corro .jump .who{color:var(--muted);font-size:12px;}
.corro .cbadge{flex:none;margin-top:2px;}
.controls{position:sticky;top:0;z-index:20;background:var(--paper);padding:16px 0 12px;margin-top:26px;border-bottom:1px solid var(--border);}
.filterbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;}
.sevpills{display:flex;gap:6px;flex-wrap:wrap;}
.pill{border:1px solid var(--border-strong);background:var(--surface);color:var(--ink-soft);border-radius:999px;
  padding:5px 12px;font-size:12.5px;font-weight:600;cursor:pointer;font-family:inherit;transition:all .12s;letter-spacing:.02em;}
.pill:hover{border-color:var(--accent)}
.pill[aria-pressed="true"].p-crit{background:var(--crit-bg);color:var(--crit);border-color:var(--crit);}
.pill[aria-pressed="true"].p-maj{background:var(--maj-bg);color:var(--maj);border-color:var(--maj);}
.pill[aria-pressed="true"].p-min{background:var(--min-bg);color:var(--min);border-color:var(--min);}
.pill[aria-pressed="true"].p-nit{background:var(--nit-bg);color:var(--nit);border-color:var(--nit);}
select,input[type=search]{font-family:inherit;font-size:13px;color:var(--ink);background:var(--surface);
  border:1px solid var(--border-strong);border-radius:8px;padding:6px 10px;}
input[type=search]{min-width:170px;flex:1;}
.chk{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--muted);cursor:pointer;user-select:none;padding:6px 4px;}
.chk input{accent-color:var(--done);width:15px;height:15px;cursor:pointer;}
select:focus-visible,input:focus-visible,.pill:focus-visible,button:focus-visible,.chk input:focus-visible{outline:2px solid var(--accent);outline-offset:1px;}
.btn-reset{border:1px solid var(--border-strong);background:var(--surface);color:var(--muted);border-radius:8px;
  padding:6px 12px;font-size:12.5px;cursor:pointer;font-family:inherit;}
.btn-reset:hover{color:var(--ink);border-color:var(--accent)}
.count-line{margin-top:10px;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums;}
.count-line b{color:var(--ink-soft)}
.list{margin-top:8px;display:flex;flex-direction:column;gap:12px;}
.card{background:var(--surface);border:1px solid var(--border);border-left-width:4px;border-radius:11px;
  padding:15px 17px;box-shadow:var(--shadow);transition:opacity .15s;}
.card.c-crit{border-left-color:var(--crit)} .card.c-maj{border-left-color:var(--maj)}
.card.c-min{border-left-color:var(--min)} .card.c-nit{border-left-color:var(--nit)}
.card.solved{opacity:.5;border-left-color:var(--done)!important;}
.card.solved .desc,.card.solved .quote{text-decoration:line-through;text-decoration-color:var(--faint);}
.card-head{display:flex;flex-wrap:wrap;gap:7px;align-items:center;margin-bottom:9px;}
.badge{font-size:11px;font-weight:600;letter-spacing:.03em;border-radius:6px;padding:2px 8px;border:1px solid var(--border);
  color:var(--ink-soft);background:var(--surface-2);white-space:nowrap;}
.badge.sev{border:none;text-transform:uppercase;letter-spacing:.06em;}
.badge.sev.b-crit{background:var(--crit-bg);color:var(--crit)} .badge.sev.b-maj{background:var(--maj-bg);color:var(--maj)}
.badge.sev.b-min{background:var(--min-bg);color:var(--min)} .badge.sev.b-nit{background:var(--nit-bg);color:var(--nit)}
.badge.cat{color:var(--accent);background:var(--accent-soft);border-color:transparent;}
.badge.src{font-family:var(--mono);font-size:10.5px;}
.badge.conf.low{border-style:dashed;color:var(--maj);border-color:var(--maj);background:transparent;}
.badge.done{background:var(--done-bg);color:var(--done);border:none;}
.badge.id{margin-left:auto;font-family:var(--mono);color:var(--faint);background:none;border:none;font-size:11px;}
.loc{font-family:var(--mono);font-size:12px;color:var(--muted);margin:0 0 9px;word-break:break-all;}
.quote{font-family:var(--mono);font-size:12.5px;background:var(--surface-2);border:1px solid var(--border);border-radius:8px;
  padding:8px 11px;margin:0 0 10px;color:var(--ink-soft);white-space:pre-wrap;word-break:break-word;overflow-x:auto;line-height:1.5;}
.desc{margin:0 0 11px;color:var(--ink);line-height:1.58;}
.fix{display:flex;gap:9px;align-items:flex-start;background:var(--surface-2);border-radius:8px;padding:9px 12px;
  font-size:13.5px;color:var(--ink-soft);line-height:1.55;}
.fix .fixlbl{flex:none;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);margin-top:3px;}
.resolution{display:flex;gap:9px;align-items:flex-start;background:var(--done-bg);border-radius:8px;padding:9px 12px;
  margin-top:9px;font-size:13px;color:var(--done);line-height:1.5;}
.card.solved .resolution{text-decoration:none;}
.resolution .rlbl{flex:none;font-size:10.5px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin-top:2px;}
.empty{text-align:center;padding:60px 20px;color:var(--muted);}
.foot{margin-top:40px;padding-top:20px;border-top:1px solid var(--border);color:var(--faint);font-size:12px;line-height:1.7;}
.foot b{color:var(--muted)} .foot code{font-family:var(--mono)}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style>

<div class="wrap">
  <header class="mast">
    <p class="eyebrow">PhD Thesis · __NAME__</p>
    <h1 class="title">__TITLE__</h1>
    <p class="subtitle">__NAME__ — a multi-agent audit of typography, grammar, correctness, soundness &amp; defensibility</p>
    <div class="metarow">
      <span><b id="m-total">__TOTAL__</b> findings</span>
      <span><b>__PAGES__</b> pages</span>
      <span><b>__AGENTS__</b> review agents</span>
      <span><b>__WORDS__</b> words</span>
      <span>Generated <b>__GENERATED__</b></span>
    </div>
    <div class="progress-wrap">
      <div class="progress-lbl"><span>Resolution progress</span><span><b id="pg-solved">__SOLVED__</b> / <span id="pg-total">__TOTAL__</span> solved</span></div>
      <div class="progress-track"><div class="progress-fill" id="pg-fill" style="width:0%"></div></div>
    </div>
    <p class="mast-note">All findings are <b>read-only advice</b> — nothing in the thesis was edited by the review.
      Severity is <b>priority-aware</b> (Introduction held to a perfect standard, appendices leniently). Math findings carry a
      <b>confidence</b> flag — treat <i>Low</i> as "verify first." To track progress, set <code>"solved": true</code> on any
      finding in <code>findings/merged.json</code> (or use <code>/solve-issue</code>) and re-run <code>python3 build_report.py</code>.</p>
  </header>

  <section class="tiles" id="tiles" aria-label="Severity summary (click to filter)"></section>

  <section class="breakdowns">
    <div class="bd"><h3>By chapter / unit</h3><div id="bd-unit"></div></div>
    <div class="bd"><h3>By category</h3><div id="bd-cat"></div></div>
  </section>

  <section class="corro" id="corro">
    <h3>Start here — confirmed by more than one agent</h3>
    <p class="sub">These surfaced independently from multiple reviewers, so they're the highest-signal items to check first.</p>
    <ul id="corro-list"></ul>
  </section>

  <div class="controls">
    <div class="filterbar">
      <div class="sevpills" id="sevpills"></div>
      <select id="f-unit" aria-label="Filter by unit"></select>
      <select id="f-cat" aria-label="Filter by category"></select>
      <select id="f-src" aria-label="Filter by agent"></select>
      <select id="f-sort" aria-label="Sort">
        <option value="sev">Sort: severity</option>
        <option value="unit">Sort: chapter</option>
        <option value="cat">Sort: category</option>
        <option value="src">Sort: agent</option>
      </select>
      <label class="chk"><input type="checkbox" id="f-hide"> Hide solved</label>
      <input type="search" id="f-search" placeholder="Search text, location, symbol…" aria-label="Search findings">
      <button class="btn-reset" id="reset">Reset</button>
    </div>
    <div class="count-line" id="countline"></div>
  </div>

  <main class="list" id="list"></main>

  <footer class="foot">
    <b>__NAME__.</b> Produced by parallel read-only agents (Introduction, one per paper chapter, plus cross-cutting passes for
    notation, bibliography, typography, visual/figures, front-matter, and a hostile-examiner pass). Line numbers reference the
    source <code>.tex</code> files at review time. Raw per-agent outputs live in <code>findings/agents/</code>; the working set
    is <code>findings/merged.json</code>; instance identity is in <code>meta.json</code>.
  </footer>
</div>

<script type="application/json" id="findings-data">__DATA__</script>
<script type="application/json" id="corro-data">__CORRO__</script>
<script>
(function(){
  "use strict";
  // NB: blob ids must stay distinct from every markup element id. The corro
  // section owns the plain corro id, so the blob uses corro-data -- reading the
  // blob off the section would return heading text and throw here.
  var DATA=JSON.parse(document.getElementById("findings-data").textContent);
  var CORRO=JSON.parse(document.getElementById("corro-data").textContent);
  var SEVS=[["Critical","crit"],["Major","maj"],["Minor","min"],["Nitpick","nit"]];
  var SEVKEY={Critical:"crit",Major:"maj",Minor:"min",Nitpick:"nit"};
  var SEVRANK={Critical:0,Major:1,Minor:2,Nitpick:3};
  var state={sev:new Set(),unit:"",cat:"",src:"",sort:"sev",q:"",hide:false};

  function counts(field){var m={};DATA.forEach(function(f){var k=f[field]||"?";m[k]=(m[k]||0)+1;});return m;}
  function sevCounts(){var m={Critical:0,Major:0,Minor:0,Nitpick:0},o={Critical:0,Major:0,Minor:0,Nitpick:0};
    DATA.forEach(function(f){if(m[f.severity]!=null){m[f.severity]++;if(!f.solved)o[f.severity]++;}});return {all:m,open:o};}

  var scAll=sevCounts(), tiles=document.getElementById("tiles");
  SEVS.forEach(function(s){
    var b=document.createElement("button");b.className="tile s-"+s[1];b.setAttribute("aria-pressed","false");
    b.innerHTML='<span class="strip"></span><div class="n">'+scAll.all[s[0]]+'</div><div class="lbl">'+s[0]+
      '</div><div class="open" data-open="'+s[0]+'">'+scAll.open[s[0]]+' open</div>';
    b.addEventListener("click",function(){toggleSev(s[0],b);});tiles.appendChild(b);
  });
  function toggleSev(name,btn){
    if(state.sev.has(name)){state.sev.delete(name);btn.setAttribute("aria-pressed","false");}
    else{state.sev.add(name);btn.setAttribute("aria-pressed","true");}
    syncPills();render();
  }

  function buildBars(id,field,order){
    var m=counts(field), el=document.getElementById(id);
    var max=Math.max.apply(null,Object.values(m).concat([1]));
    var keys=order?order.filter(function(k){return m[k]!=null;}):Object.keys(m).sort(function(a,b){return m[b]-m[a];});
    keys.forEach(function(k){
      var pct=Math.round(m[k]/max*100);
      var b=document.createElement("button");b.className="bar";
      b.innerHTML='<span class="bar-name" title="'+k+'">'+k+'</span><span class="bar-track"><span class="bar-fill" style="width:'+pct+'%"></span></span><span class="bar-n">'+m[k]+'</span>';
      b.addEventListener("click",function(){
        if(field==="unit"){state.unit=(state.unit===k?"":k);document.getElementById("f-unit").value=state.unit;}
        else{state.cat=(state.cat===k?"":k);document.getElementById("f-cat").value=state.cat;}
        render();
      });
      el.appendChild(b);
    });
  }
  var UNIT_ORDER=["Introduction","Paper-2302","Paper-2305","Paper-2405","Paper-2406","Paper-2506","Front-matter","Cross-cutting"];
  buildBars("bd-unit","unit",UNIT_ORDER); buildBars("bd-cat","category");

  var cl=document.getElementById("corro-list");
  if(!CORRO.length){document.getElementById("corro").classList.add("hidden");}
  CORRO.forEach(function(c){
    var li=document.createElement("li");
    li.innerHTML='<span class="badge sev b-'+(SEVKEY[c.sev]||"min")+' cbadge">'+c.sev+'</span>';
    var btn=document.createElement("button");btn.className="jump";
    btn.innerHTML='<b>'+c.t+'</b><br><span class="who">'+(c.who||"")+' · click to filter</span>';
    btn.addEventListener("click",function(){
      state.q=(c.q||"").toLowerCase();document.getElementById("f-search").value=c.q||"";
      window.scrollTo({top:document.getElementById("countline").getBoundingClientRect().top+window.scrollY-70,behavior:"smooth"});
      render();
    });
    li.appendChild(btn);cl.appendChild(li);
  });

  var sp=document.getElementById("sevpills");
  SEVS.forEach(function(s){
    var b=document.createElement("button");b.className="pill p-"+s[1];b.textContent=s[0];b.setAttribute("aria-pressed","false");b.dataset.sev=s[0];
    b.addEventListener("click",function(){toggleSev(s[0],tiles.children[SEVRANK[s[0]]]);});sp.appendChild(b);
  });
  function syncPills(){Array.prototype.forEach.call(sp.children,function(b){b.setAttribute("aria-pressed",state.sev.has(b.dataset.sev)?"true":"false");});}

  function fillSelect(id,label,field,order){
    var m=counts(field), sel=document.getElementById(id);
    var keys=order?order.filter(function(k){return m[k]!=null;}):Object.keys(m).sort();
    var opt=document.createElement("option");opt.value="";opt.textContent=label+" (all)";sel.appendChild(opt);
    keys.forEach(function(k){var o=document.createElement("option");o.value=k;o.textContent=k+" ("+m[k]+")";sel.appendChild(o);});
    sel.addEventListener("change",function(){state[field==="unit"?"unit":field==="category"?"cat":"src"]=sel.value;render();});
  }
  fillSelect("f-unit","Chapter","unit",UNIT_ORDER);
  fillSelect("f-cat","Category","category");
  fillSelect("f-src","Agent","source");
  document.getElementById("f-sort").addEventListener("change",function(e){state.sort=e.target.value;render();});
  document.getElementById("f-search").addEventListener("input",function(e){state.q=e.target.value.toLowerCase();render();});
  document.getElementById("f-hide").addEventListener("change",function(e){state.hide=e.target.checked;render();});
  document.getElementById("reset").addEventListener("click",function(){
    state={sev:new Set(),unit:"",cat:"",src:"",sort:"sev",q:"",hide:false};
    ["f-unit","f-cat","f-src"].forEach(function(i){document.getElementById(i).value="";});
    document.getElementById("f-sort").value="sev";document.getElementById("f-search").value="";document.getElementById("f-hide").checked=false;
    syncPills();Array.prototype.forEach.call(tiles.children,function(t){t.setAttribute("aria-pressed","false");});render();
  });

  var list=document.getElementById("list"), countline=document.getElementById("countline");
  function matches(f){
    if(state.hide&&f.solved)return false;
    if(state.sev.size&&!state.sev.has(f.severity))return false;
    if(state.unit&&f.unit!==state.unit)return false;
    if(state.cat&&f.category!==state.cat)return false;
    if(state.src&&f.source!==state.src)return false;
    if(state.q){var hay=(f.description+" "+f.quote+" "+f.location+" "+f.suggested_fix+" "+f.category+" "+f.unit+" "+f.source+" "+f.severity).toLowerCase();if(hay.indexOf(state.q)<0)return false;}
    return true;
  }
  function sortFns(a,b){
    if(state.sort==="sev"){var d=SEVRANK[a.severity]-SEVRANK[b.severity];if(d)return d;return(a.unit>b.unit?1:-1);}
    if(state.sort==="unit"){if(a.unit!==b.unit)return a.unit>b.unit?1:-1;return SEVRANK[a.severity]-SEVRANK[b.severity];}
    if(state.sort==="cat"){if(a.category!==b.category)return a.category>b.category?1:-1;return SEVRANK[a.severity]-SEVRANK[b.severity];}
    if(state.sort==="src"){if(a.source!==b.source)return a.source>b.source?1:-1;return SEVRANK[a.severity]-SEVRANK[b.severity];}
    return 0;
  }
  function esc(t){var d=document.createElement("div");d.textContent=t==null?"":t;return d.innerHTML;}
  function updateProgress(){
    var solved=DATA.filter(function(f){return f.solved;}).length;
    document.getElementById("pg-solved").textContent=solved;
    document.getElementById("pg-total").textContent=DATA.length;
    document.getElementById("pg-fill").style.width=(DATA.length?solved/DATA.length*100:0)+"%";
    Array.prototype.forEach.call(document.querySelectorAll("[data-open]"),function(el){
      var sev=el.getAttribute("data-open");
      el.textContent=DATA.filter(function(f){return f.severity===sev&&!f.solved;}).length+" open";
    });
  }
  function render(){
    var rows=DATA.filter(matches).slice().sort(sortFns);
    list.innerHTML="";
    if(!rows.length){list.innerHTML='<div class="empty">No findings match these filters.</div>';}
    rows.forEach(function(f){
      var k=SEVKEY[f.severity]||"min";
      var c=document.createElement("article");c.className="card c-"+k+(f.solved?" solved":"");
      var head='<span class="badge sev b-'+k+'">'+esc(f.severity)+'</span>'+
               '<span class="badge cat">'+esc(f.category)+'</span>'+
               '<span class="badge">'+esc(f.unit)+'</span>'+
               '<span class="badge src">'+esc(f.source)+'</span>';
      if(f.solved)head+='<span class="badge done">✓ solved</span>';
      if(f.confidence){var low=/low/i.test(f.confidence);head+='<span class="badge conf'+(low?" low":"")+'">conf: '+esc(f.confidence)+'</span>';}
      head+='<span class="badge id">'+esc(f.id)+'</span>';
      var html='<div class="card-head">'+head+'</div>';
      if(f.location)html+='<div class="loc">'+esc(f.location)+'</div>';
      if(f.quote)html+='<div class="quote">'+esc(f.quote)+'</div>';
      html+='<p class="desc">'+esc(f.description)+'</p>';
      if(f.suggested_fix)html+='<div class="fix"><span class="fixlbl">Fix</span><span>'+esc(f.suggested_fix)+'</span></div>';
      if(f.solved&&f.resolution)html+='<div class="resolution"><span class="rlbl">Resolution</span><span>'+esc(f.resolution)+'</span></div>';
      c.innerHTML=html;list.appendChild(c);
    });
    var sc=sevCounts(), openTotal=DATA.filter(function(f){return !f.solved;}).length, solvedTotal=DATA.length-openTotal;
    countline.innerHTML='Showing <b>'+rows.length+'</b> of <b>'+DATA.length+'</b> findings &nbsp;·&nbsp; '+
      '<b>'+openTotal+'</b> open / <b>'+solvedTotal+'</b> solved &nbsp;·&nbsp; '+
      'open by severity: <b>'+sc.open.Critical+'</b> crit · <b>'+sc.open.Major+'</b> maj · <b>'+sc.open.Minor+'</b> min · <b>'+sc.open.Nitpick+'</b> nit';
    updateProgress();
  }
  render();
})();
</script>
"""

html = (TEMPLATE
        .replace("__DATA__", blob)
        .replace("__CORRO__", corro_blob)
        .replace("__NAME__", esc_html(NAME))
        .replace("__TITLE__", esc_html(THESIS_TITLE))
        .replace("__PAGES__", esc_html(PAGES))
        .replace("__WORDS__", esc_html(WORDS))
        .replace("__AGENTS__", esc_html(AGENTS))
        .replace("__GENERATED__", esc_html(GENERATED))
        .replace("__TOTAL__", str(TOTAL))
        .replace("__SOLVED__", str(SOLVED)))
# The template is authored as bare page-content, so wrap it into a full standalone
# document: report.html is the single output and must open straight from disk.
_head, _body = html.split('<div class="wrap">', 1)
standalone = (
    '<!doctype html>\n<html lang="en">\n<head>\n'
    '<meta charset="utf-8">\n'
    '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
    + _head +
    '</head>\n<body>\n<div class="wrap">' + _body +
    '\n</body>\n</html>\n'
)
(HERE / "report.html").write_text(standalone, encoding="utf-8")

# ---- markdown backup ----
lines = [f"# {NAME} — Thesis Audit\n", f"**{THESIS_TITLE}**\n",
         f"- Findings: **{TOTAL}**  ·  Solved: **{SOLVED}** / {TOTAL}  ·  Pages: {PAGES}  ·  Agents: {AGENTS}  ·  Generated: {GENERATED}"]
sc = {"Critical": 0, "Major": 0, "Minor": 0, "Nitpick": 0}
for f in data:
    if f["severity"] in sc: sc[f["severity"]] += 1
lines.append(f"- Severity: **{sc['Critical']} Critical**, {sc['Major']} Major, {sc['Minor']} Minor, {sc['Nitpick']} Nitpick")
lines.append("- Read-only advice; nothing in the thesis was edited. Toggle `solved` in `findings/merged.json` (or use `/solve-issue`), then re-run `build_report.py`.\n")
for sev in ["Critical", "Major", "Minor", "Nitpick"]:
    group = [f for f in data if f["severity"] == sev]
    if not group: continue
    lines.append(f"\n## {sev} ({len(group)})\n")
    group.sort(key=lambda f: (f["unit"], f["source"]))
    for f in group:
        box = "[x]" if f.get("solved") else "[ ]"
        conf = f" · conf: {f['confidence']}" if f.get("confidence") else ""
        lines.append(f"### {box} [{f['id']}] {f['unit']} · {f['category']} · {f['source']}{conf}")
        if f.get("location"): lines.append(f"- **Location:** `{f['location']}`")
        if f.get("quote"): lines.append(f"- **Quote:** `{f['quote']}`")
        lines.append(f"- **Issue:** {f['description']}")
        if f.get("suggested_fix"): lines.append(f"- **Fix:** {f['suggested_fix']}")
        if f.get("solved") and f.get("resolution"): lines.append(f"- **Resolution:** {f['resolution']}")
        lines.append("")
(HERE / "report.md").write_text("\n".join(lines), encoding="utf-8")

print(f"{NAME}: {SOLVED}/{TOTAL} solved")
print("HTML ->", HERE / "report.html", (HERE / "report.html").stat().st_size, "bytes")
print("MD   ->", HERE / "report.md")
