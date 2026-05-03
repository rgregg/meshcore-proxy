# MeshCore Proxy - Agent Instructions

## Project Overview

This project builds a **TCP proxy** that enables a MeshCore companion radio connected via USB Serial, BLE, or an upstream TCP companion endpoint to be accessed by remote clients over a TCP network connection.

### Goals

- Connect to a MeshCore companion radio via USB Serial, Bluetooth Low Energy (BLE), or an upstream TCP companion endpoint
- Expose that radio to remote clients via TCP
- **Decode and log events** - Display human-readable logs of all events sent to/received from the radio
- Maintain compatibility with existing MeshCore clients:
  - **MeshCore-CLI** (Python CLI tool)
  - **Home Assistant MeshCore Integration** (meshcore-ha)
  - **MeshCore mobile/desktop apps** (via meshcore.js)

### Use Cases

- Run a MeshCore radio on a headless server (Raspberry Pi, etc.) and connect to it remotely
- Share a single MeshCore radio among multiple clients
- Enable Home Assistant to connect to a MeshCore radio that isn't directly attached to the HA host

## Technology Stack

**Language: Python**

Python is the correct choice for this project because:

1. **meshcore_py** - The official Python library already handles the MeshCore protocol and supports all three transport types (Serial, BLE, TCP)
2. **Ecosystem compatibility** - Home Assistant integration and meshcore-cli both use meshcore_py
3. **No protocol reimplementation needed** - The Python SDK abstracts the binary protocol

### Key Dependencies

| Package | Purpose | Repository |
|---------|---------|------------|
| `meshcore_py` | MeshCore protocol implementation | https://github.com/fdlamotte/meshcore_py |
| `asyncio` | Async I/O for handling multiple connections | stdlib |
| `bleak` | BLE support (used by meshcore_py) | pypi |
| `pyserial` | Serial port support | pypi |

## Architecture

```
┌─────────────────┐   USB/BLE/TCP    ┌──────────────────┐
│  MeshCore Radio │ ◄──────────────► │  meshcore-proxy  │
│  (Companion FW) │                  │                  │
└─────────────────┘                  │  ┌────────────┐  │
                                     │  │ TCP Server │  │
                                     │  └────────────┘  │
                                     └────────┬─────────┘
                                              │ TCP
                    ┌─────────────────────────┼─────────────────────────┐
                    │                         │                         │
                    ▼                         ▼                         ▼
           ┌──────────────┐          ┌──────────────┐          ┌──────────────┐
           │ MeshCore-CLI │          │ Home Asst.   │          │ MeshCore App │
           │   (Python)   │          │ Integration  │          │  (meshcore.js)│
           └──────────────┘          └──────────────┘          └──────────────┘
```

### Protocol Notes

- The MeshCore protocol is **event-driven**, not request-response
- Commands return `Event` objects with a type and payload
- The TCP protocol used by meshcore_py/meshcore.js is the native transport - our proxy should pass through the raw protocol, not translate it
- Default TCP port: **5000** (used by meshcore.js examples)

## MeshCore Submodule

The `MeshCore/` directory contains the official MeshCore firmware repository as a git submodule. This is for reference only - our proxy communicates with radios running the **companion radio firmware**, not the firmware source itself.

Key reference files in the submodule:
- `MeshCore/examples/companion_radio/` - Companion radio firmware source
- `MeshCore/src/Packet.h` - Packet type definitions
- `MeshCore/docs/faq.md` - Comprehensive FAQ with protocol details

## Development Guidelines

### Connection Types

Support three radio connection methods:

1. **USB Serial** - `/dev/ttyUSB0` or `/dev/ttyACM0` at 115200 baud
2. **BLE** - Connect via Bluetooth MAC address (default PIN: `123456`)
3. **Upstream TCP** - Connect to a MeshCore companion endpoint via `HOST[:PORT]` (default port: `5000`)

### Configuration

