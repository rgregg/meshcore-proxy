"""MeshCore protocol payload decoder for human-readable event logging."""

import io
import struct
from typing import Any, Optional


def decode_response(packet_type: int, payload: bytes) -> Optional[dict[str, Any]]:
    """
    Decode a response payload from the radio into a human-readable dict.
    Returns None if decoding fails or packet type is unknown.
    """
    if len(payload) < 1:
        return None

    try:
        buf = io.BytesIO(payload[1:])  # Skip packet type byte

        if packet_type == 0x00:  # OK
            result = {"status": "OK"}
            if len(payload) == 5:
                result["value"] = int.from_bytes(payload[1:5], byteorder="little")
            return result

        elif packet_type == 0x01:  # ERROR
            result = {"status": "ERROR"}
            if len(payload) > 1:
                result["error_code"] = payload[1]
            return result

        elif packet_type == 0x02:  # CONTACT_START
            return {"contact_count": int.from_bytes(payload[1:5], byteorder="little")}

        elif packet_type == 0x03 or packet_type == 0x8A:  # CONTACT or NEW_ADVERT
            return _decode_contact(buf)

        elif packet_type == 0x04:  # CONTACT_END
            return {"lastmod": int.from_bytes(buf.read(4), byteorder="little")}

        elif packet_type == 0x05:  # SELF_INFO
            return _decode_self_info(buf)

        elif packet_type == 0x06:  # MSG_SENT
            msg_type = buf.read(1)[0]
            expected_ack = buf.read(4).hex()
            suggested_timeout = int.from_bytes(buf.read(4), byteorder="little")
            return {
                "msg_type": msg_type,
                "expected_ack": expected_ack,
                "timeout_ms": suggested_timeout,
            }

        elif packet_type == 0x07:  # CONTACT_MSG_RECV
            return _decode_contact_msg(buf)

        elif packet_type == 0x08:  # CHANNEL_MSG_RECV
            return _decode_channel_msg(buf)

        elif packet_type == 0x09:  # CURRENT_TIME
            return {"time": int.from_bytes(buf.read(4), byteorder="little")}

        elif packet_type == 0x0A:  # NO_MORE_MSGS
            return {"messages_available": False}

        elif packet_type == 0x0B:  # CONTACT_URI
            return {"uri": "meshcore://" + buf.read().hex()}

        elif packet_type == 0x0C:  # BATTERY
            level = int.from_bytes(buf.read(2), byteorder="little")
            result = {"level_mv": level}
            if len(payload) > 3:
                result["used_kb"] = int.from_bytes(buf.read(4), byteorder="little")
                result["total_kb"] = int.from_bytes(buf.read(4), byteorder="little")
            return result

        elif packet_type == 0x0D:  # DEVICE_INFO
            return _decode_device_info(buf, payload)

        elif packet_type == 0x12:  # CHANNEL_INFO
            return _decode_channel_info(buf)

        elif packet_type == 0x80:  # ADVERTISEMENT
            return {"public_key": buf.read(32).hex()}

        elif packet_type == 0x81:  # PATH_UPDATE
            return {"public_key": buf.read(32).hex()}

        elif packet_type == 0x82:  # ACK
            if len(payload) >= 5:
                return {"ack_code": buf.read(4).hex()}
            return {"ack": True}

        elif packet_type == 0x83:  # MESSAGES_WAITING
            return {"messages_waiting": True}

        elif packet_type == 0x85:  # LOGIN_SUCCESS
            result = {"login": "success"}
            if len(payload) > 1:
                perms = buf.read(1)[0]
                result["is_admin"] = (perms & 1) == 1
                result["pubkey_prefix"] = buf.read(6).hex()
            return result

        elif packet_type == 0x86:  # LOGIN_FAILED
            return {"login": "failed"}

        elif packet_type == 0x87:  # STATUS_RESPONSE
            return {"status_response": True, "data_len": len(payload) - 1}

        elif packet_type == 0x8B:  # TELEMETRY_RESPONSE
            buf.read(1)  # reserved
            return {"pubkey_prefix": buf.read(6).hex(), "telemetry_len": len(payload) - 8}

        elif packet_type == 0x8C:  # BINARY_RESPONSE
            buf.read(1)  # reserved
            tag = buf.read(4).hex()
            return {"tag": tag, "data_len": len(payload) - 6}

        elif packet_type == 0x18:  # STATS (24)
            return _decode_stats(payload)

        elif packet_type == 0x15:  # CUSTOM_VARS
            raw = buf.read().decode("utf-8", "ignore")
            if raw:
                pairs = {}
                for p in raw.split(","):
                    if ":" in p:
                        k, v = p.split(":", 1)
                        pairs[k] = v
                return {"vars": pairs}
            return {"vars": {}}

    except Exception:
        pass

    return None


