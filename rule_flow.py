"""rule_flow.py - animated "rule flow" view for the Stock Scanner Pro app.

Replays your last scan as a picture: a hub visits clusters of tickers (one
cluster per sector) one filter at a time. Stocks that fail a filter switch off
in that filter's color, survivors get a green thread back to the hub.

Visualization only. It never places trades and never imports auto_trader.
It needs the *unfiltered* scan data (every ticker the scan fetched, not just
the ones that passed), so the scanner stores that in
st.session_state.all_scan_data (one line, see the hook in momentum_scanner.py).

Usage:
    import rule_flow
    rule_flow.render(st.session_state.get("all_scan_data"),
                     min_price=min_price, max_price=max_price, ...)
"""
from __future__ import annotations

import json

import streamlit as st

# One self-contained HTML page (canvas + a little JavaScript, no external
# files, no network calls). __DATA__ is replaced by the JSON payload.
_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Rule flow</title>
<style>
/* Deep-space scene with a living core: 3D galaxy stage with HUD overlaid, then instrument panes, rule cards and a table. Dark by design in both themes. */
:root{
  --bg:#03070a; --stage:#02050a; --surface:#0a1319; --line:#182a33;
  --fg:#e2f0f6; --muted:#7b97a4;
  --pass:#3ddc9b; --cool:#5fb2ff; --warm:#ffb44d; --reject:#ff5a4a;
  --font-display:'Arial Narrow','Helvetica Neue',Arial,sans-serif;
  --font-body:system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  color-scheme:dark;
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--fg);font:14px/1.5 var(--font-body)}
.wrap{max-width:1280px;margin:0 auto;padding-inline:4px;padding-block:6px 20px}
h1{font:700 30px/1 var(--font-display);letter-spacing:.03em;text-transform:uppercase;margin:0;text-wrap:balance}
h2{font:500 11px/1 var(--font-mono);letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:0}
.top{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:flex-end;justify-content:space-between}
.sub{margin:8px 0 0;color:var(--muted);max-width:64ch}
.badge{font:500 11px/1 var(--font-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--warm);border:1px solid var(--warm);padding:6px 8px;border-radius:3px}
.toolbar{display:flex;flex-wrap:wrap;gap:10px 10px;align-items:center;margin-block:16px 12px}
.btn{all:unset;cursor:pointer;padding:9px 14px;border:1px solid var(--line);border-radius:4px;font:500 13px/1 var(--font-body);background:var(--surface);color:var(--fg)}
.btn:hover{border-color:var(--cool)}
.btn[aria-pressed="true"]{border-color:var(--cool);color:var(--cool)}
button:focus-visible,input:focus-visible{outline:2px solid var(--cool);outline-offset:2px}
.summary{flex:1 1 260px;min-width:0;font:400 13px/1.4 var(--font-mono);color:var(--muted);margin-left:6px}
.summary b{color:var(--fg);font-weight:500}
.stage{position:relative;background:var(--stage);border:1px solid var(--line);border-radius:6px;overflow:hidden;box-shadow:0 0 90px rgba(95,178,255,.12)}
canvas#cv{display:block;width:100%;height:clamp(480px,52vw,660px);touch-action:pan-y}
.ov{position:absolute;pointer-events:none}
.score{left:18px;top:14px}
.score .n{font:700 80px/.9 var(--font-display);color:var(--pass);text-shadow:0 0 30px rgba(61,220,155,.6);font-variant-numeric:tabular-nums}
.score .l{font:500 11px/1.5 var(--font-mono);letter-spacing:.12em;color:var(--muted);text-transform:uppercase;margin-top:4px}
.score .bar{margin-top:8px;width:160px;height:3px;background:var(--line);border-radius:2px;overflow:hidden}
.score .bar i{display:block;height:100%;width:0;background:var(--cool);box-shadow:0 0 8px var(--cool)}
.kills{right:16px;top:14px;display:flex;flex-direction:column;gap:7px;min-width:170px}
.kills .row{display:grid;grid-template-columns:10px 1fr auto;gap:8px;align-items:center;font:500 11px/1 var(--font-mono);letter-spacing:.06em;color:var(--muted)}
.kills .row i{width:8px;height:8px;border-radius:50%;box-shadow:0 0 10px currentColor}
.kills .row b{color:var(--fg);font-weight:500;font-size:14px;font-variant-numeric:tabular-nums;min-width:2ch;text-align:right}
.kills h2{margin-bottom:2px;text-align:right}
.dlog{left:18px;bottom:58px;font:400 11px/1.35 var(--font-mono);display:flex;flex-direction:column;gap:3px;max-width:60%}
.dlog div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dlog div:nth-child(1){opacity:1}.dlog div:nth-child(2){opacity:.8}.dlog div:nth-child(3){opacity:.6}.dlog div:nth-child(4){opacity:.42}.dlog div:nth-child(5){opacity:.28}.dlog div:nth-child(6){opacity:.16}
.cam{right:16px;bottom:58px;font:500 11px/1 var(--font-mono);letter-spacing:.12em;color:var(--cool);text-transform:uppercase;text-shadow:0 0 10px rgba(95,178,255,.7);opacity:0;transition:opacity .3s}
.cam.on{opacity:1}
.lbl{position:absolute;left:0;top:0;pointer-events:none;font:500 11px/1 var(--font-mono);color:#eafff6;text-shadow:0 0 8px var(--pass),0 0 2px #000;white-space:nowrap;will-change:transform}
.legend{display:flex;flex-wrap:wrap;gap:4px 18px;padding:10px 14px;border-top:1px solid var(--line);font:400 12px/1.3 var(--font-mono);color:var(--muted)}
.tip{position:absolute;pointer-events:none;background:var(--surface);color:var(--fg);border:1px solid var(--cool);border-radius:4px;padding:8px 10px;font:400 12px/1.45 var(--font-mono);max-width:250px;box-shadow:0 6px 24px rgba(0,0,0,.6);z-index:3}
.tip b{font-weight:500}
.fallback{position:absolute;inset:0;display:grid;place-items:center;text-align:center;padding:24px;color:var(--muted);font:400 13px/1.5 var(--font-mono)}
.hud{display:grid;grid-template-columns:.8fr 1.3fr 1.3fr;gap:12px;margin-top:12px}
.pane{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:14px;min-width:0}
.pane h2{margin-bottom:12px;display:flex;justify-content:space-between;gap:8px}
.pane h2 span{color:var(--muted);opacity:.7}
.gauge{display:flex;flex-direction:column;align-items:center;gap:8px}
.gauge svg{width:132px;height:132px}
.gauge .big{font:700 34px/1 var(--font-display);fill:var(--fg)}
.gauge .lab{font:400 10px/1 var(--font-mono);fill:var(--muted);letter-spacing:.1em}
.gauge p{margin:0;font:400 12px/1.5 var(--font-mono);color:var(--muted);text-align:center}
.gauge p b{color:var(--fg);font-weight:500}
canvas#heat{display:block;width:100%;height:168px}
.code{font:400 12px/1.5 var(--font-mono);color:var(--muted);overflow-x:auto}
.code div{display:flex;gap:10px;justify-content:space-between;padding:2px 8px;border-left:2px solid var(--line);white-space:pre;transition:background .35s,color .35s}
.code div span:last-child{color:var(--fg);font-variant-numeric:tabular-nums}
.code .hot{background:rgba(255,255,255,.07);color:var(--fg)}
.rules{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:12px;margin-top:12px}
.rule{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:12px 14px;border-top:2px solid var(--rc,var(--line))}
.rhead{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 8px;align-items:baseline;margin-bottom:6px}
.rname{font-weight:600;white-space:nowrap}
.rval{font:500 12px/1 var(--font-mono);color:var(--muted);white-space:nowrap}
.chip{font:500 10px/1 var(--font-mono);letter-spacing:.06em;text-transform:uppercase;color:var(--reject);border:1px solid var(--reject);padding:3px 5px;border-radius:3px;margin-left:8px;vertical-align:1px}
.srow{display:grid;grid-template-columns:30px minmax(0,1fr) 46px;gap:8px;align-items:center;font:400 11px/1 var(--font-mono);color:var(--muted)}
.srow input{width:100%;margin:6px 0}
.srow output{text-align:right;color:var(--fg)}
input[type=range]{accent-color:var(--rc,var(--cool))}
.rstat{display:flex;align-items:center;gap:10px;margin-top:8px;font:400 11px/1.3 var(--font-mono);color:var(--muted)}
.meter{flex:0 0 60px;height:6px;background:var(--line);border-radius:3px;overflow:hidden}
.meter i{display:block;height:100%;background:var(--rc,var(--reject));width:0;box-shadow:0 0 8px var(--rc,var(--reject));transition:width .3s}
.actions{display:flex;justify-content:flex-end;margin-top:12px}
.note{font-size:12px;color:var(--muted);margin:14px 0 0;max-width:72ch}
.passed{margin-top:24px}
.tablewrap{overflow-x:auto;margin-top:10px;background:var(--surface);border:1px solid var(--line);border-radius:6px}
table{border-collapse:collapse;width:100%;min-width:560px;font:400 13px/1.3 var(--font-mono);font-variant-numeric:tabular-nums}
th{font:500 11px/1 var(--font-mono);letter-spacing:.08em;text-transform:uppercase;color:var(--muted);text-align:right;padding:10px 12px;border-bottom:1px solid var(--line)}
td{padding:9px 12px;text-align:right;border-bottom:1px solid var(--line)}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left}
tr:last-child td{border-bottom:0}
td.up{color:var(--pass)} td.dn{color:var(--reject)}
.empty{padding:18px 14px;color:var(--muted);font-family:var(--font-mono);font-size:13px}
@media (max-width:900px){.hud{grid-template-columns:minmax(0,1fr)}}
@media (max-width:700px){
  h1{font-size:32px}
  canvas#cv{height:520px}
  .score .n{font-size:56px}
  .kills{min-width:0;gap:5px}
  .kills h2{display:none}
  .dlog{display:none}
}

