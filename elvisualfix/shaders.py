"""Discover and repair the game's affected OpenGL shader programs."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import struct

from .assets import AssetError, Reader, align, encode_string
from . import lz4

PATH_SHADER = "Amplitude/Characters/PathFind"
LAVA_SHADER = "Custom/RiverLava"
TARGETS = (PATH_SHADER, LAVA_SHADER)
MARKER = re.compile(rb"\n// ELVisualFix:1 ([0-9a-f]{64})\n\Z")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical_digest(source):
    return digest(re.sub(rb"\s+", b" ", source).strip())


def load_signatures():
    payload = json.loads(Path(__file__).with_name("signatures.json").read_text())
    if payload["format"] != 1 or set(payload["shaders"]) != set(TARGETS):
        raise AssetError("Invalid shader signature database")
    return payload["shaders"]


@dataclass
class Shader:
    raw: bytes
    name: str
    script: bytes
    script_position: int
    script_end: int
    blob_header: int
    blob_end: int
    blob: bytes

    @classmethod
    def read(cls, raw):
        reader = Reader(raw)
        reader.string()  # Unity object name; the full shader name is in ShaderLab.
        script_position = reader.position
        script = reader.string()
        script_end = reader.position
        match = re.match(rb'Shader\s+"([^"\n]+)"', script)
        name = match[1].decode("utf-8") if match else ""
        if name not in TARGETS:
            return None
        reader.string()  # Source path.
        blob_header = reader.position
        unpacked_size, packed_size = reader.unpack("<II")
        packed = reader.take(packed_size)
        reader.pad()
        blob = lz4.decompress(packed, unpacked_size)
        return cls(raw, name, script, script_position, script_end,
                   blob_header, reader.position, blob)

    @property
    def marker(self):
        match = MARKER.search(self.script)
        if b"ELVisualFix:" in self.script and not match:
            raise AssetError("Unrecognized VisualFix marker in " + self.name)
        return match[1].decode() if match else None

    def rebuild(self, fixed_blob):
        if len(fixed_blob) != len(self.blob):
            raise AssetError("Shader program lengths changed")
        script = self.script.rstrip() + b"\n// ELVisualFix:1 " + digest(self.raw).encode() + b"\n"
        compressed = lz4.compress(fixed_blob)
        result = (self.raw[:self.script_position] + encode_string(script) +
                  self.raw[self.script_end:self.blob_header] +
                  struct.pack("<II", len(fixed_blob), len(compressed)) + compressed +
                  bytes(align(len(compressed)) - len(compressed)) +
                  self.raw[self.blob_end:])
        check = Shader.read(result)
        if check.blob != fixed_blob or check.marker != digest(self.raw):
            raise AssetError("Rebuilt shader validation failed")
        if check.raw[check.blob_end:] != self.raw[self.blob_end:]:
            raise AssetError("Shader fields after the program blob changed")
        return result


def programs(blob):
    previous_end = 0
    found = 0
    for match in re.finditer(rb"#version 120\n", blob):
        start = match.start()
        if start < 4:
            raise AssetError("Invalid GLSL string offset")
        size = struct.unpack_from("<I", blob, start - 4)[0]
        end = start + size
        if start < previous_end or end > len(blob):
            raise AssetError("Invalid or overlapping GLSL strings")
        source = blob[start:end]
        if not source.endswith(b"#endif\n"):
            raise AssetError("Unrecognized GLSL string layout")
        found += 1
        previous_end = end
        yield start, source
    if not found:
        raise AssetError("No supported OpenGL programs found in shader")


def repair_glsl(source):
    """Remove the complete product using an unassigned specular color."""
    if b"#ifdef FRAGMENT" not in source:
        return source, 0
    fragment_start = source.index(b"#ifdef FRAGMENT")
    fragment = source[fragment_start:]
    result = bytearray(source)
    count = 0
    for declaration in re.finditer(rb"  vec3 (tmpvar_\d+);", fragment):
        name = declaration[1]
        body = fragment[declaration.end():]
        uses = list(re.finditer(rb"\b" + name + rb"\b", body))
        if not uses or re.search(rb"\b" + name + rb"(?:\.[xyzwrgba]+)?\s*=", body):
            continue
        if len(uses) != 1:
            raise AssetError("Unexpected uses of an unassigned shader variable")
        begin = declaration.end() + uses[0].start() - 1
        if fragment[begin:begin + 1] != b"(":
            raise AssetError("Unexpected specular expression")
        level = 0
        end = None
        for index in range(begin, len(fragment)):
            if fragment[index] == 40:
                level += 1
            elif fragment[index] == 41:
                level -= 1
                if level == 0:
                    end = index + 1
                    break
        if end is None:
            raise AssetError("Unbalanced specular expression")
        old = fragment[begin:end]
        if not old.startswith(b"(" + name + b" *"):
            raise AssetError("Unexpected unassigned-variable expression")
        if b"pow " not in old and b"light_2.w" not in old and b"tmpvar_28.w" not in old:
            raise AssetError("Unrecognized specular lighting expression")
        replacement = b"vec3(0.0)" + bytes([32]) * (len(old) - 9)
        absolute = fragment_start + begin
        result[absolute:absolute + len(old)] = replacement
        count += 1
    return bytes(result), count


@dataclass
class Report:
    obj: object
    shader: Shader
    state: str
    changes: int
    repaired_blob: bytes
    affected_programs: int


def inspect_shader(obj, shader, signatures):
    rules = signatures[shader.name]
    original = {rule["original"]: rule for rule in rules}
    repaired = {rule["repaired"]: rule for rule in rules}
    blob = bytearray(shader.blob)
    changes = 0
    affected_programs = 0
    for start, source in programs(shader.blob):
        fingerprint = canonical_digest(source)
        rule = original.get(fingerprint)
        if rule is not None and rule["changes"]:
            fixed, count = repair_glsl(source)
            if count != rule["changes"] or canonical_digest(fixed) != rule["repaired"]:
                raise AssetError("Shader repair did not match the recognized program")
            blob[start:start + len(source)] = fixed
            changes += count
            affected_programs += 1
        elif fingerprint in repaired:
            affected_programs += bool(repaired[fingerprint]["changes"])
        else:
            raise AssetError("Unrecognized GLSL program in " + shader.name +
                             "; no game resources were changed")
    marker = shader.marker
    if marker and changes:
        raise AssetError("VisualFix shader marker and shader code disagree")
    state = "needs-fix" if changes else ("installed" if marker else "already-corrected")
    return Report(obj, shader, state, changes, bytes(blob), affected_programs)


def inspect_asset(asset, signatures=None):
    signatures = load_signatures() if signatures is None else signatures
    reports = []
    for obj in asset.objects:
        if obj.class_id != 48:
            continue
        shader = Shader.read(asset.object_bytes(obj))
        if shader is not None:
            reports.append(inspect_shader(obj, shader, signatures))
    if not any(report.shader.name == PATH_SHADER for report in reports):
        raise AssetError("Movement path shader was not found; no game resources were changed")
    return reports
