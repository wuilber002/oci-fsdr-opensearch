"""Self-contained modern web console served by the local FSDR API."""

DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenSearch FSDR</title>
<style>
:root{color-scheme:dark;--bg:#0b1120;--surface:#141e30;--line:#2b3e5e;--text:#e9eef8;--muted:#91a4c3;--blue:#60a5fa;--green:#4ade80;--yellow:#fbbf24;--pink:#f472d0;--radius:12px}
*{box-sizing:border-box}
html{scroll-behavior:smooth;scroll-padding-top:88px}
body{margin:0;background:var(--bg);color:var(--text);font:14px Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
.topbar{position:sticky;top:0;z-index:20;min-height:68px;padding:10px max(24px,calc((100vw - 1600px)/2));border-bottom:1px solid var(--line);background:rgba(17,26,42,.97);backdrop-filter:blur(14px);display:flex;gap:22px;align-items:center}
.brand{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:750;white-space:nowrap}
.brand-mark{display:block;width:36px;height:36px;object-fit:contain}
.top-nav{display:flex;align-items:center;gap:4px;flex:1}
.nav-item{border-radius:8px;padding:9px 11px;color:#9eb1cd;text-decoration:none;white-space:nowrap}
.nav-item:hover,.nav-item.active{background:#1b304f;color:#eaf3ff;font-weight:700}
.nav-icon{color:var(--blue);margin-right:5px}
.top-context{font-weight:700;white-space:nowrap}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--green);margin-right:7px}
.small{font-size:12px;color:var(--muted)}
.top-clock{color:#fff;font-size:12px;font-weight:800;white-space:nowrap}
.content{padding:30px;max-width:1600px;margin:auto}
.page-title{display:flex;justify-content:space-between;gap:24px;align-items:flex-start;margin-bottom:25px}
.eyebrow{color:var(--blue);font-size:10px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}
h1{font-size:30px;letter-spacing:-.035em;margin:6px 0 7px}
.subtitle{color:#a8b9d2;max-width:720px}
.status-banner{border:1px solid #244e71;background:#102a45;border-radius:10px;padding:12px 15px;color:#bbdefb;margin-bottom:18px}
.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin-bottom:22px}
.stat,.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius)}
.stat{padding:17px}
.stat-label{font-size:11px;letter-spacing:.08em;color:var(--muted);font-weight:750;text-transform:uppercase}
.stat-value{font-size:24px;font-weight:750;margin-top:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stat-note{margin-top:6px;color:var(--muted);font-size:12px}
.card{overflow:hidden}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.card-head,.section-head{padding:17px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}
.card-title,.section-head h2{font-size:16px;font-weight:750;margin:0}
.cluster-a .card-title{color:var(--yellow)}
.cluster-b .card-title{color:var(--pink)}
.role,.badge{font-size:11px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;border-radius:999px;padding:5px 9px}
.role{background:#17345a;color:#93c5fd}
.role.primary{background:#153b2c;color:#86efac}
.count{padding:16px 18px;display:flex;justify-content:space-between;color:var(--muted)}
.count strong{color:var(--green);font-size:20px}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:collapse}
th{background:rgba(20,30,48,.75);color:#91a4c3;text-align:left;font-size:10px;font-weight:800;letter-spacing:.07em;text-transform:uppercase}
th,td{padding:11px 18px;border-bottom:1px solid rgba(43,62,94,.65);white-space:nowrap}
td{color:#c9d6e8}
tr:last-child td{border-bottom:0}
.number{text-align:right;color:#85efad;font-variant-numeric:tabular-nums}
.index{font-weight:650;color:#e8eef9}
#comparison th,#comparison td,#comparison .number{text-align:center}
.badge{display:inline-flex;align-items:center;gap:5px}
.badge.ok{background:#133d2e;color:#7cf0ab}
.badge.warn{background:#4d3915;color:#fbd270}
.badge.error{background:#4a1f2a;color:#fda4af}
.badge.neutral{background:#25344c;color:#b7c6dc}
.status-icon{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;font-size:14px;font-weight:900}
.status-icon.ok{background:#133d2e;color:#7cf0ab}
.status-icon.warn{background:#4d3915;color:#fbd270}
.status-icon.error{background:#4a1f2a;color:#fda4af}
.status-icon.neutral{background:#25344c;color:#b7c6dc}
.status-cell{width:1%;padding-right:5px;text-align:center}
.job-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(420px,.85fr);gap:16px;margin-bottom:22px;align-items:stretch;height:620px}
.job-grid>.card{display:flex;flex-direction:column;min-height:0}
.job-grid>.card>.table-wrap,.job-grid>.card>.console{flex:1;min-height:0}
.console{margin:0;min-height:360px;overflow:auto;padding:18px;background:#090f19;color:#b4f4c7;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap}
.history-table{width:max-content;min-width:100%}
.history-table th,.history-table td{padding-top:8px;padding-bottom:8px}
.history-table .command{width:132px;white-space:normal;line-height:1.2}
.history-table .job{cursor:pointer}.history-table .job:hover td{background:#192943}.history-table .job.selected td{background:#27466f;color:#f0f7ff}
.empty{padding:22px;color:var(--muted);text-align:center}
.pagination{display:flex;align-items:center;justify-content:center;gap:10px;margin-top:auto;padding:12px 16px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
.pagination button{width:92px;border:1px solid var(--line);background:#18263b;color:#dbeafe;border-radius:7px;padding:6px 10px;font-weight:650}
.pagination button:disabled{opacity:.45;cursor:not-allowed}
.footer{margin-top:26px;padding-top:17px;border-top:1px solid var(--line);color:#7d91af;font-size:12px;display:flex;justify-content:space-between}
#clusters{display:none}
@media(max-width:1100px){.summary{grid-template-columns:repeat(2,1fr)}.job-grid{grid-template-columns:1fr;height:auto}.job-grid>.card{min-height:520px}.top-context{display:none}}
@media(max-width:820px){.topbar{padding:9px 18px;flex-wrap:wrap}.top-nav{order:3;flex-basis:100%;overflow:auto}.content{padding:20px}.cards,.summary{grid-template-columns:1fr}.page-title{display:block}.refresh{margin-top:14px}h1{font-size:25px}th,td{padding:10px 12px}.job-grid{grid-template-columns:minmax(0,1fr)}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand"><img class="brand-mark" src="/assets/fsdr-logo.png" alt="FSDR for OpenSearch logo"> FSDR for OpenSearch</div>
  <nav class="top-nav" aria-label="Dashboard sections">
    <a class="nav-item active" href="#overview"><span class="nav-icon">▦</span>Dashboard</a>
    <a class="nav-item" href="#comparison-section"><span class="nav-icon">◌</span>Cluster replication</a>
    <a class="nav-item" href="#active-job-section"><span class="nav-icon">≋</span>Current job</a>
    <a class="nav-item" href="#history-section"><span class="nav-icon">◫</span>Job history</a>
  </nav>
  <div class="top-context"><span class="dot"></span>Controller available</div>
  <span id="clock" class="top-clock"></span>
</header>
<main class="content">
  <div class="page-title"><div><h1>OpenSearch overview</h1><div class="eyebrow">Full Stack Disaster Recovery · Cross-Cluster Replication</div><div class="subtitle">Follow protected indices, current replication direction and FSDR jobs while the controller operates.</div></div></div>
  <div id="notice" class="status-banner" hidden></div>
  <section id="overview" class="summary"></section>
  <section id="comparison-section" class="card" style="margin-bottom:22px"><div class="section-head"><h2>Replicated indices comparison</h2><span id="topology" class="small">Waiting for topology</span></div><div id="comparison" class="cards" style="padding:16px;margin:0"></div></section>
  <section class="job-grid">
    <section id="active-job-section" class="card"><div class="section-head"><h2>Current controller job</h2><span id="active-job" class="small">No active job</span></div><pre id="active-console" class="console">No controller job is running.</pre></section>
    <section id="history-section" class="card"><div class="section-head"><h2>Controller job history</h2><span id="job-count" class="small"></span></div><div class="table-wrap"><table class="history-table"><thead><tr><th class="status-cell" aria-label="Status"></th><th>Command</th><th>Submitted</th><th>Finished</th></tr></thead><tbody id="jobs"></tbody></table></div><div id="history-pagination" class="pagination"></div></section>
  </section>
  <footer class="footer"><span>Read-only dashboard · command submission requires API authentication</span></footer>
</main>
<script>
let selected='',historyPage=0,historyRows=[];const HISTORY_PAGE_SIZE=11;
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function api(path){let r=await fetch(path);if(!r.ok)throw new Error(await r.text());return r}
function badge(status,error){let value=error||status||'Unknown',kind=error?'error':(/^(SYNCING|PRIMARY|succeeded)$/i.test(value)?'ok':/^(queued|running|interrupted)$/i.test(value)?'warn':/failed/i.test(value)?'error':'neutral');return '<span class="badge '+kind+'">'+esc(value)+'</span>'}
function statusIcon(status){let symbols={queued:'◷',running:'◌',succeeded:'✓',failed:'✕',interrupted:'!'},value=status||'unknown',kind=/^succeeded$/i.test(value)?'ok':/^(queued|running|interrupted)$/i.test(value)?'warn':/^failed$/i.test(value)?'error':'neutral';return '<span class="status-icon '+kind+'" title="'+esc(value)+'" aria-label="'+esc(value)+'">'+(symbols[value]||'?')+'</span>'}
function stat(label,value,note){return '<div class="stat"><div class="stat-label">'+esc(label)+'</div><div class="stat-value">'+esc(value)+'</div><div class="stat-note">'+esc(note)+'</div></div>'}
function orderedClusterNames(mon){return Object.keys(mon.clusters||{}).sort((left,right)=>Number(right===mon.primary)-Number(left===mon.primary))}
function clusterLabel(mon,key){let cluster=mon.clusters?.[key];return cluster?cluster.name+' ('+String(key).toUpperCase()+')':(key?String(key).toUpperCase():'Unavailable')}
function cluster(v,key){let role=v.role||'unknown',showCheckpoints=role==='follower',headers='<th>Index</th><th class="number">Docs</th><th>CCR status</th>'+(showCheckpoints?'<th class="number">Operations<br>Leader / follower</th>':''),rows=v.indices.map(i=>'<tr><td class="index">'+esc(i.index)+'</td><td class="number">'+esc(i.documents)+'</td><td>'+badge(i.replication?.status,i.error)+'</td>'+(showCheckpoints?'<td class="number">'+(i.replication?.leader_checkpoint!=null?esc(i.replication.leader_checkpoint)+' / '+esc(i.replication.follower_checkpoint):'—')+'</td>':'')+'</tr>').join('');return '<article class="card cluster-'+key+'"><div class="card-head"><div class="card-title">'+esc(v.name)+'</div><span class="role '+(role==='primary'?'primary':'')+'">'+esc(role)+'</span></div><div class="count"><span>Indices<br><strong>'+v.indices.length+'</strong></span><span>Documents<br><strong>'+esc(v.documents)+'</strong></span></div><div class="table-wrap"><table><thead><tr>'+headers+'</tr></thead><tbody>'+ (rows||'<tr><td class="empty" colspan="'+(showCheckpoints?'4':'3')+'">No protected indices observed.</td></tr>')+'</tbody></table></div></article>'}
function commandLabel(command){let parts=String(command||'—').split('-');if(parts.length<2)return esc(command);let split=Math.ceil(parts.length/2);return esc(parts.slice(0,split).join('-'))+'<br>'+esc(parts.slice(split).join('-'))}
function activeInfo(job){let running=job&&['running','queued'].includes(job.status);document.querySelector('#active-job').textContent=job?(running?'Running: ':'Latest: ')+job.id.slice(0,12):'No active job'}
function renderHistory(){let pages=Math.max(1,Math.ceil(historyRows.length/HISTORY_PAGE_SIZE));historyPage=Math.min(historyPage,pages-1);let start=historyPage*HISTORY_PAGE_SIZE,visible=historyRows.slice(start,start+HISTORY_PAGE_SIZE),body=document.querySelector('#jobs');body.innerHTML=visible.map(j=>'<tr class="job '+(j.id===selected?'selected':'')+'" data-job-id="'+esc(j.id)+'"><td class="status-cell">'+statusIcon(j.status)+'</td><td class="index command">'+commandLabel(j.request.command)+'</td><td>'+esc(j.submitted_at)+'</td><td>'+esc(j.finished_at)+'</td></tr>').join('')||'<tr><td class="empty" colspan="4">No jobs submitted yet.</td></tr>';body.querySelectorAll('[data-job-id]').forEach(row=>row.addEventListener('click',()=>{selected=row.dataset.jobId;output(selected);refresh()}));document.querySelector('#history-pagination').innerHTML=historyRows.length?'<button onclick="changeHistoryPage(-1)" '+(historyPage===0?'disabled':'')+'>Previous</button><span>Page '+(historyPage+1)+' / '+pages+'</span><button onclick="changeHistoryPage(1)" '+(historyPage===pages-1?'disabled':'')+'>Next</button>':''}
function changeHistoryPage(delta){historyPage=Math.max(0,historyPage+delta);renderHistory()}
async function output(id){if(!id)return;try{document.querySelector('#active-console').textContent=await (await api('/dashboard/v1/jobs/'+id+'/output')).text()}catch(e){document.querySelector('#active-console').textContent=e.message}}
function updateClock(){document.querySelector('#clock').textContent=new Date().toLocaleString()}
async function refresh(){
  try{
    let [mon,jobs]=await Promise.all([(await api('/dashboard/v1/monitor')).json(),(await api('/dashboard/v1/jobs')).json()]);
    let primary=clusterLabel(mon,mon.primary),topology=Object.keys(mon.clusters).length===2?'Topology identified':'Topology unavailable',inventory=mon.inventory_at?new Date(mon.inventory_at*1000).toLocaleString():'No inventory';
    let docs=Object.values(mon.clusters).reduce((n,c)=>n+(c.documents||0),0);
    document.querySelector('#notice').hidden=true;
    document.querySelector('#overview').innerHTML=stat('Observed primary',primary,'Current write target')+stat('FSDR inventory',mon.phase||'Unavailable',inventory)+stat('Topology',topology,'CCR direction observed')+stat('Protected documents',docs,'Combined cluster count');
    document.querySelector('#comparison').innerHTML=orderedClusterNames(mon).map(k=>cluster(mon.clusters[k],k)).join('');
    document.querySelector('#topology').textContent=topology;
    let active=jobs.jobs.find(j=>j.status==='running')||jobs.jobs.find(j=>j.status==='queued')||jobs.jobs[0];
    if(!selected&&active)selected=active.id;
    if(active&&['running','queued'].includes(active.status))selected=active.id;
    activeInfo(active);historyRows=jobs.jobs;
    document.querySelector('#job-count').textContent=historyRows.length+' recent job(s)';
    renderHistory();if(selected)output(selected);
  }catch(e){
    let notice=document.querySelector('#notice');notice.textContent='Dashboard refresh failed: '+e.message;notice.hidden=false;
  }
}
updateClock();setInterval(updateClock,1000);
refresh();setInterval(refresh,5000);
</script>
</body></html>'''
