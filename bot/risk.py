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
        """Called at the start of every run. Halts if a cap is breached. Returns True if trading may continue.
        A DAILY cap halt clears itself on the next trading day; a TOTAL cap halt and the kill switch wait for the operator."""
        if self.halted:
            reason = str(self.j.get("halt_reason", ""))
            if reason.startswith("DAILY") and self.j.get("day_start_date") != dt.date.today().isoformat():
                acct = self.b.account()
                self.j.set("halted", False); self.j.set("halt_reason", ""); self.j.set("day_start_date", dt.date.today().isoformat()); self.j.set("day_start_equity", acct["equity"])
                self.j.log("INFO", "new day: yesterday's daily loss halt cleared; trading resumes under the same caps")
            else: return False
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
    def entry_window(self, book="day"):
        """Day book: entries only while the market is open, before last_entry, and outside the no-entry windows. Swing book: always. Returns (ok, why)."""
        if book != "day": return True, None
        from zoneinfo import ZoneInfo
        now = dt.datetime.now(ZoneInfo(self.cfg.tz)); hhmm = now.strftime("%H:%M"); d = self.cfg.day
        if now.weekday() > 4: return False, "weekend"
        try: is_open = bool(self.b.clock().get("is_open"))
        except Exception: is_open = "09:30" <= hhmm < "16:00"
        if not is_open: return False, "market closed"
        if hhmm >= d.get("last_entry", "15:00"): return False, f"past last entry {d.get('last_entry', '15:00')} ET"
        for a, z in d.get("no_entry_windows", []) or []:
            if a <= hhmm < z: return False, f"midday pause {a}-{z} ET"
        return True, None

    def exits_for(self, book, stat):
        """Stop, target, and trail for one entry, as percents. Scaled to the stock's average daily range when we have it, clamped; the book's fixed numbers otherwise."""
        r = self.cfg.risk_for(book); v = r["vol"]; atr = (stat or {}).get("atr_pct")
        if atr and v.get("stop_atr_mult"):
            stop = min(max(atr * v["stop_atr_mult"], v["stop_min_pct"]), v["stop_max_pct"])
            target = min(max(atr * v["target_atr_mult"], v["target_min_pct"]), v["target_max_pct"])
            return {"stop_pct": round(stop, 2), "target_pct": round(target, 2), "trail_trigger_pct": round(stop, 2), "trail_pct": round(stop * 0.66, 2), "atr_pct": atr}
        return {"stop_pct": r["stop_loss_pct"], "target_pct": r["take_profit_pct"], "trail_trigger_pct": r["trail_trigger_pct"], "trail_pct": r["trail_pct"], "atr_pct": None}

    def size(self, thesis, positions, prices, book="swing", all_symbols=(), stat=None):
        """Return (qty, stop, reject_reason, exits). qty 0 with a reason means skip. positions are this book's; all_symbols is every open name in any book."""
        r = self.cfg.risk_for(book); sym = thesis["symbol"]
        if thesis["conviction"] < r["min_conviction"]: return 0, None, f"conviction {thesis['conviction']} < {r['min_conviction']}", None
        if thesis.get("priced_in"): return 0, None, "already priced in", None
        if sym in set(all_symbols) or any(p["symbol"] == sym for p in positions): return 0, None, "already held", None
        if len(positions) >= r["max_positions"]: return 0, None, f"no slot ({len(positions)}/{r['max_positions']} {book})", None
        sector = (thesis.get("sector") or "").lower()
        if sector:
            same = sum(1 for p in positions if (p.get("sector") or "").lower() == sector)
            if same >= r["max_sector_positions"]: return 0, None, f"sector cap ({sector})", None
        px = prices.get(sym)
        if not px: return 0, None, "no price", None
        if px < r["min_price"]: return 0, None, f"price ${px:.2f} below floor", None
        deployed = sum(p["qty"] * p["price"] for p in positions)
        room = r["capital_cap"] - deployed
        dollars = min(r["capital_cap"] * r["max_position_pct"] / 100, room)
        qty = int(dollars // px)
        if qty < 1: return 0, None, f"no room (${room:.0f} left in {book})", None
        ex = self.exits_for(book, stat)
        stop = round(px * (1 - ex["stop_pct"] / 100), 2)
        return qty, stop, None, ex

    def size_option(self, thesis, positions, book, contract, all_underlyings=()):
        """Contracts to buy: a slice of the book's cap in premium, within the room left. Returns (qty, stop, reject_reason, exits)."""
        r = self.cfg.risk_for(book); o = self.cfg.options; sym = thesis["symbol"]
        if thesis["conviction"] < r["min_conviction"]: return 0, None, f"conviction {thesis['conviction']} < {r['min_conviction']}", None
        if thesis.get("priced_in"): return 0, None, "already priced in", None
        if sym in set(all_underlyings): return 0, None, "already held", None
        if len(positions) >= r["max_positions"]: return 0, None, f"no slot ({len(positions)}/{r['max_positions']} {book})", None
        sector = (thesis.get("sector") or "").lower()
        if sector and sum(1 for p in positions if (p.get("sector") or "").lower() == sector) >= r["max_sector_positions"]: return 0, None, f"sector cap ({sector})", None
        ask = contract["ask"]
        from .broker import is_option
        deployed = sum(p["qty"] * p["price"] * (100 if (is_option(p["symbol"]) or p.get("underlying")) else 1) for p in positions)   # shares count once, contracts x100
        room = r["capital_cap"] - deployed
        dollars = min(r["capital_cap"] * float(o.get("max_position_pct", 10)) / 100, room)
        qty = int(dollars // (ask * 100))
        if qty < 1: return 0, None, f"premium {ask:.2f} x100 above the ${dollars:.0f} slice", None
        ex = {"stop_pct": float(o["stop_pct"]), "target_pct": float(o["target_pct"]), "trail_trigger_pct": float(o["trail_trigger_pct"]), "trail_pct": float(o["trail_pct"]), "atr_pct": None}
        stop = round(ask * (1 - ex["stop_pct"] / 100), 2)
        return qty, stop, None, ex

    # ---- exits ----
    def exit_rules(self, trade, price):
        """Return ('take_profit'|'trail'|None, new_stop). Uses the trade's own numbers, set at entry; falls back to the book's."""
        r = self.cfg.risk_for(trade.get("book") or "swing"); e = trade["entry"]; gain = (price / e - 1) * 100
        target = trade.get("target_pct") or r["take_profit_pct"]; trig = trade.get("trail_trigger_pct") or r["trail_trigger_pct"]; trail = trade.get("trail_pct") or r["trail_pct"]
        if not trade.get("stop_order_id") and trade.get("stop") and price <= trade["stop"]: return "stop", None   # engine-held stop (options have no broker stop)
        if gain >= target: return "take_profit", None
        if gain >= trig:
            new_stop = round(price * (1 - trail / 100), 2)
            if new_stop > (trade["stop"] or 0): return "trail", new_stop
        return None, None
