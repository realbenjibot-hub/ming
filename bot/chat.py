"""Text chat with Ming: the strong model, his files, his tools."""
import json
from .ideas import persona
from .tools import openai_tools, dispatch

RULES = """
You are talking with the operator in the dashboard. Use tools to read the record before you answer about the account; never guess numbers.
For kill, resume, execute, and execute_trade: say exactly what will happen, ask for a yes, and only call with confirmed=true after the operator says yes in this conversation.
Keep replies short. Lead with the answer. No contractions. Plain words."""

class Chat:
    def __init__(self, svc):
        self.svc = svc
    def reply(self, history):
        llm = self.svc.llm
        if not llm.ready: return "My brain is not connected yet. Add the OpenAI key in Railway and I will be here."
        msgs = [{"role": "system", "content": persona() + RULES + "\n\nCURRENT CONTEXT:\n" + self.svc.context_for_agent()}] + history[-16:]
        for _ in range(6):
            m = llm.chat(msgs, tools=openai_tools())
            if m is None: return "I could not reach my brain just now."
            if not m.tool_calls: return (m.content or "").strip() or "..."
            msgs.append({"role": "assistant", "content": m.content or "", "tool_calls": [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in m.tool_calls]})
            for c in m.tool_calls:
                try: args = json.loads(c.function.arguments or "{}")
                except Exception: args = {}
                out = dispatch(self.svc, c.function.name, args)
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": json.dumps(out, default=str)[:8000]})
        return "That took more steps than I expected. Ask me again more narrowly."
