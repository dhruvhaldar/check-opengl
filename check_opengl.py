#!/usr/bin/env python3
"""
check_opengl.py -- cross-platform OpenGL detector.

Determines:
  1. Whether a native OpenGL runtime library is installed.
  2. Whether Python OpenGL bindings (PyOpenGL) are available.
  3. Whether OpenGL is *active*: whether a real GL context can be created
     and queried for vendor / renderer / version strings.

Works on Windows, macOS and Linux (X11, Wayland, and headless via EGL).

Usage:
    python check_opengl.py            # human-readable report
    python check_opengl.py --json     # machine-readable JSON

Exit codes:
    0   OpenGL installed AND active (context created, version reported)
    1   OpenGL runtime present, but no context could be created / queried
    2   No OpenGL runtime found at all

Optional (pure-Python) backends used to prove OpenGL is active -- the script
uses whichever it finds first:
    pip install glfw      (GLFW bindings)
    pip install pygame    (SDL2-based)
    pip install PyOpenGL   (GLUT-based; needs a display on Linux)
    (EGL is tried last, no extra packages needed)
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import os
import platform
import shutil
import subprocess
import sys

IS_WINDOWS = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC

# --- GL enums -------------------------------------------------------------
GL_VENDOR = 0x1F00
GL_RENDERER = 0x1F01
GL_VERSION = 0x1F02
GL_SHADING_LANGUAGE_VERSION = 0x8B8C

GL_STRING_NAMES = (
    ("vendor", GL_VENDOR),
    ("renderer", GL_RENDERER),
    ("version", GL_VERSION),
    ("glsl", GL_SHADING_LANGUAGE_VERSION),
)

# --- EGL enums ------------------------------------------------------------
EGL_NONE = 0x3038
EGL_PBUFFER_BIT = 0x0001
EGL_OPENGL_BIT = 0x0008
EGL_ALPHA_SIZE, EGL_BLUE_SIZE, EGL_GREEN_SIZE, EGL_RED_SIZE = 0x3021, 0x3022, 0x3023, 0x3024
EGL_DEPTH_SIZE = 0x3025
EGL_SURFACE_TYPE = 0x0305
EGL_RENDERABLE_TYPE = 0x3040
EGL_OPENGL_API = 0x30A2
EGL_WIDTH, EGL_HEIGHT = 0x3057, 0x3056


def _os_key() -> str:
    return "win" if IS_WINDOWS else "darwin" if IS_MAC else "linux"


# --- 1. Native library ----------------------------------------------------
def load_gl_library():
    """Try to dlopen the platform's native OpenGL runtime. Returns (CDLL|None, name|None)."""
    candidates = {
        "win": ["opengl32.dll"],
        "darwin": ["/System/Library/Frameworks/OpenGL.framework/OpenGL"],
        "linux": ["libGL.so.1", "libGL.so", "libOpenGL.so.0", "libOpenGL.so"],
    }[_os_key()]

    for name in candidates:
        try:
            return ctypes.CDLL(name), name
        except OSError:
            continue

    # Fallback: ask the system where the library lives.
    for hint in {
        "win": ("opengl32",),
        "darwin": ("OpenGL",),
        "linux": ("GL", "OpenGL"),
    }[_os_key()]:
        found = ctypes.util.find_library(hint)
        if found:
            try:
                return ctypes.CDLL(found), found
            except OSError:
                continue
    return None, None


def query_gl(lib) -> dict:
    """Call glGetString on whatever context is current. Requires a current context."""
    info = {}
    try:
        lib.glGetString.restype = ctypes.c_char_p
        lib.glGetString.argtypes = [ctypes.c_uint]
    except AttributeError:
        return info
    for key, const in GL_STRING_NAMES:
        try:
            val = lib.glGetString(const)
            if val:
                info[key] = val.decode("utf-8", "replace")
        except Exception:
            pass
    return info


# --- 2. Context-creation backends -----------------------------------------
def _has_display() -> bool:
    if IS_WINDOWS or IS_MAC:
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def try_glfw(gl_lib):
    try:
        import glfw  # type: ignore
    except ImportError:
        return None, None  # backend not installed
    try:
        if not glfw.init():
            return None, "glfw.init() failed (no display server?)"
        glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
        window = glfw.create_window(64, 64, "opengl-probe", None, None)
        if not window:
            return None, "glfw.create_window() failed (driver/GPU issue?)"
        glfw.make_context_current(window)
        info = query_gl(gl_lib)
        glfw.destroy_window(window)
        glfw.terminate()
        return info or None, "context created but glGetString returned nothing"
    except Exception as exc:  # noqa: BLE001
        return None, f"glfw error: {exc}"


