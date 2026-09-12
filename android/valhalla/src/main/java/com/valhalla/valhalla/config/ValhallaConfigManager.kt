package com.valhalla.valhalla.config

import android.content.Context
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import com.valhalla.config.models.ValhallaConfig
import com.valhalla.valhalla.files.ValhallaFile
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener

/**
 * Manages the Valhalla configuration file within the Android application's available filesystem.
 *
 * @param context The Android context used for file system access.
 * @param file The file handler for the valhalla.json configuration file. Defaults to
 *   "valhalla.json" in app storage.
 * @param moshi JSON serialization adapter. Defaults to a Moshi instance with Kotlin reflection
 *   support.
 * @see ValhallaConfig
 * @see ValhallaFile
 */
class ValhallaConfigManager(
    private val context: Context,
    private val file: ValhallaFile = ValhallaFile(context, "valhalla.json"),
    private val moshi: Moshi = Moshi.Builder().add(KotlinJsonAdapterFactory()).build()
) {

  fun writeConfig(config: ValhallaConfig) {
    val jsonAdapter = moshi.adapter(ValhallaConfig::class.java)
    val serialized = jsonAdapter.toJson(config)
    val json = JSONTokener(serialized).nextValue() as? JSONObject
    val defaults = json?.optJSONObject("loki")?.optJSONObject("service_defaults")
    if (defaults == null) {
      // Preserve partial/malformed caller config; the engine remains its validator.
      file.writeText(serialized)
      return
    }
    // Models 0.0.9 omit these fields, but pinned core e2f017b reads both without defaults.
    // Source: scripts/valhalla_build_config at e2f017b16080f49203de245a211b09efab09cf72.
    // Fill missing keys only: custom Moshi adapters may already serialize caller overrides.
    if (!defaults.has("mvt_min_zoom_road_class")) {
      defaults.put("mvt_min_zoom_road_class", JSONArray(listOf(7, 7, 8, 11, 11, 12, 13, 14)))
    }
    if (!defaults.has("mvt_cache_min_zoom")) {
      defaults.put("mvt_cache_min_zoom", 11)
    }

    // https://developer.android.com/training/data-storage/
    file.writeText(json.toString())
  }

  fun getAbsolutePath(): String {
    return file.absolutePath()
  }
}
