"""Settings: .env loading and model profiles (TOML)."""
import json
import os
import re
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles"
CALIBRATION_DIR = PROFILES_DIR / "calibration"
RUNS_DIR = ROOT / "runs"


def load_env(path=None):
    """Read KEY=VALUE lines into os.environ without overriding real env vars."""
    path = Path(path) if path else ROOT / ".env"
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            values[k] = v
            os.environ.setdefault(k, v)
    return values


_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(value):
    if isinstance(value, str):
        return _VAR.sub(lambda m: os.environ.get(m.group(1), ""), value)
    return value


@dataclass
class Profile:
    name: str
    model: str
    api_base: str = "${TOKENHARBOR_BASE_URL}"
    api_key: str = "${TOKENHARBOR_API_KEY}"
    # pixels | norm1000 | norm999 | norm1 | auto (use calibration result, else pixels)
    coords: str = "auto"
    # tool_calls (OpenAI function calling) | json_text (JSON in the reply)
    output: str = "tool_calls"
    max_long_edge: int = 1280
    keep_screenshots: int = 3
    evict_batch: int = 2
    # Largest request body the provider accepts, in MB (screenshots included). Older
    # screenshots are evicted early to stay under it. None = no limit.
    max_request_mb: float | None = None
    max_tokens: int = 4096
    temperature: float | None = None
    # Send the model's reasoning back on later turns (some APIs require it
    # during tool loops, most ignore it). true | false
    carry_reasoning: bool = False
    # answer_tool: the run ends when the model calls `answer`.
    # no_tool_call: a reply without any action is taken as the final answer.
    end_mode: str = "answer_tool"
    element_hints: bool = False
    max_elements: int = 120
    # USD per 1M tokens; None = look the price up from the provider's /models.
    price_in: float | None = None
    price_out: float | None = None
    extra_body: dict = field(default_factory=dict)
    notes: str = ""
    source: str = ""

    @property
    def base_url(self):
        return _expand(self.api_base).rstrip("/")

    @property
    def key(self):
        return _expand(self.api_key)

    def resolved_coords(self):
        if self.coords != "auto":
            return self.coords, "profile"
        cal = load_calibration(self.name)
        if cal and cal.get("best", {}).get("convention"):
            return cal["best"]["convention"], "calibration"
        return "pixels", "default"

    def public_dict(self):
        d = asdict(self)
        d["api_key"] = "(hidden)" if self.key else "(missing)"
        d["api_base"] = self.base_url
        return d


def load_profile(name_or_path):
    load_env()
    p = Path(name_or_path)
    if not (p.suffix == ".toml" and p.exists()):
        # a profile name (may contain dots, e.g. "qwen3.8-27b")
        p = PROFILES_DIR / f"{name_or_path}.toml"
    if not p.exists():
        names = ", ".join(list_profiles())
        raise FileNotFoundError(f"profile {name_or_path!r} not found (available: {names})")
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    known = set(Profile.__dataclass_fields__)
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"{p.name}: unknown settings {sorted(unknown)}")
    data.setdefault("name", p.stem)
    prof = Profile(**data)
    prof.source = str(p)
    for attr, allowed in (("coords", ("auto", "pixels", "norm1000", "norm999", "norm1")),
                          ("output", ("tool_calls", "json_text")),
                          ("end_mode", ("answer_tool", "no_tool_call"))):
        if getattr(prof, attr) not in allowed:
            raise ValueError(f"{p.name}: {attr} must be one of {allowed}")
    return prof


def list_profiles():
    return sorted(x.stem for x in PROFILES_DIR.glob("*.toml") if not x.stem.startswith("_"))


def load_calibration(name):
    f = CALIBRATION_DIR / f"{name}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return None


def save_calibration(name, data):
    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    f = CALIBRATION_DIR / f"{name}.json"
    f.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return f
