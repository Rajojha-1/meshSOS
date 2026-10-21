# MeshSOS: Architecture Decisions and [VERIFY] Log

This document records key technical decisions, protocol tradeoffs, and verification results for the MeshSOS emergency mesh network and silence inference system.

---

## 1. Verified Technical Constraints ([VERIFY] Log)

### 1.1 Legacy BLE Advertising 31-byte Payload Limit
* **Source**: Bluetooth Core Specification (v4.0 - v5.4), Generic Access Profile (GAP) AD structure specifications.
* **Findings**:
  - The maximum payload length of an Advertising Physical Data Unit (`ADV_IND` / `ADV_NONCONN_IND`) is **31 bytes**.
  - All advertisement data is wrapped in AD Structures: `[Length (1B)][Type (1B)][Value (NB)]`.
  - Typical flags structure (`0x01` Flags) consumes 3 bytes (`0x02, 0x01, 0x06`), leaving **28 bytes** available for custom Service Data or Manufacturer Specific Data.
  - If using 16-bit Service Data (`AD Type 0x16`), the overhead is 1B (length) + 1B (type) + 2B (UUID) = 4 bytes, leaving **27 bytes** for payload.
* **Resolution**:
  - MeshSOS `HEARTBEAT` header + payload is strictly designed to fit within **25 bytes** total:
    - Header: `version` (1B), `type` (1B), `node_id` (4B), `seq` (2B), `ttl` (1B), `timestamp` (4B) = 13 bytes.
    - Payload: `grid_cell` (4B), `battery` (1B), `flags` (1B), `neighbour_digest` (up to 3x 2B = 6B) = 12 bytes.
    - Total: 25 bytes. Combined with 4 bytes AD structure overhead (29 bytes) <= 31 bytes. Fits in a single legacy BLE advertising packet!

### 1.2 Bluetooth 5 Extended Advertising Support
* **Source**: Android Developers (`BluetoothAdapter.isLeExtendedAdvertisingSupported()`), Bluetooth 5.0 Core Specification.
* **Findings**:
  - Extended advertising allows payloads up to 254 bytes per packet (and up to 1,650 bytes via packet chaining) on secondary channels (0-36).
  - However, hardware support requires Bluetooth 5.0+ chipsets and OEM driver support. Many budget or older Android phones in disaster-prone regions only support legacy advertising.
* **Resolution**:
  - Heartbeats, Low-battery beacons, and compact relay messages use **legacy advertising** to ensure 100% interoperability across all hardware.
  - SOS and OK_NOW messages with full Ed25519 signatures can use extended advertising when supported, or Wi-Fi Direct / Nearby Connections / BLE GATT connection fallback.

### 1.3 Ed25519 Support in Android Keystore
* **Source**: Android 12 (API 31) Security Enhancements, Java Cryptography Architecture (JCA) Provider documentation.
* **Findings**:
  - Native Ed25519 key generation in `AndroidKeyStore` (`KeyPairGenerator.getInstance("Ed25519", "AndroidKeyStore")`) is only supported on **Android 12 (API 31) and higher**, and even then requires hardware KeyMint support.
  - Devices on API 30 and below or lacking KeyMint Ed25519 fail with `NoSuchAlgorithmException`.
* **Resolution**:
  - For universal Android support, use a standard software implementation (e.g. Bouncy Castle or libsodium / Tink) for Ed25519 signing and verification.
  - On devices with Android Keystore, protect the Ed25519 master private key by encrypting it at rest with an AndroidKeyStore-backed AES-256-GCM hardware key.

### 1.4 Android Background BLE Scanning Limits
* **Source**: Android Bluetooth Low Energy Scanning Guide, Android 8.0 - 14 Background Execution Limits.
* **Findings**:
  - Android heavily restricts background scanning: when the screen turns off, **unfiltered scans are completely blocked**.
  - Calling `startScan()` without a `ScanFilter` returns 0 results when screen is off.
  - Starting and stopping scans more than 5 times in 30 seconds triggers system throttling.
  - Android 14+ requires foreground services performing BLE operations to declare `android:foregroundServiceType="connectedDevice"`.
