"""Read Unity 5.3 serialized assets and relocate resized objects.

Unknown object bytes, metadata fields, data gaps and external resource files
are preserved. Object offsets come from the file's own object table.
"""

from dataclasses import dataclass
import struct


class AssetError(ValueError):
    pass


def align(value, boundary=4):
    return (value + boundary - 1) // boundary * boundary


class Reader:
    def __init__(self, data, position=0, limit=None):
        self.data = data
        self.position = position
        self.limit = len(data) if limit is None else limit

    def take(self, count):
        if count < 0 or self.position + count > self.limit:
            raise AssetError("Truncated Unity data")
        result = self.data[self.position:self.position + count]
        self.position += count
        return result

    def unpack(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))

    def u32(self):
        return self.unpack("<I")[0]

    def i32(self):
        return self.unpack("<i")[0]

    def pad(self):
        self.take(align(self.position) - self.position)

    def string(self):
        value = self.take(self.u32())
        self.pad()
        return value


def encode_string(value):
    result = struct.pack("<I", len(value)) + value
    return result + bytes(align(len(result)) - len(result))


@dataclass(frozen=True)
class ObjectInfo:
    path_id: int
    start: int
    size: int
    class_id: int
    record: int


class SerializedAsset:
    def __init__(self, data):
        self.data = data
        if len(data) < 24:
            raise AssetError("Not a Unity serialized asset file")
        metadata_size, file_size, version, data_offset = struct.unpack_from(">4I", data)
        if version != 15 or data[16] != 0:
            raise AssetError("Expected a little-endian Unity serialized file, format 15")
        if file_size != len(data) or data_offset < 20 or data_offset > file_size:
            raise AssetError("Invalid Unity file size or data offset")
        metadata_end = 20 + metadata_size
        if metadata_end > data_offset:
            raise AssetError("Unity metadata overlaps object data")
        self.data_offset = data_offset
        reader = Reader(data, 20, metadata_end)
        version_bytes = bytearray()
        while True:
            value = reader.take(1)
            if value == b"\x00":
                break
            version_bytes.extend(value)
            if len(version_bytes) > 64:
                raise AssetError("Invalid Unity version string")
        self.unity_version = version_bytes.decode("ascii")
        if not self.unity_version.startswith("5.3."):
            raise AssetError("Expected the native game's Unity 5.3 shader format")
        if reader.i32() != 2:
            raise AssetError("Expected macOS player assets")
        type_tree = reader.take(1)[0]
        if type_tree not in (0, 1):
            raise AssetError("Invalid Unity type-tree flag")
        type_count = reader.u32()
        if type_count > (metadata_end - reader.position) // 20:
            raise AssetError("Invalid Unity type count")
        for _ in range(type_count):
            class_id = reader.i32()
            if class_id < 0 or class_id == 114:
                reader.take(16)
            reader.take(16)
            if type_tree:
                nodes, strings = reader.unpack("<II")
                reader.take(nodes * 24 + strings)
        count = reader.u32()
        if count > (metadata_end - reader.position) // 25:
            raise AssetError("Invalid Unity object count")
        objects = []
        path_ids = set()
        for _ in range(count):
            reader.pad()
            record = reader.position
            path_id, start, size, type_id, class_id, script, stripped = reader.unpack("<qIIihhB")
            start += data_offset
            if path_id in path_ids or start < data_offset or start + size > file_size:
                raise AssetError("Invalid or duplicate Unity object entry")
            path_ids.add(path_id)
            objects.append(ObjectInfo(path_id, start, size, class_id, record))
        occupied = sorted((obj for obj in objects if obj.size), key=lambda obj: obj.start)
        end = data_offset
        for obj in occupied:
            if obj.start < end:
                raise AssetError("Overlapping Unity object entries")
            end = obj.start + obj.size
        self.objects = objects
        self.by_id = {obj.path_id: obj for obj in objects}

    def object_bytes(self, obj):
        return self.data[obj.start:obj.start + obj.size]


@dataclass
class RewritePlan:
    metadata: bytes
    replacements: list
    file_size: int


def plan_rewrite(asset, objects):
    """Plan replacements by path ID; preserve 8-byte alignment of later data."""
    edits = []
    for path_id, new_bytes in objects.items():
        obj = asset.by_id[path_id]
        # Physical padding belongs outside the object's reported serialized size.
        padding = (obj.size - len(new_bytes)) % 8
        payload = new_bytes + bytes(padding)
        edits.append((obj, payload, len(new_bytes)))
    edits.sort(key=lambda edit: edit[0].start)
    metadata = bytearray(asset.data[:asset.data_offset])
    changes = {obj.path_id: (payload, size) for obj, payload, size in edits}
    edit_index = 0
    delta = 0
    for obj in sorted(asset.objects, key=lambda item: item.start):
        while edit_index < len(edits) and edits[edit_index][0].start < obj.start:
            previous, payload, _ = edits[edit_index]
            delta += len(payload) - previous.size
            edit_index += 1
        relative = obj.start - asset.data_offset + delta
        size = changes[obj.path_id][1] if obj.path_id in changes else obj.size
        if relative > 0xFFFFFFFF or size > 0xFFFFFFFF:
            raise AssetError("Unity format 15 size limit exceeded")
        struct.pack_into("<II", metadata, obj.record + 8, relative, size)
    total_delta = sum(len(payload) - obj.size for obj, payload, _ in edits)
    file_size = len(asset.data) + total_delta
    if file_size > 0xFFFFFFFF:
        raise AssetError("Unity format 15 file size limit exceeded")
    struct.pack_into(">I", metadata, 4, file_size)
    return RewritePlan(bytes(metadata), edits, file_size)


def copy_count(source, output, count):
    while count:
        block = source.read(min(count, 1024 * 1024))
        if not block:
            raise AssetError("Resource file changed or became truncated")
        output.write(block)
        count -= len(block)


def write_rewrite(source, output, plan):
    output.write(plan.metadata)
    source.seek(len(plan.metadata))
    for obj, payload, _ in plan.replacements:
        copy_count(source, output, obj.start - source.tell())
        source.seek(obj.size, 1)
        output.write(payload)
    while True:
        block = source.read(1024 * 1024)
        if not block:
            break
        output.write(block)
    if output.tell() != plan.file_size:
        raise AssetError("Incorrect rebuilt Unity file size")
