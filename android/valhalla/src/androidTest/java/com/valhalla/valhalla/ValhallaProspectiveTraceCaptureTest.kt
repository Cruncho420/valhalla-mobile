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
  private val LIBRARY_NAME = "libvalhalla-wrapper.so"

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
    val captured = JSONArray()
    var total = 0L
    // Set only once the plan ran to its end with every native return inside the declared bounds.
    var complete = false
    val mappingLines = JSONArray()
    var nativeSha = ""
    var nativeBytes = 0L
    var mappedPath = ""
    var mappedSource = ""
    var capturedAfterCall = -1
    var libraryFailure: String? = null
    var processMachine = 0
    var processAbi = ""
    var entryPath = ""
    ValhallaRaw(File(directory, "config.json").absolutePath).use { actor ->
      // The wrapper is not mapped until the first native call resolves the JNI entry points, and
      // with extractNativeLibs=false the loader maps it out of the APK, so /proc/self/maps names
      // the APK, never the .so. Prove the executing ABI from that APK mapping plus the process's
      // own ELF, never from nativeLibraryDir or the device's list of supported ABIs.
      fun elfMachine(bytes: ByteArray): Int {
        requireCapture(bytes.size >= 20 && bytes[0] == 0x7f.toByte() && bytes[1] == 'E'.code.toByte()
            && bytes[2] == 'L'.code.toByte() && bytes[3] == 'F'.code.toByte())
        // 64-bit, little endian.
        requireCapture(bytes[4] == 2.toByte() && bytes[5] == 1.toByte())
        return (bytes[18].toInt() and 0xff) or ((bytes[19].toInt() and 0xff) shl 8)
      }
      fun captureMappedLibrary() {
        val apk = File(context.applicationInfo.sourceDir).canonicalFile
        // Record every candidate mapping as it is read, before asserting anything about it: the
        // receipt is written even when this refuses, so a refusal explains itself in the
        // artifacts (instrumentation stdout never reached the uploaded transcript).
        val observed = ArrayList<String>()
        var executable = false
        File("/proc/self/maps").bufferedReader().use { reader ->
          while (true) {
            val line = reader.readLine() ?: break
            val path = line.substringAfter(" /", "").let { if (it.isEmpty()) "" else "/$it" }
            // Only this process's own installed code; system framework APKs are not candidates.
            if (!path.startsWith("/data/app/") || !path.contains(".apk")) continue
            if (observed.size >= 32) break
            observed.add(line.take(512))
            mappingLines.put(line.take(512))
            // Any other APK — a second package, a split, or a "(deleted)" path — is a second
            // binary source and is refused rather than silently preferred.
            requireCapture(path == apk.path)
            val permissions = line.split(" ")[1]
            requireCapture(permissions.length == 4)
            if (permissions[2] == 'x') executable = true
          }
        }
        // The mapped, executing APK: at least one segment of it is executable.
        requireCapture(observed.isNotEmpty() && executable)
        mappedPath = apk.path
        mappedSource = "apk-entry"
        val exeHeader = ByteArray(20)
        File("/proc/self/exe").inputStream().use { requireCapture(it.read(exeHeader) == exeHeader.size) }
        // The kernel's own view of what is executing, not Build.SUPPORTED_ABIS.
        processMachine = elfMachine(exeHeader)
        requireCapture(Process.is64Bit() && (processMachine == 62 || processMachine == 183))
        processAbi = if (processMachine == 62) "x86_64" else "arm64-v8a"
        entryPath = "lib/$processAbi/$LIBRARY_NAME"
        requireCapture(apk.isFile && apk.canonicalFile == apk)
        val bytes = java.util.zip.ZipFile(apk).use { zip ->
          val item = checkNotNull(zip.getEntry(entryPath)) { "Prospective capture incomplete" }
          requireCapture(item.size in 1..(256L * 1048576))
          val output = java.io.ByteArrayOutputStream()
          zip.getInputStream(item).use { stream ->
            val buffer = ByteArray(65536)
            while (true) {
              val count = stream.read(buffer)
              if (count < 0) break
              requireCapture(output.size() + count <= 256 * 1048576)
              output.write(buffer, 0, count)
            }
          }
          output.toByteArray()
        }
        // The chosen entry must be built for the architecture that is actually executing.
        requireCapture(elfMachine(bytes) == processMachine)
        nativeSha = digest(bytes)
        nativeBytes = bytes.size.toLong()
      }
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
        if (index == 0) {
          // The first call has now resolved the JNI entry points, so the library is mapped.
          // A refusal here is recorded and fails at the end, after the receipt is on disk.
          try {
            captureMappedLibrary()
            capturedAfterCall = 0
          } catch (error: Exception) {
            libraryFailure = error.javaClass.name
          }
        }
        if (oversize || total > 120L * 1048576) break
        if (index == requests.size - 1 && libraryFailure == null) complete = true
      }
    }
    val receipt = JSONObject().put("version", 1).put("complete", complete).put("platform", "android")
        .put("abi", processAbi).put("nativeLibrarySha256", nativeSha)
        .put("nativeLibrary", JSONObject().put("path", mappedPath).put("source", mappedSource)
            .put("sha256", nativeSha).put("bytes", nativeBytes).put("mappings", mappingLines)
            .put("processElfMachine", processMachine).put("entryPath", entryPath)
            .put("capturedAfterCallIndex", capturedAfterCall).also { library ->
              val failure = libraryFailure
              if (failure != null) library.put("failureClass", failure)
            })
        .put("stageSha256", digest(stage)).put("extractSha256", digest(graph))
        .put("configSha256", digest(configBytes)).put("rows", captured)
    save(receipt.toString().toByteArray(Charsets.UTF_8), "receipt.json")
    // Fail loudly only after the bounded evidence and its receipt are on disk.
    requireCapture(complete && libraryFailure == null && capturedAfterCall == 0)
  }
}
