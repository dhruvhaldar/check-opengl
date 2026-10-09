# check-opengl

[![CI](https://github.com/dhruvhaldar/check-opengl/actions/workflows/ci.yml/badge.svg)](https://github.com/dhruvhaldar/check-opengl/actions/workflows/ci.yml)
![Python 3.7+](https://img.shields.io/badge/python-3.7+-blue.svg)
![Platforms](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-lightgrey)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A single-file, **dependency-free** Python script that checks whether OpenGL is
installed — and actually *active* — on Windows, macOS, and Linux
(X11, Wayland, and headless servers via EGL).

## What it checks

1. **Installed?** — loads the platform's native OpenGL runtime via `ctypes`:
   - Windows: `opengl32.dll`
   - macOS: `/System/Library/Frameworks/OpenGL.framework/OpenGL`
   - Linux: `libGL.so.1` / `libOpenGL.so` (with `find_library` fallback)
2. **Bindings?** — reports whether PyOpenGL is importable (optional).
3. **Active?** — creates a *real* GL context and queries the driver for
   vendor, renderer, GL version, and GLSL version.

## Usage

```bash
python check_opengl.py            # human-readable report
python check_opengl.py --json     # machine-readable JSON (CI-friendly)
```

Or run the pre-built standalone Windows binary (available in GitHub Actions build artifacts / releases):
```powershell
check-opengl.exe
```

### Exit codes

| Code | Meaning                                                             |
| ---- | ------------------------------------------------------------------- |
| 0    | OpenGL installed **and** active (context created and queried)       |
| 1    | OpenGL runtime present, but no context could be created / verified  |
| 2    | No OpenGL runtime found at all                                      |

Because the exit code encodes the verdict, you can use it directly in CI:

```yaml
- name: Check OpenGL
  run: python check_opengl.py --json
```

## How "active" is verified

The script tries context-creation backends in order and uses the first one that works:

| Order | Backend | Requires                 | Notes                                                   |
| ----- | ------- | ------------------------ | ------------------------------------------------------- |
| 1     | GLFW    | `pip install glfw`       | hidden window; best desktop option                      |
| 2     | pygame  | `pip install pygame`     | SDL2-based                                              |
| 3     | GLUT    | `pip install PyOpenGL`   | skipped on headless Linux (freeglut aborts the process) |
| 4     | WGL     | nothing                  | Windows-only; pure-ctypes hidden window                 |
| 5     | EGL     | nothing                  | headless servers, Wayland, ANGLE                        |

With **no optional packages installed**, the WGL backend (Windows) and the EGL
backend (Linux, including headless) still verify a real context.
## Platform notes

- **Windows**:
  - Run directly via the pre-built **`check-opengl.exe`** (no Python required) or run `python check_opengl.py`.
  - `opengl32.dll` ships with Windows; actual hardware acceleration comes from your GPU driver (ICD via WGL).
- **Linux**:
  - Run via `python check_opengl.py`.
  - Uses native `libGL.so` or `libEGL.so` (works headless with Mesa / EGL surfaceless). If `glxinfo` is available, its output is parsed as a diagnostic fallback.
- **macOS**:
  - Run via `python check_opengl.py` (install `glfw` via `pip install glfw` for desktop context verification).
  - The OpenGL framework is present on the OS (version capped at 4.1).

## Requirements

- Python 3.7+
- No required dependencies (standard library only: `ctypes`, `argparse`, `json`, `platform`, `shutil`, `subprocess`)

## Example output

```
OpenGL check -- Linux-6.8.0-49-generic-x86_64-with-glibc2.39
------------------------------------------------------------
[+] Native OpenGL library found: libGL.so.1
[ ] PyOpenGL bindings not installed (optional; pip install PyOpenGL)
[+] OpenGL is ACTIVE (context created via glfw)
      Vendor   : NVIDIA Corporation
      Renderer : NVIDIA GeForce RTX 4070/PCIe/SSE2
      Version  : 4.6.0 NVIDIA 550.107.02
      Glsl     : 4.60 NVIDIA
------------------------------------------------------------
Verdict: active  (exit code 0)
```
