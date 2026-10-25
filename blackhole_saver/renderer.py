"""The fullscreen saver: one GTK4 window + GtkGLArea (own GL context) per monitor."""

import logging
import math
import os
import signal
import sys
import time

# GTK4 draws through EGL on both X11 and Wayland; PyOpenGL must agree or its
# function loading picks GLX and breaks under an EGL context.
os.environ.setdefault("PYOPENGL_PLATFORM", "egl")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

import OpenGL  # noqa: E402

OpenGL.ERROR_CHECKING = False  # per-call glGetError costs real time at 60 fps
from OpenGL import GL  # noqa: E402

from . import shader  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = "io.github.blackhole_saver.Saver"
MOTION_GRACE_SEC = 0.7   # ignore pointer motion this long after start (map/enter noise)
MOTION_THRESHOLD = 8.0   # px of movement from the first sample that counts as "moved"


class GLError(RuntimeError):
    pass


def _compile(kind, src):
    sh = GL.glCreateShader(kind)
    GL.glShaderSource(sh, src)
    GL.glCompileShader(sh)
    if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
        msg = GL.glGetShaderInfoLog(sh).decode(errors="replace")
        raise GLError(f"shader compile failed:\n{msg}")
    return sh


def _program(vs_src, fs_src):
    vs = _compile(GL.GL_VERTEX_SHADER, vs_src)
    fs = _compile(GL.GL_FRAGMENT_SHADER, fs_src)
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vs)
    GL.glAttachShader(prog, fs)
    GL.glLinkProgram(prog)
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        msg = GL.glGetProgramInfoLog(prog).decode(errors="replace")
        raise GLError(f"shader link failed:\n{msg}")
    return prog


def _make_texture(width, height, data, fmt=GL.GL_RGBA, internal=GL.GL_RGBA8):
    tex = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, internal, width, height, 0, fmt,
                    GL.GL_UNSIGNED_BYTE, data)
    for p in (GL.GL_TEXTURE_MIN_FILTER, GL.GL_TEXTURE_MAG_FILTER):
        GL.glTexParameteri(GL.GL_TEXTURE_2D, p, GL.GL_LINEAR)
    for p in (GL.GL_TEXTURE_WRAP_S, GL.GL_TEXTURE_WRAP_T):
        GL.glTexParameteri(GL.GL_TEXTURE_2D, p, GL.GL_CLAMP_TO_EDGE)
    return tex


