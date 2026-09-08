import XCTest
import ValhallaConfigModels
@testable import Valhalla

final class TestTraceRoute: XCTestCase {
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
        attributesRequest["filters"] = ["action": "include", "attributes": [
            "shape", "raw_score", "confidence_score", "matched.type",
            "matched.edge_index", "matched.distance_from_trace_point"
        ]]
        let attributesJSON = String(decoding: try JSONSerialization.data(withJSONObject: attributesRequest), as: UTF8.self)
        try assertNativeMatchEvidence(object(actor.traceAttributes(rawRequest: attributesJSON)))
        XCTAssertNotNil(try object(actor.traceAttributes(rawRequest: "{"))["code"])
        try assertNativeMatchEvidence(object(actor.traceAttributes(rawRequest: attributesJSON)))
        XCTAssertNotNil(try object(actor.traceRoute(rawRequest: "{"))["code"])
        XCTAssertNotNil(try object(actor.traceRoute(rawRequest: request))["trip"])
        XCTAssertNotNil(try object(actor.route(rawRequest: routeRequest))["trip"])
    }

    private func assertNativeMatchEvidence(_ response: [String: Any]) throws {
        XCTAssertTrue(try XCTUnwrap(response["raw_score"] as? Double).isFinite)
        // Best-candidate confidence is fixed at 1; preserve native semantics, not a probability.
        XCTAssertTrue(try XCTUnwrap(response["confidence_score"] as? Double).isFinite)
        XCTAssertFalse(try XCTUnwrap(response["shape"] as? String).isEmpty)
        let points = try XCTUnwrap(response["matched_points"] as? [[String: Any]])
        var matched = 0
        for point in points {
            let type = try XCTUnwrap(point["type"] as? String)
            XCTAssertTrue(["matched", "interpolated", "unmatched"].contains(type))
            if type != "unmatched" {
                matched += 1
                XCTAssertGreaterThanOrEqual(try XCTUnwrap(point["edge_index"] as? Int), 0)
                let distance = try XCTUnwrap(point["distance_from_trace_point"] as? Double)
                XCTAssertTrue(distance.isFinite && distance >= 0)
            }
        }
        XCTAssertGreaterThanOrEqual(matched, 2)
    }

}
