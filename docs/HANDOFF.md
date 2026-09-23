# Ming handoff brief
2026-09-21 · for the next build session with Claude

## What Ming is
Quant Ming ("Ming"): a news-driven, long-only paper trading analyst with a face. Sol (OpenAI `gpt-5.6-sol`) writes theses; a rules engine sizes, places, and protects; the operator (Caden) runs everything from one 1920x1080 page and can talk to Ming by voice. Repo: github.com/realbenjibot-hub/ming. Live: ming-production-6fc2.up.railway.app (Railway, Hobby, volume at /data). Login: any username, password is DASHBOARD_PASSWORD in Railway.

## Books and hold mode (added 09-22)
`hold_mode` in config.yaml is `day`, `swing`, or `both` (default both); the chat can flip it (set_setting hold_mode) and so can POST /api/config. In both mode capital_cap is split by `books.day_share_pct` (50) and Sol tags every 6:00 and 9:00 thesis with a horizon, day or swing; a name can sit in one book only. Day book: hunt every 30 minutes 10:00 to 15:00 (last 90 minutes of news and movers, new theses, entries; skips the 11:30 to 13:30 pause), scan every 5 minutes (stops, targets, trail, no LLM), flatten at 15:55. Swing book: 15:45 review with LLM invalidation, holds overnight. Exits are set per trade at entry from the stock's average daily range (ATR percent over 14 days) and stored on the trade: day stop 0.5x ATR, target 1x; swing stop 1.5x, target 4x; clamped in config; the book's fixed numbers when there is no data. The research pack now carries gap, relative volume, range, and ATR next to movers and news names. State, report, and the page show per-book return, edge, open, closed, hit rate. `ming/daytrading.md` is read into the persona whenever a day book runs. Caden chose both on 09-22 to compare the two over the trial. The NVO trade from 09-21 was migrated into the swing book.

## Clock bug found 09-22 (fixed)
Every cron job had been firing in UTC, four hours early: research at 02:00 ET, execute at 05:35 ET with the market closed, flatten at 11:55 ET. The scheduler was given the Eastern timezone but each CronTrigger was built without one and took the container clock. Fixed by passing timezone to every trigger; the startup log line now prints the next four job times in ET so this is visible. The 09-21 handoff line "all fired on time" was judged from UTC timestamps on the page and was wrong. A 09:31 sweep now sells any day-book position held overnight. 09-23 is the first clean day-mode day.

## Options (09-23, Caden's override of the no-options rule)
Caden asked for options day trading and reaffirmed it after the risk was laid out. Claude logged it as an operator override. Scope: long calls and long puts only, day book only, paper. `day.instrument: options` in config.yaml; `options:` block holds the picker and exit rules. The engine picks the contract (expiry closest to 7 days inside 3 to 14, strike closest to delta 0.45, open interest at least 100, spread at most 10 percent, premium at least 0.30); Sol names the stock and the direction (up or down). Size is 10 percent of the cap in premium. Stops on options live in the engine and are checked by the scan every 2 minutes (Alpaca has no stop orders on contracts). Stock exits still use broker stops. `day.instrument: stock` puts it back. The two-week trial restarts on options from 09-23.

## Rules that stand
Long only, meaning bought shares, calls, or puts; never short, never written options, never spreads. Paper first; live is double-locked (mode: live in config.yaml AND LIVE_CONFIRM=YES). Kill switch stays. The LLM never sizes or places an order. Operator direction shapes theses and can request a name; the rules engine still sizes it and sets the stop; kill, resume, execute, and operator trades need a confirmed yes.

## Scene and layout rules (do not undo)
One 1920x1080 frame, scaled and letterboxed, no scrolling. Readouts in the dark around him, no cards. Only green day and red day carry color; everything else neutral white. Screen light is the soft beam (a full-screen-height version was tried and rejected). No grain, no zoom, no blink, no keyboard glow, no chart on the monitor, no oval light blobs, limbs on one clock. Typing is fast and hard (cadence 0.22 s). Fonts: Instrument Sans + JetBrains Mono. Demo of the page with mock data: https://claude.ai/artifact/4fM4oJjZSULMvmsbzrV2nB (rebuild from static/index.html: inline the nine scene images, swap api() for the mock, add the mood strip; publish over the same link).

## Status as of 2026-09-21 16:00 UTC
- Deployed and running on schedule: 6:00 research, 9:00 refresh, 9:35 execute, 15:45 review, 16:05 report, ET weekdays. All fired on time on 09-21.
- Brain connected (OpenAI key replaced 09-21 morning after a 401). Broker Alpaca paper, $100,000 account, capital cap $10,000.
- Volume attached 09-21 ~14:09 UTC. Journal has survived redeploys since.
- First position: BUY 75 NVO @ 39.89, stop 36.70, conviction 6, 09-21 15:49 UTC. Taken because the dial was at 10 (Full send: bar conviction 5, 8 positions, 30 percent per name, 8 percent daily cap). Claude recommends 5 (Balanced) after this position closes; Caden's call.
- Bugs fixed 09-21: manual runs were labeled as the 6:00 pass; feed timestamps were UTC not ET. Both made him treat real news as the future. 09-22 6:00 is the first run with the fix.
- 09-22: both books shipped, then Caden chose day only for the two-week trial: hold_mode day, capital_cap 20000 in config.yaml, dial 6 set by Caden on the page. NVO stays in the swing book under its broker stop; the 15:45 review still runs in day mode and manages it (trail, target, invalidation). Swing gets its own two weeks after.
- Open question from Caden: raise capital_cap to the real amount he intends to trade after the trial (he liked the idea). Not yet set.

## Working method
Claude has a fine-grained GitHub token (Contents read/write, this repo only, 90 days from 09-21) and pushes directly; Railway redeploys in ~2 minutes. Claude renders the page headless (Playwright) before publishing UI changes. Claude can query the live API with the dashboard password. Keys never go into chat; they live in Railway Variables. Two keys pasted in chat on 09-19/20 (Alpaca paper pair, first OpenAI key) should be treated as burned; the OpenAI one was replaced.

## Open items, in order
1. Voice: the Realtime session mints correctly server-side; the browser end (WebRTC) has not been exercised by Caden yet. First test: click the mic, allow it, say hello.
2. capital_cap to the real intended amount.
3. Dial back to 5 after NVO closes (recommendation).
4. Watch the 09-22 6:00 and 9:00 runs; read the 16:05 report in the chat.
5. operator.md still has five unanswered questions for Caden (involvement level, disagreement handling, off-limits names, definition of a good two weeks, what Ming calls him).
6. Done 09-21 evening: brain status is now absent, connected, or rejected. OpenAI is probed at startup (no tokens) and every 401 flips the status to rejected; /health, /api/state, the page, the chat, and the log all say so.
7. Two-week scorecard at the end: edge vs SPY, hit rate and win-to-loss size, thesis quality, exit reasons, days sat out. Then the live decision.

## Prompt for the new session
Continue the Ming project. Read docs/HANDOFF.md in the repo first (github.com/realbenjibot-hub/ming). Ming is live on Railway on paper; you push to the repo with the token Caden gives you and Railway redeploys. Rules that stand are in the handoff. Start every response with Confidence and Knowledge scores, plain language, no contractions, no emdashes, one most effective next action at the end. Next step is [voice test / set capital cap / review 09-22 runs].
