#!/usr/bin/env python3
"""Create a reproducible source release without game assets or recovery data."""

import hashlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from elvisualfix import __version__


def package(destination=None):
    destination = ROOT / "dist" if destination is None else Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    name = "EndlessLegendMacOSVisualFix-" + __version__
    files = [ROOT / filename for filename in ("visual_fix.py", "README.md", "LICENSE", "RELEASE_NOTES.md")]
    for directory in ("elvisualfix", "tests", "tools"):
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.is_file() and "__pycache__" not in path.parts and path.suffix in (".py", ".json"))
    archive = destination / (name + ".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as output:
        for path in sorted(files):
            info = zipfile.ZipInfo(name + "/" + path.relative_to(ROOT).as_posix(), (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            output.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(archive) as check:
        if check.testzip() is not None:
            raise RuntimeError("Release archive validation failed")
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    sums = destination / "SHA256SUMS"
    sums.write_text(checksum + "  " + archive.name + "\n")
    print(archive)
    print(sums)
    return archive


if __name__ == "__main__":
    package()
