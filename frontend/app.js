const $ = id => document.getElementById(id);
let report = null;
let activeJob = null;
let pollTimer = null;
const historyPanel=document.createElement('section'); historyPanel.className='panel';
const historyTitle=document.createElement('h2'); historyTitle.textContent='Recent investigations';
const historyList=document.createElement('div');
const cancelButton=document.createElement('button'); cancelButton.type='button'; cancelButton.textContent='Cancel investigation'; cancelButton.hidden=true;
const cancellationNote=document.createElement('p'); cancellationNote.className='hint'; cancellationNote.textContent='Cancellation stops queued work. Active work finishes its current operation and cleanup; its result is then discarded.';
historyPanel.append(historyTitle,historyList,cancelButton,cancellationNote); document.querySelector('footer').before(historyPanel);
async function history() {
  try {
    const response=await fetch('/api/jobs'); if(!response.ok)return;
    const rows=await response.json(); historyList.replaceChildren();
    if(!rows.length) historyList.textContent='No investigations saved yet.';
    for(const row of rows) {
      const button=document.createElement('button'); button.type='button'; button.style.margin='4px';
      button.textContent=new Date(row.created*1000).toLocaleString()+' · '+row.mode.replaceAll('_',' ')+' · '+row.state;
      button.addEventListener('click',()=>watch(row.id)); historyList.append(button);
    }
  } catch { /* Keep the current report visible if history is unavailable. */ }
}
async function watch(identity) {
  clearTimeout(pollTimer); activeJob=identity; localStorage.setItem('codepilot-last-job',identity);
  try {
    const response=await fetch('/api/jobs/'+identity); if(!response.ok)throw new Error('Saved investigation is unavailable.');
    const job=await response.json(); const pending=['queued','running','cancelling'].includes(job.state);
    $('submit').disabled=pending; cancelButton.hidden=!pending || job.cancel;
    $('status').textContent=job.state; $('progress').textContent=job.state==='cancelling'?'Cancellation requested; waiting for cleanup.':'Investigation '+job.state+'.';
    if(job.result)render(job.result);
    else {report=null;$('report').hidden=true;$('empty').hidden=false;}
    if(job.error)$('progress').textContent=job.error;
    if(pending)pollTimer=setTimeout(()=>watch(identity),1000);
    else activeJob=null;
    await history();
  } catch(error) {$('progress').textContent=error.message;$('submit').disabled=false;cancelButton.hidden=true;}
}
cancelButton.addEventListener('click',async()=>{
  if(!activeJob)return;
  try {const response=await fetch('/api/jobs/'+activeJob+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});if(!response.ok)throw new Error('Cancellation request failed.');await watch(activeJob);}
  catch(error){$('progress').textContent=error.message;}
});
$('mode').addEventListener('change', () => {
  const live = $('mode').value === 'live';
  const fresh = $('mode').value === 'requests_baseline';
  const exercise = $('mode').value === 'itsdangerous_exercise';
  const aiEvidence = $('mode').value === 'gemini_evidence';
  $('live-fields').hidden = !live; $('example-note').hidden = live;
  $('repo').required = live; $('issue').required = live; $('consent').required = live;
  $('example-note').textContent = aiEvidence ? 'Opens recorded evidence of one real Gemini request and its tested learning repair. No new API call or test run is made.' : exercise ? 'Runs 8 upstream encoding cases on unchanged source twice, a deliberately broken temporary copy, and its validated repair. This is a learning exercise, not an upstream bug fix.' : fresh ? 'Runs the pinned Requests source and 19 fixed checks in the approved Docker worker. No patch, network request or API key is involved.' : 'Loads recorded evidence from an isolated Requests 2.32.5 run. It does not start a new test run.';
  $('submit').textContent = live ? 'Investigate repository' : aiEvidence ? 'Open AI repair evidence' : exercise ? 'Run learning comparison' : fresh ? 'Run isolated baseline' : 'Open verified report';
});
function download(text, filename, type) {
  const url = URL.createObjectURL(new Blob([text], {type}));
  const a = document.createElement('a'); a.href = url; a.download = filename;
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function render(value) {
  report = value; $('empty').hidden = true; $('report').hidden = false;
  $('status').textContent = value.status.replaceAll('_',' ');
  $('summary').textContent = value.summary; $('source').textContent = value.source;
  $('facts').replaceChildren();
  for (const [label, text] of [['Repository',value.repository],['Revision',value.revision],['Files reviewed',value.files.join(', ')]]) {
    const dt = document.createElement('dt'), dd = document.createElement('dd');
    dt.textContent = label; dd.textContent = text; $('facts').append(dt,dd);
  }
  const v = value.validation;
  $('validation').textContent = [
    'Scope: ' + v.scope,
    ...(v.checks_passed !== undefined ? ['Checks passed: ' + v.checks_passed] : []),
    'Syntax validation: ' + (v.syntax_valid === true ? 'passed' : v.syntax_valid === false ? 'failed' : 'not applicable'),
    'Baseline behavior checked: ' + (v.behavior_tested ? 'yes' : 'no'),
    'Proposed patch behavior checked: ' + (v.patch_tested ? 'yes' : 'no'),
    'Full upstream test suite: not run'
  ].join('\n');
  $('notes').replaceChildren();
  for (const text of [...value.risks,...value.next_steps]) { const li=document.createElement('li'); li.textContent=text; $('notes').append(li); }
  $('diff').textContent = value.diff || 'No validated diff is available.';
  $('download-diff').hidden = !value.diff; $('evidence').textContent=JSON.stringify(value.evidence,null,2);
}
$('investigation').addEventListener('submit', async event => {
  event.preventDefault(); $('submit').disabled=true; $('progress').textContent='Investigation in progress…';
  report=null; $('report').hidden=true; $('empty').hidden=false; $('status').textContent='Working';
  try {
    const response=await fetch('/api/jobs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode:$('mode').value,repo_url:$('repo').value,issue:$('issue').value,send_source_to_ai:$('consent').checked})});
    const value=await response.json();
    if(!response.ok) throw new Error(typeof value.detail==='string'?value.detail:'Check the repository URL and issue, then try again.');
    await watch(value.id);
  } catch(error) { $('status').textContent='Could not finish'; $('progress').textContent=error.message; }
  finally { if(!activeJob)$('submit').disabled=false; }
});
$('download-json').addEventListener('click',()=>{if(report)download(JSON.stringify(report,null,2),'codepilot-report.json','application/json');});
$('download-diff').addEventListener('click',()=>{if(report?.diff)download(report.diff,'codepilot-proposal.patch','text/plain');});
const previous=localStorage.getItem('codepilot-last-job');
if(previous && /^[a-f0-9]{32}$/.test(previous))watch(previous); else history();
