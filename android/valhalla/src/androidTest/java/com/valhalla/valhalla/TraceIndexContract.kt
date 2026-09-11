// PURPOSE: TEST-INDEX-2 index contract for native trace evidence on Android.
// RESPONSIBILITY: Preserve exact integer tokens and separate unassigned samples from road links.
// DEPENDENCIES: Moshi JsonReader for lossless number tokens; android.os.Process for producer width.
// CONSUMERS: ValhallaRawTraceRouteTest and ValhallaTraceEvidenceTest. Test-only; no product code.
package com.valhalla.valhalla

import com.squareup.moshi.JsonReader
import okio.Buffer

/** An unconverted JSON number, kept as its original lexeme. */
data class NumberLexeme(val raw: String)

/**
 * The unassigned marker is the *producer's* `size_t` maximum. The native library is loaded into
 * this process, so the producing ABI is this process's word width. It is never inferred from a
 * later reader's host, and the wrong width is rejected rather than read as "unassigned".
 */
object TraceIndexContract {
  const val WIDTH_64 = "18446744073709551615"
  const val WIDTH_32 = "4294967295"

  /** Marker the native library in *this* process can emit. */
  fun nativeUnassigned(): String = if (android.os.Process.is64Bit()) WIDTH_64 else WIDTH_32

  sealed class Classification {
    object Unassigned : Classification()

    data class Link(val index: Int) : Classification()
  }

  /**
   * Classify one `edge_index` value against the response's own edge array. Returns null for
   * anything that is neither this producer's marker nor an in-range link: the wrong-width marker,
   * marker neighbours, out-of-range values, negatives, decimals, exponents, quoted numbers,
   * booleans, and null.
   */
  fun classify(value: Any?, edgeCount: Int, marker: String): Classification? {
    if (value !is NumberLexeme) return null
    if (value.raw == marker) return Classification.Unassigned
    if (!value.raw.matches(Regex("0|[1-9][0-9]*"))) return null
    if (edgeCount <= 0) return null
    // Bound lexically before any conversion: no Double rounding, no signed overflow.
    val maximum = (edgeCount - 1).toString()
    val inRange =
        value.raw.length < maximum.length ||
            (value.raw.length == maximum.length && value.raw <= maximum)
    return if (inRange) Classification.Link(value.raw.toInt()) else null
  }

  /** Parse JSON, keeping every number as its original lexeme. */
  fun parse(raw: String): Any? = JsonReader.of(Buffer().writeUtf8(raw)).use { read(it) }

  private fun read(reader: JsonReader): Any? =
      when (reader.peek()) {
        JsonReader.Token.BEGIN_OBJECT -> {
          val map = LinkedHashMap<String, Any?>()
          reader.beginObject()
          while (reader.hasNext()) map[reader.nextName()] = read(reader)
          reader.endObject()
          map
        }
        JsonReader.Token.BEGIN_ARRAY -> {
          val list = ArrayList<Any?>()
          reader.beginArray()
          while (reader.hasNext()) list.add(read(reader))
          reader.endArray()
          list
        }
        JsonReader.Token.STRING -> reader.nextString()
        JsonReader.Token.BOOLEAN -> reader.nextBoolean()
        JsonReader.Token.NULL -> reader.nextNull<Any?>()
        JsonReader.Token.NUMBER -> NumberLexeme(reader.nextString())
        else -> throw IllegalArgumentException("Unexpected token")
      }
}
