"""A minimal fake ``pygame`` module used ONLY for headless testing in an
environment where the real pygame/SDL cannot be installed (no network
access to PyPI/apt in this sandbox).

This is a TEST HELPER, not part of the shipped game. On the user's Windows
machine, the real pygame package (already installed in their venv) is what
actually gets imported and used — this stub is never touched there.

test_math_meme_spire.py imports this and installs it into sys.modules
*only* when `import pygame` fails for real, so the exact same test file
also works unmodified on a machine that has real pygame.

It mimics just enough of the pygame surface that polyhedral_spire.py can be
imported and its non-rendering logic (and even its drawing calls, as
no-ops) can be exercised without a real display or SDL library.
"""

import sys
import types


class Rect:
    def __init__(self, *args):
        if len(args) == 1:
            x, y, w, h = args[0]
        elif len(args) == 4:
            x, y, w, h = args
        else:
            raise TypeError("Rect() takes 1 or 4 positional args")
        self.x = int(x)
        self.y = int(y)
        self.width = int(w)
        self.height = int(h)

    # -- geometry helpers -------------------------------------------------
    @property
    def left(self):
        return self.x

    @left.setter
    def left(self, v):
        self.x = v

    @property
    def right(self):
        return self.x + self.width

    @right.setter
    def right(self, v):
        self.x = v - self.width

    @property
    def top(self):
        return self.y

    @top.setter
    def top(self, v):
        self.y = v

    @property
    def bottom(self):
        return self.y + self.height

    @bottom.setter
    def bottom(self, v):
        self.y = v - self.height

    @property
    def centerx(self):
        return self.x + self.width // 2

    @centerx.setter
    def centerx(self, v):
        self.x = v - self.width // 2

    @property
    def centery(self):
        return self.y + self.height // 2

    @centery.setter
    def centery(self, v):
        self.y = v - self.height // 2

    @property
    def center(self):
        return (self.centerx, self.centery)

    @center.setter
    def center(self, value):
        self.centerx, self.centery = value

    @property
    def topleft(self):
        return (self.x, self.y)

    @property
    def size(self):
        return (self.width, self.height)

    def collidepoint(self, pos):
        px, py = pos
        return self.x <= px <= self.x + self.width and self.y <= py <= self.y + self.height

    def inflate(self, dx, dy):
        return Rect(self.x - dx // 2, self.y - dy // 2, self.width + dx, self.height + dy)

    def move(self, dx, dy):
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def copy(self):
        return Rect(self.x, self.y, self.width, self.height)

    def __repr__(self):
        return f"Rect({self.x}, {self.y}, {self.width}, {self.height})"


class Vector2:
    """Minimal stand-in for pygame.Vector2 — just enough vector arithmetic
    for map-route drawing code (subtraction, scaling, length, in-place
    normalize) to run headlessly. Real rendering is a no-op in this stub
    either way, so exact float behavior isn't load-bearing here."""

    def __init__(self, x=0.0, y=0.0):
        if hasattr(x, "__len__"):
            self.x, self.y = float(x[0]), float(x[1])
        else:
            self.x, self.y = float(x), float(y)

    def __sub__(self, other):
        return Vector2(self.x - other.x, self.y - other.y)

    def __add__(self, other):
        return Vector2(self.x + other.x, self.y + other.y)

    def __iadd__(self, other):
        self.x += other.x
        self.y += other.y
        return self

    def __isub__(self, other):
        self.x -= other.x
        self.y -= other.y
        return self

    def __mul__(self, scalar):
        return Vector2(self.x * scalar, self.y * scalar)

    __rmul__ = __mul__

    def length(self):
        return (self.x ** 2 + self.y ** 2) ** 0.5

    def normalize_ip(self):
        length = self.length()
        if length > 0:
            self.x /= length
            self.y /= length

    def __iter__(self):
        yield self.x
        yield self.y

    def __repr__(self):
        return f"Vector2({self.x}, {self.y})"


class Surface:
    def __init__(self, size, flags=0):
        self.width, self.height = size
        self.flags = flags

    def fill(self, color, rect=None):
        pass

    def blit(self, source, dest, area=None, special_flags=0):
        pass

    def get_rect(self, **kwargs):
        r = Rect(0, 0, self.width, self.height)
        for k, v in kwargs.items():
            setattr(r, k, v)
        return r

    def convert_alpha(self):
        return self

    def set_alpha(self, value):
        pass

    def copy(self):
        return Surface((self.width, self.height), self.flags)

    def get_size(self):
        return (self.width, self.height)

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def subsurface(self, rect):
        # Real pygame raises if the rect isn't fully inside the surface;
        # mirror that so headless tests exercise the same guard paths as
        # real SDL would (_slice_hero_sheet() relies on a clean slice per
        # frame column of a hero sheet).
        if rect.x < 0 or rect.y < 0 or rect.x + rect.width > self.width or rect.y + rect.height > self.height:
            raise ValueError("subsurface rectangle outside surface area")
        return Surface((rect.width, rect.height), self.flags)


class _FakeFont:
    def __init__(self, name=None, size=16, bold=False):
        self.size_px = size

    def render(self, text, antialias, color, *_):
        w = max(1, int(len(str(text)) * self.size_px * 0.55))
        return Surface((w, self.size_px))

    def size(self, text):
        return (max(1, int(len(str(text)) * self.size_px * 0.55)), self.size_px)


class _FontModule:
    def SysFont(self, name, size, bold=False, italic=False):
        return _FakeFont(name, size, bold)

    def Font(self, path, size):
        return _FakeFont(None, size)

    def init(self):
        pass


class _DisplayModule:
    def __init__(self):
        self._surface = None

    def set_mode(self, size, flags=0):
        self._surface = Surface(size)
        return self._surface

    def set_caption(self, title):
        pass

    def flip(self):
        pass

    def get_surface(self):
        return self._surface


class _DrawModule:
    def rect(self, surface, color, rect, width=0, border_radius=0):
        pass

    def circle(self, surface, color, center, radius, width=0):
        pass

    def ellipse(self, surface, color, rect, width=0):
        pass

    def line(self, surface, color, start, end, width=1):
        pass

    def lines(self, surface, color, closed, points, width=1):
        pass

    def polygon(self, surface, color, points, width=0):
        pass

    def arc(self, surface, color, rect, start_angle, stop_angle, width=1):
        pass


class _TimeClock:
    def tick(self, fps=0):
        return 16


class _TimeModule:
    def Clock(self):
        return _TimeClock()

    def get_ticks(self):
        return 0


class _EventModule:
    def get(self):
        return []

    def clear(self):
        pass


class _MouseModule:
    def get_pos(self):
        return (0, 0)


def init():
    pass


def quit():
    pass


QUIT = 256
MOUSEBUTTONDOWN = 1025
MOUSEMOTION = 1024
KEYDOWN = 768
VIDEORESIZE = 32770
SRCALPHA = 65536
BLEND_RGBA_MULT = 4
BLEND_RGBA_ADD = 1
BLEND_RGBA_SUB = 2
BLEND_RGBA_MAX = 5
BLEND_RGBA_MIN = 6

# Display-mode flags (real values from pygame's SDL2 backend; only need to
# be distinct/truthy here since the stub's set_mode() ignores them).
RESIZABLE = 16
FULLSCREEN = 1

# Keycodes/modifiers touched by main()'s fullscreen shortcuts and
# handle_keydown(). Not exercised by the test suite directly (main()'s
# event loop never runs headlessly) but kept real-shaped so any future
# test that does exercise them won't hit an AttributeError.
K_F11 = 292
K_RETURN = 13
K_KP_ENTER = 271
K_ESCAPE = 27
K_BACKSPACE = 8
K_l = 108
K_d = 100
K_m = 109
K_SPACE = 32
KMOD_ALT = 768


def build_stub_module():
    mod = types.ModuleType("pygame")
    mod.Rect = Rect
    mod.Vector2 = Vector2
    mod.Surface = Surface
    mod.font = _FontModule()
    mod.display = _DisplayModule()
    mod.draw = _DrawModule()
    mod.time = _TimeModule()
    mod.event = _EventModule()
    mod.mouse = _MouseModule()
    mod.init = init
    mod.quit = quit
    mod.QUIT = QUIT
    mod.MOUSEBUTTONDOWN = MOUSEBUTTONDOWN
    mod.MOUSEMOTION = MOUSEMOTION
    mod.KEYDOWN = KEYDOWN
    mod.SRCALPHA = SRCALPHA
    mod.BLEND_RGBA_MULT = BLEND_RGBA_MULT
    mod.BLEND_RGBA_ADD = BLEND_RGBA_ADD
    mod.BLEND_RGBA_SUB = BLEND_RGBA_SUB
    mod.BLEND_RGBA_MAX = BLEND_RGBA_MAX
    mod.BLEND_RGBA_MIN = BLEND_RGBA_MIN
    mod.VIDEORESIZE = VIDEORESIZE
    mod.RESIZABLE = RESIZABLE
    mod.FULLSCREEN = FULLSCREEN
    mod.K_F11 = K_F11
    mod.K_RETURN = K_RETURN
    mod.K_KP_ENTER = K_KP_ENTER
    mod.K_ESCAPE = K_ESCAPE
    mod.K_BACKSPACE = K_BACKSPACE
    mod.K_l = K_l
    mod.K_d = K_d
    mod.K_m = K_m
    mod.K_SPACE = K_SPACE
    mod.KMOD_ALT = KMOD_ALT

    transform = types.ModuleType("pygame.transform")
    transform.smoothscale = lambda surf, size: Surface(size, getattr(surf, "flags", 0))
    transform.scale = transform.smoothscale
    mod.transform = transform

    def _fake_image_load(path):
        # Real pygame raises when the file doesn't exist; get_art()/get_icon()
        # rely on that (catch -> None -> procedural fallback). Mirror it here
        # so headless tests correctly exercise the "asset not present yet"
        # path instead of always pretending art exists.
        import os as _os
        if not _os.path.exists(path):
            raise FileNotFoundError(path)
        return Surface((1, 1), SRCALPHA)

    image = types.ModuleType("pygame.image")
    image.load = _fake_image_load
    mod.image = image
    return mod


def install():
    """Install the stub into sys.modules as 'pygame' if real pygame is missing."""
    try:
        import pygame  # noqa: F401
        return False
    except ImportError:
        sys.modules["pygame"] = build_stub_module()
        return True
