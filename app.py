"""Ming: one process. FastAPI serves the stage, the API, the chat, and the voice handshake; APScheduler runs the day inside the same process."""
import os, secrets, datetime as dt
from zoneinfo import ZoneInfo
from fastapi import FastAPI, Depends, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from bot.services import Services
from bot.chat import Chat
from bot import voice as voicemod
from bot.tools import dispatch
from bot.config import ROOT

svc = Services()
chat = Chat(svc)
app = FastAPI(title="Ming")
security = HTTPBasic()
PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")

def auth(c: HTTPBasicCredentials = Depends(security)):
    if not PASSWORD or not secrets.compare_digest(c.password.encode(), PASSWORD.encode()):
        raise HTTPException(401, "wrong password", headers={"WWW-Authenticate": "Basic"})
    return True

@app.get("/health")
def health(): return {"ok": True, "busy": svc.busy, "brain": svc.llm.status, "broker": svc.b.name, "mode": "live" if svc.cfg.live else "paper"}

@app.get("/", dependencies=[Depends(auth)])
def index(): return FileResponse(ROOT / "static" / "index.html")
app.mount("/scene", StaticFiles(directory=ROOT / "static" / "scene"), name="scene")

@app.get("/api/state", dependencies=[Depends(auth)])
def state(): return svc.state()
@app.get("/api/theses", dependencies=[Depends(auth)])
def theses(day: str = None): return svc.theses(day)
@app.get("/api/trades", dependencies=[Depends(auth)])
def trades(n: int = 100): return svc.j.all_trades(n)
@app.get("/api/log", dependencies=[Depends(auth)])
def log(n: int = Query(50, le=500)): return svc.j.logs(n)
@app.get("/api/reports", dependencies=[Depends(auth)])
def reports():
    rows = svc.j._q("SELECT key FROM meta WHERE key LIKE 'report:%' ORDER BY key DESC")
    return [r["key"][7:] for r in rows]
@app.get("/api/report/{day}", dependencies=[Depends(auth)])
def report(day: str): return PlainTextResponse(svc.j.get(f"report:{day}", "no report"))
@app.get("/api/ideas", dependencies=[Depends(auth)])
def ideas(): return PlainTextResponse(svc.ideas())
@app.get("/api/aggression/presets", dependencies=[Depends(auth)])
def presets(): return svc.cfg.AGGRESSION

@app.post("/api/run/{command}", dependencies=[Depends(auth)])
def run(command: str):
    try: return {"ok": True, "result": svc.run(command)}
    except RuntimeError as e: raise HTTPException(409, str(e))
    except ValueError as e: raise HTTPException(400, str(e))
    except Exception as e:
        svc.j.log("ERROR", f"{command} failed: {type(e).__name__}: {str(e)[:200]}"); raise HTTPException(500, f"{type(e).__name__}: {str(e)[:200]}")
@app.post("/api/config", dependencies=[Depends(auth)])
async def config(req: Request):
    body = await req.json(); out = None
    for k, v in body.items():
        try: out = svc.set_hold_mode(v) if k == "hold_mode" else svc.set_config(k, v)
        except ValueError as e: raise HTTPException(400, str(e))
    return {"ok": True, "config": out}
@app.post("/api/aggression", dependencies=[Depends(auth)])
async def aggression(req: Request):
    level = int((await req.json()).get("level", 5))
    try: name = svc.set_aggression(level)
    except ValueError as e: raise HTTPException(400, str(e))
    return {"ok": True, "level": level, "name": name}
@app.post("/api/ideas", dependencies=[Depends(auth)])
async def add_idea(req: Request):
    return PlainTextResponse(svc.add_idea((await req.json()).get("text", "")))
@app.post("/api/chat", dependencies=[Depends(auth)])
async def chat_ep(req: Request):
    hist = (await req.json()).get("history", [])
    return {"reply": chat.reply([{"role": h["role"], "content": h["content"]} for h in hist if h.get("role") in ("user", "assistant")])}
@app.post("/api/voice/session", dependencies=[Depends(auth)])
def voice_session(): return voicemod.session(svc.cfg, svc)
@app.post("/api/voice/tool", dependencies=[Depends(auth)])
async def voice_tool(req: Request):
    b = await req.json(); return JSONResponse(dispatch(svc, b.get("name"), b.get("arguments") or {}))

# ---- scheduler: weekdays, Eastern ----
sched = BackgroundScheduler(timezone=svc.cfg.tz)
def _job(cmd, only_mode=None, window=None):
    """window=(start, end) in HH:MM ET limits an interval job to part of the day; only_mode limits it to one hold mode."""
    def f():
        if only_mode and only_mode not in svc.cfg.books: return
        if window:
            now = dt.datetime.now(ZoneInfo(svc.cfg.tz)); hhmm = now.strftime("%H:%M")
            if now.weekday() > 4 or not (window[0] <= hhmm < window[1]): return
        try: svc.run(cmd)
        except RuntimeError as e:
            if cmd not in ("scan",): svc.j.log("WARN", f"scheduled {cmd} skipped: {e}")
        except Exception as e: svc.j.log("ERROR", f"scheduled {cmd} failed: {type(e).__name__}: {str(e)[:200]}")
    return f
def _cron(hhmm):
    h, m = hhmm.split(":"); return CronTrigger(day_of_week="mon-fri", hour=int(h), minute=int(m))
SC, DAY = svc.cfg.schedule, svc.cfg.day
for cmd in ("research", "refresh", "execute", "report"):
    sched.add_job(_job(cmd), _cron(SC[cmd]), id=cmd, misfire_grace_time=600)
sched.add_job(_job("review"), _cron(SC["review"]), id="review", misfire_grace_time=600)   # every mode: in day mode it manages anything left from swing
sched.add_job(_job("flatten", only_mode="day"), _cron(SC.get("flatten", "15:55")), id="flatten", misfire_grace_time=240)
sched.add_job(_job("scan", only_mode="day", window=("09:36", SC.get("flatten", "15:55"))), IntervalTrigger(minutes=int(DAY.get("scan_every_min", 5))), id="scan", misfire_grace_time=60)
sched.add_job(_job("hunt", only_mode="day", window=(DAY.get("first_hunt", "10:00"), DAY.get("last_entry", "15:00"))), IntervalTrigger(minutes=int(DAY.get("hunt_every_min", 30))), id="hunt", misfire_grace_time=120)
@app.on_event("startup")
def start():
    if os.environ.get("MING_NO_SCHED") != "1":
        sched.start()
        bk = svc.cfg.books
        jobs = ", ".join(f"{k} {v}" for k, v in SC.items() if not (k == "flatten" and "day" not in bk))
        extra = f", scan every {DAY.get('scan_every_min', 5)}m, hunt every {DAY.get('hunt_every_min', 30)}m {DAY.get('first_hunt', '10:00')}-{DAY.get('last_entry', '15:00')}" if "day" in bk else ""
        svc.j.log("INFO", f"scheduler on ({svc.cfg.hold_mode}: " + ", ".join(f"{k} ${v:,.0f}" for k, v in bk.items()) + f"): {jobs}{extra} ET weekdays")
@app.on_event("shutdown")
def stop():
    if sched.running: sched.shutdown(wait=False)
