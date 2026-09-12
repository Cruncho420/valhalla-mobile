package com.valhalla.valhalla

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.squareup.moshi.JsonAdapter
import com.squareup.moshi.JsonReader
import com.squareup.moshi.JsonWriter
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import com.valhalla.config.models.ValhallaConfig
import com.valhalla.valhalla.config.ValhallaConfigManager
import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ValhallaConfigCompatibilityTest {
  private val context = InstrumentationRegistry.getInstrumentation().targetContext
  private val moshi = Moshi.Builder().add(KotlinJsonAdapterFactory()).build()

  private fun fixtureDefaults(): JSONObject =
      context.assets.open("config.json").bufferedReader().use {
        JSONObject(it.readText()).getJSONObject("loki").getJSONObject("service_defaults")
      }

  private fun write(config: ValhallaConfig, customMoshi: Moshi = moshi): JSONObject {
    val manager = ValhallaConfigManager(context, moshi = customMoshi)
    manager.writeConfig(config)
    return JSONObject(File(manager.getAbsolutePath()).readText())
  }

  @Test
  fun typedConfigRestoresRequiredPinnedCoreFieldsAndKeepsRuntimeTilePath() {
    val config = TestFileUtils.getConfig(context)
    val output = write(config)
    val defaults = output.getJSONObject("loki").getJSONObject("service_defaults")
    val expected = fixtureDefaults()
    assertEquals(
        expected.getJSONArray("mvt_min_zoom_road_class").toString(),
        defaults.getJSONArray("mvt_min_zoom_road_class").toString())
    assertEquals(expected.getInt("mvt_cache_min_zoom"), defaults.getInt("mvt_cache_min_zoom"))
    assertEquals(
        File(context.filesDir, "valhalla_tiles.tar").absolutePath,
        output.getJSONObject("mjolnir").getString("tile_extract"))
  }

  private fun customAdapter(json: JSONObject): Moshi {
    val delegate = moshi.adapter(ValhallaConfig::class.java)
    val value = moshi.adapter(Any::class.java).fromJson(json.toString())
    val adapter =
        object : JsonAdapter<ValhallaConfig>() {
          override fun fromJson(reader: JsonReader): ValhallaConfig? = delegate.fromJson(reader)

          override fun toJson(writer: JsonWriter, config: ValhallaConfig?) {
            val previous = writer.serializeNulls
            writer.serializeNulls = true
            try {
              writer.jsonValue(value)
            } finally {
              writer.serializeNulls = previous
            }
          }
        }
    return Moshi.Builder().add(ValhallaConfig::class.java, adapter).build()
  }

  @Test
  fun preservesEverySerializedCustomAdapterFieldIncludingCoreOverrides() {
    val config = TestFileUtils.getConfig(context)
    val custom = JSONObject(moshi.adapter(ValhallaConfig::class.java).toJson(config))
    val defaults = custom.getJSONObject("loki").getJSONObject("service_defaults")
    defaults.put("mvt_min_zoom_road_class", JSONArray(listOf(6, 7, 8, 9, 10, 11, 12, 13)))
    defaults.put("mvt_cache_min_zoom", 9)
    custom.put("caller_extension", JSONObject().put("enabled", true).put("label", "custom"))
    val output = write(config, customAdapter(custom))
    val untyped = moshi.adapter(Any::class.java)
    assertEquals(untyped.fromJson(custom.toString()), untyped.fromJson(output.toString()))
  }

  @Test
  fun preservesMissingAndNonobjectBranchesForNativeValidation() {
    val config = TestFileUtils.getConfig(context)
    val serialized = moshi.adapter(ValhallaConfig::class.java).toJson(config)
    val variants =
        listOf(
            JSONObject(serialized).apply { remove("loki") },
            JSONObject(serialized).apply { put("loki", JSONObject.NULL) },
            JSONObject(serialized).apply { put("loki", "invalid") },
            JSONObject(serialized).apply { getJSONObject("loki").remove("service_defaults") },
            JSONObject(serialized).apply {
              getJSONObject("loki").put("service_defaults", JSONObject.NULL)
            },
            JSONObject(serialized).apply {
              getJSONObject("loki").put("service_defaults", "invalid")
            },
        )
    val untyped = moshi.adapter(Any::class.java)
    for (custom in variants) {
      val output = write(config, customAdapter(custom))
      assertEquals(untyped.fromJson(custom.toString()), untyped.fromJson(output.toString()))
    }
  }

  @Test
  fun fillsEachMissingCoreFieldWithoutOverwritingTheOther() {
    val config = TestFileUtils.getConfig(context)
    val expected = fixtureDefaults()
    for (missing in listOf("mvt_min_zoom_road_class", "mvt_cache_min_zoom")) {
      val custom = JSONObject(moshi.adapter(ValhallaConfig::class.java).toJson(config))
      val defaults = custom.getJSONObject("loki").getJSONObject("service_defaults")
      defaults.put("mvt_min_zoom_road_class", JSONArray(listOf(6, 7, 8, 9, 10, 11, 12, 13)))
      defaults.put("mvt_cache_min_zoom", 9)
      defaults.remove(missing)
      val output = write(config, customAdapter(custom))
      defaults.put(missing, expected.get(missing))
      val untyped = moshi.adapter(Any::class.java)
      assertEquals(untyped.fromJson(custom.toString()), untyped.fromJson(output.toString()))
    }
  }
}