def decode_command(packet_type: int, payload: bytes) -> Optional[dict[str, Any]]:
    """
    Decode a command payload going to the radio into a human-readable dict.
    Returns None if decoding fails or packet type is unknown.
    """
    if len(payload) < 1:
        return None

    try:
        buf = io.BytesIO(payload[1:])  # Skip command type byte

        if packet_type == 0x01:  # APPSTART
            if len(payload) >= 3:
                version = payload[1]
                app_name = payload[2:].decode("utf-8", "ignore").strip()
                return {"version": version, "app": app_name}

        elif packet_type == 0x02:  # SEND_MSG
            msg_type = buf.read(1)[0]
            attempt = buf.read(1)[0]
            timestamp = int.from_bytes(buf.read(4), byteorder="little")
            dst = buf.read(6).hex()  # First 6 bytes of destination
            text = buf.read().decode("utf-8", "ignore")
            return {
                "type": "command" if msg_type == 1 else "message",
                "attempt": attempt,
                "timestamp": timestamp,
                "to": dst,
                "text": text[:50] + "..." if len(text) > 50 else text,
            }

        elif packet_type == 0x03:  # SEND_CHAN_MSG
            buf.read(1)  # flags
            chan = buf.read(1)[0]
            timestamp = int.from_bytes(buf.read(4), byteorder="little")
            text = buf.read().decode("utf-8", "ignore")
            return {
                "channel": chan,
                "timestamp": timestamp,
                "text": text[:50] + "..." if len(text) > 50 else text,
            }

        elif packet_type == 0x04:  # GET_CONTACTS
            result = {}
            if len(payload) > 1:
                result["lastmod"] = int.from_bytes(buf.read(4), byteorder="little")
            return result

        elif packet_type == 0x06:  # SET_TIME
            return {"time": int.from_bytes(buf.read(4), byteorder="little")}

        elif packet_type == 0x08:  # SET_NAME
            return {"name": buf.read().decode("utf-8", "ignore")}

        elif packet_type == 0x0B:  # SET_RADIO
            freq = int.from_bytes(buf.read(4), byteorder="little") / 1000
            bw = int.from_bytes(buf.read(4), byteorder="little") / 1000
            sf = buf.read(1)[0]
            cr = buf.read(1)[0]
            return {"freq_mhz": freq, "bw_khz": bw, "sf": sf, "cr": cr}

        elif packet_type == 0x0C:  # SET_TX_POWER
            return {"tx_power": int.from_bytes(buf.read(4), byteorder="little")}

        elif packet_type == 0x0E:  # SET_COORDS
            lat = int.from_bytes(buf.read(4), byteorder="little", signed=True) / 1e6
            lon = int.from_bytes(buf.read(4), byteorder="little", signed=True) / 1e6
            return {"lat": lat, "lon": lon}

        elif packet_type == 0x16:  # DEVICE_QUERY
            return {"query": "device_info"}

        elif packet_type == 0x1A:  # SEND_LOGIN
            dst = buf.read(32).hex()
            pwd = buf.read().decode("utf-8", "ignore")
            return {"to": dst[:12] + "...", "password": "***"}

        elif packet_type == 0x1F:  # GET_CHANNEL
            return {"channel_idx": buf.read(1)[0]}

        elif packet_type == 0x20:  # SET_CHANNEL
            idx = buf.read(1)[0]
            name = buf.read(32).decode("utf-8", "ignore").rstrip("\x00")
            return {"channel_idx": idx, "name": name}

        elif packet_type == 0x25:  # SET_DEVICE_PIN
            return {"pin": int.from_bytes(buf.read(4), byteorder="little")}

        elif packet_type == 0x27:  # GET_TELEMETRY
            buf.read(3)  # reserved
            if len(payload) > 4:
                return {"target": buf.read(6).hex()}
            return {"target": "self"}

        elif packet_type == 0x34:  # PATH_DISCOVERY
            buf.read(1)  # reserved
            return {"target": buf.read(32).hex()[:12] + "..."}

        elif packet_type == 0x38:  # GET_STATS
            stats_type = buf.read(1)[0]
            types = {0: "core", 1: "radio", 2: "packets"}
            return {"stats_type": types.get(stats_type, f"unknown({stats_type})")}

    except Exception:
        pass

    return None


