package com.valhalla.valhalla

/**
 * Public raw-string entry point to the Valhalla routing engine for embedders that manage their own
 * configuration files and JSON encoding (e.g. React Native / Expo native modules).
 *
 * Mirrors the internal [ValhallaActor] without the `internal` visibility modifier: [ValhallaActor]
 * and [ValhallaKotlin] cannot be reached from outside this module, which would force embedders that
 * do not want the Moshi-typed [Valhalla] facade into reflection. Candidate for an upstream PR.
 *
 * This assumes your config path is valid, tiles exist, and your request string is valid JSON.
 *
 * ### Lifecycle (r3)
 * The first [route] call lazily creates a persistent native actor (config parse + graph reader,
 * ~100+ MB for a loaded tile set) and reuses it for subsequent calls. Call [close] to destroy the
 * native actor deterministically when you are done with the engine — dropping the reference and
 * waiting for GC is NOT sufficient, because the JVM heap never feels native memory pressure and
 * repeated re-init cycles can stack native allocations until the OS kills the process.
 *
 * - [close] is idempotent: the second and later calls are no-ops.
 * - After [close], [route] throws [IllegalStateException] instead of touching freed memory.
 * - Implements [AutoCloseable], so Kotlin `use { }` / Java try-with-resources work.
 * - [route] and [close] are synchronized: the shared native actor is not thread-safe, and this
 *   also makes close-while-routing safe (close waits for the in-flight route to finish).
 *
 * @property configPath Absolute path to a valid valhalla.json configuration file.
 */
class ValhallaRaw(private val configPath: String) : AutoCloseable {
  // Instantiating ValhallaKotlin triggers its companion-object System.loadLibrary, which also
  // resolves this class's external functions (same libvalhalla-wrapper.so). Kept as the fallback
  // route path when native actor creation fails (see route()).
  private val valhallaKotlin = ValhallaKotlin()

  /** Opaque pointer to the persistent native ValhallaActor; 0 = not created (yet). */
  private var actorHandle: Long = 0

  private var closed = false

  /**
   * Run a route request against the Valhalla routing engine.
   *
   * @param request Raw Valhalla route request JSON.
   * @return Raw Valhalla response JSON (route result, or a Valhalla error response on failure).
   * @throws IllegalStateException if [close] has already been called.
   */
  @Synchronized
  fun route(request: String): String {
    if (closed) throw IllegalStateException("closed")
    if (actorHandle == 0L) {
      actorHandle = nativeCreateActor(configPath)
    }
    if (actorHandle == 0L) {
      // Native actor creation failed (e.g. broken config). Fall back to the stock per-call JNI
      // entry point, which reproduces the pre-r3 behavior exactly: route() always returns JSON
      // (a Valhalla error response here), never crashes. Nothing is leaked: 0 means no native
      // allocation survived the failed construction.
      return valhallaKotlin.route(request, configPath)
    }
    return nativeRoute(actorHandle, request)
  }

  /**
   * Map-match a raw trace_route request using the same persistent actor as [route].
   * Calls the engine's trace_route action; JSON action fields do not select the action.
   * Returns response/error JSON. Actor creation failure returns an error without routing.
   * Like [route], this is serialized with all operations, and fails after [close].
   */
  @Synchronized
  fun traceRoute(request: String): String {
    if (closed) throw IllegalStateException("closed")
    if (actorHandle == 0L) {
      actorHandle = nativeCreateActor(configPath)
    }
    if (actorHandle == 0L) {
      return "{\"code\":-1,\"message\":\"Unable to create trace route actor\"}"
    }
    return nativeTraceRoute(actorHandle, request)
  }

  /**
   * Return native trace_attributes evidence, including matched points and raw scores.
   * Scores retain Valhalla semantics; confidence_score is not an acceptance probability.
   * Uses the same serialized actor and closed-state contract as [traceRoute].
   */
  @Synchronized
  fun traceAttributes(request: String): String {
    if (closed) throw IllegalStateException("closed")
    if (actorHandle == 0L) {
      actorHandle = nativeCreateActor(configPath)
    }
    if (actorHandle == 0L) {
      return "{\"code\":-1,\"message\":\"Unable to create trace attributes actor\"}"
    }
    return nativeTraceAttributes(actorHandle, request)
  }

  /**
   * Destroy the native actor deterministically. Idempotent — second and later calls are no-ops.
   * After close, [route] throws [IllegalStateException].
   */
  @Synchronized
  override fun close() {
    closed = true
    if (actorHandle != 0L) {
      val handle = actorHandle
      actorHandle = 0
      nativeDestroyActor(handle)
    }
  }

  /** Creates a persistent native ValhallaActor. Returns 0 (never throws) on failure. */
  private external fun nativeCreateActor(configPath: String): Long

  /** Routes against a live actor handle. Returns Valhalla response/error JSON, never throws. */
  private external fun nativeRoute(actorHandle: Long, request: String): String

  /** Map-matches against a live actor handle. Returns response/error JSON. */
  private external fun nativeTraceRoute(actorHandle: Long, request: String): String

  /** Returns raw trace_attributes response/error JSON. */
  private external fun nativeTraceAttributes(actorHandle: Long, request: String): String

  /** Deletes the native ValhallaActor behind [actorHandle]. At most once per handle. */
  private external fun nativeDestroyActor(actorHandle: Long)
}
