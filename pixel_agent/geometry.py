"""Coordinate maths, kept in one place so there is exactly one conversion path.

Three spaces:
  * screen: physical pixels on the virtual desktop (what SetCursorPos uses)
  * image:  pixels of the screenshot the model was shown (image_w x image_h)
  * model:  whatever numbers the model writes, which depend on its convention

  model --(convention)--> image --(frame)--> screen
"""
from dataclasses import dataclass

CONVENTIONS = ("pixels", "norm1000", "norm999", "norm1")


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self):
        return self.left + self.width

    @property
    def bottom(self):
        return self.top + self.height

    def contains(self, x, y):
        return self.left <= x < self.right and self.top <= y < self.bottom

    def to_dict(self):
        return {"left": self.left, "top": self.top, "width": self.width, "height": self.height}


def fit_size(width, height, max_long_edge, allow_upscale=False):
    """Image size for a screen region: same aspect ratio, long edge capped."""
    long_edge = max(width, height)
    if long_edge <= max_long_edge and not allow_upscale:
        return width, height
    scale = max_long_edge / long_edge
    return max(1, round(width * scale)), max(1, round(height * scale))


@dataclass(frozen=True)
class Frame:
    """A screen region shown to the model as an image of image_w x image_h."""
    region: Rect
    image_w: int
    image_h: int

    @property
    def sx(self):
        return self.region.width / self.image_w

    @property
    def sy(self):
        return self.region.height / self.image_h

    def image_to_screen(self, x, y):
        """Centre of image pixel (x, y) -> nearest physical screen pixel."""
        sx = self.region.left + (x + 0.5) * self.sx - 0.5
        sy = self.region.top + (y + 0.5) * self.sy - 0.5
        return round(sx), round(sy)

    def screen_to_image(self, x, y):
        ix = (x - self.region.left + 0.5) / self.sx - 0.5
        iy = (y - self.region.top + 0.5) / self.sy - 0.5
        return ix, iy

    def clamp_image(self, x, y):
        return (min(max(x, 0), self.image_w - 1), min(max(y, 0), self.image_h - 1))

    def to_dict(self):
        return {"region": self.region.to_dict(), "image_w": self.image_w, "image_h": self.image_h}


def model_to_image(x, y, convention, image_w, image_h):
    """Convert a model's coordinate into image pixels (floats)."""
    if convention == "pixels":
        return float(x), float(y)
    if convention == "norm1000":
        return x / 1000 * image_w, y / 1000 * image_h
    if convention == "norm999":
        return x / 999 * (image_w - 1), y / 999 * (image_h - 1)
    if convention == "norm1":
        return x * image_w, y * image_h
    raise ValueError(f"unknown coordinate convention {convention!r}")


def image_to_model(x, y, convention, image_w, image_h):
    """Inverse of model_to_image; used to describe element boxes to the model."""
    if convention == "pixels":
        return round(x), round(y)
    if convention == "norm1000":
        return round(x / image_w * 1000), round(y / image_h * 1000)
    if convention == "norm999":
        return round(x / (image_w - 1) * 999), round(y / (image_h - 1) * 999)
    if convention == "norm1":
        return round(x / image_w, 4), round(y / image_h, 4)
    raise ValueError(f"unknown coordinate convention {convention!r}")


def describe_convention(convention, image_w, image_h):
    """One sentence for the system prompt."""
    if convention == "pixels":
        return (f"Coordinates are integer pixels of the latest screenshot, which is {image_w}x{image_h}; "
                f"(0,0) is the top-left corner, x grows to the right, y grows downwards.")
    if convention == "norm1000":
        return ("Coordinates are integers from 0 to 1000 relative to the screenshot "
                "(0,0 is the top-left corner, 1000,1000 the bottom-right), whatever its pixel size.")
    if convention == "norm999":
        return ("Coordinates are integers from 0 to 999 relative to the screenshot "
                "(0,0 is the top-left corner, 999,999 the bottom-right), whatever its pixel size.")
    if convention == "norm1":
        return ("Coordinates are decimals from 0 to 1 relative to the screenshot "
                "(0,0 is the top-left corner, 1,1 the bottom-right).")
    raise ValueError(convention)
