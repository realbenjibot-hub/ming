"""Executor: entries with immediate stops, the afternoon review, exits with journaling. Every order goes through Risk first."""
import datetime as dt

class Executor:
    def __init__(self, cfg, broker, journal, risk, analyst):
        self.cfg = cfg; self.b = broker; self.j = journal; self.r = risk; self.a = analyst

    def _positions_with_sector(self, book=None):
        pos = self.b.positions()
        for p in pos:
            t = self.j.trade_for(p["symbol"])
            th = self._thesis(t["thesis_id"]) if t else None
            p["sector"] = th.get("sector") if th else ""
            p["stop"] = t["stop"] if t else None
            p["book"] = (t.get("book") if t else None) or "swing"
            p["target_pct"] = t.get("target_pct") if t else None
            p["thesis"] = {"catalyst": th.get("catalyst")} if th else None
        return [p for p in pos if p["book"] == book] if book else pos
    def _thesis(self, tid):
        if not tid: return {}
        r = self.j._q("SELECT * FROM theses WHERE id=?", tid)
        return r[0] if r else {}

    def execute(self, day=None, only_symbol=None, books=None):
        """9:35 and every hunt: take today's unacted theses in conviction order through the rules engine, book by book."""
        if not self.r.check_caps(): self.j.log("SKIP", "halted; no entries"); return []
        day = day or dt.date.today().isoformat()
        books = books or list(self.cfg.books)
        theses = [t for t in self.j.theses_for(day) if not t["acted"] and not t["reject_reason"]]
        if only_symbol: theses = [t for t in theses if t["symbol"] == only_symbol]
        if not theses: self.j.log("SKIP", "no theses to act on"); return []
        for t in theses:
            if (t.get("book") or "swing") not in self.cfg.books: t["book"] = books[0]   # mode changed since the thesis was written: put it in a live book
        prices = self.b.prices_for([t["symbol"] for t in theses])
        try: stats = self.b.stats([t["symbol"] for t in theses])
        except Exception as e: self.j.log("WARN", f"stats failed: {type(e).__name__}"); stats = {}
        done = []
        for book in books:
            mine = [t for t in theses if (t.get("book") or "swing") == book]
            if not mine: continue
            ok, why = self.r.entry_window(book)
            if not ok: self.j.log("SKIP", f"{book} book: no entries, {why}"); continue
            positions = self._positions_with_sector(book)
            for t in mine:
                held = [x["symbol"] for x in self.j.open_trades()]
                qty, stop, why, ex = self.r.size(t, positions, prices, book=book, all_symbols=held, stat=stats.get(t["symbol"]))
                if not qty:
                    self.j.mark_thesis(t["id"], reject_reason=why); self.j.log("SKIP", f"{t['symbol']} ({book}): {why}"); continue
                try:
                    o = self.b.market_buy(t["symbol"], qty)
                    fill = o.get("filled_avg_price") or prices[t["symbol"]]
                    stop = round(fill * (1 - ex["stop_pct"] / 100), 2)
                    soid = self.b.stop_order(t["symbol"], qty, stop)
                    tid = self.j.open_trade(t["symbol"], qty, fill, stop, t["id"], o.get("id"), soid, book=book, exits=ex)
                    self.j.mark_thesis(t["id"], acted=1)
                    positions.append({"symbol": t["symbol"], "qty": qty, "price": fill, "sector": t.get("sector")})
                    atr = f", atr {ex['atr_pct']:.1f}%" if ex.get("atr_pct") else ""
                    self.j.log("TRADE", f"BUY {qty} {t['symbol']} @ {fill:.2f} [{book}] stop {stop:.2f} (-{ex['stop_pct']}%) target +{ex['target_pct']}% (conv {t['conviction']}{atr})")
                    done.append({"symbol": t["symbol"], "qty": qty, "fill": fill, "stop": stop, "book": book, "target_pct": ex["target_pct"]})
                except Exception as e:
                    self.j.mark_thesis(t["id"], reject_reason=f"order failed: {str(e)[:80]}")
                    self.j.log("ERROR", f"{t['symbol']} order failed: {str(e)[:160]}")
        self._mark_equity()
        return done

    def _default_book(self):
        return "day" if "day" in self.cfg.books and (self.cfg.hold_mode == "day" or self.r.entry_window("day")[0]) else list(self.cfg.books)[-1]

    def propose(self, symbol, side="buy", book=None):
        """Operator asked for a specific name. Return what the rules engine would do, without doing it."""
        symbol = symbol.upper(); book = book if book in self.cfg.books else self._default_book()
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
        ok, why = self.r.entry_window(book)
        if not ok: return {"ok": False, "why": f"{book} book: {why}"}
        try: stat = self.b.stats([symbol]).get(symbol)
        except Exception: stat = None
        qty, stop, why, ex = self.r.size(th, self._positions_with_sector(book), {symbol: info["price"]}, book=book, all_symbols=[x["symbol"] for x in self.j.open_trades()], stat=stat)
        if not qty: return {"ok": False, "why": why}
        research_view = f"Research today: conviction {existing[0]['conviction']}/10, {existing[0]['catalyst']}" if existing else "Research has no thesis on this name today."
        return {"ok": True, "action": f"BUY {qty} {symbol} at ~{info['price']:.2f} (${qty*info['price']:.0f}) in the {book} book, stop {stop:.2f} (-{ex['stop_pct']}%), target +{ex['target_pct']}%", "symbol": symbol, "side": "buy",
                "qty": qty, "stop": stop, "book": book, "research_view": research_view}

    def operator_trade(self, symbol, side="buy", book=None):
        """Confirmed by the operator: run it. Buys go through execute() so the same code path places the stop."""
        symbol = symbol.upper(); book = book if book in self.cfg.books else self._default_book()
        if side == "sell":
            t = self.j.trade_for(symbol)
            if not t: return {"ok": False, "why": "no position"}
            self._exit(t, "operator")
            return {"ok": True}
        day = dt.date.today().isoformat()
        if not any(t["symbol"] == symbol and not t["acted"] for t in self.j.theses_for(day)):
            self.j.add_theses(day, [{"symbol": symbol, "sector": "", "catalyst": "operator direction", "reason": "operator asked for it",
                                     "invalidation": "operator changes his mind", "conviction": self.cfg.get("min_conviction"), "operator_directed": True, "book": book}], source="operator")
        else:
            for t in self.j.theses_for(day):
                if t["symbol"] == symbol and not t["acted"]:
                    self.j._x("UPDATE theses SET conviction=MAX(conviction,?), reject_reason=NULL, operator_directed=1, book=? WHERE id=?", self.cfg.get("min_conviction"), book, t["id"])
        done = self.execute(day, only_symbol=symbol, books=[book])
        self.j.log("INFO", f"operator directed {side} {symbol}")
        return {"ok": bool(done), "done": done}

    def review(self, use_llm=True, quiet=False, book=None):
        """3:45 for the swing book, every few minutes for the day book: stop-outs, take profit, trailing stops, and (use_llm) thesis invalidation."""
        if self.r.halted:
            if not quiet: self.j.log("SKIP", "halted; no review")
            return
        self.r.check_caps()
        trades = self.j.open_trades(book)
        if not trades:
            if not quiet: self.j.log("REVIEW", "nothing open")
            self._mark_equity(); return
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
            if use_llm:
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

    def flatten(self, book="day", stale_only=False):
        """15:55: sell everything still open in the day book. book=None flattens both; that is the operator's 'go flat'. stale_only: only trades opened before today."""
        trades = self.j.open_trades(book)
        if stale_only:
            today = dt.date.today().isoformat()
            trades = [t for t in trades if (t["entry_ts"] or "")[:10] < today]
            if not trades: return []
            self.j.log("FLATTEN", f"sweep: {len(trades)} day-book position(s) held overnight; selling at the open")
        try:
            if not self.b.clock().get("is_open", True): self.j.log("WARN", "flatten with the market closed: orders will fill at the next session")
        except Exception: pass
        if not trades: self.j.log("FLATTEN", "nothing open"); self._mark_equity(); return []
        held = {p["symbol"] for p in self.b.positions()}
        prices = self.b.prices_for([t["symbol"] for t in trades])
        out = []
        for t in trades:
            if t["symbol"] not in held:
                self.j.close_trade(t["id"], t["stop"] or prices.get(t["symbol"]) or t["entry"], "stop"); self.j.log("EXIT", f"{t['symbol']} stopped out near {t['stop']:.2f}"); continue
            self._exit(t, "close", prices.get(t["symbol"])); out.append(t["symbol"])
        self._mark_equity()
        return out

    def _exit(self, t, reason, px=None):
        try:
            if t.get("stop_order_id"):
                try: self.b.cancel_order(t["stop_order_id"])
                except Exception as e: self.j.log("WARN", f"{t['symbol']} stop cancel failed: {str(e)[:100]}")
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
