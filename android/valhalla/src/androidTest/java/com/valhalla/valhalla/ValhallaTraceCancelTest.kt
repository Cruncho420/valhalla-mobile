// PURPOSE: Prove the cancellable trace_attributes (Rods r6) stops at the engine's interrupt poll,
// stops ONLY the call carrying its token, and leaves the actor usable.
// RESPONSIBILITY: Deterministic cancel-before/cancel-other/after-close cases on the pinned Andorra
// tiles; the "within one round of a mid-flight cancel" timing proof is the desktop harness's job
// (a dense city window — these tiles answer in milliseconds).
// DEPENDENCIES: Android instrumentation, pinned Andorra tiles.
// CONSUMERS: Rods engine release r6 (FEAT-090 phone bound lane).
package com.valhalla.valhalla

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ValhallaTraceCancelTest {
  private val routeRequest =
      """{"locations":[{"lat":42.5063,"lon":1.5218},{"lat":42.5086,"lon":1.5394}],"costing":"auto"}"""

  private fun attributesRequest(actor: ValhallaRaw): String {
    val shape =
        JSONObject(actor.route(routeRequest)).getJSONObject("trip").getJSONArray("legs")
            .getJSONObject(0).getString("shape")
    return JSONObject()
        .put("encoded_polyline", shape)
        .put("costing", "auto")
        .put("shape_match", "map_snap")
        .put("alternates", 1)
        .put("filters", JSONObject().put("action", "include")
            .put("attributes", JSONArray(listOf("shape", "raw_score", "matched.type"))))
        .toString()
  }

  @Test
  fun cancelStopsOnlyItsOwnCallAndTheActorSurvives() {
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    ValhallaRaw(TestFileUtils.getConfigPath(context)).use { actor ->
      val request = attributesRequest(actor)
      val plain = actor.traceAttributes(request)
      assertTrue(JSONObject(plain).has("raw_score"))
      // An uncancelled token answers exactly what the plain call answers.
      assertEquals(plain, actor.traceAttributes(request, 101))
      // Cancelled before it starts: the first interrupt poll ends it. Two pending cancels do not
      // erase each other (the second one must not un-cancel the first).
      ValhallaRaw.cancelTrace(102)
      ValhallaRaw.cancelTrace(106)
      assertEquals(-2, JSONObject(actor.traceAttributes(request, 102)).getInt("code"))
      assertEquals(-2, JSONObject(actor.traceAttributes(request, 106)).getInt("code"))
      // A cancel names one call: 103 runs to its answer while 102 is still the cancelled token.
      assertEquals(plain, actor.traceAttributes(request, 103))
      // Same actor after a cancel: the plain path and route are unchanged.
      assertEquals(plain, actor.traceAttributes(request))
      assertEquals(0, JSONObject(actor.route(routeRequest)).getJSONObject("trip").getInt("status"))
    }
  }

  @Test
  fun cancelNeedsNoActorAndTokensMustBePositive() {
    ValhallaRaw.cancelTrace(107) // no actor exists yet: still recorded for the call that carries 107
    val context = InstrumentationRegistry.getInstrumentation().targetContext
    ValhallaRaw(TestFileUtils.getConfigPath(context)).use { live ->
      assertEquals(-2, JSONObject(live.traceAttributes(attributesRequest(live), 107)).getInt("code"))
    }
    val actor = ValhallaRaw("missing-config.json")
    actor.close()
    ValhallaRaw.cancelTrace(104) // touches no actor: fine after close
    assertThrows(IllegalArgumentException::class.java) { actor.traceAttributes("{}", 0) }
    assertThrows(IllegalStateException::class.java) { actor.traceAttributes("{}", 105) }
  }
}
