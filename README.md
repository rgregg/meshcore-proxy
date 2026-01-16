# MeshCore Proxy

A TCP proxy that enables remote access to a locally-connected MeshCore companion radio.

## Overview

MeshCore Proxy connects to a MeshCore radio via USB Serial or Bluetooth LE and exposes it over TCP, allowing remote clients to interact with the radio as if it were directly connected.

```
┌─────────────────┐     USB/BLE      ┌──────────────────┐
│  MeshCore Radio │ ◄──────────────► │  meshcore-proxy  │
│  (Companion FW) │                  │                  │
└─────────────────┘                  │  TCP :5000       │
                                     └────────┬─────────┘
                                              │
              ┌───────────────────────────────┼───────────────────────────────┐
              │                               │                               │
              ▼                               ▼                               ▼
     ┌──────────────┐                ┌──────────────┐                ┌──────────────┐
     │ MeshCore-CLI │                │ Home Assistant│                │ MeshCore App │
     └──────────────┘                └──────────────┘                └──────────────┘
```

**Supported clients:**
- [MeshCore-CLI](https://github.com/fdlamotte/meshcore-cli)
- [Home Assistant MeshCore Integration](https://github.com/awolden/meshcore-ha)
- [MeshCore Apps](https://meshcore.co.uk/apps.html) (via TCP)

## Installation

### From PyPI

```bash
pip install meshcore-proxy
```

### Using pipx (isolated environment)

```bash
pipx install meshcore-proxy
```

### From source

```bash
git clone --recursive https://github.com/rgregg/meshcore-proxy.git
cd meshcore-proxy
pip install -e .
```

### Docker

```bash
docker build -t meshcore-proxy .
```

## Quick Start

### USB Serial

```bash
# Linux
meshcore-proxy --serial /dev/ttyUSB0

# macOS
meshcore-proxy --serial /dev/cu.usbmodem101

# Windows
meshcore-proxy --serial COM3
```

### Bluetooth LE

```bash
# Linux/Windows - use MAC address
meshcore-proxy --ble 12:34:56:78:90:AB

# macOS - use UUID or device name (MAC addresses not supported)
meshcore-proxy --ble 7921236E-065C-0C7B-C04D-7F60E849DC47
meshcore-proxy --ble MeshCore-07BA3987
```

### Connect a Client

Once the proxy is running, connect clients to `localhost:5000`:

```bash
meshcore-cli --tcp localhost:5000
```

## Usage

```
meshcore-proxy [OPTIONS]

Connection (one required):
  --serial PORT     Serial port path (e.g., /dev/ttyUSB0)
  --ble ADDR        BLE device address (see platform notes below)

Server options:
  --host ADDR       TCP bind address (default: 0.0.0.0)
  --port PORT       TCP port (default: 5000)

Serial options:
  --baud RATE       Serial baud rate (default: 115200)

BLE options:
  --ble-pin PIN     BLE pairing PIN (default: 123456)

Logging options:
  --quiet                  Suppress non-error output
  --log-events             Log event summaries
  --log-events-verbose     Log full event details with hex payloads
  --json                   Output event logs as JSON
  --debug                  Enable debug logging
```

### BLE Address Format by Platform

| Platform | Address Format | Example |
|----------|---------------|---------|
| **Linux** | MAC address | `12:34:56:78:90:AB` |
| **Windows** | MAC address | `12:34:56:78:90:AB` |
| **macOS** | UUID or device name | `7921236E-065C-0C7B-C04D-7F60E849DC47` or `MeshCore-07BA3987` |

macOS uses Core Bluetooth which does not expose MAC addresses. Use the device UUID (found via BLE scanning tools) or the device name broadcast by the radio.

## Event Logging

The proxy decodes and displays MeshCore protocol events for debugging and monitoring.

### Summary Mode

```bash
meshcore-proxy --serial /dev/ttyUSB0 --log-events
```

```
-> CMD_APPSTART
<- SELF_INFO
-> CMD_DEVICE_QUERY
<- DEVICE_INFO
-> CMD_GET_CONTACTS
<- CONTACT_START
<- CONTACT_END
```

### Verbose Mode

```bash
meshcore-proxy --serial /dev/ttyUSB0 --log-events-verbose
```

```
-> CMD_APPSTART [13 bytes]: 01032020202020206d63636c69
<- SELF_INFO [66 bytes]: 05011616bfac8cd412f2e401...
-> CMD_GET_BATTERY [1 bytes]: 14
<- BATTERY [11 bytes]: 0cfc101600000064000000
```

### JSON Mode

```bash
meshcore-proxy --serial /dev/ttyUSB0 --log-events --json
```

```json
{"direction": "TO_RADIO", "packet_type": "CMD_APPSTART", "packet_type_raw": 1}
{"direction": "FROM_RADIO", "packet_type": "SELF_INFO", "packet_type_raw": 5}
```

## Running with Docker

### USB Serial

```bash
# Linux
docker run -d \
  --name meshcore-proxy \
  --device=/dev/ttyUSB0 \
  -p 5000:5000 \
  meshcore-proxy \
  --serial /dev/ttyUSB0

# macOS
docker run -d \
  --name meshcore-proxy \
  --device=/dev/cu.usbmodem101 \
  -p 5000:5000 \
  meshcore-proxy \
  --serial /dev/cu.usbmodem101
```

### Bluetooth LE (Linux only)

BLE in Docker requires Linux with BlueZ. It does not work on macOS or Windows Docker.

```bash
# Requires host network and privileges for Bluetooth access
docker run -d \
  --name meshcore-proxy \
  --net=host \
  --privileged \
  -v /var/run/dbus:/var/run/dbus:ro \
  meshcore-proxy \
  --ble 12:34:56:78:90:AB
```

### Docker Compose

```bash
# USB Serial
docker compose --profile serial up -d

# BLE (set address in environment)
BLE_ADDRESS=12:34:56:78:90:AB docker compose --profile ble up -d
```

### View Logs

```bash
docker logs -f meshcore-proxy
```

## Running from Source

```bash
# Clone with submodules
git clone --recursive https://github.com/rgregg/meshcore-proxy.git
cd meshcore-proxy

# Or initialize submodules if already cloned
git submodule update --init --recursive

# Run directly
PYTHONPATH=src:meshcore_py/src python3 -m meshcore_proxy.cli \
  --serial /dev/ttyUSB0 --log-events
```

## Configuration Examples

### Home Assistant Integration

Configure the [MeshCore Home Assistant integration](https://github.com/awolden/meshcore-ha) to connect via TCP:

- **Host:** IP address of machine running the proxy
- **Port:** 5000 (or your custom port)

### Running as a System Service (Linux)

Create `/etc/systemd/system/meshcore-proxy.service`:

```ini
[Unit]
Description=MeshCore Proxy
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/meshcore-proxy --serial /dev/ttyUSB0 --log-events
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable meshcore-proxy
sudo systemctl start meshcore-proxy
```

## Troubleshooting

### Permission denied on serial port (Linux)

Add your user to the `dialout` group:

```bash
sudo usermod -aG dialout $USER
# Log out and back in for changes to take effect
```

### Port already in use

Use a different port:

```bash
meshcore-proxy --serial /dev/ttyUSB0 --port 5001
```

### BLE connection fails

- Ensure Bluetooth is enabled and the device is in range
- **macOS**: Use UUID or device name, not MAC address
- **Linux**: You may need `sudo` or add user to `bluetooth` group
- **PIN-protected devices**: Pair at the OS level first (System Preferences on macOS, `bluetoothctl` on Linux), then the proxy will use the existing pairing

### Radio not responding

- Check that the radio is running companion firmware (not repeater/room server)
- Verify the serial port/BLE address is correct
- Try power cycling the radio

## Requirements

- Python 3.10+
- MeshCore companion radio with USB or BLE firmware

## License

MIT
