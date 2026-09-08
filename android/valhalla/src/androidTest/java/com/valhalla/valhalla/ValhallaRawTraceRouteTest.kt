package com.valhalla.valhalla

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
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
                                  "shape", "raw_score", "confidence_score", "matched.type",
                                  "matched.edge_index", "matched.distance_from_trace_point"))))
              .toString()
      assertNativeMatchEvidence(JSONObject(actor.traceAttributes(attributesRequest)))
      assertTrue(JSONObject(actor.traceAttributes("{")).has("code"))
      assertNativeMatchEvidence(JSONObject(actor.traceAttributes(attributesRequest)))
      assertTrue(JSONObject(actor.traceRoute("{")).has("code"))
      assertEquals(0, JSONObject(actor.traceRoute(request)).getJSONObject("trip").getInt("status"))
      assertEquals(0, JSONObject(actor.route(routeRequest)).getJSONObject("trip").getInt("status"))
    }
  }

  private fun assertNativeMatchEvidence(response: JSONObject) {
    assertTrue(response.getDouble("raw_score").isFinite())
    // The best candidate's confidence_score is 1 by engine design, not calibrated confidence.
    assertTrue(response.getDouble("confidence_score").isFinite())
    assertTrue(response.getString("shape").isNotEmpty())
    val points = response.getJSONArray("matched_points")
    var matched = 0
    for (index in 0 until points.length()) {
      val point = points.getJSONObject(index)
      val type = point.getString("type")
      assertTrue(type in listOf("matched", "interpolated", "unmatched"))
      if (type != "unmatched") {
        matched++
        assertTrue(point.getInt("edge_index") >= 0)
        val distance = point.getDouble("distance_from_trace_point")
        assertTrue(distance.isFinite() && distance >= 0)
      }
    }
    assertTrue("Expected multiple matched trace samples", matched >= 2)
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
