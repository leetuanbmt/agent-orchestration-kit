# Web dashboard design

`agent-bridge web` is a read-only, localhost view of every project's bridge state. This page records
why it looks the way it does: the audit that started the redesign, the information architecture,
the design tokens and components, and the accessibility rules it follows. Change
the dashboard against this page, and change this page when a rule changes.

## Who it is for

One engineer running several coding agents across a few projects and accounts. They glance at the
dashboard between other work and need to answer, in this order:

1. Is anything waiting on me, and what exactly do I run?
2. What is running right now, and is it near its time limit?
3. Do my accounts have quota and free slots, and will a quota pause move to another account?
4. Where is a given task, and what happened in it?

The dashboard never acts. Every next step is a command to copy into a terminal, because approvals,
logins and account changes are human-only by design.

## Audit (before this redesign)

Measured with the seeded sandbox used for the README screenshots (one project, eight tasks), at
1280x800 and 390x844, light and dark.

| # | Problem | Evidence | Severity |
|---|---|---|---|
| 1 | Capacity was invisible | A task said "out of quota", but nothing said which account, when it resets, how many slots were busy or whether quota fallback would move the work. `/api/overview` had no account data. | Critical |
| 2 | The same fact had different numbers | "Needs you 1" (tile, chip, lane) next to "Waiting on you (2)" (queue): the queue also counts reviews. | High |
| 3 | Controls before content | 13 controls (3 views, 2 languages, theme, search, 2 selects, group, clear, 8 chips) sat above the first task row; on a phone the header took three rows and the first task started near y = 880. | High |
| 4 | Columns without information | With one project and one agent, Location and Agent repeated one value on every row; Next step showed "–" in 6 of 8 rows; phone cards repeated six labels per task (about 200 px each). | Medium |
| 5 | No visual hierarchy | Navigation, filters, statuses and badges were all outlined pills of the same weight; the warm beige palette made panels and page blend. | Medium |

## Benchmark

[OrchestratorInc/agent-orchestrator](https://github.com/OrchestratorInc/agent-orchestrator) (its
`DESIGN.md` and renderer source) was read for patterns, not copied. Taken as principles: truthful
state before polish; one status slot with a glyph, so color is never the only signal; borders as
structure rather than decoration; color only when it means something; sentence case; compact but
44 px touch targets; detail in an inspector instead of new pages. A sidebar with the project list
was added later, on evidence rather than from the reference: real use showed about 40 projects on
one machine, so the page needed a project scope that a select could not offer. Not taken: its
tokens, names and components, and any action buttons (this dashboard is view-only).

## Information architecture

| View | Answers | Contents |
|---|---|---|
| Home (default) | Questions 1 to 3 | Summary tiles, "Waiting on you" queue with copyable commands (first six, then a toggle), In progress cards with time used, account summary with the quota-fallback line, projects (first eight, then a toggle) |
| Tasks | Question 4 | Search, status tabs, the task table (phone: compact rows) |
| Board | Question 4 by stage | Lanes: Needs action, In review, Running, Queued, plus Finished |
| Accounts | Question 3 in detail | Fallback policy per project, one card per account (state, quota windows, slots, agents), recent automatic switches |
| Settings | - | Language, theme, the CLI name and the keyboard shortcuts |

The sidebar holds the product name, these views and the project list: All projects, then each
project with the number of tasks that need a person, a filter box once there are more than eight,
and "Show all". Choosing a project scopes every view and the page title. Below 1024 px the sidebar
is off-canvas, opened from the menu button.

The task drawer opens from every view (`t=` in the URL) and holds status, the next step, the
lifecycle, the planner workflow it belongs to (its steps and progress), checks, changed files (grouped by directory above eight, cache folders closed), reviews and the live log. With more than one project in view, every task id carries its project name, because ids restart at T0001 in each project; Accounts then shows one fallback summary listing every project. Old links keep working: `view=projects`
opens Home at the projects section.

Progressive disclosure: project and worktree filters appear only with more than one project,
"Group by worktree" only when a project has worktrees, Location and Agent columns only when they
differ between rows, the technical record of a task only when expanded in the drawer, and language
and theme in the Settings view. From 1000 px the task table has a fixed layout: objective, location
and agent ellipsize (full value in the title), status and next step wrap; below 1280 px the
objective takes two lines and Next step shows only the copy button.

## Tokens

