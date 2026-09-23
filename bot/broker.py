"""Alpaca wrapper. Same code paper or live; the keys and the mode decide. A FakeBroker exists for tests and for running without keys."""
import os, re, datetime as dt

OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")   # an option contract symbol, e.g. NVO260930C00040000
def is_option(sym): return bool(OCC.match(sym or ""))
def contract_label(c):
    """NVO 40C 09-30: what the page and the log show for a contract."""
    try: return f"{c['underlying']} {float(c['strike']):g}{'C' if str(c['type']).lower().startswith('c') else 'P'} {str(c['expiry'])[5:]}"
    except Exception: return c.get("symbol", "?")

class FakeBroker:
    """In-memory broker for tests and for booting with no keys. Prices are static unless set."""
    name = "fake"
    def __init__(self, equity=100000.0):
        self.cash = equity; self.pos = {}; self.prices = {"SPY": 500.0}; self.stops = {}; self._oid = 0
    def _price(self, s): return self.prices.get(s, 100.0)
    def account(self):
        eq = self.cash + sum(p["qty"] * self._price(s) for s, p in self.pos.items())
        return {"equity": eq, "cash": self.cash, "buying_power": self.cash, "status": "ACTIVE"}
    def positions(self):
        out = []
        for s, p in self.pos.items():
            px = self._price(s); out.append({"symbol": s, "qty": p["qty"], "entry": p["entry"], "price": px,
                "pl": (px - p["entry"]) * p["qty"], "pl_pct": (px / p["entry"] - 1) * 100})
        return out
    def price(self, s): return self._price(s)
    def prices_for(self, syms): return {s: self._price(s) for s in syms}
    def market_buy(self, s, qty):
        self._oid += 1; px = self._price(s); self.cash -= px * qty
        p = self.pos.setdefault(s, {"qty": 0, "entry": px}); p["entry"] = (p["entry"] * p["qty"] + px * qty) / (p["qty"] + qty); p["qty"] += qty
        return {"id": f"fake-{self._oid}", "filled_avg_price": px, "filled_qty": qty}
    def market_sell(self, s, qty=None):
        self._oid += 1; p = self.pos.get(s)
        if not p: return None
        qty = qty or p["qty"]; px = self._price(s); self.cash += px * qty; p["qty"] -= qty
        if p["qty"] <= 0: self.pos.pop(s, None); self.stops.pop(s, None)
        return {"id": f"fake-{self._oid}", "filled_avg_price": px, "filled_qty": qty}
    def stop_order(self, s, qty, stop):
        self._oid += 1; self.stops[s] = {"id": f"fake-stop-{self._oid}", "stop": stop}; return self.stops[s]["id"]
    def replace_stop(self, order_id, stop):
        for s, v in self.stops.items():
            if v["id"] == order_id: v["stop"] = stop; return order_id
        return order_id
    def cancel_all(self): self.stops.clear()
    def cancel_order(self, order_id):
        for s, v in list(self.stops.items()):
            if v["id"] == order_id: self.stops.pop(s)
    def close_all(self):
        for s in list(self.pos): self.market_sell(s)
    def clock(self):
        now = dt.datetime.now(dt.timezone.utc); return {"is_open": False, "now": now.isoformat()}
    def news(self, symbols=None, limit=50): return []
    def movers(self, top=20): return {"gainers": [], "losers": []}
    def tradable(self, s): return {"tradable": True, "price": self._price(s)}
    def stats(self, syms): return {s: self.fake_stats.get(s, {}) for s in syms} if hasattr(self, "fake_stats") else {}
    def option_pick(self, underlying, direction, o):
        """Synthetic near-the-money contract a week out, priced at 3% of the underlying."""
        px = self._price(underlying); typ = "call" if direction == "up" else "put"
        exp = (dt.date.today() + dt.timedelta(days=int(o.get("dte_target", 7)))).isoformat()
        strike = round(px); sym = f"{underlying}{exp[2:].replace('-', '')}{'C' if typ == 'call' else 'P'}{int(strike * 1000):08d}"
        mid = self.prices.setdefault(sym, round(px * 0.03, 2))
        return {"symbol": sym, "underlying": underlying, "type": typ, "strike": strike, "expiry": exp, "bid": round(mid * 0.97, 2), "ask": round(mid * 1.03, 2), "mid": mid, "delta": 0.45 if typ == "call" else -0.45, "oi": 500, "spread_pct": 6.0}, None


