# tests/test_channel_virtualizer.py
import time
from unittest.mock import patch
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


# --- Task 2: SEND_CHAN_MSG and Reassignment ---


def _make_send_chan_msg_payload(chan_idx: int, msg: str, timestamp: int = 0) -> bytes:
    """Build a SEND_CHAN_MSG (0x03) payload."""
    return (
        b"\x03\x00"
        + chan_idx.to_bytes(1, "little")
        + timestamp.to_bytes(4, "little")
        + msg.encode("utf-8")
    )


def test_send_chan_msg_rewrites_index():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    set_payload = _make_set_channel_payload(5, "Weather")
    result = alloc.process_outgoing(addr, set_payload)
    physical_idx = result[1]

    msg_payload = _make_send_chan_msg_payload(5, "hello")
    result = alloc.process_outgoing(addr, msg_payload)

    assert result[0] == 0x03, "Command type preserved"
    assert result[1] == 0x00, "Flags preserved"
    assert result[2] == physical_idx, "Channel index rewritten to physical"
    assert result[7:] == b"hello", "Message text preserved"


def test_send_chan_msg_unmapped_passes_through():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    msg_payload = _make_send_chan_msg_payload(3, "hello")
    result = alloc.process_outgoing(addr, msg_payload)

    assert result == msg_payload, "Unmapped slot passes through unchanged"


def test_reassignment_releases_old_slot():
    """When a client reassigns a virtual slot, the old physical slot is released."""
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    payload_w = _make_set_channel_payload(0, "Weather")
    result_w = alloc.process_outgoing(addr, payload_w)
    physical_w = result_w[1]

    # Occupy the slot that would be reclaimed so reassignment must pick a new one
    addr_hold = ("127.0.0.1", 9999)
    alloc.process_outgoing(addr_hold, _make_set_channel_payload(0, "Weather"))

    payload_n = _make_set_channel_payload(0, "News")
    result_n = alloc.process_outgoing(addr, payload_n)
    physical_n = result_n[1]

    assert physical_w != physical_n, "Different channels get different physical slots"
    # Weather slot is still held by addr_hold, so it remains allocated (not available)
    assert physical_w not in alloc._available_slots


def test_reassignment_keeps_shared_slot():
    """Reassignment doesn't release a physical slot still used by another client."""
    alloc = ChannelSlotAllocator(max_slots=40)
    addr_a = ("127.0.0.1", 9000)
    addr_b = ("127.0.0.1", 9001)

    payload = _make_set_channel_payload(0, "Weather")
    alloc.process_outgoing(addr_a, payload)
    alloc.process_outgoing(addr_b, payload)

    payload_n = _make_set_channel_payload(0, "News")
    alloc.process_outgoing(addr_b, payload_n)

    phys_weather = alloc._virtual_to_physical[(addr_a, 0)]
    assert phys_weather in alloc._physical_slots


# --- Task 3: LRU Eviction ---


