import asyncio
from unittest.mock import patch

import pytest
from meshcore_proxy.proxy import EventLogLevel, MeshCoreProxy


class MockRadio:
    def __init__(self, connect_fails=0):
        self.connect_fails = connect_fails
        self.connect_attempts = 0
        self.is_connected = False
        self.on_disconnect = None
        self.on_receive = None
        self.send_buffer = []

    async def connect(self):
        self.connect_attempts += 1
        if self.connect_attempts <= self.connect_fails:
            raise ConnectionError("Failed to connect")
        self.is_connected = True
        return "mock-radio"

    async def disconnect(self):
        self.is_connected = False
        if self.on_disconnect:
            result = self.on_disconnect()
            if asyncio.iscoroutine(result):
                await result

    async def send(self, data):
        if not self.is_connected:
            raise ConnectionError("Not connected")
        self.send_buffer.append(data)

    def set_disconnect_callback(self, handler):
        self.on_disconnect = handler

    def set_reader(self, reader):
        self.on_receive = reader.handle_rx


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_initial_connection_failure_and_reconnect(mock_serial_connection):
    """
    Tests that the proxy attempts to reconnect if the initial connection fails.
    """
    mock_radio = MockRadio(connect_fails=1)
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5001,
    )

    proxy_task = asyncio.create_task(proxy.run())
    await asyncio.sleep(6)

    assert proxy._radio_connected
    assert mock_radio.connect_attempts == 2

    proxy_task.cancel()
    try:
        await proxy_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_disconnection_and_reconnection(mock_serial_connection):
    """
    Tests that the proxy reconnects after a disconnection.
    """
    mock_radio = MockRadio(connect_fails=0)
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5002,
    )

    proxy_task = asyncio.create_task(proxy.run())
    await asyncio.sleep(1)
    assert proxy._radio_connected

    await mock_radio.disconnect()
    assert not proxy._radio_connected

    await asyncio.sleep(6)
    assert proxy._radio_connected

    proxy_task.cancel()
    try:
        await proxy_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_backoff_delay(mock_serial_connection):
    """
    Tests that the backoff delay increases after each failed attempt.
    """
    mock_radio = MockRadio(connect_fails=2)
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5003,
    )

    start_time = asyncio.get_event_loop().time()
    proxy_task = asyncio.create_task(proxy.run())

    await asyncio.sleep(16)

    end_time = asyncio.get_event_loop().time()
    duration = end_time - start_time

    assert proxy._radio_connected
    assert mock_radio.connect_attempts == 3
    assert duration > 15

    proxy_task.cancel()
    try:
        await proxy_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_commands_serialized_through_queue(mock_serial_connection):
    """
    Tests that commands from multiple clients are serialized through the queue
    and arrive at the radio one at a time in FIFO order.
    """
    mock_radio = MockRadio(connect_fails=0)
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5010,
    )

    proxy_task = asyncio.create_task(proxy.run())
    await asyncio.sleep(1)
    assert proxy._radio_connected

    # Enqueue three commands
    payload_a = b"\x01"  # CMD_APPSTART
    payload_b = b"\x14"  # CMD_GET_BATTERY
    payload_c = b"\x05"  # CMD_GET_TIME

    await proxy._command_queue.put(payload_a)
    await proxy._command_queue.put(payload_b)
    await proxy._command_queue.put(payload_c)

    # Give the worker time to process
    await asyncio.sleep(0.5)

    assert mock_radio.send_buffer == [payload_a, payload_b, payload_c]

    proxy_task.cancel()
    try:
        await proxy_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_tcp_client_commands_go_through_queue(mock_serial_connection):
    """
    Tests that commands received from a TCP client are routed through the
    command queue rather than sent directly to the radio.
    """
    mock_radio = MockRadio(connect_fails=0)
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5011,
    )

    proxy_task = asyncio.create_task(proxy.run())
    await asyncio.sleep(1)
    assert proxy._radio_connected

    # Connect a TCP client and send a framed command
    reader, writer = await asyncio.open_connection("127.0.0.1", 5011)

    # Send a framed CMD_APPSTART: 0x3c + 2-byte size (1, little-endian) + payload
    payload = b"\x01"
    frame = b"\x3c" + len(payload).to_bytes(2, byteorder="little") + payload
    writer.write(frame)
    await writer.drain()

    # Give time for processing
    await asyncio.sleep(0.5)

    assert mock_radio.send_buffer == [payload]

    writer.close()
    await writer.wait_closed()

    proxy_task.cancel()
    try:
        await proxy_task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_commands_dropped_when_radio_disconnected(mock_serial_connection):
    """
    Tests that commands enqueued while the radio is disconnected are dropped
    with a warning rather than causing errors.
    """
    mock_radio = MockRadio(connect_fails=0)
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5012,
    )

    proxy_task = asyncio.create_task(proxy.run())
    await asyncio.sleep(1)
    assert proxy._radio_connected

    # Disconnect the radio and prevent reconnection
    mock_radio.connect_fails = 999
    await mock_radio.disconnect()
    assert not proxy._radio_connected

    # Record send count before enqueuing
    send_count_before = len(mock_radio.send_buffer)

    # Enqueue a command while disconnected
    await proxy._command_queue.put(b"\x01")

    # Give the worker time to process
    await asyncio.sleep(0.5)

    # Command should not appear in send buffer (it was dropped)
    assert len(mock_radio.send_buffer) == send_count_before

    proxy_task.cancel()
    try:
        await proxy_task
    except asyncio.CancelledError:
        pass
