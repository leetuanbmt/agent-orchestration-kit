'use strict';

const I18N = {
  en: {
    brand: 'agent-bridge', vi: 'VI', en: 'EN', all: 'All', projects: 'Projects', language: 'Language',
    live: 'Live · every 3 s', offline: 'Disconnected · retrying in 5 s', loading: 'Loading…',
    attention: 'Needs attention', progress: 'Progress', running: 'Running now', activity: 'Last 24 hours',
    requests: 'Requests', request: 'Request', project: 'Project', stage: 'Stage', updated: 'Updated', id: 'ID',
    queue: 'Queued', build: 'Building', check: 'Checking', review: 'Review', done: 'Done', cancelled: 'Cancelled',
    needs: 'Needs you', blocked: 'Blocked', failed: 'Failed', paused: 'Paused', review_wait: 'Waiting for review',
    QUOTA: 'out of quota', AUTH: 'login needed', NETWORK: 'network down', INTERRUPTED: 'interrupted',
    MISSING_BINARY: 'CLI missing', OTHER: 'other',
    rootMissing: 'folder deleted, history kept',
    noProjects: 'No project yet. Run `agent-bridge init` in a project.', unreadable: 'Cannot read right now',
    nothing: 'Nothing is waiting.', noRunning: 'No task running.', noRequests: 'No requests yet.',
    noActivity: 'No activity in the last 24 hours.', viewOnly: 'View only', unknown: 'unknown', dash: '–',
    taskCount: '{done}/{total} tasks', requestCount: '{n} requests', agentCount: '{n} agents',
    worktrees: '+{n} worktree', quota: 'Quota left: {value}', elapsed: '{elapsed} / {timeout}',
    seconds: '{n}s', minutes: '{n}m', hours: '{n}h', days: '{n}d', ago: '{time} ago', now: 'just now',
    event: '{id} · {stage} · {time}', pauseEvent: '{id} paused: {reason} · {time}',
    copy: 'Copy command', copied: 'Copied', select: 'Command selected; copy it manually.',
    close: 'Close', closeSymbol: '×', notFound: 'Not found', detailError: 'Cannot load details. Try again.', retry: 'Retry',
    summary: 'Summary', tasks: 'Tasks', technical: 'Technical details', command: 'Suggested command',
    state: 'Raw state', needs_human: 'Needs human approval', risk_level: 'Risk level', agent: 'Agent', account: 'Account',
    attempts: 'Attempts', run: 'run {n}/{max}', checks: 'Verification checks', scope_violations: 'Scope violations',
    head_changed: 'HEAD changed', files: 'Changed files', stat: 'Diff statistics', reviews: 'Reviews', final: 'Final summary',
    failure: 'Diagnosis', yes: 'Yes', no: 'No', none: 'None', passed: 'Passed', started_at: 'Started', ended_at: 'Ended',
    exit_code: 'Exit code', kind: 'Kind', outcome: 'Outcome', decision: 'Decision', notes: 'Notes', actor: 'Actor', n: 'Run', error: 'Error',
    chart: 'Stage counts: {values}', hourly: 'Events per hour: {values}',
    BACKLOG: 'Backlog', READY: 'Ready', RUNNING: 'Running', REWORK: 'Rework', VERIFYING: 'Verifying',
    REVIEW: 'Review', APPROVED: 'Approved', DONE: 'Done', CANCELLED: 'Cancelled', PAUSED: 'Paused',
    FAILED: 'Failed', BLOCKED: 'Blocked', low: 'Low', medium: 'Medium', high: 'High'
  },
  vi: {
    brand: 'agent-bridge', vi: 'VI', en: 'EN', all: 'Tất cả', projects: 'Dự án', language: 'Ngôn ngữ',
    live: 'Trực tiếp · mỗi 3 giây', offline: 'Mất kết nối · thử lại sau 5 giây', loading: 'Đang tải…',
    attention: 'Cần chú ý', progress: 'Tiến độ', running: 'Đang chạy', activity: '24 giờ qua',
    requests: 'Yêu cầu', request: 'Yêu cầu', project: 'Dự án', stage: 'Giai đoạn', updated: 'Cập nhật', id: 'ID',
    queue: 'Chờ', build: 'Đang làm', check: 'Kiểm tra', review: 'Duyệt', done: 'Xong', cancelled: 'Đã hủy',
    needs: 'Cần bạn', blocked: 'Bị chặn', failed: 'Lỗi', paused: 'Tạm dừng', review_wait: 'Chờ duyệt',
    QUOTA: 'hết quota', AUTH: 'lỗi đăng nhập', NETWORK: 'mất mạng', INTERRUPTED: 'bị ngắt',
    MISSING_BINARY: 'thiếu CLI', OTHER: 'khác',
    rootMissing: 'thư mục đã xoá, lịch sử vẫn còn',
    noProjects: 'Chưa có dự án nào. Chạy `agent-bridge init` trong một dự án.', unreadable: 'Không đọc được lúc này',
    nothing: 'Không có gì đang chờ.', noRunning: 'Không có tác vụ đang chạy.', noRequests: 'Chưa có yêu cầu.',
    noActivity: 'Không có hoạt động trong 24 giờ qua.', viewOnly: 'Chỉ xem', unknown: 'chưa rõ', dash: '–',
    taskCount: '{done}/{total} tác vụ', requestCount: '{n} yêu cầu', agentCount: '{n} agent',
    worktrees: '+{n} worktree', quota: 'Quota còn lại: {value}', elapsed: '{elapsed} / {timeout}',
    seconds: '{n} giây', minutes: '{n} phút', hours: '{n} giờ', days: '{n} ngày', ago: '{time} trước', now: 'vừa xong',
    event: '{id} · {stage} · {time}', pauseEvent: '{id} tạm dừng: {reason} · {time}',
    copy: 'Sao chép lệnh', copied: 'Đã sao chép', select: 'Đã chọn lệnh; hãy sao chép thủ công.',
    close: 'Đóng', closeSymbol: '×', notFound: 'Không tìm thấy', detailError: 'Không tải được chi tiết. Hãy thử lại.', retry: 'Thử lại',
    summary: 'Tóm tắt', tasks: 'Tác vụ', technical: 'Chi tiết kỹ thuật', command: 'Lệnh gợi ý',
    state: 'Trạng thái gốc', needs_human: 'Cần người phê duyệt', risk_level: 'Mức rủi ro', agent: 'Agent', account: 'Tài khoản',
    attempts: 'Lần chạy', run: 'lần chạy {n}/{max}', checks: 'Kiểm tra xác minh', scope_violations: 'Vi phạm phạm vi',
    head_changed: 'HEAD thay đổi', files: 'Tệp thay đổi', stat: 'Thống kê thay đổi', reviews: 'Đánh giá', final: 'Tóm tắt cuối',
    failure: 'Chẩn đoán', yes: 'Có', no: 'Không', none: 'Không có', passed: 'Đạt', started_at: 'Bắt đầu', ended_at: 'Kết thúc',
    exit_code: 'Mã thoát', kind: 'Loại', outcome: 'Kết quả', decision: 'Quyết định', notes: 'Ghi chú', actor: 'Người đánh giá', n: 'Lần chạy', error: 'Lỗi',
    chart: 'Số tác vụ theo giai đoạn: {values}', hourly: 'Sự kiện mỗi giờ: {values}',
    BACKLOG: 'Tồn đọng', READY: 'Sẵn sàng', RUNNING: 'Đang chạy', REWORK: 'Làm lại', VERIFYING: 'Đang xác minh',
    REVIEW: 'Duyệt', APPROVED: 'Đã duyệt', DONE: 'Xong', CANCELLED: 'Đã hủy', PAUSED: 'Tạm dừng',
    FAILED: 'Lỗi', BLOCKED: 'Bị chặn', low: 'Thấp', medium: 'Trung bình', high: 'Cao'
  }
};
const STAGES = ['queue', 'build', 'check', 'review', 'done'];
// Build the namespace without an external resource reference.
const SVG_NS = ['http:', '', 'www.w3.org', '2000', 'svg'].join('/');
const $ = id => document.getElementById(id);
let language = navigator.language.toLowerCase().startsWith('vi') ? 'vi' : 'en';
try { const saved = localStorage.getItem('dashboard-language'); if (['vi', 'en'].includes(saved)) language = saved; } catch {}
let state = {p: '', lang: language, r: '', t: ''};
let overview = null, timer = null, inFlight = false, disconnected = false;
let detailVersion = 0, requestData = null, taskData = null, detailError = '', returnFocus = null;
const tr = (key, values = {}) => {
  let value = I18N[state.lang][key] ?? key;
  for (const [name, replacement] of Object.entries(values)) value = value.replaceAll(`{${name}}`, String(replacement));
  return value;
};
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = String(text);
  if (className) node.setAttribute('class', className);
  return node;
}
function attr(node, key, value) { if (node.getAttribute(key) !== String(value)) node.setAttribute(key, String(value)); }
function text(node, value) { if (node.textContent !== String(value)) node.textContent = String(value); }
function svg(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}
function button(label, action, className) {
  const node = el('button', label, className); node.type = 'button'; node.onclick = action; return node;
}
function syncNode(current, next) {
  if (current.nodeType !== next.nodeType || current.nodeName !== next.nodeName) { current.replaceWith(next); return next; }
  if (current.nodeType === Node.TEXT_NODE) { if (current.nodeValue !== next.nodeValue) current.nodeValue = next.nodeValue; return current; }
  for (const attribute of Array.from(current.attributes)) if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
  for (const attribute of Array.from(next.attributes)) {
    if (attribute.name === 'style') { if (current.style.cssText !== next.style.cssText) current.style.cssText = next.style.cssText; }
    else attr(current, attribute.name, attribute.value);
  }
  current.onclick = next.onclick;
  current.onkeydown = next.onkeydown;
  const keyed = new Map(Array.from(current.children).filter(n => n.dataset.key).map(n => [n.dataset.key, n]));
  const unused = new Set(current.childNodes);
  let cursor = current.firstChild;
  for (const child of Array.from(next.childNodes)) {
    const key = child.nodeType === Node.ELEMENT_NODE ? child.dataset.key : null;
    let match = key ? keyed.get(key) : cursor;
    if (match && key && match !== cursor) current.insertBefore(match, cursor);
    if (!match) { current.insertBefore(child, cursor); match = child; }
    else { unused.delete(match); match = syncNode(match, child); }
    cursor = match.nextSibling;
  }
  for (const child of unused) child.remove();
  return current;
}
// Preserve keyed containers and their children when their rendered inputs are unchanged.
function patch(parent, items, keyOf, signatureOf, render) {
  const existing = new Map(Array.from(parent.children, node => [node.dataset.key, node]));
  let cursor = parent.firstElementChild;
  for (const item of items) {
    const key = String(keyOf(item)); let node = existing.get(key);
    if (!node) { node = el(parent.tagName === 'TBODY' ? 'tr' : parent.id === 'columns' ? 'th' : 'div'); attr(node, 'data-key', key); }
    existing.delete(key);
    const signature = JSON.stringify([state.lang, signatureOf(item)]);
    if (node._signature !== signature) {
      const next = el(node.tagName); attr(next, 'data-key', key); render(next, item);
      syncNode(node, next); node._signature = signature;
    }
    if (node !== cursor) parent.insertBefore(node, cursor);
    cursor = node.nextElementSibling;
  }
  for (const node of existing.values()) node.remove();
}
function duration(seconds) {
  const n = Math.max(0, Math.floor(seconds));
  if (n < 60) return tr('seconds', {n});
  if (n < 3600) return tr('minutes', {n: Math.floor(n / 60)});
  if (n < 86400) return tr('hours', {n: Math.floor(n / 3600)});
  return tr('days', {n: Math.floor(n / 86400)});
}
function relative(ts) { return ts == null ? tr('unknown') : Date.now() / 1000 - ts < 5 ? tr('now') : tr('ago', {time: duration(Date.now() / 1000 - ts)}); }
const percent = (done, total) => total ? `${Math.floor(100 * done / total)}%` : tr('dash');
const totalOf = counts => STAGES.reduce((n, stage) => n + (counts[stage] || 0), 0);
const stageText = item => tr(item.stage || 'cancelled') + (item.exception ? ` · ${tr(item.exception)}` : '');
function chartLabel(counts) { return tr('chart', {values: STAGES.map(stage => `${tr(stage)} ${counts[stage] || 0}`).join(', ')}); }
function meter(value, label) {
  const node = el('div', undefined, 'meter'); attr(node, 'role', 'img'); attr(node, 'aria-label', label);
  const fill = el('span'); fill.style.width = `${Math.min(100, Math.max(0, value || 0))}%`; node.append(fill); return node;
}
function stageBar(counts) {
  const node = el('div', undefined, 'stage-bar'); attr(node, 'role', 'img'); attr(node, 'aria-label', chartLabel(counts));
  const total = totalOf(counts);
  for (const stage of STAGES) { const part = el('span', undefined, `segment ${stage}`); part.style.width = `${total ? 100 * (counts[stage] || 0) / total : 0}%`; node.append(part); }
  return node;
}
function heading(key) {
  const node = el('h2'); const icon = svg('svg', {viewBox: '0 0 16 16', class: 'icon', 'aria-hidden': 'true'});
  icon.append(svg('path', {d: 'M2 12V8m4 4V4m4 8V6m4 6V2'})); node.append(icon, el('span', tr(key))); return node;
}
function legend(counts) {
  const node = el('div', undefined, 'legend');
  for (const stage of STAGES) node.append(el('span', `${tr(stage)} ${counts[stage] || 0}`)); return node;
}
function projectName(pid) { return overview?.projects.find(p => p.pid === pid)?.name || tr('unknown'); }
function openItem(pid, rid, tid = '') {
  returnFocus = document.activeElement;
  setState({p: pid, r: rid, t: tid});
}
async function copyCommand(command, trigger, field) {
  try { await navigator.clipboard.writeText(command); text(trigger, tr('copied')); }
  catch {
    const input = field || el('textarea', command, 'command');
    if (!field) { input.readOnly = true; trigger.after(input); }
    input.focus(); input.select(); text(trigger, tr('select'));
  }
}
function attentionTile(node, items) {
  node.replaceChildren(heading('attention'), el('div', items.length, 'value'));
  if (!items.length) { node.append(el('p', tr('nothing'), 'muted')); return; }
  const list = el('div'); node.append(list);
  for (const item of items.slice(0, 5)) {
    const row = el('div', undefined, 'item'); attr(row, 'data-key', `${item.pid}:${item.task_id}`); const line = el('div', undefined, 'item-line');
    line.append(button(`${item.task_id} · ${tr(item.kind)}${item.reason ? ` · ${tr(item.reason)}` : ''}`, () => openAttention(item), `open ${item.kind}`));
    if (item.command) { const copy = button(tr('copy'), event => copyCommand(item.command, event.currentTarget)); line.append(copy); }
    row.append(line, el('p', item.objective), el('span', `${projectName(item.pid)} · ${relative(item.since)}`, 'muted')); list.append(row);
  }
}
async function openAttention(item) {
  const request = overview.requests.find(r => r.pid === item.pid && r.id === item.task_id);
  openItem(item.pid, request?.id || '', item.task_id);
  if (request) return;
  for (const candidate of overview.requests.filter(r => r.pid === item.pid && r.id.startsWith('W'))) {
    try {
      const detail = await getJSON(`/api/projects/${encodeURIComponent(item.pid)}/requests/${encodeURIComponent(candidate.id)}`);
      if (detail.tasks.some(task => task.task_id === item.task_id)) {
        if (state.p === item.pid && state.t === item.task_id && !state.r) setState({r: candidate.id});
        return;
      }
    } catch { /* The task detail remains available when its request cannot be read. */ }
  }
}
function progressTile(node, data) {
  const {counts, requests} = data, total = totalOf(counts), done = counts.done || 0;
  const ring = svg('svg', {viewBox: '0 0 108 108', class: 'ring', role: 'img', 'aria-label': chartLabel(counts)});
  ring.append(svg('circle', {cx: 54, cy: 54, r: 40, class: 'track'}));
  let offset = 0; const circumference = 2 * Math.PI * 40;
  for (const stage of STAGES) {
    const length = total ? circumference * (counts[stage] || 0) / total : 0;
    ring.append(svg('circle', {cx: 54, cy: 54, r: 40, class: `segment ${stage}`, 'stroke-dasharray': `${length} ${circumference - length}`, 'stroke-dashoffset': -offset})); offset += length;
  }
  const layout = el('div', undefined, 'ring-layout'), figures = el('div');
  figures.append(el('div', percent(done, total), 'value'), el('div', tr('taskCount', {done, total}), 'muted'));
  layout.append(ring, figures); node.replaceChildren(heading('progress'), layout, legend(counts), el('footer', tr('requestCount', {n: requests.length}), 'badge'));
}
function runningTile(node, items) {
  const agents = [...new Set(items.map(item => item.agent).filter(Boolean))];
  node.replaceChildren(heading('running'), el('div', items.length, 'value'), el('span', agents.length === 1 ? agents[0] : tr('agentCount', {n: agents.length}), 'badge'));
  if (!items.length) node.append(el('p', tr('noRunning'), 'muted'));
  for (const item of items) {
    const row = el('div', undefined, 'item');
    const elapsed = item.started_at == null ? null : Math.max(0, Date.now() / 1000 - item.started_at);
    const timing = tr('elapsed', {elapsed: elapsed == null ? tr('unknown') : duration(elapsed), timeout: duration(item.timeout_s)});
    row.append(button(`${item.task_id} · ${tr(item.stage)} · ${item.agent || tr('unknown')}`, () => openItem(item.pid, '', item.task_id), 'open'));
    row.append(el('div', timing, 'muted'), meter(elapsed == null ? 0 : 100 * elapsed / item.timeout_s, timing),
      el('footer', tr('quota', {value: item.quota_left == null ? tr('unknown') : `${Math.floor(item.quota_left)}%`})));
    node.append(row);
  }
}
function projectTile(node, project) {
  const title = el('h2', project.name);
  node.replaceChildren(title, el('p', project.root, 'muted'));
  if (project.error) { node.append(el('p', tr('unreadable'))); return; }
  node.append(el('div', project.percent == null ? tr('dash') : `${project.percent}%`, 'value'), stageBar(project.counts), legend(project.counts));
  if (project.badge) {
    const badge = project.badge;
    const label = badge.kind === 'worktrees' ? tr('worktrees', {n: badge.count}) : tr(badge.text_key || badge.kind);
    node.append(badge.ref ? button(label, () => openItem(project.pid, badge.ref), `badge ${badge.kind}`) : el('span', label, 'badge'));
  }
  if (project.root_missing) node.append(el('footer', tr('rootMissing')));
  if (project.worktree_of) node.append(el('footer', projectName(project.worktree_of)));
}
function activityTile(node, activity) {
  node.replaceChildren(heading('activity'), el('div', activity.done_24h, 'value'), el('span', tr('done'), 'muted'));
  const values = activity.per_hour, max = Math.max(1, ...values);
  const chart = svg('svg', {viewBox: '0 0 276 60', class: 'sparkline', role: 'img', 'aria-label': tr('hourly', {values: values.join(', ')})});
  chart.append(svg('polyline', {points: values.map((v, i) => `${i * 12},${55 - v / max * 50}`).join(' ')})); node.append(chart);
  const latest = activity.latest;
  node.append(el('footer', latest ? tr(latest.kind === 'paused' ? 'pauseEvent' : 'event', {
    id: latest.task_id, stage: tr(latest.kind || 'cancelled'), reason: tr(latest.reason || 'OTHER'), time: relative(latest.ts)
  }) : tr('noActivity')));
}
function renderOverview() {
  document.documentElement.lang = state.lang;
  document.title = tr('brand');
  text($('brand'), tr('brand')); text($('live'), tr(disconnected ? 'offline' : overview ? 'live' : 'loading'));
  attr($('live'), 'class', `pill live${disconnected ? ' offline' : ''}`);
  $('content').classList.toggle('disconnected', disconnected);
  text($('requests-heading'), tr('requests')); text($('footer'), tr('viewOnly'));
  text($('requests-empty'), tr('noRequests'));
  attr($('filters'), 'aria-label', tr('projects')); attr($('languages'), 'aria-label', tr('language'));
  patch($('languages'), ['vi', 'en'], x => x, x => [x, state.lang], (node, lang) => {
    node.replaceChildren(button(tr(lang), () => setState({lang})));
    attr(node.firstChild, 'aria-pressed', lang === state.lang);
  });
  patch($('columns'), ['request', 'project', 'stage', 'progress', 'updated', 'id'], x => x, x => x, (node, key) => {
    attr(node, 'scope', 'col'); text(node, tr(key));
  });
  if (!overview) return;
  patch($('filters'), [{pid: '', name: tr('all')}, ...overview.projects], p => p.pid, p => [p.name, state.p], (node, p) => {
    node.replaceChildren(button(p.name, () => setState({p: p.pid, r: '', t: ''})));
    attr(node.firstChild, 'aria-pressed', p.pid === state.p);
  });
  const belongs = item => !state.p || item.pid === state.p;
  const projects = overview.projects.filter(p => !state.p || p.pid === state.p);
  const requests = overview.requests.filter(belongs), attention = overview.attention.filter(belongs), running = overview.running.filter(belongs);
  const counts = Object.fromEntries(STAGES.map(stage => [stage, projects.reduce((n, p) => n + (p.counts[stage] || 0), 0)]));
  const time = Math.floor(Date.now() / 1000);
  const tiles = overview.projects.length ? [
    {key: 'attention', data: attention, render: attentionTile, time, names: overview.projects.map(p => p.name)},
    {key: 'progress', data: {counts, requests}, render: progressTile},
    {key: 'running', data: running, render: runningTile, time},
    ...projects.map(p => ({key: p.pid, data: p, render: projectTile})),
    {key: 'activity', data: overview.activity, render: activityTile, time}
  ] : [{key: 'empty', data: null, render: node => node.replaceChildren(el('p', tr('noProjects')))}];
  patch($('tiles'), tiles, t => t.key, t => [t.data, t.time, t.names], (node, tile) => {
    attr(node, 'class', `tile ${tile.key === 'attention' ? `attention${attention.length ? ' has-attention' : ''}` : ''}`);
    tile.render(node, tile.data);
  });
  patch($('request-rows'), requests, r => `${r.pid}:${r.id}`, r => [r, relative(r.updated_at), projectName(r.pid)], (row, request) => {
    attr(row, 'tabindex', '0'); attr(row, 'class', request.exception || '');
    row.onclick = () => openItem(request.pid, request.id);
    row.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); openItem(request.pid, request.id); } };
    const progress = el('td');
    progress.append(meter(request.total ? request.done / request.total * 100 : 0, tr('taskCount', request)), el('span', request.total ? `${request.done}/${request.total}` : tr('dash')));
    const stage = el('td'); stage.append(el('span', stageText(request), request.exception || ''));
    row.replaceChildren(el('td', request.name), el('td', projectName(request.pid)), stage, progress, el('td', relative(request.updated_at)), el('td', request.id));
  });
  $('requests-empty').hidden = requests.length > 0;
}
function setState(change) {
  const next = {...state, ...change};
  const params = new URLSearchParams();
  for (const key of ['p', 'lang', 'r', 't']) params.set(key, next[key]);
  const hash = `#${params}`;
  if (location.hash === hash) return;
  location.hash = hash;
}
function readState() {
  const params = new URLSearchParams(location.hash.slice(1));
  const previous = state;
  state = {p: params.get('p') || '', lang: ['vi', 'en'].includes(params.get('lang')) ? params.get('lang') : state.lang,
    r: params.get('r') || '', t: params.get('t') || ''};
  try { localStorage.setItem('dashboard-language', state.lang); } catch {}
  renderOverview();
  if (['p', 'r', 't'].some(key => previous[key] !== state[key])) loadDetail(); else renderDetail();
}
function closeDrawer() { setState({r: '', t: ''}); }
async function getJSON(url) {
  const response = await fetch(url, {cache: 'no-store'});
  if (!response.ok) { const error = new Error(); error.status = response.status; throw error; }
  return response.json();
}
async function loadDetail(preserve = false) {
  const version = ++detailVersion;
  if (preserve !== true) { requestData = null; taskData = null; }
  detailError = '';
  renderDetail();
  if (!state.p || (!state.r && !state.t)) return;
  const base = `/api/projects/${encodeURIComponent(state.p)}`;
  try {
    const [request, task] = await Promise.all([
      state.r ? getJSON(`${base}/requests/${encodeURIComponent(state.r)}`) : null,
      state.t ? getJSON(`${base}/tasks/${encodeURIComponent(state.t)}`) : null
    ]);
    if (version !== detailVersion) return;
    requestData = request; taskData = task; renderDetail();
  } catch (error) {
    if (version !== detailVersion) return;
    detailError = error.status === 404 ? 'notFound' : 'detailError'; renderDetail();
  }
}
function labeled(list, label, value) { list.append(el('dt', tr(label)), el('dd', value == null || value === '' ? tr('unknown') : value)); }
function records(parent, key, records, fields) {
  parent.append(el('h3', tr(key)));
  if (!records.length) { parent.append(el('p', tr('none'), 'muted')); return; }
  for (const record of records) {
    const list = el('dl');
    for (const field of fields) {
      let value = record[field];
      if (field.endsWith('_at') || field === 'ts') value = value == null ? tr('unknown') : new Date(value * 1000).toLocaleString(state.lang);
      if (typeof value === 'boolean') value = tr(value ? 'yes' : 'no');
      labeled(list, field === 'ts' ? 'updated' : field, value);
    }
    parent.append(list);
  }
}
function renderDetail() {
  const drawer = $('drawer'), wasHidden = drawer.hidden;
  drawer.hidden = !state.p || (!state.r && !state.t);
  text($('close'), tr('closeSymbol')); attr($('close'), 'aria-label', tr('close'));
  if (drawer.hidden) { if (!wasHidden && returnFocus?.isConnected) returnFocus.focus(); return; }
  if (wasHidden) $('close').focus();
  text($('drawer-title'), requestData?.name || taskData?.objective || state.r || state.t);
  const body = $('drawer-body');
  const signature = JSON.stringify([state.lang, requestData, taskData, detailError]);
  if (body._signature === signature) return;
  body._signature = signature; body.replaceChildren();
  if (detailError) { body.append(el('p', tr(detailError)), button(tr('retry'), () => loadDetail())); return; }
  if (!requestData && !taskData) { body.append(el('p', tr('loading'))); return; }
  if (requestData) {
    body.append(el('h3', tr('summary')), el('p', tr('taskCount', requestData)), legend(requestData.summary.counts));
    const exceptions = el('div', undefined, 'legend');
    for (const [kind, count] of Object.entries(requestData.summary.exceptions)) exceptions.append(el('span', `${tr(kind)} ${count}`, kind));
    body.append(exceptions);
    const steps = el('ol', undefined, 'stepper');
    for (const stage of STAGES) steps.append(el('li', tr(stage), STAGES.indexOf(stage) <= STAGES.indexOf(requestData.stage) ? 'current' : ''));
    body.append(steps, el('h3', tr('tasks')));
    const tasks = el('div', undefined, 'task-list'); body.append(tasks);
    for (const task of requestData.tasks) {
      const item = button(`${task.task_id} · ${task.objective} · ${stageText(task)}`, () => setState({t: task.task_id}));
      attr(item, 'data-key', task.task_id); attr(item, 'aria-pressed', state.t === task.task_id); tasks.append(item);
    }
  }
  if (!taskData) return;
  const task = taskData;
  body.append(el('h3', tr('technical')), el('p', `${task.task_id} · ${task.objective}`));
  if (task.exception) body.append(el('p', `${tr(task.exception)}${task.state === 'PAUSED' ? ` · ${tr((task.failure || '').split(':')[0] in I18N.en ? task.failure.split(':')[0] : 'OTHER')}` : ''}`, task.exception));
  const details = el('dl');
  for (const field of ['state', 'risk_level', 'agent', 'account']) labeled(details, field, task[field] == null ? null : ['state', 'risk_level'].includes(field) ? tr(task[field]) : task[field]);
  labeled(details, 'needs_human', tr(task.needs_human ? 'yes' : 'no'));
  labeled(details, 'attempts', tr('run', {n: task.attempts_used, max: task.max_attempts}));
  labeled(details, 'head_changed', tr(task.head_changed ? 'yes' : 'no')); body.append(details);
  if (task.failure) body.append(el('h3', tr('failure')), el('pre', task.failure));
  records(body, 'attempts', task.attempts, ['n', 'kind', 'outcome', 'started_at', 'ended_at', 'exit_code']);
  records(body, 'checks', task.checks, ['command', 'passed', 'error']);
  for (const key of ['scope_violations', 'files', 'stat', 'final']) {
    const value = Array.isArray(task[key]) ? task[key].join('\n') : task[key];
    body.append(el('h3', tr(key)), el('pre', value || tr('none')));
  }
  records(body, 'reviews', task.reviews, ['decision', 'notes', 'actor', 'ts']);
  if (task.command) {
    const field = el('textarea', task.command, 'command'); field.readOnly = true; attr(field, 'aria-label', tr('command'));
    const copy = button(tr('copy'), () => copyCommand(task.command, copy, field));
    body.append(el('h3', tr('command')), field, copy);
  }
}
async function poll() {
  clearTimeout(timer);
  if (document.hidden || inFlight) return;
  inFlight = true;
  try {
    const previous = overview;
    overview = await getJSON('/api/overview'); disconnected = false;
    if (previous && state.p && (state.r || state.t)) {
      const relevant = data => JSON.stringify(data.requests.filter(r => r.pid === state.p));
      if (relevant(previous) !== relevant(overview)) loadDetail(true);
    }
  }
  catch { disconnected = true; }
  finally {
    inFlight = false; renderOverview();
    if (!document.hidden) timer = setTimeout(poll, disconnected ? 5000 : 3000);
  }
}
$('close').addEventListener('click', closeDrawer);
document.addEventListener('keydown', event => {
  if ($('drawer').hidden) return;
  if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); }
  if (event.key === 'Tab') {
    const nodes = Array.from($('drawer').querySelectorAll('button, textarea, [tabindex="0"]'));
    const first = nodes[0], last = nodes[nodes.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }
});
window.addEventListener('hashchange', readState);
document.addEventListener('visibilitychange', () => { clearTimeout(timer); if (!document.hidden) poll(); });
readState();
history.replaceState(null, '', `#${new URLSearchParams(state)}`);
loadDetail(); poll();
