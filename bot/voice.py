"""Voice: mint a short-lived client secret for the OpenAI Realtime API with Ming's persona and tools. The browser connects over WebRTC; tool calls come back to /api/voice/tool."""
import os, httpx
from .ideas import persona
from .tools import realtime_tools

VOICE_RULES = """
You are speaking out loud with the operator. Short spoken sentences. Lead with the answer. Say numbers plainly.
Read the record with tools before answering about the account. For anything that deserves real thought, call think and speak its answer.
For kill, resume, execute, and execute_trade: say exactly what will happen, wait for a spoken yes, then call with confirmed=true.
If the operator interrupts you, stop and listen."""

def session(cfg, svc):
    key = os.environ.get("OPENAI_API_KEY")
    if not key: return {"error": "no OPENAI_API_KEY"}
    body = {"session": {"type": "realtime", "model": cfg.llm["voice_model"],
            "instructions": persona() + VOICE_RULES + "\n\nCURRENT CONTEXT:\n" + svc.context_for_agent(),
            "tools": realtime_tools(), "tool_choice": "auto",
            "audio": {"input": {"turn_detection": {"type": "semantic_vad", "eagerness": "medium"}, "transcription": {"model": "gpt-4o-mini-transcribe"}}, "output": {"voice": cfg.llm["voice"]}}}}
    try:
        r = httpx.post("https://api.openai.com/v1/realtime/client_secrets", headers={"Authorization": f"Bearer {key}"}, json=body, timeout=20)
        if r.status_code >= 300: return {"error": f"openai {r.status_code}: {r.text[:200]}"}
        d = r.json()
        return {"client_secret": d.get("value") or d.get("client_secret", {}).get("value"), "model": cfg.llm["voice_model"], "expires_at": d.get("expires_at")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:120]}"}
