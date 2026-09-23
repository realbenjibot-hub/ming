"""Ming's files: ming.md and operator.md ship with the repo; ideas.md lives on DATA_DIR so it survives redeploys."""
import datetime as dt
from .config import ROOT, DATA_DIR

MING = ROOT / "ming" / "ming.md"
OPERATOR = ROOT / "ming" / "operator.md"
DAYMODE = ROOT / "ming" / "daytrading.md"
IDEAS = DATA_DIR / "ideas.md"
LESSONS = DATA_DIR / "lessons.md"

def _read(p):
    try: return open(p).read()
    except FileNotFoundError: return ""

def persona(hold_mode=None):
    if hold_mode is None:
        try:
            from .config import Config; hold_mode = Config().hold_mode
        except Exception: hold_mode = "swing"
    extra = ("\n\n" + _read(DAYMODE)) if hold_mode == "day" else ""
    les = lessons()
    if les: extra += "\n\n## What you have learned from your own trades (newest last; these are yours, weigh them)\n" + les
    return _read(MING) + "\n\n" + _read(OPERATOR) + extra

def lessons(max_lines=40):
    """The last max_lines lessons Ming wrote about his own trades."""
    lines = [l for l in _read(LESSONS).splitlines() if l.strip()]
    return "\n".join(lines[-max_lines:])

def add_lessons(day, items):
    LESSONS.parent.mkdir(parents=True, exist_ok=True)
    with open(LESSONS, "a") as f:
        for it in items: f.write(f"- [{day}] {it.strip()}\n")
    return lessons()

def ideas():
    if not IDEAS.exists() and (ROOT / "ming" / "ideas.md").exists():   # first boot: seed from the repo
        IDEAS.parent.mkdir(parents=True, exist_ok=True); IDEAS.write_text((ROOT / "ming" / "ideas.md").read_text())
    return _read(IDEAS).strip() or "(no standing direction from the operator yet)"

def add_idea(text, who="operator"):
    IDEAS.parent.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(IDEAS, "a") as f:
        f.write(f"- [{stamp}] ({who}) {text.strip()}\n")
    return ideas()

def clear_ideas():
    if IDEAS.exists(): IDEAS.unlink()
    return ideas()
