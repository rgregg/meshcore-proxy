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
