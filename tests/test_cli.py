"""Tests for CLI signal handling."""

import asyncio
import os
import signal
from unittest.mock import patch

import pytest

from meshcore_proxy.cli import run_with_shutdown
from meshcore_proxy.proxy import EventLogLevel, MeshCoreProxy


class MockRadio:
    """Mock radio connection for testing."""

    def __init__(self):
        self.is_connected = False
        self.on_disconnect = None
        self.on_receive = None

    async def connect(self):
        self.is_connected = True
        return "mock-radio"

    async def disconnect(self):
        self.is_connected = False
        if self.on_disconnect:
            result = self.on_disconnect()
            if asyncio.iscoroutine(result):
                await result

    async def send(self, data):
        pass

    def set_disconnect_handler(self, handler):
        self.on_disconnect = handler

    def set_reader(self, reader):
        self.on_receive = reader.handle_rx


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_sigterm_triggers_graceful_shutdown(mock_serial_connection):
    """Test that SIGTERM signal triggers graceful shutdown."""
    mock_radio = MockRadio()
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5010,
    )

    # Start the proxy with signal handling
    async def run_and_signal():
        """Run proxy and send SIGTERM after a short delay."""
        # Give the proxy time to start
        await asyncio.sleep(1)
        # Send SIGTERM to trigger shutdown
        os.kill(os.getpid(), signal.SIGTERM)

    # Run both tasks
    signal_task = asyncio.create_task(run_and_signal())
    shutdown_task = asyncio.create_task(run_with_shutdown(proxy))

    # Wait for shutdown with a timeout
    try:
        await asyncio.wait_for(shutdown_task, timeout=5)
    except asyncio.TimeoutError:
        pytest.fail("Shutdown did not complete within timeout")

    await signal_task

    # Verify proxy stopped cleanly
    assert not proxy._is_running


@pytest.mark.asyncio
@patch("meshcore_proxy.proxy.SerialConnection")
async def test_sigint_triggers_graceful_shutdown(mock_serial_connection):
    """Test that SIGINT signal (Ctrl+C) triggers graceful shutdown."""
    mock_radio = MockRadio()
    mock_serial_connection.return_value = mock_radio

    proxy = MeshCoreProxy(
        serial_port="/dev/ttyUSB0",
        event_log_level=EventLogLevel.OFF,
        tcp_port=5011,
    )

    # Start the proxy with signal handling
    async def run_and_signal():
        """Run proxy and send SIGINT after a short delay."""
        await asyncio.sleep(1)
        os.kill(os.getpid(), signal.SIGINT)

    # Run both tasks
    signal_task = asyncio.create_task(run_and_signal())
    shutdown_task = asyncio.create_task(run_with_shutdown(proxy))

    # Wait for shutdown with a timeout
    try:
        await asyncio.wait_for(shutdown_task, timeout=5)
    except asyncio.TimeoutError:
        pytest.fail("Shutdown did not complete within timeout")

    await signal_task

    # Verify proxy stopped cleanly
    assert not proxy._is_running
