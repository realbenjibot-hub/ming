"""One door to OpenAI. No key -> every call returns None and logs once, so the machine runs without a brain attached.
status is one of: absent (no key), connected (key accepted by OpenAI), rejected (OpenAI answered 401 to the key)."""
import os, json, threading

class LLM:
    def __init__(self, cfg, journal):
        self.cfg = cfg; self.j = journal; self.model = cfg.llm["model"]; self._warned = False
        key = os.environ.get("OPENAI_API_KEY")
        self.client = None; self.status = "absent"
        if key:
            from openai import OpenAI
            self.client = OpenAI(api_key=key); self.status = "connected"
            threading.Thread(target=self.probe, daemon=True).start()
    @property
    def ready(self):
        return self.client is not None and self.status != "rejected"
    @property
    def problem(self):
        """Short reason the brain is unavailable, for logs and replies."""
        return "brain rejected the key" if self.status == "rejected" else "no brain connected"
    def probe(self):
        """Ask OpenAI whether the key is accepted. Free: no tokens. Only a 401 flips status to rejected."""
        if not self.client: return
        try:
            self.client.models.retrieve(self.model); self._accepted()
        except Exception as e:
            self._check(e, "probe", level="WARN")
    def _accepted(self):
        if self.status == "rejected": self.j.log("INFO", "brain connected: OpenAI accepted the key")
        self.status = "connected"
    def _check(self, e, what, level="ERROR"):
        """Classify a failed call. 401 means the key is bad: say so once and stop pretending the brain is on."""
        if type(e).__name__ == "AuthenticationError" or getattr(e, "status_code", None) == 401:
            if self.status != "rejected": self.j.log("ERROR", "brain rejected: OpenAI returned 401 for OPENAI_API_KEY; replace the key in Railway and redeploy")
            self.status = "rejected"
        else:
            self.j.log(level, f"LLM {what} call failed: {type(e).__name__}: {str(e)[:200]}")
    def _warn(self):
        if not self._warned:
            self.j.log("WARN", ("brain rejected: OPENAI_API_KEY is invalid" if self.status == "rejected" else "no OPENAI_API_KEY: Ming has no brain connected") + "; research and chat are skipped"); self._warned = True

    def json(self, system, user, schema_hint="", max_tokens=4000):
        """Ask for a JSON object. Returns a dict or None."""
        if not self.ready: self._warn(); return None
        try:
            r = self.client.chat.completions.create(model=self.model, max_completion_tokens=max_tokens,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": system + ("\n\nRespond with one JSON object only." + schema_hint)},
                          {"role": "user", "content": user}])
            self._accepted()
            txt = r.choices[0].message.content or "{}"
            return json.loads(txt)
        except Exception as e:
            self._check(e, "json"); return None

    def chat(self, messages, tools=None, max_tokens=1500):
        """One chat turn with optional tools. Returns the raw message or None."""
        if not self.ready: self._warn(); return None
        try:
            kw = dict(model=self.model, messages=messages, max_completion_tokens=max_tokens)
            if tools: kw["tools"] = tools; kw["tool_choice"] = "auto"; kw["reasoning_effort"] = "none"   # Sol: tools on chat/completions need reasoning off; hard thinking goes through the think tool
            r = self.client.chat.completions.create(**kw)
            self._accepted()
            return r.choices[0].message
        except Exception as e:
            self._check(e, "chat"); return None
