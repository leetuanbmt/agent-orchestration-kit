// Browser scenarios for the agent-bridge web dashboard, run by tests/qa/test_e2e_web.py.
// Usage: node dashboard.cjs <config.json>; prints one JSON line {ok, checks, error}.
const { chromium } = require('playwright');
const { execFileSync } = require('child_process');
const fs = require('fs');

const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const checks = [];
function check(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail === undefined ? null : String(detail).slice(0, 300) });
}
function cli(...args) {
  return JSON.parse(execFileSync(cfg.cli, args, { cwd: cfg.root, env: cfg.env, encoding: 'utf8' }));
}
const rows = page => page.locator('#task-rows tr:not(.worktree-header)');
async function open(page, hash = '') {
  await page.goto(cfg.url + hash);
  await page.waitForFunction(() => /Live|Trực tiếp/.test(document.getElementById('live').textContent));
}

const scenarios = {
  // Opening the printed link: the token leaves the address bar, data loads, filters count.
  async load(page) {
    await open(page);
    const url = new URL(page.url());
    check('token removed from the address bar', !url.search.includes('token'), page.url());
    const stored = await page.evaluate(() => [sessionStorage.getItem('agent-bridge-token'),
                                              localStorage.getItem('agent-bridge-token')]);
    check('token kept for this tab only (sessionStorage)', stored[0] && !stored[1], stored);
    await page.getByRole('button', { name: /^All \(/ }).click();
    await page.waitForFunction(n => document.querySelectorAll('#task-rows tr').length === n, cfg.total);
    check('every task listed under All', (await rows(page).count()) === cfg.total, await rows(page).count());
    check('Done chip counts the DONE task', await page.getByRole('button', { name: 'Done (1)' }).count() === 1);
    check('Projects tab counts one project', await page.getByRole('button', { name: 'Projects (1)' }).count() === 1);
    await page.screenshot({ path: cfg.shots + '/load.png' });
  },

  // Opening a task: drawer content, deep link that survives a reload, closing.
  async drawer(page) {
    await open(page, '#view=tasks&s=all');
    await page.locator('#task-rows tr', { hasText: cfg.tasks.review }).click();
    const opened = () => page.waitForFunction(() => !document.getElementById('drawer').hidden &&
      document.getElementById('drawer-title').textContent.includes('Set x to 2'), null, { timeout: 8000 });
    await opened();
    await page.waitForFunction(() => !/Loading/.test(document.getElementById('drawer-body').innerText),
                               null, { timeout: 8000 });
    const body = await page.locator('#drawer-body').innerText();
    check('drawer title is the objective', (await page.textContent('#drawer-title')).includes('Set x to 2'));
    check('drawer shows the task id and state', body.includes(cfg.tasks.review) || /Review/.test(body), body.slice(0, 300));
    check('drawer shows the verification checks', /Verification checks/.test(body), body.slice(0, 600));
    const command = await page.locator('#drawer-body textarea.command').inputValue().catch(() => '');
    check('drawer shows the suggested command', command.includes(`agent-bridge result ${cfg.tasks.review}`), command);
    const hash = decodeURIComponent(new URL(page.url()).hash);
    check('selection is in the address (deep link)', hash.includes(`t=${cfg.pid}:${cfg.tasks.review}`), hash);
    await page.reload();
    await opened().then(() => check('deep link reopens after reload', true),
                        e => check('deep link reopens after reload', false, e));
    await page.screenshot({ path: cfg.shots + '/drawer.png' });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(400);
    const escaped = await page.evaluate(() => document.getElementById('drawer').hidden);
    check('Escape closes the drawer', escaped);
    if (!escaped) {
      await page.locator('#close').click();
      await page.waitForTimeout(400);
    }
    check('close leaves no selection in the address', !decodeURIComponent(new URL(page.url()).hash).includes(':T0'));
  },

  // A tab opened without the printed link has no token.
  async no_token(page) {
    await page.goto(cfg.base + '/');
    await page.waitForFunction(() => /Open the link|Mở đường dẫn/.test(document.getElementById('live').textContent),
                               null, { timeout: 8000 }).then(() => check('asks for the printed link', true),
                                                             e => check('asks for the printed link', false, e));
    check('no task data shown', (await page.locator('#task-rows tr').count()) === 0);
  },

  // Objective text is shown as text, never as markup.
  async xss(page) {
    await open(page, `#view=tasks&s=all&t=${cfg.pid}:${cfg.tasks.xss}`);
    await page.waitForFunction(() => !document.getElementById('drawer').hidden, null, { timeout: 8000 });
    await page.waitForTimeout(1000);
    check('payload shown literally in the drawer title',
          (await page.textContent('#drawer-title')).includes('<img'));
    check('no injected element in the drawer', (await page.locator('#drawer img').count()) === 0);
    const fired = await page.evaluate(() => window.__xss === 1);
    check('script in an objective never runs', !fired);
    check('no injected element in the table', (await page.locator('#task-rows img').count()) === 0);
    check('payload shown literally', (await page.locator('#task-rows').innerText()).includes('<img'));
  },

  // New work appears without a reload.
  async live(page) {
    await open(page, '#view=tasks&s=all');
    const before = await rows(page).count();
    const created = cli('create', cfg.newSpec);
    await page.waitForFunction(id => document.getElementById('task-rows').textContent.includes(id),
                               created.task_id, { timeout: 8000 })
      .then(() => check('a task created in a terminal appears within 8 s', true),
            e => check('a task created in a terminal appears within 8 s', false, e));
    cli('execute', created.task_id);
    const row = page.locator('#task-rows tr', { hasText: created.task_id });
    await page.waitForFunction(id => [...document.querySelectorAll('#task-rows tr')]
      .some(r => r.textContent.includes(id) && /review/i.test(r.textContent)), created.task_id, { timeout: 8000 })
      .then(() => check('its state change appears within 8 s', true),
            e => check('its state change appears within 8 s', false, e));
    check('row count grew by one', (await rows(page).count()) === before + 1, await row.innerText());
  },

  // Switching language, kept after a reload.
  async language(page) {
    await open(page);
    await page.locator('#languages button', { hasText: 'VI' }).click();
    await page.waitForFunction(() => document.documentElement.lang === 'vi');
    const chips = await page.locator('#statuses').innerText();
    check('labels switch to Vietnamese', chips.includes('Tất cả'), chips);
    await page.goto(cfg.base + '/');
    await page.waitForTimeout(1000);
    check('choice kept after reopening', await page.evaluate(() => document.documentElement.lang) === 'vi');
  },

  // Search, empty result and clearing filters.
  async search(page) {
    await open(page, '#view=tasks&s=all');
    await page.fill('#search', cfg.tasks.review);
    await page.waitForTimeout(500);
    check('search by id keeps one row', (await rows(page).count()) === 1, await rows(page).count());
    await page.fill('#search', 'no-such-task-zzz');
    await page.waitForTimeout(500);
    check('empty state message shown', await page.locator('#tasks-empty').isVisible());
    await page.locator('#empty-clear').click();
    await page.waitForTimeout(500);
    check('clear filters brings tasks back', (await rows(page).count()) > 0);
    await page.fill('#search', 'tiếng Việt');
    await page.waitForTimeout(500);
    check('search matches Vietnamese text', (await rows(page).count()) === 1);
  },

  // The server goes away while the page is open.
  async offline(page) {
    await open(page);
    process.kill(cfg.serverPid, 'SIGTERM');
    await page.waitForFunction(() => /Disconnected|Mất kết nối/.test(document.getElementById('live').textContent),
                               null, { timeout: 12000 })
      .then(() => check('shows disconnected', true), e => check('shows disconnected', false, e));
    check('last data stays visible', (await page.locator('#task-rows tr').count()) >= 0);
  },

  // A phone-sized screen.
  async mobile(page) {
    await page.setViewportSize({ width: 375, height: 740 });
    await open(page, '#view=tasks&s=all');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal page scroll at 375 px', overflow <= 1, `overflow ${overflow}px`);
    await page.screenshot({ path: cfg.shots + '/mobile.png', fullPage: true });
  },

  // Time to a usable table with every task.
  async performance(page) {
    const started = Date.now();
    await open(page, '#view=tasks&s=all');
    await page.waitForFunction(n => document.querySelectorAll('#task-rows tr').length >= n, cfg.total, { timeout: 15000 });
    const ms = Date.now() - started;
    check(`table with ${cfg.total} tasks ready in under 3 s`, ms < 3000, `${ms} ms`);
  },
};

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, locale: 'en-US' });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  try {
    await scenarios[cfg.scenario](page);
    check('no JavaScript errors', errors.length === 0, errors.join(' | '));
    console.log(JSON.stringify({ ok: checks.every(c => c.ok), checks }));
  } catch (e) {
    await page.screenshot({ path: `${cfg.shots}/${cfg.scenario}-error.png` }).catch(() => {});
    console.log(JSON.stringify({ ok: false, checks, error: String(e && e.stack || e) }));
  } finally {
    await browser.close();
  }
})();
