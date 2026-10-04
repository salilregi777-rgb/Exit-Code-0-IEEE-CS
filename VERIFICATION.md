# Phase 1 local handoff

Updated and verified on 2 October 2026 in `/Users/sregi/Desktop/phase 1/EXITCODE_0`.

## Run and review

The local preview is available at http://127.0.0.1:5050. To restart it:

```sh
cd '/Users/sregi/Desktop/phase 1/EXITCODE_0'
./run_local.sh
```

The launcher uses `database/local_preview.db`. The existing `database/exit_code_0.db` was preserved; it is not the preview database. The original phase 1 files are backed up in `.local-backups/`. Nothing was committed, pushed, or deployed.

Organizer sign-in: `/admin/login`, username `admin`, local default password `exitcode0_admin_2026`. Register a participant team, then start the event from the organizer console to enter the arena. Set `ADMIN_PASSWORD` and a stable `SECRET_KEY` in the environment before the real event; without a stable secret, restarting the process invalidates sessions.

## What changed

- Rebuilt the public pages, registration, waiting room, arena, standings, results, quiz, and organizer console with a black-led, red-accented palette and the original compact technical event layout. The homepage editor program and geometry are preserved; its later authorized animation update is documented below.
- Integrated all seven unique supplied React components with locally bundled React, Motion, OGL, GSAP, and Hugeicons: PatternWaves, RubberSegment, ThoughtLine, BorderGlow, TechText, ElectricLogo, and DotGrid. Borders are clearly visible at rest and intensify near the pointer. Reduced-motion users receive static effects.
- Added code-line selection, local draft recovery, review markers, question navigation with browser history, server-synchronized timing, recoverable request errors, and fullscreen recovery.
- Added separate server-timed quiz scoring, final-results publication, organizer metrics, activity monitoring, submission review, and CSV exports.
- Hardened assignment validation, duplicate submission retries, transactional scoring, stale-session invalidation, and event transitions. Existing activity history migrates without duplication.

Implementation and API responsibilities are described in `README.md`. Core frontend changes are under `templates/`, `static/css/`, and `static/js/`; backend changes are in `app.py`, `database.py`, `event_manager.py`, `scoring.py`, and the new `quiz.py`.

## Verification completed

- **39 pytest tests passed**, including 20 concurrent logical teams with retry/idempotency checks, migration compatibility, quiz isolation, and reset identity.
- **10 critical acceptance scenarios passed** in `verify_critical_scenarios.py`.
- **16 browser journey groups passed** in `scripts/browser_smoke.cjs`: registration, lobby entry, arena, autosave, pause/resume, failed-network recovery, submission, history, fullscreen, leaderboard, organizer review/security, CSV, quiz, publication, reset, and no JavaScript errors.
- **React component checks passed** in `scripts/react_bits_smoke.cjs`: actual WebGL rendering/animation, BorderGlow pointer response, Motion tab dragging and keyboard/assistive activation, reduced motion, mobile layout, no-WebGL fallback, real submission trace, and no React/WebGL/JavaScript errors. `scripts/interaction_smoke.cjs` is a compatibility entry point for this check.
- Arena verified at 1366×768, 1440×900, 1920×1080, 768×1024, and 390×844. Public mobile pages checked at 390×844. Desktop arena fits the viewport; narrow layouts stack and allow vertical scrolling.
- Browser screenshots and the journey report are in ignored `artifacts/`. Browser tests ran against a separate `artifacts/browser-test.db`; Python tests use temporary databases.

Run Python checks with:

```sh
venv/bin/python -m pytest -q
venv/bin/python verify_critical_scenarios.py
```

Browser scripts require Playwright and Chrome. They deliberately require `EXITCODE_E2E=1` because they reset their target event. Only run them against an isolated test database/server (default port 5056), never the real competition server.

## Known boundaries

- The existing Team ID/name login model is retained. It is intended for a supervised lab and is not password-based participant authentication.
- The common 30-question Python/C/Java assignment is preserved. A language selector was not introduced because the existing bank has unequal difficulty distributions and no C++ track; adding one without equivalent question sets would change competition fairness.
- Focus/fullscreen activity is advisory evidence, not an automatic cheating verdict or penalty. Browser restrictions cannot prevent use of another device.
- Logical concurrency was tested locally. Physical lab machines, the college network, and real event load still need an organizer rehearsal.
- The preview uses Flask's development server for local review. This handoff does not claim production deployment or load certification.

## Final redesign checks

