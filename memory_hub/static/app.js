const $ = (s) => document.querySelector(s);
const escapeHtml = (s='') => String(s).replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

let agentPrompt = '';

const CN_NUM = ['〇','一','二','三','四','五','六','七','八','九'];
function toCnYear(y){ return String(y).split('').map(d=>CN_NUM[+d]).join(''); }
function toCnMonth(m){ return ['','一','二','三','四','五','六','七','八','九','十','十一','十二'][m]; }

// 报头日期行（正式中文日期，纯展示）
const dateline = $('#dateline');
if (dateline) {
  const t = new Date();
  const week = ['日','一','二','三','四','五','六'][t.getDay()];
  dateline.textContent = `${toCnYear(t.getFullYear())}年${toCnMonth(t.getMonth()+1)}月${t.getDate()}日 · 星期${week} · 本机检索`;
}

// 深浅模式手动切换
const themeToggle = $('#theme-toggle');
function syncThemeLabel() {
  const isDark = document.documentElement.dataset.theme === 'dark';
  themeToggle.textContent = isDark ? '☉' : '☾';
  themeToggle.title = isDark ? '切换到日间版' : '切换到夜间版';
}
themeToggle.onclick = () => {
  const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('mh-theme', next);
  syncThemeLabel();
};
// 系统主题变化时，若用户未手动选过，则跟随系统
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', e => {
  if (!localStorage.getItem('mh-theme')) {
    document.documentElement.dataset.theme = e.matches ? 'dark' : 'light';
    syncThemeLabel();
  }
});
syncThemeLabel();

async function copyAgentPrompt() {
  const button = $('#copy-agent-prompt'), status = $('#copy-status');
  const label = button.dataset.label || (button.dataset.label = button.textContent);
  try {
    agentPrompt = (await apiFetch('/api/agent-prompt')).prompt;
    $('#agent-prompt-text').value = agentPrompt;
  } catch (error) { status.textContent = error.message; return; }
  try {
    await navigator.clipboard.writeText(agentPrompt);
  } catch {
    const text = $('#agent-prompt-text');
    text.focus(); text.select();
    document.execCommand('copy');
  }
  button.textContent = '已抄录';
  status.textContent = '提示词已抄录，可直接粘贴给任意 AI。';
  setTimeout(() => { button.textContent = label; }, 1600);
  setTimeout(() => { status.textContent = ''; }, 4000);
}

async function loadStatus() {
  const data = await apiFetch('/api/status');
  const box = $('#status');
  box.innerHTML = '';
  for (const s of data.sources) {
    const e = document.createElement('div');
    e.className = `source ${s.state}`;
    const no = document.createElement('span'); no.className = 'src-no';
    const name = document.createElement('span'); name.className = 'src-name';
    const label = {partial:'部分收录', not_configured:'未配置', unavailable:'暂不可用', error:'读取失败'}[s.state];
    name.textContent = `${s.source}${label ? ' · ' + label : (s.detail === 'metadata only' ? ' · metadata only' : '')}`;
    e.title = s.detail || '';
    const leader = document.createElement('span'); leader.className = 'src-leader';
    const count = document.createElement('span'); count.className = 'src-count';
    count.textContent = s.document_count != null ? `${s.document_count.toLocaleString()} 条` : s.state;
    e.append(no, name, leader, count);
    box.append(e);
  }
}

function render(rows) {
  const box = $('#results');
  box.innerHTML = '';
  $('#count').textContent = `共 ${rows.length} 条`;
  if (!rows.length) {
    box.innerHTML = '<div class="empty">未检得匹配项。可尝试更短的关键词，或点击“立即同步”后再查。</div>';
    return;
  }
  for (const row of rows) {
    const e = $('#result').content.firstElementChild.cloneNode(true);
    e.querySelector('.badge').textContent = row.source;
    e.querySelector('time').textContent = row.source_updated_at ? new Date(row.source_updated_at * 1000).toLocaleString() : '';
    e.querySelector('.folio').textContent = row.locator ? `存档 ${row.locator}` : '';
    e.querySelector('h3').textContent = row.title;
    e.querySelector('.project').textContent = row.project_title || row.workspace_path || '';
    e.querySelector('.excerpt').innerHTML = escapeHtml(row.excerpt || (row.body || '').slice(0, 360)).replaceAll('&lt;mark&gt;', '<mark>').replaceAll('&lt;/mark&gt;', '</mark>');
    e.querySelector('.open').onclick = async () => {
      try { await apiFetch(`/api/documents/${row.id}/open`, {method:'POST'}); }
      catch (error) { $('#count').textContent = error.message; }
    };
    e.querySelector('.detail').onclick = async () => {
      try {
        const data = await apiFetch(`/api/documents/${row.id}`);
        $('#answer-section').hidden = false;
        $('#answer').textContent = `${data.title}\n\n${data.body}`;
      } catch (error) { $('#count').textContent = error.message; }
    };
    box.append(e);
  }
}

let searchVersion = 0;
async function run() {
  const version = ++searchVersion;
  const q = $('#query').value.trim();
  try {
    const rows = await apiFetch(`/api/search?q=${encodeURIComponent(q)}`);
    if (version === searchVersion) render(rows.results);
  } catch (error) { if (version === searchVersion) $('#count').textContent = error.message; }
}

async function ask() {
  const q = $('#query').value.trim();
  if (!q) return;
  const button = $('#ask');
  button.disabled = true;
  button.textContent = '查考中…';
  const section = $('#answer-section');
  const box = $('#answer');
  section.hidden = false;
  box.classList.remove('empty');
  try {
  const data = await apiFetch('/api/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question: q })
  });
  box.textContent = data.answer;
  } catch (error) { box.textContent = error.message; }
  finally { button.disabled = false; button.textContent = '提问'; }
}

$('#search').onclick = run;
$('#query').addEventListener('keydown', e => { if (e.key === 'Enter') run(); });
$('#sync').onclick = async () => {
  const b = $('#sync');
  b.disabled = true;
  b.textContent = '编目中…';
  try {
    const data = await apiFetch('/api/sync', { method: 'POST' });
    $('#count').textContent = data.state === 'already_running' ? '编目已在进行' : '已开始编目';
    await loadStatus();
    await run();
  } catch (error) { $('#count').textContent = error.message; }
  finally { b.disabled = false; b.textContent = '立即同步'; }
};

const q = new URLSearchParams(location.search).get('q');
if (q) { $('#query').value = q; run(); } else { run(); }
loadStatus().catch(error => { $('#status').textContent = error.message; });
$('#ask').onclick = ask;
$('#agent-prompt-text').value = agentPrompt;
$('#copy-agent-prompt').onclick = copyAgentPrompt;
apiFetch('/api/agent-prompt').then(data => {
  agentPrompt = data.prompt; $('#agent-prompt-text').value = agentPrompt;
}).catch(error => { $('#copy-status').textContent = error.message; });
