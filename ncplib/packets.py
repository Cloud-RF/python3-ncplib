import warnings
from collections import namedtuple, OrderedDict
from struct import Struct
from ncplib.errors import DecodeError, DecodeWarning
from ncplib.helpers import unix_to_datetime, datetime_to_unix
from ncplib.values import encode_value, decode_value


# Packet structs.

PACKET_HEADER_STRUCT = Struct("<4s4sII4sII4s")

FIELD_HEADER_STRUCT = Struct("<4s3sBI")

PARAM_HEADER_STRUCT = Struct("<4s3sB")

PACKET_FOOTER_STRUCT = Struct("<I4s")


# Identifier encoding.

def encode_identifier(value):
    return value.encode("latin1").ljust(4, b" ")


# Identifier decoding.

def decode_identifier(value):
    return value.rstrip(b" \x00").decode("latin1")


# u24 size encoding.

def encode_u24_size(value):
    return (value // 4).to_bytes(4, "little")


# u24 size decoding.

def decode_u24_size(value):
    return int.from_bytes(value, "little") * 4


# Param encoding.

def encode_params(params):
    buf = bytearray()
    for name, value in params.items():
        type_id, encoded_value = encode_value(value)
        size = PARAM_HEADER_STRUCT.size + len(encoded_value)
        padding_size = -size % 4
        buf.extend(PARAM_HEADER_STRUCT.pack(
            encode_identifier(name),
            encode_u24_size(size + padding_size),
            type_id,
        ))
        buf.extend(encoded_value)
        buf.extend(b"\x00" * padding_size)
    return buf


# Param decoding.

def decode_params(buf, offset, limit):
    while offset < limit:
        # HACK: Work around a known garbled NCP packet problem from Axis nodes.
        if buf[offset:offset+8] == b"\x00\x00\x00\x00\xaa\xbb\xcc\xdd":
            warnings.warn(DecodeWarning("Encountered embedded packet footer bug"))
            offset += 8
            continue
        # Keep decoding.
        name, u24_size, type_id = PARAM_HEADER_STRUCT.unpack_from(buf, offset)
        name = decode_identifier(name)
        size = decode_u24_size(u24_size)
        value_encoded = bytes(buf[offset+PARAM_HEADER_STRUCT.size:offset+size])
        value = decode_value(type_id, value_encoded)
        yield name, value
        offset += size
    if offset > limit:  # pragma: no cover
        raise DecodeError("Parameter overflow by {} bytes".format(offset - limit))


# Field encoding.

FieldData = namedtuple("FieldData", ("name", "id", "params",))


def encode_fields(fields):
    buf = bytearray()
    for name, field_id, params in fields:
        encoded_params = encode_params(params)
        buf.extend(FIELD_HEADER_STRUCT.pack(
            encode_identifier(name),
            encode_u24_size(FIELD_HEADER_STRUCT.size + len(encoded_params)),
            0,  # Field type ID is ignored.
            field_id,
        ))
        buf.extend(encoded_params)
    return buf


# Field decoding.

def decode_fields(buf, offset, limit):
    while offset < limit:
        name, u24_size, type_id, field_id = FIELD_HEADER_STRUCT.unpack_from(buf, offset)
        name = decode_identifier(name)
        size = decode_u24_size(u24_size)
        params = OrderedDict(decode_params(buf, offset+FIELD_HEADER_STRUCT.size, offset+size))
        yield FieldData(
            name=name,
            id=field_id,
            params=params,
        )
        offset += size
    if offset > limit:  # pragma: no cover
        raise DecodeError("Field overflow by {} bytes".format(offset - limit))


# Packet encoding.

def encode_packet(packet_type, packet_id, timestamp, info, fields):
    timestamp_unix, timestamp_nano = datetime_to_unix(timestamp)
    encoded_fields = encode_fields(fields)
    # Encode the header.
    buf = bytearray(32)  # 32 is the size of the packet header.
    PACKET_HEADER_STRUCT.pack_into(
        buf, 0,
        b"\xdd\xcc\xbb\xaa",  # Hardcoded packet header.
        encode_identifier(packet_type),
        (40 + len(encoded_fields)) // 4,  # 40 is the size of the packet header plus footer.
        packet_id,
        b'\x01\x00\x00\x00',
        timestamp_unix, timestamp_nano,
        info,
    )
    # Write the packet fields.
    buf.extend(encoded_fields)
    # Encode the packet footer.
    buf.extend(b"\x00\x00\x00\x00\xaa\xbb\xcc\xdd")  # Hardcoded packet footer with no checksum.
    # All done!
    return buf


# PacketData decoding.

PacketData = namedtuple("PacketData", ("type", "id", "timestamp", "info", "fields",))


def decode_packet_cps(header_buf):
    (
        header,
        packet_type,
        size_words,
        packet_id,
        format_id,
        time,
        nanotime,
        info,
    ) = PACKET_HEADER_STRUCT.unpack(header_buf)
    packet_type = decode_identifier(packet_type)
    size = size_words * 4
    if header != b"\xdd\xcc\xbb\xaa":  # pragma: no cover
        raise DecodeError("Invalid packet header {}".format(header))
    timestamp = unix_to_datetime(time, nanotime)
    # Decode the rest of the body data.
    size_remaining = size - PACKET_HEADER_STRUCT.size

    def decode_packet_body(body_buf):
        if len(body_buf) > size_remaining:  # pragma: no cover
            raise DecodeError("Packet body overflow by {} bytes".format(len(body_buf) - size_remaining))
        fields = list(decode_fields(body_buf, 0, size_remaining - PACKET_FOOTER_STRUCT.size))
        (
            checksum,
            footer,
        ) = PACKET_FOOTER_STRUCT.unpack_from(body_buf, size_remaining - PACKET_FOOTER_STRUCT.size)
        if footer != b"\xaa\xbb\xcc\xdd":  # pragma: no cover
            raise DecodeError("Invalid packet footer {}".format(footer))
        # All done!
        return PacketData(
            type=packet_type,
            id=packet_id,
            timestamp=timestamp,
            info=info,
            fields=fields,
        )

    # Return the number of bytes to read, and the function to finish decoding.
    return size_remaining, decode_packet_body


def decode_packet(buf):
    body_size, decode_packet_body = decode_packet_cps(buf[:PACKET_HEADER_STRUCT.size])
    return decode_packet_body(buf[PACKET_HEADER_STRUCT.size:])
