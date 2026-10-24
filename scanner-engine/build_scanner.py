# build_scanner.py
# Run: python build_scanner.py
# Builds: CLI scanner + Overlay bridge (for Electron)
# Cross-platform: Windows, macOS, Linux

import os
import sys
import shutil
import tempfile
from pathlib import Path

import PyInstaller.__main__

ENGINE_DIR = Path(__file__).resolve().parent
os.chdir(ENGINE_DIR)

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

DIST = ENGINE_DIR / "dist"
BUILD = Path(tempfile.gettempdir()) / "sc-price-scanner-pyinstaller"
LOCAL_BUILD = ENGINE_DIR / "build"
TESSDATA_DST = ENGINE_DIR / "tessdata"


def tessdata_sources():
    if IS_WIN:
        return [Path(r"C:\Program Files\Tesseract-OCR\tessdata")]
    if IS_MAC:
        return [
            Path("/opt/homebrew/share/tessdata"),
            Path("/usr/local/share/tessdata"),
        ]
    return [
        Path("/usr/share/tesseract-ocr/5/tessdata"),
        Path("/usr/share/tesseract-ocr/4.00/tessdata"),
        Path("/usr/share/tessdata"),
    ]


# Clean previous builds
for p in [DIST, BUILD, LOCAL_BUILD]:
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)

# Prepare tessdata (CI usually pre-copies it; otherwise grab from a local install)
if not TESSDATA_DST.exists():
    for src in tessdata_sources():
        if src.exists():
            print(f"Copying tessdata from {src}...")
            shutil.copytree(src, TESSDATA_DST)
            break
    else:
        print("[WARN] tessdata not found. Bundle it manually or install Tesseract.")

SEP = os.pathsep  # ';' on Windows, ':' on POSIX

# Platform-specific pynput backend
if IS_WIN:
    PYNPUT_BACKENDS = ["pynput.keyboard._win32", "pynput.mouse._win32"]
elif IS_MAC:
    PYNPUT_BACKENDS = ["pynput.keyboard._darwin", "pynput.mouse._darwin"]
else:
    PYNPUT_BACKENDS = ["pynput.keyboard._xorg", "pynput.mouse._xorg"]

COMMON_ARGS = [
    "--onefile",
    "--clean",
    f"--distpath={DIST}",
    f"--workpath={BUILD}",
    f"--specpath={ENGINE_DIR}",
    "--hidden-import=pytesseract",
    "--hidden-import=cv2",
    "--hidden-import=mss",
    "--hidden-import=numpy",
    "--collect-all=pytesseract",
]
COMMON_ARGS += [f"--hidden-import={b}" for b in PYNPUT_BACKENDS]

if TESSDATA_DST.exists():
    COMMON_ARGS.append(f"--add-data={TESSDATA_DST.resolve()}{SEP}tessdata")


def final_name(name: str) -> str:
    if IS_WIN:
        return name
    return name + ("-mac" if IS_MAC else "-linux")


def build(script: Path, name: str, console: bool = False):
    out = final_name(name)
    args = [str(script), f"--name={out}"] + list(COMMON_ARGS)

    # --noconsole is meaningful on Windows. On macOS it turns the build into a
    # .app bundle, which breaks the flat "SC-Price-Scanner-mac" file we ship.
    if not console and IS_WIN:
        args.append("--noconsole")

    print(f"\nBuilding {out}...")
    PyInstaller.__main__.run(args)

    built = DIST / (out + (".exe" if IS_WIN else ""))
    if built.exists():
        size_mb = built.stat().st_size / (1024 * 1024)
        print(f"[OK] {out}: {built} ({size_mb:.1f} MB)")
    else:
        print(f"[FAIL] {out} build failed")
    return built


if __name__ == "__main__":
    print("=" * 60)
    print("  SC Price Scanner - Multi-Target Build")
    print(f"  Platform: {sys.platform}")
    print("=" * 60)

    # 1. CLI Scanner (standalone, hotkey-based)
    build(ENGINE_DIR / "scanner.py", "SC-Price-Scanner")

    # 2. Overlay Bridge (stdin/stdout JSON for Electron)
    build(ENGINE_DIR / "scanner_overlay_bridge.py", "SC-Price-Scanner-Bridge", console=True)

    print("\n" + "=" * 60)
    print("Build complete. Files in dist/:")
    for f in DIST.glob("*"):
        print(f"  {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
