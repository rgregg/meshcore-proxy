"""Tests for CLI signal handling."""

import asyncio
import os
import signal
import sys
from unittest.mock import patch

import pytest

from meshcore_proxy.cli import parse_args, run_with_shutdown
from meshcore_proxy.proxy import EventLogLevel, MeshCoreProxy

# Test timing constants
STARTUP_DELAY = 0.2  # Time to wait for proxy to start before sending signal
SHUTDOWN_TIMEOUT = 5  # Maximum time to wait for graceful shutdown


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


def test_parse_args_accepts_tcp_endpoint(monkeypatch):
    """Test that upstream TCP endpoint options are parsed correctly."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "meshcore-proxy",
            "--tcp",
            "192.168.1.103:5000",
        ],
    )

    args = parse_args()

    assert args.radio_tcp_host == "192.168.1.103"
    assert args.radio_tcp_port == 5000
    assert args.serial is None
    assert args.ble is None


def test_parse_args_accepts_tcp_env(monkeypatch):
    """Test that upstream TCP environment variables satisfy connection selection."""
    monkeypatch.setenv("RADIO_TCP", "192.168.1.103:5001")
    monkeypatch.setattr(sys, "argv", ["meshcore-proxy"])

    args = parse_args()

    assert args.radio_tcp_host == "192.168.1.103"
    assert args.radio_tcp_port == 5001


def test_parse_args_accepts_tcp_host_without_port(monkeypatch):
    """Test that --tcp defaults to port 5000 when no port is provided."""
    monkeypatch.setattr(sys, "argv", ["meshcore-proxy", "--tcp", "192.168.1.103"])

    args = parse_args()

    assert args.radio_tcp_host == "192.168.1.103"
    assert args.radio_tcp_port == 5000


def test_parse_args_accepts_bracketed_ipv6_with_port(monkeypatch):
    """Test that bracketed IPv6 endpoints parse correctly with a port."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["meshcore-proxy", "--tcp", "[2001:db8::1]:5001"],
    )

    args = parse_args()

    assert args.radio_tcp_host == "2001:db8::1"
    assert args.radio_tcp_port == 5001


def test_parse_args_accepts_bare_ipv6_without_port(monkeypatch):
    """Test that bare IPv6 endpoints default to port 5000."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["meshcore-proxy", "--tcp", "2001:db8::1"],
    )

    args = parse_args()

    assert args.radio_tcp_host == "2001:db8::1"
    assert args.radio_tcp_port == 5000


def test_parse_args_rejects_malformed_multi_colon_endpoint(monkeypatch):
    """Test that malformed multi-colon endpoints are rejected."""
    monkeypatch.setattr(
        sys,
        "argv",
        ["meshcore-proxy", "--tcp", "2001:db8::zzzz:5001"],
    )

    with pytest.raises(SystemExit):
        parse_args()


def test_parse_args_requires_connection_mode(monkeypatch):
    """Test that one upstream connection mode is required."""
    for var in (
        "SERIAL_PORT",
        "BLE_ADDRESS",
        "RADIO_TCP",
        "RADIO_TCP_ADDRESS",
        "RADIO_TCP_HOST",
        "RADIO_TCP_PORT",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["meshcore-proxy"])

    with pytest.raises(SystemExit):
        parse_args()


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
        await asyncio.sleep(STARTUP_DELAY)
        # Send SIGTERM to trigger shutdown
        os.kill(os.getpid(), signal.SIGTERM)

    # Run both tasks
    signal_task = asyncio.create_task(run_and_signal())
    shutdown_task = asyncio.create_task(run_with_shutdown(proxy))

    # Wait for shutdown with a timeout
    try:
        await asyncio.wait_for(shutdown_task, timeout=SHUTDOWN_TIMEOUT)
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
        await asyncio.sleep(STARTUP_DELAY)
        os.kill(os.getpid(), signal.SIGINT)

    # Run both tasks
    signal_task = asyncio.create_task(run_and_signal())
    shutdown_task = asyncio.create_task(run_with_shutdown(proxy))

    # Wait for shutdown with a timeout
    try:
        await asyncio.wait_for(shutdown_task, timeout=SHUTDOWN_TIMEOUT)
    except asyncio.TimeoutError:
        pytest.fail("Shutdown did not complete within timeout")

    await signal_task

    # Verify proxy stopped cleanly
    assert not proxy._is_running
