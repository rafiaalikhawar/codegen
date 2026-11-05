const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let project = null;

async function api(path, method = 'GET', body) {
  const res = await fetch(path, {method, headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { flash(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail), true); throw new Error('api'); }
  return data;
}
function flash(msg, bad) { const f = $('flash'); f.hidden = false; f.textContent = msg; f.className = 'flash ' + (bad ? 'err' : 'ok'); }
function pct(v) { return v == null ? '–' : (v * 100).toFixed(0) + '%'; }

function render(p) {
  project = p;
  $('title').textContent = p.name + ' (' + p.origin + ')';
  $('nl-section').hidden = p.origin !== 'nl';
  if (document.activeElement !== $('xml')) $('xml').value = p.xml;
  if (p.origin === 'nl' && document.activeElement !== $('req')) $('req').value = p.requirements || '';
  const v = p.validation;
  $('validation').innerHTML = (v.errors.length ? '<b class="err">Validation errors</b><ul>' + v.errors.map(e => '<li class="err">' + esc(e) + '</li>').join('') + '</ul>'
      : '<b class="ok">Model is valid.</b>') +
    (v.warnings.length ? '<b>Warnings</b><ul>' + v.warnings.map(e => '<li>' + esc(e) + '</li>').join('') + '</ul>' : '');
  if (p.extraction) $('validation').innerHTML += '<p class="muted">Extraction: ' + p.extraction.usage.total_tokens + ' tokens, ' +
    p.extraction.usage.latency_s + ' s, ' + p.extraction.repair_rounds + ' automatic repair round(s), model ' + esc(p.extraction.model) + '</p>';
  const s = p.summary;
  $('preview').innerHTML = s ? '<h3>' + esc(s.name) + '</h3><table><tr><th>Entity</th><th>Attributes</th></tr>' +
    s.entities.map(e => '<tr><td>' + esc(e.name) + '</td><td>' + e.attributes.map(a => esc(a.name) + ':' + esc(a.type) + (a.required ? '*' : '')).join(', ') + '</td></tr>').join('') + '</table>' +
    '<p><b>Relationships:</b> ' + (s.relationships.map(r => esc(r.from) + ' —' + esc(r.type) + '→ ' + esc(r.to) + ' (' + esc(r.name) + ')').join('; ') || 'none') + '</p>' +
    '<p><b>Rules:</b></p><ul>' + s.rules.map(r => '<li>' + esc(r.entity) + ': ' + esc(r.description || r.pattern) + ' <span class="muted">[' + esc(r.pattern || 'text only') + ']</span></li>').join('') + '</ul>' : '';
  $('approval-state').innerHTML = p.status === 'approved' ? '<b class="ok">Approved</b>' : '<b>Not approved</b>' +
    (p.requires_approval ? ' — required before generation for natural-language projects.' : ' — optional for XML projects.');
  $('approve').disabled = v.errors.length > 0 || p.status === 'approved';
  $('generate').disabled = !p.can_generate;
  $('manual').textContent = 'cd workspace/projects/' + p.id + '/app_output && uvicorn app.main:app --port 8000';
  const g = p.generation;
  if (g) {
    $('files').innerHTML = '<ul class="files">' + g.files.map(f => '<li><a data-f="' + esc(f) + '">' + esc(f) + '</a></li>').join('') + '</ul>';
    document.querySelectorAll('#files a').forEach(a => a.onclick = async () => {
      const r = await api('/api/projects/' + PID + '/file?path=' + encodeURIComponent(a.dataset.f));
      $('file-view').hidden = false; $('file-view').textContent = r.content; });
    const rep = g.report, m = g.metrics;
    $('gen-out').innerHTML = '<p class="ok">Generated ' + g.files.length + ' files.</p>' +
      '<p>Rules implemented: <b>' + rep.implemented_rules.length + '</b>; not implemented: <b class="' + (rep.unimplemented_rules.length ? 'err' : '') + '">' + rep.unimplemented_rules.length + '</b></p>' +
      (rep.unimplemented_rules.length ? '<ul>' + rep.unimplemented_rules.map(r => '<li class="err">' + esc(r.entity) + ': ' + esc(r.description) + '</li>').join('') + '</ul>' : '') +
      Object.entries(g.rule_interpretation || {}).map(([k, r]) => '<p class="muted">SLM on ' + esc(k) + ': ' + esc(r.status) + ' ' + esc(r.pattern || '') + ' ' + esc(r.reason || '') + '</p>').join('');
    $('metrics').innerHTML = '<table><tr><th>Generation time</th><td>' + m.generation_seconds + ' s (templates ' + m.template_seconds + ' s)</td></tr>' +
      '<tr><th>SLM tokens (in/out/total)</th><td>' + m.slm.input_tokens + ' / ' + m.slm.output_tokens + ' / ' + m.slm.total_tokens + '</td></tr>' +
      '<tr><th>SLM latency</th><td>' + m.slm.latency_s + ' s over ' + m.slm.calls + ' call(s)</td></tr>' +
      '<tr><th>Human edits</th><td>' + p.human_edits + '</td></tr></table>';
  }
  const t = p.tests;
  if (t) {
    const sm = t.summary;
    $('run-out').innerHTML = '<p>Startup: <b class="' + (t.startup_success ? 'ok' : 'err') + '">' + (t.startup_success ? 'success' : 'failed') + '</b></p>' + (sm ?
      '<table><tr><th>Checks passed</th><td>' + sm.checks_passed + ' / ' + sm.checks_total + ' (' + pct(sm.functional_pass_rate) + ')</td></tr>' +
      Object.entries(sm.by_category).map(([k, v]) => '<tr><th>' + k + '</th><td>' + v.passed + ' / ' + v.total + '</td></tr>').join('') + '</table>' : '<pre>' + esc(t.error) + '</pre>') +
      (t.failures.length ? '<b class="err">Failures</b><ul>' + t.failures.map(f => '<li>' + esc(f.name) + ' — ' + esc(f.detail) + '</li>').join('') + '</ul>' : '');
  }
}

async function load() { render(await api('/api/projects/' + PID)); }
async function loadSlm() {
  const s = await api('/api/slm'); $('provider').value = s.config.provider; $('model').value = s.config.model;
  const env = s.environment, models = [...env.ollama.models, ...env.lmstudio.models];
  $('models').innerHTML = models.map(m => '<option value="' + esc(m) + '">').join('');
  $('slm-env').innerHTML = 'Ollama: ' + (env.ollama.running ? 'running (' + env.ollama.models.length + ' models)' : env.ollama.installed ? 'installed but not running (start with <code>ollama serve</code>)' : 'not installed') +
    ' · LM Studio: ' + (env.lmstudio.running ? 'running' : 'not running') + (env.recommended ? ' · suggested: ' + esc(env.recommended.model) : '') +
    (env.instructions.length ? '<br>' + env.instructions.map(esc).join('<br>') : '');
}
$('save').onclick = async () => { render(await api('/api/projects/' + PID + '/xml', 'PUT', {xml: $('xml').value})); flash('Saved. Approval (if any) was withdrawn.'); };
$('upload').onchange = e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => { $('xml').value = r.result; }; r.readAsText(f); };
$('extract').onclick = async () => { flash('Asking the model… this can take a minute on a laptop.'); $('extract').disabled = true;
  try { render(await api('/api/projects/' + PID + '/extract', 'POST', {requirements: $('req').value})); flash('Proposal ready — review, edit if needed, then approve.'); } finally { $('extract').disabled = false; } };
$('approve').onclick = async () => { render(await api('/api/projects/' + PID + '/approve', 'POST')); flash('Model approved.'); };
$('save-slm').onclick = async () => { await api('/api/slm', 'PUT', {provider: $('provider').value, model: $('model').value}); flash('SLM settings saved.'); loadSlm(); };
$('generate').onclick = async () => { flash('Generating…'); render(await api('/api/projects/' + PID + '/generate', 'POST', {use_slm: $('use-slm').checked})); flash('Website generated.'); };
$('launch').onclick = async () => { const r = await api('/api/projects/' + PID + '/launch', 'POST');
  $('run-out').innerHTML = r.running ? '<p>Running at <a target="_blank" rel="noopener" href="' + esc(r.url) + '">' + esc(r.url) + '</a> (API docs: <a target="_blank" href="' + esc(r.url) + '/docs">/docs</a>)</p>' : '<p class="err">The app failed to start.</p>'; };
$('stop').onclick = async () => { await api('/api/projects/' + PID + '/stop', 'POST'); flash('Stopped.'); };
$('test').onclick = async () => { flash('Starting the app and running tests…'); render(await api('/api/projects/' + PID + '/test', 'POST')); flash('Tests finished.'); };
load(); loadSlm();
