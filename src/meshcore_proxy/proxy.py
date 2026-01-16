"""MeshCore TCP Proxy implementation."""

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional

from .decoder import decode_command, decode_response, format_decoded

logger = logging.getLogger(__name__)

# Try importing meshcore - first from installed package, then from submodule
try:
    from meshcore.serial_cx import SerialConnection
    from meshcore.ble_cx import BLEConnection
    from meshcore.packets import PacketType
except ImportError:
    # Fall back to submodule path for development
    submodule_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "meshcore_py", "src"
    )
    sys.path.insert(0, os.path.abspath(submodule_path))
    from meshcore.serial_cx import SerialConnection
    from meshcore.ble_cx import BLEConnection
    from meshcore.packets import PacketType


class EventLogLevel(Enum):
    """Event logging verbosity levels."""

    OFF = "off"
    SUMMARY = "summary"
    VERBOSE = "verbose"


# Map response packet types to human-readable names for logging
RESPONSE_TYPE_NAMES = {v.value: v.name for v in PacketType}

# Map command codes to human-readable names
# These are the first byte of outgoing packets (client -> radio)
COMMAND_TYPE_NAMES = {
    0x01: "CMD_APPSTART",
    0x02: "CMD_SEND_MSG",
    0x03: "CMD_SEND_CHAN_MSG",
    0x04: "CMD_GET_CONTACTS",
    0x05: "CMD_GET_TIME",
    0x06: "CMD_SET_TIME",
    0x07: "CMD_SEND_ADVERT",
    0x08: "CMD_SET_NAME",
    0x09: "CMD_UPDATE_CONTACT",
    0x0A: "CMD_GET_MSG",
    0x0B: "CMD_SET_RADIO",
    0x0C: "CMD_SET_TX_POWER",
    0x0D: "CMD_RESET_PATH",
    0x0E: "CMD_SET_COORDS",
    0x0F: "CMD_REMOVE_CONTACT",
    0x10: "CMD_SHARE_CONTACT",
    0x11: "CMD_EXPORT_CONTACT",
    0x12: "CMD_IMPORT_CONTACT",
    0x13: "CMD_REBOOT",
    0x14: "CMD_GET_BATTERY",
    0x15: "CMD_SET_TUNING",
    0x16: "CMD_DEVICE_QUERY",
    0x17: "CMD_EXPORT_PRIVATE_KEY",
    0x18: "CMD_IMPORT_PRIVATE_KEY",
    0x1A: "CMD_SEND_LOGIN",
    0x1B: "CMD_SEND_STATUS_REQ",
    0x1D: "CMD_SEND_LOGOUT",
    0x1F: "CMD_GET_CHANNEL",
    0x20: "CMD_SET_CHANNEL",
    0x21: "CMD_SIGN_START",
    0x22: "CMD_SIGN_DATA",
    0x23: "CMD_SIGN_FINISH",
    0x24: "CMD_SEND_TRACE",
    0x25: "CMD_SET_DEVICE_PIN",
    0x26: "CMD_SET_OTHER_PARAMS",
    0x27: "CMD_GET_TELEMETRY",
    0x28: "CMD_GET_CUSTOM_VARS",
    0x29: "CMD_SET_CUSTOM_VAR",
    0x32: "CMD_BINARY_REQ",
    0x33: "CMD_FACTORY_RESET",
    0x34: "CMD_PATH_DISCOVERY",
    0x36: "CMD_SET_FLOOD_SCOPE",
    0x37: "CMD_SEND_CONTROL_DATA",
    0x38: "CMD_GET_STATS",
    0x39: "CMD_REQUEST_ADVERT",
}


