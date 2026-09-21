"""One door to OpenAI. No key -> every call returns None and logs once, so the machine runs without a brain attached."""
import os, json

class LLM:
    def __init__(self, cfg, journal):
        self.cfg = cfg; self.j = journal; self.model = cfg.llm["model"]; self._warned = False
        key = os.environ.get("OPENAI_API_KEY")
        self.client = None
        if key:
            from openai import OpenAI
            self.client = OpenAI(api_key=key)
    @property
    def ready(self):
        return self.client is not None
    def _warn(self):
        if not self._warned:
            self.j.log("WARN", "no OPENAI_API_KEY: Ming has no brain connected; research and chat are skipped"); self._warned = True

    def json(self, system, user, schema_hint="", max_tokens=4000):
        """Ask for a JSON object. Returns a dict or None."""
        if not self.client: self._warn(); return None
        try:
            r = self.client.chat.completions.create(model=self.model, max_completion_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system + ("\n\nRespond with one JSON object only." + schema_hint)},
                          {"role": "user", "content": user}])
            txt = r.choices[0].message.content or "{}"
            return json.loads(txt)
        except Exception as e:
            self.j.log("ERROR", f"LLM json call failed: {type(e).__name__}: {str(e)[:200]}"); return None

    def chat(self, messages, tools=None, max_tokens=1500):
        """One chat turn with optional tools. Returns the raw message or None."""
        if not self.client: self._warn(); return None
        try:
            kw = dict(model=self.model, messages=messages, max_completion_tokens=max_tokens)
            if tools: kw["tools"] = tools; kw["tool_choice"] = "auto"; kw["reasoning_effort"] = "none"   # Sol: tools on chat/completions need reasoning off; hard thinking goes through the think tool
            r = self.client.chat.completions.create(**kw)
            return r.choices[0].message
        except Exception as e:
            self.j.log("ERROR", f"LLM chat call failed: {type(e).__name__}: {str(e)[:200]}"); return None