The proxy should accept configuration for:
- Local connection type (`serial`, `ble`, or `tcp`)
- Serial port path, BLE MAC address, or upstream TCP host/port
- TCP server bind address and port
- Event logging verbosity (off, summary, verbose/decoded)
- Optional: logging level, reconnection settings

### Event Logging

The proxy should decode and log MeshCore events for debugging and monitoring:

- **Log direction**: Indicate whether events are TO radio (from client) or FROM radio (to client)
- **Event types**: Decode event type enums to human-readable names (e.g., `MSG_SENT`, `CONTACT_RECEIVED`, `DEVICE_INFO`)
- **Payload summary**: Show relevant payload data (message content, contact names, timestamps, etc.)
- **Configurable verbosity**:
  - `--quiet`: No event logging, only errors
  - `--log-events`: Summary of each event (type, direction, basic info)
  - `--log-events-verbose`: Full decoded payload details
- **Output format**: Structured logs suitable for stdout (human readable) or JSON (for parsing)

### Error Handling

- Handle radio disconnection gracefully with auto-reconnect
- Handle client disconnection without affecting other clients or the radio connection
- Log connection events for debugging

### Testing

- Test with meshcore-cli's TCP connection mode
- Test with Home Assistant integration if available
- Verify multiple simultaneous TCP clients work correctly

## Related Projects

| Project | Description | URL |
|---------|-------------|-----|
| meshcore_py | Python library for MeshCore | https://github.com/fdlamotte/meshcore_py |
| meshcore-cli | CLI tool using meshcore_py | https://github.com/fdlamotte/meshcore-cli |
| meshcore-ha | Home Assistant integration | https://github.com/awolden/meshcore-ha |
| meshcore.js | JavaScript library | https://github.com/liamcottle/meshcore.js |
| MeshCore | Firmware source (submodule) | https://github.com/meshcore-dev/MeshCore |

## Commands Reference

### Useful meshcore-cli commands for testing

```bash
# Connect via serial
meshcore-cli --serial /dev/ttyUSB0

# Connect via BLE
meshcore-cli --ble 12:34:56:78:90:AB

# Proxy an upstream TCP companion endpoint
meshcore-proxy --tcp 192.168.1.103:5000

# Connect via TCP (what our proxy exposes)
meshcore-cli --tcp 192.168.1.100:5000
```

### Repeater/Room Server CLI (via USB serial console)

See: https://github.com/meshcore-dev/MeshCore/wiki/Repeater-&-Room-Server-CLI-Reference

## Packaging & Distribution

The proxy must be installable and runnable via multiple methods:

### pip / pipx

The package is configured in `pyproject.toml` with a console script entry point:

```bash
# Install with pip
pip install meshcore-proxy

# Install with pipx (isolated environment)
pipx install meshcore-proxy

# Run
meshcore-proxy --serial /dev/ttyUSB0
```

### Docker

A `Dockerfile` is provided for containerized deployment:

```bash
# Build
docker build -t meshcore-proxy .

# Run with USB serial (requires device passthrough)
docker run --device=/dev/ttyUSB0 -p 5000:5000 meshcore-proxy --serial /dev/ttyUSB0

# Run with BLE (requires host network and bluetooth access)
docker run --net=host --privileged meshcore-proxy --ble 12:34:56:78:90:AB

# Run with upstream TCP companion mode
docker run -p 5000:5000 meshcore-proxy --tcp 192.168.1.103:5000
```

**Docker considerations:**
- USB serial requires `--device` flag to pass through the serial port
- BLE requires `--net=host` and `--privileged` (or specific capabilities) for Bluetooth access
- Upstream TCP mode only needs network access to the companion endpoint
- Consider providing a `docker-compose.yml` for easier configuration

### PyPI Publishing

When ready for release:
1. Update version in `pyproject.toml` and `src/meshcore_proxy/__init__.py`
2. Build: `python -m build`
3. Upload: `twine upload dist/*`
