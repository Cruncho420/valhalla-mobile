// PURPOSE: Prove trace_route dispatches to native map matching and the actor survives errors.
// RESPONSIBILITY: Bounded-index match evidence using exact native integers, never signed coercion.
// DEPENDENCIES: Valhalla test tiles (Andorra); no network, no coordinate logging.
// CONSUMERS: TEST-INDEX-2 acceptance for the Rods native trace provider.
import XCTest
import ValhallaConfigModels
@testable import Valhalla

/// TEST-INDEX-2 index contract, shared by the live assertions and the boundary cases.
///
/// The unassigned marker is the *producer's* `size_t` maximum.
/// The native library runs in this process,
/// so the producing ABI is this process's word width — never inferred from a later reader.
enum TraceIndexContract {
    static let width64 = UInt64.max            // size_t max on a 64-bit producer
    static let width32 = UInt64(UInt32.max)    // size_t max on a 32-bit producer

    /// The marker this process's native library can emit. `UInt` is the host word.
    static let nativeUnassigned = UInt64(UInt.max)

    enum Classification: Equatable {
        case unassigned
        case link(UInt64)
    }

    /// Classify an already-exact index against the response's own edge array.
    /// Returns nil for anything that is neither the producer's marker nor an in-range link.
    static func classify(_ index: UInt64, edgeCount: Int, unassigned: UInt64) -> Classification? {
        if index == unassigned { return .unassigned }
        // Any other marker width is a wrong-ABI value, not a road link, and not "unassigned".
        guard index < UInt64(edgeCount) else { return nil }
        return .link(index)
    }
}

final class TestTraceRoute: XCTestCase {
    /// Decoded with `UInt64`, so decimals, exponents, quoted numbers, booleans, null,
    /// and negative values fail to decode rather than being coerced to a plausible index.
    private struct Evidence: Decodable {
        struct Edge: Decodable { let length: Double }
        struct Match: Decodable {
            let type: String
            let edge_index: UInt64?
            let distance_from_trace_point: Double?
        }
        let raw_score: Double
        let confidence_score: Double
        let shape: String
        let edges: [Edge]
        let matched_points: [Match]
    }