def _decode_contact(buf: io.BytesIO) -> dict[str, Any]:
    """Decode a contact record."""
    public_key = buf.read(32).hex()
    contact_type = buf.read(1)[0]
    flags = buf.read(1)[0]
    path_len = int.from_bytes(buf.read(1), signed=True, byteorder="little")
    buf.read(64)  # path data
    name = buf.read(32).decode("utf-8", "ignore").replace("\0", "")
    last_advert = int.from_bytes(buf.read(4), byteorder="little")
    lat = int.from_bytes(buf.read(4), byteorder="little", signed=True) / 1e6
    lon = int.from_bytes(buf.read(4), byteorder="little", signed=True) / 1e6

    type_names = {0: "node", 1: "repeater", 2: "room"}
    return {
        "name": name,
        "public_key": public_key[:12] + "...",
        "type": type_names.get(contact_type, f"unknown({contact_type})"),
        "path_len": path_len,
        "last_advert": last_advert,
        "lat": lat if lat != 0 else None,
        "lon": lon if lon != 0 else None,
    }


def _decode_self_info(buf: io.BytesIO) -> dict[str, Any]:
    """Decode SELF_INFO response."""
    adv_type = buf.read(1)[0]
    tx_power = buf.read(1)[0]
    max_tx_power = buf.read(1)[0]
    public_key = buf.read(32).hex()
    lat = int.from_bytes(buf.read(4), byteorder="little", signed=True) / 1e6
    lon = int.from_bytes(buf.read(4), byteorder="little", signed=True) / 1e6
    buf.read(1)  # multi_acks
    buf.read(1)  # adv_loc_policy
    buf.read(1)  # telemetry_mode
    buf.read(1)  # manual_add_contacts
    freq = int.from_bytes(buf.read(4), byteorder="little") / 1000
    bw = int.from_bytes(buf.read(4), byteorder="little") / 1000
    sf = buf.read(1)[0]
    cr = buf.read(1)[0]
    name = buf.read().decode("utf-8", "ignore")

    type_names = {0: "node", 1: "client", 2: "repeater", 3: "room"}
    return {
        "name": name,
        "type": type_names.get(adv_type, f"unknown({adv_type})"),
        "public_key": public_key[:12] + "...",
        "tx_power": tx_power,
        "freq_mhz": freq,
        "bw_khz": bw,
        "sf": sf,
        "cr": cr,
        "lat": lat if lat != 0 else None,
        "lon": lon if lon != 0 else None,
    }


def _decode_device_info(buf: io.BytesIO, payload: bytes) -> dict[str, Any]:
    """Decode DEVICE_INFO response."""
    fw_ver = buf.read(1)[0]
    result = {"fw_version": fw_ver}

    if payload[1] >= 3 and len(payload) > 60:
        result["max_contacts"] = buf.read(1)[0] * 2
        result["max_channels"] = buf.read(1)[0]
        buf.read(4)  # ble_pin
        result["fw_build"] = buf.read(12).decode("utf-8", "ignore").replace("\0", "")
        result["model"] = buf.read(40).decode("utf-8", "ignore").replace("\0", "")
        result["version"] = buf.read(20).decode("utf-8", "ignore").replace("\0", "")

    return result


