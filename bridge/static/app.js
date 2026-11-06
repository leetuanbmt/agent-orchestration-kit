'use strict';

const launchParams = new URLSearchParams(location.search);
if (launchParams.has("code")) {
  history.replaceState(history.state, "", location.pathname + location.hash);
}

const I18N = {
  en: {
    workflow: 'Workflow', workflowTitle: '{id} · {objective}', workflowProgress: '{done} of {total} steps done',
    showQueue: 'Show all ({n})', homeProjects: 'Show all projects ({n})', showLess: 'Show fewer', fileGroup: '{dir}/ · {n} files', fileGroupOne: '{dir}/ · {n} file',
    log: 'Log', messagesOnly: 'Messages only', logTail: 'Showing the last 256 KB', noLog: 'No log for this attempt',
    awaitingReview: 'Awaiting review', nextStep: 'Next step', approve: 'Approve', result: 'View result', resume: 'Resume',
    groupWorktree: 'Group by worktree', stale: 'stale',
    open: 'Open', needsAction: 'Needs action', waiting: 'Waiting', status: 'Status', task: 'Task', location: 'Location',
    search: 'Search', searchPlaceholder: 'Task ID, workflow ID or objective', worktree: 'Worktree', branch: 'Branch', main: 'main',
    allProjects: 'All projects', allWorktrees: 'All worktrees', clear: 'Clear filters', noMatches: 'No task matches these filters',
    viewTasks: 'View tasks', projectCounts: 'Needs action {needs} · Open {open} · Done {done}',
    showWorktrees: 'Show {n} worktrees without open tasks', hideWorktrees: 'Hide {n} worktrees without open tasks',
    tokenRequired: "Open the link printed by agent-bridge web again (restart it if the link was used).",
    brand: 'Agent Orchestration Kit', vi: 'VI', en: 'EN', all: 'All', projects: 'Projects', language: 'Language',
    home: 'Home', settings: 'Settings', menu: 'Menu', mainNav: 'Main', appearance: 'Appearance', about: 'About',
    filterProjects: 'Filter projects', filterProjectsPlaceholder: 'Filter projects', needYouCount: '{n} need you',
    showAllProjects: 'Show all ({n})', showFewerProjects: 'Show fewer', noProjectMatch: 'No project matches',
    aboutCli: 'Command-line tool: agent-bridge',
    aboutData: "Data comes from each project's bridge database, read-only; actions run through the command-line tool.",
    aboutRefresh: 'Refreshes every 3 seconds while this page is visible.', shortcuts: 'Keyboard shortcuts',
    shortcutQueue: 'Previous or next item waiting on you', shortcutEsc: 'Close panels and menus',
    shortcutSearch: 'Focus the search box',
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
    FAILED: 'Failed', BLOCKED: 'Blocked', low: 'Low', medium: 'Medium', high: 'High',
    theme: 'Theme', themeSystem: 'System', themeLight: 'Light', themeDark: 'Dark', filters: 'Filter tasks',
    noTasksYet: 'No tasks yet. Create one in a terminal with `agent-bridge create <spec.json>`; it appears here within a few seconds.',
    staleData: 'Cannot reach agent-bridge. Showing data from {time}; retrying every 5 s.',
    noData: 'Cannot reach agent-bridge yet; retrying every 5 s.',
    staleTitle: 'No update for {n} days', lifecycle: 'Lifecycle', stepDone: 'completed', risk: 'Risk: {level}',
    nextResult: 'Open the result to see what the agent did and decide what happens next.',
    nextApprove: 'This task is waiting for your approval before it can finish.',
    nextResume: 'Resume the task when you are ready for the agent to continue.',
    checkFailed: 'Failed',
    board: 'Board', inReview: 'In review', queued: 'Queued', finished: 'Finished ({n})', nothingHere: 'Nothing here',
    done24h: 'Done in 24 h', summary: 'Summary', queueTitle: 'Waiting on you ({n})',
    queueHint: 'Press [ or ] to open the previous or next item.',
    elapsedOf: '{elapsed} of {limit}', elapsedOnly: 'Running for {elapsed}', quotaLeft: '{n}% quota left', timeUsed: 'Time used',
    announce: '{n} tasks need you', announceOne: '1 task needs you', announceNone: 'No task needs you now',
    testsRun: 'Tests run', filesChanged: 'Files changed', rawOutput: 'Raw output',
    accounts: 'Accounts',
    inProgress: 'In progress', nothingRunning: 'Nothing is running.',
    waitingLine: '{n} waiting on you', openOverview: 'Open overview',
    stateAvailable: 'Available', stateQuota: 'Out of quota', stateAuth: 'Login needed',
    slotsShort: '{running} of {max} busy', slotsBusy: '{running} of {max} slots busy',
    pctLeft: '{n}% left', pctUsed: '{n}% used', resetsIn: 'resets in {time}', resetsNow: 'resets now', resetsOn: 'resets {date}', resetDone: 'reset',
    noQuotaShort: 'No quota reported yet',
    noQuotaLong: 'No quota reported yet. It appears after the first run on this account.',
    fallbackOnLine: 'Quota fallback is on: a task out of quota moves to the next eligible agent.',
    fallbackOffLine: 'Quota fallback is off: a task out of quota waits until you resume it.',
    fallbackMixed: 'Quota fallback is on in {a} of {b} projects.',
    showAllAccounts: 'Show all accounts', noAccounts: 'No account is configured for these projects yet.',
    fallbackTitle: 'Quota fallback', on: 'On', off: 'Off', agentOff: '(off)',
    fallbackThreshold: 'Moves when less than {n}% quota is left', fallbackAtZero: 'Moves only when the quota runs out',
    fallbackCandidates: 'Candidates: {list}', fallbackRoleOrder: "the executor role's agents in order",
    fallbackPolicy: 'A person sets this policy in .ai/orchestrator.yaml (routing.quota_fallback); it cannot be changed here.',
    liftedReset: 'Quota mark lifted: the window reset', liftedCooldown: 'Quota mark lifted: the cooldown passed',
    sinceTime: 'since {time}', window5h: '5-hour window', windowWeek: 'Weekly window',
    windowMinutes: '{n}-minute window', windowUsage: 'Usage window', meterLabel: '{label}: {n}% used',
    reportedAt: 'Reported {time}', recentSwitches: 'Recent automatic switches',
    switchMoved: '{task} moved from {from} to {to}', switchRoute: 'moved from {from} to {to}',
    reason_quota: 'out of quota', reason_limit: 'usage limit', reason_below_threshold: 'low quota', reason_other: 'other',
    noSwitches: 'No automatic switches in the last 7 days.',
    accountsHint: 'Accounts are added and logged in by a person in a terminal:'
  },
  vi: {
    workflow: 'Quy trình', workflowTitle: '{id} · {objective}', workflowProgress: '{done} trên {total} bước đã xong',
    showQueue: 'Xem tất cả ({n})', homeProjects: 'Xem tất cả dự án ({n})', showLess: 'Thu gọn', fileGroup: '{dir}/ · {n} tệp', fileGroupOne: '{dir}/ · {n} tệp',
    log: 'Nhật ký', messagesOnly: 'Chỉ tin nhắn agent', logTail: 'Đang hiển thị 256 KB cuối', noLog: 'Không có nhật ký cho lần chạy này',
    awaitingReview: 'Chờ duyệt', nextStep: 'Việc tiếp theo', approve: 'Phê duyệt', result: 'Xem kết quả', resume: 'Tiếp tục',
    groupWorktree: 'Gom theo worktree', stale: 'cũ',
    open: 'Đang mở', needsAction: 'Cần xử lý', waiting: 'Đang chờ', status: 'Trạng thái', task: 'Tác vụ', location: 'Vị trí',
    search: 'Tìm kiếm', searchPlaceholder: 'Mã tác vụ, mã quy trình hoặc mục tiêu', worktree: 'Worktree', branch: 'Nhánh', main: 'chính',
    allProjects: 'Tất cả dự án', allWorktrees: 'Tất cả worktree', clear: 'Xóa bộ lọc', noMatches: 'Không có tác vụ khớp với bộ lọc này',
    viewTasks: 'Xem tác vụ', projectCounts: 'Cần xử lý {needs} · Đang mở {open} · Xong {done}',
    showWorktrees: 'Hiện {n} worktree không có tác vụ mở', hideWorktrees: 'Ẩn {n} worktree không có tác vụ mở',
    tokenRequired: "Mở lại đường dẫn do agent-bridge web in ra (khởi động lại nếu đường dẫn đã được dùng).",
    brand: 'Agent Orchestration Kit', vi: 'VI', en: 'EN', all: 'Tất cả', projects: 'Dự án', language: 'Ngôn ngữ',
    home: 'Tổng quan', settings: 'Cài đặt', menu: 'Menu', mainNav: 'Chính', appearance: 'Giao diện hiển thị', about: 'Giới thiệu',
    filterProjects: 'Lọc dự án', filterProjectsPlaceholder: 'Lọc dự án', needYouCount: '{n} cần bạn',
    showAllProjects: 'Xem tất cả ({n})', showFewerProjects: 'Thu gọn', noProjectMatch: 'Không có dự án khớp',
    aboutCli: 'Công cụ dòng lệnh: agent-bridge',
    aboutData: 'Dữ liệu lấy từ cơ sở dữ liệu bridge của từng dự án, chỉ đọc; các thao tác chạy qua công cụ dòng lệnh.',
    aboutRefresh: 'Làm mới mỗi 3 giây khi trang đang hiển thị.', shortcuts: 'Phím tắt',
    shortcutQueue: 'Mục đang chờ bạn, trước hoặc sau', shortcutEsc: 'Đóng bảng và menu',
    shortcutSearch: 'Chuyển đến ô tìm kiếm',
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
    FAILED: 'Lỗi', BLOCKED: 'Bị chặn', low: 'Thấp', medium: 'Trung bình', high: 'Cao',
    theme: 'Giao diện', themeSystem: 'Theo hệ thống', themeLight: 'Sáng', themeDark: 'Tối', filters: 'Lọc tác vụ',
    noTasksYet: 'Chưa có tác vụ nào. Hãy tạo tác vụ trong terminal bằng `agent-bridge create <spec.json>`; tác vụ sẽ xuất hiện ở đây sau vài giây.',
    staleData: 'Không kết nối được agent-bridge. Đang hiển thị dữ liệu lúc {time}; tự thử lại mỗi 5 giây.',
    noData: 'Chưa kết nối được agent-bridge; tự thử lại mỗi 5 giây.',
    staleTitle: 'Không có cập nhật trong {n} ngày', lifecycle: 'Vòng đời', stepDone: 'đã xong', risk: 'Rủi ro: {level}',
    nextResult: 'Mở kết quả để xem agent đã làm gì và quyết định bước tiếp theo.',
    nextApprove: 'Tác vụ này đang chờ bạn phê duyệt trước khi hoàn tất.',
    nextResume: 'Tiếp tục tác vụ khi bạn sẵn sàng để agent làm tiếp.',
    checkFailed: 'Không đạt',
    board: 'Bảng', inReview: 'Đang duyệt', queued: 'Trong hàng đợi', finished: 'Đã kết thúc ({n})', nothingHere: 'Không có gì ở đây',
    done24h: 'Xong trong 24 giờ', summary: 'Tóm tắt', queueTitle: 'Đang chờ bạn ({n})',
    queueHint: 'Nhấn [ hoặc ] để mở mục trước hoặc sau.',
    elapsedOf: '{elapsed} / {limit}', elapsedOnly: 'Đã chạy {elapsed}', quotaLeft: 'còn {n}% quota', timeUsed: 'Thời gian đã dùng',
    announce: '{n} tác vụ cần bạn', announceOne: '1 tác vụ cần bạn', announceNone: 'Hiện không có tác vụ nào cần bạn',
    testsRun: 'Kiểm thử đã chạy', filesChanged: 'Tệp đã thay đổi', rawOutput: 'Kết quả thô',
    accounts: 'Tài khoản',
    inProgress: 'Đang thực hiện', nothingRunning: 'Không có tác vụ nào đang chạy.',
    waitingLine: '{n} mục đang chờ bạn', openOverview: 'Mở tổng quan',
    stateAvailable: 'Sẵn sàng', stateQuota: 'Hết quota', stateAuth: 'Cần đăng nhập',
    slotsShort: '{running}/{max} đang bận', slotsBusy: '{running}/{max} slot đang bận',
    pctLeft: 'còn {n}%', pctUsed: 'đã dùng {n}%', resetsIn: 'đặt lại sau {time}', resetsNow: 'đặt lại ngay', resetsOn: 'đặt lại vào {date}', resetDone: 'đã đặt lại',
    noQuotaShort: 'Chưa có báo cáo quota',
    noQuotaLong: 'Chưa có báo cáo quota. Thông tin sẽ xuất hiện sau lần chạy đầu tiên trên tài khoản này.',
    fallbackOnLine: 'Chuyển dự phòng khi hết quota đang bật: tác vụ hết quota sẽ chuyển sang agent phù hợp tiếp theo.',
    fallbackOffLine: 'Chuyển dự phòng khi hết quota đang tắt: tác vụ hết quota sẽ chờ đến khi bạn tiếp tục nó.',
    fallbackMixed: 'Chuyển dự phòng khi hết quota đang bật ở {a}/{b} dự án.',
    showAllAccounts: 'Xem tất cả tài khoản', noAccounts: 'Chưa có tài khoản nào được cấu hình cho các dự án này.',
    fallbackTitle: 'Chuyển dự phòng khi hết quota', on: 'Bật', off: 'Tắt', agentOff: '(tắt)',
    fallbackThreshold: 'Chuyển khi còn dưới {n}% quota', fallbackAtZero: 'Chỉ chuyển khi quota đã hết',
    fallbackCandidates: 'Ứng viên: {list}', fallbackRoleOrder: 'các agent của vai trò executor theo thứ tự',
    fallbackPolicy: 'Chính sách này do một người thiết lập trong .ai/orchestrator.yaml (routing.quota_fallback); không thể thay đổi tại đây.',
    liftedReset: 'Đã gỡ dấu hết quota: cửa sổ đã đặt lại', liftedCooldown: 'Đã gỡ dấu hết quota: đã qua thời gian chờ',
    sinceTime: 'từ {time}', window5h: 'Cửa sổ 5 giờ', windowWeek: 'Cửa sổ hằng tuần',
    windowMinutes: 'Cửa sổ {n} phút', windowUsage: 'Cửa sổ sử dụng', meterLabel: '{label}: đã dùng {n}%',
    reportedAt: 'Báo cáo {time}', recentSwitches: 'Chuyển tự động gần đây',
    switchMoved: '{task} đã chuyển từ {from} sang {to}', switchRoute: 'đã chuyển từ {from} sang {to}',
    reason_quota: 'hết quota', reason_limit: 'giới hạn sử dụng', reason_below_threshold: 'quota thấp', reason_other: 'khác',
    noSwitches: 'Không có lần chuyển tự động nào trong 7 ngày qua.',
    accountsHint: 'Tài khoản do một người thêm và đăng nhập trong terminal:'
  }
};
const $ = id => document.getElementById(id);
const GROUPS = ['open', 'needsYou', 'awaitingReview', 'running', 'waiting', 'done', 'cancelled', 'all'];
// The single list of views: the sidebar nav, the hash and the page title all follow it.
const VIEWS = ['overview', 'tasks', 'board', 'accounts', 'settings'];
const viewLabel = view => tr(view === 'overview' ? 'home' : view);
const PROJECT_ROWS = 8;
let projectsExpanded = false, projectFilter = '', sidebarOpen = false, drawerOpen = false;
const narrow = window.matchMedia('(max-width: 1023px)');
// Account states as badges: the family picks the glyph and color, the key the label.
const ACCOUNT_STATES = {available: ['done', 'stateAvailable'], quota: ['paused', 'stateQuota'], auth: ['blocked', 'stateAuth']};
const CAPACITY_ROWS = 6;
let scrollToProjects = false;
const NEEDS_KINDS = ['needs', 'failed', 'blocked', 'paused'];
// Board lanes, left to right, with the heading key of each.
const LANES = [['needsYou', 'needsAction'], ['awaitingReview', 'inReview'], ['running', 'running'], ['waiting', 'queued']];
// The needsYou group keeps its key and hash value; its label is "Needs action", so it does not read
// like the "Waiting on you" queue, which also counts reviews.
const groupLabel = group => tr(group === 'needsYou' ? 'needsAction' : group);
// One glyph per state family, so a status reads without color.
const GLYPHS = {needs: '!', review_wait: '◐', running: '▶', queued: '○', done: '✓', failed: '✕', blocked: '✕', paused: '‖', cancelled: '–'};
const SUMMARY = [['needsYou', 'needs'], ['awaitingReview', 'review_wait'], ['running', 'running'], ['done24h', 'done']];
let queueExpanded = false, homeProjectsExpanded = false;
let attentionShown = [], lastOpened = '', lastNeeds = null;
const expanded = new Set();
let language = navigator.language.toLowerCase().startsWith('vi') ? 'vi' : 'en';
try { const saved = localStorage.getItem('dashboard-language'); if (['vi', 'en'].includes(saved)) language = saved; } catch {}
let state = {view: 'overview', p: '', w: '', s: 'open', q: '', t: '', g: '', lang: language};
let overview = null, timer = null, inFlight = false, disconnected = false, tokenRequired = false;
let detailVersion = 0, taskData = null, detailError = '', returnFocus = null;
let attemptLog = null, lastOk = null, technicalOpen = '';
const FLOW = ['BACKLOG', 'READY', 'RUNNING', 'VERIFYING', 'REVIEW', 'APPROVED', 'DONE'];
const THEMES = ['system', 'light', 'dark'];
let theme = 'system';
try { const saved = localStorage.getItem('dashboard-theme'); if (THEMES.includes(saved)) theme = saved; } catch {}
function applyTheme() {
  if (theme === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
}
applyTheme();
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
  row.replaceChildren(...keys.map(key => { const cell = el('th', tr(key), `col-${key}`); attr(cell, 'scope', 'col'); return cell; }));
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
  const reason = task.exception === 'paused' ? task.reason || pauseReason(task.failure) : '';
  return task.exception === 'needs' ? `${tr(task.state)} · ${tr('needs')}`
    : `${tr(task.exception || task.state)}${reason ? ` · ${tr(reason)}` : ''}`;
}
function statusFamily(task) {
  if (task.exception) return task.exception;
  if (matches(task, 'running')) return 'running';
  return {DONE: 'done', CANCELLED: 'cancelled'}[task.state] || 'queued';
}
// A status badge: an aria-hidden glyph whose shape tells the state family, then the text label.
function badge(family, label) {
  const node = el('span', undefined, `badge status-${family}`), glyph = el('span', GLYPHS[family] || GLYPHS.queued, 'glyph');
  attr(glyph, 'aria-hidden', 'true'); node.append(glyph, el('span', label));
  return node;
}
function statusBadge(task, label = statusLabel(task)) { return badge(statusFamily(task), label); }
function boardOrder(a, b) {
  const rank = task => attentionRank.get(`${task.pid}:${task.task_id}`) ?? Infinity;
  return (rank(a) - rank(b)) || (b.updated_at || 0) - (a.updated_at || 0) ||
    (a.task_id < b.task_id ? -1 : a.task_id > b.task_id ? 1 : a.pid < b.pid ? -1 : a.pid > b.pid ? 1 : 0);
}
let attentionRank = new Map(), runningByKey = new Map();
function runningInfo(task) {
  const run = runningByKey.get(`${task.pid}:${task.task_id}`);
  if (!run || run.started_at == null) return null;
  const elapsed = Math.max(0, Math.floor(Date.now() / 1000 - run.started_at));
  return {elapsed, timeout: run.timeout_s || 0, quota: run.quota_left == null ? null : Math.round(run.quota_left)};
}
function projectName(pid) {
  return projectGroups().find(g => g.members.some(m => m.pid === pid))?.project.name || tr('unknown');
}
function projectTag(node, pid, drawer = false) {
  if (projectGroups().length > 1 && (drawer || !state.p && !state.w))
    node.append(el('span', projectName(pid), 'project-tag'));
}
function homeToggle(id, count, limit, expanded, key, toggle) {
  const node = $(id);
  node.hidden = count <= limit;
  text(node, tr(expanded ? 'showLess' : key, {n: count}));
  attr(node, 'aria-expanded', expanded);
  node.onclick = toggle;
}
function renderCard(node, task) {
  attr(node, 'role', 'listitem');
  const card = button(undefined, () => openItem(task.pid, task.task_id), 'card');
  const top = el('span', undefined, 'card-top'); top.append(statusBadge(task), el('code', task.task_id)); projectTag(top, task.pid);
  const objective = el('span', task.objective || '', 'card-objective'); attr(objective, 'title', task.objective || '');
  const meta = el('span', `${task.agent ?? task.account ?? tr('dash')} · ${relative(task.updated_at)}`, 'card-meta muted');
  if (task.updated_at != null) attr(meta, 'title', new Date(task.updated_at * 1000).toLocaleString(state.lang));
  card.append(top, objective, meta);
  const run = matches(task, 'running') ? runningInfo(task) : null;
  if (run) {
    const line = el('span', undefined, 'card-progress');
    let label = tr('elapsedOnly', {elapsed: duration(run.elapsed)});
    if (run.timeout) {
      const bar = el('progress'); attr(bar, 'max', run.timeout); attr(bar, 'value', Math.min(run.elapsed, run.timeout));
      attr(bar, 'aria-label', tr('timeUsed')); line.append(bar);
      label = tr('elapsedOf', {elapsed: duration(run.elapsed), limit: duration(run.timeout)});
    }
    if (run.quota != null) label += ` · ${tr('quotaLeft', {n: run.quota})}`;
    line.append(el('span', label)); card.append(line);
  }
  node.append(card);
}
function cardSignature(task) { return [task, state.p, state.w, projectName(task.pid), projectGroups().length, relative(task.updated_at), matches(task, 'running') ? runningInfo(task) : null]; }
function pauseReason(failure) {
  const prefix = (failure || '').split(':')[0];
  return ['QUOTA', 'AUTH', 'NETWORK', 'INTERRUPTED', 'MISSING_BINARY'].includes(prefix) ? prefix : 'OTHER';
}
function taskLocation(task, full = false) {
  const group = projectGroups().find(g => g.members.some(p => p.pid === task.pid));
  const checkout = group?.members.find(p => p.pid === task.pid);
  const worktree = checkout ? checkout.pid === group.project.pid ? 'main' : checkout.name : tr('unknown');
  return {label: group && (full || !state.p && state.g !== '1') ? `${group.project.name} › ${worktree}` : worktree,
    branch: checkout?.branch || tr('dash')};
}
// Summary counters, the ranked attention queue and the Needs-you announcement.
function renderMonitor(scoped, tasks) {
  attentionRank = new Map((overview?.attention || []).map((a, i) => [`${a.pid}:${a.task_id}`, i]));
  runningByKey = new Map((overview?.running || []).map(r => [`${r.pid}:${r.task_id}`, r]));
  // The server already ranked the list; keep its order exactly.
  const attention = (overview?.attention || []).filter(a => scoped(a.pid));
  attentionShown = queueExpanded ? attention : attention.slice(0, 6);
  homeToggle('attention-toggle', attention.length, 6, queueExpanded, 'showQueue', () => { queueExpanded = !queueExpanded; renderOverview(); });
  const now = Date.now() / 1000, everywhere = !state.p && !state.w;
  const counts = {
    needsYou: attention.filter(a => NEEDS_KINDS.includes(a.kind)).length,
    awaitingReview: attention.filter(a => a.kind === 'review_wait').length,
    running: (overview?.running || []).filter(r => scoped(r.pid)).length,
    done24h: everywhere ? overview?.activity?.done_24h ?? 0
      : tasks.filter(t => scoped(t.pid) && t.state === 'DONE' && now - (t.updated_at || 0) <= 86400).length
  };
  $('summary').hidden = !overview; attr($('summary'), 'aria-label', tr('summary')); attr($('summary'), 'role', 'group');
  patch($('summary'), SUMMARY, ([key]) => key, ([key]) => [counts[key]], (node, [key, family]) => {
    attr(node, 'class', `counter counter-${family}${counts[key] === 0 ? ' counter-zero' : ''}`);
    const glyph = el('span', GLYPHS[family], 'counter-glyph'); attr(glyph, 'aria-hidden', 'true');
    const parts = [glyph, el('span', counts[key], 'counter-value'), el('span', groupLabel(key), 'counter-label')];
    if (key === 'done24h') { const box = el('div', undefined, 'counter-static'); box.append(...parts); node.append(box); return; }
    const action = button(undefined, () => setState({view: 'tasks', s: key})); action.append(...parts); node.append(action);
  });
  $('attention').hidden = !attention.length;
  text($('attention-title'), tr('queueTitle', {n: attention.length})); text($('attention-hint'), tr('queueHint'));
  patch($('attention-list'), attentionShown, a => `${a.pid}:${a.task_id}`, a => [a, relative(a.since), state.p, state.w, projectName(a.pid), projectGroups().length], (node, item) => {
    attr(node, 'role', 'listitem'); attr(node, 'class', 'attention-item');
    node.onclick = () => openItem(item.pid, item.task_id);
    const open = el('button', undefined, 'attention-open'); open.type = 'button';
    const head = el('span', undefined, 'attention-head');
    head.append(badge(item.kind, tr(item.kind)), el('code', item.task_id)); projectTag(head, item.pid);
    if (item.reason) head.append(el('span', tr(item.reason), 'muted'));
    const since = el('span', relative(item.since), 'muted');
    if (item.since != null) attr(since, 'title', new Date(item.since * 1000).toLocaleString(state.lang));
    head.append(since);
    const objective = el('span', (item.objective || '').split('\n')[0], 'attention-objective'); attr(objective, 'title', item.objective || '');
    open.append(head, objective); node.append(open);
    if (item.command) {
      const line = el('div', undefined, 'attention-command');
      const copy = button('⧉', event => { event.stopPropagation(); copyCommand(item.command, event.currentTarget); }, 'copy-icon');
      attr(copy, 'aria-label', tr('copy')); attr(copy, 'title', tr('copy'));
      line.append(el('code', item.command), copy); node.append(line);
    }
  });
  // Announce only a change in the Needs-you count, never the first load or a filter switch.
  if (overview) {
    const key = `${state.p}|${state.w}`, n = counts.needsYou;
    if (lastNeeds && lastNeeds.key === key && lastNeeds.n !== n)
      text($('announcer'), tr(n === 0 ? 'announceNone' : n === 1 ? 'announceOne' : 'announce', {n}));
    lastNeeds = {key, n};
  }
}
function renderBoard(filtered) {
  for (const [group, label] of LANES) {
    const items = filtered.filter(t => matches(t, group)).sort(boardOrder);
    text($(`lane-${group}-title`), `${tr(label)} (${items.length})`);
    patch($(`lane-${group}`), items, t => `${t.pid}:${t.task_id}`, cardSignature, renderCard);
    const empty = $(`lane-${group}-empty`); text(empty, tr('nothingHere')); empty.hidden = !overview || items.length > 0;
  }
  const finished = filtered.filter(t => matches(t, 'done') || matches(t, 'cancelled'))
    .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0) || boardOrder(a, b));
  text($('finished-summary'), tr('finished', {n: finished.length}));
  patch($('finished-cards'), finished.slice(0, 20), t => `${t.pid}:${t.task_id}`, cardSignature, renderCard);
}
const clampPct = value => Math.max(0, Math.min(100, Number(value) || 0));
// A reset more than a week away reads better as a date than as a day count.
function resetText(resetsAt, now = Date.now()) {
  const left = resetsAt - now / 1000;
  if (left <= 0) return tr('resetsNow');
  if (left <= 7 * 86400) return tr('resetsIn', {time: duration(left)});
  const when = new Date(resetsAt * 1000), options = {month: 'short', day: 'numeric'};
  if (when.getFullYear() !== new Date(now).getFullYear()) options.year = 'numeric';
  return tr('resetsOn', {date: when.toLocaleDateString(state.lang, options)});
}
const resetTitle = resetsAt => new Date(resetsAt * 1000).toLocaleString(state.lang);
// The most used window of an account, or null.
const tightestWindow = account => (account.windows || []).reduce((a, b) => (a && a.used_pct >= b.used_pct ? a : b), null);
function accountBadge(account) {
  const [family, label] = ACCOUNT_STATES[account.state] || ACCOUNT_STATES.available;
  return badge(family, tr(label));
}
function windowLabel(win) {
  const minutes = Number(win.window_minutes);
  if (minutes === 300) return tr('window5h');
  if (minutes === 10080) return tr('windowWeek');
  return minutes > 0 ? tr('windowMinutes', {n: Math.round(minutes)}) : tr('windowUsage');
}
// The lowest quota left on an account and when its most used window comes back.
function quotaSummary(account) {
  const windows = account.windows || [];
  if (!windows.length && account.remaining_pct == null) return tr('noQuotaShort');
  const tightest = tightestWindow(account);
  const left = account.remaining_pct ?? 100 - tightest.used_pct;
  const parts = [tr('pctLeft', {n: Math.round(clampPct(left))})];
  if (tightest?.resets_at != null) parts.push(resetText(tightest.resets_at));
  return parts.join(' · ');
}
// One sentence for the quota-fallback policy of these projects; '' when no config could be read.
function fallbackLine(groups) {
  const policies = groups.map(g => g.project.fallback).filter(Boolean), on = policies.filter(p => p.enabled).length;
  if (!policies.length) return '';
  if (on === policies.length) return tr('fallbackOnLine');
  return on ? tr('fallbackMixed', {a: on, b: policies.length}) : tr('fallbackOffLine');
}
// A command the reader copies into a terminal; the dashboard never runs it.
function commandLine(node, command, lead) {
  const signature = JSON.stringify([state.lang, command, lead]);
  if (node._signature === signature) return;
  node._signature = signature;
  const line = el('div', undefined, 'attention-command command-line');
  const copy = button('⧉', event => copyCommand(command, event.currentTarget), 'copy-icon');
  attr(copy, 'aria-label', tr('copy')); attr(copy, 'title', tr('copy'));
  line.append(el('code', command), copy);
  node.replaceChildren(...(lead ? [el('p', lead)] : []), line);
}
// Overview: placeholders before the first answer, the work in progress, and on Tasks and Board
// the one-line link back to the queue.
function renderHome(scoped, tasks) {
  const loaded = !!overview;
  $('overview-loading').hidden = loaded || disconnected || tokenRequired; text($('overview-loading-text'), tr('loading'));
  $('inflight-section').hidden = !loaded; text($('inflight-title'), tr('inProgress'));
  const running = tasks.filter(t => scoped(t.pid) && matches(t, 'running')).sort(boardOrder);
  patch($('inflight'), running, t => `${t.pid}:${t.task_id}`, cardSignature, renderCard);
  text($('inflight-empty'), tr('nothingRunning')); $('inflight-empty').hidden = !loaded || running.length > 0;
  const waiting = (overview?.attention || []).filter(a => scoped(a.pid)).length;
  $('queue-link').hidden = !waiting || !['tasks', 'board'].includes(state.view);
  text($('queue-link-text'), tr('waitingLine', {n: waiting})); text($('queue-link-open'), tr('openOverview'));
}
// Overview: one compact row per account and the fallback policy of the shown projects.
function renderCapacity(scoped, groups) {
  const loaded = !!overview, filtered = !!(state.p || state.w);
  const accounts = (overview?.accounts || []).filter(a => !filtered || (a.projects || []).some(scoped));
  $('capacity-section').hidden = !loaded; text($('capacity-title'), tr('accounts'));
  const line = fallbackLine(groups.filter(g => g.members.some(m => scoped(m.pid))));
  text($('capacity-fallback'), line); $('capacity-fallback').hidden = !line;
  patch($('capacity-summary'), accounts.slice(0, CAPACITY_ROWS), a => a.name, a => [a, quotaSummary(a)], (node, account) => {
    attr(node, 'role', 'listitem'); attr(node, 'class', 'capacity-row');
    const who = el('span', undefined, 'capacity-name'); who.append(el('strong', account.name));
    if (account.provider) who.append(el('span', account.provider, 'muted'));
    const quota = el('span', quotaSummary(account), 'capacity-quota'), tightest = tightestWindow(account);
    if (tightest?.resets_at != null) attr(quota, 'title', resetTitle(tightest.resets_at));
    node.append(who, accountBadge(account),
      el('span', tr('slotsShort', {running: account.running, max: account.max_concurrency}), 'capacity-slots'), quota);
  });
  text($('capacity-empty'), tr('noAccounts')); $('capacity-empty').hidden = !loaded || accounts.length > 0;
  text($('capacity-all'), tr('showAllAccounts')); $('capacity-all').hidden = accounts.length <= CAPACITY_ROWS;
}
// A labelled usage bar: the number is always in the text, so the color is never the only signal.
function quotaMeter(win) {
  const raw = clampPct(win.used_pct), used = Math.round(raw), label = windowLabel(win);
  const box = el('div', undefined, 'quota-window');
  const meter = el('div', undefined, raw >= 100 ? 'meter meter-full' : raw >= 80 ? 'meter meter-high' : 'meter');
  attr(meter, 'role', 'meter'); attr(meter, 'aria-valuemin', 0); attr(meter, 'aria-valuemax', 100);
  attr(meter, 'aria-valuenow', used); attr(meter, 'aria-label', tr('meterLabel', {label, n: used}));
  const fill = el('div', undefined, 'meter-fill'); fill.style.width = `${raw}%`; meter.append(fill);
  const parts = [tr('pctUsed', {n: used})];
  if (win.resets_at != null) parts.push(resetText(win.resets_at));
  else if (used === 0) parts.push(tr('resetDone'));
  const caption = el('p', parts.join(' · '), 'quota-text muted');
  if (win.resets_at != null) attr(caption, 'title', resetTitle(win.resets_at));
  box.append(el('div', label, 'quota-label'), meter, caption);
  return box;
}
function renderAccountCard(node, account) {
  attr(node, 'role', 'listitem'); attr(node, 'class', 'panel account-card');
  const head = el('div', undefined, 'account-head'), title = el('div', undefined, 'account-title');
  title.append(el('h3', account.name, 'account-name'));
  if (account.provider) title.append(el('span', account.provider, 'muted'));
  head.append(title, accountBadge(account)); node.append(head);
  if (account.lifted) node.append(el('p', tr(account.lifted === 'reset' ? 'liftedReset' : 'liftedCooldown'), 'muted'));
  if (['quota', 'auth'].includes(account.state)) {
    if (account.error) {
      const error = el('p', account.error, 'account-error muted'); attr(error, 'title', account.error); node.append(error);
    }
    const since = el('p', tr('sinceTime', {time: relative(account.since)}), 'muted');
    if (account.since != null) attr(since, 'title', new Date(account.since * 1000).toLocaleString(state.lang));
    node.append(since);
  }
  const windows = account.windows || [];
  if (!windows.length) node.append(el('p', tr('noQuotaLong'), 'muted'));
  for (const win of windows) node.append(quotaMeter(win));
  if (account.observed_at != null) {
    const reported = el('p', tr('reportedAt', {time: relative(account.observed_at)}), 'muted');
    attr(reported, 'title', new Date(account.observed_at * 1000).toLocaleString(state.lang)); node.append(reported);
  }
  node.append(el('p', tr('slotsBusy', {running: account.running, max: account.max_concurrency}), 'account-slots'));
  if (account.agents?.length) {
    const chips = el('div', undefined, 'agent-chips');
    for (const agent of account.agents) {
      const label = [agent.agent, (agent.roles || []).join(', ')].filter(Boolean).join(' · ');
      chips.append(el('span', agent.enabled === false ? `${label} ${tr('agentOff')}` : label, 'chip'));
    }
    node.append(chips);
  }
}
// Accounts view: fallback policy per project, one card per account, recent automatic switches.
function renderAccounts(groups) {
  const loaded = !!overview, accounts = overview?.accounts || [], switches = overview?.switches || [];
  text($('accounts-title'), tr('accounts'));
  $('accounts-loading').hidden = loaded || disconnected || tokenRequired; text($('accounts-loading-text'), tr('loading'));
  $('accounts-body').hidden = !loaded;
  if (!loaded || state.view !== 'accounts') return;
  groups = groups.filter(g => g.members.some(m => (!state.p || g.project.pid === state.p) && (!state.w || m.pid === state.w)));
  const multi = groups.length > 1;
  if (multi) {
    patch($('fallback-panels'), [groups], () => 'shared', item => item, (node, items) => {
      attr(node, 'class', 'panel fallback-panel');
      node.append(el('h2', tr('fallbackTitle'), 'section-title'), el('p', fallbackLine(items)));
      const list = el('ul', undefined, 'fallback-facts');
      for (const group of items) {
        const row = el('li'), policy = group.project.fallback;
        row.append(el('span', group.project.name));
        if (policy) {
          row.append(badge(policy.enabled ? 'done' : 'paused', tr(policy.enabled ? 'on' : 'off')));
          const pct = Math.round(Number(policy.min_remaining_pct) || 0);
          const candidates = policy.agents == null ? tr('fallbackRoleOrder') : policy.agents.length ? policy.agents.join(' → ') : tr('none');
          row.append(el('p', `${pct > 0 ? tr('fallbackThreshold', {n: pct}) : tr('fallbackAtZero')} · ${tr('fallbackCandidates', {list: candidates})}`, 'muted'));
        } else row.append(el('p', tr('unreadable'), 'muted'));
        list.append(row);
      }
      node.append(list, el('p', tr('fallbackPolicy'), 'muted'));
    });
  } else {
    patch($('fallback-panels'), groups, g => g.project.pid, g => [g.project.name, g.project.fallback, multi], (node, group) => {
      const policy = group.project.fallback;
      attr(node, 'class', 'panel fallback-panel');
      const head = el('div', undefined, 'panel-head');
      head.append(el('h2', multi ? `${tr('fallbackTitle')} · ${group.project.name}` : tr('fallbackTitle'), 'section-title'));
      if (policy) head.append(badge(policy.enabled ? 'done' : 'paused', tr(policy.enabled ? 'on' : 'off')));
      node.append(head);
      if (!policy) { node.append(el('p', tr('unreadable'), 'muted')); return; }
      const pct = Math.round(Number(policy.min_remaining_pct) || 0);
      const candidates = policy.agents == null ? tr('fallbackRoleOrder') : policy.agents.length ? policy.agents.join(' → ') : tr('none');
      const facts = el('ul', undefined, 'fallback-facts');
      facts.append(el('li', pct > 0 ? tr('fallbackThreshold', {n: pct}) : tr('fallbackAtZero')),
        el('li', tr('fallbackCandidates', {list: candidates})));
      node.append(el('p', tr(policy.enabled ? 'fallbackOnLine' : 'fallbackOffLine')), facts, el('p', tr('fallbackPolicy'), 'muted'));
    });
  }
  patch($('account-cards'), accounts, a => a.name, a => [a, Math.floor(Date.now() / 1000)], renderAccountCard);
  $('account-cards').hidden = !accounts.length; $('accounts-empty').hidden = accounts.length > 0;
  text($('accounts-empty-text'), tr('noAccounts'));
  commandLine($('accounts-empty-command'), 'agent-bridge config-init orchestrator');
  text($('switches-title'), tr('recentSwitches'));
  patch($('switches'), switches, s => `${s.pid}:${s.task_id}:${s.ts}`, s => [s, relative(s.ts), state.p, state.w, projectName(s.pid), projectGroups().length], (node, item) => {
    attr(node, 'role', 'listitem'); attr(node, 'class', 'switch-item');
    const who = (agent, account) => agent == null ? tr('unknown') : account ? `${agent} (${account})` : agent;
    const open = button(undefined, () => openItem(item.pid, item.task_id), 'switch-open');
    const when = el('span', relative(item.ts), 'muted');
    if (item.ts != null) attr(when, 'title', new Date(item.ts * 1000).toLocaleString(state.lang));
    const values = {task: item.task_id, from: who(item.from, item.from_account), to: who(item.to, item.to_account)};
    if (projectGroups().length > 1 && !state.p && !state.w) {
      open.append(el('code', item.task_id)); projectTag(open, item.pid);
      open.append(el('span', tr('switchRoute', values), 'switch-text'));
    } else open.append(el('span', tr('switchMoved', values), 'switch-text'));
    open.append(el('span', tr(`reason_${item.reason}`), 'chip'), when);
    node.append(open);
  });
  text($('switches-empty'), tr('noSwitches')); $('switches-empty').hidden = switches.length > 0;
  commandLine($('accounts-hint'), 'agent-bridge account-list', tr('accountsHint'));
}
function renderOverview() {
  document.documentElement.lang = state.lang; text($('brand-text'), tr('brand'));
  text($('live'), tr(tokenRequired ? 'tokenRequired' : disconnected ? 'offline' : overview ? 'live' : 'loading'));
  attr($('live'), 'class', disconnected ? 'live offline' : 'live'); $('content').classList.toggle('disconnected', disconnected);
  text($('footer'), tr('viewOnly')); attr($('languages'), 'aria-label', tr('language')); attr($('nav'), 'aria-label', tr('mainNav'));
  text($('theme-label'), tr('theme'));
  selectOptions($('theme'), [['system', tr('themeSystem')], ['light', tr('themeLight')], ['dark', tr('themeDark')]], theme);
  renderSettings();
  attr($('filters'), 'aria-label', tr('filters'));
  const banner = $('offline-banner');
  banner.hidden = !disconnected || tokenRequired;
  text(banner, lastOk ? tr('staleData', {time: lastOk.toLocaleTimeString(state.lang)}) : tr('noData'));
  patch($('languages'), ['en', 'vi'], x => x, x => [x, state.lang], (node, lang) => {
    node.replaceChildren(button(tr(lang), () => setState({lang}))); attr(node.firstChild, 'aria-pressed', lang === state.lang);
  });
  const groups = projectGroups(), tasks = overview?.tasks || [];
  const openCount = tasks.filter(t => matches(t, 'open')).length;
  const troubled = (overview?.accounts || []).filter(a => a.state !== 'available').length;
  const counts = {tasks: overview ? openCount : 0, accounts: troubled};
  patch($('nav'), VIEWS, x => x, x => [state.view, counts[x] || 0, !!overview], (node, view) => {
    const tab = button(undefined, () => { closeSidebar(true); setState({view}); }, 'nav-item');
    tab.append(el('span', viewLabel(view), 'nav-label'));
    if (counts[view]) tab.append(el('span', counts[view], 'nav-count'));
    if (state.view === view) attr(tab, 'aria-current', 'page');
    node.replaceChildren(tab);
  });
  const selected = groups.find(g => g.project.pid === state.p);
  const title = `${viewLabel(state.view)}${selected ? ` · ${selected.project.name}` : ''}`;
  text($('page-title'), title);
  document.title = `${viewLabel(state.view)} · ${tr('brand')}`;
  renderProjectNav(groups);
  attr($('menu-button'), 'aria-label', tr('menu'));
  syncSidebar();
  for (const view of VIEWS) { const node = $(`${view}-view`); if (node) node.hidden = state.view !== view; }
  $('task-tools').hidden = !['tasks', 'board'].includes(state.view);
  // The worktree filter only when the selected project has several checkouts (or the address already chose one).
  $('worktree').closest('label').hidden = !(selected?.members.length > 1 || !!state.w);
  $('group-worktree').hidden = state.view !== 'tasks' || !(state.g === '1' || groups.some(g => g.members.length > 1));
  text($('search-label'), tr('search')); text($('worktree-label'), tr('worktree'));
  attr($('search'), 'placeholder', tr('searchPlaceholder'));
  text($('group-worktree'), tr('groupWorktree')); attr($('group-worktree'), 'aria-pressed', state.g === '1');
  if ($('search').value !== state.q) $('search').value = state.q;
  selectOptions($('worktree'), [['', tr('allWorktrees')], ...(selected?.members || []).map((p, i) => [p.pid, i === 0 ? `${p.name} (${tr('main')})` : `${p.name} · ${p.branch || tr('dash')}`])], state.w);
  $('worktree').disabled = !selected; text($('clear'), tr('clear'));
  $('clear').hidden = !state.p && !state.w && !state.q && state.s === 'open';
  const ids = selected ? new Set(selected.members.map(p => p.pid)) : null;
  const scoped = pid => (!state.p || !!ids?.has(pid)) && (!state.w || pid === state.w);
  const filtered = tasks.filter(t => scoped(t.pid) &&
    `${t.task_id} ${t.workflow_id || ''} ${t.objective}`.toLocaleLowerCase().includes(state.q.toLocaleLowerCase()));
  renderMonitor(scoped, tasks);
  renderHome(scoped, tasks);
  renderCapacity(scoped, groups);
  renderAccounts(groups);
  if (state.view === 'board') renderBoard(filtered);
  patch($('statuses'), GROUPS, x => x, x => [state.s, filtered.filter(t => matches(t, x)).length], (node, group) => {
    const chip = button(`${groupLabel(group)} (${filtered.filter(t => matches(t, group)).length})`, () => setState({s: group}));
    attr(chip, 'aria-pressed', state.s === group); node.replaceChildren(chip);
  });
  const shown = filtered.filter(t => matches(t, state.s)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
  text($('task-count'), overview ? tr(shown.length === 1 ? 'taskCountOne' : 'taskCount', {n: shown.length}) : '');
  // Columns that would repeat one value on every row are left out; header and cells share this list.
  const checkouts = new Set(shown.map(t => t.pid));
  const agents = new Set(shown.map(t => t.agent ?? t.account).filter(name => name != null && name !== ''));
  const columns = ['status', 'task', ...(checkouts.size > 1 || state.g === '1' ? ['location'] : []),
    ...(agents.size > 1 ? ['agent'] : []), 'updated', 'nextStep'];
  headers($('columns'), columns);
  let rows = shown;
  if (state.g === '1') {
    const worktrees = new Map();
    for (const task of shown) {
      if (!worktrees.has(task.pid)) worktrees.set(task.pid, []);
      worktrees.get(task.pid).push(task);
    }
    rows = Array.from(worktrees, ([pid, tasks]) => [{pid, groupHeader: true, count: tasks.length}, ...tasks]).flat();
  }
  patch($('task-rows'), rows, t => t.groupHeader ? `group:${t.pid}` : `${t.pid}:${t.task_id}`, t => [t, overview?.projects, state.p, state.g, columns, relative(t.updated_at), Date.now() / 1000 - t.updated_at > 7 * 86400], (row, task) => {
    if (task.groupHeader) {
      const location = taskLocation(task, true), cell = el('th', `${location.label} · ${location.branch} (${task.count})`);
      attr(cell, 'colspan', columns.length); attr(cell, 'scope', 'rowgroup'); attr(row, 'class', 'worktree-header'); row.append(cell); return;
    }
    activate(row, () => openItem(task.pid, task.task_id));
    const status = el('td', undefined, 'cell-status'); status.append(statusBadge(task));
    const name = el('td', undefined, 'task-cell'), line = el('div', undefined, 'task-line');
    const objective = el('span', (task.objective || '').split('\n')[0], 'objective');
    attr(objective, 'title', task.objective || ''); line.append(el('code', task.task_id));
    if (task.workflow_id) line.append(el('small', task.workflow_id, 'workflow-id'));
    line.append(objective); name.append(line);
    const next = el('td', undefined, 'next-step');
    // No command: the cell stays empty (the phone layout then drops it).
    if (task.command) {
      const copy = button('⧉', event => { event.stopPropagation(); copyCommand(task.command, event.currentTarget); }, 'copy-icon');
      attr(copy, 'aria-label', tr('copy')); attr(copy, 'title', task.command);
      next.append(el('span', tr(task.command.split(' ')[1]), 'next-label'), copy);
    }
    const updated = timeCell(task.updated_at), age = Date.now() / 1000 - task.updated_at;
    attr(updated, 'class', 'cell-updated');
    if (matches(task, 'open') && task.updated_at != null && age > 7 * 86400) {
      const stale = el('span', tr('stale'), 'muted stale'); attr(stale, 'title', tr('staleTitle', {n: Math.floor(age / 86400)})); updated.append(' ', stale);
    }
    const make = {
      status: () => status, task: () => name, updated: () => updated, nextStep: () => next,
      location: () => {
        const location = taskLocation(task), place = el('td', location.label, 'cell-location');
        attr(place, 'title', `${location.label} · ${location.branch}`); return place;
      },
      agent: () => {
        const who = task.agent ?? task.account ?? tr('dash'), cell = el('td', who, 'cell-agent');
        attr(cell, 'title', who); return cell;
      }
    };
    const cells = columns.map(key => { const cell = make[key](); attr(cell, 'data-label', tr(key)); return cell; });
    row.replaceChildren(...cells);
  });
  // Before the first answer there is nothing to judge: show placeholders, never "no matches".
  const loaded = !!overview, none = loaded && tasks.length === 0;
  $('tasks-loading').hidden = loaded || disconnected || tokenRequired; text($('loading-text'), tr('loading'));
  $('tasks-table').hidden = !loaded;
  $('tasks-empty').hidden = !loaded || shown.length > 0;
  text($('empty-message'), tr(none ? 'noTasksYet' : 'noMatches')); text($('empty-clear'), tr('clear'));
  $('empty-clear').hidden = none;
  renderProjects(groups, tasks);
  // An old `view=projects` link lands on the overview at the projects section, once the data is laid out.
  if (scrollToProjects && overview && state.view === 'overview') {
    scrollToProjects = false; $('projects-view').scrollIntoView({block: 'start'});
  }
}
// Projects that need a person first, then the most recently updated.
function sortedGroups(groups) {
  const latest = group => Math.max(0, ...group.members.map(p => p.updated_at || 0));
  return [...groups].sort((a, b) => totals(b.members).needs - totals(a.members).needs || latest(b) - latest(a));
}
// Sidebar project list: All projects, then the first few groups; a filter box once the list is long.
function renderProjectNav(groups) {
  const all = sortedGroups(groups), many = all.length > PROJECT_ROWS;
  if (!many) projectFilter = '';
  $('project-filter-box').hidden = !many;
  text($('projects-heading'), tr('projects')); text($('project-filter-label'), tr('filterProjects'));
  attr($('project-filter'), 'placeholder', tr('filterProjectsPlaceholder'));
  if ($('project-filter').value !== projectFilter) $('project-filter').value = projectFilter;
  const needle = projectFilter.trim().toLocaleLowerCase();
  const found = needle ? all.filter(g => g.project.name.toLocaleLowerCase().includes(needle)) : all;
  let shown = projectsExpanded || needle ? found : found.slice(0, PROJECT_ROWS);
  const chosen = all.find(g => g.project.pid === state.p);
  if (chosen && !shown.includes(chosen) && !needle) shown = [...shown, chosen];
  const items = [{key: 'all'}, ...shown.map(group => ({key: `p:${group.project.pid}`, group, needs: totals(group.members).needs}))];
  patch($('project-nav'), items, item => item.key, item => [item.key, item.group?.project.name, item.needs, state.p],
    (node, item) => {
      const pid = item.group ? item.group.project.pid : '';
      const name = item.group ? item.group.project.name : tr('allProjects');
      const choose = button(undefined, () => { closeSidebar(true); setState({p: pid, w: ''}); }, 'nav-item project-item');
      const label = el('span', name, 'nav-label'); attr(label, 'title', name); choose.append(label);
      if (item.needs > 0) {
        const count = el('span', item.needs, 'nav-count'); attr(count, 'aria-hidden', 'true');
        choose.append(count, el('span', tr('needYouCount', {n: item.needs}), 'sr-only'));
      }
      if (state.p === pid) attr(choose, 'aria-current', 'page');
      node.append(choose);
    });
  const more = $('project-more');
  more.hidden = !!needle || found.length <= PROJECT_ROWS;
  text(more, projectsExpanded ? tr('showFewerProjects') : tr('showAllProjects', {n: all.length}));
  attr(more, 'aria-expanded', projectsExpanded);
  text($('project-none'), tr('noProjectMatch')); $('project-none').hidden = !needle || found.length > 0;
}
function renderSettings() {
  text($('settings-title'), tr('settings')); text($('appearance-title'), tr('appearance')); text($('language-title'), tr('language'));
  text($('about-title'), tr('about')); text($('about-name'), tr('brand')); text($('about-cli'), tr('aboutCli'));
  text($('about-data'), tr('aboutData')); text($('about-refresh'), tr('aboutRefresh')); text($('shortcuts-title'), tr('shortcuts'));
  text($('shortcut-queue'), tr('shortcutQueue')); text($('shortcut-esc'), tr('shortcutEsc')); text($('shortcut-search'), tr('shortcutSearch'));
}
// The sidebar is a column on wide screens and an off-canvas panel below 1024 px.
function syncSidebar() {
  const open = sidebarOpen && narrow.matches;
  attr($('menu-button'), 'aria-expanded', open); $('scrim').hidden = !open;
  $('app').classList.toggle('sidebar-open', open);
  const blocked = narrow.matches && !open || drawerOpen;
  if ($('sidebar').inert !== blocked) $('sidebar').inert = blocked;
}
function openSidebar() {
  sidebarOpen = true; syncSidebar();
  ($('nav').querySelector('[aria-current]') || $('nav').querySelector('button'))?.focus();
}
function closeSidebar(refocus) {
  if (!sidebarOpen) return;
  sidebarOpen = false; syncSidebar();
  if (refocus && narrow.matches) $('menu-button').focus();
}
// The overview's last section: one card per project with its worktrees.
function renderProjects(groups, tasks) {
  text($('projects-title'), tr('projects')); $('projects-view').hidden = !overview;
  groups = sortedGroups(groups);
  homeToggle('projects-toggle', groups.length, 8, homeProjectsExpanded, 'homeProjects', () => { homeProjectsExpanded = !homeProjectsExpanded; renderOverview(); });
  if (!homeProjectsExpanded) groups = groups.slice(0, 8);
  patch($('project-cards'), groups, g => g.project.pid, g => [g, tasks, expanded.has(g.project.pid), Math.floor(Date.now() / 1000)], (card, group) => {
    attr(card, 'class', 'project-card'); const project = group.project, count = totals(group.members);
    const head = el('div', undefined, 'card-header'), title = el('div', undefined, 'card-title');
    const root = el('div', project.root, 'muted root'); attr(root, 'title', project.root || '');
    title.append(el('h2', project.name), root, el('p', tr('projectCounts', count)));
    head.append(title, button(tr('viewTasks'), () => setState({view: 'tasks', p: project.pid, w: '', s: 'open', q: ''}))); card.append(head);
    if (project.root_missing) card.append(el('p', tr('rootMissing'), 'muted'));
    if (project.error) card.append(el('p', tr('unreadable'), 'muted'));
    const scroll = el('div', undefined, 'table-scroll'), table = el('table'), thead = el('thead'), columns = el('tr');
    headers(columns, ['worktree', 'branch', 'needsAction', 'open', 'done', 'updated']); thead.append(columns);
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
  const params = new URLSearchParams(location.hash.slice(1)), previous = state, view = params.get('view');
  // The projects view became the overview's last section; old links land there.
  if (view === 'projects') scrollToProjects = true;
  state = {view: VIEWS.includes(view) ? view : 'overview', p: params.get('p') || '', w: params.get('w') || '',
    s: GROUPS.includes(params.get('s')) ? params.get('s') : 'open', q: params.get('q') || '', t: params.get('t') || '', g: params.get('g') === '1' ? '1' : '',
    lang: ['vi', 'en'].includes(params.get('lang')) ? params.get('lang') : state.lang};
  try { localStorage.setItem('dashboard-language', state.lang); } catch {}
  if (state.t) lastOpened = state.t;
  if (view === 'projects') history.replaceState(null, '', `#${new URLSearchParams(state)}`);
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
    log.box.setAttribute('role', 'log');
    log.box.setAttribute('aria-live', 'off');
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
  const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store'});
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
function drawerHead(task) {
  const head = el('div', undefined, 'drawer-head');
  head.append(el('code', task.task_id)); projectTag(head, state.t.split(':')[0], true); head.append(statusBadge(task));
  if (task.risk_level) head.append(el('span', tr('risk', {level: tr(task.risk_level)}), 'chip'));
  const who = [task.agent, task.account].filter(Boolean).join(' · ');
  if (who) head.append(el('span', who, 'muted'));
  return head;
}
// Where the task sits in its normal path, or why it left that path.
function lifecycle(task) {
  const index = FLOW.indexOf(task.state);
  if (index < 0) {
    const box = el('div', undefined, 'state-box');
    const reason = task.state === 'PAUSED' ? tr(task.reason || pauseReason(task.failure)) : '';
    box.append(statusBadge(task, `${tr(task.state)}${reason ? ` · ${reason}` : ''}`));
    if (task.failure) box.append(el('h3', tr('failure')), el('pre', task.failure));
    return box;
  }
  const list = el('ol', undefined, 'stepper'); attr(list, 'aria-label', tr('lifecycle'));
  FLOW.forEach((step, i) => {
    const item = el('li', undefined, i < index ? 'step done' : i === index ? 'step current' : 'step');
    const mark = el('span', i < index ? '✓' : String(i + 1), 'step-mark'); attr(mark, 'aria-hidden', 'true');
    item.append(mark, el('span', tr(step), 'step-name'));
    if (i < index) item.append(el('span', ` (${tr('stepDone')})`, 'sr-only'));
    if (i === index) attr(item, 'aria-current', 'step');
    list.append(item);
  });
  if (!task.failure) return list;
  const box = el('div'); box.append(list, el('h3', tr('failure')), el('pre', task.failure));
  return box;
}
function section(key, className) {
  const node = el('section', undefined, className ? `drawer-section ${className}` : 'drawer-section');
  node.append(el('h3', tr(key))); return node;
}
function codeList(items) {
  const list = el('ul', undefined, 'plain-list');
  for (const item of items) { const li = el('li'); li.append(el('code', item)); list.append(li); }
  return list;
}
function changedFiles(items) {
  if (items.length <= 8) return codeList(items);
  const groups = new Map(), quiet = new Set(['.cache', '.codex-tmp', 'node_modules', '__pycache__', '.pytest_cache', '.venv', 'dist', 'build', '.next']);
  for (const path of items) {
    const segment = path.includes('/') ? path.split('/')[0] : '.';
    if (!groups.has(segment)) groups.set(segment, []);
    groups.get(segment).push(path);
  }
  const collapsed = ([name, paths]) => quiet.has(name) || paths.length > 20;
  const box = el('div', undefined, 'file-groups');
  for (const [name, paths] of [...groups].sort((a, b) => Number(collapsed(a)) - Number(collapsed(b)))) {
    const group = el('details');
    group.open = !collapsed([name, paths]);
    group.append(el('summary', tr(paths.length === 1 ? 'fileGroupOne' : 'fileGroup', {dir: name, n: paths.length})), codeList(paths));
    box.append(group);
  }
  return box;
}
// The agent's final text: readable fields when it is a JSON object, the raw text kept folded below.
function finalSummary(raw) {
  if (!raw) return [el('p', tr('none'), 'muted')];
  let data = null;
  try { data = JSON.parse(raw); } catch {}
  if (!data || typeof data !== 'object' || Array.isArray(data)) return [el('pre', raw)];
  const nodes = [], filled = value => value != null && String(value).trim() !== '';
  if (filled(data.summary)) nodes.push(el('p', data.summary, 'final-summary'));
  if (filled(data.status)) {
    const line = el('p', undefined, 'final-status');
    line.append(el('strong', `${tr('status')}: `), el('span', data.status)); nodes.push(line);
  }
  if (filled(data.notes)) {
    const notes = el('p', undefined, 'final-notes');
    notes.append(el('strong', `${tr('notes')}: `), el('span', data.notes)); nodes.push(notes);
  }
  for (const [field, label] of [['tests_run', 'testsRun'], ['files_changed', 'filesChanged']]) {
    const items = Array.isArray(data[field]) ? data[field].filter(filled) : [];
    if (!items.length) continue;
    const list = el('ul', undefined, 'final-list');
    for (const item of items) list.append(el('li', typeof item === 'object' ? JSON.stringify(item) : item));
    nodes.push(el('h4', tr(label), 'final-label'), list);
  }
  const details = el('details', undefined, 'final-raw');
  details.append(el('summary', tr('rawOutput')), el('pre', raw));
  nodes.push(details);
  return nodes;
}
function setInert(value) {
  for (const node of [$('content'), $('topbar'), $('footer')]) if (node.inert !== value) node.inert = value;
  drawerOpen = value; syncSidebar();
}
function workflowContext() {
  const task = overview?.tasks.find(t => `${t.pid}:${t.task_id}` === state.t);
  if (!task?.workflow_id) return null;
  return {
    task,
    request: overview.requests?.find(r => r.pid === task.pid && r.id === task.workflow_id),
    steps: overview.tasks.filter(t => t.pid === task.pid && t.workflow_id === task.workflow_id)
      .sort((a, b) => a.task_id.localeCompare(b.task_id)),
  };
}
function workflowSection(context) {
  const block = section('workflow', 'workflow-section');
  const {task, request, steps} = context;
  block.append(el('p', tr('workflowTitle', {id: task.workflow_id, objective: request?.name || tr('unknown')})));
  if (request) block.append(el('p', tr('workflowProgress', {done: request.done, total: request.total}), 'muted'));
  const list = el('ul', undefined, 'workflow-steps');
  for (const step of steps) {
    const item = el('li');
    const current = `${step.pid}:${step.task_id}` === state.t;
    const row = current ? el('div', undefined, 'workflow-step') :
      button(undefined, () => openItem(step.pid, step.task_id), 'workflow-step');
    if (current) attr(row, 'aria-current', 'true');
    row.append(statusBadge(step), el('code', step.task_id), el('span', step.objective));
    item.append(row); list.append(item);
  }
  block.append(list);
  return block;
}
function renderDetail() {
  const drawer = $('drawer'), wasHidden = drawer.hidden;
  drawer.hidden = !state.t;
  setInert(!drawer.hidden);
  text($('close'), tr('closeSymbol')); attr($('close'), 'aria-label', tr('close'));
  if (drawer.hidden) { stopLog(); if (!wasHidden && returnFocus?.isConnected) returnFocus.focus(); return; }
  if (attemptLog && attemptLog.task !== state.t) stopLog();
  if (wasHidden) { closeSidebar(false); $('close').focus(); }
  text($('drawer-title'), taskData?.objective || state.t.split(':').pop());
  const body = $('drawer-body');
  const context = workflowContext();
  const signature = JSON.stringify([state.lang, taskData, detailError, context]);
  if (body._signature === signature) return;
  body._signature = signature; body.replaceChildren();
  if (detailError) { stopLog(); body.append(el('p', tr(detailError)), button(tr('retry'), () => loadDetail())); return; }
  if (!taskData) { body.append(el('p', tr('loading'))); return; }
  const task = taskData;
  if (task.preview) {
    const details = el('dl');
    labeled(details, 'location', taskLocation(task, true).label);
    labeled(details, 'updated', task.updated_at == null ? null : new Date(task.updated_at * 1000).toLocaleString(state.lang));
    body.append(drawerHead(task), details, el('p', tr('loading'))); return;
  }
  // (a) who and what, (b) where it is, (c) what to do now.
  body.append(drawerHead(task), lifecycle(task));
  if (context) body.append(workflowSection(context));
  if (task.command) {
    const verb = task.command.split(' ')[1];
    const callout = section('nextStep', 'callout');
    const field = el('textarea', task.command, 'command'); field.readOnly = true; field.rows = 2; attr(field, 'aria-label', tr('command'));
    const copy = button(tr('copy'), () => copyCommand(task.command, copy, field));
    callout.append(el('p', tr({approve: 'nextApprove', resume: 'nextResume'}[verb] || 'nextResult')), field, copy);
    body.append(callout);
  }
  // (d) evidence: checks, scope, changes, summary, reviews.
  const checks = section('checks');
  if (!task.checks?.length) checks.append(el('p', tr('none'), 'muted'));
  else {
    const list = el('ul', undefined, 'check-list');
    for (const check of task.checks) {
      const result = check.passed === true ? 'pass' : check.passed === false ? 'fail' : 'unknown';
      const item = el('li', undefined, `check ${result}`);
      const mark = el('span', {pass: '✓', fail: '✗', unknown: '–'}[result], 'check-mark'); attr(mark, 'aria-hidden', 'true');
      item.append(mark, el('span', tr({pass: 'passed', fail: 'checkFailed', unknown: 'unknown'}[result]), 'check-state'),
        el('code', check.command || tr('dash')));
      if (result !== 'pass' && check.error) item.append(el('pre', check.error, 'check-error'));
      list.append(item);
    }
    checks.append(list);
  }
  const violations = task.scope_violations || [];
  const scope = section('scope_violations', violations.length ? 'alert' : '');
  scope.append(violations.length ? codeList(violations) : el('p', tr('none'), 'muted'));
  const files = section('files');
  files.append(task.files?.length ? changedFiles(task.files) : el('p', tr('none'), 'muted'));
  const summary = section('final');
  summary.append(...finalSummary(task.final));
  const reviews = section('reviews');
  if (!task.reviews?.length) reviews.append(el('p', tr('none'), 'muted'));
  else {
    const list = el('ul', undefined, 'plain-list');
    for (const review of task.reviews) {
      const item = el('li', undefined, 'review'), when = review.ts == null ? tr('unknown') : new Date(review.ts * 1000).toLocaleString(state.lang);
      item.append(el('strong', review.decision || tr('unknown')), el('span', ` · ${review.actor || tr('unknown')} · ${when}`, 'muted'));
      if (review.notes) item.append(el('p', review.notes));
      list.append(item);
    }
    reviews.append(list);
  }
  body.append(checks, scope, files, summary, reviews);
  // (e) attempts and the live log.
  records(body, 'attempts', task.attempts, ['n', 'kind', 'outcome', 'started_at', 'ended_at', 'exit_code']);
  renderLog(body, task);
  // (f) raw fields, closed unless the reader opened them for this task.
  const technical = el('details', undefined, 'technical'), details = el('dl');
  technical.open = technicalOpen === state.t;
  technical.addEventListener('toggle', () => { technicalOpen = technical.open ? state.t : ''; });
  labeled(details, 'state', task.state);
  labeled(details, 'needs_human', tr(task.needs_human ? 'yes' : 'no'));
  labeled(details, 'attempts', tr('run', {n: task.attempts_used, max: task.max_attempts}));
  labeled(details, 'head_changed', tr(task.head_changed ? 'yes' : 'no'));
  technical.append(el('summary', tr('technical')), details, el('h3', tr('stat')), el('pre', task.stat || tr('none')));
  body.append(technical);
}
async function poll() {
  clearTimeout(timer);
  if (document.hidden || inFlight) return;
  inFlight = true;
  try {
    const previous = overview;
    overview = await getJSON('/api/overview'); disconnected = false; lastOk = new Date();
    if (previous && state.t) {
      const relevant = data => JSON.stringify(data.tasks.find(t => `${t.pid}:${t.task_id}` === state.t));
      if (relevant(previous) !== relevant(overview)) loadDetail(true);
    }
  }
  catch { disconnected = true; }
  finally {
    inFlight = false; renderOverview(); renderDetail();
    if (!document.hidden) timer = setTimeout(poll, disconnected ? 5000 : 3000);
  }
}
$('search').addEventListener('input', event => setState({q: event.target.value}, true));
$('project-filter').addEventListener('input', event => { projectFilter = event.target.value; renderOverview(); });
$('project-more').onclick = () => { projectsExpanded = !projectsExpanded; renderOverview(); };
$('menu-button').onclick = () => { if (sidebarOpen) closeSidebar(true); else openSidebar(); };
$('scrim').onclick = () => closeSidebar(true);
narrow.addEventListener('change', () => { sidebarOpen = false; syncSidebar(); });
$('worktree').onchange = event => setState({w: event.target.value});
$('group-worktree').onclick = () => setState({g: state.g === '1' ? '' : '1'});
$('clear').onclick = clearFilters; $('empty-clear').onclick = clearFilters;
$('queue-link-open').onclick = () => setState({view: 'overview'});
$('capacity-all').onclick = () => setState({view: 'accounts'});
$('theme').onchange = event => {
  theme = THEMES.includes(event.target.value) ? event.target.value : 'system';
  try { if (theme === 'system') localStorage.removeItem('dashboard-theme'); else localStorage.setItem('dashboard-theme', theme); } catch {}
  applyTheme();
};
$('close').addEventListener('click', closeDrawer);
// Escape closes the off-canvas sidebar and returns focus to the menu button.
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape' || !sidebarOpen || !$('drawer').hidden) return;
  event.preventDefault(); closeSidebar(true);
});
// "/" jumps to the task search (from any view), unless the reader is already typing.
document.addEventListener('keydown', event => {
  if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey || !$('drawer').hidden) return;
  const target = event.target;
  if (target instanceof Element && (target.closest('input, select, textarea') || target.isContentEditable)) return;
  event.preventDefault(); closeSidebar(false);
  if (!['tasks', 'board'].includes(state.view)) setState({view: 'tasks'});
  $('search').focus();
});
// [ and ] step through the attention queue, unless the reader is typing or the drawer is open.
document.addEventListener('keydown', event => {
  if (!['[', ']'].includes(event.key) || event.ctrlKey || event.metaKey || event.altKey || !$('drawer').hidden) return;
  const target = event.target;
  if (target instanceof Element && (target.closest('input, select, textarea') || target.isContentEditable)) return;
  const items = attentionShown, n = items.length;
  if (!n) return;
  event.preventDefault();
  const index = items.findIndex(a => `${a.pid}:${a.task_id}` === lastOpened), step = event.key === ']' ? 1 : -1;
  const item = items[index < 0 ? (step > 0 ? 0 : n - 1) : (index + step + n) % n];
  openItem(item.pid, item.task_id);
});
document.addEventListener('keydown', event => {
  if ($('drawer').hidden) return;
  if (event.key === 'Escape') { event.preventDefault(); closeDrawer(); }
  if (event.key === 'Tab') {
    const nodes = Array.from($('drawer').querySelectorAll('button, textarea, select, input, summary, [tabindex="0"]'))
      .filter(node => !node.disabled && node.getClientRects().length);
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
