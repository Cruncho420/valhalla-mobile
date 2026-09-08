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
        XCTAssertNotNil(try object(actor.traceRoute(rawRequest: "{"))["code"])
        XCTAssertNotNil(try object(actor.traceRoute(rawRequest: request))["trip"])
        XCTAssertNotNil(try object(actor.route(rawRequest: routeRequest))["trip"])
    }
}
