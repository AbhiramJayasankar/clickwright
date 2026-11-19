"""Driver self-test on the real screen (no model involved). Moves the mouse!

Opens a full-screen window on one monitor with coloured targets at known
physical-pixel positions, then for each target:
  1. captures a screenshot the way the agent does (scaled to max_long_edge)
  2. finds the target's colour in the screenshot and converts its centre
     back to screen pixels through the Frame (tests capture + mapping)
  3. clicks there with SendInput and records where the click landed
     (tests input)
Reports per-target errors for both halves at the monitor's current scaling.
Run it once at 100%, 125% and 150% display scaling to cover DPI handling.
"""
import json
import threading
import time

from .win import DPI_MODE
from .win import screen, input as winput

COLORS = [(230, 30, 30), (30, 160, 60), (30, 80, 230), (230, 150, 20), (160, 40, 200), (20, 180, 190),
          (120, 120, 0), (200, 60, 140), (60, 60, 60)]


def _hex(c):
    return "#%02x%02x%02x" % c


def run(monitor_index=0, max_long_edge=1280, size=24):
    import tkinter as tk
    mons = screen.monitors()
    mon = mons[monitor_index]
    r = mon.rect
    fx = [0.03, 0.5, 0.97]
    fy = [0.05, 0.5, 0.95]
    targets = []
    for j, yy in enumerate(fy):
        for i, xx in enumerate(fx):
            cx = r.left + round((r.width - size) * xx) + size // 2
            cy = r.top + round((r.height - size) * yy) + size // 2
            targets.append({"screen": (cx, cy), "color": COLORS[len(targets)]})
    clicks = []
    root = tk.Tk()
    root.overrideredirect(True)
    root.geometry(f"{r.width}x{r.height}+{r.left}+{r.top}")
    root.attributes("-topmost", True)
    cv = tk.Canvas(root, width=r.width, height=r.height, bg="white", highlightthickness=0)
    cv.pack()
    for t in targets:
        x, y = t["screen"][0] - r.left, t["screen"][1] - r.top
        cv.create_rectangle(x - size // 2, y - size // 2, x + size // 2 - 1, y + size // 2 - 1,
                            fill=_hex(t["color"]), outline="")
    cv.create_text(r.width // 2, r.height // 2 + 60, text="clickwright driver test — do not touch the mouse",
                   font=("Segoe UI", 16))
    cv.bind("<Button-1>", lambda e: clicks.append((e.x_root, e.y_root)))
    results = {}

    def work():
        time.sleep(1.2)
        rows = []
        for t in targets:
            cap = screen.capture(r, max_long_edge=max_long_edge, draw_cursor=False)
            f = cap.frame
            # centroid of pixels close to the target colour
            sx = sy = n = 0
            want = t["color"]
            ex, ey = f.screen_to_image(*t["screen"])  # search near where it should be (±100 px)
            for y in range(max(0, int(ey) - 100), min(f.image_h, int(ey) + 100)):
                for x in range(max(0, int(ex) - 100), min(f.image_w, int(ex) + 100)):
                    pr, pg, pb = cap.pixel(x, y)
                    if abs(pr - want[0]) < 40 and abs(pg - want[1]) < 40 and abs(pb - want[2]) < 40:
                        sx += x
                        sy += y
                        n += 1
            if not n:
                rows.append({"target": t["screen"], "error": "target colour not found in screenshot"})
                continue
            ix, iy = sx / n, sy / n
            mapped = f.image_to_screen(ix, iy)
            before = len(clicks)
            winput.click(*mapped)
            for _ in range(50):
                if len(clicks) > before:
                    break
                time.sleep(0.02)
            landed = clicks[-1] if len(clicks) > before else None
            rows.append({"target": t["screen"], "image_centroid": (round(ix, 2), round(iy, 2)), "mapped": mapped,
                         "map_error_px": round(((mapped[0] - t["screen"][0]) ** 2 + (mapped[1] - t["screen"][1]) ** 2) ** 0.5, 2),
                         "landed": landed,
                         "input_error_px": None if not landed else
                         round(((landed[0] - mapped[0]) ** 2 + (landed[1] - mapped[1]) ** 2) ** 0.5, 2)})
        results["rows"] = rows
        results["frame"] = f.to_dict()
        root.after(0, root.destroy)

    threading.Thread(target=work, daemon=True).start()
    root.mainloop()
    rows = results.get("rows", [])
    map_errs = [x["map_error_px"] for x in rows if "map_error_px" in x]
    in_errs = [x["input_error_px"] for x in rows if x.get("input_error_px") is not None]
    scale = results.get("frame", {})
    # Finding a target in a downscaled screenshot is only exact to about one screenshot
    # pixel (sx screen px), plus ~1 px because Tk's rectangle edges make the true centre
    # ambiguous by half a pixel on each axis.
    allowed_map = (r.width / max(1, scale.get("image_w", r.width))) + 1
    report = {"dpi_mode": DPI_MODE, "monitor": mon.to_dict(), "frame": scale, "rows": rows,
              "max_map_error_px": max(map_errs) if map_errs else None, "allowed_map_error_px": round(allowed_map, 2),
              "max_input_error_px": max(in_errs) if in_errs else None,
              "clicks_registered": f"{len(in_errs)}/{len(rows)}",
              "passed": bool(map_errs) and max(map_errs) <= allowed_map and len(in_errs) == len(rows)
              and max(in_errs) <= 1}
    return report


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
