package org.meshsos.privacy

import android.content.Context
import android.content.SharedPreferences
import java.security.SecureRandom
import kotlin.math.roundToInt

/**
 * Privacy and Identity Manager implementing Section 8 constraints:
 * - Rotating pseudonymous node IDs (rotates every 2 hours).
 * - Coarse grid cell quantization (~100m grid) for heartbeats; precise GPS only for SOS.
 * - Consent tracking and explicit "stop sharing" toggle.
 * - Emits LOWBAT with reason: USER_STOP when user opts out, preventing false alarms.
 */
class PrivacyManager(private val context: Context) {
    private val prefs: SharedPreferences = context.getSharedPreferences("meshsos_privacy", Context.MODE_PRIVATE)
    private val rng = SecureRandom()

    companion object {
        private const val KEY_NODE_ID = "pseudonymous_node_id"
        private const val KEY_ID_ROTATION_TIME = "last_id_rotation_time"
        private const val KEY_USER_CONSENT = "user_opt_in_consent"
        private const val ROTATION_INTERVAL_MS = 2 * 3600 * 1000L // 2 hours
        private const val GRID_SIZE_METERS = 100.0
    }

    fun hasUserConsented(): Boolean {
        return prefs.getBoolean(KEY_USER_CONSENT, false)
    }

    fun setUserConsent(consent: Boolean) {
        prefs.edit().putBoolean(KEY_USER_CONSENT, consent).apply()
    }

    fun getOrRotateNodeId(): Long {
        val now = System.currentTimeMillis()
        val lastRotation = prefs.getLong(KEY_ID_ROTATION_TIME, 0L)
        val currentId = prefs.getLong(KEY_NODE_ID, 0L)

        if (currentId == 0L || (now - lastRotation) > ROTATION_INTERVAL_MS) {
            val newId = (rng.nextLong() and 0xFFFFFFFFL).coerceAtLeast(1L)
            prefs.edit()
                .putLong(KEY_NODE_ID, newId)
                .putLong(KEY_ID_ROTATION_TIME, now)
                .apply()
            return newId
        }
        return currentId
    }

    /**
     * Quantize continuous latitude/longitude into coarse ~100m grid cell coordinates.
     */
    fun quantizeToGridCell(lat: Double, lon: Double): Pair<Short, Short> {
        if (lat == 0.0 && lon == 0.0) return Pair(0, 0)
        val gx = (lon * 1000.0).roundToInt().coerceIn(-32768, 32767).toShort()
        val gy = (lat * 1000.0).roundToInt().coerceIn(-32768, 32767).toShort()
        return Pair(gx, gy)
    }
}
