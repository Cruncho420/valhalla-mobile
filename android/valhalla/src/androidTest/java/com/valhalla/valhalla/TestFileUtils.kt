package com.valhalla.valhalla

import android.content.Context
import com.squareup.moshi.Moshi
import com.squareup.moshi.kotlin.reflect.KotlinJsonAdapterFactory
import com.valhalla.config.models.ValhallaConfig
import com.valhalla.valhalla.files.ValhallaFile
import com.valhalla.valhalla.files.copyAssetFileToStorage
import java.io.File
import org.json.JSONObject

class TestFileUtils {

  companion object {
    fun getConfigPath(context: Context): String {
      val tilePath = ValhallaFile.copyAssetFileToStorage(context, "valhalla_tiles.tar")
      val config =
          context.assets.open("config.json").bufferedReader().use { JSONObject(it.readText()) }
      // Resolve the test sandbox at runtime; the fixture's package path is not portable.
      config.getJSONObject("mjolnir").put("tile_extract", tilePath)
      return File(context.filesDir, "config.json")
          .apply { writeText(config.toString()) }
          .absolutePath
    }

    fun getConfig(context: Context): ValhallaConfig {
      // The pinned models expose ValhallaConfig, but not the newer config builder.
      val moshi = Moshi.Builder().add(KotlinJsonAdapterFactory()).build()
      return requireNotNull(
          moshi
              .adapter(ValhallaConfig::class.java)
              .fromJson(File(getConfigPath(context)).readText()))
    }
  }
}