class AlpacaBroker:
    name = "alpaca"
    def __init__(self, key, secret, paper=True):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.historical.screener import ScreenerClient
        self.paper = paper
        from alpaca.data.historical.option import OptionHistoricalDataClient
        self.tc = TradingClient(key, secret, paper=paper)
        self.dc = StockHistoricalDataClient(key, secret)
        self.oc = OptionHistoricalDataClient(key, secret)
        self.nc = NewsClient(key, secret)
        self.sc = ScreenerClient(key, secret)
    def account(self):
        a = self.tc.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash), "buying_power": float(a.buying_power), "status": str(a.status),
                "options_level": getattr(a, "options_trading_level", None), "options_bp": float(getattr(a, "options_buying_power", 0) or 0)}
    def positions(self):
        out = []
        for p in self.tc.get_all_positions():
            out.append({"symbol": p.symbol, "qty": float(p.qty), "entry": float(p.avg_entry_price), "price": float(p.current_price),
                        "pl": float(p.unrealized_pl), "pl_pct": float(p.unrealized_plpc) * 100, "asset_class": str(getattr(p, "asset_class", "") or "")})
        return out
    def price(self, s):
        return self.prices_for([s]).get(s)
    def prices_for(self, syms):
        """Latest trade for stocks; quote midpoint for option contracts (option prints are sparse, the mid is the honest mark)."""
        from alpaca.data.requests import StockLatestTradeRequest
        syms = list(dict.fromkeys(s for s in syms if s)); out = {}
        stocks = [s for s in syms if not is_option(s)]; opts = [s for s in syms if is_option(s)]
        if stocks:
            r = self.dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=stocks))
            out.update({k: float(v.price) for k, v in r.items()})
        if opts: out.update(self.option_mids(opts))
        return out
    def option_mids(self, syms):
        from alpaca.data.requests import OptionLatestQuoteRequest
        try:
            r = self.oc.get_option_latest_quote(OptionLatestQuoteRequest(symbol_or_symbols=list(syms)))
            out = {}
            for k, q in r.items():
                b, a = float(q.bid_price or 0), float(q.ask_price or 0)
                if a > 0: out[k] = round((b + a) / 2, 2) if b > 0 else a
            return out
        except Exception: return {}
    def option_pick(self, underlying, direction, o):
        """Pick one contract for a view on the underlying: calls for up, puts for down. Expiry closest to dte_target inside the window,
        strike closest to delta_target (nearest the money when greeks are missing), open interest and spread filters. Returns (contract, reject_reason)."""
        from alpaca.trading.requests import GetOptionContractsRequest
        from alpaca.trading.enums import ContractType, AssetStatus
        from alpaca.data.requests import OptionSnapshotRequest
        typ = ContractType.CALL if direction == "up" else ContractType.PUT
        today = dt.date.today(); lo = today + dt.timedelta(days=int(o.get("dte_min", 3))); hi = today + dt.timedelta(days=int(o.get("dte_max", 14)))
        px = self.price(underlying)
        if not px: return None, "no underlying price"
        try:
            r = self.tc.get_option_contracts(GetOptionContractsRequest(underlying_symbols=[underlying], status=AssetStatus.ACTIVE, type=typ,
                    expiration_date_gte=lo, expiration_date_lte=hi, strike_price_gte=str(round(px * 0.85, 2)), strike_price_lte=str(round(px * 1.15, 2)), limit=300))
            cs = list(r.option_contracts or [])
        except Exception as e: return None, f"chain failed: {str(e)[:80]}"
        if not cs: return None, "no contracts in the expiry window"
        target = today + dt.timedelta(days=int(o.get("dte_target", 7)))
        best_exp = min({c.expiration_date for c in cs}, key=lambda d: abs((d - target).days))
        cs = [c for c in cs if c.expiration_date == best_exp and (c.open_interest is None or float(c.open_interest) >= float(o.get("min_open_interest", 100)))]
        if not cs: return None, f"open interest below {o.get('min_open_interest', 100)}"
        syms = [c.symbol for c in cs]
        try: snaps = self.oc.get_option_snapshot(OptionSnapshotRequest(symbol_or_symbols=syms))
        except Exception as e: snaps = {}
        cands = []
        for c in cs:
            sn = snaps.get(c.symbol); q = getattr(sn, "latest_quote", None) if sn else None
            b, a = (float(q.bid_price or 0), float(q.ask_price or 0)) if q else (0.0, 0.0)
            if a <= 0: continue
            mid = (b + a) / 2 if b > 0 else a
            delta = None
            g = getattr(sn, "greeks", None) if sn else None
            if g is not None and getattr(g, "delta", None) is not None: delta = abs(float(g.delta))
            spread = (a - b) / mid * 100 if (mid > 0 and b > 0) else 100.0
            cands.append({"symbol": c.symbol, "underlying": underlying, "type": "call" if typ == ContractType.CALL else "put", "strike": float(c.strike_price), "expiry": best_exp.isoformat(),
                          "bid": b, "ask": a, "mid": round(mid, 2), "delta": delta, "oi": float(c.open_interest or 0), "spread_pct": round(spread, 1)})
        if not cands: return None, "no quotes on the chain"
        dt_ = float(o.get("delta_target", 0.45))
        cands.sort(key=lambda c: abs(c["delta"] - dt_) if c["delta"] is not None else abs(c["strike"] - px) / px)
        for c in cands:
            if c["spread_pct"] > float(o.get("max_spread_pct", 10)): continue
            if c["mid"] < float(o.get("min_premium", 0.30)): continue
            return c, None
        c = cands[0]
        return None, f"best contract {contract_label(c)} rejected: spread {c['spread_pct']}%, premium {c['mid']:.2f}"
    def market_buy(self, s, qty):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        o = self.tc.submit_order(MarketOrderRequest(symbol=s, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.DAY))
        return self._wait(o.id)
    def market_sell(self, s, qty=None):
        if qty is None:
            o = self.tc.close_position(s)
        else:
            from alpaca.trading.requests import MarketOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            o = self.tc.submit_order(MarketOrderRequest(symbol=s, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY))
        return self._wait(o.id)
    def _wait(self, oid, tries=20):
        import time
        for _ in range(tries):
            o = self.tc.get_order_by_id(oid)
            if o.filled_avg_price: return {"id": str(o.id), "filled_avg_price": float(o.filled_avg_price), "filled_qty": float(o.filled_qty)}
            time.sleep(0.5)
        return {"id": str(oid), "filled_avg_price": None, "filled_qty": 0}
    def stop_order(self, s, qty, stop):
        from alpaca.trading.requests import StopOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        o = self.tc.submit_order(StopOrderRequest(symbol=s, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.GTC, stop_price=round(stop, 2)))
        return str(o.id)
    def replace_stop(self, order_id, stop):
        from alpaca.trading.requests import ReplaceOrderRequest
        o = self.tc.replace_order_by_id(order_id, ReplaceOrderRequest(stop_price=round(stop, 2)))
        return str(o.id)
    def cancel_all(self):
        self.tc.cancel_orders()
    def cancel_order(self, order_id):
        self.tc.cancel_order_by_id(order_id)
    def close_all(self):
        self.tc.cancel_orders(); self.tc.close_all_positions(cancel_orders=True)
    def clock(self):
        c = self.tc.get_clock(); return {"is_open": c.is_open, "now": c.timestamp.isoformat(), "next_open": c.next_open.isoformat(), "next_close": c.next_close.isoformat()}
    def news(self, symbols=None, limit=50):
        from alpaca.data.requests import NewsRequest
        r = self.nc.get_news(NewsRequest(symbols=",".join(symbols) if symbols else None, limit=limit))
        items = r.data.get("news", []) if hasattr(r, "data") else list(r)
        return [{"headline": i.headline, "summary": getattr(i, "summary", "") or "", "symbols": list(getattr(i, "symbols", []) or []),
                 "source": getattr(i, "source", "alpaca"), "ts": i.created_at.isoformat() if getattr(i, "created_at", None) else "", "url": getattr(i, "url", "")} for i in items]
    def movers(self, top=20):
        from alpaca.data.requests import MarketMoversRequest
        m = self.sc.get_market_movers(MarketMoversRequest(top=top))
        f = lambda xs: [{"symbol": x.symbol, "price": float(x.price), "change_pct": float(x.percent_change)} for x in xs]
        return {"gainers": f(m.gainers), "losers": f(m.losers)}
    def stats(self, syms, days=21):
        """Per symbol: gap from the prior close, today's volume against the 20 day average, average daily range (ATR as a percent of price), today's range.
        One bars request for all names. Missing data means a missing key, never a guess."""
        from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
        from alpaca.data.timeframe import TimeFrame
        syms = [x for x in dict.fromkeys(syms) if x]
        if not syms: return {}
        out = {}
        try:
            start = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=int(days * 1.6) + 5)
            bars = self.dc.get_stock_bars(StockBarsRequest(symbol_or_symbols=syms, timeframe=TimeFrame.Day, start=start, limit=days * len(syms)))
            data = bars.data if hasattr(bars, "data") else dict(bars)
        except Exception: data = {}
        try:
            snaps = self.dc.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=syms))
        except Exception: snaps = {}
        today = dt.datetime.now(dt.timezone.utc).date()
        for s in syms:
            bs = [b for b in (data.get(s) or []) if b.timestamp.date() < today][-20:]
            snap = snaps.get(s); d = {}
            if bs:
                closes = [float(b.close) for b in bs]; vols = [float(b.volume) for b in bs]
                trs = [(float(b.high) - float(b.low)) / float(b.close) * 100 for b in bs[-14:] if float(b.close) > 0]
                if trs: d["atr_pct"] = round(sum(trs) / len(trs), 2)
                d["prev_close"] = closes[-1]; d["avg_vol"] = sum(vols) / len(vols)
            if snap is not None:
                try:
                    px = float(snap.latest_trade.price) if snap.latest_trade else None
                    if px: d["price"] = px
                    db = snap.daily_bar
                    if db and db.timestamp.date() == today:
                        d["open"] = float(db.open); d["vol"] = float(db.volume); d["high"] = float(db.high); d["low"] = float(db.low)
                        if d.get("avg_vol"): d["rel_vol"] = round(d["vol"] / d["avg_vol"], 2)
                        if px: d["range_pct"] = round((d["high"] - d["low"]) / px * 100, 2)
                    if d.get("prev_close") and px: d["gap_pct"] = round((px / d["prev_close"] - 1) * 100, 2)
                except Exception: pass
            out[s] = d
        return out
    def tradable(self, s):
        try:
            a = self.tc.get_asset(s)
            return {"tradable": bool(a.tradable) and str(a.status).endswith("ACTIVE"), "price": self.price(s)}
        except Exception as e:
            return {"tradable": False, "price": None, "error": str(e)[:120]}


def make_broker(cfg, journal):
    key, sec = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
    if os.environ.get("MING_FAKE_BROKER") == "1" or not (key and sec):
        journal.log("WARN", "no Alpaca keys: using the fake broker (nothing is real)")
        return FakeBroker()
    return AlpacaBroker(key, sec, paper=not cfg.live)