- All seven unique supplied effects are mounted and exercised. Repeated BorderGlow attachments are covered by the same component.
- Protected homepage demo markup and `landing.js` are byte-identical to the pre-redesign capture. Its desktop dimensions and interior screenshot pixels match; only antialiased outer corners blend into the new surrounding black background.
- Strong borders were checked for a visible 2px red edge at rest, including reduced motion. Registration, lobby, quiz, results and organizer surfaces use the same treatment.
- Full 16-group competition browser journey, 7-group original React smoke test, additional redesign/interaction checks, and 39 Python tests passed with no browser/React/WebGL errors.
- Responsive public layouts passed at 1440px, 768px, and 390px. The arena flow passed at 1366×768.
- Frame pacing in local headless Chrome: median 16.7ms across hero effects, electric logo, spring tabs/glow, and request trace; 95th percentile 16.7–16.8ms. Two isolated frames exceeded 50ms during control/trace startup. This is a local measurement, not a guarantee for every GPU or device.
- Original competition data was not reset by this redesign. Destructive browser tests used only `artifacts/browser-test.db`; read-only visual tests used the local preview.

The current preview remains at http://127.0.0.1:5050/?ui=black-red. No commit, push, or deployment was performed.

## Subsequent demo-only update

At the user's request, the formerly excluded homepage demo now matches the black/red theme. Its source program, existing markup and dimensions remain unchanged. React/Motion adds a moving line scan, fault emphasis, animated correction, test-progress bar and console transitions; the demo's BorderGlow and PatternWaves now use red.

The demo timer and animation controls pause together, suspend offscreen/when the tab is hidden, and use a static final state for reduced motion. The main title and event-information strip were compared against pre-update captures and remained pixel-identical. No other page, backend, registration flow or competition behavior was changed. Local checks are in `scripts/demo_motion_smoke.cjs`.


## Participant rule update · 2 October 2026

- Isolated browser flow: signed-in lobby → debugging → results → quiz → feedback → blocked-account login. Fullscreen entry and actual browser exits exercised; first warning gates access, second blocks the account persistently.
- Debugging first answer is final; hint/swap penalties and a two-keyword minimum have regression coverage, including concurrent submissions and idempotent retries.
- Quiz review and six 1–5 ratings plus note survive refresh. Organizer feedback panel verified. No browser JavaScript errors.
- All repaired C/Python programs were executed against their expected output. Every reference descriptive answer meets the keyword minimum.
- Versioned bank migration preserves teams, assignments, submission scores and active flags; a subsequent startup preserves organizer edits.
- Run Python verification with `SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX15.4.sdk venv/bin/python -m pytest -q` on this Mac (its default newer SDK linker is incompatible).
- Browser regression: `EXITCODE_E2E=1 TEST_URL=http://127.0.0.1:5057 node scripts/participant_policy_smoke.cjs`, with Playwright available and an isolated server/database. This script resets its target event. Never target participant data.
- Homepage demo source and animation files were unchanged by this update.


## Field review, focus enforcement, and full-page dots · 3 October 2026

- 150 Python tests passed, including field-level verdicts, saved-answer isolation, migration, focus-departure deduplication, delayed entry/status handling, and offline departure reconciliation.
- Answer fields show independent automated verdicts and points in the arena, after refresh, and in the post-round results disclosure. Field scoring is before hint/swap deductions, Double Commit and organizer total overrides.
- Fullscreen exits, window blur and hidden-tab signals share one departure limit. Reentry must be explicit, visible and focused. Correlated signals count once. Browser signals cannot prevent OS application switching; managed kiosk/exam software is required for that.
- `scripts/focus_policy_smoke.cjs` deterministically exercises window blur while fullscreen and a hidden-tab event. These event tests simulate focus/visibility transitions; they do not claim to lock the OS or test every operating system's shortcut behavior.
- The existing DotGrid now covers the viewport across public pages, with 1,600 dots maximum, DPR capped at 1.5, no continuous drawing while idle, and static reduced-motion/no-JavaScript fallbacks. Original demo code and animation controllers are preserved.

## Event workflow and single-line corrections · 4 October 2026

- 206 Python tests passed. All 30 corrected C/Python programs compile/run to their expected output.
- Registration closes atomically when debugging starts; existing teams can log in during later rounds. Waiting-room fullscreen/focus signals cannot produce violations.
- Debugging now has four scored fields: location 10%, type 15%, output 20%, corrected code line 55%. Root cause is retired. Code comparison preserves operators, literals, case and meaningful Python indentation without executing participant code.
- Easy/Medium/Difficult questions award 20/25/35 points. A stable shared shuffle places one difficult question within each six-question block.
- Versioned migration preserves historical scores, review denominators and active/completed assignments; untouched waiting teams adopt the new order.
- Expanded participant_policy_smoke.cjs passed: stale registration closure, four-field answers, hint penalty, refresh persistence, highlighted Rapid Fire link, feedback-first completion, feedback CSV, removed organizer nav links, unban after publication, modal and blocked-page recovery, and no browser JavaScript errors.
- focus_policy_smoke.cjs passed: pre-event focus changes ignored, active-round blur warning, correlated-event deduplication and a second simulated hidden-tab departure blocking the account.
- Organizer feedback CSV includes team identifiers, all six ratings, optional notes and submission time; formula-like text is escaped for spreadsheet safety.
- Existing preview data was backed up before migration. No commits or pushes were made.

## Persistent fullscreen and clearer questions · 4 October 2026

