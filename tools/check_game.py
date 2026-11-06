#!/usr/bin/env python3
"""Integration checks using a locally owned, unmodified game resource file."""

import argparse
import ctypes
import ctypes.util
from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from elvisualfix.assets import SerializedAsset
from elvisualfix.installer import inspect, mutate, read_asset, sha256
from elvisualfix.shaders import inspect_asset


def check(resource):
    resource = resource.resolve()
    original_hash = sha256(resource)
    with read_asset(resource) as original:
        reports = inspect_asset(original)
        if not reports or not all(report.changes for report in reports):
            raise ValueError("Use an original resource file with affected shaders")
        expected_blobs = {report.shader.name: report.repaired_blob for report in reports}
        with tempfile.TemporaryDirectory(prefix="visualfix-game-check-") as directory:
            root = Path(directory)
            working = root / "resources.assets"
            state = root / "VisualFix"
            shutil.copyfile(resource, working)
            result = mutate(working, state, "install")
            assert result["changed"]
            with read_asset(working) as patched:
                after = inspect_asset(patched)
                assert all(report.state == "installed" for report in after)
                for report in after:
                    assert report.shader.blob == expected_blobs[report.shader.name]
                modified_ids = {report.obj.path_id for report in reports}
                # Check every unrelated serialized object, not only shader objects.
                assert len(original.objects) == len(patched.objects)
                untouched = 0
                for obj in original.objects:
                    other = patched.by_id[obj.path_id]
                    assert other.class_id == obj.class_id
                    if obj.path_id not in modified_ids:
                        assert original.object_bytes(obj) == patched.object_bytes(other)
                        untouched += 1
                print("Unrelated objects preserved:", untouched)
                # Optional independent check with a native LZ4 decoder.
                candidates = [ctypes.util.find_library("lz4"), "/opt/homebrew/lib/liblz4.dylib"]
                for candidate in candidates:
                    if not candidate:
                        continue
                    try:
                        library = ctypes.CDLL(candidate)
                    except OSError:
                        continue
                    function = library.LZ4_decompress_safe
                    function.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
                    function.restype = ctypes.c_int
                    from elvisualfix.assets import Reader
                    for report in after:
                        shader = report.shader
                        reader = Reader(shader.raw, shader.blob_header)
                        unpacked, packed = reader.unpack("<II")
                        compressed = reader.take(packed)
                        output = ctypes.create_string_buffer(unpacked)
                        assert function(compressed, output, packed, unpacked) == unpacked
                        assert output.raw == shader.blob
                    print("Native LZ4 decoder agrees for every repaired shader")
                    break
            identity = working.stat().st_ino
            assert not mutate(working, state, "install")["changed"]
            assert working.stat().st_ino == identity
            assert mutate(working, state, "uninstall")["exact_restore"]
            assert sha256(working) == original_hash
            print("Exact installation, idempotency and byte-for-byte removal passed")
    assert sha256(resource) == original_hash
    print("Input game file unchanged:", original_hash)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", type=Path)
    check(parser.parse_args().resource)
