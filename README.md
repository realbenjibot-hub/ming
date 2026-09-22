# Ming

Quant Ming: a news-driven, long-only paper trading analyst with a face. Sol (OpenAI) writes theses; a rules engine sizes, places, and protects; the operator runs everything from one 1920x1080 page and can talk to Ming by voice.

Rules that stand: long only, paper first, kill switch stays, the LLM never sizes or places an order.

## Layout
- `app.py` FastAPI, HTTP Basic auth, scheduler. Swing mode: 6:00 research, 9:00 refresh, 9:35 execute, 15:45 review, 16:05 report. Day mode: same morning, then a hunt every 30 minutes 10:00 to 15:00, a scan every 5 minutes, flatten at 15:55, report 16:05. Both mode (the default): both books at once with the capital cap split, Sol tags each thesis day or swing. ET weekdays. `hold_mode` in `config.yaml` picks; the chat can flip it.
- `main.py` CLI for the same commands
- `config.yaml` every rule, the ten aggression presets, the source list, model names
- `bot/` broker (Alpaca or fake), research, analyst, risk, executor, journal (SQLite on `DATA_DIR`), report, chat, voice, tools, ideas
- `ming/` `ming.md` (who he is), `operator.md` (who he works for), `daytrading.md` (how he trades in day mode), `ideas.md` (seed for the operator's standing direction; the live copy lives on `DATA_DIR`)
- `static/index.html` the stage; `static/scene/` the nine image layers

## Run locally
```
pip install -r requirements.txt
cp .env.example .env   # fill it in, or leave keys out to run with the fake broker and no brain
export $(grep -v '^#' .env | xargs)
uvicorn app:app --port 8080
```
Open http://localhost:8080, any username, the dashboard password.

`MING_FAKE_BROKER=1` forces the in-memory broker. `MING_NO_SCHED=1` disables the scheduler. `python main.py research|execute|review|hunt|scan|flatten|report|status|kill|resume`.

## Day mode and real money
Day mode closes everything at 15:55 ET, so there is no overnight risk. On a real account under $25,000, FINRA's pattern day trader rule limits you to three round trips in five business days; paper does not enforce it. Check the current threshold before the live decision.

## Going live later
Fund a live Alpaca account with the `capital_cap` amount, swap the two Alpaca keys, set `LIVE_CONFIRM=YES`, change `mode: paper` to `mode: live` in `config.yaml`, redeploy, click resume to reset the baselines. Both locks are required; either one alone keeps it on paper.
