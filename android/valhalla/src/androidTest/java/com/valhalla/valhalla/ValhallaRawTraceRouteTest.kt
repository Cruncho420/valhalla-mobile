// PURPOSE: Prove trace_route dispatches to native map matching and the actor survives errors.
// RESPONSIBILITY: Bounded-index match evidence using exact native tokens, never signed coercion.
// DEPENDENCIES: Android instrumentation, pinned Andorra tiles, TraceIndexContract.
// CONSUMERS: TEST-INDEX-2 acceptance for the Rods native trace provider.
package com.valhalla.valhalla

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.valhalla.valhalla.TraceIndexContract.Classification
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ValhallaRawTraceRouteTest {
  private val routeRequest =
      """{"locations":[{"lat":42.5063,"lon":1.5218},{"lat":42.5086,"lon":1.5394}],"costing":"auto"}"""

  @Test
  fun traceUsesMapMatchingAndActorSurvivesErrors() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    ValhallaRaw(TestFileUtils.getConfigPath(context)).use { actor ->
      val route = JSONObject(actor.route(routeRequest)).getJSONObject("trip")
      val shape = route.getJSONArray("legs").getJSONObject(0).getString("shape")
      val request =
          JSONObject()
              .put("encoded_polyline", shape)
              .put("costing", "auto")
              .put("shape_match", "map_snap")
              .toString()

      // There are no locations: the route action must reject this trace-only request.
      assertTrue(JSONObject(actor.route(request)).has("code"))
      val trace = JSONObject(actor.traceRoute(request)).getJSONObject("trip")
      assertEquals(0, trace.getInt("status"))
      assertTrue(trace.getJSONArray("legs").getJSONObject(0).getString("shape").isNotEmpty())
      // TEST-INDEX-2: request the actual edge array so every genuine index has a denominator.
      val attributesRequest =
          JSONObject(request)
              .put(
                  "filters",
                  JSONObject()
                      .put("action", "include")
                      .put(
                          "attributes",
                          JSONArray(
                              listOf(
                                  "shape",
                                  "raw_score",
                                  "confidence_score",
                                  "edge.length",
                                  "matched.type",
                                  "matched.edge_index",
                                  "matched.distance_from_trace_point"))))
              .toString()
      assertNativeMatchEvidence(actor.traceAttributes(attributesRequest))
      assertTrue(JSONObject(actor.traceAttributes("{")).has("code"))
      assertNativeMatchEvidence(actor.traceAttributes(attributesRequest))
      assertTrue(JSONObject(actor.traceRoute("{")).has("code"))
      assertEquals(0, JSONObject(actor.traceRoute(request)).getJSONObject("trip").getInt("status"))
      assertEquals(0, JSONObject(actor.route(routeRequest)).getJSONObject("trip").getInt("status"))
    }
  }

  @Suppress("UNCHECKED_CAST")
  private fun assertNativeMatchEvidence(raw: String) {
    val response = TraceIndexContract.parse(raw) as Map<String, Any?>
    assertTrue((response["raw_score"] as NumberLexeme).raw.toDouble().isFinite())
    // The best candidate's confidence_score is 1 by engine design, not calibrated confidence.
    assertTrue((response["confidence_score"] as NumberLexeme).raw.toDouble().isFinite())
    assertTrue((response["shape"] as String).isNotEmpty())
    val edges = response["edges"] as List<Any?>
    assertTrue("Index verification needs the response's own edges", edges.isNotEmpty())
    val marker = TraceIndexContract.nativeUnassigned()

    val points = response["matched_points"] as List<Any?>
    var links = 0
    var unassigned = 0
    points.forEachIndexed { position, item ->
      val point = item as Map<String, Any?>
      val type = point["type"] as String
      assertTrue(type in listOf("matched", "interpolated", "unmatched"))
      if (type == "unmatched") return@forEachIndexed
      val distance = (point["distance_from_trace_point"] as NumberLexeme).raw.toDouble()
      assertTrue(distance.isFinite() && distance >= 0)
      when (TraceIndexContract.classify(point["edge_index"], edges.size, marker)) {
        // Unassigned samples stay in order and are excluded from the actual-link count.
        is Classification.Unassigned -> unassigned++
        is Classification.Link -> links++
        null -> fail("Sample $position index is neither this ABI's marker nor an in-range edge")
      }
    }
    assertTrue("Successful matching needs two genuine road links", links >= 2)
    assertEquals(
        points.count { (it as Map<String, Any?>)["type"] != "unmatched" }, links + unassigned)
  }

  /** Boundary cases for the index contract; no engine involvement, both ABI widths exercised. */
  @Test
  fun indexContractBoundaries() {
    val edges = 72
    for (width in listOf(TraceIndexContract.WIDTH_64, TraceIndexContract.WIDTH_32)) {
      val other =
          if (width == TraceIndexContract.WIDTH_64) TraceIndexContract.WIDTH_32
          else TraceIndexContract.WIDTH_64
      assertEquals(Classification.Link(0), classify("0", edges, width))
      assertEquals(Classification.Link(71), classify("71", edges, width))
      assertNull(classify("72", edges, width))
      assertNull(classify("1000", edges, width))
      // Each width's own marker is unassigned; it is never counted as a road link.
      assertEquals(Classification.Unassigned, classify(width, edges, width))
      // The wrong-width marker is rejected outright rather than read as "unassigned".
      assertNull(classify(other, edges, width))
      // Marker neighbours are out-of-range values, not markers.
      assertNull(classify(width.dropLast(1) + (width.last() - 1), edges, width))
    }
    // Rejected spellings where an index is required.
    for (token in
        listOf(
            "\"5\"",
            "\"18446744073709551615\"",
            "5.0",
            "5.5",
            "5e0",
            "-1",
            "true",
            "false",
            "null")) {
      assertNull(
          "token $token must not yield an index",
          classify(token, edges, TraceIndexContract.WIDTH_64))
    }
    // All-unassigned input fails the successful-matching requirement.
    val all = List(3) { classify(TraceIndexContract.WIDTH_64, edges, TraceIndexContract.WIDTH_64) }
    assertEquals(3, all.count { it is Classification.Unassigned })
    assertEquals(0, all.count { it is Classification.Link })
  }

  @Suppress("UNCHECKED_CAST")
  private fun classify(token: String, edges: Int, marker: String): Classification? {
    val parsed = TraceIndexContract.parse("""{"edge_index":$token}""") as Map<String, Any?>
    return TraceIndexContract.classify(parsed["edge_index"], edges, marker)
  }

  @Test
  fun closeIsIdempotentAndRejectsBothActions() {
    val actor = ValhallaRaw("missing-config.json")
    assertTrue(JSONObject(actor.traceRoute("{}")).has("code"))
    assertTrue(JSONObject(actor.traceAttributes("{}")).has("code"))
    actor.close()
    actor.close()
    assertThrows(IllegalStateException::class.java) { actor.traceRoute("{}") }
    assertThrows(IllegalStateException::class.java) { actor.route("{}") }
    assertThrows(IllegalStateException::class.java) { actor.traceAttributes("{}") }
  }
}
