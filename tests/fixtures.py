"""Original synthetic Unity assets; no game files are included in the project."""

import struct

from elvisualfix.assets import align, encode_string
from elvisualfix import lz4
from elvisualfix.shaders import PATH_SHADER, LAVA_SHADER, canonical_digest

EXPRESSION = b"(tmpvar_1 * (pow (max (0.0, 1.0), 0.0) * _Intensity))"
SOURCE = b"""#version 120
#ifdef VERTEX
void main() { gl_Position = gl_Vertex; }
#endif
#ifdef FRAGMENT
uniform float _Intensity;
uniform vec4 _Color;
void main() {
  vec3 tmpvar_1;
  vec3 color = _Color.rgb + """ + EXPRESSION + b""";
  gl_FragData[0] = vec4(color, 1.0);
}
#endif
"""
REPAIRED = SOURCE.replace(EXPRESSION, b"vec3(0.0)" + b" " * (len(EXPRESSION) - 9))
UNRELATED_PROGRAM = b"""#version 120
#ifdef VERTEX
void main() { gl_Position = gl_Vertex; }
#endif
#ifdef FRAGMENT
void main() { gl_FragData[0] = vec4(1.0); }
#endif
"""


def rule(source, repaired, changes):
    return {"original": canonical_digest(source), "repaired": canonical_digest(repaired), "changes": changes}


RULES = {name: [rule(SOURCE, REPAIRED, 1), rule(UNRELATED_PROGRAM, UNRELATED_PROGRAM, 0)]
         for name in (PATH_SHADER, LAVA_SHADER)}


def blob(sources):
    header_size = 4 + 8 * len(sources)
    header = bytearray(struct.pack("<I", len(sources)))
    body = bytearray()
    for source in sources:
        record = encode_string(source)
        header.extend(struct.pack("<II", header_size + len(body), len(record)))
        body.extend(record)
    return bytes(header + body)


def shader(name=PATH_SHADER, sources=None, trailing=b"\x00\x00\x00\x00TAIL"):
    sources = [SOURCE] if sources is None else sources
    code = blob(sources)
    packed = lz4.compress(code)
    return (encode_string(b"SyntheticShader") + encode_string(b'Shader "' + name.encode() + b'" {\n}\n') +
            encode_string(b"") + struct.pack("<II", len(code), len(packed)) + packed +
            bytes(align(len(packed)) - len(packed)) + trailing)


def asset(objects=None, type_tree=False, reverse_table=False, padding=16):
    # Entries: path ID, Unity class ID, serialized object bytes.
    objects = [(101, 48, shader()), (301, 28, b"UNRELATED-TEXTURE"),
               (202, 48, shader(LAVA_SHADER, [SOURCE, UNRELATED_PROGRAM]))] if objects is None else objects
    metadata = bytearray(b"5.3.6p1\x00" + struct.pack("<iB", 2, bool(type_tree)))
    classes = sorted({obj[1] for obj in objects})
    metadata.extend(struct.pack("<I", len(classes)))
    for class_id in classes:
        metadata.extend(struct.pack("<i", class_id))
        if class_id < 0 or class_id == 114:
            metadata.extend(bytes(16))
        metadata.extend(bytes(16))
        if type_tree:
            metadata.extend(struct.pack("<II", 1, 4) + bytes(24) + b"test")
    metadata.extend(struct.pack("<I", len(objects)))
    records = {}
    for path_id, class_id, raw in (list(reversed(objects)) if reverse_table else objects):
        metadata.extend(bytes(align(20 + len(metadata)) - 20 - len(metadata)))
        records[path_id] = len(metadata)
        metadata.extend(struct.pack("<qIIihhB", path_id, 0, len(raw), class_id, class_id, -1, 0))
    metadata.extend(b"\x00\x00\x00\x00SYNTHETIC-METADATA")
    data_offset = align(20 + len(metadata) + padding, 16)
    body = bytearray()
    for path_id, class_id, raw in objects:
        body.extend(bytes(align(len(body), 8) - len(body)))
        struct.pack_into("<I", metadata, records[path_id] + 8, len(body))
        body.extend(raw)
    body.extend(b"UNREFERENCED-TRAILER")
    header = struct.pack(">4I", len(metadata), data_offset + len(body), 15, data_offset) + bytes(4)
    return header + metadata + bytes(data_offset - 20 - len(metadata)) + body
