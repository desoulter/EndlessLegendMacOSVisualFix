"""Raw LZ4 blocks using only Python's standard library."""


def decompress(data, expected):
    if not 0 <= expected <= 64 * 1024 * 1024:
        raise ValueError("Invalid shader decompression size")
    output = bytearray()
    position = 0

    def extended(length):
        nonlocal position
        if length == 15:
            while True:
                if position >= len(data):
                    raise ValueError("Truncated LZ4 length")
                value = data[position]
                position += 1
                length += value
                if value != 255:
                    break
        return length

    while position < len(data):
        token = data[position]
        position += 1
        length = extended(token >> 4)
        if position + length > len(data) or len(output) + length > expected:
            raise ValueError("Invalid LZ4 literals")
        output.extend(data[position:position + length])
        position += length
        if position == len(data):
            break
        if position + 2 > len(data):
            raise ValueError("Truncated LZ4 offset")
        offset = data[position] | data[position + 1] << 8
        position += 2
        if not offset or offset > len(output):
            raise ValueError("Invalid LZ4 match offset")
        length = extended(token & 15) + 4
        if len(output) + length > expected:
            raise ValueError("LZ4 output exceeds expected size")
        for _ in range(length):
            output.append(output[-offset])
    if len(output) != expected:
        raise ValueError("Wrong LZ4 output size")
    return bytes(output)


def compress(data):
    """Emit a valid raw LZ4 block with one literal sequence.

    Shader blobs are small. A literal sequence avoids native dependencies and
    compression-library ABI differences; it is decoded by Unity's normal LZ4
    reader. The asset writer supports the resulting object size increase.
    """
    length = len(data)
    output = bytearray([min(length, 15) << 4])
    if length >= 15:
        remaining = length - 15
        while remaining >= 255:
            output.append(255)
            remaining -= 255
        output.append(remaining)
    output.extend(data)
    return bytes(output)