def _decode_channel_info(buf: io.BytesIO) -> dict[str, Any]:
    """Decode CHANNEL_INFO response."""
    idx = buf.read(1)[0]
    name_bytes = buf.read(32)
    null_pos = name_bytes.find(0)
    if null_pos >= 0:
        name = name_bytes[:null_pos].decode("utf-8", "ignore")
    else:
        name = name_bytes.decode("utf-8", "ignore")
    return {"channel_idx": idx, "name": name}


def _decode_contact_msg(buf: io.BytesIO) -> dict[str, Any]:
    """Decode a contact message."""
    pubkey_prefix = buf.read(6).hex()
    path_len = buf.read(1)[0]
    txt_type = buf.read(1)[0]
    timestamp = int.from_bytes(buf.read(4), byteorder="little")

    result = {
        "from": pubkey_prefix,
        "path_len": path_len,
        "timestamp": timestamp,
    }

    if txt_type == 2:
        result["signature"] = buf.read(4).hex()

    text = buf.read().decode("utf-8", "ignore")
    result["text"] = text[:100] + "..." if len(text) > 100 else text
    result["type"] = {0: "text", 1: "command", 2: "signed"}.get(txt_type, f"unknown({txt_type})")

    return result


def _decode_channel_msg(buf: io.BytesIO) -> dict[str, Any]:
    """Decode a channel message."""
    channel_idx = buf.read(1)[0]
    path_len = buf.read(1)[0]
    txt_type = buf.read(1)[0]
    timestamp = int.from_bytes(buf.read(4), byteorder="little")
    text = buf.read().decode("utf-8", "ignore")

    return {
        "channel": channel_idx,
        "path_len": path_len,
        "timestamp": timestamp,
        "text": text[:100] + "..." if len(text) > 100 else text,
        "type": {0: "text", 1: "command"}.get(txt_type, f"unknown({txt_type})"),
    }


def _decode_stats(payload: bytes) -> dict[str, Any]:
    """Decode stats response."""
    if len(payload) < 2:
        return {}

    stats_type = payload[1]

    if stats_type == 0 and len(payload) >= 11:  # STATS_CORE
        battery_mv, uptime_secs, errors, queue_len = struct.unpack("<HIHB", payload[2:11])
        return {
            "stats_type": "core",
            "battery_mv": battery_mv,
            "uptime_secs": uptime_secs,
            "errors": errors,
            "queue_len": queue_len,
        }

    elif stats_type == 1 and len(payload) >= 14:  # STATS_RADIO
        noise_floor, last_rssi, last_snr_scaled, tx_air_secs, rx_air_secs = struct.unpack(
            "<hbbII", payload[2:14]
        )
        return {
            "stats_type": "radio",
            "noise_floor": noise_floor,
            "last_rssi": last_rssi,
            "last_snr": last_snr_scaled / 4.0,
            "tx_air_secs": tx_air_secs,
            "rx_air_secs": rx_air_secs,
        }

    elif stats_type == 2 and len(payload) >= 26:  # STATS_PACKETS
        recv, sent, flood_tx, direct_tx, flood_rx, direct_rx = struct.unpack(
            "<IIIIII", payload[2:26]
        )
        return {
            "stats_type": "packets",
            "recv": recv,
            "sent": sent,
            "flood_tx": flood_tx,
            "direct_tx": direct_tx,
            "flood_rx": flood_rx,
            "direct_rx": direct_rx,
        }

    return {"stats_type": f"unknown({stats_type})"}


def format_decoded(decoded: dict[str, Any]) -> str:
    """Format a decoded payload dict as a concise string."""
    if not decoded:
        return ""

    parts = []
    for key, value in decoded.items():
        if value is None:
            continue
        if isinstance(value, bool):
            if value:
                parts.append(key)
        elif isinstance(value, float):
            parts.append(f"{key}={value:.2f}")
        elif isinstance(value, dict):
            # Nested dict (like vars)
            inner = ", ".join(f"{k}={v}" for k, v in value.items())
            parts.append(f"{key}={{{inner}}}")
        else:
            parts.append(f"{key}={value}")

    return " | ".join(parts)