def test_lru_eviction_when_slots_exhausted():
    """When all slots are full, the least recently used slot is evicted."""
    alloc = ChannelSlotAllocator(max_slots=2)
    addr = ("127.0.0.1", 9000)

    with patch("meshcore_proxy.channel_virtualizer.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        alloc.process_outgoing(addr, _make_set_channel_payload(0, "ChannelA"))
        # Fix up last_used (default_factory captures real time.monotonic)
        alloc._physical_slots[alloc._virtual_to_physical[(addr, 0)]].last_used = 100.0

        mock_time.monotonic.return_value = 200.0
        alloc.process_outgoing(addr, _make_set_channel_payload(1, "ChannelB"))
        alloc._physical_slots[alloc._virtual_to_physical[(addr, 1)]].last_used = 200.0

        mock_time.monotonic.return_value = 300.0
        result = alloc.process_outgoing(addr, _make_set_channel_payload(2, "ChannelC"))

    assert result is not None, "Should allocate after eviction"
    assert len(alloc._physical_slots) == 2, "Still only 2 slots used"
    assert (addr, 0) not in alloc._virtual_to_physical


def test_lru_eviction_respects_recent_usage():
    """LRU eviction picks the slot that was used least recently."""
    alloc = ChannelSlotAllocator(max_slots=2)
    addr = ("127.0.0.1", 9000)

    with patch("meshcore_proxy.channel_virtualizer.time") as mock_time:
        mock_time.monotonic.return_value = 100.0
        alloc.process_outgoing(addr, _make_set_channel_payload(0, "ChannelA"))
        alloc._physical_slots[alloc._virtual_to_physical[(addr, 0)]].last_used = 100.0

        mock_time.monotonic.return_value = 200.0
        alloc.process_outgoing(addr, _make_set_channel_payload(1, "ChannelB"))
        alloc._physical_slots[alloc._virtual_to_physical[(addr, 1)]].last_used = 200.0

        mock_time.monotonic.return_value = 300.0
        alloc.process_outgoing(addr, _make_send_chan_msg_payload(0, "ping"))

        mock_time.monotonic.return_value = 400.0
        alloc.process_outgoing(addr, _make_set_channel_payload(2, "ChannelC"))

    assert (addr, 0) in alloc._virtual_to_physical, "ChannelA survives (recently used)"
    assert (addr, 1) not in alloc._virtual_to_physical, "ChannelB evicted (LRU)"


# --- Task 4: Response Rewriting and Client Disconnect ---


def _make_channel_info_response(channel_idx: int, name: str) -> bytes:
    """Build a CHANNEL_INFO (0x12) response payload."""
    name_bytes = name.encode("utf-8")[:32].ljust(32, b"\x00")
    return b"\x12" + channel_idx.to_bytes(1, "little") + name_bytes


def _make_channel_msg_recv_response(
    channel_idx: int, path_len: int = 1, txt_type: int = 0,
    timestamp: int = 0, text: str = "hello",
) -> bytes:
    """Build a CHANNEL_MSG_RECV (0x08) response payload."""
    return (
        b"\x08"
        + channel_idx.to_bytes(1, "little")
        + path_len.to_bytes(1, "little")
        + txt_type.to_bytes(1, "little")
        + timestamp.to_bytes(4, "little")
        + text.encode("utf-8")
    )


def test_response_rewrites_channel_info_for_mapped_client():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    set_payload = _make_set_channel_payload(5, "Weather")
    result = alloc.process_outgoing(addr, set_payload)
    physical_idx = result[1]

    response = _make_channel_info_response(physical_idx, "Weather")
    rewritten = alloc.process_incoming(addr, response)

    assert rewritten[1] == 5, "Physical index rewritten to virtual index"
    assert rewritten[0] == 0x12, "Response type preserved"
    assert rewritten[2:] == response[2:], "Rest of payload unchanged"


def test_response_rewrites_channel_msg_recv():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    set_payload = _make_set_channel_payload(3, "Alerts")
    result = alloc.process_outgoing(addr, set_payload)
    physical_idx = result[1]

    response = _make_channel_msg_recv_response(physical_idx, text="alert!")
    rewritten = alloc.process_incoming(addr, response)

    assert rewritten[1] == 3, "Physical index rewritten to virtual index"
    assert rewritten[0] == 0x08, "Response type preserved"


def test_response_passes_through_for_unmapped_client():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr_a = ("127.0.0.1", 9000)
    addr_b = ("127.0.0.1", 9001)

    set_payload = _make_set_channel_payload(0, "Weather")
    result = alloc.process_outgoing(addr_a, set_payload)
    physical_idx = result[1]

    response = _make_channel_info_response(physical_idx, "Weather")
    rewritten = alloc.process_incoming(addr_b, response)

    assert rewritten == response, "Unmapped client gets raw physical index"


def test_non_channel_response_passes_through():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    response = b"\x0c\x64\x00"
    rewritten = alloc.process_incoming(addr, response)

    assert rewritten == response, "Non-channel response unchanged"


def test_remove_client_releases_exclusive_slots():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    alloc.process_outgoing(addr, _make_set_channel_payload(0, "Weather"))
    alloc.process_outgoing(addr, _make_set_channel_payload(1, "News"))

    assert len(alloc._physical_slots) == 2

    alloc.remove_client(addr)

    assert len(alloc._physical_slots) == 0, "All slots released"
    assert len(alloc._virtual_to_physical) == 0, "All mappings cleared"
    assert len(alloc._available_slots) == 40, "All slots available"


def test_remove_client_keeps_shared_slots():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr_a = ("127.0.0.1", 9000)
    addr_b = ("127.0.0.1", 9001)

    payload = _make_set_channel_payload(0, "Weather")
    alloc.process_outgoing(addr_a, payload)
    alloc.process_outgoing(addr_b, payload)

    alloc.remove_client(addr_a)

    assert len(alloc._physical_slots) == 1
    assert (addr_b, 0) in alloc._virtual_to_physical


# --- Task 5: GET_CHANNEL and Pass-Through ---


def _make_get_channel_payload(channel_idx: int) -> bytes:
    """Build a GET_CHANNEL (0x1F) payload."""
    return b"\x1f" + channel_idx.to_bytes(1, "little")


def test_get_channel_rewrites_mapped_index():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    set_payload = _make_set_channel_payload(7, "Weather")
    result = alloc.process_outgoing(addr, set_payload)
    physical_idx = result[1]

    get_payload = _make_get_channel_payload(7)
    result = alloc.process_outgoing(addr, get_payload)

    assert result[0] == 0x1F, "Command type preserved"
    assert result[1] == physical_idx, "Index rewritten to physical"


def test_get_channel_unmapped_passes_through():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    get_payload = _make_get_channel_payload(3)
    result = alloc.process_outgoing(addr, get_payload)

    assert result == get_payload, "Unmapped GET_CHANNEL passes through"


def test_non_channel_command_passes_through():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    payload = b"\x01"
    result = alloc.process_outgoing(addr, payload)

    assert result == payload, "Non-channel command unchanged"


def test_empty_payload_passes_through():
    alloc = ChannelSlotAllocator(max_slots=40)
    addr = ("127.0.0.1", 9000)

    result = alloc.process_outgoing(addr, b"")
    assert result == b""

    result = alloc.process_incoming(addr, b"")
    assert result == b""
