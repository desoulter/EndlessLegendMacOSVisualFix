"""Atomic installation and shader-specific rollback with content-addressed backups."""

from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import mmap
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zlib

from . import __version__
from .assets import AssetError, SerializedAsset, plan_rewrite, write_rewrite
from .shaders import Shader, digest, inspect_asset

RESOURCE = Path("EndlessLegend.app/Contents/Resources/Data/resources.assets")
DEFAULT_GAME = Path.home() / "Library/Application Support/Steam/steamapps/common/Endless Legend"
HASH = re.compile(r"[0-9a-f]{64}\Z")


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def fingerprint(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


@contextmanager
def read_asset(resource):
    with resource.open("rb") as stream:
        if not resource.stat().st_size:
            raise AssetError("Resource file is empty")
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            yield SerializedAsset(data)


def inspect(resource, signatures=None):
    with read_asset(resource) as asset:
        return asset.unity_version, inspect_asset(asset, signatures)


def sync_directory(directory):
    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="receipt-", dir=str(path.parent))
    pending = Path(name)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, path)
        sync_directory(path.parent)
    finally:
        if pending.exists():
            pending.unlink()


def save_backup(resource, state, source_hash):
    directory = state / "backups" / source_hash
    backup = directory / "resources.assets"
    if backup.exists():
        if sha256(backup) != source_hash:
            raise AssetError("Existing original backup is damaged; it was not overwritten")
        return backup
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="backup-", dir=str(directory))
    os.close(descriptor)
    pending = Path(name)
    try:
        shutil.copyfile(resource, pending)
        if sha256(pending) != source_hash:
            raise AssetError("Resource file changed while creating the backup")
        with pending.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(pending, backup)
        sync_directory(directory)
    finally:
        if pending.exists():
            pending.unlink()
    return backup


def load_receipts(state):
    for path in sorted((state / "receipts").glob("*.json")):
        try:
            receipt = json.loads(path.read_text())
            if receipt.get("format") != 1:
                continue
            if not all(HASH.fullmatch(receipt[key]) for key in ("source_sha256", "patched_sha256")):
                continue
            if path.stem != receipt["patched_sha256"]:
                continue
            yield receipt
        except (OSError, ValueError, KeyError, TypeError):
            continue


def rollback_objects(reports, receipts):
    objects = {}
    for report in reports:
        if not report.shader.marker:
            continue
        wanted = digest(report.shader.raw)
        match = None
        for receipt in receipts:
            for record in receipt.get("shaders", []):
                if (record.get("name") == report.shader.name and
                        record.get("before_sha256") == report.shader.marker and
                        record.get("after_sha256") == wanted):
                    match = record
                    break
            if match:
                break
        if match is None:
            raise AssetError("Original shader receipt is missing or the installed shader changed: " +
                             report.shader.name + ". No game resources were changed")
        original = zlib.decompress(base64.b64decode(match["before_zlib_base64"], validate=True))
        if digest(original) != report.shader.marker:
            raise AssetError("Original shader receipt checksum is incorrect")
        shader = Shader.read(original)
        if shader is None or shader.name != report.shader.name or shader.marker:
            raise AssetError("Original shader receipt is invalid")
        objects[report.obj.path_id] = original
    return objects


def validate_staged(pending, replacements, signatures):
    with read_asset(pending) as asset:
        inspect_asset(asset, signatures)
        for path_id, expected in replacements.items():
            obj = asset.by_id.get(path_id)
            if obj is None or asset.object_bytes(obj) != expected:
                raise AssetError("Rebuilt shader object did not match the installation plan")


def mutate(resource, state, command, signatures=None, dry_run=False):
    """Analyze each input independently; whole-file hashes identify backups only."""
    if dry_run:
        with read_asset(resource) as asset:
            reports = inspect_asset(asset, signatures)
            objects = {r.obj.path_id: r.shader.rebuild(r.repaired_blob) for r in reports if r.changes}
            plan = plan_rewrite(asset, objects)
            return {"changed": False, "planned": len(objects), "size": plan.file_size,
                    "reports": reports}
    state.mkdir(parents=True, exist_ok=True)
    with (state / "operation.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        initial = fingerprint(resource)
        source_hash = sha256(resource)
        with read_asset(resource) as asset:
            reports = inspect_asset(asset, signatures)
            if command == "install":
                objects = {r.obj.path_id: r.shader.rebuild(r.repaired_blob) for r in reports if r.changes}
                receipts = []
            else:
                receipts = list(load_receipts(state))
                objects = rollback_objects(reports, receipts)
            if not objects:
                return {"changed": False, "reports": reports}
            plan = plan_rewrite(asset, objects)
        descriptor, name = tempfile.mkstemp(prefix="resources.assets.visualfix-", dir=str(resource.parent))
        pending = Path(name)
        try:
            owned_hashes = {digest(report.shader.raw) for report in reports if report.shader.marker}
            exact_receipt = next((item for item in receipts
                if item["patched_sha256"] == source_hash and owned_hashes.issubset(
                    {record.get("after_sha256") for record in item.get("shaders", [])})), None)
            if exact_receipt is not None:
                original = state / "backups" / exact_receipt["source_sha256"] / "resources.assets"
                if not original.is_file() or sha256(original) != exact_receipt["source_sha256"]:
                    raise AssetError("Original full-file backup is missing or damaged")
                with os.fdopen(descriptor, "wb") as output, original.open("rb") as stream:
                    shutil.copyfileobj(stream, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            else:
                with os.fdopen(descriptor, "wb") as output, resource.open("rb") as stream:
                    write_rewrite(stream, output, plan)
                    output.flush()
                    os.fsync(output.fileno())
            descriptor = None
            validate_staged(pending, objects, signatures)
            result_hash = sha256(pending)
            if exact_receipt is not None and result_hash != exact_receipt["source_sha256"]:
                raise AssetError("Original resource checksum did not match")
            if fingerprint(resource) != initial or sha256(resource) != source_hash:
                raise AssetError("Resource file changed during preparation; no game resources were changed")
            backup = save_backup(resource, state, source_hash)
            if command == "install":
                receipt = {"format": 1, "version": __version__, "source_sha256": source_hash,
                           "patched_sha256": result_hash, "shaders": []}
                for report in reports:
                    if report.obj.path_id not in objects:
                        continue
                    before = report.shader.raw
                    after = objects[report.obj.path_id]
                    receipt["shaders"].append({"name": report.shader.name,
                        "before_sha256": digest(before), "after_sha256": digest(after),
                        "before_zlib_base64": base64.b64encode(zlib.compress(before, 9)).decode()})
                # Persist recovery information before replacing the game file.
                atomic_json(state / "receipts" / (result_hash + ".json"), receipt)
            if fingerprint(resource) != initial:
                raise AssetError("Resource file changed before commit; no game resources were changed")
            os.chmod(pending, stat.S_IMODE(resource.stat().st_mode))
            os.replace(pending, resource)
            sync_directory(resource.parent)
            return {"changed": True, "reports": reports, "backup": backup,
                    "sha256": result_hash, "exact_restore": exact_receipt is not None}
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if pending.exists():
                pending.unlink()
