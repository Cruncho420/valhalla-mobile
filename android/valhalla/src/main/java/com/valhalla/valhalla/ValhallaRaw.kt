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
 * @property configPath Absolute path to a valid valhalla.json configuration file.
 */
class ValhallaRaw(private val configPath: String) {
  private val valhallaKotlin = ValhallaKotlin()

  /**
   * Run a route request against the Valhalla routing engine.
   *
   * @param request Raw Valhalla route request JSON.
   * @return Raw Valhalla response JSON (route result, or a Valhalla error response on failure).
   */
  fun route(request: String): String {
    return valhallaKotlin.route(request, configPath)
  }
}
