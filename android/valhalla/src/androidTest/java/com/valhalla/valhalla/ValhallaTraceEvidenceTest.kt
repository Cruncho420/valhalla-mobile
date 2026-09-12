// PURPOSE: Capture genuine Android native trace evidence from the pinned Andorra fixtures.
// RESPONSIBILITY: Preserve exact integer tokens, validate gaps/alternatives, and exercise actor
// reuse.
// DEPENDENCIES: Android instrumentation, Moshi/Okio, existing graph/config, and pinned sample
// assets.
// CONSUMERS: Explicit native certification runs; these tests do not define GPX acceptance
// thresholds.
package com.valhalla.valhalla

import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.squareup.moshi.JsonReader
import java.io.File
import java.security.MessageDigest
import java.util.UUID
import okio.Buffer
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ValhallaTraceEvidenceTest {
  private data class NumberToken(val raw: String)

  private data class Point(val lat: Double, val lon: Double)

  private data class Match(
      val type: String,
      val index: String?,
      val begin: Boolean?,
      val end: Boolean?,
  )

  private data class Evidence(val points: List<Match>, val alternatives: List<Evidence>)

  private val instrumentation = InstrumentationRegistry.getInstrumentation()
  private val directory by lazy {
    File(instrumentation.targetContext.filesDir, "trace-evidence-" + UUID.randomUUID()).apply {
      check(mkdir())
      // Only an artifact directory identifier is logged, never coordinates or native messages.
      Log.i("TraceEvidence", "Captured native responses in " + name)
    }
  }
  private var savedFiles = 0
  // TEST-INDEX-2: the marker is the producing ABI's size_t maximum, not a fixed 64-bit constant.
  private val sentinel = TraceIndexContract.nativeUnassigned()
  private val routeRequest =
      """{"locations":[{"lat":42.5063,"lon":1.5218},{"lat":42.5086,"lon":1.5394}],"costing":"auto"}"""

  @Test
  fun originalSamplesErrorsAndSameActorRecovery() {
    val clean = input("clean-request.json")
    val noisy = input("modest-noise-request.json")
    ValhallaRaw(TestFileUtils.getConfigPath(instrumentation.targetContext)).use { actor ->
      assertRoute(actor.route(routeRequest), "seed-route")
      exercise(actor, clean, "clean")
      exercise(actor, noisy, "modest-noise")
      val outside = listOf(Point(0.0, 0.0), Point(0.001, 0.001))
      exerciseError(actor, outside, "out-of-coverage", 171)
      exerciseError(
          actor,
          listOf(clean.first(), outside.first(), clean.last()),
          "disconnected",
          154,
      )
      save("{", "malformed-request.json")
      assertError(actor.traceRoute("{"), "malformed-trace", 100)
      assertError(actor.traceAttributes("{"), "malformed-attributes", 100)
      exercise(actor, clean, "after-errors")
      assertRoute(actor.route(routeRequest), "route-after-errors")
    }
  }

  @Test
  fun boundedPrefixesAlternativesAndDiscontinuities() {
    val clean = input("clean-request.json")
    ValhallaRaw(TestFileUtils.getConfigPath(instrumentation.targetContext)).use { actor ->
      for ((name, points) in
          listOf("clean" to clean, "noisy" to input("modest-noise-request.json"))) {
        // The original Android/iOS prefix99/100 alternatives regression must succeed genuinely.
        for (count in listOf(99, 100, 50)) {
          val result = capture(actor, points.take(count), "$name-prefix$count", 2)
          assertEquals(2, result.alternatives.size)
        }
        assertEquals(
            0,
            capture(actor, points.take(100), "$name-no-alternates", 0).alternatives.size,
        )
        assertEquals(2, capture(actor, points.takeLast(97), "$name-last97", 2).alternatives.size)
      }
      capture(actor, clean.takeLast(3), "short-tail3", 2)
      capture(actor, clean.take(80).reversed(), "reversed80", 2)
      capture(actor, clean.take(40) + clean.take(40).reversed(), "loop80", 2)
      for (offset in listOf(0.02, 0.05, 0.15)) {
        val origin = clean[99]
        val gap =
            listOf(
                Point(origin.lat + offset, origin.lon + offset),
                Point(origin.lat + offset + 0.0001, origin.lon + offset),
            )
        val result =
            capture(actor, clean.take(30) + gap + clean.takeLast(30), "partial-gap-$offset", 2)
        if (offset >= 0.05) assertGap(result)
      }
      assertError(actor.traceAttributes("{"), "bounded-malformed", 100)
      assertEquals(2, capture(actor, clean.take(100), "reused-prefix100", 2).alternatives.size)
    }
  }

  @Test
  fun numericTokensAreNeverRoundedOrConfusedWithStrings() {
    // TEST-INDEX-2: only this producing ABI's marker is unassigned; the other width is invalid.
    val other =
        if (sentinel == TraceIndexContract.WIDTH_64) TraceIndexContract.WIDTH_32
        else TraceIndexContract.WIDTH_64
    val exact = objectValue(parse("""{"edge_index":$sentinel}"""))["edge_index"]
    assertEquals(sentinel, edgeIndex(exact, 72))
    for (token in
        listOf(
            "\"$sentinel\"",
            other,
            sentinel.dropLast(1) + (sentinel.last() - 1),
            "18446744073709551616",
            "1.8446744073709551615e19",
            "$sentinel.0",
            "72",
            "-1",
        )) {
      val value = objectValue(parse("""{"edge_index":$token}"""))["edge_index"]
      assertThrows(IllegalArgumentException::class.java) { edgeIndex(value, 72) }
    }
    val embedded =
        objectValue(parse("""{"text":"edge_index:18446744073709551615","edge_\u0069ndex":1}"""))
    assertEquals("edge_index:18446744073709551615", embedded["text"])
    assertEquals("1", edgeIndex(embedded["edge_index"], 72))
  }

  private fun input(name: String): List<Point> {
    val bytes = instrumentation.context.assets.open("trace-evidence/$name").use { it.readBytes() }
    val expected =
        if (name == "clean-request.json")
            "97403021d49b71d058b3d610320004d739f4e44ef6421bec0747dbc5b3b2a6e9"
        else "1c7e7ab25d2a666c910147ec519c864c2bae82f993b43bcce207ed0107831899"
    assertEquals(
        expected,
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) },
    )
    val points = JSONObject(bytes.toString(Charsets.UTF_8)).getJSONArray("shape")
    assertEquals(196, points.length())
    return (0 until points.length()).map { i ->
      val p = points.getJSONObject(i)
      Point(p.getDouble("lat"), p.getDouble("lon"))
    }
  }

  private fun request(points: List<Point>, alternates: Int? = null): String {
    val shape = JSONArray()
    points.forEach { shape.put(JSONObject().put("lat", it.lat).put("lon", it.lon)) }
    val attributes =
        listOf(
            "shape",
            "raw_score",
            "confidence_score",
            "edge.begin_shape_index",
            "edge.end_shape_index",
            "edge.length",
            "matched.type",
            "matched.edge_index",
            "matched.distance_from_trace_point",
            "matched.begin_route_discontinuity",
            "matched.end_route_discontinuity",
        )
    val request =
        JSONObject()
            .put("shape", shape)
            .put("costing", "auto")
            .put("shape_match", "map_snap")
            .put(
                "filters",
                JSONObject().put("action", "include").put("attributes", JSONArray(attributes)),
            )
    if (alternates != null) request.put("alternates", alternates)
    return request.toString()
  }

  private fun exercise(actor: ValhallaRaw, points: List<Point>, name: String) {
    val request = request(points)
    save(request, "$name-request.json")
    assertRoute(actor.traceRoute(request), "$name-trace")
    val raw = actor.traceAttributes(request)
    save(raw, "$name-attributes.json")
    validate(objectValue(parse(raw)), points.size)
  }

  private fun exerciseError(actor: ValhallaRaw, points: List<Point>, name: String, code: Int) {
    val request = request(points)
    save(request, "$name-request.json")
    assertError(actor.traceRoute(request), "$name-trace", code)
    assertError(actor.traceAttributes(request), "$name-attributes", code)
  }

  private fun capture(
      actor: ValhallaRaw,
      points: List<Point>,
      name: String,
      alternates: Int,
  ): Evidence {
    require(points.size in 2..100)
    val distance =
        points.zipWithNext().sumOf { (a, b) ->
          111_320 * (kotlin.math.abs(a.lat - b.lat) + kotlin.math.abs(a.lon - b.lon))
        }
    require(distance < 200_000)
    val request = request(points, alternates)
    save(request, "$name-request.json")
    val raw = actor.traceAttributes(request)
    save(raw, "$name-attributes.json")
    return validate(objectValue(parse(raw)), points.size)
  }

  private fun validate(path: Map<String, Any?>, count: Int): Evidence {
    require(!path.containsKey("code")) { "Native error instead of trace evidence" }
    finite(path["raw_score"])
    finite(path["confidence_score"]) // Engine score validation is not probability calibration.
    val shapeCount = decodeShape(path["shape"] as String).size
    val edges = listValue(path["edges"])
    require(edges.isNotEmpty())
    edges.forEach { item ->
      val edge = objectValue(item)
      val begin = boundedIndex(edge["begin_shape_index"], shapeCount)
      val end = boundedIndex(edge["end_shape_index"], shapeCount)
      require(begin <= end && finite(edge["length"]) >= 0)
    }
    val points = listValue(path["matched_points"]).map { match(objectValue(it), edges.size) }
    assertEquals(count, points.size)
    assertTrue(points.count { it.index != null && it.index != sentinel } >= 2)
    val alternatives =
        if (path.containsKey("alternate_paths"))
            listValue(path["alternate_paths"]).map { validate(objectValue(it), count) }
        else emptyList()
    return Evidence(points, alternatives)
  }

  private fun match(point: Map<String, Any?>, edgeCount: Int): Match {
    val type = point["type"] as String
    require(type in listOf("matched", "interpolated", "unmatched"))
    val index =
        if (point.containsKey("edge_index")) edgeIndex(point["edge_index"], edgeCount) else null
    if (type != "unmatched")
        require(index != null && finite(point["distance_from_trace_point"]) >= 0)
    else require(index == null || index == sentinel)
    fun flag(key: String): Boolean? = if (point.containsKey(key)) point[key] as Boolean else null
    return Match(type, index, flag("begin_route_discontinuity"), flag("end_route_discontinuity"))
  }

  private fun assertGap(path: Evidence) {
    assertEquals(62, path.points.size)
    assertEquals(listOf(25), path.points.indices.filter { path.points[it].begin == true })
    assertEquals(listOf(32), path.points.indices.filter { path.points[it].end == true })
    assertEquals(
        (26..31).toList(),
        path.points.indices.filter { path.points[it].type == "unmatched" },
    )
    path.points.slice(26..31).forEach {
      require(it.index == null && it.begin == null && it.end == null)
    }
  }

  private fun assertError(raw: String, name: String, code: Int) {
    save(raw, "$name.json")
    val error = objectValue(parse(raw))
    assertEquals(code.toString(), (error["code"] as NumberToken).raw)
    assertTrue((error["message"] as String).isNotEmpty())
    assertFalse(error.containsKey("matched_points"))
  }

  private fun assertRoute(raw: String, name: String) {
    save(raw, "$name.json")
    val trip = objectValue(objectValue(parse(raw))["trip"])
    assertEquals("0", (trip["status"] as NumberToken).raw)
    val legs = listValue(trip["legs"])
    require(legs.isNotEmpty())
    legs.forEach { require(decodeShape(objectValue(it)["shape"] as String).size >= 2) }
  }

  private fun edgeIndex(value: Any?, count: Int): String {
    require(value is NumberToken)
    if (value.raw == sentinel) return sentinel
    boundedIndex(value, count)
    return value.raw
  }

  private fun boundedIndex(value: Any?, count: Int): Int {
    require(value is NumberToken && value.raw.matches(Regex("0|[1-9][0-9]*")))
    // Bound lexically before signed conversion: no Double, UInt64 rounding, or overflow.
    val maximum = (count - 1).toString()
    require(
        count > 0 &&
            (value.raw.length < maximum.length ||
                (value.raw.length == maximum.length && value.raw <= maximum)))
    return value.raw.toInt()
  }

  private fun finite(value: Any?): Double {
    require(value is NumberToken)
    return value.raw.toDouble().also { require(it.isFinite()) }
  }

  @Suppress("UNCHECKED_CAST")
  private fun objectValue(value: Any?): Map<String, Any?> = value as Map<String, Any?>

  private fun listValue(value: Any?): List<*> = value as List<*>

  private fun parse(raw: String): Any? {
    require(raw.toByteArray(Charsets.UTF_8).size <= 1_048_576)
    var values = 0
    fun read(reader: JsonReader, depth: Int): Any? {
      require(++values <= 65_536 && depth <= 32)
      return when (reader.peek()) {
        JsonReader.Token.BEGIN_OBJECT -> {
          val result = linkedMapOf<String, Any?>()
          reader.beginObject()
          while (reader.hasNext()) {
            val key = reader.nextName()
            require(!result.containsKey(key))
            result[key] = read(reader, depth + 1)
          }
          reader.endObject()
          result
        }
        JsonReader.Token.BEGIN_ARRAY -> {
          val result = mutableListOf<Any?>()
          reader.beginArray()
          while (reader.hasNext()) result.add(read(reader, depth + 1))
          reader.endArray()
          result
        }
        JsonReader.Token.NUMBER -> NumberToken(reader.nextString())
        JsonReader.Token.STRING -> reader.nextString()
        JsonReader.Token.BOOLEAN -> reader.nextBoolean()
        JsonReader.Token.NULL -> reader.nextNull<Any>()
        else -> throw IllegalArgumentException("Invalid native JSON")
      }
    }
    return JsonReader.of(Buffer().writeUtf8(raw)).use { reader ->
      read(reader, 0).also { require(reader.peek() == JsonReader.Token.END_DOCUMENT) }
    }
  }

  private fun decodeShape(encoded: String): List<Point> {
    require(encoded.length <= 1_048_576)
    var cursor = 0
    fun component(): Long {
      var value = 0L
      for (shift in 0..55 step 5) {
        require(cursor < encoded.length)
        val digit = encoded[cursor++].code - 63
        require(digit in 0..63)
        value = value or ((digit.toLong() and 31L) shl shift)
        if (digit < 32) return if (value and 1L == 0L) value shr 1 else -(value shr 1) - 1
      }
      throw IllegalArgumentException("Invalid shape component")
    }
    var lat = 0L
    var lon = 0L
    val points = mutableListOf<Point>()
    while (cursor < encoded.length) {
      lat = Math.addExact(lat, component())
      lon = Math.addExact(lon, component())
      require(
          lat in -90_000_000..90_000_000 &&
              lon in -180_000_000..180_000_000 &&
              points.size < 10_000)
      points.add(Point(lat / 1_000_000.0, lon / 1_000_000.0))
    }
    return points
  }

  private fun save(raw: String, name: String) {
    require(raw.toByteArray(Charsets.UTF_8).size <= 1_048_576 && savedFiles < 96)
    val file = File(directory, name)
    check(file.createNewFile())
    file.writeText(raw, Charsets.UTF_8)
    savedFiles++
  }
}
