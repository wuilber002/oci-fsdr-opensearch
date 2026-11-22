"""Self-contained modern web console served by the local FSDR API."""

DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenSearch FSDR</title>
<style>
:root{color-scheme:dark;--bg:#0b1120;--surface:#141e30;--line:#2b3e5e;--text:#e9eef8;--muted:#91a4c3;--blue:#60a5fa;--green:#4ade80;--orange:#fb923c;--red:#f87171;--pink:#f472d0;--radius:12px}
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
.summary{display:grid;grid-template-columns:repeat(3,minmax(max-content,1fr));gap:14px;margin-bottom:22px}
.recovery-section{margin-bottom:22px}
.recovery-flow{position:relative;padding:15px 16px 17px;display:grid;grid-template-columns:145px repeat(3,minmax(0,1fr)) 42px repeat(2,minmax(0,1fr));grid-template-rows:auto auto;gap:10px 12px;align-items:stretch}.recovery-route{display:contents}.recovery-route-label{align-self:center;width:100%;color:#a8b9d2;font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.path-name{display:block}.recovery-route-label.path-active{color:#86efac}.path-indicator{display:flex;width:max-content;margin:8px auto 0;padding:2px 5px;border:1px solid #2d8c58;border-radius:999px;color:#86efac;font-size:8px;letter-spacing:.05em}.path-unavailable{opacity:.44;filter:saturate(.35)}.recovery-route-label.path-unavailable{text-decoration:line-through;text-decoration-thickness:2px;text-decoration-color:#f87171}.recovery-sequence.path-unavailable .recovery-node{background-image:repeating-linear-gradient(-45deg,rgba(248,113,113,.12) 0 5px,transparent 5px 10px);border-color:#70404a}.planned-label{grid-column:1;grid-row:1}.failover-label{grid-column:1;grid-row:2}.recovery-sequence{position:relative;display:grid;min-width:0}.planned-sequence{grid-column:2 / span 3;grid-row:1;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}.failover-sequence{grid-column:2 / span 3;grid-row:2;grid-template-columns:minmax(0,1fr)}.recovery-node{position:relative;min-width:0;min-height:89px;padding:10px 11px;display:grid;grid-template-rows:13px minmax(0,1fr) 16px;background:#101a2a;border:1px solid #29405f;border-radius:9px}.recovery-node{z-index:1}.recovery-connectors{position:absolute;inset:0;z-index:0;width:100%;height:100%;pointer-events:none;overflow:visible}.recovery-connectors path{fill:none;stroke:#5d7395;stroke-width:1.25;vector-effect:non-scaling-stroke}.recovery-connectors marker#recovery-arrow path{fill:#5d7395;stroke:none}.recovery-connectors marker#recovery-arrow-open path{fill:#4ade80;stroke:none}.recovery-connectors path.recovery-connector-open{stroke:#4ade80;stroke-width:1.8}.recovery-connectors path.recovery-connector-blocked{stroke:#f87171;stroke-width:1.8;stroke-dasharray:4 3}.shared-route{position:relative;grid-column:6 / span 2;grid-row:1 / span 2;display:block}.shared-route-label{position:absolute;left:0;top:calc(50% - 66px);color:#a8b9d2;font-size:11px;font-weight:800;letter-spacing:.06em;text-transform:uppercase}.shared-sequence{position:absolute;left:0;right:0;top:50%;transform:translateY(-50%);display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;height:89px}.path-header{display:flex;align-items:center;gap:8px;min-width:0}.path-requirements{display:flex;align-items:center;gap:4px;overflow:hidden}.path-attempt{margin-left:auto;color:#8ba4ca;font-size:9px;line-height:1;letter-spacing:.02em;white-space:nowrap}.path-requirement{display:block;width:18px;height:5px;border-radius:2px;background:#3a4658;cursor:help}.path-requirement.met{background:#86efac;box-shadow:0 0 7px rgba(134,239,172,.5)}.path-requirement.blocked{background:#f87171}.path-main{align-self:center;display:flex;align-items:center;gap:8px;color:#fff;font-size:13px;font-weight:800;line-height:1.1}.path-step-led{flex:0 0 auto;width:14px;height:14px;border:2px solid #3a4658;border-radius:50%;background:#192639;cursor:help}.path-step-led.succeeded{border-color:#4ade80;background:#16834b;box-shadow:0 0 10px rgba(74,222,128,.75)}.path-step-led.running{border-color:var(--orange);border-right-color:transparent;animation:status-spinner .8s linear infinite}.path-step-led.failed,.path-step-led.blocked,.path-step-led.interrupted{border-color:#f87171;background:#6b2330}.path-status{align-self:end;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;cursor:help}.recovery-node.status-ready .path-status{color:var(--green);animation:ready-pulse 1s ease-in-out infinite}.recovery-node.status-waiting .path-status,.recovery-node.status-queued .path-status,.recovery-node.status-running .path-status{color:var(--orange)}.recovery-node.status-failed .path-status,.recovery-node.status-blocked .path-status,.recovery-node.status-interrupted .path-status{color:#f87171}
.summary-card,.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius)}
.summary-card{padding:17px}
.summary .summary-card{min-height:122px;box-sizing:border-box;display:grid;grid-template-rows:auto minmax(0,1fr) auto}
.stat-label{font-size:11px;letter-spacing:.08em;color:var(--muted);font-weight:750;text-transform:uppercase}
.stat-value{font-size:24px;font-weight:750;margin-top:9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.summary .summary-card .stat-value,.summary .state-card .state-value{align-self:center;margin:0}
.stat-note{margin-top:6px;color:var(--muted);font-size:12px}
.summary .summary-card .stat-note{margin:0;align-self:end}
.summary-card.topology-unidirectional{background:#10291f;border-color:#256342}
.summary-card.topology-unidirectional .stat-value{color:#fff;font-weight:750}
.summary-card.topology-bidirectional{background:#351821;border-color:#7f1d2d}
.summary-card.topology-bidirectional .stat-value{color:#fff;font-weight:750;cursor:help}
.primary-card,.topology-card,.state-card{width:200px;min-width:200px;white-space:nowrap}
.primary-card .stat-value,.state-card .state-value,.topology-card .stat-value{color:#fff;font-size:20px;font-weight:800}
.primary-card.primary-identified{background:#10291f;border-color:#256342}
.primary-card.primary-unavailable{background:#351821;border-color:#7f1d2d}
.primary-card .stat-note strong{color:#fff;font-weight:800}
.state-card .state-value{min-height:42px;margin-top:7px;display:flex;align-items:center;color:#fff;font-size:20px;font-weight:800;line-height:1.05}
.state-card .stat-note{max-width:140px;white-space:normal}
.state-card.state-ready,.state-card.state-protection-restored{background:#10291f;border-color:#256342}
.state-card.state-service-restored{background:#102a45;border-color:#244e71}
.state-card.state-transition,.state-card.state-reprotecting{background:#3b260f;border-color:#8a4b15}
.state-card.state-error{background:#351821;border-color:#7f1d2d}
.card{overflow:hidden}
.cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.card-head,.section-head{padding:17px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between}
.card-title,.section-head h2{font-size:16px;font-weight:750;margin:0}
.role,.badge{font-size:11px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;border-radius:999px;padding:5px 9px}
.role{display:inline-block;margin-left:7px;padding:2px 5px;border-radius:4px;vertical-align:middle;font-size:9px;font-weight:850;letter-spacing:.06em;line-height:1.2;background:#303846;color:#e5e7eb}
.role.primary{background:#4a3510;color:#facc15}
.cluster-key{position:relative;display:inline-block}
.cluster-key-crown{position:absolute;top:-8px;left:50%;transform:translateX(-50%);color:#facc15;font-size:14px;line-height:1}
.cluster-metrics{display:flex;align-items:baseline;justify-content:flex-end;gap:16px;margin-left:auto;color:#fff;font-size:12px;white-space:nowrap}
.cluster-metrics strong{margin-left:5px;color:#fff;font-size:18px;font-weight:750;font-variant-numeric:tabular-nums}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:collapse}
th{background:rgba(20,30,48,.75);color:#91a4c3;text-align:left;font-size:10px;font-weight:800;letter-spacing:.07em;text-transform:uppercase}
th,td{padding:11px 18px;border-bottom:1px solid rgba(43,62,94,.65);white-space:nowrap}
td{color:#c9d6e8}
tr:last-child td{border-bottom:0}
.number{text-align:right;color:#85efad;font-variant-numeric:tabular-nums}
.index{font-weight:750;color:#fff}
.index-label{vertical-align:middle}
.index.index-problem .index-label{color:#f87171;font-weight:400}
.index.index-warning .index-label{color:var(--orange);font-weight:400}
.index-info-cell{width:0;min-width:0;max-width:0;padding:0!important;position:relative;overflow:visible}
.index-entry{position:relative;display:inline-block}
.index-info{position:absolute;left:-29px;top:50%;z-index:2;display:block;width:24px;height:24px;transform:translateY(-50%);color:var(--orange);font-size:18px;font-weight:400;line-height:24px;text-align:center;cursor:help}
.index-info.problem{color:#f87171}
.fsdr-tag{display:inline-block;margin-left:7px;padding:2px 5px;border-radius:4px;vertical-align:middle;font-size:9px;font-weight:850;letter-spacing:.06em;line-height:1.2;cursor:help}
.fsdr-tag.protected{background:#153b2c;color:#86efac}
.fsdr-tag.missing{background:#7f1d2d;color:#fecdd3;text-decoration:line-through;text-decoration-thickness:1px}
#comparison th,#comparison td,#comparison .number{text-align:center}
#comparison th{color:#fff;font-weight:800}
#comparison thead tr{height:51px}
#comparison th{height:51px;padding-top:0;padding-bottom:0;vertical-align:middle}
#comparison tbody tr{height:44px}
#comparison tbody td{height:44px;box-sizing:border-box;padding-top:0;padding-bottom:0;vertical-align:middle}
#comparison th.docs-heading{color:#fff;font-weight:800}
#comparison td,#comparison .number{color:#fff;font-weight:400}
#comparison .card-title{color:#fff;font-size:18px;font-weight:750}
#comparison .badge{padding:0;background:transparent;color:#fff;font-size:inherit;font-weight:400;letter-spacing:normal;text-transform:none}
#comparison .badge.long-status{display:inline-block;font-size:10px;line-height:1.05;white-space:normal}
.badge{display:inline-flex;align-items:center;gap:5px}
.badge.ok{background:#133d2e;color:#7cf0ab}
.badge.warn{background:#512b12;color:#fdba74}
.badge.error{background:#4a1f2a;color:#fda4af}
.badge.neutral{background:#25344c;color:#b7c6dc}
.status-icon{position:relative;display:inline-flex;align-items:center;justify-content:center;vertical-align:middle;width:22px;height:22px;border-radius:50%;font-size:14px;font-weight:900;line-height:1}
.status-icon-glyph{display:flex;width:100%;height:100%;align-items:center;justify-content:center;line-height:1;transform:translateY(-.5px)}
.status-icon.ok{background:#133d2e;color:#7cf0ab}
.status-icon.warn{background:#512b12;color:#fdba74}
.status-icon.error{background:#4a1f2a;color:#fda4af}
.status-icon.neutral{background:#25344c;color:#b7c6dc}
.status-icon.running{position:relative;font-size:0}
.status-icon.rejected .status-icon-glyph{position:relative;font-size:0;transform:none}
.status-icon.rejected .status-icon-glyph::before{content:"";position:absolute;width:11px;height:11px;border:2px solid currentColor;border-radius:50%}
.status-icon.rejected .status-icon-glyph::after{content:"";position:absolute;width:15px;height:2px;border-radius:2px;background:currentColor;transform:rotate(-58deg)}
.status-icon.running::after{content:"";position:absolute;left:50%;top:50%;width:12px;height:12px;margin:-6px;box-sizing:border-box;border:2px solid rgba(255,255,255,.34);border-top-color:#fff;border-radius:50%;animation:status-spinner .75s linear infinite}
@keyframes status-spinner{to{transform:rotate(360deg)}}@keyframes ready-pulse{50%{opacity:.35;text-shadow:0 0 7px rgba(74,222,128,.9)}}
.status-cell{width:1%;padding-right:5px;text-align:center}
.controller-job{margin-bottom:22px}
.job-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(420px,.85fr);align-items:stretch;height:620px}
.job-panel{display:flex;flex-direction:column;min-width:0;min-height:0}
.job-panel+.job-panel{border-left:1px solid var(--line)}
.job-panel>.table-wrap,.job-panel>.console{flex:1;min-height:0}
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
#overview,#comparison-section,#active-job-section,#history-section{scroll-margin-top:88px}
.nav-target-flash{animation:nav-target-flash 1.4s ease-out}
@keyframes nav-target-flash{0%{box-shadow:0 0 0 2px #60a5fa,0 0 24px rgba(96,165,250,.45)}100%{box-shadow:none}}
#clusters{display:none}
@media(max-width:1100px){.summary{grid-template-columns:repeat(2,1fr)}.recovery-flow{grid-template-columns:1fr;grid-template-rows:auto;gap:10px}.planned-label,.failover-label,.planned-sequence,.failover-sequence,.shared-route{grid-column:1;grid-row:auto}.recovery-route-label{margin-top:5px}.planned-sequence{grid-template-columns:repeat(3,minmax(130px,1fr))}.shared-route{position:relative;min-height:121px}.shared-route-label{top:0}.shared-sequence{top:32px;transform:none}.job-grid{grid-template-columns:1fr;height:auto}.job-panel{min-height:520px}.job-panel+.job-panel{border-left:0;border-top:1px solid var(--line)}.top-context{display:none}}
@media(max-width:820px){.topbar{padding:9px 18px;flex-wrap:wrap}.top-nav{order:3;flex-basis:100%;overflow:auto}.content{padding:20px}.cards,.summary{grid-template-columns:1fr}.page-title{display:block}.refresh{margin-top:14px}h1{font-size:25px}th,td{padding:10px 12px}.job-grid{grid-template-columns:minmax(0,1fr)}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand"><img class="brand-mark" src="/assets/fsdr-logo.png" alt="FSDR for OpenSearch logo"> FSDR for OpenSearch</div>
  <nav class="top-nav" aria-label="Dashboard sections">
    <a class="nav-item active" href="#overview"><span class="nav-icon">▦</span>Dashboard</a>
    <a class="nav-item" href="#comparison-section"><span class="nav-icon">◌</span>Cluster replication</a>
    <a class="nav-item" href="#active-job-section"><span class="nav-icon">≋</span>Log</a>
    <a class="nav-item" href="#history-section"><span class="nav-icon">◫</span>History</a>
  </nav>
  <div class="top-context"><span class="dot"></span>Controller available</div>
  <span id="clock" class="top-clock"></span>
</header>
<main class="content">
  <div class="page-title"><div><h1>OpenSearch overview</h1><div class="eyebrow">Full Stack Disaster Recovery · Cross-Cluster Replication</div><div class="subtitle">Follow protected indices, current replication direction and FSDR jobs while the controller operates.</div></div></div>
  <div id="notice" class="status-banner">Connecting to the controller…</div>
  <section id="overview" class="summary"></section>
  <section id="recovery-paths" class="card recovery-section"><div class="section-head"><h2>Recovery paths</h2><span class="small">Switchover and failover gates</span></div><div id="recovery-flow" class="recovery-flow"></div></section>
  <section id="comparison-section" class="card" style="margin-bottom:22px"><div class="section-head"><h2>Replicated indices comparison</h2><span id="topology" class="small">Waiting for topology</span></div><div id="comparison" class="cards" style="padding:16px;margin:0"></div></section>
  <section class="card controller-job"><div class="section-head"><h2>Controller job</h2></div>
    <div class="job-grid">
      <section id="active-job-section" class="job-panel"><div class="section-head"><h2>Log</h2><span id="active-job" class="small">No active job</span></div><pre id="active-console" class="console">No controller job is running.</pre></section>
      <section id="history-section" class="job-panel"><div class="section-head"><h2>History</h2><span id="job-count" class="small"></span></div><div class="table-wrap"><table class="history-table"><thead><tr><th class="status-cell" aria-label="Status"></th><th>Command</th><th>Submitted</th><th>Finished</th></tr></thead><tbody id="jobs"></tbody></table></div><div id="history-pagination" class="pagination"></div></section>
    </div>
  </section>
  <footer class="footer"><span>Read-only dashboard · command submission requires API authentication</span></footer>
</main>
<script>
let selected='',historyPage=0,historyRows=[],latestJob=null,outputStream=null,dashboardStream=null,jobStream=null,streamingJob='',loadedOutput='';const HISTORY_PAGE_SIZE=11;
const esc=s=>String(s??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function badge(status,error){let value=error||status||'Unknown',kind=error?'error':(/^(SYNCING|PRIMARY|succeeded)$/i.test(value)?'ok':/^(queued|running|interrupted)$/i.test(value)?'warn':/failed/i.test(value)?'error':'neutral'),longStatus=!error&&String(value).toUpperCase()==='REPLICATION NOT IN PROGRESS',label=longStatus?'REPLICATION NOT<br>IN PROGRESS':esc(value);return '<span class="badge '+kind+(longStatus?' long-status':'')+'">'+label+'</span>'}
function rejectionReason(job){let rejected=job?.rejection||{},unmet=(rejected.requirements||[]).filter(item=>!item.met),details=unmet.map(item=>item.detail||item.explanation).filter(Boolean);return details.length?details.join(' · '):(job?.reason||rejected.code||'Workflow prerequisites are not satisfied.')}
const STATUS_ICON_TEMPLATE=Object.freeze({rootClass:'status-icon',glyphClass:'status-icon-glyph',symbols:Object.freeze({queued:'◷',running:'◌',succeeded:'✓',failed:'✕',interrupted:'!',rejected:'⊘'}),kind(value){return /^succeeded$/i.test(value)?'ok':/^(queued|running|interrupted)$/i.test(value)?'warn':/^(failed|rejected)$/i.test(value)?'error':'neutral'}});
function statusIcon(job,template=STATUS_ICON_TEMPLATE){let value=job?.status||'unknown',kind=template.kind(value),state=String(value).toLowerCase().replace(/[^a-z0-9_-]/g,'-'),title=value==='rejected'?'Rejected — '+rejectionReason(job):value;return '<span class="'+template.rootClass+' '+kind+' '+state+'" title="'+esc(title)+'" aria-label="'+esc(title)+'"><span class="'+template.glyphClass+'" aria-hidden="true">'+esc(template.symbols[value]||'?')+'</span></span>'}
function summaryCard({label,value,note,classes='',tooltip='',valueClass='stat-value',valueHtml=false,noteHtml=false}){let output=valueHtml?value:esc(value),detail=noteHtml?note:esc(note),title=tooltip?' title="'+esc(tooltip)+'" aria-label="'+esc(tooltip)+'"':'';return '<article class="summary-card '+classes+'"><div class="stat-label">'+esc(label)+'</div><div class="'+valueClass+'"'+title+'>'+output+'</div><div class="stat-note">'+detail+'</div></article>'}
function displayTime(value){if(!value)return 'No status update';let date=new Date(typeof value==='number'?value*1000:value);return Number.isNaN(date.getTime())?'No status update':date.toLocaleString()}
function stateLabel(phase){return String(phase).toLowerCase().split('_').map(word=>esc(word.charAt(0).toUpperCase()+word.slice(1))).join('<br>')}
function stateStat(mon){let phase=mon.phase||'Unavailable',failed=Boolean((mon.errors||[]).length)||['failed','interrupted'].includes(latestJob?.status),tone='state-'+phase.toLowerCase().replace(/_/g,'-'),timestamp=mon.state_updated_at,reason='';if(failed){tone='state-error';timestamp=latestJob?.finished_at||latestJob?.started_at||latestJob?.submitted_at||timestamp;reason=latestJob?.reason||mon.errors?.[0]||'The latest FSDR operation did not complete successfully.'}return summaryCard({label:'FSDR State',value:stateLabel(phase),note:displayTime(timestamp),classes:'state-card '+tone,tooltip:reason,valueClass:'state-value',valueHtml:true})}
function primaryStat(mon){let key=mon.primary,cluster=mon.clusters?.[key],identified=Boolean(cluster&&key),name=identified?cluster.name:'Unavailable',target=identified?'Current write target (<strong>'+esc(String(key).toUpperCase())+'</strong>)':'Current write target';return summaryCard({label:'Observed primary',value:name,note:target,classes:'primary-card '+(identified?'primary-identified':'primary-unavailable'),noteHtml:true})}
function requirementTooltip(requirement){return [requirement.detail,requirement.explanation].filter(Boolean).join('\n')}
function workflowStatusLabel(status){return ({ready:'Ready',waiting:'Waiting for prerequisites',blocked:'Blocked',queued:'Queued',running:'Running',succeeded:'Succeeded',failed:'Failed',interrupted:'Interrupted'})[status]||'Unavailable'}
function recoveryRequirement(requirement){let state=requirement.met?'met':(requirement.level==='blocked'?'blocked':'waiting'),tooltip=requirementTooltip(requirement);return '<span class="path-requirement '+state+'" title="'+esc(tooltip)+'" aria-label="'+esc(tooltip)+'"></span>'}
function workflowAttemptTime(value){if(!value)return '--:--:-- --/--/----';let date=new Date(typeof value==='number'?value*1000:value);if(Number.isNaN(date.getTime()))return '--:--:-- --/--/----';return date.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})+' '+date.toLocaleDateString()}
const FLOW_CARD_TEMPLATE=Object.freeze({rootClass:'recovery-node',statusClassPrefix:'status-',headerClass:'path-header',requirementsClass:'path-requirements',timeClass:'path-attempt',mainClass:'path-main',stepLedClass:'path-step-led',statusTextClass:'path-status',executionTitle:'Last execution attempt'});
function flowCard(template,{command,label,status,statusDetail,requirements=[],lastAttemptAt=null,eligible=false}){let led=status==='succeeded'?'succeeded':status,timestamp=workflowAttemptTime(lastAttemptAt);return '<article data-command="'+esc(command)+'" data-status="'+esc(status)+'" data-eligible="'+String(Boolean(eligible))+'" class="'+template.rootClass+' '+template.statusClassPrefix+esc(status)+'"><div class="'+template.headerClass+'"><div class="'+template.requirementsClass+'">'+requirements.map(recoveryRequirement).join('')+'</div><time class="'+template.timeClass+'" title="'+esc(template.executionTitle)+'">'+esc(timestamp)+'</time></div><div class="'+template.mainClass+'"><span class="'+template.stepLedClass+' '+esc(led)+'" title="'+esc(statusDetail)+'" aria-label="'+esc(statusDetail)+'"></span><span>'+esc(label)+'</span></div><div class="'+template.statusTextClass+'" title="'+esc(statusDetail)+'" aria-label="'+esc(statusDetail)+'">'+esc(workflowStatusLabel(status))+'</div></article>'}
function recoveryNode(step,options={}){return flowCard(FLOW_CARD_TEMPLATE,{command:step.command,label:options.label||step.label,status:step.status,statusDetail:step.status_detail,requirements:step.requirements,lastAttemptAt:step.last_attempt_at,eligible:step.eligible})}
function recoverySequence(steps){return (steps||[]).map(recoveryNode).join('')}
function recoveryPoint(rect,frame,edge){return {x:rect[edge]-frame.left,y:rect.top-frame.top+rect.height/2}}
function drawRecoveryConnectors(target){let planned=[...target.querySelectorAll('.planned-sequence .recovery-node')],failover=target.querySelector('.failover-sequence .recovery-node'),shared=[...target.querySelectorAll('.shared-sequence .recovery-node')];if(planned.length!==3||!failover||shared.length!==2)return;let frame=target.getBoundingClientRect(),svg=target.querySelector('.recovery-connectors');if(!svg){svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('recovery-connectors');target.prepend(svg)}let [inventory,validation,switchover]=planned.map(node=>node.getBoundingClientRect()),failure=failover.getBoundingClientRect(),[post,reprotect]=shared.map(node=>node.getBoundingClientRect()),a=recoveryPoint(inventory,frame,'right'),b=recoveryPoint(validation,frame,'left'),c=recoveryPoint(validation,frame,'right'),d=recoveryPoint(switchover,frame,'left'),switchEnd=recoveryPoint(switchover,frame,'right'),failEnd=recoveryPoint(failure,frame,'right'),postStart=recoveryPoint(post,frame,'left'),postEnd=recoveryPoint(post,frame,'right'),reprotectStart=recoveryPoint(reprotect,frame,'left'),busX=(switchEnd.x+postStart.x)/2,failoverUnavailable=failover.parentElement.classList.contains('path-unavailable'),isOpen=(from,to)=>from.dataset.status==='succeeded'&&(to.dataset.status==='succeeded'||to.dataset.eligible==='true'),arrow='<defs><marker id="recovery-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="#5d7395"/></marker><marker id="recovery-arrow-open" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7" fill="#4ade80"/></marker></defs>',link=(from,to,open)=>'<path'+(open?' class="recovery-connector-open" marker-end="url(#recovery-arrow-open)"':' marker-end="url(#recovery-arrow)"')+' d="M '+from.x+' '+from.y+' H '+to.x+'"/>',plannedOneOpen=isOpen(planned[0],planned[1]),plannedTwoOpen=isOpen(planned[1],planned[2]),sharedEntryOpen=isOpen(planned[2],shared[0]),sharedExitOpen=isOpen(shared[0],shared[1]),switchJoin='<path'+(sharedEntryOpen?' class="recovery-connector-open"':'')+' d="M '+switchEnd.x+' '+switchEnd.y+' H '+busX+' V '+postStart.y+'"/>',failJoin=failoverUnavailable?'<path class="recovery-connector-blocked" d="M '+failEnd.x+' '+failEnd.y+' H '+busX+' V '+postStart.y+'"/>':link(failEnd,{x:busX,y:failEnd.y},isOpen(failover,shared[0])),sharedJoin='<path'+(sharedEntryOpen?' class="recovery-connector-open" marker-end="url(#recovery-arrow-open)"':' marker-end="url(#recovery-arrow)"')+' d="M '+busX+' '+postStart.y+' H '+postStart.x+'"/>';svg.setAttribute('viewBox','0 0 '+target.clientWidth+' '+target.clientHeight);svg.innerHTML=arrow+link(a,b,plannedOneOpen)+link(c,d,plannedTwoOpen)+switchJoin+failJoin+sharedJoin+link(postEnd,reprotectStart,sharedExitOpen)}
function recoveryPathLabel(path,classes){let state=path.active?' path-active':(path.unavailable?' path-unavailable':''),indicator=path.active?'<span class="path-indicator">ACTIVE PATH</span>':'';return '<div class="recovery-route-label '+classes+state+'"><span class="path-name">'+esc(path.label)+'</span>'+indicator+'</div>'}
function renderRecoveryPaths(workflow){let target=document.querySelector('#recovery-flow');if(!target)return;let paths=Object.fromEntries((workflow?.paths||[]).map(path=>[path.id,path]));if(!paths.switchover||!paths.failover||!paths.shared){target.innerHTML='<div class="empty">Recovery paths are unavailable.</div>';return}let plannedState=paths.switchover.unavailable?' path-unavailable':'',failoverState=paths.failover.unavailable?' path-unavailable':'';target.innerHTML=recoveryPathLabel(paths.switchover,'planned-label')+'<div class="recovery-sequence planned-sequence'+plannedState+'">'+recoverySequence(paths.switchover.steps)+'</div>'+recoveryPathLabel(paths.failover,'failover-label')+'<div class="recovery-sequence failover-sequence'+failoverState+'">'+recoverySequence(paths.failover.steps)+'</div><div class="shared-route"><div class="shared-route-label">'+esc(paths.shared.label)+'</div><div class="shared-sequence">'+recoverySequence(paths.shared.steps)+'</div></div>';requestAnimationFrame(()=>drawRecoveryConnectors(target))}
function topologyState(mon){return mon.topology||((mon.primary&&Object.keys(mon.clusters||{}).length===2)?'unidirectional':'unavailable')}
function topologyStat(topology){if(topology==='bidirectional'){let explanation='CCR is active in both directions. FSDR supports only one primary-to-follower direction because switchover and reprotect would otherwise be ambiguous.';return summaryCard({label:'Topology',value:'Bidirectional',note:'CCR direction observed',classes:'topology-card topology-bidirectional',tooltip:explanation})}if(topology==='unidirectional')return summaryCard({label:'Topology',value:'Unidirectional',note:'CCR direction observed',classes:'topology-card topology-unidirectional'});return summaryCard({label:'Topology',value:'Unavailable',note:'CCR direction observed',classes:'topology-card'})}
function orderedClusterNames(mon){return Object.keys(mon.clusters||{}).sort((left,right)=>Number(right===mon.primary)-Number(left===mon.primary))}
function clusterLabel(mon,key){let cluster=mon.clusters?.[key];return cluster?cluster.name+' ('+String(key).toUpperCase()+')':(key?String(key).toUpperCase():'Unavailable')}
function fsdrTag(replicate){return replicate?'<span class="fsdr-tag protected" title="FSDR replication marker is configured." aria-label="FSDR replication marker is configured">FSDR</span>':'<span class="fsdr-tag missing" title="FSDR replication marker is missing. Replication through FSDR cannot be performed." aria-label="FSDR replication marker is missing. Replication through FSDR cannot be performed.">FSDR</span>'}
function indexCondition(item,phase){let status=item.replication?.status||'';if(!item.fsdr_replicate)return {level:'problem',message:'The FSDR replication marker is missing. Replication through FSDR cannot be performed.'};if(item.error)return {level:'problem',message:'The dashboard could not read this index\'s CCR state ('+item.error+').'};if(/(?:FAIL|ERROR)/i.test(status))return {level:'problem',message:'CCR reported an error: '+status+'.'};if(/(?:NOT IN PROGRESS|PAUSED|STOPPED)/i.test(status))return {level:'warning',message:phase==='SERVICE_RESTORED'?'CCR is intentionally stopped after switchover. Run reprotect to restore reverse protection.':'CCR is not currently active for this FSDR-marked index.'};return null}
function indexInfo(condition){return condition?'<span class="index-info '+condition.level+'" title="'+esc(condition.message)+'" aria-label="'+esc(condition.message)+'">🛈</span>':''}
function cluster(v,key,phase){let role=v.role||'unknown',showCheckpoints=role==='follower',headers='<th class="index-info-cell" aria-label="Index condition"></th><th>Index</th><th>CCR status</th><th class="number docs-heading">Docs</th>'+(showCheckpoints?'<th class="number">Operations<br>Leader / follower</th>':''),rows=v.indices.map(i=>{let condition=indexCondition(i,phase);return '<tr><td class="index-info-cell"></td><td class="index '+(condition?'index-'+condition.level:'')+'"><span class="index-entry">'+indexInfo(condition)+'<span class="index-label">'+esc(i.index)+'</span>'+fsdrTag(i.fsdr_replicate)+'</span></td><td>'+badge(i.replication?.status,i.error)+'</td><td class="number">'+esc(i.documents)+'</td>'+(showCheckpoints?'<td class="number">'+(i.replication?.leader_checkpoint!=null?esc(i.replication.leader_checkpoint)+' / '+esc(i.replication.follower_checkpoint):'—')+'</td>':'')+'</tr>'}).join('');let clusterKey='<span class="cluster-key">'+esc(String(key).toUpperCase())+(role==='primary'?'<span class="cluster-key-crown" aria-label="Primary cluster">🜲</span>':'')+'</span>: ';let metrics='<div class="cluster-metrics"><span>Indices <strong>'+v.indices.length+'</strong></span><span>Documents <strong>'+esc(v.documents)+'</strong></span></div>';return '<article class="card cluster-'+key+'"><div class="card-head"><div class="card-title">'+clusterKey+esc(v.name)+'<span class="role '+(role==='primary'?'primary':'')+'">'+esc(role)+'</span></div>'+metrics+'</div><div class="table-wrap"><table><thead><tr>'+headers+'</tr></thead><tbody>'+ (rows||'<tr><td class="empty" colspan="'+(showCheckpoints?'5':'4')+'">No protected indices observed.</td></tr>')+'</tbody></table></div></article>'}
function commandLabel(command){let parts=String(command||'—').split('-');if(parts.length<2)return esc(command);let split=Math.ceil(parts.length/2);return esc(parts.slice(0,split).join('-'))+'<br>'+esc(parts.slice(split).join('-'))}
function activeInfo(job){let running=job&&['running','queued'].includes(job.status);document.querySelector('#active-job').textContent=job?(running?'Running: ':'Latest: ')+job.id.slice(0,12):'No active job'}
function renderHistory(){let pages=Math.max(1,Math.ceil(historyRows.length/HISTORY_PAGE_SIZE));historyPage=Math.min(historyPage,pages-1);let start=historyPage*HISTORY_PAGE_SIZE,visible=historyRows.slice(start,start+HISTORY_PAGE_SIZE),body=document.querySelector('#jobs');body.innerHTML=visible.map(j=>'<tr class="job '+(j.id===selected?'selected':'')+'" data-job-id="'+esc(j.id)+'"><td class="status-cell">'+statusIcon(j)+'</td><td class="index command">'+commandLabel(j.request.command)+'</td><td>'+esc(j.submitted_at)+'</td><td>'+esc(j.finished_at)+'</td></tr>').join('')||'<tr><td class="empty" colspan="4">No jobs submitted yet.</td></tr>';body.querySelectorAll('[data-job-id]').forEach(row=>row.addEventListener('click',()=>{selected=row.dataset.jobId;renderHistory();output(selected)}));document.querySelector('#history-pagination').innerHTML=historyRows.length?'<button onclick="changeHistoryPage(-1)" '+(historyPage===0?'disabled':'')+'>Previous</button><span>Page '+(historyPage+1)+' / '+pages+'</span><button onclick="changeHistoryPage(1)" '+(historyPage===pages-1?'disabled':'')+'>Next</button>':''}
function changeHistoryPage(delta){historyPage=Math.max(0,historyPage+delta);renderHistory()}
function output(id){if(!id||streamingJob===id||loadedOutput===id)return;if(outputStream)outputStream.close();streamingJob=id;loadedOutput='';let console=document.querySelector('#active-console');console.textContent='';let stream=new EventSource('/dashboard/v1/jobs/'+encodeURIComponent(id)+'/events');outputStream=stream;stream.addEventListener('output',event=>{if(selected!==id)return;console.textContent+=JSON.parse(event.data);console.scrollTop=console.scrollHeight});stream.addEventListener('end',()=>{if(outputStream===stream){stream.close();outputStream=null;streamingJob='';loadedOutput=id}});stream.addEventListener('error',()=>{if(stream.readyState===EventSource.CLOSED&&outputStream===stream){outputStream=null;streamingJob='';console.textContent||='Unable to stream this job log.'}})}
function applyJobs(jobs){latestJob=jobs.jobs[0]||null;let active=jobs.jobs.find(j=>j.status==='running')||jobs.jobs.find(j=>j.status==='queued')||jobs.jobs[0];if(!selected&&active)selected=active.id;if(active&&['running','queued'].includes(active.status))selected=active.id;activeInfo(active);historyRows=jobs.jobs;document.querySelector('#job-count').textContent=historyRows.length+' recent job(s)';renderHistory();if(selected)output(selected)}
function applyDashboard(snapshot){if(!snapshot||!snapshot.monitor||!snapshot.jobs)return;latestJob=null;applyJobs(snapshot.jobs);renderMonitor(snapshot.monitor,true,true);document.querySelector('#notice').hidden=true}
function connectDashboardStream(){if(dashboardStream)return;dashboardStream=new EventSource('/dashboard/v1/events');dashboardStream.addEventListener('snapshot',event=>{try{applyDashboard(JSON.parse(event.data))}catch(error){let notice=document.querySelector('#notice');notice.textContent='Dashboard stream payload is invalid.';notice.hidden=false}});dashboardStream.addEventListener('error',()=>{let notice=document.querySelector('#notice');notice.textContent='Dashboard connection lost; reconnecting…';notice.hidden=false})}
function connectJobStream(){if(jobStream)return;jobStream=new EventSource('/dashboard/v1/jobs/events');jobStream.addEventListener('jobs',event=>{try{let update=JSON.parse(event.data);if(!Array.isArray(update.jobs))throw new Error('missing jobs');applyJobs({jobs:update.jobs});if(update.workflow){renderRecoveryPaths(update.workflow)}}catch(error){console.warn('Dashboard job stream payload is invalid.',error)}});jobStream.addEventListener('error',()=>console.warn('Dashboard job stream disconnected; browser will reconnect.'))}
function updateClock(){document.querySelector('#clock').textContent=new Date().toLocaleString()}
window.addEventListener('resize',()=>{let target=document.querySelector('#recovery-flow');if(target)drawRecoveryConnectors(target)})
document.querySelectorAll('.top-nav a[href^="#"]').forEach(link=>link.addEventListener('click',event=>{let target=document.querySelector(link.getAttribute('href'));if(!target)return;event.preventDefault();target.scrollIntoView({behavior:'smooth',block:'start'});history.replaceState(null,'',link.getAttribute('href'));target.classList.remove('nav-target-flash');void target.offsetWidth;target.classList.add('nav-target-flash')}));
function renderMonitor(mon,includeOverview,includeComparison){
    let topology=topologyState(mon);
    if(includeOverview){document.querySelector('#overview').innerHTML=primaryStat(mon)+topologyStat(topology)+stateStat(mon);renderRecoveryPaths(mon.workflow)}
    if(includeComparison){document.querySelector('#comparison').innerHTML=orderedClusterNames(mon).map(k=>cluster(mon.clusters[k],k,mon.phase)).join('');document.querySelector('#topology').textContent=topology==='unavailable'?'Topology unavailable':topology.charAt(0).toUpperCase()+topology.slice(1)+' topology'}
}
updateClock();setInterval(updateClock,1000);
connectDashboardStream();
connectJobStream();
</script>
</body></html>'''
