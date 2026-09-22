"""Config: config.yaml plus DATA_DIR/overrides.json plus the aggression preset. One object, read everywhere."""
import os, json, yaml
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

class Config:
    def __init__(self, path=ROOT / "config.yaml"):
        self.path = path
        self.raw = yaml.safe_load(open(path))
        self.EDITABLE = list(self.raw.get("editable", []))
        self.AGGRESSION = {int(k): v for k, v in self.raw.get("aggression", {}).items()}
        self.overrides_path = DATA_DIR / "overrides.json"
        self.overrides = json.load(open(self.overrides_path)) if self.overrides_path.exists() else {}

    # ---- mode ----
    @property
    def mode(self):
        return self.raw.get("mode", "paper")
    @property
    def live(self):
        return self.mode == "live" and os.environ.get("LIVE_CONFIRM") == "YES"
    @property
    def tz(self):
        return self.raw.get("timezone", "America/New_York")
    @property
    def schedule(self):
        return self.raw.get("schedule", {})
    @property
    def hold_mode(self):
        return str(self.overrides.get("hold_mode") or self.raw.get("hold_mode", "swing"))
    @property
    def day(self):
        return dict(self.raw.get("day", {}))
    DAY_EXIT_KEYS = ("stop_loss_pct", "take_profit_pct", "trail_trigger_pct", "trail_pct")
    @property
    def llm(self):
        d = dict(self.raw.get("llm", {}))
        d["model"] = os.environ.get("OPENAI_MODEL", d.get("model"))
        d["voice_model"] = os.environ.get("OPENAI_VOICE_MODEL", d.get("voice_model"))
        d["voice"] = os.environ.get("OPENAI_VOICE", d.get("voice", "cedar"))
        return d
    @property
    def sources(self):
        return self.raw.get("sources", {})

    # ---- risk: yaml < aggression preset < explicit overrides ----
    @property
    def aggression(self):
        return int(self.overrides.get("aggression", 5))
    @property
    def risk(self):
        r = dict(self.raw.get("risk", {}))
        preset = dict(self.AGGRESSION.get(self.aggression, {}))
        preset.pop("name", None)
        r.update(preset)
        if self.hold_mode == "day":
            r.update({k: v for k, v in self.day.items() if k in self.DAY_EXIT_KEYS})
        r.update({k: v for k, v in self.overrides.items() if k in self.EDITABLE})
        return r
    def get(self, key):
        return self.risk.get(key)

    def set(self, key, value):
        if key == "hold_mode":
            if value not in ("day", "swing"): raise ValueError("hold_mode must be day or swing")
            self.overrides["hold_mode"] = value; self._save(); return
        if key not in self.EDITABLE:
            raise ValueError(f"{key} is not editable")
        self.overrides[key] = float(value) if key != "max_positions" and key != "max_sector_positions" and key != "min_conviction" else int(value)
        self._save()
    def set_aggression(self, level):
        level = int(level)
        if level not in self.AGGRESSION:
            raise ValueError("level must be 1 to 10")
        # a preset resets the knobs it owns so the dial is the truth again
        for k in self.AGGRESSION[level]:
            self.overrides.pop(k, None)
        self.overrides["aggression"] = level
        self._save()
        return self.AGGRESSION[level]["name"]
    def _save(self):
        json.dump(self.overrides, open(self.overrides_path, "w"), indent=1)
