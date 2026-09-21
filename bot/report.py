"""Daily report in Ming's voice, plus the numbers. Optional Telegram send."""
import os, datetime as dt, httpx
from .ideas import persona

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
        closed = self.j.closed_trades(); wins = [t for t in closed if (t["pl"] or 0) > 0]
        return {"equity": acct["equity"], "cash": acct["cash"], "pnl": pnl, "day_pnl": day, "bot_return_pct": bot_ret, "spy_return_pct": spy_ret,
                "edge_pts": bot_ret - spy_ret, "closed_trades": len(closed), "hit_rate": (len(wins) / len(closed) * 100) if closed else None,
                "realized": sum(t["pl"] or 0 for t in closed), "capital_cap": cap}

    def text(self):
        s = self.state(); day = dt.date.today().isoformat()
        th = self.j.theses_for(day); tr = [t for t in self.j.all_trades(50) if (t["entry_ts"] or "")[:10] == day or (t["exit_ts"] or "")[:10] == day]
        hit = "n/a" if s["hit_rate"] is None else f"{s['hit_rate']:.0f}%"
        head = (f"Ming, {day}. Equity ${s['equity']:,.2f}, today {s['day_pnl']:+.2f}, since start {s['pnl']:+.2f} ({s['bot_return_pct']:+.2f}% on the cap vs SPY {s['spy_return_pct']:+.2f}%, edge {s['edge_pts']:+.2f} pts). "
                f"Closed trades {s['closed_trades']}, hit rate {hit}.")
        facts = "THESES TODAY:\n" + "\n".join(f"- {t['symbol']} conv {t['conviction']} {'IN' if t['acted'] else ('skip: '+(t['reject_reason'] or 'pending'))}: {t['catalyst']}" for t in th) or "none"
        facts += "\nTRADES TODAY:\n" + ("\n".join(f"- {t['side']} {t['qty']:.0f} {t['symbol']} @ {t['entry']:.2f}" + (f" -> {t['exit']:.2f} ({t['exit_reason']}) P&L {t['pl']:+.2f}" if t['status']=='closed' else f" stop {t['stop']:.2f}") for t in tr) or "none")
        if self.llm.ready:
            m = self.llm.chat([{"role": "system", "content": persona() + "\n\nWrite the 4:05 PM daily report in your own voice. Six to ten short sentences. Lead with the number, then what you did and why, then what you would do differently. No spin."},
                               {"role": "user", "content": head + "\n\n" + facts}], max_tokens=500)
            body = m.content.strip() if m else head
        else:
            body = head
        report = body + "\n\n" + facts
        self.j.set(f"report:{day}", report); self.j.log("REPORT", "daily report written")
        self._telegram(report)
        return report

    def _telegram(self, text):
        tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
        if not (tok and chat): return
        try: httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text[:4000]}, timeout=15)
        except Exception as e: self.j.log("WARN", f"telegram failed: {e}")