def try_pygame(gl_lib):
    try:
        import pygame  # type: ignore
    except ImportError:
        return None, None
    try:
        pygame.init()
        pygame.display.set_mode((64, 64), pygame.DOUBLEBUF | pygame.OPENGL)
        info = query_gl(gl_lib)
        pygame.display.quit()
        pygame.quit()
        return info or None, "context created but glGetString returned nothing"
    except Exception as exc:  # noqa: BLE001
        try:
            pygame.quit()
        except Exception:
            pass
        return None, f"pygame error: {exc}"


def try_glut(gl_lib):
    try:
        from OpenGL import GL as pgl  # type: ignore
        from OpenGL.GLUT import glutCreateWindow, glutDestroyWindow, glutInit  # type: ignore
    except Exception:
        return None, None  # PyOpenGL not installed
    if not _has_display():
        # freeglut hard-aborts the whole process on headless boxes; skip it.
        return None, "skipped (no display; freeglut would abort)"
    try:
        glutInit(["check_opengl"])
        win = glutCreateWindow("opengl-probe")
        info = {}
        for key, const in GL_STRING_NAMES:
            try:
                val = pgl.glGetString(const)
                if val:
                    info[key] = val.decode("utf-8", "replace") if isinstance(val, bytes) else str(val)
            except Exception:
                pass
        glutDestroyWindow(win)
        return info or None, "context created but glGetString returned nothing"
    except Exception as exc:  # noqa: BLE001
        return None, f"glut error: {exc}"


def try_egl(gl_lib):
    names = {
        "win": ["libEGL.dll"],
        "darwin": [],  # macOS has no EGL
        "linux": ["libEGL.so.1", "libEGL.so"],
    }[_os_key()]
    egl = None
    for name in names:
        try:
            egl = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if egl is None:
        return None, None  # EGL not available

    VP, I32 = ctypes.c_void_p, ctypes.c_int32
    dpy = surf = ctx = None
    try:
        egl.eglGetDisplay.restype, egl.eglGetDisplay.argtypes = VP, [VP]
        egl.eglInitialize.argtypes = [VP, ctypes.POINTER(I32), ctypes.POINTER(I32)]
        egl.eglChooseConfig.argtypes = [VP, ctypes.POINTER(I32), ctypes.POINTER(VP), I32, ctypes.POINTER(I32)]
        egl.eglBindAPI.argtypes = [ctypes.c_uint32]
        egl.eglCreateContext.restype, egl.eglCreateContext.argtypes = VP, [VP, VP, VP, ctypes.POINTER(I32)]
        egl.eglCreatePbufferSurface.restype, egl.eglCreatePbufferSurface.argtypes = VP, [VP, VP, ctypes.POINTER(I32)]
        egl.eglMakeCurrent.argtypes = [VP, VP, VP, VP]
        egl.eglDestroySurface.argtypes = [VP]
        egl.eglDestroyContext.argtypes = [VP]
        egl.eglTerminate.argtypes = [VP]

        dpy = egl.eglGetDisplay(None)
        if not dpy:
            return None, "eglGetDisplay failed"
        if not egl.eglInitialize(dpy, ctypes.byref(I32(0)), ctypes.byref(I32(0))):
            return None, "eglInitialize failed"
        egl.eglBindAPI(EGL_OPENGL_API)

        cfg_attribs = (I32 * 15)(
            EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
            EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
            EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8,
            EGL_ALPHA_SIZE, 8, EGL_DEPTH_SIZE, 24,
            EGL_NONE,
        )
        configs = (VP * 1)()
        if not egl.eglChooseConfig(dpy, cfg_attribs, configs, 1, ctypes.byref(I32(0))) or not configs[0]:
            return None, "no EGL config supporting desktop OpenGL"

        ctx = egl.eglCreateContext(dpy, configs[0], None, (I32 * 1)(EGL_NONE))
        if not ctx:
            return None, "eglCreateContext failed"

        surf = egl.eglCreatePbufferSurface(
            dpy, configs[0], (I32 * 5)(EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE)
        )
        if not egl.eglMakeCurrent(dpy, surf, surf, ctx):
            # Some drivers support surfaceless contexts instead.
            if not egl.eglMakeCurrent(dpy, None, None, ctx):
                return None, "eglMakeCurrent failed"

        info = query_gl(gl_lib) if gl_lib else {}
        if not info:
            # Resolve glGetString straight from the EGL driver (e.g. ANGLE).
            try:
                egl.eglGetProcAddress.restype, egl.eglGetProcAddress.argtypes = VP, [ctypes.c_char_p]
                ptr = egl.eglGetProcAddress(b"glGetString")
                if ptr:
                    fn = ctypes.cast(ptr, ctypes.CFUNCTYPE(ctypes.c_char_p, ctypes.c_uint))
                    for key, const in GL_STRING_NAMES:
                        val = fn(const)
                        if val:
                            info[key] = val.decode("utf-8", "replace")
            except Exception:
                pass
        return info or None, "context created but glGetString returned nothing"
    except Exception as exc:  # noqa: BLE001
        return None, f"egl error: {exc}"
    finally:
        try:
            if dpy:
                egl.eglMakeCurrent(dpy, None, None, None)
                if surf:
                    egl.eglDestroySurface(dpy, surf)
                if ctx:
                    egl.eglDestroyContext(dpy, ctx)
                egl.eglTerminate(dpy)
        except Exception:
            pass


