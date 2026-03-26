# tests/test_channel_virtualizer.py
import time
from meshcore_proxy.channel_virtualizer import ChannelSlotAllocator


def _make_set_channel_payload(virtual_idx: int, name: str, secret: bytes = None) -> bytes:
    """Build a SET_CHANNEL (0x20) payload."""
    if secret is None:
        from hashlib import sha256
        secret = sha256(name.encode("utf-8")).digest()[:16]
    name_bytes = name.encode("utf-8")[:32].ljust(32, b"\x00")
    return b"\x20" + virtual_idx.to_bytes(1, "little") + name_bytes + secret


def test_set_channel_allocates_physical_slot():
    alloc = ChannelSlotAllocator(max_slots=40)
    client_addr = ("127.0.0.1", 9000)
    payload = _make_set_channel_payload(0, "Weather")

    result = alloc.process_outgoing(client_addr, payload)

    assert result is not None, "Should return rewritten payload"
    assert result[0] == 0x20, "Command type preserved"
    physical_idx = result[1]
    assert 0 <= physical_idx < 40
    assert result[2:] == payload[2:]


def test_set_channel_dedup_returns_none():
    """Second client setting same channel should return None (no radio command needed)."""
    alloc = ChannelSlotAllocator(max_slots=40)
    addr_a = ("127.0.0.1", 9000)
    addr_b = ("127.0.0.1", 9001)
    payload = _make_set_channel_payload(0, "Weather")

    alloc.process_outgoing(addr_a, payload)
    result = alloc.process_outgoing(addr_b, payload)

    assert result is None, "Dedup hit: no command sent to radio"
