# build_scanner.py
# Run: python build_scanner.py
# Builds: CLI scanner + Overlay bridge (for Electron)

import PyInstaller.__main__
import shutil
from pathlib import Path

DIST = Path("dist")
BUILD = Path("build")
TESSDATA_DST = Path("tessdata")
TESSDATA_SRC = Path(r"C:\Program Files\Tesseract-OCR\tessdata")

# Clean previous builds
for p in [DIST, BUILD]:
    if p.exists():
        shutil.rmtree(p)

# Prepare tessdata
if not TESSDATA_DST.exists():
    if TESSDATA_SRC.exists():
        print(f"Copying tessdata from {TESSDATA_SRC}...")
        shutil.copytree(TESSDATA_SRC, TESSDATA_DST)
    else:
        print("⚠️  tessdata not found. OCR will need system Tesseract install.")

# Common PyInstaller args
COMMON_ARGS = [
    "--onefile",
    "--noconsole",
    "--clean",
    "--add-data=tessdata;tessdata",
    "--hidden-import=pytesseract",
    "--hidden-import=cv2",
    "--hidden-import=mss",
    "--hidden-import=pynput.keyboard._win32",
    "--hidden-import=pynput.mouse._win32",
    "--hidden-import=numpy",
    "--collect-all=pytesseract",
]

def build(script: Path, name: str, console: bool = False):
    args = [str(script), f"--name={name}"] + COMMON_ARGS
    if console:
        args.remove("--noconsole")
    print(f"\nBuilding {name}...")
    PyInstaller.__main__.run(args)
    
    exe = DIST / f"{name}.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f"✅ {name}: {exe} ({size_mb:.1f} MB)")
    else:
        print(f"❌ {name} build failed")
    return exe

if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════╗")
    print("║     SC Price Scanner — Multi-Target Build                ║")
    print("╚══════════════════════════════════════════════════════════╝")
    
    # 1. CLI Scanner (standalone, hotkey-based)
    build(Path("scanner.py"), "SC-Price-Scanner")
    
    # 2. Overlay Bridge (stdin/stdout JSON for Electron)
    build(Path("scanner_overlay_bridge.py"), "SC-Price-Scanner-Bridge", console=True)
    
    print("\n══════════════════════════════════════════════════════════")
    print("Build complete. Files in dist/:")
    for f in DIST.glob("*"):
        print(f"  {f.name} ({f.stat().st_size / 1024 / 1024:.1f} MB)")
    
    # Cleanup temp tessdata copy
    if TESSDATA_DST.exists() and not TESSDATA_SRC.joinpath("eng.traineddata").exists():
        shutil.rmtree(TESSDATA_DST)