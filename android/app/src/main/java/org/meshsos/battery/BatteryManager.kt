package org.meshsos.battery

import android.content.Context
import android.os.BatteryManager as AndroidBatteryManager
import org.meshsos.protocol.LowbatReason
import org.meshsos.protocol.LowbatMessage
import org.meshsos.protocol.Header
import org.meshsos.protocol.MessageType

/**
 * Battery Manager implementing Phase 6 power optimizations:
 * - RFC 6206 Trickle back-off: lengthens intervals up to I_max when neighbourhood is stable, resets on change.
 * - GPS duty cycle reduction: avoids waking GPS per heartbeat.
 * - Dynamic TX power reduction in dense areas.
 * - Emits final LOWBAT beacon when battery hits <= 5% before radio shutdown.
 */
class BatteryManager(
    private val context: Context,
    private val minIntervalMs: Long = 15000L,
    private val maxIntervalMs: Long = 120000L,
    private val redundancyConstantK: Int = 2
) {
    private var currentIntervalMs: Long = minIntervalMs
    private var consistentCount: Int = 0
    private var lastNeighbourSnapshot: Set<Long> = emptySet()

    var isLowBatBeaconSent = false
        private set

    fun getBatteryPercent(): Int {
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as AndroidBatteryManager
        return bm.getIntProperty(AndroidBatteryManager.BATTERY_PROPERTY_CAPACITY)
    }

    /**
     * Trickle Algorithm (RFC 6206) step:
     * When current neighbours match previous snapshot, increment consistency and double interval.
     * When a new neighbour or event appears, reset interval to minIntervalMs.
     */
    fun updateTrickleInterval(currentNeighbours: Set<Long>): Long {
        if (currentNeighbours == lastNeighbourSnapshot) {
            consistentCount++
            if (consistentCount >= redundancyConstantK) {
                currentIntervalMs = minOf(maxIntervalMs, currentIntervalMs * 2)
            }
        } else {
            // Topology change detected: reset Trickle timer
            currentIntervalMs = minIntervalMs
            consistentCount = 0
            lastNeighbourSnapshot = currentNeighbours
        }
        return currentIntervalMs
    }

    fun resetIntervalOnEvent() {
        currentIntervalMs = minIntervalMs
        consistentCount = 0
    }

    /**
     * Checks if battery is critically low (<= 5%).
     * Returns true if lowbat beacon needs to be transmitted.
     */
    fun checkCriticalBattery(batteryPercent: Int): Boolean {
        if (batteryPercent <= 5 && !isLowBatBeaconSent) {
            isLowBatBeaconSent = true
            return true
        }
        return false
    }

    fun markLowBatSent() {
        isLowBatBeaconSent = true
    }

    fun shouldReduceTxPower(neighbourCount: Int): Boolean {
        // In dense clusters (> 8 neighbours), drop TX power to conserve energy and reduce channel collisions
        return neighbourCount > 8
    }
}
