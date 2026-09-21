"""Services: the one object graph. The buttons, the CLI, the chat, and the voice tools all call these same methods."""
import os, datetime as dt, threading
from .config import Config
from .journal import Journal
from .broker import make_broker
from .llm import LLM
from .research import Research
from .analyst import Analyst
from .risk import Risk
from .executor import Executor
from .report import Report
from . import ideas as ideasmod

class Services:
    def __init__(self):
        self.cfg = Config(); self.j = Journal(); self.b = make_broker(self.cfg, self.j); self.llm = LLM(self.cfg, self.j)
        self.research = Research(self.cfg, self.b, self.j); self.analyst = Analyst(self.cfg, self.llm, self.j)
        self.risk = Risk(self.cfg, self.b, self.j); self.exe = Executor(self.cfg, self.b, self.j, self.risk, self.analyst)
        self.report = Report(self.cfg, self.b, self.j, self.llm)
        self.busy = None; self._lock = threading.Lock()
        self.j.log("INFO", f"Ming online: mode {'LIVE' if self.cfg.live else 'paper'}, broker {self.b.name}, brain {'connected' if self.llm.ready else 'NOT connected'}")

    # ---- commands (the buttons) ----
    def run(self, cmd):
        fn = {"research": self.do_research, "refresh": self.do_refresh, "execute": self.do_execute, "review": self.do_review,
              "report": self.do_report, "kill": self.do_kill, "resume": self.do_resume, "morning": self.do_morning}.get(cmd)
        if not fn: raise ValueError(f"unknown command {cmd}")
        if not self._lock.acquire(blocking=False): raise RuntimeError(f"busy: {self.busy}")
        try:
            self.busy = cmd; return fn()
        finally:
            self.busy = None; self._lock.release()

    def do_research(self, refresh=False):
        self.risk.ensure_baselines()
        day = dt.date.today().isoformat()
        pack, parts = self.research.gather(refresh=refresh)
        existing = self.j.theses_for(day) if refresh else None
        th, note = self.analyst.theses(pack, refresh=refresh, existing=existing)
        if refresh: self.j.replace_day(day, th, "refresh")
        else: self.j.add_theses(day, th, "research")
        if th: self.j.log("RESEARCH", f"{len(th)} theses: " + ", ".join(f"{t['symbol']} {t['conviction']}" for t in th))
        else: self.j.log("RESEARCH", f"no theses ({note or 'sat out'})")
        self.exe._mark_equity()
        return {"theses": th, "note": note}
    def do_refresh(self): return self.do_research(refresh=True)
    def do_execute(self): return {"trades": self.exe.execute()}
    def do_review(self): self.exe.review(); return {"ok": True}
    def do_report(self): return {"report": self.report.text()}
    def do_kill(self): self.risk.halt("operator kill switch"); return {"ok": True}
    def do_resume(self): self.risk.reset_baselines(); return {"ok": True}
    def do_morning(self):
        r = self.do_research(); e = self.exe.execute(); return {"theses": r["theses"], "trades": e}

    # ---- state for the page and the agents ----
    def state(self):
        s = self.report.state()
        try: clock = self.b.clock()
        except Exception: clock = {"is_open": False}
        pos = self.exe._positions_with_sector()
        now = dt.datetime.now(); wd = now.weekday() < 5
        s.update({"mode": "live" if self.cfg.live else "paper", "halted": self.risk.halted, "halt_reason": self.j.get("halt_reason", ""),
                  "market_open": bool(clock.get("is_open")), "workday": wd and 5 <= now.hour < 18, "aggression": self.cfg.aggression,
                  "positions": pos, "config": self.cfg.risk, "busy": self.busy, "brain": self.llm.ready, "broker": self.b.name})
        return s
    def theses(self, day=None):
        return self.j.theses_for(day or dt.date.today().isoformat())
    def set_config(self, key, value):
        self.cfg.set(key, value); self.j.log("CONFIG", f"{key} = {value}"); return self.cfg.risk
    def set_aggression(self, level):
        name = self.cfg.set_aggression(level); self.j.log("CONFIG", f"aggression {level} ({name})"); return name
    def add_idea(self, text, who="operator"):
        out = ideasmod.add_idea(text, who); self.j.log("IDEA", text[:140]); return out
    def ideas(self): return ideasmod.ideas()

    def context_for_agent(self):
        """Compact context for chat and voice: state, theses, positions, last log lines, ideas."""
        s = self.state(); th = self.theses()
        lines = [f"MODE {s['mode']}{' HALTED: ' + s['halt_reason'] if s['halted'] else ''}. Equity ${s['equity']:,.2f}, today {s['day_pnl']:+.2f}, since start {s['pnl']:+.2f}, edge vs SPY {s['edge_pts']:+.2f} pts, closed {s['closed_trades']}, hit rate {s['hit_rate'] or 0:.0f}%. Aggression {s['aggression']}. Brain {'on' if s['brain'] else 'NOT connected'}. Market {'open' if s['market_open'] else 'closed'}.",
                 "POSITIONS: " + ("; ".join(f"{p['symbol']} {p['qty']:.0f} @ {p['entry']:.2f} now {p['price']:.2f} ({p['pl_pct']:+.1f}%) stop {p['stop'] or '-'}" for p in s["positions"]) or "none"),
                 "THESES TODAY: " + ("; ".join(f"{t['symbol']} conv {t['conviction']} {'IN' if t['acted'] else ('skip: ' + (t['reject_reason'] or 'pending'))}: {t['catalyst']}" for t in th) or "none"),
                 "IDEAS: " + self.ideas().replace("\n", " | "),
                 "RECENT LOG: " + " | ".join(f"{r['ts'][11:16]} {r['level']} {r['msg']}" for r in self.j.logs(12))]
        return "\n".join(lines)
