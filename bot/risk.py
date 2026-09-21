"""Rules engine. Loss caps and halt, slots, per-name and per-sector caps, price and conviction filters, stop and trail math. This is the only thing that sizes."""
import datetime as dt

class Risk:
    def __init__(self, cfg, broker, journal):
        self.cfg = cfg; self.b = broker; self.j = journal

    # ---- baselines and halt ----
    def ensure_baselines(self):
        acct = self.b.account()
        if self.j.get("baseline_equity") is None:
            self.j.set("baseline_equity", acct["equity"]); self.j.set("baseline_ts", self.j.now())
            spy = self._spy()
            if spy: self.j.set("baseline_spy", spy)
            self.j.log("INFO", f"baseline set: equity {acct['equity']:.2f}")
        today = dt.date.today().isoformat()
        if self.j.get("day_start_date") != today:
            self.j.set("day_start_date", today); self.j.set("day_start_equity", acct["equity"])
        return acct
    def reset_baselines(self):
        acct = self.b.account()
        self.j.set("baseline_equity", acct["equity"]); self.j.set("baseline_ts", self.j.now())
        self.j.set("day_start_date", dt.date.today().isoformat()); self.j.set("day_start_equity", acct["equity"])
        spy = self._spy()
        if spy: self.j.set("baseline_spy", spy)
        self.j.set("halted", False); self.j.set("halt_reason", "")
        self.j.log("INFO", f"resumed; baseline reset to {acct['equity']:.2f}")
    def _spy(self):
        try: return self.b.price("SPY")
        except Exception: return None

    @property
    def halted(self):
        return bool(self.j.get("halted", False))
    def halt(self, reason):
        self.j.set("halted", True); self.j.set("halt_reason", reason)
        try: self.b.close_all()
        except Exception as e: self.j.log("ERROR", f"close_all failed: {e}")
        for t in self.j.open_trades():
            px = None
            try: px = self.b.price(t["symbol"])
            except Exception: pass
            self.j.close_trade(t["id"], px or t["entry"], "halt")
        self.j.log("HALT", reason)

    def check_caps(self):
        """Called at the start of every run. Halts if a cap is breached. Returns True if trading may continue."""
        if self.halted: return False
        acct = self.ensure_baselines()
        cap = self.cfg.get("capital_cap")
        day_pl = acct["equity"] - (self.j.get("day_start_equity") or acct["equity"])
        tot_pl = acct["equity"] - (self.j.get("baseline_equity") or acct["equity"])
        if day_pl < -cap * self.cfg.get("loss_cap_daily_pct") / 100:
            self.halt(f"DAILY loss cap hit: down ${-day_pl:.0f} today"); return False
        if tot_pl < -cap * self.cfg.get("loss_cap_total_pct") / 100:
            self.halt(f"TOTAL loss cap hit: down ${-tot_pl:.0f} from baseline"); return False
        return True

    # ---- entries ----
    def size(self, thesis, positions, prices):
        """Return (qty, stop, reject_reason). qty 0 with a reason means skip."""
        r = self.cfg.risk; sym = thesis["symbol"]
        if thesis["conviction"] < r["min_conviction"]: return 0, None, f"conviction {thesis['conviction']} < {r['min_conviction']}"
        if thesis.get("priced_in"): return 0, None, "already priced in"
        if any(p["symbol"] == sym for p in positions): return 0, None, "already held"
        if len(positions) >= r["max_positions"]: return 0, None, f"no slot ({len(positions)}/{r['max_positions']})"
        sector = (thesis.get("sector") or "").lower()
        if sector:
            same = sum(1 for p in positions if (p.get("sector") or "").lower() == sector)
            if same >= r["max_sector_positions"]: return 0, None, f"sector cap ({sector})"
        px = prices.get(sym)
        if not px: return 0, None, "no price"
        if px < r["min_price"]: return 0, None, f"price ${px:.2f} below floor"
        deployed = sum(p["qty"] * p["price"] for p in positions)
        room = r["capital_cap"] - deployed
        dollars = min(r["capital_cap"] * r["max_position_pct"] / 100, room)
        qty = int(dollars // px)
        if qty < 1: return 0, None, f"no room (${room:.0f} left)"
        stop = round(px * (1 - r["stop_loss_pct"] / 100), 2)
        return qty, stop, None

    # ---- exits ----
    def exit_rules(self, trade, price):
        """Return ('take_profit'|'trail'|None, new_stop). trail returns the raised stop."""
        r = self.cfg.risk; e = trade["entry"]; gain = (price / e - 1) * 100
        if gain >= r["take_profit_pct"]: return "take_profit", None
        if gain >= r["trail_trigger_pct"]:
            new_stop = round(price * (1 - r["trail_pct"] / 100), 2)
            if new_stop > (trade["stop"] or 0): return "trail", new_stop
        return None, None