class MonitorView:
    """One monitor: its window, GL area, GL objects and background slice."""

    def __init__(self, saver, monitor, bg_slice, seed):
        self.saver = saver
        self.bg_slice = bg_slice   # GdkPixbuf (RGBA) or None
        self.seed = seed
        self.ready = False
        self.prog = self.blit = self.vao = None
        self.bg_tex = self.fbo = self.fbo_tex = None
        self.fbo_size = (0, 0)

        win = Gtk.Window(application=saver.app, title="Black hole screensaver",
                         decorated=False)
        win.add_css_class("blackhole-saver")
        win.set_cursor(Gdk.Cursor.new_from_name("none", None))

        area = Gtk.GLArea()
        area.set_allowed_apis(Gdk.GLAPI.GL)
        area.set_required_version(3, 3)
        area.set_auto_render(False)
        area.set_has_depth_buffer(False)
        area.set_has_stencil_buffer(False)
        area.set_cursor(Gdk.Cursor.new_from_name("none", None))
        area.connect("realize", self._on_realize)
        area.connect("unrealize", self._on_unrealize)
        area.connect("render", self._on_render)
        win.set_child(area)

        saver.attach_input(win)
        self.window, self.area = win, area
        win.fullscreen_on_monitor(monitor)
        win.present()

    # -- GL lifecycle --------------------------------------------------------
    def _on_realize(self, area):
        area.make_current()
        if area.get_error() is not None:
            self.saver.fail(f"GL context error: {area.get_error().message}")
            return
        try:
            cfg = self.saver.cfg
            self.prog = _program(shader.source("main.vert"),
                                 shader.source("blackhole.frag", cfg))
            self.blit = _program(shader.source("blit.vert"), shader.source("blit.frag"))
        except GLError as e:
            self.saver.fail(str(e))
            return
        self.vao = GL.glGenVertexArrays(1)
        if self.bg_slice is not None:
            pb = self.bg_slice
            self.bg_tex = _make_texture(pb.get_width(), pb.get_height(),
                                        pb.read_pixel_bytes().get_data())
        self.u = {n: GL.glGetUniformLocation(self.prog, n) for n in
                  ("iResolution", "iTime", "iChannel0", "uHasBg", "uSeed", "uFade")}
        self.u_blit = GL.glGetUniformLocation(self.blit, "uTex")
        if not self.saver.gl_logged:
            self.saver.gl_logged = True
            log.info("GL: %s / %s", GL.glGetString(GL.GL_RENDERER).decode(),
                     GL.glGetString(GL.GL_VERSION).decode())
        self.ready = True

    def _on_unrealize(self, area):
        area.make_current()
        if area.get_error() is not None:
            return
        for tex in (self.bg_tex, self.fbo_tex):
            if tex:
                GL.glDeleteTextures([tex])
        if self.fbo:
            GL.glDeleteFramebuffers(1, [self.fbo])
        if self.vao:
            GL.glDeleteVertexArrays(1, [self.vao])
        for p in (self.prog, self.blit):
            if p:
                GL.glDeleteProgram(p)
        self.ready = False

    def _ensure_fbo(self, w, h):
        if self.fbo_size == (w, h):
            return
        if self.fbo_tex:
            GL.glDeleteTextures([self.fbo_tex])
        if not self.fbo:
            self.fbo = GL.glGenFramebuffers(1)
        self.fbo_tex = _make_texture(w, h, None)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0,
                                  GL.GL_TEXTURE_2D, self.fbo_tex, 0)
        self.fbo_size = (w, h)

    def _on_render(self, area, _ctx):
        if not self.ready:
            return True
        cfg = self.saver.cfg
        sf = area.get_scale_factor()
        w, h = max(1, area.get_width() * sf), max(1, area.get_height() * sf)
        scale = cfg["render"]["render_scale"]
        sw, sh = max(1, math.ceil(w * scale)), max(1, math.ceil(h * scale))
        target = GL.glGetIntegerv(GL.GL_FRAMEBUFFER_BINDING)  # GtkGLArea's own FBO
        offscreen = (sw, sh) != (w, h)

        GL.glBindVertexArray(self.vao)
        if offscreen:
            self._ensure_fbo(sw, sh)
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self.fbo)
        GL.glViewport(0, 0, sw, sh)

        t = time.monotonic() - self.saver.t0
        fade_in = cfg["render"]["fade_in_sec"]
        GL.glUseProgram(self.prog)
        GL.glUniform3f(self.u["iResolution"], sw, sh, 1.0)
        GL.glUniform1f(self.u["iTime"], t)
        GL.glUniform1f(self.u["uSeed"], self.seed)
        GL.glUniform1f(self.u["uFade"], min(1.0, t / fade_in) if fade_in > 0 else 1.0)
        GL.glUniform1i(self.u["uHasBg"], 1 if self.bg_tex else 0)
        GL.glUniform1i(self.u["iChannel0"], 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self.bg_tex or 0)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)

        if offscreen:
            GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, target)
            GL.glViewport(0, 0, w, h)
            GL.glUseProgram(self.blit)
            GL.glUniform1i(self.u_blit, 0)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.fbo_tex)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        self.saver.frames += 1
        return True


def _monitor_slices(pixbuf, monitors):
    """Crop the desktop screenshot into one RGBA pixbuf per monitor.

    Monitor geometry is in logical pixels, the screenshot in physical ones;
    the ratio of screenshot width to the monitors' logical bounding box gives
    the scale, which is right whenever all monitors share one scale factor.
    """
    if pixbuf is None:
        return [None] * len(monitors)
    rects = [m.get_geometry() for m in monitors]
    x0 = min(r.x for r in rects)
    y0 = min(r.y for r in rects)
    x1 = max(r.x + r.width for r in rects)
    y1 = max(r.y + r.height for r in rects)
    sx = pixbuf.get_width() / max(1, x1 - x0)
    sy = pixbuf.get_height() / max(1, y1 - y0)
    out = []
    for r in rects:
        x = max(0, round((r.x - x0) * sx))
        y = max(0, round((r.y - y0) * sy))
        w = min(pixbuf.get_width() - x, round(r.width * sx))
        h = min(pixbuf.get_height() - y, round(r.height * sy))
        if w <= 0 or h <= 0:
            out.append(None)
            continue
        # add_alpha copies into a tightly packed RGBA buffer (no row padding)
        out.append(pixbuf.new_subpixbuf(x, y, w, h).add_alpha(False, 0, 0, 0))
    return out


