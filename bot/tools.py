"""Ming's tools, shared by text chat and voice. One definition, one dispatcher. Destructive commands need confirmed=true."""
import json

TOOLS = [
  {"name": "get_state", "description": "Account, positions, P&L, edge vs SPY, halt state, settings. Call this before answering anything about the account.", "parameters": {"type": "object", "properties": {}}},
  {"name": "get_theses", "description": "Today's theses with conviction, catalyst, and whether each was taken or skipped and why.", "parameters": {"type": "object", "properties": {}}},
  {"name": "get_trades", "description": "Trade history, newest first.", "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}},
  {"name": "get_log", "description": "Recent activity log lines.", "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}},
  {"name": "get_report", "description": "A past daily report by date YYYY-MM-DD (today if omitted).", "parameters": {"type": "object", "properties": {"day": {"type": "string"}}}},
  {"name": "run_command", "description": "Run one of: research, refresh, execute, review, report, hunt, scan, flatten, kill, resume. hunt is an intraday research pass plus entries (day mode); scan checks stops and targets; flatten sells everything open. kill, resume, execute, hunt and flatten require confirmed=true, and you must first tell the operator exactly what will happen and get a yes.", "parameters": {"type": "object", "properties": {"command": {"type": "string"}, "confirmed": {"type": "boolean"}}, "required": ["command"]}},
  {"name": "set_setting", "description": "Change one risk setting by key (see get_state config), set aggression 1 to 10 with key 'aggression', or set hold_mode to 'day' or 'swing'.", "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": ["number", "string"]}}, "required": ["key", "value"]}},
  {"name": "add_idea", "description": "Record standing direction from the operator: a theme, a sector to favor or avoid, a name to watch, how aggressive to be. Read at every research run.", "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
  {"name": "get_ideas", "description": "Read the operator's standing direction.", "parameters": {"type": "object", "properties": {}}},
  {"name": "propose_trade", "description": "Operator asked to buy or sell a specific name. Returns what the rules engine would do (size, stop) and what research thinks. Show this to the operator and ask for a yes before calling execute_trade.", "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "side": {"type": "string", "enum": ["buy", "sell"]}}, "required": ["symbol"]}},
  {"name": "execute_trade", "description": "Place the trade the operator confirmed after propose_trade. Requires confirmed=true.", "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}, "side": {"type": "string", "enum": ["buy", "sell"]}, "confirmed": {"type": "boolean"}}, "required": ["symbol", "confirmed"]}},
  {"name": "think", "description": "Hand a hard question to your slow, strong reasoning (the research model) and get a considered answer. Use for 'what do you think about X', 'why did you', 'should we', anything that deserves more than a quick reply.", "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
]
DESTRUCTIVE = {"kill", "resume", "execute", "hunt", "flatten"}

def openai_tools():
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}} for t in TOOLS]
def realtime_tools():
    return [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["parameters"]} for t in TOOLS]

def dispatch(svc, name, args):
    args = args or {}
    try:
        if name == "get_state": return svc.state()
        if name == "get_theses": return svc.theses()
        if name == "get_trades": return svc.j.all_trades(int(args.get("n", 30)))
        if name == "get_log": return svc.j.logs(int(args.get("n", 30)))
        if name == "get_report":
            import datetime as dt
            return {"report": svc.j.get(f"report:{args.get('day') or dt.date.today().isoformat()}", "no report for that day")}
        if name == "run_command":
            c = args.get("command", "")
            if c in DESTRUCTIVE and not args.get("confirmed"): return {"ok": False, "needs_confirmation": True, "what": _what(c)}
            return {"ok": True, "result": _trim(svc.run(c))}
        if name == "set_setting":
            k = args.get("key"); v = args.get("value")
            if k == "aggression": return {"ok": True, "name": svc.set_aggression(int(v))}
            if k == "hold_mode": return {"ok": True, "hold_mode": svc.set_hold_mode(str(v))}
            return {"ok": True, "config": svc.set_config(k, v)}
        if name == "add_idea": return {"ok": True, "ideas": svc.add_idea(args.get("text", ""))}
        if name == "get_ideas": return {"ideas": svc.ideas()}
        if name == "propose_trade": return svc.exe.propose(args.get("symbol", ""), args.get("side", "buy"))
        if name == "execute_trade":
            if not args.get("confirmed"): return {"ok": False, "needs_confirmation": True}
            return svc.exe.operator_trade(args.get("symbol", ""), args.get("side", "buy"))
        if name == "think": return {"answer": svc.analyst.think(args.get("question", ""), svc.context_for_agent())}
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}

def _what(c):
    return {"kill": "cancel every open order, sell every position at market, and halt until the operator resumes",
            "resume": "clear the halt and reset the baseline equity to right now",
            "execute": "take today's theses through the rules engine and place real orders",
            "hunt": "read the last hour of news and movers, write new theses, and place orders for what qualifies",
            "flatten": "cancel every stop and sell every open position at market"}.get(c, c)
def _trim(x):
    s = json.dumps(x, default=str)
    return json.loads(s[:6000]) if len(s) <= 6000 else s[:6000]
