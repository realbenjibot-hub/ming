# Ming

Quant Ming: a news-driven, long-only paper trading analyst with a face. Sol (OpenAI) writes theses; a rules engine sizes, places, and protects; the operator runs everything from one 1920x1080 page and can talk to Ming by voice.

Rules that stand: long only, paper first, kill switch stays, the LLM never sizes or places an order.

## Layout
- `app.py` FastAPI, HTTP Basic auth, scheduler (6:00 research, 9:00 refresh, 9:35 execute, 15:45 review, 16:05 report, ET weekdays)
- `main.py` CLI for the same commands
- `config.yaml` every rule, the ten aggression presets, the source list, model names
- `bot/` broker (Alpaca or fake), research, analyst, risk, executor, journal (SQLite on `DATA_DIR`), report, chat, voice, tools, ideas
- `ming/` `ming.md` (who he is), `operator.md` (who he works for), `ideas.md` (seed for the operator's standing direction; the live copy lives on `DATA_DIR`)
- `static/index.html` the stage; `static/scene/` the nine image layers

## Run locally
```
pip install -r requirements.txt
cp .env.example .env   # fill it in, or leave keys out to run with the fake broker and no brain
export $(grep -v '^#' .env | xargs)
uvicorn app:app --port 8080
```
Open http://localhost:8080, any username, the dashboard password.

`MING_FAKE_BROKER=1` forces the in-memory broker. `MING_NO_SCHED=1` disables the scheduler. `python main.py research|execute|review|report|status|kill|resume`.

## Going live later
Fund a live Alpaca account with the `capital_cap` amount, swap the two Alpaca keys, set `LIVE_CONFIRM=YES`, change `mode: paper` to `mode: live` in `config.yaml`, redeploy, click resume to reset the baselines. Both locks are required; either one alone keeps it on paper.