- **239 Python tests passed**, including per-team shuffling, sequential unlocking, stable assignments, private locked prompts, historical source snapshots, and unban during a concurrent status poll. All 30 canonical single-line repairs compile/run with the expected output; logical-error samples produce a different output before correction.
- Each team receives its own persisted shuffle, with one Difficult question per six. Existing active/completed assignments remain unchanged. Untouched waiting teams receive the new order during the versioned migration.
- Replaced Q23, Q24 and the ambiguous Q26 precedence question. Added a visible intended-behavior statement to every question, clarified output after the fix, and repaired the extra fall-through in Q14 so one line fixes it.
- `scripts/navigation_smoke.cjs` passed in Chrome: arena/leaderboard/home navigation, URL aliases, Back/Forward, question history, draft and answer persistence, stopped page pollers, persistent dot host, queued navigation, failed navigation, end-of-round redirects, results/quiz transitions and zero page errors. The same document and fullscreen element remained active throughout; real exits and simulated app-focus departures still warn/block.
- `scripts/participant_policy_smoke.cjs` passed with a randomly assigned first question: registration gating, saved answers, hint deductions, all quiz answers, feedback and CSV, publication, blocking and organizer unban. The run exposed and verified a fix for an inactive/blocked status read race during unban.
- `scripts/focus_policy_smoke.cjs` passed. Blur while fullscreen warns once; a hidden-tab signal after reentry blocks. These simulated focus signals do not claim OS-level application locking.
- React bundle rebuilt and 22 JavaScript files syntax-checked. Homepage demo component/animation source remains unchanged; React roots now unmount/remount with page navigation while the shared dot field stays alive.
- Local preview backup: `.local-backups/preview-before-fullscreen-navigation-20261004-124639.db`. After restart, teams, scores, all 30 answers, assignments, event state and competition controls matched the backup. All 30 historical reviews retain their original public question source. No reset, commit or push was performed.
- Full browser reloads and external/new-tab navigation still require explicit fullscreen entry, because they leave the persistent document.

## Fixed power-up points, event feedback and organizer sessions · 4 October 2026

- **262 Python tests passed**, plus all 10 scenarios in `verify_critical_scenarios.py`. New coverage checks immediate tool changes, concurrent/repeated activation, no charge on a failed swap, no duplicate deductions after answers or judging, saved-score migration, organizer cookie isolation, and correct quiz answers revealed only after that team's attempt locks.
- New tool uses change the team total immediately: hint −5, question change −7, Double Commit +15. Question awards remain separate. Totals can be negative; zero → −5 → −12 → +3 → 23 after all tools and a correct 20-point answer. Recorded old scores and pending uses under the earlier rules remain intact.
- `scripts/powerup_smoke.cjs` passed in Chrome: visible totals, a delayed stale progress response, swap, bonus, rejected retries, reload, answer submission and leaderboard agreement. The arena refresh now discards polls started before a confirmed score change.
- `scripts/admin_session_smoke.cjs` passed with two tabs sharing one browser context: participant registration, repeated focus/visibility changes, participant logout/login, organizer refresh and explicit organizer logout. Organizer and participant cookies use separate names/signatures; organizer event/leaderboard polls use protected organizer endpoints.
- Updated `scripts/participant_policy_smoke.cjs` passed: wrong selected option red, correct option green immediately and in saved review, six event-wide feedback ratings, direct feedback-to-leaderboard navigation without a quiz-entry page, feedback CSV, publication, fullscreen enforcement and unban. No browser page errors.
- The correct-option display lasts 1.4 seconds before advancing; the server's existing quiz deadline continues during that feedback. Unanswered question answer keys remain private.
- Local preview backup: `.local-backups/preview-before-fixed-powerups-20261004-231419.db`. Teams, all 30 saved answers, scores, assignments, event state, controls, feedback and pre-existing power-up fields were verified unchanged after migration. Historical tools received no additional charge.
- JavaScript syntax and Git whitespace checks passed. No event reset, commit or push. The preview runs on port 5050; organizers sign in once to establish the new separate cookie.

## Conditional Double Commit · 4 October 2026

- **287 Python tests passed** and all 10 critical acceptance scenarios passed. Double Commit now arms without awarding points; a completely correct answer earns the normal question points plus 15, while any partially correct or incorrect answer earns zero for that question. Hint and swap retain their immediate −5/−7 team costs.
- Tests cover each incorrect field at all three point values, concurrent activation, immutable retries, persistence, question replacement, individual correctness reviews, and organizer override limits. Pending bonuses from the prior immediate rule are removed and rearmed; completed historical awards remain unchanged.
- `scripts/powerup_smoke.cjs` passed in Chrome: unchanged total while armed, correct award after a perfect answer, zero after a partial answer, refresh/retry handling, saved review messages and leaderboard totals. No browser page errors. JavaScript syntax and whitespace checks passed.
- Local preview restarted on port 5050 after backup to `.local-backups/preview-before-conditional-double-20261004-233145.db`. Teams, all 30 submissions, scores, power-ups, assignments, event state, controls and feedback match the backup. No event reset, commit or push.