    private func object(_ json: String) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
    }

    func testTraceUsesMapMatchingAndActorSurvivesErrors() throws {
        let tiles = Bundle.module.resourceURL!.appendingPathComponent("TestData/valhalla_tiles")
        let actor = try Valhalla(ValhallaConfig(tilesDir: tiles))
        let routeRequest = #"{"locations":[{"lat":42.5063,"lon":1.5218},{"lat":42.5086,"lon":1.5394}],"costing":"auto"}"#
        let route = try XCTUnwrap(object(actor.route(rawRequest: routeRequest))["trip"] as? [String: Any])
        let legs = try XCTUnwrap(route["legs"] as? [[String: Any]])
        let shape = try XCTUnwrap(legs.first?["shape"] as? String)
        let requestData = try JSONSerialization.data(withJSONObject: [
            "encoded_polyline": shape, "costing": "auto", "shape_match": "map_snap"
        ])
        let request = String(decoding: requestData, as: UTF8.self)

        // No locations are supplied, so accidentally dispatching to route cannot pass.
        XCTAssertNotNil(try object(actor.route(rawRequest: request))["code"])
        let trace = try XCTUnwrap(object(actor.traceRoute(rawRequest: request))["trip"] as? [String: Any])
        XCTAssertEqual(trace["status"] as? Int, 0)
        let traceLegs = try XCTUnwrap(trace["legs"] as? [[String: Any]])
        XCTAssertFalse(try XCTUnwrap(traceLegs.first?["shape"] as? String).isEmpty)
        var attributesRequest = try object(request)
        // TEST-INDEX-2: request the actual edge array so every genuine index has something to
        // be verified against. Without `edge.*` there is no denominator for the bound check.
        attributesRequest["filters"] = ["action": "include", "attributes": [
            "shape", "raw_score", "confidence_score", "edge.length", "matched.type",
            "matched.edge_index", "matched.distance_from_trace_point"
        ]]
        let attributesJSON = String(decoding: try JSONSerialization.data(withJSONObject: attributesRequest), as: UTF8.self)
        try assertNativeMatchEvidence(actor.traceAttributes(rawRequest: attributesJSON))
        XCTAssertNotNil(try object(actor.traceAttributes(rawRequest: "{"))["code"])
        try assertNativeMatchEvidence(actor.traceAttributes(rawRequest: attributesJSON))
        XCTAssertNotNil(try object(actor.traceRoute(rawRequest: "{"))["code"])
        XCTAssertNotNil(try object(actor.traceRoute(rawRequest: request))["trip"])
        XCTAssertNotNil(try object(actor.route(rawRequest: routeRequest))["trip"])
    }

    private func assertNativeMatchEvidence(_ json: String) throws {
        let evidence = try JSONDecoder().decode(Evidence.self, from: Data(json.utf8))
        XCTAssertTrue(evidence.raw_score.isFinite)
        // Best-candidate confidence is fixed at 1; preserve native semantics, not a probability.
        XCTAssertTrue(evidence.confidence_score.isFinite)
        XCTAssertFalse(evidence.shape.isEmpty)
        XCTAssertFalse(evidence.edges.isEmpty, "Index verification needs the response's own edges")
        for edge in evidence.edges { XCTAssertTrue(edge.length.isFinite && edge.length >= 0) }

        var links = 0
        var unassigned = 0
        for (position, point) in evidence.matched_points.enumerated() {
            XCTAssertTrue(["matched", "interpolated", "unmatched"].contains(point.type))
            guard point.type != "unmatched" else { continue }
            let distance = try XCTUnwrap(point.distance_from_trace_point)
            XCTAssertTrue(distance.isFinite && distance >= 0)
            let index = try XCTUnwrap(point.edge_index, "Sample \(position) is matched but carries no index")
            switch TraceIndexContract.classify(
                index, edgeCount: evidence.edges.count,
                unassigned: TraceIndexContract.nativeUnassigned
            ) {
            // Unassigned samples stay in order and are excluded from the actual-link count.
            case .unassigned: unassigned += 1
            case .link: links += 1
            case nil:
                XCTFail("Sample \(position) index is neither this ABI's marker nor an in-range edge")
            }
        }
        XCTAssertGreaterThanOrEqual(links, 2, "Successful matching needs two genuine road links")
        XCTAssertEqual(links + unassigned, evidence.matched_points.filter { $0.type != "unmatched" }.count)
    }

    /// Boundary cases for the index contract; no engine involvement, both ABI widths exercised.
    func testIndexContractBoundaries() throws {
        let edgeCount = 72
        for width in [TraceIndexContract.width64, TraceIndexContract.width32] {
            let other = width == TraceIndexContract.width64
                ? TraceIndexContract.width32 : TraceIndexContract.width64
            // The response's own array bounds the accepted range: 0 and 71 are genuine links.
            XCTAssertEqual(TraceIndexContract.classify(0, edgeCount: edgeCount, unassigned: width), .link(0))
            XCTAssertEqual(TraceIndexContract.classify(71, edgeCount: edgeCount, unassigned: width), .link(71))
            XCTAssertNil(TraceIndexContract.classify(72, edgeCount: edgeCount, unassigned: width))
            XCTAssertNil(TraceIndexContract.classify(1000, edgeCount: edgeCount, unassigned: width))
            // Each width's own marker is unassigned; it is never counted as a road link.
            XCTAssertEqual(TraceIndexContract.classify(width, edgeCount: edgeCount, unassigned: width), .unassigned)
            // The wrong-width marker is rejected outright rather than read as "unassigned".
            XCTAssertNil(TraceIndexContract.classify(other, edgeCount: edgeCount, unassigned: width))
            // Marker neighbours are out-of-range values, not markers.
            XCTAssertNil(TraceIndexContract.classify(width - 1, edgeCount: edgeCount, unassigned: width))
            if width != TraceIndexContract.width64 {
                XCTAssertNil(TraceIndexContract.classify(width + 1, edgeCount: edgeCount, unassigned: width))
            }
        }
        // All-unassigned matching fails the two-genuine-link requirement.
        let allUnassigned = (0..<3).map { _ in
            TraceIndexContract.classify(
                TraceIndexContract.nativeUnassigned, edgeCount: edgeCount,
                unassigned: TraceIndexContract.nativeUnassigned)
        }
        XCTAssertEqual(allUnassigned.filter { $0 == .unassigned }.count, 3)
        XCTAssertEqual(allUnassigned.filter { if case .link = $0 { return true }; return false }.count, 0)
    }

    /// Decoding rejects every non-integer spelling where an index is required.
    func testIndexDecodingRejectsCoercions() throws {
        // Quoted numbers, booleans, null, and negatives can never yield a usable index.
        for token in ["\"5\"", "\"18446744073709551615\"", "true", "false", "null", "-1",
                      "-18446744073709551615"] {
            let json = #"{"type":"matched","edge_index":\#(token),"distance_from_trace_point":1.0}"#
            XCTAssertNil((try? JSONDecoder().decode(Evidence.Match.self, from: Data(json.utf8)))?.edge_index ?? nil)
        }
        // Decimals and exponents must not round into a plausible index.
        for token in ["5.5", "71.9", "18446744073709551615.0"] {
            let json = #"{"type":"matched","edge_index":\#(token),"distance_from_trace_point":1.0}"#
            XCTAssertNil((try? JSONDecoder().decode(Evidence.Match.self, from: Data(json.utf8)))?.edge_index ?? nil)
        }
        // Exact wide integers survive without rounding.
        let exact = #"{"type":"matched","edge_index":18446744073709551615,"distance_from_trace_point":1.0}"#
        XCTAssertEqual(
            try JSONDecoder().decode(Evidence.Match.self, from: Data(exact.utf8)).edge_index,
            TraceIndexContract.width64)
        let near = #"{"type":"matched","edge_index":18446744073709551614,"distance_from_trace_point":1.0}"#
        XCTAssertEqual(
            try JSONDecoder().decode(Evidence.Match.self, from: Data(near.utf8)).edge_index,
            TraceIndexContract.width64 - 1)
    }
}
