"""The action set, how models express it, and turning replies into actions.

One internal action set (modelled on Anthropic's computer toolset) is offered
either as OpenAI function tools or, for models without tool calling, as JSON
in the reply. Parsing is deliberately tolerant: models mix spellings
("left_click", "coordinate": [x, y], "keys" vs "text"), so aliases are folded
into one canonical form before anything runs.
"""
import json
import re
from dataclasses import dataclass

ACTION_NAMES = ("click", "move", "drag", "scroll", "type", "key", "wait", "zoom", "screenshot", "answer")
POINTER_ACTIONS = {"click", "move", "drag", "scroll"}
INPUT_ACTIONS = {"click", "move", "drag", "scroll", "type", "key"}


@dataclass
class Action:
    name: str
    args: dict
    call_id: str | None = None
    raw: object = None
    error: str | None = None  # set when the reply could not be turned into a valid action

    def to_dict(self):
        return {"name": self.name, "args": self.args, "call_id": self.call_id, "error": self.error}


def _coord_desc(convention):
    return {"pixels": "pixel coordinate in the latest screenshot",
            "norm1000": "coordinate on the 0-1000 grid of the screenshot",
            "norm999": "coordinate on the 0-999 grid of the screenshot",
            "norm1": "coordinate from 0 to 1 of the screenshot"}[convention]


def tool_schemas(convention):
    num = "number" if convention == "norm1" else "integer"
    c = _coord_desc(convention)

    def fn(name, desc, props, required):
        return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
            "type": "object", "properties": props, "required": required}}}

    xy = {"x": {"type": num, "description": f"x {c}"}, "y": {"type": num, "description": f"y {c}"}}
    return [
        fn("click", "Move the mouse to (x, y) and click. Use clicks=2 for a double click.",
           {**xy, "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "clicks": {"type": "integer", "enum": [1, 2, 3], "default": 1},
            "modifiers": {"type": "array", "items": {"type": "string"},
                          "description": "keys held during the click, e.g. [\"ctrl\"] or [\"shift\"]"}},
           ["x", "y"]),
        fn("move", "Move the mouse to (x, y) without clicking (e.g. to reveal a hover menu).", xy, ["x", "y"]),
        fn("drag", "Press the left button at (x1, y1), drag to (x2, y2) and release.",
           {"x1": {"type": num}, "y1": {"type": num}, "x2": {"type": num}, "y2": {"type": num}},
           ["x1", "y1", "x2", "y2"]),
        fn("scroll", "Scroll the mouse wheel with the pointer at (x, y).",
           {**xy, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
            "amount": {"type": "integer", "description": "wheel notches, 1-15 (about 3 lines each)",
                       "default": 3}},
           ["x", "y", "direction"]),
        fn("type", "Type text into the focused control. \\n presses Enter.",
           {"text": {"type": "string"}}, ["text"]),
        fn("key", "Press a key or key combination, e.g. \"enter\", \"ctrl+s\", \"alt+f4\", \"ctrl+shift+t\".",
           {"keys": {"type": "string"}, "repeat": {"type": "integer", "default": 1}}, ["keys"]),
        fn("wait", "Wait for the screen to update (loading, animations), then look again.",
           {"seconds": {"type": "number", "default": 1.0}}, []),
        fn("zoom", "Look at a region of the screenshot in more detail (x1,y1 top-left, x2,y2 bottom-right). "
                   "Coordinates in later actions still refer to the full screenshot.",
           {"x1": {"type": num}, "y1": {"type": num}, "x2": {"type": num}, "y2": {"type": num}},
           ["x1", "y1", "x2", "y2"]),
        fn("screenshot", "Take a fresh screenshot without doing anything.", {}, []),
        fn("answer", "Finish the task. Put the result or a short report in text. "
                     "Set success=false if the task could not be done.",
           {"text": {"type": "string"}, "success": {"type": "boolean", "default": True}}, ["text"]),
    ]


JSON_FORMAT_HELP = """Reply with your reasoning in one or two short sentences, then exactly one JSON object:
{"actions": [ {"action": "<name>", ...arguments...}, ... ]}
Actions and arguments:
  click x y [button=left|right|middle] [clicks=1|2|3] [modifiers=["ctrl"]]
  move x y
  drag x1 y1 x2 y2
  scroll x y direction=up|down|left|right [amount=3]
  type text
  key keys (e.g. "ctrl+s") [repeat=1]
  wait [seconds=1]
  zoom x1 y1 x2 y2
  screenshot
  answer text [success=true]   (finishes the task)
Example: {"actions": [{"action": "click", "x": 412, "y": 230}, {"action": "type", "text": "hello"}]}"""