@dataclass
class TCPClient:
    """Represents a connected TCP client."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    addr: tuple

    # Frame parsing state (same as meshcore_py TCP connection)
    frame_started: bool = False
    frame_size: int = 0
    header: bytes = b""
    inframe: bytes = b""


class MeshCoreProxy:
    """
    TCP proxy for MeshCore companion radios.

    Connects to a MeshCore radio via Serial or BLE and exposes it
    to remote clients via TCP.
    """

    def __init__(
        self,
        serial_port: Optional[str] = None,
        ble_address: Optional[str] = None,
        baud_rate: int = 115200,
        ble_pin: str = "123456",
        tcp_host: str = "0.0.0.0",
        tcp_port: int = 5000,
        event_log_level: EventLogLevel = EventLogLevel.OFF,
        event_log_json: bool = False,
    ):
        self.serial_port = serial_port
        self.ble_address = ble_address
        self.baud_rate = baud_rate
        self.ble_pin = ble_pin
        self.tcp_host = tcp_host
        self.tcp_port = tcp_port
        self.event_log_level = event_log_level
        self.event_log_json = event_log_json

        self._radio_connection: Optional[SerialConnection | BLEConnection] = None
        self._tcp_server: Optional[asyncio.Server] = None
        self._clients: dict[tuple, TCPClient] = {}
        self._is_ble = False

    def _log_event(
        self,
        direction: str,
        packet_type: int,
        payload: bytes,
    ) -> None:
        """Log a MeshCore event based on configured verbosity."""
        if self.event_log_level == EventLogLevel.OFF:
            return

        # Get human-readable packet type name based on direction
        if direction == "TO_RADIO":
            # Commands going to the radio
            type_name = COMMAND_TYPE_NAMES.get(packet_type, f"CMD_UNKNOWN(0x{packet_type:02x})")
            decoded = decode_command(packet_type, payload)
        else:
            # Responses coming from the radio
            type_name = RESPONSE_TYPE_NAMES.get(packet_type, f"RESP_UNKNOWN(0x{packet_type:02x})")
            decoded = decode_response(packet_type, payload)

        # Format decoded data
        decoded_str = format_decoded(decoded) if decoded else ""

        if self.event_log_json:
            log_data = {
                "direction": direction,
                "packet_type": type_name,
                "packet_type_raw": packet_type,
            }
            if decoded:
                log_data["decoded"] = decoded
            if self.event_log_level == EventLogLevel.VERBOSE:
                log_data["payload_hex"] = payload.hex()
                log_data["payload_len"] = len(payload)
            print(json.dumps(log_data), flush=True)
        else:
            arrow = "->" if direction == "TO_RADIO" else "<-"
            if self.event_log_level == EventLogLevel.SUMMARY:
                if decoded_str:
                    print(f"{arrow} {type_name}: {decoded_str}", flush=True)
                else:
                    print(f"{arrow} {type_name}", flush=True)
            else:  # VERBOSE
                if decoded_str:
                    print(f"{arrow} {type_name}: {decoded_str}", flush=True)
                    print(f"   [{len(payload)} bytes]: {payload.hex()}", flush=True)
                else:
                    print(f"{arrow} {type_name} [{len(payload)} bytes]: {payload.hex()}", flush=True)

    def _frame_payload(self, payload: bytes) -> bytes:
        """Frame a payload for TCP transmission (0x3c + 2-byte size + payload)."""
        size = len(payload)
        return b"\x3c" + size.to_bytes(2, byteorder="little") + payload

    async def _handle_radio_rx(self, payload: bytes) -> None:
        """Handle data received from the radio - forward to all TCP clients."""
        if len(payload) == 0:
            return

        # Log the event
        packet_type = payload[0] if payload else 0
        self._log_event("FROM_RADIO", packet_type, payload)

        # Frame and forward to all TCP clients
        framed = self._frame_payload(payload)
        disconnected = []

        for addr, client in self._clients.items():
            try:
                client.writer.write(framed)
                await client.writer.drain()
            except Exception as e:
                logger.warning(f"Failed to forward to client {addr}: {e}")
                disconnected.append(addr)

        # Clean up disconnected clients
        for addr in disconnected:
            await self._remove_client(addr)

    async def _send_to_radio(self, payload: bytes) -> None:
        """Send a payload to the radio."""
        if not self._radio_connection:
            logger.error("Radio not connected")
            return

        # Log the event
        packet_type = payload[0] if payload else 0
        self._log_event("TO_RADIO", packet_type, payload)

        # BLE sends raw payload, Serial/TCP adds framing
        if self._is_ble:
            await self._radio_connection.send(payload)
        else:
            await self._radio_connection.send(payload)

    def _parse_tcp_frame(self, client: TCPClient, data: bytes) -> list[bytes]:
        """
        Parse incoming TCP data into complete frames.
        Returns list of complete payloads (without frame headers).
        Uses same framing logic as meshcore_py.
        """
        payloads = []
        offset = 0

        while offset < len(data):
            remaining = data[offset:]

            if not client.frame_started:
                # Need 3-byte header: 0x3c + 2-byte size
                header_needed = 3 - len(client.header)
                if len(remaining) >= header_needed:
                    client.header = client.header + remaining[:header_needed]
                    client.frame_started = True
                    client.frame_size = int.from_bytes(client.header[1:], byteorder="little")
                    offset += header_needed
                else:
                    client.header = client.header + remaining
                    break
            else:
                # Accumulate frame data
                frame_needed = client.frame_size - len(client.inframe)
                if len(remaining) >= frame_needed:
                    client.inframe = client.inframe + remaining[:frame_needed]
                    payloads.append(client.inframe)

                    # Reset for next frame
                    client.frame_started = False
                    client.header = b""
                    client.inframe = b""
                    offset += frame_needed
                else:
                    client.inframe = client.inframe + remaining
                    break

        return payloads

    async def _remove_client(self, addr: tuple) -> None:
        """Remove a client and close its connection."""
        if addr in self._clients:
            client = self._clients.pop(addr)
            try:
                client.writer.close()
                await client.writer.wait_closed()
            except Exception:
                pass
            logger.info(f"Client disconnected: {addr}")

    async def _handle_tcp_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a TCP client connection."""
        addr = writer.get_extra_info("peername")
        logger.info(f"Client connected: {addr}")

        client = TCPClient(reader=reader, writer=writer, addr=addr)
        self._clients[addr] = client

        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break

                # Parse frames from the TCP data
                payloads = self._parse_tcp_frame(client, data)

                # Forward each complete payload to the radio
                for payload in payloads:
                    await self._send_to_radio(payload)

        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            logger.debug(f"Client {addr} connection reset")
        except Exception as e:
            logger.error(f"Error handling client {addr}: {e}")
        finally:
            await self._remove_client(addr)

    async def _connect_radio(self) -> None:
        """Connect to the MeshCore radio."""
        if self.serial_port:
            logger.info(f"Connecting to radio via serial: {self.serial_port}")
            self._radio_connection = SerialConnection(
                self.serial_port,
                self.baud_rate,
            )
            self._is_ble = False
        elif self.ble_address:
            logger.info(f"Connecting to radio via BLE: {self.ble_address}")
            self._radio_connection = BLEConnection(
                address=self.ble_address,
                pin=self.ble_pin if self.ble_pin else None,
            )
            self._is_ble = True
        else:
            raise ValueError("No connection method specified")

        # Create a reader adapter that forwards to our handler
        class ReaderAdapter:
            def __init__(self, handler: Callable):
                self._handler = handler

            async def handle_rx(self, data: bytes) -> None:
                await self._handler(data)

        self._radio_connection.set_reader(ReaderAdapter(self._handle_radio_rx))

        # Connect
        result = await self._radio_connection.connect()
        if result is None:
            raise ConnectionError("Failed to connect to radio")

        logger.info(f"Connected to radio: {result}")

    async def _start_tcp_server(self) -> None:
        """Start the TCP server."""
        self._tcp_server = await asyncio.start_server(
            self._handle_tcp_client,
            self.tcp_host,
            self.tcp_port,
        )
        addrs = ", ".join(str(sock.getsockname()) for sock in self._tcp_server.sockets)
        logger.info(f"TCP server listening on {addrs}")

    async def run(self) -> None:
        """Run the proxy."""
        conn_type = "serial" if self.serial_port else "BLE"
        conn_target = self.serial_port or self.ble_address
        logger.info(f"Starting MeshCore Proxy ({conn_type}: {conn_target})...")

        # Connect to radio
        await self._connect_radio()

        # Start TCP server
        await self._start_tcp_server()

        # Run until cancelled
        async with self._tcp_server:
            await self._tcp_server.serve_forever()

    async def stop(self) -> None:
        """Stop the proxy."""
        logger.info("Stopping MeshCore Proxy...")

        # Close all clients
        for addr in list(self._clients.keys()):
            await self._remove_client(addr)

        # Stop TCP server
        if self._tcp_server:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()

        # Disconnect radio
        if self._radio_connection:
            await self._radio_connection.disconnect()
