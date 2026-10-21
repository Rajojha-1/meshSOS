package org.meshsos.transport

import android.bluetooth.BluetoothAdapter
import android.bluetooth.le.*
import android.content.Context
import android.os.ParcelUuid
import org.meshsos.protocol.MeshMessage
import org.meshsos.protocol.MeshProtocolDeserializer
import org.meshsos.relay.PriorityLevel
import java.util.*
import java.util.concurrent.ConcurrentHashMap

/**
 * Native Bluetooth Low Energy transport implementation.
 * Incorporates all verified Android BLE constraints from DECISIONS.md:
 * - Fits within 31-byte legacy advertising payload.
 * - Screen-off background scanning requires hardware-filtered scans (ScanFilter with ServiceUuid).
 * - Hardware scan buffering with setReportDelay() prevents Doze drops.
 */
class BleTransport(
    private val context: Context,
    private val bluetoothAdapter: BluetoothAdapter? = BluetoothAdapter.getDefaultAdapter()
) : Transport {

    override val transportName: String = "BLE_Legacy_Adv"
    private val serviceUuid = ParcelUuid(UUID.fromString("0000FD01-0000-1000-8000-00805F9B34FB"))

    private var advertiser: BluetoothLeAdvertiser? = null
    private var scanner: BluetoothLeScanner? = null
    private var receiveListener: ((MeshMessage, Long, Float) -> Unit)? = null

    private val neighboursMap = ConcurrentHashMap<Long, Float>()
    private var isScanning = false

    override fun start() {
        if (bluetoothAdapter == null || !bluetoothAdapter.isEnabled) return
        advertiser = bluetoothAdapter.bluetoothLeAdvertiser
        scanner = bluetoothAdapter.bluetoothLeScanner
        startFilteredScanning()
    }

    override fun stop() {
        stopScanning()
    }

    override fun send(message: MeshMessage, priority: PriorityLevel): Boolean {
        if (advertiser == null) return false
        val bytes = message.toBytes()
        if (bytes.size > 26) {
            // Log warning: payload exceeds legacy advertising space
            return false
        }

        val settings = AdvertiseSettings.Builder()
            .setAdvertiseMode(
                if (priority == PriorityLevel.SOS) AdvertiseSettings.ADVERTISE_MODE_LOW_LATENCY
                else AdvertiseSettings.ADVERTISE_MODE_BALANCED
            )
            .setTxPowerLevel(AdvertiseSettings.ADVERTISE_TX_POWER_HIGH)
            .setConnectable(false)
            .setTimeout(3000) // Brief burst
            .build()

        val data = AdvertiseData.Builder()
            .addServiceData(serviceUuid, bytes)
            .setIncludeDeviceName(false)
            .setIncludeTxPowerLevel(false)
            .build()

        advertiser?.startAdvertising(settings, data, object : AdvertiseCallback() {
            override fun onStartSuccess(settingsInEffect: AdvertiseSettings?) {}
            override fun onStartFailure(errorCode: Int) {}
        })
        return true
    }

    private fun startFilteredScanning() {
        if (isScanning || scanner == null) return

        // Critical verification requirement: Non-empty ScanFilter with Service UUID
        // Without this, Android completely blocks screen-off background scanning!
        val filter = ScanFilter.Builder()
            .setServiceData(serviceUuid, byteArrayOf()) // Match our Service UUID
            .build()

        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_POWER)
            .setReportDelay(5000L) // Hardware batching
            .build()

        try {
            scanner?.startScan(listOf(filter), settings, scanCallback)
            isScanning = true
        } catch (e: SecurityException) {
            // Lacking BLUETOOTH_SCAN runtime permission
        }
    }

    private fun stopScanning() {
        if (!isScanning) return
        try {
            scanner?.stopScan(scanCallback)
        } catch (e: Exception) {}
        isScanning = false
    }

    private val scanCallback = object : ScanCallback() {
        override fun onBatchScanResults(results: MutableList<ScanResult>?) {
            results?.forEach { processScanResult(it) }
        }

        override fun onScanResult(callbackType: Int, result: ScanResult?) {
            result?.let { processScanResult(it) }
        }

        override fun onScanFailed(errorCode: Int) {}
    }

    private fun processScanResult(result: ScanResult) {
        val record = result.scanRecord ?: return
        val serviceData = record.getServiceData(serviceUuid) ?: return
        try {
            val message = MeshProtocolDeserializer.deserialize(serviceData)
            val senderId = message.header.nodeId
            val rssi = result.rssi.toFloat()
            neighboursMap[senderId] = rssi
            receiveListener?.invoke(message, senderId, rssi)
        } catch (e: Exception) {
            // Corrupt or non-protocol packet
        }
    }

    override fun getNeighbours(): Map<Long, Float> = neighboursMap

    override fun getCapabilities(): Set<String> = setOf("BLE_BROADCAST", "LOW_POWER", "FILTERED_SCAN")

    override fun registerReceiveListener(listener: (message: MeshMessage, senderId: Long, rssi: Float) -> Unit) {
        this.receiveListener = listener
    }
}
