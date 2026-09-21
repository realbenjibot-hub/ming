"""Alpaca wrapper. Same code paper or live; the keys and the mode decide. A FakeBroker exists for tests and for running without keys."""
import os, datetime as dt

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
    def close_all(self):
        for s in list(self.pos): self.market_sell(s)
    def clock(self):
        now = dt.datetime.now(dt.timezone.utc); return {"is_open": False, "now": now.isoformat()}
    def news(self, symbols=None, limit=50): return []
    def movers(self, top=20): return {"gainers": [], "losers": []}
    def tradable(self, s): return {"tradable": True, "price": self._price(s)}


class AlpacaBroker:
    name = "alpaca"
    def __init__(self, key, secret, paper=True):
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.historical.screener import ScreenerClient
        self.paper = paper
        self.tc = TradingClient(key, secret, paper=paper)
        self.dc = StockHistoricalDataClient(key, secret)
        self.nc = NewsClient(key, secret)
        self.sc = ScreenerClient(key, secret)
    def account(self):
        a = self.tc.get_account()
        return {"equity": float(a.equity), "cash": float(a.cash), "buying_power": float(a.buying_power), "status": str(a.status)}
    def positions(self):
        out = []
        for p in self.tc.get_all_positions():
            out.append({"symbol": p.symbol, "qty": float(p.qty), "entry": float(p.avg_entry_price), "price": float(p.current_price),
                        "pl": float(p.unrealized_pl), "pl_pct": float(p.unrealized_plpc) * 100})
        return out
    def price(self, s):
        return self.prices_for([s]).get(s)
    def prices_for(self, syms):
        from alpaca.data.requests import StockLatestTradeRequest
        if not syms: return {}
        r = self.dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=list(syms)))
        return {k: float(v.price) for k, v in r.items()}
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
