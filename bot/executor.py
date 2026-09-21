"""Executor: entries with immediate stops, the afternoon review, exits with journaling. Every order goes through Risk first."""
import datetime as dt

class Executor:
    def __init__(self, cfg, broker, journal, risk, analyst):
        self.cfg = cfg; self.b = broker; self.j = journal; self.r = risk; self.a = analyst

    def _positions_with_sector(self):
        pos = self.b.positions()
        for p in pos:
            t = self.j.trade_for(p["symbol"])
            th = self._thesis(t["thesis_id"]) if t else None
            p["sector"] = th.get("sector") if th else ""
            p["stop"] = t["stop"] if t else None
            p["thesis"] = {"catalyst": th.get("catalyst")} if th else None
        return pos
    def _thesis(self, tid):
        if not tid: return {}
        r = self.j._q("SELECT * FROM theses WHERE id=?", tid)
        return r[0] if r else {}

    def execute(self, day=None, only_symbol=None):
        """9:35: take today's theses in conviction order through the rules engine."""
        if not self.r.check_caps(): self.j.log("SKIP", "halted; no entries"); return []
        day = day or dt.date.today().isoformat()
        theses = [t for t in self.j.theses_for(day) if not t["acted"] and not t["reject_reason"]]
        if only_symbol: theses = [t for t in theses if t["symbol"] == only_symbol]
        if not theses: self.j.log("SKIP", "no theses to act on"); return []
        positions = self._positions_with_sector()
        prices = self.b.prices_for([t["symbol"] for t in theses])
        done = []
        for t in theses:
            qty, stop, why = self.r.size(t, positions, prices)
            if not qty:
                self.j.mark_thesis(t["id"], reject_reason=why); self.j.log("SKIP", f"{t['symbol']}: {why}"); continue
            try:
                o = self.b.market_buy(t["symbol"], qty)
                fill = o.get("filled_avg_price") or prices[t["symbol"]]
                stop = round(fill * (1 - self.cfg.get("stop_loss_pct") / 100), 2)
                soid = self.b.stop_order(t["symbol"], qty, stop)
                tid = self.j.open_trade(t["symbol"], qty, fill, stop, t["id"], o.get("id"), soid)
                self.j.mark_thesis(t["id"], acted=1)
                positions.append({"symbol": t["symbol"], "qty": qty, "price": fill, "sector": t.get("sector")})
                self.j.log("TRADE", f"BUY {qty} {t['symbol']} @ {fill:.2f} stop {stop:.2f} (conv {t['conviction']})")
                done.append({"symbol": t["symbol"], "qty": qty, "fill": fill, "stop": stop})
            except Exception as e:
                self.j.mark_thesis(t["id"], reject_reason=f"order failed: {str(e)[:80]}")
                self.j.log("ERROR", f"{t['symbol']} order failed: {str(e)[:160]}")
        self._mark_equity()
        return done

    def propose(self, symbol, side="buy"):
        """Operator asked for a specific name. Return what the rules engine would do, without doing it."""
        symbol = symbol.upper()
        if side == "sell":
            t = self.j.trade_for(symbol)
            if not t: return {"ok": False, "why": f"no open position in {symbol}"}
            px = self.b.price(symbol)
            return {"ok": True, "action": f"SELL {t['qty']:.0f} {symbol} at market (~{px:.2f}), P&L {((px - t['entry']) * t['qty']):+.2f}", "symbol": symbol, "side": "sell"}
        if not self.r.check_caps(): return {"ok": False, "why": "halted"}
        info = self.b.tradable(symbol)
        if not info.get("tradable"): return {"ok": False, "why": f"{symbol} is not tradable: {info.get('error','')}"}
        day = dt.date.today().isoformat()
        existing = [t for t in self.j.theses_for(day) if t["symbol"] == symbol]
        th = dict(existing[0]) if existing else {"symbol": symbol, "sector": "", "catalyst": "operator direction", "conviction": self.cfg.get("min_conviction"), "operator_directed": 1}
        th["conviction"] = max(th.get("conviction", 0), self.cfg.get("min_conviction"))  # operator direction clears the conviction bar, nothing else
        th["priced_in"] = False
        qty, stop, why = self.r.size(th, self._positions_with_sector(), {symbol: info["price"]})
        if not qty: return {"ok": False, "why": why}
        research_view = f"Research today: conviction {existing[0]['conviction']}/10, {existing[0]['catalyst']}" if existing else "Research has no thesis on this name today."
        return {"ok": True, "action": f"BUY {qty} {symbol} at ~{info['price']:.2f} (${qty*info['price']:.0f}), stop {stop:.2f}", "symbol": symbol, "side": "buy",
                "qty": qty, "stop": stop, "research_view": research_view}

    def operator_trade(self, symbol, side="buy"):
        """Confirmed by the operator: run it. Buys go through execute() so the same code path places the stop."""
        symbol = symbol.upper()
        if side == "sell":
            t = self.j.trade_for(symbol)
            if not t: return {"ok": False, "why": "no position"}
            self._exit(t, "operator")
            return {"ok": True}
        day = dt.date.today().isoformat()
        if not any(t["symbol"] == symbol and not t["acted"] for t in self.j.theses_for(day)):
            self.j.add_theses(day, [{"symbol": symbol, "sector": "", "catalyst": "operator direction", "reason": "operator asked for it",
                                     "invalidation": "operator changes his mind", "conviction": self.cfg.get("min_conviction"), "operator_directed": True}], source="operator")
        else:
            for t in self.j.theses_for(day):
                if t["symbol"] == symbol and not t["acted"]:
                    self.j._x("UPDATE theses SET conviction=MAX(conviction,?), reject_reason=NULL, operator_directed=1 WHERE id=?", self.cfg.get("min_conviction"), t["id"])
        done = self.execute(day, only_symbol=symbol)
        self.j.log("INFO", f"operator directed {side} {symbol}")
        return {"ok": bool(done), "done": done}

    def review(self):
        """3:45: take profit, LLM invalidation, trailing stops."""
        if self.r.halted: self.j.log("SKIP", "halted; no review"); return
        self.r.check_caps()
        trades = self.j.open_trades()
        if not trades: self.j.log("REVIEW", "nothing open"); self._mark_equity(); return
        prices = self.b.prices_for([t["symbol"] for t in trades])
        held = {p["symbol"] for p in self.b.positions()}
        for t in trades:
            sym = t["symbol"]
            if sym not in held:   # the stop fired at the broker; close the journal side
                px = prices.get(sym) or t["stop"] or t["entry"]
                self.j.close_trade(t["id"], t["stop"] or px, "stop"); self.j.log("EXIT", f"{sym} stopped out near {t['stop']:.2f}"); continue
            px = prices.get(sym)
            if not px: continue
            rule, new_stop = self.r.exit_rules(t, px)
            if rule == "take_profit": self._exit(t, "take_profit", px); continue
            th = self._thesis(t["thesis_id"])
            try: news = [n["headline"] for n in self.b.news([sym], limit=15)]
            except Exception: news = []
            bad, why = self.a.invalidated(t, th, px, news)
            if bad: self.j.log("REVIEW", f"{sym} thesis invalidated: {why}"); self._exit(t, "invalidated", px); continue
            if rule == "trail":
                try:
                    soid = self.b.replace_stop(t["stop_order_id"], new_stop) if t.get("stop_order_id") else self.b.stop_order(sym, t["qty"], new_stop)
                    self.j.update_stop(t["id"], new_stop, soid); self.j.log("STOP", f"{sym} stop raised to {new_stop:.2f}")
                except Exception as e: self.j.log("ERROR", f"{sym} trail failed: {str(e)[:120]}")
        self._mark_equity()

    def _exit(self, t, reason, px=None):
        try:
            self.b.cancel_all_for(t["symbol"]) if hasattr(self.b, "cancel_all_for") else None
            o = self.b.market_sell(t["symbol"])
            fill = (o or {}).get("filled_avg_price") or px or self.b.price(t["symbol"])
            pl = self.j.close_trade(t["id"], fill, reason)
            self.j.log("EXIT", f"SELL {t['qty']:.0f} {t['symbol']} @ {fill:.2f} ({reason}) P&L {pl:+.2f}")
        except Exception as e:
            self.j.log("ERROR", f"{t['symbol']} exit failed: {str(e)[:160]}")

    def _mark_equity(self):
        try:
            a = self.b.account(); self.j.mark_equity(a["equity"], a["cash"], self.b.price("SPY"))
        except Exception as e: self.j.log("WARN", f"equity mark failed: {str(e)[:100]}")
