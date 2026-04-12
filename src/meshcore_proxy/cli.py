"""Command-line interface for MeshCore Proxy."""

import argparse
import asyncio
import functools
import logging
import os
import signal
import sys

from meshcore_proxy.proxy import EventLogLevel, MeshCoreProxy


def _parse_tcp_endpoint(value: str) -> tuple[str, int]:
    """Parse an upstream TCP endpoint in HOST or HOST:PORT form."""
    value = value.strip()
    if not value:
        raise ValueError("TCP endpoint cannot be empty")

    if ":" not in value:
        return value, 5000

    host, port_str = value.rsplit(":", 1)
    if not host:
        raise ValueError("TCP endpoint host cannot be empty")
    if not port_str:
        raise ValueError("TCP endpoint port cannot be empty")

    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(f"Invalid TCP port: {port_str}") from exc

    return host, port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TCP proxy for MeshCore companion radios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Connect via USB serial
  meshcore-proxy --serial /dev/ttyUSB0

  # Connect via BLE
  meshcore-proxy --ble 12:34:56:78:90:AB

  # Connect via upstream TCP
  meshcore-proxy --tcp 192.168.1.103:5000

  # With event logging
  meshcore-proxy --serial /dev/ttyUSB0 --log-level debug

  # Specify TCP port
  meshcore-proxy --serial /dev/ttyUSB0 --port 5000
        """,
    )

    # Connection type (mutually exclusive)
    # Allow env vars so docker-compose can configure without modifying command
    conn_group = parser.add_mutually_exclusive_group(
        required=not (
            os.environ.get("SERIAL_PORT")
            or os.environ.get("BLE_ADDRESS")
            or os.environ.get("RADIO_TCP")
            or os.environ.get("RADIO_TCP_ADDRESS")
            or os.environ.get("RADIO_TCP_HOST")
        ),
    )
    conn_group.add_argument(
        "--serial",
        metavar="PORT",
        default=os.environ.get("SERIAL_PORT"),
        help="Serial port path (e.g., /dev/ttyUSB0) [env: SERIAL_PORT]",
    )
    conn_group.add_argument(
        "--ble",
        metavar="MAC",
        default=os.environ.get("BLE_ADDRESS"),
        help="BLE device MAC address (e.g., 12:34:56:78:90:AB) [env: BLE_ADDRESS]",
    )
    conn_group.add_argument(
        "--tcp",
        metavar="HOST[:PORT]",
        default=(
            os.environ.get("RADIO_TCP")
            or os.environ.get("RADIO_TCP_ADDRESS")
            or os.environ.get("RADIO_TCP_HOST")
        ),
        help=(
            "Upstream MeshCore TCP endpoint (e.g., 192.168.1.103 or "
            "192.168.1.103:5000) [env: RADIO_TCP or RADIO_TCP_ADDRESS]"
        ),
    )

    # TCP server options
    parser.add_argument(
        "--host",
        default=os.environ.get("TCP_HOST", "0.0.0.0"),
        help="TCP server bind address (default: 0.0.0.0) [env: TCP_HOST]",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("TCP_PORT", "5000")),
        help="TCP server port (default: 5000) [env: TCP_PORT]",
    )

    # Serial options
    parser.add_argument(
        "--baud",
        type=int,
        default=int(os.environ.get("BAUD_RATE", "115200")),
        help="Serial baud rate (default: 115200) [env: BAUD_RATE]",
    )

    # BLE options
    parser.add_argument(
        "--ble-pin",
        default=os.environ.get("BLE_PIN", "123456"),
        help="BLE pairing PIN (default: 123456) [env: BLE_PIN]",
    )

    # Log level: controls both Python logging and event output verbosity
    # off=errors only, error=errors only, warning=warnings+errors,
    # info=normal (default), debug=debug logging, verbose=debug + full event details
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "info"),
        choices=["off", "error", "warning", "info", "debug", "verbose"],
        help="Log verbosity: off, error, warning, info, debug, verbose (default: info) [env: LOG_LEVEL]",
    )

    # Output format
    parser.add_argument(
        "--json",
        action="store_true",
        default=os.environ.get("LOG_JSON", "").lower() in ("1", "true", "yes"),
        help="Output event logs as JSON (for parsing) [env: LOG_JSON]",
    )

    # Channel virtualization
    parser.add_argument(
        "--virtualize-channels",
        action="store_true",
        default=os.environ.get("VIRTUALIZE_CHANNELS", "").lower() in ("1", "true", "yes"),
        help="Virtualize channel slots for multi-client isolation [env: VIRTUALIZE_CHANNELS]",
    )

    args = parser.parse_args()

    args.radio_tcp_host = None
    args.radio_tcp_port = None
    if args.tcp:
        tcp_value = args.tcp
        # Preserve compatibility with older HOST/PORT env vars if only host is set.
        if ":" not in tcp_value and os.environ.get("RADIO_TCP_PORT"):
            tcp_value = f"{tcp_value}:{os.environ['RADIO_TCP_PORT']}"
        try:
            args.radio_tcp_host, args.radio_tcp_port = _parse_tcp_endpoint(tcp_value)
        except ValueError as exc:
            parser.error(str(exc))

    return args


async def run_with_shutdown(proxy: MeshCoreProxy) -> None:
    """Run the proxy with proper signal handling for graceful shutdown."""
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def signal_handler(sig):
        """Handle shutdown signals."""
        signame = signal.Signals(sig).name
        logging.info(f"Received {signame}, shutting down gracefully...")
        shutdown_event.set()

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, functools.partial(signal_handler, sig))

    # Create the proxy task
    proxy_task = asyncio.create_task(proxy.run())

    # Wait for either the proxy to complete or a shutdown signal
    shutdown_task = asyncio.create_task(shutdown_event.wait())
    done, pending = await asyncio.wait(
        [proxy_task, shutdown_task],
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If shutdown was signaled, cancel the proxy task
    if shutdown_task in done:
        proxy_task.cancel()
        try:
            await proxy_task
        except asyncio.CancelledError:
            pass

    # Cancel any remaining tasks
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def main() -> int:
    """Main entry point."""
    args = parse_args()

    # Map unified log level to Python logging and event verbosity
    level = args.log_level.lower()
    log_level_map = {
        "off": logging.ERROR,
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG,
        "verbose": logging.DEBUG,
    }
    event_level_map = {
        "off": EventLogLevel.OFF,
        "error": EventLogLevel.OFF,
        "warning": EventLogLevel.OFF,
        "info": EventLogLevel.SUMMARY,
        "debug": EventLogLevel.SUMMARY,
        "verbose": EventLogLevel.VERBOSE,
    }
    log_level = log_level_map[level]
    event_log_level = event_level_map[level]

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Create proxy
    proxy = MeshCoreProxy(
        serial_port=args.serial,
        ble_address=args.ble,
        radio_tcp_host=args.radio_tcp_host,
        radio_tcp_port=args.radio_tcp_port,
        baud_rate=args.baud,
        ble_pin=args.ble_pin,
        tcp_host=args.host,
        tcp_port=args.port,
        event_log_level=event_log_level,
        event_log_json=args.json,
        virtualize_channels=args.virtualize_channels,
    )

    # Run with signal handling
    try:
        asyncio.run(run_with_shutdown(proxy))
        return 0
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