# --- normalisation ----------------------------------------------------------
_ALIASES = {
    "left_click": ("click", {}), "leftclick": ("click", {}), "tap": ("click", {}),
    "double_click": ("click", {"clicks": 2}), "doubleclick": ("click", {"clicks": 2}),
    "triple_click": ("click", {"clicks": 3}),
    "right_click": ("click", {"button": "right"}), "rightclick": ("click", {"button": "right"}),
    "middle_click": ("click", {"button": "middle"}),
    "mouse_move": ("move", {}), "hover": ("move", {}), "move_mouse": ("move", {}), "mousemove": ("move", {}),
    "left_click_drag": ("drag", {}), "drag_and_drop": ("drag", {}),
    "type_text": ("type", {}), "write": ("type", {}), "input_text": ("type", {}), "typing": ("type", {}),
    "keypress": ("key", {}), "key_press": ("key", {}), "press": ("key", {}), "hotkey": ("key", {}),
    "press_key": ("key", {}), "keys": ("key", {}),
    "sleep": ("wait", {}), "pause": ("wait", {}),
    "look": ("screenshot", {}), "observe": ("screenshot", {}),
    "done": ("answer", {}), "finish": ("answer", {}), "finished": ("answer", {}), "terminate": ("answer", {}),
    "final_answer": ("answer", {}), "complete": ("answer", {}), "stop": ("answer", {}),
    "fail": ("answer", {"success": False}),
}


def _num(v):
    if isinstance(v, bool):
        raise ValueError("boolean is not a number")
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return float(v.strip()) if "." in v else int(v.strip())
    raise ValueError(f"not a number: {v!r}")


def _pair(v):
    if isinstance(v, (list, tuple)) and len(v) >= 2:
        return _num(v[0]), _num(v[1])
    if isinstance(v, str):
        nums = re.findall(r"-?\d+(?:\.\d+)?", v)
        if len(nums) >= 2:
            return _num(nums[0]), _num(nums[1])
    if isinstance(v, dict) and "x" in v and "y" in v:
        return _num(v["x"]), _num(v["y"])
    raise ValueError(f"not a coordinate pair: {v!r}")


def normalise(name, args):
    """Fold aliases into (canonical name, clean args) or raise ValueError."""
    name = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    args = dict(args or {})
    if name in ("computer", "computer_use", "gui", "mouse", "keyboard", "action") and "action" in args:
        name = str(args.pop("action")).strip().lower()
    if name in _ALIASES:
        name, implied = _ALIASES[name]
        for k, v in implied.items():
            args.setdefault(k, v)
    if name not in ACTION_NAMES:
        raise ValueError(f"unknown action {name!r}; use one of {', '.join(ACTION_NAMES)}")

    out = {}
    if name in ("click", "move", "scroll"):
        if "x" in args and "y" in args:
            out["x"], out["y"] = _num(args["x"]), _num(args["y"])
        else:
            for k in ("coordinate", "coordinates", "point", "position", "start_box", "pos", "xy"):
                if k in args:
                    out["x"], out["y"] = _pair(args[k])
                    break
            else:
                raise ValueError(f"{name} needs x and y")
    if name == "click":
        button = str(args.get("button", "left")).lower()
        if button not in ("left", "right", "middle"):
            raise ValueError("button must be left, right or middle")
        out["button"] = button
        clicks = int(_num(args.get("clicks", args.get("count", args.get("num_clicks", 1)))))
        if clicks not in (1, 2, 3):
            raise ValueError("clicks must be 1, 2 or 3")
        out["clicks"] = clicks
        mods = args.get("modifiers", args.get("modifier", args.get("text")))
        if isinstance(mods, str):
            mods = [m for m in re.split(r"[+\s,]+", mods) if m]
        out["modifiers"] = [str(m) for m in (mods or [])]
    elif name in ("drag", "zoom"):
        if all(k in args for k in ("x1", "y1", "x2", "y2")):
            out.update({k: _num(args[k]) for k in ("x1", "y1", "x2", "y2")})
        elif "region" in args and isinstance(args["region"], (list, tuple)) and len(args["region"]) == 4:
            out.update(dict(zip(("x1", "y1", "x2", "y2"), (_num(v) for v in args["region"]))))
        else:
            start = args.get("start_coordinate", args.get("start", args.get("from")))
            end = args.get("coordinate", args.get("end_coordinate", args.get("end", args.get("to"))))
            if start is None or end is None:
                raise ValueError(f"{name} needs x1, y1, x2, y2")
            (out["x1"], out["y1"]), (out["x2"], out["y2"]) = _pair(start), _pair(end)
        if name == "zoom" and (out["x2"] <= out["x1"] or out["y2"] <= out["y1"]):
            raise ValueError("zoom region needs x2 > x1 and y2 > y1")
    elif name == "scroll":
        direction = str(args.get("direction", args.get("scroll_direction", "down"))).lower()
        if direction not in ("up", "down", "left", "right"):
            raise ValueError("direction must be up, down, left or right")
        out["direction"] = direction
        amount = int(_num(args.get("amount", args.get("scroll_amount", args.get("clicks", 3)))))
        out["amount"] = max(1, min(15, amount))
    elif name == "type":
        text = args.get("text", args.get("content", args.get("value")))
        if not isinstance(text, str) or text == "":
            raise ValueError("type needs non-empty text")
        out["text"] = text
    elif name == "key":
        keys = args.get("keys", args.get("key", args.get("text", args.get("combo"))))
        if isinstance(keys, list):
            keys = "+".join(str(k) for k in keys)
        if not isinstance(keys, str) or not keys.strip():
            raise ValueError("key needs keys, e.g. \"ctrl+s\"")
        out["keys"] = keys.strip()
        out["repeat"] = max(1, min(50, int(_num(args.get("repeat", 1)))))
    elif name == "wait":
        out["seconds"] = max(0.0, min(30.0, float(_num(args.get("seconds", args.get("duration", 1.0))))))
    elif name == "answer":
        text = args.get("text", args.get("content", args.get("answer", args.get("result", ""))))
        out["text"] = text if isinstance(text, str) else json.dumps(text)
        success = args.get("success", True)
        if isinstance(success, str):
            success = success.strip().lower() not in ("false", "no", "0")
        out["success"] = bool(success)
    return name, out


