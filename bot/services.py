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
        self.j.log("INFO", f"Ming online: mode {'LIVE' if self.cfg.live else 'paper'}, broker {self.b.name}, brain {self.llm.status}")

    # ---- commands (the buttons) ----
    def run(self, cmd):
        fn = {"research": self.do_research, "refresh": self.do_refresh, "execute": self.do_execute, "review": self.do_review,
              "report": self.do_report, "kill": self.do_kill, "resume": self.do_resume, "morning": self.do_morning,
              "scan": self.do_scan, "hunt": self.do_hunt, "flatten": self.do_flatten, "flatten_all": self.do_flatten_all, "sweep": self.do_sweep}.get(cmd)
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
    def do_review(self):
        """3:45 with the LLM invalidation check. Both mode: the swing book. Day or swing mode: every open trade, so a position left from the other mode is still managed."""
        self.exe.review(book="swing" if self.cfg.hold_mode == "both" else None); return {"ok": True}
    def do_scan(self):
        """Every few minutes while the market is open: the day book's stops, targets, trails. No LLM, quiet unless something happens.
        Safety net: past the flatten time it flattens the day book itself, so a missed 15:55 job cannot leave him holding overnight."""
        from zoneinfo import ZoneInfo
        hhmm = dt.datetime.now(ZoneInfo(self.cfg.tz)).strftime("%H:%M")
        if hhmm >= self.cfg.schedule.get("flatten", "15:55") and self.j.open_trades("day"):
            self.j.log("FLATTEN", "scan past the flatten time with day-book positions open: flattening now")
            return {"closed": self.exe.flatten("day")}
        self.exe.review(use_llm=False, quiet=True, book="day"); return {"ok": True}
    def do_hunt(self):
        """Every half hour: what moved in the last hour -> new day-book theses -> rules engine."""
        if "day" not in self.cfg.books: return {"theses": [], "trades": [], "note": "no day book"}
        ok, why = self.risk.entry_window("day")
        if not ok: self.j.log("SKIP", f"hunt skipped: {why}"); return {"theses": [], "trades": [], "note": why}
        self.risk.ensure_baselines()
        day = dt.date.today().isoformat()
        pack, parts = self.research.gather(intraday=True)
        existing = self.j.theses_for(day)
        th, note = self.analyst.theses(pack, existing=existing, intraday=True)
        seen = {t["symbol"] for t in existing}
        th = [t for t in th if t["symbol"] not in seen]
        if th:
            self.j.add_theses(day, th, "hunt")
            self.j.log("RESEARCH", f"hunt: {len(th)} new: " + ", ".join(f"{t['symbol']} {t['conviction']}" for t in th))
        else: self.j.log("RESEARCH", f"hunt: nothing new ({note or 'sat out'})")
        pending = [t for t in self.j.theses_for(day) if not t["acted"] and not t["reject_reason"] and (t.get("book") or "day") == "day"]
        trades = self.exe.execute(books=["day"]) if (th or pending) else []   # pending: a thesis written earlier that no execute has judged yet
        return {"theses": th, "trades": trades, "note": note}
    def do_flatten(self): return {"closed": self.exe.flatten("day")}
    def do_flatten_all(self): return {"closed": self.exe.flatten(None)}
    def do_sweep(self): return {"closed": self.exe.flatten("day", stale_only=True)}
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
        s.update({"mode": "live" if self.cfg.live else "paper", "hold_mode": self.cfg.hold_mode, "books": self.report.books(), "schedule": self.schedule_view(), "thinking": self.j.get("thinking"), "halted": self.risk.halted, "halt_reason": self.j.get("halt_reason", ""),
                  "market_open": bool(clock.get("is_open")), "workday": wd and 5 <= now.hour < 18, "aggression": self.cfg.aggression,
                  "positions": pos, "config": self.cfg.risk, "busy": self.busy, "brain": self.llm.status, "broker": self.b.name})
        return s
    def theses(self, day=None):
        return self.j.theses_for(day or dt.date.today().isoformat())
    def set_config(self, key, value):
        self.cfg.set(key, value); self.j.log("CONFIG", f"{key} = {value}"); return self.cfg.risk
    def schedule_view(self):
        """What the page shows across the top: the day's jobs in order, for the current hold mode."""
        sc = self.cfg.schedule; d = self.cfg.day
        def m(hhmm): h, mm = hhmm.split(":"); return int(h) * 60 + int(mm)
        rows = [(sc["research"], "research"), (sc["refresh"], "refresh"), (sc["execute"], "execute")]
        if "day" in self.cfg.books and any((t["entry_ts"] or "")[:10] < dt.date.today().isoformat() for t in self.j.open_trades("day")): rows.append((sc.get("sweep", "09:31"), "sweep"))
        if "day" in self.cfg.books:
            rows += [(d.get("first_hunt", "10:00"), f"hunt /{d.get('hunt_every_min', 30)}m"), (d.get("last_entry", "15:00"), "last entry")]
        if self.cfg.hold_mode != "day" or self.j.open_trades("swing"): rows += [(sc["review"], "review")]
        if "day" in self.cfg.books: rows += [(sc.get("flatten", "15:55"), "flatten")]
        rows += [(sc["report"], "report")]
        rows.sort(key=lambda r: m(r[0]))
        return [{"m": m(t), "time": t.lstrip("0"), "label": l} for t, l in rows]
    def set_hold_mode(self, mode):
        self.cfg.set("hold_mode", mode); self.j.log("CONFIG", f"hold_mode = {mode}"); return mode
    def set_aggression(self, level):
        name = self.cfg.set_aggression(level); self.j.log("CONFIG", f"aggression {level} ({name})"); return name
    def add_idea(self, text, who="operator"):
        out = ideasmod.add_idea(text, who); self.j.log("IDEA", text[:140]); return out
    def ideas(self): return ideasmod.ideas()

    def _et(self, iso):
        try:
            from zoneinfo import ZoneInfo
            return dt.datetime.fromisoformat(iso).astimezone(ZoneInfo(self.cfg.tz)).strftime("%H:%M")
        except Exception: return iso[11:16]
    def context_for_agent(self):
        """Compact context for chat and voice: state, theses, positions, last log lines, ideas."""
        s = self.state(); th = self.theses()
        lines = [f"MODE {s['mode']}, hold_mode {s['hold_mode']}{' HALTED: ' + s['halt_reason'] if s['halted'] else ''}. Equity ${s['equity']:,.2f}, today {s['day_pnl']:+.2f}, since start {s['pnl']:+.2f}, edge vs SPY {s['edge_pts']:+.2f} pts, closed {s['closed_trades']}, hit rate {s['hit_rate'] or 0:.0f}%. Aggression {s['aggression']}. Brain {s['brain']}. Market {'open' if s['market_open'] else 'closed'}.",
                 "BOOKS: " + "; ".join(f"{k} cap ${v['cap']:,.0f} return {v['return_pct']:+.2f}% (edge {v['edge_pts']:+.2f} pts) open {v['open']} closed {v['closed']} hit {v['hit_rate'] if v['hit_rate'] is not None else '-'}" for k, v in s["books"].items()),
                 "POSITIONS: " + ("; ".join(f"{p['symbol']} [{p.get('book','')}] {p['qty']:.0f} @ {p['entry']:.2f} now {p['price']:.2f} ({p['pl_pct']:+.1f}%) stop {p['stop'] or '-'} target +{p.get('target_pct') or '-'}%" for p in s["positions"]) or "none"),
                 "THESES TODAY: " + ("; ".join(f"{t['symbol']} conv {t['conviction']} {'IN' if t['acted'] else ('skip: ' + (t['reject_reason'] or 'pending'))}: {t['catalyst']}" for t in th) or "none"),
                 "IDEAS: " + self.ideas().replace("\n", " | "),
                 "RECENT LOG (ET): " + " | ".join(f"{self._et(r['ts'])} {r['level']} {r['msg']}" for r in self.j.logs(12))]
        return "\n".join(lines)