All colors live in three token blocks at the top of `bridge/static/app.css` (light, dark from the
system setting, dark chosen in the menu); the rest of the sheet uses only `var()`.

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | #F6F7F9 | #0F1115 | page |
| `--surface` | #FFFFFF | #171A20 | panels, header, drawer |
| `--surface-2` | #EEF0F3 | #1F232B | quiet buttons, code, lanes |
| `--surface-3` | #E4E7EC | #2A2F38 | hover |
| `--border` | #E3E6EB | #2A2F38 | panel and row lines |
| `--border-strong` | #7D8491 | #6B7280 | inputs, pressed controls (3:1) |
| `--text` | #16181D | #E8EAEE | content |
| `--text-muted` | #596170 | #A0A7B4 | metadata, labels |
| `--accent` | #2454D0 | #7AA2FF | focus, selection, progress |
| `--st-needs` | #C2410C | #FB923C | needs action |
| `--st-review` | #9A5B00 | #FBBF24 | awaiting review, quota above 80 % |
| `--st-running` | #2454D0 | #7AA2FF | running |
| `--st-done` | #157A3B | #4ADE80 | done, account available |
| `--st-blocked` | #B91C1C | #F87171 | blocked, failed, login needed, quota at 100 % |
| `--st-paused`, `--st-queue` | #596170 | #A0A7B4 | paused, queued, cancelled |

Every text color reaches 4.5:1 on `--bg`, `--surface` and `--surface-2` of its theme; the browser
audit measures it again on the rendered page.

Type: system UI font; 12 / 13 / 14 (body) / 16 (section titles) / 20 px (stat values); weight 500
for controls, 600 for headings and selected items; tabular figures for counts and times. Spacing:
4, 8, 12, 16, 24, 32 px. Radius: 6 px for controls, 10 px for panels and cards. Shadows only on the
drawer and the phone sidebar. Motion: color transitions under 150 ms, none with
`prefers-reduced-motion`.

## Components

- **Status badge**: a glyph (`!` needs, `◐` review, `▶` running, `○` queued, `✓` done, `✕` failed or
  blocked, `‖` paused, `–` cancelled) in the state color, then the label in the text color. The
  glyph is `aria-hidden`; the label carries the meaning.
- **Quiet button**: `--surface-2` fill, no border, 32 px (44 px on touch screens). Pressed state:
  surface fill, strong border, weight 600.
- **Sidebar navigation** for the views and the project scope; an off-canvas sheet with a menu button below 1024 px.
- **Tabs** for status filters: text with a 2 px accent underline on the selected one.
- **Panel**: surface, 1 px border, 10 px radius. No card inside a card.
- **Command**: monospace on `--surface-2` with a copy button; copying falls back to selecting the text.
- **Meter** for quota windows: `role="meter"` with `aria-valuenow`, a label naming the window, and the
  percentage in text so color is never the only signal.
- **States**: skeleton rows (hidden from screen readers, with a "Loading…" text) before the first
  answer, an offline banner that keeps the last data visible, empty states that say what to run.

## Accessibility rules

- Contrast: text 4.5:1 (3:1 for large text), input borders 3:1.
- Every control has an accessible name in English and Vietnamese; icon-only controls use
  `aria-label`.
- Visible focus: a 2 px accent outline on every focusable element.
- Keyboard: `[` and `]` step through the queue, Escape closes the drawer and the phone sidebar and
  returns focus.
- Touch: 44 px targets on coarse pointers; no horizontal page scroll at 320 px.
- Live updates: one polite announcement when the number of tasks needing action changes.

## How it is checked

- `tests/qa/test_e2e_web.py` with `tests/qa/web/dashboard.cjs` (Playwright, `QA_E2E=1`): loading,
  token handling, XSS, live updates, language, theme, search, offline, phone layout, drawer, queue,
  board, live region, the shell, the overview, the accounts view with a real quota failover,
  several projects sharing task ids, a planner workflow and the table fit. Of the rules above it
  enforces no horizontal scroll at 375 px, focus returning on Escape, the meter labels, the status
  symbols, 24 px targets in the sidebar and the drawer, the drawer's dialog element and the log
  role; contrast and focus rings are not checked by the suite.
- `test_web_visual` compares 13 screenshots (each view in both themes, the drawer, phone Home)
  with baselines in `tests/qa/web/visual/<platform>/`, on a fixed clock with timestamps and paths
  normalised. Baselines exist for macOS only; other platforms skip it. After an intended visual
  change, update them with `QA_E2E=1 QA_VISUAL_UPDATE=1 uv run --frozen pytest -q
  tests/qa/test_e2e_web.py::test_web_visual` and review the new images before committing.
- For this redesign, audits outside the repository were run on real data (37 projects) as well
  as on a seeded project: axe-core 4.10 (WCAG 2.0, 2.1 and 2.2 A and AA plus best practice) found
  no violation on any view in either theme at 1440 and 390 px after the fixes; a computed-style
  pass checked contrast, accessible names, duplicate ids, focus rings on a Tab walk, target sizes
  and overflow. Repeat them by hand after changing colors or layout.