def from_tool_calls(tool_calls):
    actions = []
    for tc in tool_calls or []:
        fnc = tc.get("function") or {}
        name = fnc.get("name", "")
        raw_args = fnc.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
            cname, cargs = normalise(name, args)
            actions.append(Action(cname, cargs, tc.get("id"), tc))
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            actions.append(Action(name or "?", {}, tc.get("id"), tc, error=str(e)))
    return actions


def _json_objects(text):
    """Yield top-level {...} substrings (string-aware brace matching)."""
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield text[start:i + 1]


_FUNC_RX = re.compile(r"\b([a-z_]+)\s*\(([^()]*)\)", re.I)


def from_text(text):
    """Actions from a text reply: JSON first, then function-call syntax."""
    actions = []
    cleaned = re.sub(r"```(?:json)?", "", text or "")
    for blob in _json_objects(cleaned):
        try:
            obj = json.loads(blob)
        except json.JSONDecodeError:
            continue
        items = None
        if isinstance(obj, dict):
            if isinstance(obj.get("actions"), list):
                items = obj["actions"]
            elif "action" in obj or "name" in obj or "type" in obj:
                items = [obj]
        if items is None:
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            it = dict(it)
            if "name" in it and "arguments" in it:
                name, args = it["name"], it["arguments"]
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
            else:
                name = it.pop("action", None) or it.pop("name", None) or it.pop("type", None)
                args = it
            try:
                cname, cargs = normalise(name, args)
                actions.append(Action(cname, cargs, None, it))
            except (ValueError, TypeError) as e:
                actions.append(Action(str(name), {}, None, it, error=str(e)))
        if actions:
            return actions
    # Function-call style: click(412, 230) / click(x=412, y=230) / type("hi")
    for m in _FUNC_RX.finditer(cleaned):
        fname, body = m.group(1), m.group(2)
        try:
            normalise(fname, {})
        except ValueError as e:
            if "unknown action" in str(e):
                continue
        args = {}
        kw = re.findall(r"(\w+)\s*=\s*('[^']*'|\"[^\"]*\"|\[[^\]]*\]|[^,]+)", body)
        if kw:
            for k, v in kw:
                v = v.strip()
                if v[:1] in "'\"":
                    args[k] = v[1:-1]
                else:
                    try:
                        args[k] = json.loads(v)
                    except json.JSONDecodeError:
                        args[k] = v
        else:
            strs = re.findall(r"'([^']*)'|\"([^\"]*)\"", body)
            nums = re.findall(r"-?\d+(?:\.\d+)?", body)
            if strs:
                s = next(a or b for a, b in strs)
                args = {"text": s, "keys": s}
            elif len(nums) >= 4:
                args = dict(zip(("x1", "y1", "x2", "y2"), nums[:4]))
                if fname.lower() in ("click", "move", "scroll", "left_click", "double_click", "right_click"):
                    args = {"x": nums[0], "y": nums[1]}
            elif len(nums) >= 2:
                args = {"x": nums[0], "y": nums[1]}
        try:
            cname, cargs = normalise(fname, args)
            actions.append(Action(cname, cargs, None, m.group(0)))
        except (ValueError, TypeError):
            # Prose like "I will click (the blue one)" also matches; only keep
            # function-style calls that form a valid action.
            continue
    return actions
