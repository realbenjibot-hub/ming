"""Analyst: Ming reads the pack and writes theses. He never sizes or places anything."""
import json
from .ideas import persona

SCHEMA = """
Schema: {"theses":[{"symbol":"NVDA","sector":"Semiconductors","catalyst":"one line, what happened","reason":"why it moves the stock in the next days","invalidation":"what would prove this wrong","conviction":7,"priced_in":false,"operator_directed":false,"horizon":"day","sources":["SEC 8-K","CNBC"]}],"sat_out_because":"optional one line if the list is empty"}
horizon is "day" when the catalyst plays out within today's session and "swing" when it needs days. Set it on every thesis.
Rules: long only; US stocks and ETFs above the price floor; conviction is 1 to 10; max theses as instructed; an empty list is allowed and often right.
A thesis with priced_in true must have conviction 5 or lower. Every thesis must name at least one source from the pack. Do not invent tickers.
If the operator's direction applies, say so in reason and set operator_directed true."""

class Analyst:
    def __init__(self, cfg, llm, journal):
        self.cfg = cfg; self.llm = llm; self.j = journal

    def theses(self, pack, refresh=False, existing=None, intraday=False):
        if not self.llm.ready: return [], self.llm.problem
        risk = self.cfg.risk
        which = "intraday hunt" if intraday else ("9:00 refresh" if refresh else "6:00 research pass")
        sys = persona(self.cfg.hold_mode) + f"\n\nYou are running the {which}. Current rules: min price ${risk['min_price']}, min conviction to trade {risk['min_conviction']}, max positions {risk['max_positions']}, stop {risk['stop_loss_pct']}%, target {risk['take_profit_pct']}%. Write at most {self.cfg.llm.get('max_theses',8)} theses."
        if self.cfg.hold_mode == "both" and not intraday:
            sys += (f"\n\nTWO BOOKS. You run a day book and a swing book side by side, each with its own capital. Tag every thesis with horizon day or swing. "
                    f"Day: the catalyst plays out within today's session and the position is sold at {self.cfg.schedule.get('flatten', '15:55')} ET; it needs volume behind it now. Swing: the catalyst needs days; the position rides a wider stop. "
                    "A name can be in only one book. When in doubt, swing.")
        if intraday or self.cfg.hold_mode == "day":
            sys += (f"\n\nDAY MODE. Every position is sold at {self.cfg.schedule.get('flatten', '15:55')} ET today, no exceptions. A thesis only counts if the catalyst can move the stock within hours, today. "
                    "Yesterday's news that already gapped at the open is priced in. Prefer fresh catalysts with volume behind them: an earnings beat still running, guidance, an FDA decision, a contract, an upgrade this morning, a sector move with a clear driver. "
                    "Avoid names that already ran more than 15% today unless the catalyst is still unfolding, thin names, and anything without a source in the pack. Fewer, cleaner theses beat a long list.")
        user = pack
        if refresh and existing:
            user += "\n\nYOUR 6:00 THESES (revise conviction, drop what is priced in, add only if something new and real happened):\n" + json.dumps(existing)[:6000]
        if intraday and existing:
            user += "\n\nALREADY COVERED TODAY (do not repeat these names; write only what is new since the last pass, or nothing):\n" + ", ".join(sorted({t["symbol"] for t in existing}))
        out = self.llm.json(sys, user, SCHEMA, max_tokens=5000)
        if not out: return [], "LLM failed"
        th = []
        for t in out.get("theses", []):
            try:
                sym = str(t["symbol"]).upper().strip()
                if not sym or len(sym) > 6: continue
                th.append({"symbol": sym, "sector": t.get("sector", ""), "catalyst": str(t.get("catalyst", ""))[:300], "reason": str(t.get("reason", ""))[:500],
                           "invalidation": str(t.get("invalidation", ""))[:300], "conviction": max(1, min(10, int(t.get("conviction", 0)))),
                           "priced_in": bool(t.get("priced_in")), "operator_directed": bool(t.get("operator_directed")), "sources": t.get("sources", []),
                           "book": self._book(t, intraday)})
            except Exception: continue
        th.sort(key=lambda x: -x["conviction"])
        return th, out.get("sat_out_because", "")

    def _book(self, t, intraday):
        hm = self.cfg.hold_mode
        if intraday or hm == "day": return "day"
        if hm == "swing": return "swing"
        return "day" if str(t.get("horizon", "")).lower().startswith("day") else "swing"

    def invalidated(self, trade, thesis, price, news_lines):
        """Afternoon check: is the reason for the trade gone? Returns (bool, why)."""
        if not self.llm.ready or not thesis: return False, (self.llm.problem if not self.llm.ready else "no thesis")
        sys = persona(self.cfg.hold_mode) + "\n\nYou are reviewing an open position at 3:45 PM ET. Decide only whether the original thesis is invalidated. Be strict: normal noise is not invalidation."
        user = f"POSITION: {trade['symbol']} entry {trade['entry']:.2f} now {price:.2f} ({(price/trade['entry']-1)*100:+.1f}%)\nTHESIS: {thesis.get('catalyst')}\nREASON: {thesis.get('reason')}\nINVALIDATION CONDITION: {thesis.get('invalidation')}\nTODAY'S NEWS ON IT:\n" + "\n".join(news_lines[:30])
        out = self.llm.json(sys, user, '\nSchema: {"invalidated":false,"why":"one line"}', max_tokens=300)
        if not out: return False, "LLM failed"
        return bool(out.get("invalidated")), str(out.get("why", ""))[:200]

    def think(self, question, context):
        """Slow thinking for the voice agent: a real answer from the strong model."""
        if not self.llm.ready: return "OpenAI rejected my key. Ask the operator to replace it in Railway." if self.llm.status == "rejected" else "I do not have my brain connected yet. Ask the operator to add the OpenAI key."
        sys = persona(self.cfg.hold_mode) + "\n\nThe operator asked you to think hard about something. Answer in your own voice, in a few short paragraphs at most, with numbers from the context when you have them. Never predict a price."
        m = self.llm.chat([{"role": "system", "content": sys}, {"role": "user", "content": f"CONTEXT:\n{context}\n\nQUESTION: {question}"}], max_tokens=800)
        return (m.content if m else "I could not think that through right now.").strip()
