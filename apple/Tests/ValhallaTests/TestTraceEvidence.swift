// PURPOSE: Collect bounded synthetic-fixture evidence from the real native matcher.
// RESPONSIBILITY: Check edge links without converting the native UInt64 sentinel to a signed index.
// DEPENDENCIES: XCTest, Valhalla, and the bundled Andorra graph.
// CONSUMERS: Explicit simulator evidence runs; this is not a consumer acceptance threshold.
import XCTest
import ValhallaConfigModels
@testable import Valhalla

final class TestTraceEvidence: XCTestCase {
    private struct Point: Codable {
        let lat: Double
        let lon: Double
    }

    private struct Evidence: Decodable {
        struct Edge: Decodable {
            let begin_shape_index: UInt64
            let end_shape_index: UInt64
            let length: Double
        }
        struct Match: Decodable {
            let type: String
            let edge_index: UInt64?
            let distance_from_trace_point: Double?
        }
        let shape: String
        let raw_score: Double
        let confidence_score: Double
        let edges: [Edge]
        let matched_points: [Match]
    }

    private struct NativeError: Decodable {
        let code: Int
        let message: String
    }

    private var evidenceDirectory: URL?
    private var savedFiles = 0

    override func setUpWithError() throws {
        guard let path = ProcessInfo.processInfo.environment["VALHALLA_TRACE_EVIDENCE_DIR"] else {
            return
        }
        guard path.hasPrefix("/"), !path.contains("://") else {
            throw invalidEvidence()
        }
        // A distinct directory prevents replacing prior evidence or unrelated operator files.
        let directory = URL(fileURLWithPath: path, isDirectory: true)
            .appendingPathComponent("trace-evidence-" + UUID().uuidString, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        evidenceDirectory = directory
    }

    func testSyntheticTraceEvidenceAndActorReuse() throws {
        let tiles = Bundle.module.resourceURL!.appendingPathComponent("TestData/valhalla_tiles")
        let actor = try Valhalla(ValhallaConfig(tilesDir: tiles))
        let routeRequest = #"{"locations":[{"lat":42.5063,"lon":1.5218},{"lat":42.5086,"lon":1.5394}],"costing":"auto"}"#
        let routeJSON = actor.route(rawRequest: routeRequest)
        try save(routeJSON, name: "seed-route.json")
        let route = try XCTUnwrap(object(routeJSON)["trip"] as? [String: Any])
        let legs = try XCTUnwrap(route["legs"] as? [[String: Any]])
        let shape = try XCTUnwrap(legs.first?["shape"] as? String)
        let clean = try decodeShape(shape)
        XCTAssertGreaterThanOrEqual(clean.count, 2)
        let first = try XCTUnwrap(clean.first)
        let last = try XCTUnwrap(clean.last)
        try exercise(actor, points: clean, name: "clean", allowsNativeError: false)

        // Fixed alternating offsets of roughly 2–3 metres; no random seed or telemetry coordinates.
        let noisy = clean.enumerated().map { index, point in
            Point(lat: point.lat + (index.isMultiple(of: 2) ? 0.00002 : -0.00002),
                  lon: point.lon + (index.isMultiple(of: 3) ? 0.00002 : -0.00002))
        }
        try exercise(actor, points: noisy, name: "modest-noise", allowsNativeError: false)
        // These synthetic points are far outside the Andorra fixture, not a claimed road route.
        let outside = [Point(lat: 0, lon: 0), Point(lat: 0.001, lon: 0.001)]
        try exercise(actor, points: outside, name: "out-of-coverage", allowsNativeError: true)
        try exercise(actor, points: [first, outside[0], last],
                     name: "disconnected", allowsNativeError: true)

        for (name, response) in [("malformed-trace", actor.traceRoute(rawRequest: "{")),
                                 ("malformed-attributes", actor.traceAttributes(rawRequest: "{"))] {
            try save(response, name: name + ".json")
            let error = try JSONDecoder().decode(NativeError.self, from: Data(response.utf8))
            XCTAssertFalse(error.message.isEmpty)
        }
        try exercise(actor, points: clean, name: "after-errors", allowsNativeError: false)
        let reusedRoute = actor.route(rawRequest: routeRequest)
        try save(reusedRoute, name: "route-after-errors.json")
        XCTAssertEqual(try XCTUnwrap(object(reusedRoute)["trip"] as? [String: Any])["status"] as? Int, 0)
    }

    private func exercise(_ actor: Valhalla, points: [Point], name: String,
                          allowsNativeError: Bool) throws {
        let pointObjects = try JSONSerialization.jsonObject(with: JSONEncoder().encode(points))
        let request: [String: Any] = ["shape": pointObjects, "costing": "auto", "shape_match": "map_snap"]
        let requestJSON = String(decoding: try JSONSerialization.data(withJSONObject: request), as: UTF8.self)
        try save(requestJSON, name: name + "-request.json")
        let trace = actor.traceRoute(rawRequest: requestJSON)
        try save(trace, name: name + "-trace.json")
        let traceObject = try object(trace)
        if traceObject["code"] != nil {
            XCTAssertTrue(allowsNativeError)
            XCTAssertFalse(try JSONDecoder().decode(NativeError.self, from: Data(trace.utf8)).message.isEmpty)
        } else {
            let trip = try XCTUnwrap(traceObject["trip"] as? [String: Any])
            XCTAssertEqual(trip["status"] as? Int, 0)
            let legs = try XCTUnwrap(trip["legs"] as? [[String: Any]])
            XCTAssertFalse(legs.isEmpty)
            XCTAssertFalse(try XCTUnwrap(legs.first?["shape"] as? String).isEmpty)
        }
        var attributesRequest = request
        attributesRequest["filters"] = ["action": "include", "attributes": [
            "shape", "raw_score", "confidence_score", "edge.begin_shape_index",
            "edge.end_shape_index", "edge.length", "matched.type", "matched.edge_index",
            "matched.distance_from_trace_point"
        ]]
        let attributesJSON = String(decoding: try JSONSerialization.data(withJSONObject: attributesRequest), as: UTF8.self)
        try save(attributesJSON, name: name + "-attributes-request.json")
        let response = actor.traceAttributes(rawRequest: attributesJSON)
        try save(response, name: name + "-attributes.json")
        if try object(response)["code"] != nil {
            XCTAssertTrue(allowsNativeError)
            // A successful trace must not be accepted without accompanying linked evidence.
            XCTAssertNotNil(traceObject["code"])
            XCTAssertFalse(try JSONDecoder().decode(NativeError.self, from: Data(response.utf8)).message.isEmpty)
        } else {
            try validate(response, inputCount: points.count)
        }
    }

    private func validate(_ json: String, inputCount: Int) throws {
        // JSONDecoder decodes UInt64 directly from the original token: no Double or Int coercion.
        let evidence = try JSONDecoder().decode(Evidence.self, from: Data(json.utf8))
        XCTAssertTrue(evidence.raw_score.isFinite)
        XCTAssertTrue(evidence.confidence_score.isFinite) // Best-candidate 1 is not a probability.
        XCTAssertFalse(evidence.edges.isEmpty)
        XCTAssertEqual(evidence.matched_points.count, inputCount)
        let shapeCount = UInt64(try decodeShape(evidence.shape).count)
        for edge in evidence.edges {
            XCTAssertLessThanOrEqual(edge.begin_shape_index, edge.end_shape_index)
            XCTAssertLessThan(edge.end_shape_index, shapeCount)
            XCTAssertTrue(edge.length.isFinite && edge.length >= 0)
        }
        var linked = 0
        for point in evidence.matched_points {
            XCTAssertTrue(["matched", "interpolated", "unmatched"].contains(point.type))
            if let index = point.edge_index, index != UInt64.max {
                XCTAssertLessThan(index, UInt64(evidence.edges.count))
            }
            guard point.type != "unmatched" else { continue }
            let distance = try XCTUnwrap(point.distance_from_trace_point)
            XCTAssertTrue(distance.isFinite && distance >= 0)
            let index = try XCTUnwrap(point.edge_index)
            // The native unresolved sentinel remains exact in saved JSON; it is never an edge link.
            guard index != UInt64.max else { continue }
            if index < UInt64(evidence.edges.count) { linked += 1 }
        }
        XCTAssertGreaterThanOrEqual(linked, 2, "Successful matching needs at least two actual edge links")
    }

    private func decodeShape(_ encoded: String) throws -> [Point] {
        let bytes = Array(encoded.utf8)
        guard bytes.count <= 1_048_576 else { throw invalidEvidence() }
        var cursor = 0
        func component() throws -> Int64 {
            var value: UInt64 = 0
            for shift in stride(from: 0, through: 55, by: 5) {
                guard cursor < bytes.count, bytes[cursor] >= 63, bytes[cursor] <= 126 else {
                    throw invalidEvidence()
                }
                let chunk = UInt64(bytes[cursor] - 63)
                cursor += 1
                value |= (chunk & 31) << shift
                if chunk < 32 { return value & 1 == 0 ? Int64(value >> 1) : -Int64(value >> 1) - 1 }
            }
            throw invalidEvidence()
        }
        var lat: Int64 = 0
        var lon: Int64 = 0
        var result: [Point] = []
        while cursor < bytes.count {
            let nextLat = lat.addingReportingOverflow(try component())
            let nextLon = lon.addingReportingOverflow(try component())
            guard !nextLat.overflow, !nextLon.overflow else { throw invalidEvidence() }
            lat = nextLat.partialValue
            lon = nextLon.partialValue
            guard (-90_000_000...90_000_000).contains(lat),
                  (-180_000_000...180_000_000).contains(lon), result.count < 10_000 else {
                throw invalidEvidence()
            }
            result.append(Point(lat: Double(lat) / 1_000_000, lon: Double(lon) / 1_000_000))
        }
        return result
    }

    private func object(_ json: String) throws -> [String: Any] {
        guard json.utf8.count <= 1_048_576 else { throw invalidEvidence() }
        return try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
    }

    private func save(_ json: String, name: String) throws {
        guard json.utf8.count <= 1_048_576 else { throw invalidEvidence() }
        guard let directory = evidenceDirectory else { return }
        guard savedFiles < 32 else { throw invalidEvidence() }
        try Data(json.utf8).write(to: directory.appendingPathComponent(name), options: .withoutOverwriting)
        savedFiles += 1
    }

    private func invalidEvidence() -> NSError {
        NSError(domain: "TraceEvidence", code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Invalid or oversized synthetic trace evidence"])
    }
}
