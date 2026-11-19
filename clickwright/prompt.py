"""System prompt and the per-step observation text."""
import platform

from .actions import JSON_FORMAT_HELP
from .geometry import describe_convention, image_to_model


def system_prompt(convention, image_w, image_h, output_mode, platform_name=None, extra=""):
    plat = platform_name or f"Windows {platform.release()}"
    lines = [
        f"You control a computer running {plat} by looking at screenshots and sending mouse and keyboard actions.",
        "",
        "How it works:",
        "- After your actions run, you get a new screenshot. Never assume an action worked: check the next screenshot.",
        f"- {describe_convention(convention, image_w, image_h)}",
        "- Aim at the centre of the thing you want to click. If a target is small or text is hard to read, use zoom first.",
        "- The mouse pointer is drawn in the screenshot. Solid black areas hide private windows: never interact with them.",
        "- Prefer keyboard shortcuts when they are reliable (ctrl+s, ctrl+l, alt+tab, win to open Start, enter, esc).",
        "- To type into a field, click it first, then use type. Use key for shortcuts and special keys.",
        "- Several actions in one reply are fine when you are sure of the outcome (e.g. click a field, then type); "
        "otherwise do one action and look again.",
        "- If something unexpected appears (dialog, error, login prompt), deal with it or report it; do not guess.",
        "- Do not enter passwords, payment details or other secrets, and do not buy, send or delete anything "
        "unless the task explicitly asks for it.",
        "- When the task is done, call answer with the result (or what you found). If it cannot be done, call "
        "answer with success=false and say why.",
    ]
    if output_mode == "json_text":
        lines += ["", JSON_FORMAT_HELP]
    if extra:
        lines += ["", extra]
    return "\n".join(lines)


def observation_text(obs, convention, step, max_steps, note=""):
    f = obs.frame
    parts = [f"Step {step} of at most {max_steps}. Screenshot: {f.image_w}x{f.image_h}."]
    if obs.cursor:
        ix, iy = f.screen_to_image(*obs.cursor)
        mx, my = image_to_model(ix, iy, convention, f.image_w, f.image_h)
        parts.append(f"Mouse pointer at ({mx}, {my}).")
    if obs.foreground:
        title = (obs.foreground.get("title") or "")[:100]
        parts.append(f"Focused window: \"{title}\" ({obs.foreground.get('process') or '?'}).")
    if note:
        parts.append(note)
    text = " ".join(parts)
    if obs.elements:
        text += "\n" + elements_text(obs.elements, obs.frame, convention)
    return text


def elements_text(elements, frame, convention):
    rows = ["Accessible elements in view (role \"name\" [x1,y1,x2,y2] in screenshot coordinates; "
            "a hint only, the screenshot is the truth):"]
    for e in elements:
        l, t, r, b = e["rect"]
        x1, y1 = frame.screen_to_image(l, t)
        x2, y2 = frame.screen_to_image(r, b)
        x1, y1 = frame.clamp_image(x1, y1)
        x2, y2 = frame.clamp_image(x2, y2)
        a = image_to_model(x1, y1, convention, frame.image_w, frame.image_h)
        z = image_to_model(x2, y2, convention, frame.image_w, frame.image_h)
        flags = "" if e.get("enabled", True) else " (disabled)"
        name = (e.get("name") or "").replace('"', "'")
        rows.append(f"- {e['role']} \"{name}\" [{a[0]},{a[1]},{z[0]},{z[1]}]{flags}")
    return "\n".join(rows)
