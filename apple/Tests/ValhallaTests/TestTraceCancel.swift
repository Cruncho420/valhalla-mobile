// PURPOSE: Prove the cancellable trace_attributes (Rods r6) stops at the engine's interrupt poll,
// stops ONLY the call carrying its token, and leaves the actor usable.
// RESPONSIBILITY: Deterministic cancel-before/cancel-other cases on the Andorra test tiles; the
// mid-flight "within one round" timing proof is the desktop harness's job (dense city window).
// DEPENDENCIES: Valhalla test tiles (Andorra); no network, no coordinate logging.
// CONSUMERS: Rods engine release r6 (FEAT-090 phone bound lane).
import XCTest
import ValhallaConfigModels
@testable import Valhalla

final class TestTraceCancel: XCTestCase {
    private func object(_ json: String) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: Any])
    }

    func testCancelStopsOnlyItsOwnCallAndTheActorSurvives() throws {
        let tiles = Bundle.module.resourceURL!.appendingPathComponent("TestData/valhalla_tiles")
        let actor = try Valhalla(ValhallaConfig(tilesDir: tiles))
        let routeRequest = #"{"locations":[{"lat":42.5063,"lon":1.5218},{"lat":42.5086,"lon":1.5394}],"costing":"auto"}"#
        let route = try XCTUnwrap(object(actor.route(rawRequest: routeRequest))["trip"] as? [String: Any])
        let shape = try XCTUnwrap((route["legs"] as? [[String: Any]])?.first?["shape"] as? String)
        let request = String(decoding: try JSONSerialization.data(withJSONObject: [
            "encoded_polyline": shape, "costing": "auto", "shape_match": "map_snap", "alternates": 1,
            "filters": ["action": "include", "attributes": ["shape", "raw_score", "matched.type"]],
        ]), as: UTF8.self)

        let plain = actor.traceAttributes(rawRequest: request)
        XCTAssertNotNil(try object(plain)["raw_score"])
        // An uncancelled token answers exactly what the plain call answers.
        XCTAssertEqual(actor.traceAttributes(rawRequest: request, token: 201), plain)
        // Cancelled before it starts: the first interrupt poll ends it.
        Valhalla.cancelTrace(202)
        XCTAssertEqual(try object(actor.traceAttributes(rawRequest: request, token: 202))["code"] as? Int, -2)
        // A cancel names one call: 203 runs to its answer.
        XCTAssertEqual(actor.traceAttributes(rawRequest: request, token: 203), plain)
        // Same actor after a cancel.
        XCTAssertEqual(actor.traceAttributes(rawRequest: request), plain)
        XCTAssertNotNil(try object(actor.route(rawRequest: routeRequest))["trip"])
    }
}
