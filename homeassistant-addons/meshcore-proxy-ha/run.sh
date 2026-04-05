#!/usr/bin/with-contenv bashio
set -e

CONNECTION_TYPE="$(bashio::config 'connection_type')"
TCP_PORT="$(bashio::config 'tcp_port')"
LOG_LEVEL="$(bashio::config 'log_level')"

ARGS=(--host 0.0.0.0 --port "${TCP_PORT}")

case "${CONNECTION_TYPE}" in
    serial)
        if ! bashio::config.has_value 'serial_device'; then
            bashio::exit.nok "connection_type is 'serial' but no serial_device was selected. Open the add-on Configuration tab and pick a device."
        fi
        SERIAL_DEVICE="$(bashio::config 'serial_device')"
        BAUD="$(bashio::config 'baud')"
        ARGS+=(--serial "${SERIAL_DEVICE}" --baud "${BAUD}")
        bashio::log.info "Proxying serial device: ${SERIAL_DEVICE} @ ${BAUD} baud"
        ;;
    ble)
        if ! bashio::config.has_value 'ble_address'; then
            bashio::exit.nok "connection_type is 'ble' but no ble_address was provided."
        fi
        BLE_ADDRESS="$(bashio::config 'ble_address')"
        ARGS+=(--ble "${BLE_ADDRESS}")
        if bashio::config.has_value 'ble_pin'; then
            ARGS+=(--ble-pin "$(bashio::config 'ble_pin')")
        fi
        bashio::log.info "Proxying BLE device: ${BLE_ADDRESS}"
        ;;
    *)
        bashio::exit.nok "Unknown connection_type: ${CONNECTION_TYPE}"
        ;;
esac

case "${LOG_LEVEL}" in
    debug)          ARGS+=(--debug) ;;
    events)         ARGS+=(--log-events) ;;
    events_verbose) ARGS+=(--log-events-verbose) ;;
    json)           ARGS+=(--json) ;;
    info)           : ;;
esac

# Report where the proxy is reachable from. The add-on's TCP port is mapped
# to the host at the port configured above, so clients on the LAN connect to
# the Home Assistant host IP on that port.
HOST_IP="$(bashio::network.ipv4_address 2>/dev/null || true)"
if [ -z "${HOST_IP}" ] || [ "${HOST_IP}" = "null" ]; then
    HOST_IP="<home-assistant-host-ip>"
else
    HOST_IP="${HOST_IP%/*}"
fi

bashio::log.info "----------------------------------------------------------"
bashio::log.info " MeshCore Proxy is starting"
bashio::log.info " Reachable at: tcp://${HOST_IP}:${TCP_PORT}"
bashio::log.info " (from any device on the same network as Home Assistant)"
bashio::log.info "----------------------------------------------------------"

exec meshcore-proxy "${ARGS[@]}"
