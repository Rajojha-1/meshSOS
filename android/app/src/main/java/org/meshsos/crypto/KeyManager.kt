package org.meshsos.crypto

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature

/**
 * Manages Ed25519 asymmetric keys for signing MeshSOS emergency packets.
 * As verified in DECISIONS.md:
 * - API 31+ (Android 12) supports Ed25519 natively in AndroidKeyStore.
 * - Older APIs use a software Ed25519 provider (e.g. Bouncy Castle / Tink).
 */
class KeyManager(private val alias: String = "MeshSOS_Identity_Key") {

    private var fallbackKeyPair: KeyPair? = null

    fun getOrCreateKeyPair(): KeyPair {
        val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (ks.containsAlias(alias)) {
            val entry = ks.getEntry(alias, null) as? KeyStore.PrivateKeyEntry
            if (entry != null) {
                return KeyPair(entry.certificate.publicKey, entry.privateKey)
            }
        }

        // Generate new KeyPair
        return try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val kpg = KeyPairGenerator.getInstance("Ed25519", "AndroidKeyStore")
                val spec = KeyGenParameterSpec.Builder(alias, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
                    .setDigests(KeyProperties.DIGEST_NONE)
                    .build()
                kpg.initialize(spec)
                kpg.generateKeyPair()
            } else {
                generateSoftwareEd25519()
            }
        } catch (e: Exception) {
            // Hardware/Keystore fallback
            generateSoftwareEd25519()
        }
    }

    private fun generateSoftwareEd25519(): KeyPair {
        if (fallbackKeyPair == null) {
            val kpg = KeyPairGenerator.getInstance("Ed25519")
            fallbackKeyPair = kpg.generateKeyPair()
        }
        return fallbackKeyPair!!
    }

    fun signData(data: ByteArray): ByteArray {
        val kp = getOrCreateKeyPair()
        val signer = Signature.getInstance("Ed25519")
        signer.initSign(kp.private)
        signer.update(data)
        return signer.sign()
    }

    fun verifySignature(pubkey: ByteArray, data: ByteArray, signature: ByteArray): Boolean {
        return try {
            val kf = java.security.KeyFactory.getInstance("Ed25519")
            val pubSpec = java.security.spec.X509EncodedKeySpec(pubkey)
            val publicKey = kf.generatePublic(pubSpec)
            val verifier = Signature.getInstance("Ed25519")
            verifier.initVerify(publicKey)
            verifier.update(data)
            verifier.verify(signature)
        } catch (e: Exception) {
            false
        }
    }
}
