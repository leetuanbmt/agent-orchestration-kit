'use strict';

const launchParams = new URLSearchParams(location.search);
if (launchParams.has("token")) {
  sessionStorage.setItem("agent-bridge-token", launchParams.get("token"));
  history.replaceState(history.state, "", location.pathname + location.hash);
}

const I18N = {
  en: {
    log: 'Log', messagesOnly: 'Messages only', logTail: 'Showing the last 256 KB', noLog: 'No log for this attempt',
    awaitingReview: 'Awaiting review', nextStep: 'Next step', approve: 'Approve', result: 'View result', resume: 'Resume',
    groupWorktree: 'Group by worktree', stale: 'stale',
    views: 'Views', open: 'Open', needsYou: 'Needs you', waiting: 'Waiting', status: 'Status', task: 'Task', location: 'Location',
    search: 'Search', searchPlaceholder: 'Task ID or objective', worktree: 'Worktree', branch: 'Branch', main: 'main',
    allProjects: 'All projects', allWorktrees: 'All worktrees', clear: 'Clear filters', noMatches: 'No task matches these filters',
    viewTasks: 'View tasks', projectCounts: 'Needs you {needs} · Open {open} · Done {done}',
    showWorktrees: 'Show {n} worktrees without open tasks', hideWorktrees: 'Hide {n} worktrees without open tasks',
    tokenRequired: "Open the link printed by agent-bridge web.",
    brand: 'agent-bridge', vi: 'VI', en: 'EN', all: 'All', projects: 'Projects', language: 'Language',
    live: 'Live · every 3 s', offline: 'Disconnected · retrying in 5 s', loading: 'Loading…',
    running: 'Running', project: 'Project', updated: 'Updated', done: 'Done', cancelled: 'Cancelled',
    needs: 'Needs you', blocked: 'Blocked', failed: 'Failed', paused: 'Paused', review_wait: 'Waiting for review',
    QUOTA: 'out of quota', AUTH: 'login needed', NETWORK: 'network down', INTERRUPTED: 'interrupted',
    MISSING_BINARY: 'CLI missing', OTHER: 'other',
    rootMissing: 'folder deleted, history kept',
    noProjects: 'No project yet. Run `agent-bridge init` in a project.', unreadable: 'Cannot read right now',
    viewOnly: 'View only', unknown: 'unknown', dash: '–',
    taskCount: '{n} tasks', taskCountOne: '{n} task', seconds: '{n}s', minutes: '{n}m', hours: '{n}h', days: '{n}d', ago: '{time} ago', now: 'just now',
    copy: 'Copy command', copied: 'Copied', select: 'Command selected; copy it manually.',
    close: 'Close', closeSymbol: '×', notFound: 'Not found', detailError: 'Cannot load details. Try again.', retry: 'Retry',
    tasks: 'Tasks', technical: 'Technical details', command: 'Suggested command',
    state: 'Raw state', needs_human: 'Needs human approval', risk_level: 'Risk level', agent: 'Agent', account: 'Account',
    attempts: 'Attempts', run: 'run {n}/{max}', checks: 'Verification checks', scope_violations: 'Scope violations',
    head_changed: 'HEAD changed', files: 'Changed files', stat: 'Diff statistics', reviews: 'Reviews', final: 'Final summary',
    failure: 'Diagnosis', yes: 'Yes', no: 'No', none: 'None', passed: 'Passed', started_at: 'Started', ended_at: 'Ended',
    exit_code: 'Exit code', kind: 'Kind', outcome: 'Outcome', decision: 'Decision', notes: 'Notes', actor: 'Actor', n: 'Run', error: 'Error',
    BACKLOG: 'Backlog', READY: 'Ready', RUNNING: 'Running', REWORK: 'Rework', VERIFYING: 'Verifying',
    REVIEW: 'Review', APPROVED: 'Approved', DONE: 'Done', CANCELLED: 'Cancelled', PAUSED: 'Paused',
    FAILED: 'Failed', BLOCKED: 'Blocked', low: 'Low', medium: 'Medium', high: 'High'
  },
  vi: {
    log: 'Nhật ký', messagesOnly: 'Chỉ tin nhắn agent', logTail: 'Đang hiển thị 256 KB cuối', noLog: 'Không có nhật ký cho lần chạy này',
    awaitingReview: 'Chờ duyệt', nextStep: 'Việc tiếp theo', approve: 'Phê duyệt', result: 'Xem kết quả', resume: 'Tiếp tục',
    groupWorktree: 'Gom theo worktree', stale: 'cũ',
    views: 'Chế độ xem', open: 'Đang mở', needsYou: 'Cần bạn', waiting: 'Đang chờ', status: 'Trạng thái', task: 'Tác vụ', location: 'Vị trí',
    search: 'Tìm kiếm', searchPlaceholder: 'Mã tác vụ hoặc mục tiêu', worktree: 'Worktree', branch: 'Nhánh', main: 'chính',
    allProjects: 'Tất cả dự án', allWorktrees: 'Tất cả worktree', clear: 'Xóa bộ lọc', noMatches: 'Không có tác vụ khớp với bộ lọc này',
    viewTasks: 'Xem tác vụ', projectCounts: 'Cần bạn {needs} · Đang mở {open} · Xong {done}',
    showWorktrees: 'Hiện {n} worktree không có tác vụ mở', hideWorktrees: 'Ẩn {n} worktree không có tác vụ mở',
    tokenRequired: "Mở đường dẫn do agent-bridge web in ra.",
    brand: 'agent-bridge', vi: 'VI', en: 'EN', all: 'Tất cả', projects: 'Dự án', language: 'Ngôn ngữ',
    live: 'Trực tiếp · mỗi 3 giây', offline: 'Mất kết nối · thử lại sau 5 giây', loading: 'Đang tải…',
    running: 'Đang chạy', project: 'Dự án', updated: 'Cập nhật', done: 'Xong', cancelled: 'Đã hủy',
    needs: 'Cần bạn', blocked: 'Bị chặn', failed: 'Lỗi', paused: 'Tạm dừng', review_wait: 'Chờ duyệt',
    QUOTA: 'hết quota', AUTH: 'lỗi đăng nhập', NETWORK: 'mất mạng', INTERRUPTED: 'bị ngắt',
    MISSING_BINARY: 'thiếu CLI', OTHER: 'khác',
    rootMissing: 'thư mục đã xoá, lịch sử vẫn còn',
    noProjects: 'Chưa có dự án nào. Chạy `agent-bridge init` trong một dự án.', unreadable: 'Không đọc được lúc này',
    viewOnly: 'Chỉ xem', unknown: 'chưa rõ', dash: '–',
    taskCount: '{n} tác vụ', taskCountOne: '{n} tác vụ', seconds: '{n} giây', minutes: '{n} phút', hours: '{n} giờ', days: '{n} ngày', ago: '{time} trước', now: 'vừa xong',
    copy: 'Sao chép lệnh', copied: 'Đã sao chép', select: 'Đã chọn lệnh; hãy sao chép thủ công.',
    close: 'Đóng', closeSymbol: '×', notFound: 'Không tìm thấy', detailError: 'Không tải được chi tiết. Hãy thử lại.', retry: 'Thử lại',
    tasks: 'Tác vụ', technical: 'Chi tiết kỹ thuật', command: 'Lệnh gợi ý',
    state: 'Trạng thái gốc', needs_human: 'Cần người phê duyệt', risk_level: 'Mức rủi ro', agent: 'Agent', account: 'Tài khoản',
    attempts: 'Lần chạy', run: 'lần chạy {n}/{max}', checks: 'Kiểm tra xác minh', scope_violations: 'Vi phạm phạm vi',
    head_changed: 'HEAD thay đổi', files: 'Tệp thay đổi', stat: 'Thống kê thay đổi', reviews: 'Đánh giá', final: 'Tóm tắt cuối',
    failure: 'Chẩn đoán', yes: 'Có', no: 'Không', none: 'Không có', passed: 'Đạt', started_at: 'Bắt đầu', ended_at: 'Kết thúc',
    exit_code: 'Mã thoát', kind: 'Loại', outcome: 'Kết quả', decision: 'Quyết định', notes: 'Ghi chú', actor: 'Người đánh giá', n: 'Lần chạy', error: 'Lỗi',
    BACKLOG: 'Tồn đọng', READY: 'Sẵn sàng', RUNNING: 'Đang chạy', REWORK: 'Làm lại', VERIFYING: 'Đang xác minh',
    REVIEW: 'Duyệt', APPROVED: 'Đã duyệt', DONE: 'Xong', CANCELLED: 'Đã hủy', PAUSED: 'Tạm dừng',
    FAILED: 'Lỗi', BLOCKED: 'Bị chặn', low: 'Thấp', medium: 'Trung bình', high: 'Cao'
  }
};
const $ = id => document.getElementById(id);
const GROUPS = ['open', 'needsYou', 'awaitingReview', 'running', 'waiting', 'done', 'cancelled', 'all'];
const expanded = new Set();
let language = navigator.language.toLowerCase().startsWith('vi') ? 'vi' : 'en';
try { const saved = localStorage.getItem('dashboard-language'); if (['vi', 'en'].includes(saved)) language = saved; } catch {}
let state = {view: 'tasks', p: '', w: '', s: 'open', q: '', t: '', g: '', lang: language};
let overview = null, timer = null, inFlight = false, disconnected = false, tokenRequired = false;
let detailVersion = 0, taskData = null, detailError = '', returnFocus = null;
let attemptLog = null;
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
    if (!node) { node = el(parent.tagName === 'TBODY' ? 'tr' : 'div'); attr(node, 'data-key', key); }
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
async function copyCommand(command, trigger, field) {
  try { await navigator.clipboard.writeText(command); text(trigger, tr('copied')); }
  catch {
    const input = field || el('textarea', command, 'command');
    if (!field) { input.readOnly = true; trigger.after(input); }
    input.focus(); input.select(); text(trigger, tr('select'));
  }
}
function projectGroups() {
  const projects = overview?.projects || [], ids = new Set(projects.map(p => p.pid));
  return projects.filter(p => !p.worktree_of || !ids.has(p.worktree_of)).map(project => ({
    project, members: [project, ...projects.filter(p => p.worktree_of === project.pid)]
  }));
}
function matches(task, group) {
  if (group === 'open') return !['DONE', 'CANCELLED'].includes(task.state);
  if (group === 'needsYou') return ['needs', 'failed', 'blocked', 'paused'].includes(task.exception);
  if (group === 'awaitingReview') return task.exception === 'review_wait';
  if (group === 'running') return ['RUNNING', 'VERIFYING'].includes(task.state);
  if (group === 'waiting') return ['BACKLOG', 'READY', 'REWORK'].includes(task.state) || task.state === 'APPROVED' && !task.needs_human;
  if (group === 'done') return task.state === 'DONE';
  if (group === 'cancelled') return task.state === 'CANCELLED';
  return true;
}
function totals(members) {
  const ids = new Set(members.map(p => p.pid)), tasks = (overview?.tasks || []).filter(t => ids.has(t.pid));
  return {needs: tasks.filter(t => matches(t, 'needsYou')).length, open: tasks.filter(t => matches(t, 'open')).length,
    done: tasks.filter(t => matches(t, 'done')).length};
}
function activate(row, action) {
  attr(row, 'tabindex', '0'); row.onclick = action;
  row.onkeydown = event => {
    if (event.target === row && ['Enter', ' '].includes(event.key)) { event.preventDefault(); action(); }
  };
}
function timeCell(ts) {
  const cell = el('td', relative(ts));
  if (ts != null) attr(cell, 'title', new Date(ts * 1000).toLocaleString(state.lang));
  return cell;
}
function headers(row, keys) {
  const signature = JSON.stringify([state.lang, keys]);
  if (row._signature === signature) return;
  row._signature = signature;
  row.replaceChildren(...keys.map(key => { const cell = el('th', tr(key)); attr(cell, 'scope', 'col'); return cell; }));
}
function selectOptions(select, options, value) {
  const signature = JSON.stringify(options);
  if (select._signature !== signature) {
    select.replaceChildren(...options.map(([id, label]) => { const option = el('option', label); option.value = id; return option; }));
    select._signature = signature;
  }
  if (select.value !== value) select.value = value;
}
function clearFilters() { setState({p: '', w: '', s: 'open', q: ''}); }
function openItem(pid, tid) { returnFocus = document.activeElement; setState({t: `${pid}:${tid}`}); }
function statusLabel(task) {
  return task.exception === 'needs' ? `${tr(task.state)} · ${tr('needs')}`
    : `${tr(task.exception || task.state)}${task.exception === 'paused' && task.reason ? ` · ${tr(task.reason)}` : ''}`;
}
function taskLocation(task, full = false) {
  const group = projectGroups().find(g => g.members.some(p => p.pid === task.pid));
  const checkout = group?.members.find(p => p.pid === task.pid);
  const worktree = checkout ? checkout.pid === group.project.pid ? 'main' : checkout.name : tr('unknown');
  return {label: group && (full || !state.p && state.g !== '1') ? `${group.project.name} › ${worktree}` : worktree,
    branch: checkout?.branch || tr('dash')};
}
function renderOverview() {
  document.documentElement.lang = state.lang; document.title = tr('brand'); text($('brand'), tr('brand'));
  text($('live'), tr(tokenRequired ? 'tokenRequired' : disconnected ? 'offline' : overview ? 'live' : 'loading'));
  attr($('live'), 'class', disconnected ? 'live offline' : 'live'); $('content').classList.toggle('disconnected', disconnected);
  text($('footer'), tr('viewOnly')); attr($('languages'), 'aria-label', tr('language')); attr($('views'), 'aria-label', tr('views'));
  patch($('languages'), ['en', 'vi'], x => x, x => [x, state.lang], (node, lang) => {
    node.replaceChildren(button(tr(lang), () => setState({lang}))); attr(node.firstChild, 'aria-pressed', lang === state.lang);
  });
  const groups = projectGroups(), tasks = overview?.tasks || [];
  patch($('views'), ['tasks', 'projects'], x => x, x => [state.view, tasks.filter(t => matches(t, 'open')).length, groups.length], (node, view) => {
    const tab = button(`${tr(view)} (${view === 'tasks' ? tasks.filter(t => matches(t, 'open')).length : groups.length})`, () => setState({view}));
    attr(tab, 'aria-pressed', state.view === view); node.replaceChildren(tab);
  });
  $('tasks-view').hidden = state.view !== 'tasks'; $('projects-view').hidden = state.view !== 'projects';
  text($('search-label'), tr('search')); text($('project-label'), tr('project')); text($('worktree-label'), tr('worktree'));
  attr($('search'), 'placeholder', tr('searchPlaceholder'));
  text($('group-worktree'), tr('groupWorktree')); attr($('group-worktree'), 'aria-pressed', state.g === '1');
  if ($('search').value !== state.q) $('search').value = state.q;
  const selected = groups.find(g => g.project.pid === state.p);
  selectOptions($('project'), [['', tr('allProjects')], ...groups.map(g => [g.project.pid, g.project.name])], state.p);
  selectOptions($('worktree'), [['', tr('allWorktrees')], ...(selected?.members || []).map((p, i) => [p.pid, i === 0 ? `${p.name} (${tr('main')})` : `${p.name} · ${p.branch || tr('dash')}`])], state.w);
  $('worktree').disabled = !selected; text($('clear'), tr('clear'));
  $('clear').hidden = !state.p && !state.w && !state.q && state.s === 'open';
  const ids = selected ? new Set(selected.members.map(p => p.pid)) : null;
  const filtered = tasks.filter(t => (!state.p || ids?.has(t.pid)) && (!state.w || t.pid === state.w) &&
    `${t.task_id} ${t.objective}`.toLocaleLowerCase().includes(state.q.toLocaleLowerCase()));
  patch($('statuses'), GROUPS, x => x, x => [state.s, filtered.filter(t => matches(t, x)).length], (node, group) => {
    const chip = button(`${tr(group)} (${filtered.filter(t => matches(t, group)).length})`, () => setState({s: group}));
    attr(chip, 'aria-pressed', state.s === group); node.replaceChildren(chip);
  });
  const shown = filtered.filter(t => matches(t, state.s)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  text($('task-count'), tr(shown.length === 1 ? 'taskCountOne' : 'taskCount', {n: shown.length})); headers($('columns'), ['status', 'task', 'location', 'agent', 'updated', 'nextStep']);
  let rows = shown;
  if (state.g === '1') {
    const worktrees = new Map();
    for (const task of shown) {
      if (!worktrees.has(task.pid)) worktrees.set(task.pid, []);
      worktrees.get(task.pid).push(task);
    }
    rows = Array.from(worktrees, ([pid, tasks]) => [{pid, groupHeader: true, count: tasks.length}, ...tasks]).flat();
  }
  patch($('task-rows'), rows, t => t.groupHeader ? `group:${t.pid}` : `${t.pid}:${t.task_id}`, t => [t, overview?.projects, state.p, state.g, relative(t.updated_at), Date.now() / 1000 - t.updated_at > 7 * 86400], (row, task) => {
    if (task.groupHeader) {
      const location = taskLocation(task, true), cell = el('th', `${location.label} · ${location.branch} (${task.count})`);
      attr(cell, 'colspan', '6'); attr(cell, 'scope', 'rowgroup'); attr(row, 'class', 'worktree-header'); row.append(cell); return;
    }
    activate(row, () => openItem(task.pid, task.task_id));
    const label = statusLabel(task);
    const status = el('td'); status.append(el('span', label, `badge status-${task.exception || (matches(task, 'running') ? 'running' : task.state.toLowerCase())}`));
    const name = el('td', undefined, 'task-cell'), line = el('div', undefined, 'task-line');
    const objective = el('span', (task.objective || '').split('\n')[0], 'objective');
    attr(objective, 'title', task.objective || ''); line.append(el('code', task.task_id), objective); name.append(line);
    const location = taskLocation(task), place = el('td', location.label); attr(place, 'title', location.branch);
    const next = el('td', undefined, 'next-step');
    if (task.command) {
      const copy = button('⧉', event => { event.stopPropagation(); copyCommand(task.command, event.currentTarget); }, 'copy-icon');
      attr(copy, 'aria-label', 'Copy command'); attr(copy, 'title', task.command);
      next.append(el('span', tr(task.command.split(' ')[1])), copy);
    }
    const updated = timeCell(task.updated_at), age = Date.now() / 1000 - task.updated_at;
    if (matches(task, 'open') && task.updated_at != null && age > 7 * 86400) {
      const stale = el('span', tr('stale'), 'muted stale'); attr(stale, 'title', `No update for ${Math.floor(age / 86400)} days`); updated.append(' ', stale);
    }
    row.replaceChildren(status, name, place, el('td', task.agent ?? task.account ?? tr('dash')), updated, next);
  });
  $('tasks-empty').hidden = shown.length > 0; text($('empty-message'), tr('noMatches')); text($('empty-clear'), tr('clear'));
  groups.sort((a, b) => totals(b.members).needs - totals(a.members).needs || Math.max(...b.members.map(p => p.updated_at || 0)) - Math.max(...a.members.map(p => p.updated_at || 0)));
  patch($('project-cards'), groups, g => g.project.pid, g => [g, tasks, expanded.has(g.project.pid), Math.floor(Date.now() / 1000)], (card, group) => {
    attr(card, 'class', 'project-card'); const project = group.project, count = totals(group.members);
    const head = el('div', undefined, 'card-header'), title = el('div');
    title.append(el('h2', project.name), el('div', project.root, 'muted root'), el('p', tr('projectCounts', count)));
    head.append(title, button(tr('viewTasks'), () => setState({view: 'tasks', p: project.pid, w: '', s: 'open', q: ''}))); card.append(head);
    if (project.root_missing) card.append(el('p', tr('rootMissing'), 'muted'));
    if (project.error) card.append(el('p', tr('unreadable'), 'muted'));
    const scroll = el('div', undefined, 'table-scroll'), table = el('table'), thead = el('thead'), columns = el('tr');
    headers(columns, ['worktree', 'branch', 'needsYou', 'open', 'done', 'updated']); thead.append(columns);
    const body = el('tbody');
    for (const [i, member] of group.members.entries()) {
      const counts = totals([member]);
      if (!counts.open && !expanded.has(project.pid)) continue;
      const row = el('tr'); attr(row, 'data-key', member.pid); if (!counts.open) attr(row, 'class', 'muted');
      activate(row, () => setState({view: 'tasks', p: project.pid, w: member.pid, s: 'open', q: ''}));
      row.append(el('td', `${member.name}${i === 0 ? ` (${tr('main')})` : ''}`), el('td', member.branch || tr('dash')), el('td', counts.needs), el('td', counts.open), el('td', counts.done), timeCell(member.updated_at)); body.append(row);
    }
    table.append(thead, body); scroll.append(table); card.append(scroll);
    const closed = group.members.filter(p => !totals([p]).open).length;
    if (closed) card.append(button(tr(expanded.has(project.pid) ? 'hideWorktrees' : 'showWorktrees', {n: closed}), () => {
      if (expanded.has(project.pid)) expanded.delete(project.pid); else expanded.add(project.pid); renderOverview();
    }, 'toggle-worktrees'));
  });
  text($('projects-empty'), tr('noProjects')); $('projects-empty').hidden = !overview || groups.length > 0;
}
function setState(change, replace = false) {
  const next = {...state, ...change}, hash = `#${new URLSearchParams(next)}`;
  if (replace) { history.replaceState(null, '', hash); readState(); }
  else if (location.hash !== hash) { location.hash = hash; readState(); }
}
function readState() {
  const params = new URLSearchParams(location.hash.slice(1)), previous = state;
  state = {view: params.get('view') === 'projects' ? 'projects' : 'tasks', p: params.get('p') || '', w: params.get('w') || '',
    s: GROUPS.includes(params.get('s')) ? params.get('s') : 'open', q: params.get('q') || '', t: params.get('t') || '', g: params.get('g') === '1' ? '1' : '',
    lang: ['vi', 'en'].includes(params.get('lang')) ? params.get('lang') : state.lang};
  try { localStorage.setItem('dashboard-language', state.lang); } catch {}
  renderOverview(); if (previous.t !== state.t) loadDetail(); else renderDetail();
}
function closeDrawer() { stopLog(); setState({t: ''}); }
function stopLog() {
  if (attemptLog) clearTimeout(attemptLog.timer);
  attemptLog = null;
}
function logLive(log) {
  return attemptLog === log && state.t === log.task &&
    ['RUNNING', 'VERIFYING'].includes(taskData?.state) &&
    log.n === Math.max(...(taskData.attempts || []).map(a => a.n));
}
function drawLog(log, append = []) {
  const bottom = log.box.scrollHeight - log.box.scrollTop - log.box.clientHeight < 24;
  for (const entry of append) {
    const message = entry.text.startsWith('agent:') || entry.text.startsWith('result');
    const row = el('div', undefined, `log-row log-${entry.kind}`);
    row.hidden = log.filter.checked && !message;
    row._message = message;
    const time = entry.ts == null ? tr('dash') : new Date(entry.ts * 1000).toLocaleTimeString('en-GB', {hour12: false});
    row.append(el('span', `${time} `, 'muted'), el(entry.text.startsWith('agent: ') ? 'strong' : 'span', entry.text));
    log.box.append(row);
  }
  log.empty.hidden = log.box.childElementCount > 0;
  if (bottom) log.box.scrollTop = log.box.scrollHeight;
}
async function fetchLog(log) {
  if (attemptLog !== log || log.busy) return;
  log.busy = true;
  clearTimeout(log.timer);
  log.timer = null;
  try {
    const [pid, tid] = log.task.split(':');
    const data = await getJSON(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}/log?n=${log.n}&offset=${log.offset}`);
    if (attemptLog !== log || state.t !== log.task) return;
    log.offset = data.offset;
    log.tail.hidden = !(log.truncated ||= data.truncated);
    drawLog(log, data.entries);
    // Drain complete pages even for attempts that have already finished.
    if (data.entries.length === 500) log.timer = setTimeout(() => fetchLog(log), 0);
  } catch (error) {
    if (attemptLog !== log) return;
    log.empty.textContent = tr(error.status === 404 ? 'noLog' : error.status === 401 ? 'tokenRequired' : 'detailError');
    log.empty.hidden = false;
  } finally {
    log.busy = false;
    if (logLive(log) && !log.timer) log.timer = setTimeout(() => { log.timer = null; fetchLog(log); }, 2000);
  }
}
function renderLog(body, task, selected = null) {
  const latest = Math.max(0, ...task.attempts.map(a => a.n));
  if (attemptLog?.task !== state.t || (selected !== null && selected !== attemptLog?.n) ||
      (attemptLog?.latest !== latest && attemptLog?.n === attemptLog?.latest)) stopLog();
  if (!attemptLog) {
    const section = el('section', undefined, 'attempt-log-section'), select = el('select'), filter = el('input');
    filter.type = 'checkbox';
    const log = {task: state.t, n: selected ?? latest, latest, offset: 0, section, select, filter,
      box: el('div', undefined, 'attempt-log'), tail: el('p', undefined, 'muted'),
      empty: el('p', undefined, 'muted'), timer: null, busy: false};
    attemptLog = log;
    log.box.tabIndex = 0;
    log.tail.hidden = true;
    const heading = el('h3'), label = el('label');
    label.append(filter, el('span'));
    section.append(heading, select, label, log.tail, log.empty, log.box);
    select.onchange = () => {
      if (attemptLog !== log || state.t !== log.task) return;
      renderLog(body, taskData, Number(select.value));
    };
    filter.onchange = () => { for (const row of log.box.children) row.hidden = filter.checked && !row._message; };
  }
  const log = attemptLog;
  log.latest = latest;
  log.section.querySelector('h3').textContent = tr('log');
  log.section.querySelector('label span').textContent = tr('messagesOnly');
  log.select.setAttribute('aria-label', tr('attempts'));
  log.box.setAttribute('aria-label', tr('log'));
  log.select.replaceChildren(...task.attempts.map(a => { const option = el('option', `${tr('n')} ${a.n}`); option.value = String(a.n); return option; }));
  log.select.value = String(log.n);
  log.select.disabled = !latest;
  log.tail.textContent = tr('logTail'); log.empty.textContent = tr('noLog');
  for (const section of body.querySelectorAll('.attempt-log-section')) {
    if (section !== log.section) section.remove();
  }
  body.append(log.section);
  if (!logLive(log)) { clearTimeout(log.timer); log.timer = null; }
  if (latest && !log.busy && !log.timer) fetchLog(log);
}
async function getJSON(url) {
  const response = await fetch(url, {headers: {"X-Bridge-Token": sessionStorage.getItem("agent-bridge-token") || ""}, cache: 'no-store'});
  tokenRequired = response.status === 401;
  if (tokenRequired) overview = null;
  if (!response.ok) { const error = new Error(); error.status = response.status; throw error; }
  return response.json();
}
async function loadDetail(preserve = false) {
  const version = ++detailVersion;
  if (preserve !== true) {
    const known = overview?.tasks.find(t => `${t.pid}:${t.task_id}` === state.t);
    taskData = known ? {...known, preview: true} : null;
  }
  detailError = '';
  renderDetail();
  if (!state.t) return;
  const [pid, tid] = state.t.split(":");
  try {
    const task = await getJSON(`/api/projects/${encodeURIComponent(pid)}/tasks/${encodeURIComponent(tid)}`);
    if (version !== detailVersion) return;
    taskData = task; renderDetail();
  } catch (error) {
    if (version !== detailVersion) return;
    detailError = error.status === 401 ? "tokenRequired" : error.status === 404 ? 'notFound' : 'detailError'; renderDetail();
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
  drawer.hidden = !state.t;
  text($('close'), tr('closeSymbol')); attr($('close'), 'aria-label', tr('close'));
  if (drawer.hidden) { stopLog(); if (!wasHidden && returnFocus?.isConnected) returnFocus.focus(); return; }
  if (attemptLog && attemptLog.task !== state.t) stopLog();
  if (wasHidden) $('close').focus();
  text($('drawer-title'), taskData?.objective || state.t.split(':').pop());
  const body = $('drawer-body');
  const signature = JSON.stringify([state.lang, taskData, detailError]);
  if (body._signature === signature) return;
  body._signature = signature; body.replaceChildren();
  if (detailError) { stopLog(); body.append(el('p', tr(detailError)), button(tr('retry'), () => loadDetail())); return; }
  if (!taskData) { body.append(el('p', tr('loading'))); return; }
  const task = taskData;
  if (task.preview) {
    const details = el('dl');
    labeled(details, 'task', task.task_id); labeled(details, 'status', statusLabel(task));
    labeled(details, 'location', taskLocation(task, true).label);
    labeled(details, 'agent', task.agent ?? task.account ?? tr('dash'));
    labeled(details, 'updated', task.updated_at == null ? null : new Date(task.updated_at * 1000).toLocaleString(state.lang));
    body.append(details, el('p', tr('loading'))); return;
  }
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
    if (key === 'final') renderLog(body, task);
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
    if (previous && state.t) {
      const relevant = data => JSON.stringify(data.tasks.find(t => `${t.pid}:${t.task_id}` === state.t));
      if (relevant(previous) !== relevant(overview)) loadDetail(true);
    }
  }
  catch { disconnected = true; }
  finally {
    inFlight = false; renderOverview();
    if (!document.hidden) timer = setTimeout(poll, disconnected ? 5000 : 3000);
  }
}
$('search').addEventListener('input', event => setState({q: event.target.value}, true));
$('project').onchange = event => setState({p: event.target.value, w: ''});
$('worktree').onchange = event => setState({w: event.target.value});
$('group-worktree').onclick = () => setState({g: state.g === '1' ? '' : '1'});
$('clear').onclick = clearFilters; $('empty-clear').onclick = clearFilters;
$('close').addEventListener('click', closeDrawer);
document.addEventListener('keydown', event => {
  if ($('drawer').hidden) return;
  if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); }
  if (event.key === 'Tab') {
    const nodes = Array.from($('drawer').querySelectorAll('button, textarea, select, input, [tabindex="0"]'));
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
