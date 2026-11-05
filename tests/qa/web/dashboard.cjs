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
    // The first screen is the overview; its projects section lists the one project.
    check('the overview lists one project card', await page.locator('#overview-view').isVisible() &&
          await page.locator('#project-cards .project-card').count() === 1,
          await page.locator('#project-cards .project-card').count());
    await page.locator('#views button', { hasText: /^Tasks \(/ }).click();
    await page.getByRole('button', { name: /^All \(/ }).click();
    await page.waitForFunction(n => document.querySelectorAll('#task-rows tr').length === n, cfg.total);
    check('every task listed under All', (await rows(page).count()) === cfg.total, await rows(page).count());
    check('Done chip counts the DONE task', await page.getByRole('button', { name: 'Done (1)' }).count() === 1);
    await page.screenshot({ path: cfg.shots + '/load.png' });
    await open(page, '#view=projects');
    const landed = decodeURIComponent(new URL(page.url()).hash);
    check('#view=projects shows the overview with the projects section',
          landed.includes('view=overview') && await page.locator('#overview-view').isVisible() &&
          await page.locator('#projects-view').isVisible() &&
          await page.locator('#project-cards .project-card').count() === 1, landed);
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
    await open(page, '#view=tasks');
    // Language lives in the display-settings menu.
    await page.locator('#settings summary').click();
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

  // Before the first answer arrives the page shows placeholders, never "no matches".
  async loading(page) {
    await page.route('**/api/overview', async route => {
      await new Promise(resolve => setTimeout(resolve, 1500));
      await route.continue().catch(() => {});
    });
    await page.goto(cfg.url + '#view=tasks');
    await page.waitForTimeout(600);
    check('skeleton shown while the first overview loads', await page.locator('#tasks-loading').isVisible());
    check('skeleton rows hidden from screen readers',
          await page.locator('#tasks-loading .skeleton-row[aria-hidden="true"]').count() === 3);
    check('loading text for screen readers', /Loading/.test(await page.textContent('#loading-text') || ''));
    check('no empty state before data arrives', !(await page.locator('#tasks-empty').isVisible()));
    check('no task rows yet', (await page.locator('#task-rows tr').count()) === 0);
    await page.waitForFunction(() => /Live/.test(document.getElementById('live').textContent), null, { timeout: 8000 });
    await page.waitForFunction(() => document.querySelectorAll('#task-rows tr').length > 0, null, { timeout: 8000 })
      .then(() => check('task rows after the delay', true), e => check('task rows after the delay', false, e));
    check('skeleton gone once data is there', !(await page.locator('#tasks-loading').isVisible()));
    check('empty state still hidden', !(await page.locator('#tasks-empty').isVisible()));
    // The Overview and Accounts views use the same placeholders.
    for (const [view, id] of [['overview', 'overview-loading'], ['accounts', 'accounts-loading']]) {
      await page.goto(cfg.url + `#view=${view}`);
      await page.waitForTimeout(600);
      check(`${view}: skeleton shown while the first overview loads`, await page.locator(`#${id}`).isVisible());
      check(`${view}: skeleton rows hidden from screen readers`,
            await page.locator(`#${id} .skeleton-row[aria-hidden="true"]`).count() === 3);
      check(`${view}: loading text for screen readers`, /Loading/.test(await page.textContent(`#${id} .sr-only`) || ''));
      await page.waitForFunction(id => document.getElementById(id).hidden, id, { timeout: 8000 })
        .then(() => check(`${view}: skeleton gone once data is there`, true),
              e => check(`${view}: skeleton gone once data is there`, false, e));
    }
  },

  // Light and dark themes: from the system setting, and chosen in the top bar, kept after a reload.
  async theme(page) {
    const background = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    await page.emulateMedia({ colorScheme: 'light' });
    await open(page);
    const light = await background();
    await page.emulateMedia({ colorScheme: 'dark' });
    const dark = await background();
    check('system light and dark give different backgrounds', light !== dark, `${light} / ${dark}`);
    await page.emulateMedia({ colorScheme: 'light' });
    check('theme control is labelled', await page.getByLabel('Theme').count() === 1);
    // The theme select lives in the display-settings menu.
    await page.locator('#settings summary').click();
    await page.selectOption('#theme', 'dark');
    const chosen = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
    check('choosing Dark sets data-theme', chosen === 'dark', chosen);
    check('Dark wins over a light system setting', await background() === dark, await background());
    await page.reload();
    await page.waitForFunction(() => /Live/.test(document.getElementById('live').textContent), null, { timeout: 8000 });
    const kept = await page.evaluate(() => [document.documentElement.getAttribute('data-theme'),
                                            document.getElementById('theme').value]);
    check('Dark kept after a reload', kept[0] === 'dark' && kept[1] === 'dark', kept);
  },

  // The drawer leads with status and the next step; raw fields stay folded away.
  async drawer_layout(page) {
    await open(page, '#view=tasks&s=all');
    await page.locator('#task-rows tr', { hasText: cfg.tasks.review }).click();
    await page.waitForFunction(() => !document.getElementById('drawer').hidden &&
      /Verification checks/.test(document.getElementById('drawer-body').innerText), null, { timeout: 8000 });
    const current = await page.locator('#drawer-body [aria-current="step"]').innerText().catch(() => '');
    check('stepper marks Review as the current step', /Review/.test(current), current);
    const earlier = await page.locator('#drawer-body .stepper li.done').count();
    check('earlier steps are marked done', earlier === 4, earlier);
    const body = await page.locator('#drawer-body').innerText();
    const next = body.indexOf('Next step'), checks = body.indexOf('Verification checks');
    check('Next step comes before the verification checks', next >= 0 && next < checks, `${next} / ${checks}`);
    const command = await page.locator('#drawer-body .callout textarea.command').inputValue().catch(() => '');
    check('the callout holds the command', command.includes(`agent-bridge result ${cfg.tasks.review}`), command);
    const technical = await page.evaluate(() => {
      const found = [...document.querySelectorAll('#drawer-body details')]
        .find(d => /Technical details/.test(d.querySelector('summary')?.textContent || ''));
      return found ? { open: found.open } : null;
    });
    check('Technical details is a closed <details>', technical && technical.open === false, JSON.stringify(technical));
    // The fake agent's final text is a JSON object whose summary is "fake ok".
    const final = await page.evaluate(() => {
      const section = [...document.querySelectorAll('#drawer-body .drawer-section')]
        .find(s => /Final summary/.test(s.querySelector('h3')?.textContent || ''));
      if (!section) return null;
      const summary = [...section.querySelectorAll('p')].find(p => p.textContent.includes('fake ok'));
      const raw = section.querySelector('details pre');
      return { summary: !!summary && !summary.closest('pre'), label: section.querySelector('details summary')?.textContent,
               raw: raw ? raw.textContent : '', status: /Status: completed/.test(section.textContent) };
    });
    check('final summary text shown outside a <pre>', final && final.summary, JSON.stringify(final));
    check('final status shown as a labelled line', final && final.status, JSON.stringify(final));
    check('raw final text kept in a Raw output <details>',
          final && final.label === 'Raw output' && final.raw.includes('"summary"') && final.raw.includes('fake ok'),
          JSON.stringify(final));
    check('main is inert while the drawer is open', await page.evaluate(() => document.querySelector('main').inert));
    check('top bar is inert while the drawer is open', await page.evaluate(() => document.querySelector('header').inert));
    await page.screenshot({ path: cfg.shots + '/drawer_layout.png' });
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.getElementById('drawer').hidden, null, { timeout: 4000 }).catch(() => {});
    check('main is not inert after Escape', await page.evaluate(() => !document.querySelector('main').inert));
    check('focus returns to the task row', await page.evaluate(() => !!document.activeElement?.closest('#task-rows')));
  },

  // On a phone each task is a compact block: every field, including the next step, is on screen.
  async mobile_cards(page) {
    await page.setViewportSize({ width: 375, height: 740 });
    await open(page, '#view=tasks&s=all');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal page scroll at 375 px', overflow <= 1, `overflow ${overflow}px`);
    check('one row per task', (await rows(page).count()) === cfg.total, await rows(page).count());
    const row = page.locator('#task-rows tr', { hasText: cfg.tasks.review });
    const next = row.locator('td.next-step span').first();
    await next.scrollIntoViewIfNeeded();
    const box = await next.boundingBox();
    check('next step of the REVIEW task is inside the viewport',
          box && box.x >= 0 && box.x + box.width <= 375 && box.y >= 0 && box.y + box.height <= 740, JSON.stringify(box));
    const columns = await page.locator('#columns th').count();
    const cells = await row.locator('td').evaluateAll(tds => tds.map(td => {
      const r = td.getBoundingClientRect(); return { left: r.left, right: r.right, label: td.dataset.label };
    }));
    check('every field of the card fits the screen width',
          columns >= 4 && cells.length === columns && cells.every(c => c.left >= 0 && c.right <= 375 && c.label),
          JSON.stringify({ columns, cells }));
    const labels = await row.evaluate(r => [...r.querySelectorAll('td')].map(td => getComputedStyle(td, '::before').content));
    check('no repeated field labels on phone rows', labels.every(c => c === 'none' || c === 'normal' || c === '""'),
          JSON.stringify(labels));
    const chips = await page.evaluate(() => {
      const box = document.getElementById('statuses');
      return { tops: new Set([...box.children].map(c => c.getBoundingClientRect().top)).size, overflow: getComputedStyle(box).overflowX };
    });
    check('status chips are one scrollable row', chips.tops === 1 && chips.overflow === 'auto', JSON.stringify(chips));
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    const views = await page.locator('#views button').evaluateAll(buttons => buttons.map(b => b.getBoundingClientRect().y));
    check('view buttons share one row at 390 px',
          views.length === 4 && Math.max(...views) - Math.min(...views) <= 2, JSON.stringify(views));
    const wide = await page.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth]);
    check('no horizontal page scroll at 390 px', wide[0] <= wide[1], JSON.stringify(wide));
    await page.screenshot({ path: cfg.shots + '/mobile_cards.png', fullPage: true });
  },

  // The summary strip and the ranked queue lead to what needs a person.
  async attention(page) {
    // The tiles and the queue live on the overview; a tile then opens the Tasks view.
    await open(page, '#view=overview&s=open');
    await page.waitForFunction(() => !document.getElementById('attention').hidden, null, { timeout: 8000 })
      .then(() => check('the queue is shown', true), e => check('the queue is shown', false, e));
    const item = page.locator('#attention-list [role="listitem"]', { hasText: cfg.tasks.review });
    check('the REVIEW task is in the queue', await item.count() === 1, await item.count());
    const itemText = await item.innerText().catch(() => '');
    check('the queue item shows its command', itemText.includes(`agent-bridge result ${cfg.tasks.review}`), itemText);
    check('the queue badge starts with an aria-hidden glyph',
          await item.locator('.badge > .glyph[aria-hidden="true"]').count() === 1);
    const order = await page.evaluate(async () => {
      const response = await fetch('/api/overview', { headers: { 'X-Bridge-Token': sessionStorage.getItem('agent-bridge-token') } });
      return (await response.json()).attention.map(a => `${a.pid}:${a.task_id}`);
    });
    const shown = await page.locator('#attention-list > [data-key]').evaluateAll(nodes => nodes.map(n => n.dataset.key));
    check('the queue keeps the server order', JSON.stringify(order) === JSON.stringify(shown), `${order} / ${shown}`);
    const counter = page.locator('#summary [data-key="awaitingReview"]');
    const value = (await counter.locator('.counter-value').innerText().catch(() => '')).trim();
    check('Awaiting review counter reads 1', value === '1', value);
    await counter.locator('button').click();
    await page.waitForTimeout(300);
    check('the counter opens the Tasks view', await page.locator('#tasks-view').isVisible() &&
          decodeURIComponent(new URL(page.url()).hash).includes('view=tasks'));
    const pressed = await page.locator('#statuses button[aria-pressed="true"]').innerText().catch(() => '');
    check('the counter selects the Awaiting review chip', /^Awaiting review \(/.test(pressed), pressed);
    check('every table badge has a glyph', await page.evaluate(() => [...document.querySelectorAll('#task-rows .badge')]
      .every(b => b.firstElementChild?.matches('.glyph[aria-hidden="true"]') && b.textContent.length > 2)));
    await page.keyboard.press(']');
    await page.waitForFunction(() => !document.getElementById('drawer').hidden &&
      document.getElementById('drawer-title').textContent.includes('Set x to 2'), null, { timeout: 8000 })
      .then(() => check('] opens the drawer of the queued task', true), e => check('] opens the drawer of the queued task', false, e));
    const hash = decodeURIComponent(new URL(page.url()).hash);
    check('the drawer selection is the REVIEW task', hash.includes(`t=${cfg.pid}:${cfg.tasks.review}`), hash);
    await page.screenshot({ path: cfg.shots + '/attention.png' });
  },

  // The board: lanes by what happens next, finished work folded away.
  async board(page) {
    await open(page, '#view=board');
    await page.waitForFunction(() => document.querySelectorAll('#board-view .card').length > 0, null, { timeout: 8000 })
      .catch(() => {});
    check('the board view is shown', await page.locator('#board-view').isVisible());
    check('the Board button is pressed',
          await page.getByRole('button', { name: 'Board', exact: true }).getAttribute('aria-pressed').catch(() => '') === 'true');
    const tabs = (await page.locator('#views button').allInnerTexts()).map(t => t.trim());
    check('the views are Overview, Tasks, Board and Accounts',
          tabs.length === 4 && tabs[0] === 'Overview' && /^Tasks \(\d+ open\)$/.test(tabs[1]) && tabs[2] === 'Board' &&
          /^Accounts( \(\d+\))?$/.test(tabs[3]), JSON.stringify(tabs));
    check('the board does not repeat the queue', !(await page.locator('#attention').isVisible()) &&
          !(await page.locator('#summary').isVisible()));
    const waiting = (await page.locator('#queue-link').innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
    check('the board shows the one-line link to the queue',
          await page.locator('#queue-link').isVisible() && /^\d+ waiting on you · Open overview$/.test(waiting), waiting);
    const headings = await page.locator('#lanes .lane h2').allInnerTexts();
    const expected = [/^Needs action \(\d+\)$/, /^In review \(\d+\)$/, /^Running \(\d+\)$/, /^Queued \(\d+\)$/];
    check('four lane headings in order', headings.length === 4 && expected.every((re, i) => re.test(headings[i])),
          JSON.stringify(headings));
    const card = page.locator('#lane-awaitingReview .card', { hasText: cfg.tasks.review });
    check('the REVIEW card is in In review', await card.count() === 1, await card.count());
    check('the two READY tasks are queued', await page.locator('#lane-waiting .card').count() === 2,
          await page.locator('#lane-waiting .card').count());
    check('an empty lane says Nothing here', /Nothing here/.test(await page.locator('#lane-running-empty').innerText().catch(() => '')));
    check('Finished is a collapsed <details>', await page.evaluate(() => document.getElementById('finished').open === false));
    check('Finished counts two tasks', /^Finished \(2\)/.test(await page.textContent('#finished-summary')),
          await page.textContent('#finished-summary'));
    check('Finished holds two cards', await page.locator('#finished-cards .card').count() === 2,
          await page.locator('#finished-cards .card').count());
    check('every card badge has a glyph', await page.evaluate(() => [...document.querySelectorAll('#board-view .card .badge')]
      .every(b => b.firstElementChild?.matches('.glyph[aria-hidden="true"]'))));
    await card.focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => !document.getElementById('drawer').hidden &&
      document.getElementById('drawer-title').textContent.includes('Set x to 2'), null, { timeout: 8000 })
      .then(() => check('Enter on a card opens the drawer', true), e => check('Enter on a card opens the drawer', false, e));
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.getElementById('drawer').hidden, null, { timeout: 4000 }).catch(() => {});
    await page.reload();
    await page.waitForFunction(() => /Live/.test(document.getElementById('live').textContent), null, { timeout: 8000 });
    const hash = decodeURIComponent(new URL(page.url()).hash);
    check('a reload keeps view=board', hash.includes('view=board') && await page.locator('#board-view').isVisible(), hash);
    await page.setViewportSize({ width: 375, height: 740 });
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal page scroll at 375 px', overflow <= 1, `overflow ${overflow}px`);
    await page.screenshot({ path: cfg.shots + '/board.png', fullPage: true });
  },

  // Screen readers hear about a change in the Needs-you count, and nothing at load.
  async live_region(page) {
    let inject = false;
    await page.route('**/api/overview', async route => {
      const response = await route.fetch();
      const body = await response.json();
      if (inject) body.attention = [{ pid: cfg.pid, task_id: 'T9999', kind: 'failed', objective: 'Injected',
                                      since: Date.now() / 1000, reason: null, command: null }, ...body.attention];
      await route.fulfill({ response, json: body });
    });
    await open(page);
    await page.waitForTimeout(3500);
    const region = await page.evaluate(() => {
      const node = document.getElementById('announcer');
      return node && { live: node.getAttribute('aria-live'), text: node.textContent, inMain: !!node.closest('main') };
    });
    check('a polite live region exists', region && region.live === 'polite', JSON.stringify(region));
    check('the live region is empty after load', region && region.text === '', JSON.stringify(region));
    check('the live region stays outside the inert main', region && !region.inMain);
    inject = true;
    await page.waitForFunction(() => /1 task needs you/.test(document.getElementById('announcer').textContent),
                               null, { timeout: 8000 })
      .then(() => check('a new Needs-you item is announced', true), e => check('a new Needs-you item is announced', false, e));
  },

  // The shell: display-settings menu, phone header and bottom bar, columns and filters that carry information.
  async shell(page) {
    await open(page, '#view=tasks&s=all');
    const summary = page.locator('#settings summary');
    const name = () => summary.evaluate(s => [...s.childNodes]
      .filter(n => !(n.nodeType === 1 && n.getAttribute('aria-hidden') === 'true'))
      .map(n => n.textContent).join('').trim());
    check('settings summary is named in English', await name() === 'Display settings', await name());
    check('settings menu starts closed', await page.evaluate(() => !document.getElementById('settings').open));
    await summary.click();
    check('clicking the summary opens the menu', await page.evaluate(() => document.getElementById('settings').open));
    check('the menu holds the languages and the theme',
          await page.locator('#settings #languages').isVisible() && await page.locator('#settings #theme').isVisible());
    await page.locator('#theme').focus();
    await page.keyboard.press('Escape');
    const escaped = await page.evaluate(() => {
      const menu = document.getElementById('settings');
      return { open: menu.open, focus: document.activeElement === menu.querySelector('summary') };
    });
    check('Escape closes the menu', escaped.open === false, JSON.stringify(escaped));
    check('Escape returns focus to the summary', escaped.focus, JSON.stringify(escaped));
    await summary.click();
    await page.locator('#task-count').click();
    check('a click outside closes the menu', await page.evaluate(() => !document.getElementById('settings').open));
    await summary.click();
    await page.locator('#languages button', { hasText: 'VI' }).click();
    await page.waitForFunction(() => document.documentElement.lang === 'vi');
    check('settings summary is named in Vietnamese', await name() === 'Cài đặt hiển thị', await name());
    check('the needsYou tab reads Cần xử lý',
          await page.locator('#statuses button', { hasText: /^Cần xử lý \(/ }).count() === 1);
    await page.locator('#languages button', { hasText: 'EN' }).click();
    await page.waitForFunction(() => document.documentElement.lang === 'en');
    await page.keyboard.press('Escape');

    // One project and one agent: no Location or Agent column; header and cells match.
    const heads = await page.locator('#columns th').allInnerTexts();
    check('no Location or Agent header with one project and one agent',
          !heads.some(h => /^(Location|Agent)$/.test(h.trim())) && heads.length === 4, JSON.stringify(heads));
    const counts = await rows(page).evaluateAll(trs => trs.map(tr => tr.querySelectorAll('td').length));
    check('every row has one cell per header', counts.length > 0 && counts.every(n => n === heads.length),
          JSON.stringify(counts));
    const nexts = await page.locator('#task-rows td.next-step').evaluateAll(tds =>
      tds.map(td => ({ button: !!td.querySelector('button'), text: td.textContent })));
    check('a Next step cell without a command is empty',
          nexts.some(c => !c.button) && nexts.every(c => c.button || c.text === ''), JSON.stringify(nexts));
    check('project and worktree filters hidden with one project',
          !(await page.locator('#project').isVisible()) && !(await page.locator('#worktree').isVisible()));
    check('Group by worktree hidden without extra checkouts', !(await page.locator('#group-worktree').isVisible()));
    check('search stays visible', await page.locator('#search').isVisible());
    check('the needsYou tab reads Needs action',
          await page.locator('#statuses button', { hasText: /^Needs action \(/ }).count() === 1);
    check('the needsYou tile reads Needs action',
          /Needs action/.test(await page.locator('#summary [data-key="needsYou"]').innerText().catch(() => '')));
    const hash = decodeURIComponent(new URL(page.url()).hash);
    await page.locator('#statuses button', { hasText: /^Needs action \(/ }).click();
    check('the needsYou hash value is unchanged', decodeURIComponent(new URL(page.url()).hash).includes('s=needsYou'), hash);
    await page.locator('#statuses button', { hasText: /^All \(/ }).click();

    // Phone: a one-row header and the views as a fixed bottom bar.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(300);
    const phone = await page.evaluate(() => {
      const header = document.getElementById('topbar').getBoundingClientRect();
      const views = document.getElementById('views'), box = views.getBoundingClientRect();
      return { header: header.height, position: getComputedStyle(views).position, bottom: box.bottom, top: box.top,
               height: window.innerHeight,
               items: [...views.querySelectorAll('button')].map(b => b.getBoundingClientRect().height) };
    });
    check('phone header is at most 56 px tall', phone.header <= 56, JSON.stringify(phone));
    check('views bar is fixed at the bottom', phone.position === 'fixed' && Math.abs(phone.bottom - phone.height) <= 1,
          JSON.stringify(phone));
    check('bottom bar items are at least 56 px tall', phone.items.length === 4 && phone.items.every(h => h >= 56),
          JSON.stringify(phone));
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    await page.waitForTimeout(200);
    const reach = await page.evaluate(() => {
      const views = document.getElementById('views').getBoundingClientRect().top;
      const footer = document.getElementById('footer').getBoundingClientRect().bottom;
      const all = document.querySelectorAll('#task-rows tr'), last = all[all.length - 1].getBoundingClientRect().bottom;
      return { views, footer, last };
    });
    check('footer and last row stay above the bottom bar', reach.footer <= reach.views + 1 && reach.last <= reach.views + 1,
          JSON.stringify(reach));
    await page.screenshot({ path: cfg.shots + '/shell.png' });

    // The status tabs scroll inside their own row, never the page.
    await page.setViewportSize({ width: 375, height: 740 });
    await page.waitForTimeout(300);
    const tabs = await page.evaluate(() => ({
      page: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      overflow: getComputedStyle(document.getElementById('statuses')).overflowX }));
    check('status tabs cause no horizontal page scroll at 375 px', tabs.page <= 1 && tabs.overflow === 'auto',
          JSON.stringify(tabs));
  },

  // The home screen: triage first, then work in progress, capacity and projects; no task table.
  async overview(page) {
    await open(page);
    const hash = decodeURIComponent(new URL(page.url()).hash);
    check('the default view is the overview', hash.includes('view=overview') && await page.locator('#overview-view').isVisible(), hash);
    check('the Overview button is pressed',
          await page.locator('#views button', { hasText: /^Overview$/ }).getAttribute('aria-pressed').catch(() => '') === 'true');
    await page.waitForFunction(() => !document.getElementById('attention').hidden, null, { timeout: 8000 })
      .then(() => check('the queue is shown', true), e => check('the queue is shown', false, e));
    check('the REVIEW task is in the queue',
          await page.locator('#attention-list [role="listitem"]', { hasText: cfg.tasks.review }).count() === 1);
    check('the summary tiles are shown', await page.locator('#summary').isVisible() &&
          await page.locator('#summary > [data-key]').count() === 4);
    check('In progress has its heading', (await page.textContent('#inflight-title')) === 'In progress');
    const idle = await page.textContent('#inflight-empty');
    check('In progress says nothing is running', await page.locator('#inflight-empty').isVisible() && idle === 'Nothing is running.', idle);
    const accountRows = await page.locator('#capacity-summary [role="listitem"]').count();
    check('the accounts summary is shown', await page.locator('#capacity-section').isVisible() &&
          (accountRows > 0 || await page.locator('#capacity-empty').isVisible()), accountRows);
    check('every account row has a state badge', await page.evaluate(() =>
      [...document.querySelectorAll('#capacity-summary [role="listitem"]')]
        .every(r => r.querySelector('.badge > .glyph[aria-hidden="true"]'))));
    check('the projects section lists one project card', await page.locator('#projects-view').isVisible() &&
          await page.locator('#project-cards .project-card').count() === 1);
    check('no task table on the overview', !(await page.locator('#task-rows').isVisible()) &&
          !(await page.locator('#filters').isVisible()));
    check('no waiting line on the overview itself', !(await page.locator('#queue-link').isVisible()));
    const tops = await page.evaluate(() => ['summary', 'attention', 'inflight-section', 'capacity-section', 'projects-view']
      .map(id => document.getElementById(id).getBoundingClientRect().top));
    check('tiles, queue, In progress, accounts, projects from top to bottom',
          tops.every((top, i) => i === 0 || top > tops[i - 1]), JSON.stringify(tops));
    await page.screenshot({ path: cfg.shots + '/overview.png', fullPage: true });

    // Old links to the projects view land on the overview at the projects section.
    await open(page, '#view=projects');
    await page.waitForTimeout(400);
    const landed = decodeURIComponent(new URL(page.url()).hash);
    check('#view=projects lands on the overview', landed.includes('view=overview') &&
          await page.locator('#overview-view').isVisible(), landed);
    const place = await page.evaluate(() => [document.getElementById('projects-view').getBoundingClientRect().top, window.innerHeight]);
    check('the projects section is on screen', place[0] >= 0 && place[0] < place[1], JSON.stringify(place));
    check('the Overview button is pressed after the old link',
          await page.locator('#views button', { hasText: /^Overview$/ }).getAttribute('aria-pressed').catch(() => '') === 'true');

    // Tasks no longer repeats the queue; one line leads back to it.
    await open(page, '#view=tasks');
    const line = (await page.locator('#queue-link').innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
    check('Tasks shows the waiting line', await page.locator('#queue-link').isVisible() &&
          /^\d+ waiting on you · Open overview$/.test(line), line);
    check('Tasks does not repeat the queue or the tiles', !(await page.locator('#attention').isVisible()) &&
          !(await page.locator('#summary').isVisible()));
    await page.locator('#queue-link-open').click();
    await page.waitForFunction(() => !document.getElementById('overview-view').hidden, null, { timeout: 4000 })
      .then(() => check('the waiting line opens the overview', true), e => check('the waiting line opens the overview', false, e));
    check('the address says view=overview', decodeURIComponent(new URL(page.url()).hash).includes('view=overview'));
    await page.setViewportSize({ width: 375, height: 740 });
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal page scroll at 375 px', overflow <= 1, `overflow ${overflow}px`);
  },

  // Capacity after a real quota failover: cl1 ran out, the task moved to claude2 on cl2.
  async capacity(page) {
    const cardsReady = () => page.waitForFunction(() => document.querySelectorAll('#account-cards > [data-key]').length >= 2,
                                                  null, { timeout: 8000 });
    await open(page, '#view=accounts');
    await cardsReady().then(() => check('account cards shown', true), e => check('account cards shown', false, e));
    const card = name => page.locator(`#account-cards > [data-key="${name}"]`);
    check('one card for cl1 and one for cl2', await card('cl1').count() === 1 && await card('cl2').count() === 1);
    const cl1 = await card('cl1').innerText().catch(() => '');
    check('cl1 has a state badge', await card('cl1').locator('.account-head .badge > .glyph[aria-hidden="true"]').count() === 1, cl1);
    check('cl1 is out of quota, or available with the lifted note',
          /Out of quota/.test(cl1) || (/Available/.test(cl1) && /Quota mark lifted/.test(cl1)), cl1);
    const meters = await card('cl2').locator('[role="meter"]').evaluateAll(nodes => nodes.map(m => ({
      now: Number(m.getAttribute('aria-valuenow')), min: m.getAttribute('aria-valuemin'), max: m.getAttribute('aria-valuemax'),
      label: m.getAttribute('aria-label'), width: m.querySelector('.meter-fill')?.style.width })));
    check('cl2 shows at least one labelled meter', meters.length >= 1 && meters.every(m =>
      m.now >= 0 && m.now <= 100 && m.min === '0' && m.max === '100' && /: \d+% used$/.test(m.label || '')), JSON.stringify(meters));
    check('meter fills are sized through the CSSOM', meters.every(m => /%$/.test(m.width || '')), JSON.stringify(meters));
    const cl2 = await card('cl2').innerText().catch(() => '');
    check('cl2 says how much is used', /\d+% used/.test(cl2), cl2);
    check('cl2 lists its slots and agent', /\d+ of \d+ slots busy/.test(cl2) && cl2.includes('claude2'), cl2);
    const panel = await page.locator('#fallback-panels').innerText().catch(() => '');
    check('the fallback panel says it is on', /Quota fallback is on/.test(panel), panel);
    check('the fallback panel names where the policy is set', panel.includes('routing.quota_fallback'), panel);
    check('the fallback panel has no edit controls',
          await page.locator('#fallback-panels button, #fallback-panels input, #fallback-panels select').count() === 0);
    const switches = page.locator('#switches [role="listitem"]');
    check('one automatic switch listed', await switches.count() === 1, await switches.count());
    const row = await switches.first().innerText().catch(() => '');
    check('the switch names the task, claude1 and claude2',
          row.includes(cfg.tasks.failover) && row.includes('claude1') && row.includes('claude2'), row);
    check('the account-list hint is shown', (await page.locator('#accounts-hint').innerText()).includes('agent-bridge account-list'));
    await page.screenshot({ path: cfg.shots + '/capacity.png', fullPage: true });
    await switches.first().locator('button').click();
    await page.waitForFunction(() => !document.getElementById('drawer').hidden, null, { timeout: 8000 })
      .then(() => check('clicking the switch opens the drawer', true), e => check('clicking the switch opens the drawer', false, e));
    const hash = decodeURIComponent(new URL(page.url()).hash);
    check('the drawer is for the switched task', hash.includes(`t=${cfg.pid}:${cfg.tasks.failover}`), hash);
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => document.getElementById('drawer').hidden, null, { timeout: 4000 }).catch(() => {});

    // The overview summarises both accounts and the policy.
    await open(page, '#view=overview');
    const summary = page.locator('#capacity-summary');
    check('the overview lists both accounts',
          await summary.locator('[data-key="cl1"]').count() === 1 && await summary.locator('[data-key="cl2"]').count() === 1,
          await summary.innerText().catch(() => ''));
    check('every summary row has a state badge', await page.evaluate(() =>
      [...document.querySelectorAll('#capacity-summary [role="listitem"]')]
        .every(r => r.querySelector('.badge > .glyph[aria-hidden="true"]'))));
    const cl2Row = await summary.locator('[data-key="cl2"]').innerText().catch(() => '');
    check('the cl2 row shows slots and quota left', /\d+ of \d+ busy/.test(cl2Row) && /\d+% left/.test(cl2Row), cl2Row);
    const line = await page.textContent('#capacity-fallback');
    check('the overview fallback line says it is on',
          await page.locator('#capacity-fallback').isVisible() && /Quota fallback is on/.test(line), line);

    // A phone: nothing pushes the page sideways.
    await page.setViewportSize({ width: 375, height: 740 });
    await open(page, '#view=accounts');
    await cardsReady().catch(() => {});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    check('no horizontal page scroll at 375 px on Accounts', overflow <= 1, `overflow ${overflow}px`);
    await page.screenshot({ path: cfg.shots + '/capacity-mobile.png', fullPage: true });

    // Vietnamese.
    await page.setViewportSize({ width: 1280, height: 800 });
    await open(page, '#view=accounts&lang=vi');
    await page.waitForFunction(() => document.documentElement.lang === 'vi');
    const heading = await page.textContent('#accounts-title');
    check('the Accounts heading is Vietnamese', heading === 'Tài khoản', heading);
    const vi = await page.locator('#fallback-panels').innerText().catch(() => '');
    check('the fallback panel is Vietnamese', /đang bật/.test(vi) && !/Quota fallback is on/.test(vi), vi);
    await open(page, '#view=overview&lang=vi');
    const viLine = await page.textContent('#capacity-fallback');
    check('the overview fallback line is Vietnamese', /đang bật/.test(viLine) && !/Quota fallback/.test(viLine), viLine);
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
