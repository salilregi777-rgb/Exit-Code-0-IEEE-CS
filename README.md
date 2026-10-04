# EXIT CODE 0

A live competitive debugging platform for **IEEE Computer Society**, Amrita Vishwa Vidyapeetham, Bengaluru. **7 October 2026, 2:50–4:00 PM IST · 40-minute debugging round · 7-minute quiz · teams of 2–3.**

**Analyse → Debug → Fix → Exit 0**

Flask, Jinja, and SQLite continue to control scores, assignments, deadlines, and permissions. The supplied React components now run as small interactive mounts inside those pages, with React, Motion, OGL, GSAP, and Hugeicons bundled locally. No runtime CDN is required.

## Run locally

The working project is `/Users/sregi/Desktop/phase 1/EXITCODE_0`.
For this Mac, start the reviewed build with:

```sh
cd "/Users/sregi/Desktop/phase 1/EXITCODE_0"
./run_local.sh
```

Open **http://127.0.0.1:5050**. The script uses `database/local_preview.db` and preserves the original `database/exit_code_0.db`. Port 5050 avoids macOS AirPlay's common use of port 5000. The existing `venv` is supported; the generated frontend bundle is included, so Node is only needed when editing the React components.

For a fresh checkout:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
./run_local.sh
```

With `python app.py` directly, the default address is **http://127.0.0.1:5000**. The organizer console is **http://127.0.0.1:5000/admin/login**.

Local organizer defaults are username `admin`, password `exitcode0_admin_2026`. Set `ADMIN_PASSWORD` and a stable, private `SECRET_KEY` before an actual event. Without `SECRET_KEY`, the app generates a fresh secret on each process start; restarting then signs out existing sessions. A stable secret preserves session cookies across restarts.

```sh
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD='your-private-organizer-password'
export SECRET_KEY='your-long-random-private-secret'
python app.py
```

`PORT` defaults to `5000`; `DATABASE_PATH` defaults to `database/exit_code_0.db`. A database is created and seeded on first run. Existing databases receive additive migrations. Back up an existing event database before upgrading. The development server binds to localhost and does not expose debug tracebacks by default. For a college LAN, configure an appropriate production WSGI server and network access separately.

## Participant experience

- Responsive event homepage with an illustrative debugger, live registration count, real standings, and reduced-motion support.
- Team registration and existing Team ID/name sign-in, with inline validation and registration confirmation.
- Live waiting room that enters the arena when organizers start or resume debugging.
- Full-height arena with sequential question navigation, syntax-highlighted read-only code, clickable error-line numbers, review markers, partial-credit response fields, and all three existing power-ups.
- Unsent drafts saved locally per event generation, team, and question. Successful submissions clear their draft. Local storage contains participant text and review markers only.
- Server-confirmed submission feedback, idempotent retries after connection loss, live progress, connection status, and synchronized countdown display.
- Before the event starts, teams can register and use the waiting room without fullscreen or focus violations. Starting debugging closes new registrations, including forms already open; existing teams can still sign in. Fullscreen is required before attempting questions and on participant pages through both rounds until logout. The first fullscreen exit, app switch, or tab switch warns; a second distinct departure persistently blocks the team. One departure is counted once even if the browser reports several signals. Unsupported browsers must switch to a fullscreen-capable desktop browser. Fullscreen signals are browser-reported; this is not kiosk software.
- Live leaderboard, own-team highlighting, rank movement, provisional/frozen states, and explicitly published final results.
- Separate rapid-fire quiz after debugging. **Quiz points never enter debugging scores or final ranking.**

### Question bank and scoring

Teams receive **30 questions** in an independently shuffled order saved for each team, with sequential unlocking and one difficult question in each group of six. Refreshing or restarting the server does not reshuffle an existing assignment. Easy questions award **20 points**, Medium **25**, and Difficult **35**. Rounds already started keep their saved assignments and scores. The bank contains 15 Python questions and 15 C questions, focused on first-year programming fundamentals. Each question states its intended behavior; expected output is what the program prints after the single-line fix. The ten-question rapid-fire quiz also uses C and Python only. All teams use the same mixed-language track.

Debugging uses four scored fields:

| Component | Base score share |
| --- | ---: |
| Error type | 15% |
| Error location | 10% |
| Expected output | 20% |
| Corrected line of code | 55% |

Each debugging answer is final after its first submission, even when incorrect. A retry with the same request ID returns the original result. Correction must be the complete corrected version of the mistaken line, entered as one line of code. Operators, punctuation, literals, and meaningful Python indentation are checked; prose and keyword matches do not earn correction credit. No root-cause answer is requested. Question awards are separate from fixed power-up changes to the team total. Participants see correct, partial, or incorrect status and their saved debugging answers; reference debugging answer keys remain private.

Power-ups remain one use per team. **Rubber Duck costs 5 points** and **Git Revert costs 7 points**, deducted from the team total immediately and saved atomically with use of the tool. The total can be negative. **Double Commit is armed for the selected question and changes the score only on submission: a 100% correct answer earns the question’s normal points plus a 15-point bonus; any incorrect or partially correct part means 0 points for the whole question.** Individual checks still show which parts were correct. For example, using hint and swap moves a zero score to −12; arming Double Commit leaves it at −12. A perfect 20-point answer then earns 35, making the total 23; a partially correct answer earns 0, leaving the total at −12. Retries and refreshes never apply a tool twice, and organizer overrides retain the separate hint/swap costs. Completed historical submissions keep their saved awards. An unsubmitted Double Commit activated under the earlier immediate-bonus rule is rearmed for its question and its upfront bonus removed so it follows the conditional rule.

## Organizer workflow

1. Open **Overview** to inspect real team counts, recent activity, completion, and average debugging score.
2. Use **Teams** and **Questions** to search the roster and question bank. Inspecting answers is restricted to the organizer console.
3. In **Event control**, start debugging. Pause preserves remaining server time; resume continues it. Ending requires confirmation and locks submissions.
4. In **Submissions**, review participant responses and apply bounded score adjustments with a reason.
5. Inspect **Security** for focus losses, tab switches, fullscreen exits, and last activity after the event starts. Two departures block the account. Use **Unban team** in Security or Teams to restore access with zero violations and preserve answers and scores. The participant can then re-enter fullscreen; old queued departure reports cannot reinstate the cleared ban. Unbanning also restores public ranking eligibility, including after results are published.
6. After debugging ends, open **Quiz**. The quiz has 10 questions, a 7-minute session deadline, and 42-second server-controlled question deadlines. Refreshing does not reset them.
7. Verify debugging standings, then **Publish final results** in **Leaderboard**. The participant results page shows final rank and podium only after publication.
8. Export roster/results and **Feedback** ratings/notes as CSV. In **Settings**, reset only after exporting anything needed. Reset requires typing **RESET EVENT** and clears quiz/activity data as well as competition progress.

Event duration and metadata live in `data/config.json`. Question content lives in `data/questions.json`; changing this seed file does not overwrite existing database questions. The Settings panel reports actual configuration rather than presenting nonfunctional editing controls.

## Security and compatibility

Existing route names and aliases remain supported. Participant identity comes from the session; APIs do not accept another team's ID or an official score. Locked/abandoned questions, inactive teams, invalid event transitions, malformed payloads, and expired submissions are checked by the server. Scoring writes and power-up consumption use transactions. An event generation invalidates stale sessions and namespaces local drafts after reset.

Mutation requests reject cross-site Origin/Fetch metadata. Session cookies are HttpOnly and SameSite=Lax; sensitive responses use `Cache-Control: no-store`. Participant-facing errors omit raw Python exceptions. Export cells that could be interpreted as spreadsheet formulas are escaped.

**Existing Team ID/name login is preserved.** It is a trusted-lab access model, not password-based participant authentication. Anyone who knows a team's identifier can sign in as that team. Use supervised lab access or add organizer-issued participant credentials before using this outside that model.

## Tests and verification

```sh
.venv/bin/python -m pytest -q
```

The test suite covers the original event, scoring, route, database, and power-up behavior, plus separate quiz scoring, timeout persistence, idempotent retries, assignment protection, publication, stale sessions, and request validation. Tests use a temporary database and do not erase the running event.

See `VERIFICATION.md` for the final local browser coverage and any remaining manual checks. Browser screenshots are generated into the ignored `artifacts/` directory.

## Code layout

- `app.py`: existing routes plus progress, activity, quiz, publication, and organizer data APIs.
- `database.py`, `database/schema.sql`: persistence, migrations, assignment and activity helpers.
- `event_manager.py`, `scoring.py`: event lifecycle and authoritative scoring.
- `quiz.py`: server-only quiz bank, deadlines, answer validation, and isolated scoring.
- `templates/base.html`: accessible shared shell, navigation, confirmation dialog and notifications.
- `templates/arena.html`, `static/css/arena.css`: competition workspace.
- `static/css/style.css`: shared tokens and components; `public.css` and `admin.css` scope page-specific layouts.
- `static/js/main.js`, `timer.js`: shared request, modal, toast, storage, and event polling utilities.
- `static/js/editor.js`, `arena.js`, `participant-guard.js`: source interaction, submissions/drafts, and fullscreen/activity behavior.
- Public page scripts handle registration, lobby, standings, homepage demonstration, quiz, and results separately.

All work can be reviewed locally. No push or deployment is required.

## Supplied React components

The actual supplied components are in `frontend/components/`, with companion CSS added for this event. The interface follows the original compact debugging-event layout in a black-led, red-accented 75:25 design. `static/css/black-red.css` applies this to the public pages, arena, quiz, results and organizer console. The homepage editor demo keeps its original program, markup and dimensions. A subsequent demo-only update now matches the black/red palette and adds a line scan, animated fault-to-fix transition, test-progress bar, and matching BorderGlow. The rest of the design is unchanged.

All seven unique supplied features are integrated (the repeated BorderGlow attachments refer to the same component):

- **TechText:** the homepage EXIT CODE 0 title, including draggable letters, scanning outlines, selection labels, and spring return.
- **ElectricLogo:** a red electrical /0 mark in the homepage event banner and participant lobby, including pointer and click pulses.
- **DotGrid:** GSAP-powered responsive dots behind the homepage title and registration story, with inertia, elastic return, and click shockwaves.

- **PatternWaves:** the supplied OGL shader and ripple simulation behind the landing editor. Reduced motion stays static; hidden/offscreen rendering pauses, and unsupported WebGL2 receives a dotted fallback. The existing “Pause demo” control also pauses the waves.
- **RubberSegment:** the supplied Motion spring/stretch/flick control for registration and login. It supports drag, keyboard, assistive activation, and tab-panel semantics.
- **ThoughtLine:** the supplied Motion shimmer, elapsed display, and collapsible trace tied to actual arena requests. It shows local validation, waiting for the server, and the server outcome.
- **BorderGlow:** persistent visible 2px red edges on event cards, registration, lobby, standings, quiz, results, and organizer panels; pointer movement adds a brighter directional highlight. The homepage editor now uses the same red glow, drawn inside its clipping edge.

The JSX retains the supplied rendering and animation logic, with small accessibility and lifecycle fixes. `frontend/react-bits.jsx` connects it to the Flask-rendered forms and existing arena API. `static/js/interactions.js` is only a submission-status fallback if the React asset cannot load.

To rebuild after editing JSX/CSS, use Node and pnpm:

```sh
pnpm install --frozen-lockfile
pnpm build
# Optional, while editing:
pnpm watch
```

The pinned packages and lockfile make builds reproducible. `frontend/build.mjs` produces `static/dist/react-bits.js`, its CSS, and third-party legal notices; keep those generated files with the project so `./run_local.sh` needs no Node process. The integration follows [React's existing-project approach](https://react.dev/learn/add-react-to-an-existing-project).

Previous phase 1 files are preserved in `.local-backups/`. Existing `activity_events` history migrates into the organizer monitor without duplication, and the legacy activity payload remains accepted. No Git push was performed.

## Black/red redesign verification

`VERIFICATION.md` records the final checks. `scripts/redesign_smoke.cjs` checks responsive layouts, actual component rendering and the authorized demo enhancement. `scripts/demo_motion_smoke.cjs` checks the demo sequence, pausing, offscreen suspension, reduced motion, mobile layout, and surrounding pixels against a locally captured baseline. `scripts/animation_pacing.cjs` records frame intervals while the seven effects run. Both use only the local preview and do not reset the competition. The animation report is generated at `artifacts/animation-performance.json`.

All continuous canvas effects suspend when hidden/offscreen and honor reduced-motion preferences. DotGrid draws only when changed; expensive electric-logo rasterization is cached and scheduled separately from interaction. Canvas resolutions and dot counts are bounded. The local results do not replace testing on the actual lab hardware.

### Quiz review and feedback
The ten rapid-fire questions use only C and Python and focus on first-year fundamentals. Once an answer locks, the correct option is highlighted green and a wrong selected option red; both remain available in saved review. Unanswered question keys stay private. After completion, teams submit six required ratings (1–5) covering debugging and quiz clarity, difficulty, interface/powerups, round timings, enjoyment and the overall event, plus an optional note of up to 2,000 characters. Organizers can read and export responses in **Feedback**. After feedback, the action leads directly to the debugging leaderboard and published winners. Feedback and quiz points do not alter debugging rankings.

Organizer authentication uses a separate signed cookie from participant authentication. Switching browser tabs, participant sign-in/out, fullscreen violations and participant session expiry cannot clear organizer access. Organizer status and leaderboard polls use protected organizer endpoints. Explicit organizer sign-out still ends that session. Existing organizers must sign in once after this cookie-separation update.

Question-bank updates use a versioned migration. Existing teams, submissions, scores and question active flags are preserved. Untouched waiting teams adopt the new per-team order; assignments in rounds already started remain unchanged. Completed reviews retain the source, title, task and points originally answered. Historical scores are not automatically regraded against revised questions.

Signed-in participant navigation between home, leaderboard, arena, waiting room, results and quiz keeps the same document in fullscreen. Page polling, listeners and React effects are cleaned up on departure; the shared fullscreen guard and dot field stay mounted. Actual fullscreen exits and focus departures still count. A browser reload, an external page or opening a new tab requires explicit fullscreen entry again because those actions create or leave the document.
