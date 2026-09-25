"""Daily report in Ming's voice, plus the numbers. Optional Telegram send. Nightly reflection: lessons from the day's closed trades."""
import os, json, datetime as dt, httpx
from .ideas import persona, add_lessons, lessons

class Report:
    def __init__(self, cfg, broker, journal, llm):
        self.cfg = cfg; self.b = broker; self.j = journal; self.llm = llm

    def state(self):
        acct = self.b.account(); cap = self.cfg.get("capital_cap")
        base = self.j.get("baseline_equity") or acct["equity"]; day0 = self.j.get("day_start_equity") or acct["equity"]
        pnl = acct["equity"] - base; day = acct["equity"] - day0
        spy0 = self.j.get("baseline_spy"); spy = None
        try: spy = self.b.price("SPY")
        except Exception: pass
        spy_ret = ((spy / spy0 - 1) * 100) if (spy and spy0) else 0.0
        bot_ret = pnl / cap * 100
        closed = self._since_baseline(self.j.closed_trades()); wins = [t for t in closed if (t["pl"] or 0) > 0]
        return {"equity": acct["equity"], "cash": acct["cash"], "pnl": pnl, "day_pnl": day, "bot_return_pct": bot_ret, "spy_return_pct": spy_ret,
                "edge_pts": bot_ret - spy_ret, "closed_trades": len(closed), "hit_rate": (len(wins) / len(closed) * 100) if closed else None,
                "realized": sum(t["pl"] or 0 for t in closed), "capital_cap": cap}

    @staticmethod
    def trade_view(t):
        """One trade for the page: label, status, P&L, exit reason, hold time, entry and exit prices."""
        from .broker import contract_label
        label = contract_label({"underlying": t["underlying"], "strike": t["strike"], "type": t["opt_type"], "expiry": t["expiry"]}) if t.get("opt_type") else t["symbol"]
        held = None
        try:
            a = dt.datetime.fromisoformat(t["entry_ts"]); b = dt.datetime.fromisoformat(t["exit_ts"]) if t.get("exit_ts") else dt.datetime.now(dt.timezone.utc)
            held = int((b - a).total_seconds() // 60)
        except Exception: pass
        return {"label": label, "status": t["status"], "pl": round(t["pl"], 2) if t.get("pl") is not None else None, "exit_reason": t.get("exit_reason"),
                "entry": t["entry"], "exit": t.get("exit"), "qty": t["qty"], "held_min": held, "book": t.get("book"),
                "pl_pct": round((t["exit"] / t["entry"] - 1) * 100, 2) if (t.get("exit") and t.get("entry")) else None}

    def _since_baseline(self, trades):
        """Only trades closed after the current baseline count on the scorecard. Resume resets the baseline, so it also resets the record."""
        b0 = self.j.get("baseline_ts") or ""
        return [t for t in trades if (t.get("exit_ts") or "") >= b0]

    def books(self):
        """Per book: realized plus unrealized against that book's cap, edge vs SPY over the same baseline, open, closed, hit rate."""
        out = {}
        spy0 = self.j.get("baseline_spy"); spy = None
        try: spy = self.b.price("SPY")
        except Exception: pass
        spy_ret = ((spy / spy0 - 1) * 100) if (spy and spy0) else 0.0
        open_all = self.j.open_trades(); prices = {}
        try: prices = self.b.prices_for([t["symbol"] for t in open_all]) if open_all else {}
        except Exception: pass
        for book, cap in self.cfg.books.items():
            closed = self._since_baseline(self.j.closed_trades(500, book)); opens = [t for t in open_all if (t.get("book") or "swing") == book]
            realized = sum(t["pl"] or 0 for t in closed)
            unreal = sum(((prices.get(t["symbol"]) or t["entry"]) - t["entry"]) * t["qty"] for t in opens)
            wins = [t for t in closed if (t["pl"] or 0) > 0]; losses = [t for t in closed if (t["pl"] or 0) <= 0]
            ret = (realized + unreal) / cap * 100 if cap else 0.0
            out[book] = {"cap": cap, "realized": round(realized, 2), "unrealized": round(unreal, 2), "return_pct": round(ret, 2), "edge_pts": round(ret - spy_ret, 2),
                         "open": len(opens), "closed": len(closed), "hit_rate": round(len(wins) / len(closed) * 100) if closed else None,
                         "avg_win": round(sum(t["pl"] for t in wins) / len(wins), 2) if wins else None, "avg_loss": round(sum(t["pl"] for t in losses) / len(losses), 2) if losses else None}
        return out

    def history(self, n=30):
        """One row per trading day, newest first: the day's P&L in dollars and as a percent of the cap. Days with a 16:05 record use it;
        earlier days without one fall back to realized P&L from trades closed that day. Today is live from the account."""
        cap = self.cfg.get("capital_cap") or 1
        rows = {}
        for r in self.j._q("SELECT key, value FROM meta WHERE key LIKE 'day:%'"):
            import json; d = json.loads(r["value"]); rows[r["key"][4:]] = {"day": r["key"][4:], "pnl": d["pnl"], "pct": d["pct"], "closed": d.get("closed", 0), "source": "report"}
        for t in self.j.closed_trades(1000):
            d = (t["exit_ts"] or "")[:10]
            if not d or d in rows and rows[d]["source"] == "report": continue
            r = rows.setdefault(d, {"day": d, "pnl": 0.0, "pct": 0.0, "closed": 0, "source": "trades"})
            r["pnl"] = round(r["pnl"] + (t["pl"] or 0), 2); r["closed"] += 1; r["pct"] = round(r["pnl"] / cap * 100, 3)
        today = dt.date.today().isoformat()
        s = self.state()
        rows[today] = {"day": today, "pnl": round(s["day_pnl"], 2), "pct": round(s["day_pnl"] / cap * 100, 3), "closed": sum(1 for t in self.j.closed_trades(200) if (t["exit_ts"] or "")[:10] == today), "source": "live"}
        out = sorted(rows.values(), key=lambda r: r["day"], reverse=True)[:n]
        for r in out:
            r["trades"] = [self.trade_view(t) for t in self.j.trades_closed_on(r["day"])]
            if r["day"] == today: r["trades"] += [self.trade_view(t) for t in self.j.open_trades()]
        return out

    def text(self):
        s = self.state(); day = dt.date.today().isoformat()
        th = self.j.theses_for(day); tr = [t for t in self.j.all_trades(50) if (t["entry_ts"] or "")[:10] == day or (t["exit_ts"] or "")[:10] == day]
        hit = "n/a" if s["hit_rate"] is None else f"{s['hit_rate']:.0f}%"
        head = (f"Ming, {day}. Equity ${s['equity']:,.2f}, today {s['day_pnl']:+.2f}, since start {s['pnl']:+.2f} ({s['bot_return_pct']:+.2f}% on the cap vs SPY {s['spy_return_pct']:+.2f}%, edge {s['edge_pts']:+.2f} pts). "
                f"Closed trades {s['closed_trades']}, hit rate {hit}.")
        bk = self.books()
        facts = "BOOKS:\n" + "\n".join(f"- {k}: cap ${v['cap']:,.0f}, return {v['return_pct']:+.2f}% (edge vs SPY {v['edge_pts']:+.2f} pts), realized {v['realized']:+.2f}, open {v['open']}, closed {v['closed']}, hit rate {v['hit_rate'] if v['hit_rate'] is not None else 'n/a'}, avg win {v['avg_win'] if v['avg_win'] is not None else '-'}, avg loss {v['avg_loss'] if v['avg_loss'] is not None else '-'}" for k, v in bk.items())
        facts += "\nTHESES TODAY:\n" + ("\n".join(f"- {t['symbol']} [{t.get('book') or '-'}] conv {t['conviction']} {'IN' if t['acted'] else ('skip: '+(t['reject_reason'] or 'pending'))}: {t['catalyst']}" for t in th) or "none")
        facts += "\nTRADES TODAY:\n" + ("\n".join(f"- {t['side']} {t['qty']:.0f} {t['symbol']} [{t.get('book') or '-'}] @ {t['entry']:.2f}" + (f" -> {t['exit']:.2f} ({t['exit_reason']}) P&L {t['pl']:+.2f}" if t['status']=='closed' else f" stop {t['stop']:.2f}") for t in tr) or "none")
        if self.llm.ready:
            m = self.llm.chat([{"role": "system", "content": persona(self.cfg.hold_mode) + "\n\nWrite the 4:05 PM daily report in your own voice. Six to ten short sentences. Lead with the number, then what you did and why, then what you would do differently. When two books are running, say one line on how each did and whether the day or the swing side earned it. No spin."},
                               {"role": "user", "content": head + "\n\n" + facts}], max_tokens=500)
            body = m.content.strip() if m else head
        else:
            body = head
        report = body + "\n\n" + facts
        self.j.set(f"report:{day}", report); self.j.log("REPORT", "daily report written")
        self.j.set(f"day:{day}", {"pnl": round(s["day_pnl"], 2), "pct": round(s["day_pnl"] / cap * 100, 3) if (cap := s["capital_cap"]) else 0.0,
                                  "equity": round(s["equity"], 2), "closed": len([t for t in tr if t["status"] == "closed"]),
                                  "realized": round(sum((t["pl"] or 0) for t in tr if t["status"] == "closed" and (t["exit_ts"] or "")[:10] == day), 2)})
        self._telegram(report)
        return report

    def reflect(self):
        """16:10: read today's closed trades with their theses and exits; write one to three dated lessons with the evidence. Skipped when nothing closed."""
        day = dt.date.today().isoformat()
        closed = [t for t in self.j.closed_trades(100) if (t["exit_ts"] or "")[:10] == day]
        if not closed: self.j.log("LESSON", "nothing closed today; nothing to learn from"); return []
        if not self.llm.ready: self.j.log("LESSON", f"skipped: {self.llm.problem}"); return []
        rows = []
        for t in closed:
            th = self.j._q("SELECT * FROM theses WHERE id=?", t["thesis_id"]); th = th[0] if th else {}
            name = f"{t['underlying']} {t['strike']:g}{'C' if str(t.get('opt_type')).startswith('c') else 'P'} {str(t.get('expiry'))[5:]}" if t.get("opt_type") else t["symbol"]
            held = ""
            try:
                a = dt.datetime.fromisoformat(t["entry_ts"]); b = dt.datetime.fromisoformat(t["exit_ts"]); held = f"{(b - a).total_seconds() / 60:.0f} min"
            except Exception: pass
            rows.append(f"- {name} [{t.get('book')}] entry {t['entry']:.2f} at {t['entry_ts'][11:16]}Z, exit {t['exit']:.2f} ({t['exit_reason']}) after {held}, P&L {t['pl']:+.2f}. "
                        f"Thesis conv {th.get('conviction', '?')}, direction {th.get('direction', '?')}: {th.get('catalyst', '?')} | reason: {th.get('reason', '?')} | invalidation: {th.get('invalidation', '?')}")
        sys = persona(self.cfg.hold_mode) + ("\n\nEnd of day. These are your closed trades with the thesis you wrote for each. Write one to three lessons you can act on tomorrow. "
               "Each lesson names the pattern, the trade that shows it, and what you will do differently. A pattern from one trade is a hypothesis, say so. "
               "No self-punishment, no spin. If a loss was a clean thesis that failed, say that and move on. "
               "Respond with one JSON object only: {\"lessons\": [\"...\", \"...\"]}")
        out = self.llm.json(sys, "TRADES TODAY:\n" + "\n".join(rows) + ("\n\nLESSONS SO FAR:\n" + lessons(20) if lessons() else ""), max_tokens=800)
        items = [str(x)[:300] for x in (out or {}).get("lessons", []) if str(x).strip()][:3]
        if not items: self.j.log("LESSON", "no lesson written"); return []
        add_lessons(day, items)
        for it in items: self.j.log("LESSON", it[:200])
        self.j.set(f"lessons:{day}", items)
        return items

    def _telegram(self, text):
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not (tok and chat): return
        try: httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text[:4000]}, timeout=15)
        except Exception as e: self.j.log("WARN", f"telegram failed: {e}")