# --- 3. Optional extras ----------------------------------------------------
def pyopengl_version():
    try:
        import OpenGL  # type: ignore
        return OpenGL.__version__
    except Exception:
        return None


def glxinfo_data():
    """On Linux, fall back to parsing glxinfo if it is installed."""
    if not IS_LINUX:
        return None
    path = shutil.which("glxinfo")
    if not path:
        return None
    try:
        out = subprocess.run([path], capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return None
    prefixes = {
        "OpenGL vendor string": "vendor",
        "OpenGL renderer string": "renderer",
        "OpenGL core profile version string": "core_version",
        "OpenGL version string": "version",
    }
    info = {}
    for line in out.splitlines():
        for prefix, key in prefixes.items():
            if line.startswith(prefix):
                info.setdefault(key, line.split(":", 1)[1].strip())
    return info or None


def system_extras(gl_lib_name):
    notes = []
    if IS_WINDOWS:
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        if os.path.exists(os.path.join(sysroot, "System32", "opengl32.dll")):
            notes.append("opengl32.dll present in System32")
    if IS_MAC:
        notes.append("macOS ships OpenGL in the OS (max version 4.1; "
                     "deprecated since macOS 10.14 but still functional)")
    return notes


# --- Main -------------------------------------------------------------------
def run(as_json: bool) -> int:
    gl_lib, gl_lib_name = load_gl_library()
    pyopengl = pyopengl_version()

    backend_statuses = {}
    active_backend = None
    gl_info = None

    if gl_lib:
        for backend, fn in (("glfw", try_glfw), ("pygame", try_pygame),
                            ("glut", try_glut), ("egl", try_egl)):
            info, detail = fn(gl_lib)
            if info:
                backend_statuses[backend] = "ok"
                active_backend, gl_info = backend, info
                break
            if detail is None:
                backend_statuses[backend] = "not installed"
            else:
                backend_statuses[backend] = f"failed ({detail})"

    glx = glxinfo_data()
    notes = system_extras(gl_lib_name)
    verdict, exit_code = "not-found", 2
    if gl_info:
        verdict, exit_code = "active", 0
    elif gl_lib:
        verdict, exit_code = "installed-but-not-verified-active", 1

    report = {
        "platform": platform.platform(),
        "native_library": {"found": gl_lib is not None, "name": gl_lib_name},
        "pyopengl": {"installed": pyopengl is not None, "version": pyopengl},
        "opengl_active": gl_info is not None,
        "context_backend": active_backend,
        "opengl_info": gl_info,
        "backends": backend_statuses,
        "glxinfo": glx,
        "notes": notes,
        "verdict": verdict,
        "exit_code": exit_code,
    }

    if as_json:
        print(json.dumps(report, indent=2))
        return exit_code

    def line(sym, msg):
        print(f"[{sym}] {msg}")

    print(f"OpenGL check -- {platform.platform()}")
    print("-" * 60)

    if gl_lib:
        line("+", f"Native OpenGL library found: {gl_lib_name}")
    else:
        line("-", "Native OpenGL library NOT found")
        line(" ", "-> Install GPU drivers (Linux: mesa/GPU vendor packages; "
                  "Windows/macOS: bundled with the OS).")
        return exit_code

    if pyopengl:
        line("+", f"PyOpenGL bindings installed: {pyopengl}")
    else:
        line(" ", "PyOpenGL bindings not installed (optional; pip install PyOpenGL)")

    if gl_info:
        line("+", f"OpenGL is ACTIVE (context created via {active_backend})")
        for key in ("vendor", "renderer", "version", "glsl"):
            if key in gl_info:
                print(f"      {key.capitalize():<9}: {gl_info[key]}")
    else:
        line("-", "Could not create a GL context -- OpenGL present but not provably active.")
        print("      Backend attempts:")
        for backend, status in backend_statuses.items():
            print(f"        {backend:<7}: {status}")
        line(" ", "Tip: pip install glfw  (or pygame / PyOpenGL) to verify a real context.")
        if glx:
            line(" ", "glxinfo reports:")
            for k, v in glx.items():
                print(f"        {k:<13}: {v}")

    for note in notes:
        line("*", note)

    print("-" * 60)
    print(f"Verdict: {verdict}  (exit code {exit_code})")
    return exit_code


def main():
    parser = argparse.ArgumentParser(description="Check whether OpenGL is installed and active.")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    sys.exit(run(args.json))


if __name__ == "__main__":
    main()
