"""Research: gather the pack Ming reads. Tier 1 filings and wires, tier 2 news, Alpaca news and movers, Firecrawl confirmation, the operator's ideas."""
import os, re, datetime as dt, feedparser, httpx
from .ideas import ideas

UA = {"User-Agent": "Ming research bot (contact: operator@example.com)"}

class Research:
    def __init__(self, cfg, broker, journal):
        self.cfg = cfg; self.b = broker; self.j = journal
        self.fc_key = os.environ.get("FIRECRAWL_API_KEY")

    def _feed(self, name, url, limit=40, hours=30):
        try:
            r = httpx.get(url, headers=UA, timeout=15, follow_redirects=True)
            f = feedparser.parse(r.text)
            cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours)
            out = []
            for e in f.entries[:limit]:
                ts = e.get("published_parsed") or e.get("updated_parsed")
                if ts:
                    t = dt.datetime(*ts[:6], tzinfo=dt.timezone.utc)
                    if t < cutoff: continue
                    stamp = t.strftime("%m-%d %H:%M")
                else: stamp = ""
                title = re.sub(r"\s+", " ", e.get("title", "")).strip()
                summ = re.sub(r"<[^>]+>", "", e.get("summary", "") or "")[:200].strip()
                out.append(f"[{name} {stamp}] {title}" + (f" — {summ}" if summ and summ != title else ""))
            return out
        except Exception as e:
            self.j.log("WARN", f"feed {name} failed: {type(e).__name__}"); return []

    def _firecrawl(self, query, limit=5):
        if not self.fc_key: return []
        try:
            r = httpx.post("https://api.firecrawl.dev/v1/search", timeout=40, headers={"Authorization": f"Bearer {self.fc_key}"},
                           json={"query": query, "limit": limit, "tbs": "qdr:d"})
            data = r.json().get("data") or []
            ex = self.cfg.sources.get("excluded_domains", [])
            out = []
            for d in data:
                url = d.get("url", "")
                if any(x in url for x in ex): continue
                out.append(f"[web] {d.get('title','')[:120]} — {d.get('description','')[:220]} ({url})")
            return out
        except Exception as e:
            self.j.log("WARN", f"firecrawl failed: {type(e).__name__}"); return []

    def gather(self, refresh=False):
        """refresh=True is the 9:00 pass: movers, latest news, filings since 6:00. Returns a text pack and a dict of parts."""
        parts = {}
        src = self.cfg.sources
        hours = 4 if refresh else 30
        tiers = ["tier2"] if refresh else ["tier1", "tier2"]
        for tier in tiers:
            lines = []
            for s in src.get(tier, []):
                lines += self._feed(s["name"], s["url"], hours=hours)
            parts[tier] = lines
        if not refresh:
            parts["tier1_filings"] = parts.get("tier1", [])
        try:
            parts["alpaca_news"] = [f"[alpaca {n['ts'][5:16]}] {n['headline']} {' '.join(n['symbols'][:4])} — {n['summary'][:160]}" for n in self.b.news(limit=60)]
        except Exception as e:
            self.j.log("WARN", f"alpaca news failed: {type(e).__name__}"); parts["alpaca_news"] = []
        try:
            m = self.b.movers(top=25); minp = self.cfg.get("min_price")
            parts["movers"] = [f"{x['symbol']} ${x['price']:.2f} {x['change_pct']:+.1f}%" for x in m["gainers"] + m["losers"] if x["price"] >= minp]
        except Exception as e:
            self.j.log("WARN", f"movers failed: {type(e).__name__}"); parts["movers"] = []
        web = []
        for q in src.get("firecrawl_queries", []):
            web += self._firecrawl(q)
        parts["web"] = web
        parts["ideas"] = ideas()
        day = dt.datetime.now().strftime("%A %Y-%m-%d")
        pack = [f"DATE: {day}. PASS: {'9:00 refresh' if refresh else '6:00 full research'}.",
                "\nOPERATOR DIRECTION (ideas.md):\n" + parts["ideas"]]
        for k, title in [("tier1", "TIER 1: SEC FILINGS AND PRESS WIRES (primary sources)"), ("tier2", "TIER 2: WIRE SERVICES AND MARKET NEWS"),
                         ("alpaca_news", "ALPACA NEWS FEED"), ("movers", "MOVERS (above the price floor; prior session unless pre-market)"), ("web", "WEB CONFIRMATION (Firecrawl)")]:
            if parts.get(k): pack.append(f"\n{title}:\n" + "\n".join(parts[k][:120]))
        text = "\n".join(pack)
        self.j.log("RESEARCH", f"pack: {sum(len(v) for k,v in parts.items() if isinstance(v,list))} items, {len(text)//1000}k chars" + (" (refresh)" if refresh else ""))
        return text[:60000], parts