class Saver:
    def __init__(self, cfg, bg_path=None, duration=None):
        self.cfg = cfg
        self.bg_path = bg_path
        self.duration = duration
        self.t0 = time.monotonic()
        self.frames = 0
        self.exit_code = 0
        self.gl_logged = False
        self.views = []
        self.motion_origin = None
        self.quitting = False
        self.app = Gtk.Application(application_id=APP_ID,
                                   flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.app.connect("activate", self._on_activate)

    def _on_activate(self, app):
        css = Gtk.CssProvider()
        css.load_from_data(b"window.blackhole-saver { background: black; }", -1)
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        monitors = [display.get_monitors().get_item(i)
                    for i in range(display.get_monitors().get_n_items())]
        pixbuf = None
        if self.bg_path:
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file(self.bg_path)
            except GLib.Error as e:
                log.warning("background %s unusable (%s); using starfield", self.bg_path, e.message)
        slices = _monitor_slices(pixbuf, monitors)
        log.info("monitors: %d, background: %s", len(monitors),
                 "screenshot" if pixbuf else "starfield")

        self.t0 = time.monotonic()
        for i, (mon, sl) in enumerate(zip(monitors, slices)):
            self.views.append(MonitorView(self, mon, sl, seed=37.0 * i))

        fps = self.cfg["render"]["fps"]
        GLib.timeout_add(max(1, round(1000 / fps)), self._tick)
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
        if self.duration:
            GLib.timeout_add(int(self.duration * 1000), self._on_timeout)

    def _tick(self):
        for v in self.views:
            v.area.queue_render()
        return GLib.SOURCE_CONTINUE

    def _on_signal(self):
        self.quit("signal")
        return GLib.SOURCE_REMOVE

    def _on_timeout(self):
        self.quit("test duration elapsed")
        return GLib.SOURCE_REMOVE

    # -- input -> exit ---------------------------------------------------------
    def attach_input(self, win):
        key = Gtk.EventControllerKey()
        key.connect("key-pressed", lambda *a: self.quit("key press") or True)
        win.add_controller(key)

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", lambda *a: self.quit("mouse click"))
        win.add_controller(click)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", lambda *a: self.quit("scroll") or True)
        win.add_controller(scroll)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        win.add_controller(motion)

    def _on_motion(self, ctrl, x, y):
        if time.monotonic() - self.t0 < MOTION_GRACE_SEC:
            return
        # compare in surface-independent terms: each window has its own
        # coordinates, so the origin is per widget
        widget = ctrl.get_widget()
        if self.motion_origin is None or self.motion_origin[0] is not widget:
            self.motion_origin = (widget, x, y)
            return
        _, ox, oy = self.motion_origin
        if math.hypot(x - ox, y - oy) >= MOTION_THRESHOLD:
            self.quit("mouse movement")

    # -- lifecycle -----------------------------------------------------------
    def fail(self, msg):
        log.error("%s", msg)
        self.exit_code = 2
        GLib.idle_add(self.app.quit)

    def quit(self, reason):
        if self.quitting:
            return
        self.quitting = True
        elapsed = time.monotonic() - self.t0
        if elapsed > 0 and self.frames:
            per_mon = self.frames / max(1, len(self.views)) / elapsed
            log.info("exit (%s) after %.1f s, %.1f fps per monitor", reason, elapsed, per_mon)
        else:
            log.info("exit (%s)", reason)
        for v in self.views:
            v.window.destroy()
        self.app.quit()

    def run(self):
        self.app.run([sys.argv[0]])
        return self.exit_code


def run_saver(cfg, bg_path=None, duration=None):
    return Saver(cfg, bg_path, duration).run()
