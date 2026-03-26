# src/meshcore_proxy/channel_virtualizer.py
"""Channel slot virtualization for multi-client MeshCore proxy."""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Command codes
CMD_SET_CHANNEL = 0x20
CMD_SEND_CHAN_MSG = 0x03
CMD_GET_CHANNEL = 0x1F

# Response codes with channel indices
RESP_CHANNEL_INFO = 0x12
RESP_CHANNEL_MSG_RECV = 0x08


@dataclass
class PhysicalSlot:
    """A physical channel slot on the radio."""

    physical_idx: int
    config: bytes  # raw channel config (name + secret, bytes 2-49 of SET_CHANNEL)
    clients: set = field(default_factory=set)  # set of (client_addr, virtual_idx)
    last_used: float = field(default_factory=time.monotonic)


class ChannelSlotAllocator:
    """Manages virtual-to-physical channel slot mapping with dedup and LRU eviction."""

    def __init__(self, max_slots: int = 40):
        self._max_slots = max_slots
        self._physical_slots: dict[int, PhysicalSlot] = {}
        self._virtual_to_physical: dict[tuple, int] = {}
        self._available_slots: set[int] = set(range(max_slots))

    def process_outgoing(self, client_addr: tuple, payload: bytes) -> bytes | None:
        """Process an outgoing command, rewriting channel indices if needed.

        Returns:
            Rewritten payload to send to radio, or None if command should be
            suppressed (dedup hit on SET_CHANNEL).
        """
        if not payload:
            return payload

        cmd = payload[0]

        if cmd == CMD_SET_CHANNEL:
            return self._handle_set_channel(client_addr, payload)
        elif cmd == CMD_SEND_CHAN_MSG:
            return self._handle_send_chan_msg(client_addr, payload)
        elif cmd == CMD_GET_CHANNEL:
            return self._handle_get_channel(client_addr, payload)
        else:
            return payload

    def _handle_set_channel(self, client_addr: tuple, payload: bytes) -> bytes | None:
        """Handle SET_CHANNEL: allocate/dedup physical slot, rewrite index."""
        virtual_idx = payload[1]
        config = payload[2:]  # name (32 bytes) + secret (16 bytes)
        client_key = (client_addr, virtual_idx)

        # Step 1: Handle reassignment - release old mapping first
        if client_key in self._virtual_to_physical:
            old_physical = self._virtual_to_physical.pop(client_key)
            if old_physical in self._physical_slots:
                slot = self._physical_slots[old_physical]
                slot.clients.discard(client_key)
                if not slot.clients:
                    del self._physical_slots[old_physical]
                    self._available_slots.add(old_physical)
                    logger.debug(
                        f"Released physical slot {old_physical} (no remaining clients)"
                    )

        # Step 2: Check for dedup match
        for phys_idx, slot in self._physical_slots.items():
            if slot.config == config:
                slot.clients.add(client_key)
                slot.last_used = time.monotonic()
                self._virtual_to_physical[client_key] = phys_idx
                logger.debug(
                    f"Dedup: {client_addr} virtual {virtual_idx} -> "
                    f"existing physical {phys_idx}"
                )
                return None  # No radio command needed

        # Step 3: Allocate new physical slot
        if not self._available_slots:
            self._evict_lru()

        physical_idx = min(self._available_slots)
        self._available_slots.remove(physical_idx)

        self._physical_slots[physical_idx] = PhysicalSlot(
            physical_idx=physical_idx,
            config=config,
            clients={client_key},
        )
        self._virtual_to_physical[client_key] = physical_idx

        logger.debug(
            f"Allocated: {client_addr} virtual {virtual_idx} -> physical {physical_idx}"
        )

        # Rewrite the index byte
        return payload[0:1] + physical_idx.to_bytes(1, "little") + config

    def _handle_send_chan_msg(self, client_addr: tuple, payload: bytes) -> bytes:
        """Handle SEND_CHAN_MSG: rewrite channel index if mapped."""
        virtual_idx = payload[2]
        client_key = (client_addr, virtual_idx)

        physical_idx = self._virtual_to_physical.get(client_key)
        if physical_idx is None:
            logger.warning(
                f"SEND_CHAN_MSG from {client_addr} for unmapped virtual slot "
                f"{virtual_idx}, passing through"
            )
            return payload

        # Update last_used timestamp
        if physical_idx in self._physical_slots:
            self._physical_slots[physical_idx].last_used = time.monotonic()

        # Rewrite: byte 0 (cmd) + byte 1 (flags) + byte 2 (chan idx) + rest
        return payload[0:2] + physical_idx.to_bytes(1, "little") + payload[3:]

    def _handle_get_channel(self, client_addr: tuple, payload: bytes) -> bytes:
        """Handle GET_CHANNEL: rewrite channel index if mapped."""
        virtual_idx = payload[1]
        client_key = (client_addr, virtual_idx)

        physical_idx = self._virtual_to_physical.get(client_key)
        if physical_idx is None:
            return payload  # Pass through unmapped

        return payload[0:1] + physical_idx.to_bytes(1, "little")

    def process_incoming(self, client_addr: tuple, payload: bytes) -> bytes:
        """Rewrite channel indices in radio responses for a specific client.

        Returns the payload with physical indices replaced by virtual indices
        where a mapping exists.
        """
        if not payload:
            return payload

        resp_type = payload[0]

        if resp_type == RESP_CHANNEL_INFO:
            return self._rewrite_response_channel_idx(client_addr, payload, idx_offset=1)
        elif resp_type == RESP_CHANNEL_MSG_RECV:
            return self._rewrite_response_channel_idx(client_addr, payload, idx_offset=1)
        else:
            return payload

    def _rewrite_response_channel_idx(
        self, client_addr: tuple, payload: bytes, idx_offset: int
    ) -> bytes:
        """Rewrite a channel index in a response payload for a specific client."""
        if len(payload) <= idx_offset:
            return payload

        physical_idx = payload[idx_offset]

        if physical_idx not in self._physical_slots:
            return payload

        slot = self._physical_slots[physical_idx]
        # Find this client's virtual index for this physical slot
        for (addr, virtual_idx) in slot.clients:
            if addr == client_addr:
                return (
                    payload[:idx_offset]
                    + virtual_idx.to_bytes(1, "little")
                    + payload[idx_offset + 1 :]
                )

        # Client has no mapping for this physical slot - pass through
        return payload

    def remove_client(self, client_addr: tuple) -> None:
        """Remove all mappings for a disconnected client."""
        keys_to_remove = [
            key for key in self._virtual_to_physical if key[0] == client_addr
        ]
        for client_key in keys_to_remove:
            physical_idx = self._virtual_to_physical.pop(client_key)
            if physical_idx in self._physical_slots:
                slot = self._physical_slots[physical_idx]
                slot.clients.discard(client_key)
                if not slot.clients:
                    del self._physical_slots[physical_idx]
                    self._available_slots.add(physical_idx)
                    logger.debug(
                        f"Released physical slot {physical_idx} "
                        f"(client {client_addr} disconnected)"
                    )

    def _evict_lru(self) -> None:
        """Evict the least recently used physical slot to free space."""
        if not self._physical_slots:
            return

        lru_slot = min(self._physical_slots.values(), key=lambda s: s.last_used)

        logger.warning(
            f"Evicting LRU physical slot {lru_slot.physical_idx} "
            f"(last used {time.monotonic() - lru_slot.last_used:.1f}s ago, "
            f"clients: {lru_slot.clients})"
        )

        # Remove all client mappings pointing to this slot
        for client_key in list(lru_slot.clients):
            self._virtual_to_physical.pop(client_key, None)

        del self._physical_slots[lru_slot.physical_idx]
        self._available_slots.add(lru_slot.physical_idx)
