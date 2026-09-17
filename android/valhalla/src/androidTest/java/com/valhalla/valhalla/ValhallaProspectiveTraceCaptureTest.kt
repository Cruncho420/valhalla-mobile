// PURPOSE: Preserve frozen prospective requests and every raw native return.
// RESPONSIBILITY: Capture only, without response-derived labels or consumer acceptance.
// DEPENDENCIES: Instrumentation, the raw wrapper, and an explicitly staged source graph.
// CONSUMERS: Opt-in artifact-only native evidence workflow.
package com.valhalla.valhalla

import android.os.Process
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import java.security.MessageDigest
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ValhallaProspectiveTraceCaptureTest {
  private fun requireCapture(value: Boolean) {
    check(value) { "Prospective capture incomplete" }
  }

  private fun digest(bytes: ByteArray): String =
      MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

  private fun read(file: File, limit: Int): ByteArray {
    // filesDir is reached through /data/user/0, which canonicalizes to /data/data, so an absolute
    // path can never equal its canonical form here. Inside an already-canonical root, a file that
    // resolves to itself is the same refusal of symlinks and traversal without that false failure.
    requireCapture(file.isFile && file.canonicalFile == file && file.length() <= limit)
    val bytes = file.inputStream().use { stream ->
      val output = java.io.ByteArrayOutputStream()
      val buffer = ByteArray(8192)
      while (true) {
        val count = stream.read(buffer)
        if (count < 0) break
        requireCapture(output.size() + count <= limit)
        output.write(buffer, 0, count)
      }
      output.toByteArray()
    }
    requireCapture(bytes.size <= limit)
    return bytes
  }

  @Test
  fun frozenProspectiveCapture() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    val root = File(context.filesDir, "prospective-input").canonicalFile
    val directory = File(context.filesDir, "prospective-capture").canonicalFile
    requireCapture(!directory.exists())
    val expectedStage = InstrumentationRegistry.getArguments().getString("prospectiveStageSha256")
    requireCapture(expectedStage != null)
    val stage = read(File(root, "stage.json"), 262144)
    requireCapture(digest(stage) == expectedStage)
    val plan = JSONObject(stage.toString(Charsets.UTF_8))
    val rows = plan.getJSONArray("rows")
    requireCapture(rows.length() == 283)
    val graph = read(File(root, "graph.tar"), 4194304)
    requireCapture(digest(graph) == "c0957c92bb71833ed3763e4b2c42a536cb28f2bcb69c991264532485edee75d4")
    val requests = (0 until rows.length()).map { index ->
      val row = rows.getJSONObject(index)
      val action = row.getString("action")
      requireCapture(action == "trace_attributes" || action == "trace_route")
      // Imported recordings are exact post-resampling requests, so unlike the
      // reviewed diagnostic cohort they have no invented "original" twin.
      val kind =
          if (action == "trace_route" || row.has("request")) "request" else "diagnostic"
      val item = row.getJSONObject(kind)
      val name = "%03d.%s.json".format(index, kind)
      requireCapture(item.getString("file") == name)
      read(File(root, name), 8192).also {
        requireCapture(digest(it) == item.getString("sha256"))
        requireCapture(it.toString(Charsets.UTF_8).toByteArray(Charsets.UTF_8).contentEquals(it))
      }.let { action to it }
    }
    val template = read(File(root, "config-template.json"), 65536)
    requireCapture(digest(template) == plan.getString("configTemplateSha256"))
    val config = JSONObject(template.toString(Charsets.UTF_8))
    config.getJSONObject("mjolnir").put("tile_extract", File(root, "graph.tar").absolutePath)
    val configBytes = config.toString().toByteArray(Charsets.UTF_8)
    requireCapture(directory.mkdir())
    fun save(bytes: ByteArray, name: String) {
      val file = File(directory, name)
      requireCapture(file.createNewFile())
      file.writeBytes(bytes)
    }
    save(stage, "stage.json")
    save(configBytes, "config.json")
    // Read the actual loaded ABI library, rather than the device's list of supported ABIs.
    val library = File(context.applicationInfo.nativeLibraryDir, "libvalhalla-wrapper.so")
    val header = ByteArray(20)
    library.inputStream().use { requireCapture(it.read(header) == header.size) }
    requireCapture(header.size == 20 && header[0] == 0x7f.toByte() && header[1] == 'E'.code.toByte())
    requireCapture(header[4] == 2.toByte() && header[5] == 1.toByte() && Process.is64Bit())
    requireCapture(header[18] == 62.toByte() && header[19] == 0.toByte())
    requireCapture(library.length() <= 256L * 1048576)
    val nativeDigest = MessageDigest.getInstance("SHA-256")
    library.inputStream().use { stream ->
      val buffer = ByteArray(65536)
      var totalBytes = 0L
      while (true) {
        val count = stream.read(buffer)
        if (count < 0) break
        totalBytes += count
        requireCapture(totalBytes <= 256L * 1048576)
        nativeDigest.update(buffer, 0, count)
      }
    }
    val nativeSha = nativeDigest.digest().joinToString("") { "%02x".format(it) }
    val captured = JSONArray()
    var total = 0L
    // Set only once the plan ran to its end with every native return inside the declared bounds.
    var complete = false
    ValhallaRaw(File(directory, "config.json").absolutePath).use { actor ->
      for ((index, entry) in requests.withIndex()) {
        val (action, request) = entry
        save(request, "%03d.request.json".format(index))
        val row = JSONObject().put("identity", rows.getJSONObject(index))
            .put("requestSha256", digest(request)).put("requestBytes", request.size)
        // A native refusal is evidence, not an excuse to drop the row: record what actually
        // happened rather than asserting a return that never came back.
        var returned = false
        var response = ByteArray(0)
        try {
          response = (if (action == "trace_attributes") actor.traceAttributes(request.toString(Charsets.UTF_8))
                      else actor.traceRoute(request.toString(Charsets.UTF_8))).toByteArray(Charsets.UTF_8)
          returned = true
        } catch (error: Exception) {
          row.put("failureClass", error.javaClass.name)
        }
        // Write the bounded bytes before failing, so an oversize return is preserved and marked
        // rather than silently truncated into an apparently ordinary response.
        val oversize = response.size > 1048576
        val stored = if (oversize) response.copyOf(1048576) else response
        total += stored.size
        save(stored, "%03d.response.raw".format(index))
        row.put("returned", returned)
            .put("responseSha256", digest(stored)).put("responseBytes", stored.size)
        if (oversize) row.put("oversize", true).put("nativeResponseBytes", response.size)
        captured.put(row)
        if (oversize || total > 120L * 1048576) break
        if (index == requests.size - 1) complete = true
      }
    }
    val receipt = JSONObject().put("version", 1).put("complete", complete).put("platform", "android")
        .put("abi", "x86_64").put("nativeLibrarySha256", nativeSha)
        .put("stageSha256", digest(stage)).put("extractSha256", digest(graph))
        .put("configSha256", digest(configBytes)).put("rows", captured)
    save(receipt.toString().toByteArray(Charsets.UTF_8), "receipt.json")
    // Fail loudly only after the bounded evidence and its receipt are on disk.
    requireCapture(complete)
  }
}