.seg{left:0;right:0;top:0;display:flex;gap:3px}
.seg i{flex:1;height:4px;background:rgba(255,255,255,.08);position:relative;overflow:hidden}
.seg i b{position:absolute;left:0;top:0;bottom:0;width:0}
.stt{left:18px;top:18px;display:flex;gap:12px;align-items:flex-start}
.stn{font:700 40px/.9 var(--font-display);font-variant-numeric:tabular-nums}
.stname{font:700 20px/1 var(--font-display);letter-spacing:.1em}
.stsub{font:400 11px/1.4 var(--font-mono);color:var(--muted);margin-top:5px;max-width:34ch}
.score{top:86px}
.score .n{font-size:64px}
.hud{grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.pc{display:block;width:100%;height:160px}
.strows{display:flex;flex-direction:column;gap:7px}
.strow{display:grid;grid-template-columns:8px 92px minmax(0,1fr) 34px;gap:8px;align-items:center;font:500 11px/1 var(--font-mono);color:var(--muted);padding:2px 0}
.strow i{width:8px;height:8px;border-radius:50%}
.strow .bar{height:5px;background:var(--line);border-radius:3px;overflow:hidden}
.strow .bar b{display:block;height:100%;width:0;transition:width .3s}
.strow em{font-style:normal;color:var(--fg);text-align:right;font-variant-numeric:tabular-nums}
.strow.cur{color:var(--fg)}
.plog{font:400 11px/1.35 var(--font-mono);display:flex;flex-direction:column;gap:5px;min-height:150px}
.plog div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
canvas#cv{height:clamp(520px,54vw,680px)}
@media (max-width:700px){canvas#cv{height:600px}.score .n{font-size:48px}.stsub{display:none}}

.score .n.pop{animation:pop .22s ease-out}
@keyframes pop{0%{transform:scale(1.22);filter:brightness(1.8)}100%{transform:none;filter:none}}
.stt.flashin{animation:stin .6s ease-out}
@keyframes stin{0%{opacity:0;transform:translateX(-18px) scale(1.12);filter:brightness(2.2)}100%{opacity:1;transform:none;filter:none}}
.score .n{transform-origin:left center;text-shadow:0 0 36px rgba(61,220,155,.75)}
@media (prefers-reduced-motion:reduce){.score .n.pop,.stt.flashin{animation:none}}

body.mx{--bg:#000;--stage:#000;--surface:#010a04;--line:#0c3318;--fg:#c9ffd9;--muted:#4ea875;--pass:#00ff66;--cool:#35ff8e}
body.mx .stage{box-shadow:0 0 90px rgba(0,255,100,.14);border-color:#0f4a22}
body.mx h1{text-shadow:0 0 14px rgba(0,255,100,.6);letter-spacing:.08em}
body.mx .badge{color:#00ff66;border-color:#00ff66}
body.mx .pane,body.mx .btn{border-color:#0c3318}
</style></head><body>
<main class="wrap">
  <header class="top">
    <div>
      <h1>Rule flow</h1>
      <p class="sub">Your last scan, replayed. A hub visits clusters of tickers one filter at a time. Stocks that fail switch off in that filter's color; survivors get a green thread back to the hub. Visualization only, nothing here places trades.</p>
    </div>
    <div class="badge">Your scan data</div>
  </header>

  <div class="toolbar">
    <button id="replay" class="btn" type="button">Replay</button>
    <button id="mxbtn" class="btn" type="button" aria-pressed="true">Matrix</button>
        <div class="summary" id="summary" aria-live="polite"></div>
  </div>

  <section class="stage" id="stage">
    <canvas id="cv" role="img" aria-label="Dense clusters of sample tickers, one per sector. A scanner hub visits the clusters stage by stage, one rule per stage, and stocks that fail turn off in that rule's color. Survivors are linked to the hub in green. "></canvas>
    <div class="ov seg" id="seg"></div>
    <div class="ov stt"><div class="stn" id="stNum">01</div><div><div class="stname" id="stName">PRICE</div><div class="stsub" id="stSub"></div></div></div>
    <div class="ov score"><div class="n" id="hPass">0</div><div class="l" id="hSub"></div></div>
    <div class="tip" id="tip" hidden></div>
    <div class="legend"><span>one hub, six stages, one rule each</span><span>colored flash = stopped by that rule</span><span>green = passed all five</span><span>hover a dot for its numbers</span></div>
  </section>

  <div class="hud">
    <div class="pane"><h2>Stages <span>stopped per rule</span></h2><div id="stRows" class="strows"></div></div>
    <div class="pane"><h2>Funnel <span>tickers still in</span></h2><canvas id="fun" class="pc" aria-label="Line of tickers remaining after each rule"></canvas></div>
    <div class="pane"><h2>Sectors <span>still in vs start</span></h2><canvas id="rad" class="pc" aria-label="Radar of surviving tickers per sector"></canvas></div>
    <div class="pane"><h2>Who stops where <span>rule &times; sector</span></h2><canvas id="mat" class="pc" aria-label="Matrix of stops by rule and sector"></canvas></div>
    <div class="pane"><h2>Hit rate</h2>
      <div class="gauge"><svg viewBox="0 0 132 132" aria-hidden="true"><circle cx="66" cy="66" r="54" fill="none" stroke="var(--line)" stroke-width="9"/><circle id="ring" cx="66" cy="66" r="54" fill="none" stroke="var(--pass)" stroke-width="9" stroke-linecap="round" stroke-dasharray="0 340" transform="rotate(-90 66 66)" style="filter:drop-shadow(0 0 6px var(--pass));transition:stroke-dasharray .25s"/><text id="gp" class="big" x="66" y="72" text-anchor="middle">0%</text><text class="lab" x="66" y="90" text-anchor="middle">STILL IN</text></svg><p id="ins"></p></div></div>
    <div class="pane"><h2>Decision log <span>latest first</span></h2><div id="hLog" class="plog"></div></div>
    <div class="pane"><h2>scanner.py <span>live</span></h2><div class="code" id="code"></div></div>
  </div>
</main>
<script>
(function(){
"use strict";
var DATA=__DATA__;
var $=function(id){return document.getElementById(id)};
var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
var SECT=['Tech','Health','Energy','Finance','Consumer','Industrial'];
var RHEX=['#ffa940','#ff4fa3','#9b7bff','#ff5a4a','#4de3ff','#ffd23f'];
var RULES=DATA.rules,NG=RULES.length;
function mulberry(a){return function(){a|=0;a=a+0x6D2B79F5|0;var t=Math.imul(a^a>>>15,1|a);t=t+Math.imul(t^t>>>7,61|t)^t;return((t^t>>>14)>>>0)/4294967296}}
function fmtVol(v){return v>=1e6?(v/1e6).toFixed(1)+'M':Math.round(v/1e3)+'K'}
function passRule(r,s){
  var v=s[r.field];
  if(r.field==='gap')return !!s.gap;
  if(r.field==='rsi'&&v==null)return true;
  if(v==null)return false;
  if(r.lo!=null&&v<r.lo)return false;
  if(r.hi!=null&&v>r.hi)return false;
  return true;
}
var stocks=DATA.stocks,N=stocks.length,passN=0;
stocks.forEach(function(s){
  s.pass=RULES.map(function(r){return passRule(r,s)});
  s.failIdx=s.pass.indexOf(false);s.failN=s.pass.filter(function(x){return !x}).length;
  if(s.failIdx<0)passN++;
});
var SECT_ORDER=0;
var codeEls=[];
function buildCode(){
  var ch=$('code');ch.textContent='';
  var head=document.createElement('div');head.innerHTML='<span>for ticker in scan(hub):</span><span>'+N+'</span>';head.style.borderLeftColor='transparent';ch.appendChild(head);
  RULES.forEach(function(r,ri){
    var d=document.createElement('div');d.style.borderLeftColor=RHEX[ri];
    var a=document.createElement('span');a.textContent='  '+r.code+'  reject';var b=document.createElement('span');b.textContent='0';
    d.appendChild(a);d.appendChild(b);ch.appendChild(d);codeEls.push(d);
  });
  var tail=document.createElement('div');tail.style.borderLeftColor='var(--pass)';tail.innerHTML='<span>hub.link(ticker)  # passed</span><span>0</span>';ch.appendChild(tail);codeEls.push(tail);
}
var NGsum=RULES.map(function(r){return r.name}).join(', ');
/* ---------- stage: dense clusters, one hub that visits them ---------- */
var stage=$('stage'),cv=$('cv'),tip=$('tip'),ctx=cv.getContext('2d');
var RC=RHEX.map(function(h){var n=parseInt(h.slice(1),16);return [n>>16&255,n>>8&255,n&255]});
var SECC=[[95,160,255],[77,235,184],[255,158,71],[255,217,102],[255,122,191],[179,128,255]];
var SAB=['TEC','HLT','ENR','FIN','CON','IND'];
var STG=RULES.map(function(r){return r.short}).concat(['SHIP']);
var STC=RC.slice(0,NG).concat([[61,220,155]]);
var SD=3.0,TOTAL=SD*(NG+1);
function zeros(n){var a=[];for(var i=0;i<n;i++)a.push(0);return a}
function ci(st){return st>=0&&st<NG?st%6:-1}
var CLP=[[.26,.36],[.73,.30],[.19,.76],[.52,.62],[.83,.74],[.47,.17]];
var W=0,H=0,dpr=1,M=100,sig=40,cl=[],dust=[],haze=null;
var hub={x:0,y:0,tx:0,ty:0,col:[95,200,255]};
var run={t:0,active:false,stage:-1,ptr:0,q:[]};
var clock=0,aliveN=N,kills=zeros(NG),killMat=[],passed=0,threads=[],logItems=[],funnel=[N],act=[.5,.5,.5,.5,.5,.5],corePulse=0,lastDecision={i:-1,t:-9};
var hudDirty=true,hudT=0,tagBudget=0,sparks=[],rings=[],trail=[],flash=0,lastDt=.016,shownStage=-9,shownAlive=-1;
function spark(x,y,c,n,sp){for(var i=0;i<n;i++){var a=Math.random()*6.283,v=sp*(.3+Math.random());sparks.push({x:x,y:y,vx:Math.cos(a)*v,vy:Math.sin(a)*v,life:1,d:.9+Math.random()*.9,c:c})}}
function g2(r){var u=0;while(!u)u=r();return Math.sqrt(-2*Math.log(u))*Math.cos(6.2832*r())}
(function(){var r2=mulberry(31);stocks.forEach(function(s,i){s.gx=g2(r2);s.gy=g2(r2);s.cv=((i*37)%11/11-.5)*.5;s.ph=(i*.618)%1;s.dead=false;s.lit=false;s.failT=-9;s.sx=0;s.sy=0})})();
function rgba(c,a){return 'rgba('+c[0]+','+c[1]+','+c[2]+','+a+')'}
function mix(a,b,k){return [Math.round(a[0]+(b[0]-a[0])*k),Math.round(a[1]+(b[1]-a[1])*k),Math.round(a[2]+(b[2]-a[2])*k)]}
function ease(t){return 1-Math.pow(1-t,3)}
function buildDust(){
  var r3=mulberry(77),n=W<700?1100:2100,j,i;
  dust=[];
  for(j=0;j<6;j++){
    var c=document.createElement('canvas');c.width=Math.round(W*dpr);c.height=Math.round(H*dpr);
    var g=c.getContext('2d');g.scale(dpr,dpr);
    var bl=g.createRadialGradient(cl[j][0],cl[j][1],0,cl[j][0],cl[j][1],sig*3);
    bl.addColorStop(0,rgba(SECC[j],.2));bl.addColorStop(.5,rgba(SECC[j],.07));bl.addColorStop(1,rgba(SECC[j],0));
    g.fillStyle=bl;g.fillRect(0,0,W,H);
    for(i=0;i<n;i++){
      var wide=r3()<.18,k=wide?2.9:1.25,x=cl[j][0]+g2(r3)*sig*k,y=cl[j][1]+g2(r3)*sig*k*.9;
      if(x<2||y<2||x>W-2||y>H-2)continue;
      var col=mix(SECC[j],[255,255,255],r3()*.65),sz=r3()<.12?2:1.1;
      g.fillStyle=rgba(col,.22+.6*r3());g.fillRect(x,y,sz,sz);
    }
    dust.push(c);
  }
  haze=document.createElement('canvas');haze.width=Math.round(W*dpr);haze.height=Math.round(H*dpr);
  var hg=haze.getContext('2d');hg.scale(dpr,dpr);
  for(i=0;i<(W<700?700:1500);i++){hg.fillStyle='rgba(190,215,235,'+(.08+.25*r3())+')';hg.fillRect(r3()*W,r3()*H,1.1,1.1)}
}
var MX=true,MXG=[90,255,150],MXF="'Courier New',ui-monospace,Menlo,Consolas,monospace",rain=[],rainCW=14;
var MXC='ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ$%+-=<>';
try{MX=localStorage.getItem('rf_mx')!=='0'}catch(e){}
function body_mx(){document.body.classList.toggle('mx',MX);var b=$('mxbtn');if(b)b.setAttribute('aria-pressed',MX?'true':'false')}
function buildRain(){
  rain=[];var n=Math.ceil(W/rainCW),i,j;
  for(i=0;i<n;i++){var len=8+Math.floor(Math.random()*22),ch=[];for(j=0;j<len;j++)ch.push(Math.floor(Math.random()*MXC.length));rain.push({y:Math.random()*H,v:50+Math.random()*130,len:len,ch:ch})}
}
function drawRain(dt){
  ctx.font='12px '+MXF;ctx.textAlign='center';ctx.textBaseline='middle';
  var sg=sig*2.4,i,j,k,r,x,y,m,al,dx,dy,e;
  for(i=0;i<rain.length;i++){
    r=rain[i];x=i*rainCW+rainCW/2;
    if(!reduce){
      r.y+=r.v*dt;
      if(r.y-r.len*rainCW>H){r.y=-Math.random()*H*.3;r.v=50+Math.random()*130}
      if(Math.random()<.08)r.ch[Math.floor(Math.random()*r.len)]=Math.floor(Math.random()*MXC.length);
    }
    for(j=0;j<r.len;j++){
      y=r.y-j*rainCW;if(y<-14||y>H+14)continue;
      m=0;for(k=0;k<6;k++){dx=x-cl[k][0];dy=y-cl[k][1];e=act[k]*Math.exp(-(dx*dx+dy*dy)/(2*sg*sg));if(e>m)m=e}
      al=(.14+.86*Math.min(1,m*1.3))*Math.pow(1-j/r.len,1.4);
      if(al<.03)continue;
      ctx.fillStyle=j===0?'rgba(220,255,230,'+Math.min(1,al*1.6)+')':'rgba(0,255,90,'+al+')';
      ctx.fillText(MXC.charAt(r.ch[j]),x,y);
    }
  }
  for(k=0;k<6;k++){
    var gg=ctx.createRadialGradient(cl[k][0],cl[k][1],0,cl[k][0],cl[k][1],sig*2.4);
    gg.addColorStop(0,'rgba(0,255,100,'+(.09*act[k])+')');gg.addColorStop(1,'rgba(0,255,100,0)');
    ctx.fillStyle=gg;ctx.fillRect(cl[k][0]-sig*2.4,cl[k][1]-sig*2.4,sig*4.8,sig*4.8);
  }
}
function glyphCurve(s,g,x0,y0,col,a){
  var n=Math.max(3,Math.round(Math.hypot(s.sx-x0,s.sy-y0)*g/11)),q,seed=reduce?0:Math.floor(clock*10),ch;
  ctx.strokeStyle=rgba(col,a*.2);ctx.lineWidth=1;strokeCurve(s,g,x0,y0);
  ctx.font='12px '+MXF;ctx.textAlign='center';ctx.textBaseline='middle';
  for(q=1;q<=n;q++){
    curve(s,g*q/n,x0,y0,tp);ch=MXC.charAt((q*7+seed*3+((s.ph*97)|0))%MXC.length);
    ctx.fillStyle=rgba((q===n&&g<1)?[220,255,230]:col,a*(.55+.45*q/n));ctx.fillText(ch,tp.x,tp.y);
  }
}
function resize(){
  W=cv.clientWidth;H=cv.clientHeight;if(W<50||H<50)return;dpr=Math.min(2,window.devicePixelRatio||1);
  cv.width=Math.round(W*dpr);cv.height=Math.round(H*dpr);
  M=Math.min(W,H*1.15);sig=M*(W<700?.075:.082);
  cl=CLP.map(function(p){return [p[0]*W,p[1]*H]});
  stocks.forEach(function(s){
    s.sx=Math.max(22,Math.min(W-22,cl[s.sec][0]+s.gx*sig*.9));s.sy=Math.max(74,Math.min(H-30,cl[s.sec][1]+s.gy*sig*.8));
  });
  buildDust();buildRain();
  if(!run.active){hub.x=hub.tx=W/2;hub.y=hub.ty=H/2}
  begin();
}
/* ---------- scan state ---------- */
function resetScan(){
  stocks.forEach(function(s){s.dead=false;s.lit=false;s.failT=-9});
  aliveN=N;kills=zeros(NG);passed=0;threads=[];sparks=[];rings=[];trail=[];logItems=[];funnel=[N];hudDirty=true;
  killMat=[];for(var k=0;k<NG;k++)killMat.push([0,0,0,0,0,0]);
}
function pushLog(s,fail){
  logItems.unshift({t:s.t,ok:!fail,why:fail?'stopped at '+RULES[s.failIdx].name:'passed all rules',c:fail?RHEX[s.failIdx]:'#3ddc9b'});
  if(logItems.length>8)logItems.pop();hudDirty=true;
}
function beginStage(st){
  if(st>0)funnel.push(aliveN);
  run.stage=st;run.ptr=0;
  var c0=ci(st),hx=c0>=0?cl[c0][0]:W/2,hy=c0>=0?cl[c0][1]:H/2;
  hub.tx=hx;hub.ty=hy;hub.col=STC[st];flash=1;
  if(!reduce)rings.push({x:hx,y:hy,born:clock+.55,col:STC[st],max:Math.max(W,H)*.6,dur:1.4,w:3});
  var list=stocks.filter(function(s){return !s.dead});
  list.sort(function(a,b){return Math.hypot(a.sx-hx,a.sy-hy)-Math.hypot(b.sx-hx,b.sy-hy)});
  var t0=st*SD+.2,span=SD*(st<NG?.76:.6);
  list.forEach(function(s,i){s.tt=t0+i/Math.max(1,list.length)*span});
  run.q=list;hudDirty=true;
}
function processStock(s,st){
  var fail=st<NG&&s.failIdx===st;
  if(st===NG){
    spark(s.sx,s.sy,[120,255,200],18,110);rings.push({x:s.sx,y:s.sy,born:clock,col:[61,220,155],max:46,dur:.9,w:2});s.lit=true;passed++;corePulse=1;
    threads.push({s:s,k:5,born:clock,fail:false,persist:true});pushLog(s,false);
  }else if(fail){
    spark(s.sx,s.sy,RC[st],9,85);s.dead=true;s.failT=clock;aliveN--;kills[st]++;killMat[st][s.sec]++;
    threads.push({s:s,k:st,born:clock,fail:true,persist:false});pushLog(s,true);
  }else threads.push({s:s,k:st,born:clock,fail:false,persist:false});
  lastDecision={i:fail?st:(st===NG?NG:-1),t:clock};
}
function startRun(){resetScan();run.t=0;run.active=true;run.stage=-1;hub.x=W/2;hub.y=H/2;beginStage(0)}
function settleAll(){
  resetScan();run.active=false;run.stage=NG;
  var c=[N],k;for(k=0;k<NG;k++)c.push(0);
  stocks.forEach(function(s){
    if(s.failIdx>=0){s.dead=true;s.failT=-9;kills[s.failIdx]++;killMat[s.failIdx][s.sec]++}
    else{s.lit=true;passed++;threads.push({s:s,k:5,born:-9,fail:false,persist:true})}
    for(k=0;k<NG;k++)if(s.failIdx<0||s.failIdx>k)c[k+1]++;
  });
  funnel=c;aliveN=passed;hub.x=hub.tx=W/2;hub.y=hub.ty=H/2;hub.col=STC[5];
  for(k=0;k<6;k++)act[k]=.6;
  order6().slice(-6).forEach(function(s){logItems.push({t:s.t,ok:s.failIdx<0,why:s.failIdx<0?'passed all rules':'stopped at '+RULES[s.failIdx].name,c:s.failIdx<0?'#3ddc9b':RHEX[s.failIdx]})});
  hudDirty=true;
}
function order6(){return stocks.slice().sort(function(a,b){return a.sx-b.sx})}
/* ---------- drawing ---------- */
var tp={x:0,y:0};
function curve(s,u,x0,y0,o){
  var dx=s.sx-x0,dy=s.sy-y0,mx=x0+dx/2-dy*s.cv,my=y0+dy/2+dx*s.cv,v=1-u;
  o.x=v*v*x0+2*v*u*mx+u*u*s.sx;o.y=v*v*y0+2*v*u*my+u*u*s.sy;
}
function strokeCurve(s,g,x0,y0){
  var n=Math.max(2,Math.round(26*g)),q;
  ctx.beginPath();for(q=0;q<=n;q++){curve(s,g*q/n,x0,y0,tp);if(q)ctx.lineTo(tp.x,tp.y);else ctx.moveTo(tp.x,tp.y)}ctx.stroke();
}
function reasonText(s,k){
  var f=RULES[k].field;
  return f==='price'?'$'+s.price.toFixed(1):f==='chg'?(s.chg>=0?'+':'')+s.chg.toFixed(1)+'%':f==='vol'?fmtVol(s.vol):f==='spike'?s.spike.toFixed(2)+'\u00d7':f==='rsi'?'RSI '+Math.round(s.rsi):'no gap';
}
function draw(){
  var wt=reduce?0:clock,i,k;
  ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,cv.width,cv.height);
  ctx.globalCompositeOperation='lighter';
  if(haze&&!MX)ctx.drawImage(haze,0,0);
  for(k=0;k<6;k++){
    var tgt=run.active?(ci(run.stage)===k?1:(run.stage===NG?.5:0)):.55;
    act[k]+=(tgt-act[k])*.05;
    if(!MX){ctx.globalAlpha=.5+.5*act[k];ctx.drawImage(dust[k],0,0)}
  }
  ctx.globalAlpha=1;ctx.setTransform(dpr,0,0,dpr,0,0);
  if(MX)drawRain(lastDt);
  if(run.active&&run.stage>=0&&run.stage<NG){
    var c0=cl[ci(run.stage)],bg=ctx.createRadialGradient(c0[0],c0[1],0,c0[0],c0[1],sig*3.2);
    bg.addColorStop(0,rgba(MX?MXG:STC[run.stage],.22));bg.addColorStop(1,rgba(MX?MXG:STC[run.stage],0));ctx.fillStyle=bg;ctx.fillRect(0,0,W,H);
  }
  // stocks
  stocks.forEach(function(s){
    var sc=MX?MXG:SECC[s.sec];
    if(s.lit){
      var gl=ctx.createRadialGradient(s.sx,s.sy,0,s.sx,s.sy,15);gl.addColorStop(0,'rgba(61,220,155,.6)');gl.addColorStop(1,'rgba(61,220,155,0)');
      ctx.fillStyle=gl;ctx.beginPath();ctx.arc(s.sx,s.sy,15,0,6.283);ctx.fill();
      ctx.fillStyle='#caffe8';ctx.beginPath();ctx.arc(s.sx,s.sy,3.4,0,6.283);ctx.fill();
    }else if(s.dead){
      var t=clock-s.failT,c=RC[s.failIdx];
      if(t<.9){ctx.strokeStyle=rgba(c,.9*(1-t/.9));ctx.lineWidth=1.4;ctx.beginPath();ctx.arc(s.sx,s.sy,3+15*ease(t/.9),0,6.283);ctx.stroke()}
      ctx.fillStyle=rgba(c,.42);ctx.beginPath();ctx.arc(s.sx,s.sy,1.7,0,6.283);ctx.fill();
    }else{
      ctx.fillStyle=rgba(mix(sc,[255,255,255],.45),.95);ctx.beginPath();ctx.arc(s.sx,s.sy,2.2,0,6.283);ctx.fill();
    }
  });
  // threads
  var keep=[];
  threads.forEach(function(th){
    var age=clock-th.born,s=th.s,g=ease(Math.min(1,age/.5)),a,x0=th.persist?W/2:hub.x,y0=th.persist?H/2:hub.y;
    if(th.persist){a=.5}
    else if(th.fail){if(age>1.9)return;a=age<1.1?.95:.95*(1-(age-1.1)/.8)}
    else{if(age>1.2)return;a=.4*(1-Math.max(0,(age-.4)/.8))}
    keep.push(th);
    var col=th.persist?[61,220,155]:(th.fail?RC[th.k]:(MX?MXG:mix(STC[th.k],[255,255,255],.3)));
    if(MX){glyphCurve(s,g,x0,y0,col,a)}else{
    if(th.fail||th.persist){ctx.strokeStyle=rgba(col,a*.22);ctx.lineWidth=6;strokeCurve(s,g,x0,y0)}
    ctx.strokeStyle=rgba(col,a);ctx.lineWidth=th.fail?1.4:(th.persist?1.3:.8);strokeCurve(s,g,x0,y0)}
    if(g<1){curve(s,g,x0,y0,tp);ctx.fillStyle='rgba(255,255,255,.95)';ctx.beginPath();ctx.arc(tp.x,tp.y,2.6,0,6.283);ctx.fill()}
    else if(th.persist&&!reduce){curve(s,(wt*.16+s.ph)%1,x0,y0,tp);ctx.fillStyle='rgba(190,255,225,.9)';ctx.beginPath();ctx.arc(tp.x,tp.y,1.8,0,6.283);ctx.fill()}
  });
  threads=keep;
  // tags on failures (cap), labels on survivors
  ctx.font='500 10px "IBM Plex Mono",ui-monospace,monospace';ctx.textBaseline='middle';ctx.textAlign='left';
  var tags=0;ctx.globalCompositeOperation='source-over';
  for(i=threads.length-1;i>=0&&tags<8;i--){
    var th2=threads[i];if(!th2.fail)continue;var ag=clock-th2.born;if(ag<.35||ag>1.7)continue;
    var s2=th2.s,txt=s2.t+'  '+reasonText(s2,th2.k),w=ctx.measureText(txt).width+12,bx=Math.min(W-w-4,s2.sx+8),by=s2.sy-18;
    var ta=(ag>1.3?(1.7-ag)/.4:1);
    if(MX){ctx.fillStyle='rgba(0,8,3,.92)';ctx.fillRect(bx,by,w,15);ctx.strokeStyle=rgba(RC[th2.k],.9*ta);ctx.lineWidth=1;ctx.strokeRect(bx+.5,by+.5,w-1,14);ctx.fillStyle=rgba(RC[th2.k],ta);ctx.fillText(txt,bx+6,by+8);tags++;continue}
    ctx.fillStyle=rgba(RC[th2.k],.88*ta);ctx.fillRect(bx,by,w,15);
    ctx.fillStyle='#05080c';ctx.fillText(txt,bx+6,by+8);tags++;
  }
  ctx.font='500 11px "IBM Plex Mono",ui-monospace,monospace';
  stocks.forEach(function(s){
    if(!s.lit)return;
    var left=s.sx>W-80;ctx.textAlign=left?'right':'left';ctx.fillStyle='#d8fff0';ctx.shadowColor='#000';ctx.shadowBlur=4;
    ctx.fillText(s.t,s.sx+(left?-9:9),s.sy-8);ctx.shadowBlur=0;
  });
  // sector names
  ctx.font='500 10px "IBM Plex Mono",ui-monospace,monospace';ctx.textAlign='center';
  for(k=0;k<6;k++){ctx.fillStyle=rgba(MX?MXG:SECC[k],.4+.5*act[k]);ctx.fillText(SECT[k].toUpperCase(),cl[k][0],Math.min(H-12,cl[k][1]+sig*2.1))}
  // flash, shockwaves, sparks, trail
  if(flash>0){ctx.fillStyle=rgba(MX?MXG:hub.col,.1*flash);ctx.fillRect(0,0,W,H)}
  rings=rings.filter(function(rg){return clock-rg.born<rg.dur});
  rings.forEach(function(rg){var a=(clock-rg.born)/rg.dur;if(a<0)return;ctx.strokeStyle=rgba(MX?MXG:rg.col,.7*(1-a));ctx.lineWidth=rg.w*(1-a*.5)+.5;ctx.beginPath();ctx.arc(rg.x,rg.y,rg.max*ease(a),0,6.283);ctx.stroke()});
  sparks=sparks.filter(function(p){return p.life>0});
  sparks.forEach(function(p){p.x+=p.vx*lastDt;p.y+=p.vy*lastDt;p.vx*=.95;p.vy*=.95;p.life-=lastDt/p.d;ctx.fillStyle=rgba(p.c,Math.max(0,p.life));ctx.fillRect(p.x-1,p.y-1,2.2,2.2)});
  if(trail.length>1){for(i=1;i<trail.length;i++){ctx.strokeStyle=rgba(MX?MXG:hub.col,.5*i/trail.length);ctx.lineWidth=1+4*i/trail.length;ctx.beginPath();ctx.moveTo(trail[i-1][0],trail[i-1][1]);ctx.lineTo(trail[i][0],trail[i][1]);ctx.stroke()}}
  // hub: urchin
  ctx.globalCompositeOperation='lighter';
  var hc=MX?MXG:hub.col,pul=1+.05*Math.sin(wt*1.6)+corePulse*.3,boost=run.active?1.35:1;
  var hg=ctx.createRadialGradient(hub.x,hub.y,0,hub.x,hub.y,52*pul);hg.addColorStop(0,'rgba(255,255,255,.9)');hg.addColorStop(.18,rgba(mix(hc,[255,255,255],.5),.5));hg.addColorStop(1,rgba(hc,0));
  ctx.fillStyle=hg;ctx.beginPath();ctx.arc(hub.x,hub.y,52*pul,0,6.283);ctx.fill();
  ctx.strokeStyle=rgba(mix(hc,[255,255,255],.35),.6);ctx.lineWidth=1;ctx.beginPath();
  for(i=0;i<72;i++){
    var a=i/72*6.283,h1=((i*53)%17)/17,len=(12+h1*26+Math.sin(wt*1.3+i*.7)*4)*boost;
    var x1=hub.x+Math.cos(a)*10*pul,y1=hub.y+Math.sin(a)*10*pul,x2=hub.x+Math.cos(a+.1*Math.sin(wt+i))*(10+len),y2=hub.y+Math.sin(a+.1*Math.sin(wt+i))*(10+len);
    ctx.moveTo(x1,y1);ctx.lineTo(x2,y2);
  }
  ctx.stroke();
  ctx.strokeStyle=rgba(mix(hc,[255,255,255],.5),.95);ctx.lineWidth=1.6;ctx.beginPath();ctx.arc(hub.x,hub.y,9*pul,0,6.283);ctx.stroke();
  ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(hub.x,hub.y,3,0,6.283);ctx.fill();
  ctx.globalCompositeOperation='source-over';
}
function frame(ts){
  if(!started||!dust.length){requestAnimationFrame(frame);return}
  var dt=Math.min(.05,(ts-frame.last)/1000||0);frame.last=ts;clock+=dt;lastDt=dt;flash=Math.max(0,flash-dt*1.6);
  if(run.active){
    run.t+=dt;var st=Math.min(NG,Math.floor(run.t/SD));
    if(st!==run.stage)beginStage(st);
    while(run.ptr<run.q.length&&run.t>=run.q[run.ptr].tt){processStock(run.q[run.ptr],run.stage);run.ptr++}
    if(run.t>=TOTAL+.9){run.active=false;hub.tx=W/2;hub.ty=H/2;hudDirty=true;rings.push({x:W/2,y:H/2,born:clock,col:[61,220,155],max:Math.max(W,H)*.7,dur:1.8,w:4});flash=1}
  }
  var kk=Math.min(1,dt*2.4);hub.x+=(hub.tx-hub.x)*kk;hub.y+=(hub.ty-hub.y)*kk;
  if(!reduce&&Math.hypot(hub.tx-hub.x,hub.ty-hub.y)>6){trail.push([hub.x,hub.y]);if(trail.length>22)trail.shift()}else if(trail.length)trail.shift();
  corePulse=Math.max(0,corePulse-dt*1.8);
  draw();
  hudT+=dt;if(hudT>.12){hudT=0;renderHud()}
  requestAnimationFrame(frame);
}
frame.last=0;

/* ---------- HUD ---------- */
function cvs(id){var c=$(id),d=Math.min(2,window.devicePixelRatio||1),w=c.clientWidth,h=c.clientHeight;
  if(c.width!==Math.round(w*d)||c.height!==Math.round(h*d)){c.width=Math.round(w*d);c.height=Math.round(h*d)}
  var g=c.getContext('2d');g.setTransform(d,0,0,d,0,0);g.clearRect(0,0,w,h);return {g:g,w:w,h:h}}
var MONO='500 10px "IBM Plex Mono",ui-monospace,monospace';
function drawFunnel(){
  var o=cvs('fun'),g=o.g,w=o.w,h=o.h,L=26,B=20,T=18,R=14,pts=funnel,i;
  g.font=MONO;g.textBaseline='middle';
  for(i=0;i<=3;i++){var y=T+(h-T-B)*i/3;g.strokeStyle='rgba(120,160,185,.14)';g.beginPath();g.moveTo(L,y);g.lineTo(w-R,y);g.stroke();g.fillStyle='#7b97a4';g.textAlign='right';g.fillText(Math.round(N*(1-i/3)),L-5,y)}
  var lab=['ALL'].concat(RULES.map(function(r){return r.short.slice(0,3)}));
  function X(k){return L+(w-L-R)*k/NG}function Y(v){return T+(h-T-B)*(1-v/N)}
  g.textAlign='center';lab.forEach(function(t,k){g.fillStyle=k<pts.length?'#b9cfda':'#4b6470';g.fillText(t,X(k),h-8)});
  if(pts.length<1)return;
  var gr=g.createLinearGradient(0,T,0,h-B);gr.addColorStop(0,'rgba(61,220,155,.35)');gr.addColorStop(1,'rgba(61,220,155,0)');
  g.beginPath();g.moveTo(X(0),h-B);pts.forEach(function(v,k){g.lineTo(X(k),Y(v))});g.lineTo(X(pts.length-1),h-B);g.closePath();g.fillStyle=gr;g.fill();
  g.strokeStyle='#3ddc9b';g.lineWidth=2;g.beginPath();pts.forEach(function(v,k){if(k)g.lineTo(X(k),Y(v));else g.moveTo(X(k),Y(v))});g.stroke();g.lineWidth=1;
  pts.forEach(function(v,k){g.fillStyle='#caffe8';g.beginPath();g.arc(X(k),Y(v),3,0,6.283);g.fill();g.fillStyle='#e2f0f6';g.textAlign='center';g.fillText(v,X(k),Y(v)-10)});
}
function drawRadar(){
  var o=cvs('rad'),g=o.g,w=o.w,h=o.h,cx=w/2,cy=h/2+2,R=Math.min(w,h)/2-22,k,j;
  var init=[0,0,0,0,0,0],al=[0,0,0,0,0,0];
  stocks.forEach(function(s){init[s.sec]++;if(!s.dead)al[s.sec]++});
  var mx=Math.max.apply(null,init);
  function P(j,v){var a=-Math.PI/2+j*Math.PI/3;return [cx+Math.cos(a)*R*v,cy+Math.sin(a)*R*v]}
  g.font=MONO;g.textAlign='center';g.textBaseline='middle';
  [.33,.66,1].forEach(function(r){g.strokeStyle='rgba(120,160,185,.2)';g.beginPath();for(j=0;j<6;j++){var p=P(j,r);if(j)g.lineTo(p[0],p[1]);else g.moveTo(p[0],p[1])}g.closePath();g.stroke()});
  for(j=0;j<6;j++){var q=P(j,1),t=P(j,1.22);g.strokeStyle='rgba(120,160,185,.15)';g.beginPath();g.moveTo(cx,cy);g.lineTo(q[0],q[1]);g.stroke();g.fillStyle=rgba(SECC[j],.9);g.fillText(SAB[j],t[0],t[1])}
  g.beginPath();for(j=0;j<6;j++){var p2=P(j,init[j]/mx);if(j)g.lineTo(p2[0],p2[1]);else g.moveTo(p2[0],p2[1])}g.closePath();g.strokeStyle='rgba(150,190,215,.55)';g.setLineDash([3,3]);g.stroke();g.setLineDash([]);
  g.beginPath();for(j=0;j<6;j++){var p3=P(j,al[j]/mx);if(j)g.lineTo(p3[0],p3[1]);else g.moveTo(p3[0],p3[1])}g.closePath();g.fillStyle='rgba(61,220,155,.28)';g.fill();g.strokeStyle='#3ddc9b';g.lineWidth=1.6;g.stroke();g.lineWidth=1;
}
function drawMatrix(){
  var o=cvs('mat'),g=o.g,w=o.w,h=o.h,lw=58,top=16,cw=(w-lw)/6,rh=(h-top)/NG,k,j,mx=1;
  killMat.forEach(function(r){r.forEach(function(v){if(v>mx)mx=v})});
  g.font=MONO;g.textBaseline='middle';
  for(j=0;j<6;j++){g.fillStyle=rgba(SECC[j],.9);g.textAlign='center';g.fillText(SAB[j],lw+j*cw+cw/2,7)}
  for(k=0;k<NG;k++){
    g.fillStyle=RHEX[k];g.textAlign='left';g.fillText(RULES[k].short,0,top+k*rh+rh/2);
    for(j=0;j<6;j++){var v=killMat[k][j];g.fillStyle=rgba(RC[k],v?.16+.8*v/mx:.06);g.fillRect(lw+j*cw+1,top+k*rh+1,cw-2,rh-2);
      if(v){g.fillStyle=v/mx>.55?'#05080c':'#e2f0f6';g.textAlign='center';g.fillText(v,lw+j*cw+cw/2,top+k*rh+rh/2)}}
  }
}
var stRows=[];
function buildStageRows(){
  var host=$('stRows');host.textContent='';
  STG.forEach(function(nm,k){
    var d=document.createElement('div');d.className='strow';
    d.innerHTML='<i style="background:'+RHEXS(k)+'"></i><span>'+nm+'</span><span class="bar"><b style="background:'+RHEXS(k)+'"></b></span><em>0</em>';
    host.appendChild(d);stRows.push(d);
  });
}
function RHEXS(k){return k<NG?RHEX[k]:'#3ddc9b'}
function renderHud(){
  if(hudDirty){
    hudDirty=false;
    var host=$('hLog');host.textContent='';
    logItems.forEach(function(it){var d=document.createElement('div');d.style.color=it.c;d.textContent=(it.ok?'▶ ':'✕ ')+it.t+'  '+it.why;host.appendChild(d)});
  }
  var st=run.active?run.stage:NG,k;
  var hp=$('hPass');
  if(aliveN!==shownAlive){hp.textContent=aliveN;if(shownAlive>=0&&!reduce){hp.classList.remove('pop');void hp.offsetWidth;hp.classList.add('pop')}shownAlive=aliveN}
  $('hSub').textContent=run.active&&st<NG?'still in the running':(run.active?'linking survivors':'passed all rules');
  if(st!==shownStage){shownStage=st;var bn=document.querySelector('.stt');if(!reduce){bn.classList.remove('flashin');void bn.offsetWidth;bn.classList.add('flashin')}}
  $('stNum').textContent='0'+(st+1);$('stNum').style.color=RHEXS(st);
  $('stName').textContent=STG[st];
  $('stSub').textContent=st<NG?'keep '+RULES[st].fmt.replace('≥','at least')+' · '+(run.active?run.q.length:N)+' tested':(run.active?'':'Hover a dot for its numbers');
  var segs=$('seg').children;
  for(k=0;k<=NG;k++){
    var f=run.active?Math.max(0,Math.min(1,(run.t-k*SD)/SD)):1;
    segs[k].firstChild.style.width=(f*100)+'%';
  }
  var mk=Math.max.apply(null,kills.concat([1]));
  stRows.forEach(function(d,i){
    var v=i<NG?kills[i]:passed;
    d.lastChild.textContent=v;d.children[2].firstChild.style.width=(i<NG?v/mk*100:passed/Math.max(1,aliveN||passed)*100)+'%';
    d.classList.toggle('cur',run.active&&run.stage===i);
  });
  RULES.forEach(function(r,i){codeEls[i].lastChild.textContent='×'+kills[i]});
  codeEls[NG].lastChild.textContent='×'+passed;
  var hot=clock-lastDecision.t<.35?(lastDecision.i<0?-1:lastDecision.i):-1;
  codeEls.forEach(function(d,i){d.classList.toggle('hot',i===hot)});
  var rate=aliveN/N;
  $('gp').textContent=Math.round(rate*100)+'%';
  $('ring').setAttribute('stroke-dasharray',(339*rate).toFixed(1)+' 340');
  $('ins').innerHTML='<b>'+aliveN+'</b> of <b>'+N+'</b> tickers still in.<br>'+(run.active?'Stage '+(st+1)+' of '+(NG+1):'Scan complete');
  drawFunnel();drawRadar();drawMatrix();
}
/* ---------- interaction ---------- */
function showTip(e){
  var r=cv.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top,best=null,bd=14;
  stocks.forEach(function(s){var d=Math.hypot(s.sx-mx,s.sy-my);if(d<bd){bd=d;best=s}});
  if(!best){tip.hidden=true;return}
  var s=best;tip.textContent='';
  var l1=document.createElement('div');var b=document.createElement('b');b.textContent=s.t;l1.appendChild(b);l1.appendChild(document.createTextNode(' · '+s.sn));
  var l2=document.createElement('div');l2.textContent='$'+s.price.toFixed(2)+' · '+(s.chg>=0?'+':'')+s.chg.toFixed(2)+'% · vol '+fmtVol(s.vol);
  var l3=document.createElement('div');l3.textContent='spike '+s.spike.toFixed(2)+'× · RSI '+(s.rsi==null?'n/a':Math.round(s.rsi));
  var l4=document.createElement('div');
  if(s.lit){l4.textContent='Passes all rules';l4.style.color='var(--pass)'}
  else if(s.dead){l4.textContent='Stopped at '+RULES[s.failIdx].name+(s.failN>1?' (+'+(s.failN-1)+' more)':'');l4.style.color=RHEX[s.failIdx]}
  else{l4.textContent='Still in the running';l4.style.color='var(--muted)'}
  [l1,l2,l3,l4].forEach(function(x){tip.appendChild(x)});
  tip.hidden=false;tip.style.left=Math.min(mx+14,r.width-tip.offsetWidth-6)+'px';tip.style.top=Math.max(4,Math.min(my+14,r.height-tip.offsetHeight-6))+'px';
}
cv.addEventListener('pointermove',showTip);cv.addEventListener('pointerleave',function(){tip.hidden=true});
if(window.ResizeObserver)new ResizeObserver(resize).observe(cv);else window.addEventListener('resize',resize);


/* ---------- wiring ---------- */
var started=false;
body_mx();
function begin(){
  if(started||W<50)return;started=true;
  settleAll();renderHud();
  if(!reduce)setTimeout(startRun,700);
}
$('mxbtn').addEventListener('click',function(){MX=!MX;try{localStorage.setItem('rf_mx',MX?'1':'0')}catch(e){}body_mx()});
$('replay').addEventListener('click',function(){if(reduce)settleAll();else startRun()});
$('summary').textContent=passN+' of '+N+' tickers pass all '+NG+' rules';
buildCode();buildStageRows();
(function(){var h=$('seg');for(var k=0;k<=NG;k++){var i=document.createElement('i');i.innerHTML='<b style="background:'+(k<NG?RHEX[k]:'#3ddc9b')+'"></b>';h.appendChild(i)}})();
resize();
requestAnimationFrame(frame);
})();

</script></body></html>
'''

# yfinance sector names -> the six clusters shown in the picture.
_GROUPS = {
    0: ("technology", "communication"),
    1: ("healthcare", "health"),
    2: ("energy", "basic materials", "utilities"),
    3: ("financial", "real estate"),
    4: ("consumer",),
}  # anything else (Industrials, N/A, ...) lands in cluster 5


def _group(sector) -> int:
    s = str(sector or "").lower()
    for idx, keys in _GROUPS.items():
        if any(k in s for k in keys):
            return idx
    return 5


def _num(x):
    try:
        v = float(x)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None


def build_payload(scan_data, *, min_price, max_price, min_change, max_change,
                  min_vol_spike, min_volume, min_rsi, max_rsi,
                  gap_up_only=False) -> dict:
    """Turn raw scan rows + the current filter values into the JSON the page needs.

    The rules mirror the filter block in momentum_scanner.py, in this order:
    price, % change, volume, volume spike, RSI (+ gap up when switched on).
    A stock with no RSI passes the RSI rule, same as the scanner does.
    """
    stocks = []
    for d in scan_data or []:
        price, chg = _num(d.get("price")), _num(d.get("chg"))
        if price is None or chg is None:
            continue
        stocks.append({
            "t": str(d.get("ticker", "?"))[:8],
            "sec": _group(d.get("sector")),
            "sn": str(d.get("sector") or "N/A")[:24],
            "price": price, "chg": chg,
            "vol": _num(d.get("vol")) or 0.0,
            "spike": _num(d.get("vol_spike")) or 0.0,
            "rsi": _num(d.get("rsi")),
            "gap": bool(d.get("is_gap_up")),
        })

    lo_p, hi_p = float(min_price), float(max_price)
    lo_c, hi_c = float(min_change), float(max_change)
    lo_r, hi_r = float(min_rsi), float(max_rsi)
    vol_min, spike_min = float(min_volume), float(min_vol_spike)
    rules = [
        {"field": "price", "name": "Price", "short": "PRICE", "lo": lo_p, "hi": hi_p,
         "fmt": f"${lo_p:g} \u2013 ${hi_p:g}", "code": f"if not {lo_p:g} <= price <= {hi_p:g}:"},
        {"field": "chg", "name": "% change", "short": "CHANGE", "lo": lo_c, "hi": hi_c,
         "fmt": f"{lo_c:g}% \u2013 {hi_c:g}%", "code": f"if not {lo_c:g} <= change <= {hi_c:g}:"},
        {"field": "vol", "name": "Volume", "short": "VOLUME", "lo": vol_min if vol_min > 0 else None, "hi": None,
         "fmt": f"\u2265 {vol_min / 1e6:g}M shares", "code": f"if volume < {int(vol_min)}:"},
        {"field": "spike", "name": "Volume spike", "short": "SPIKE", "lo": spike_min if spike_min > 0 else None, "hi": None,
         "fmt": f"\u2265 {spike_min:g}\u00d7 average", "code": f"if spike < {spike_min:g}:"},
        {"field": "rsi", "name": "RSI", "short": "RSI", "lo": lo_r, "hi": hi_r,
         "fmt": f"{lo_r:g} \u2013 {hi_r:g}", "code": f"if not {lo_r:g} <= rsi <= {hi_r:g}:"},
    ]
    if gap_up_only:
        rules.append({"field": "gap", "name": "Gap up", "short": "GAP", "lo": None, "hi": None,
                      "fmt": "opened above prior close", "code": "if not is_gap_up:"})
    return {"stocks": stocks, "rules": rules}


def build_html(payload: dict) -> str:
    # "<" is escaped so a ticker or sector string can never close the <script> tag.
    data = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c")
    return _TEMPLATE.replace("__DATA__", data)


def render(scan_data, *, min_price=30, max_price=500, min_change=1.0, max_change=8.0,
           min_vol_spike=1.5, min_volume=1_000_000, min_rsi=48.0, max_rsi=75.0,
           gap_up_only=False, fallback_height=1500):
    """Draw the rule flow. Pass the raw (unfiltered) scan rows and the filter values."""
    if not scan_data:
        st.info("Run a scan on the Scanner tab first. This view replays that scan.")
        return
    payload = build_payload(
        scan_data, min_price=min_price, max_price=max_price, min_change=min_change,
        max_change=max_change, min_vol_spike=min_vol_spike, min_volume=min_volume,
        min_rsi=min_rsi, max_rsi=max_rsi, gap_up_only=gap_up_only)
    if not payload["stocks"]:
        st.warning("The last scan had no usable rows.")
        return
    html = build_html(payload)
    try:  # newer Streamlit: sizes itself to the page
        st.iframe(html, height="content", alt="Animated replay of the last scan, one filter at a time")
    except (AttributeError, TypeError):  # older Streamlit
        import streamlit.components.v1 as components
        components.html(html, height=fallback_height, scrolling=True)
    st.caption("Filters are read from the Filters box above, so you can change them and watch the same scan "
               "re-run without scanning again. The Scanner tab list only updates when you press Run Scan.")