* **Resolution**:
  - MeshSOS runs inside an ongoing Foreground Service with `connectedDevice` type and `FOREGROUND_SERVICE` permission.
  - All scans use hardware-filtered `ScanFilter` targeting the MeshSOS Service UUID (`0xFD01` or standard 128-bit MeshSOS UUID).
  - Use `ScanSettings.SCAN_MODE_LOW_POWER` or `SCAN_MODE_BALANCED` with `setReportDelay(reportDelayMillis)` to buffer scan results in hardware/system memory, avoiding Doze mode drops.

### 1.5 Google Nearby Connections Radio Control and RSSI
* **Source**: Google Nearby Connections API documentation.
* **Findings**:
  - Nearby Connections manages the underlying radio transitions automatically (BLE for discovery, Wi-Fi Direct/LAN for high-speed transfers).
  - It abstracts away low-level BLE advertisement byte structures; you cannot directly inject raw custom BLE advertisement frames into Nearby Connections.
  - BLE RSSI is accessible during the discovery phase via `DiscoveredEndpointInfo`, but fine-grained radio duty cycle and transmission power control are internal to Google Play Services.
* **Resolution**:
  - Abstract transports behind a clean `Transport` interface.
  - Use raw BLE advertising/scanning for low-power presence (Heartbeat, Lowbat) and relay suppression.
  - Use Nearby Connections or Wi-Fi Direct as a high-bandwidth transport pipe for SOS payloads, signature exchange, and batched gateway uplinks.

### 1.6 Google Play SMS Permission Policy (`SEND_SMS`)
* **Source**: Google Play Console Policy Center: Permissions and APIs that Access Sensitive Information (SMS and Call Log permissions).
* **Findings**:
  - `SEND_SMS` is restricted. Apps must either be the default SMS handler OR qualify for strict exceptions.
  - **Exception category**: "Physical safety / emergency alert applications" is an officially recognized exception.
  - Requires submitting a declaration form, demonstrating emergency panic button triggering, and showing core functionality failure if SMS permission is denied.
* **Resolution**:
  - Default gateway uplink is TCP/HTTP over Cellular/Wi-Fi.
  - SMS is structured as an optional, explicitly enabled fallback gateway channel. If permission is granted under the emergency exception, gateway phones batch digests as 140-byte SMS messages to emergency control numbers.

---

## 2. Monorepo Architecture & Protocol Decisions

### 2.1 Directory Structure
- `/protocol`: Canonical specifications, schemas, binary serializer/deserializer implementations (Python and Kotlin), and shared test vector JSON files.
- `/simulator`: Discrete-event mesh network simulator (Python) implementing radio propagation, mobility, packet loss, relay logic, and fault injection (collapses, battery deaths).
- `/inference`: Python package implementing the Silence Detection Engine, contact graph maintenance, overdue tracking, group correlation, and explainable confidence scoring.
- `/backend`: FastAPI backend with SQLite storage, ingest endpoints for batched mesh packets, periodic inference execution, and GeoJSON map layer endpoints.
- `/dashboard`: Rescuer web interface featuring interactive Leaflet maps, SOS alerts, active mesh coverage, silent zones with evidence drawers, and contact graph inspection.
- `/android`: Android application source (Kotlin) implementing UI, Local Storage, Foreground Service, RelayCore, and Transport abstractions.

### 2.2 Wire Protocol Binary Packing
To fit legacy BLE advertising packets without fragmentation:
- **Endianness**: Big-endian (Network Byte Order) for all multi-byte integers.
- **Node ID**: 4 bytes (32-bit unsigned integer), pseudonymous rotating identifier.
- **Sequence Number**: 2 bytes (16-bit unsigned integer, range 0-65535 per node).
- **Timestamps**: 4 bytes (32-bit unsigned integer, Unix epoch seconds).
- **Grid Cell Coordinates**: Coarse location packed as 2 bytes latitude index + 2 bytes longitude index (~100m grid resolution).
- **Neighbour Digest**: 1 byte count $N$ ($0 \le N \le 3$) followed by $N \times 2$ bytes (truncated lower 16 bits of neighbour node IDs).
