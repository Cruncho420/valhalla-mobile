// PURPOSE: Retain native discontinuity and alternate-path evidence within service limits.
// RESPONSIBILITY: Exercise synthetic variants without claiming independent road ground truth.
// DEPENDENCIES: XCTest, Valhalla, bundled Andorra graph, and retained original fixture JSON.
// CONSUMERS: Explicit simulator evidence runs; no product acceptance thresholds are changed.
import XCTest
import ValhallaConfigModels
@testable import Valhalla

final class TestTraceDiscontinuityEvidence: XCTestCase {
    private struct Point: Codable { let lat: Double; let lon: Double }
    private struct Input: Decodable { let shape: [Point] }
    private struct Path: Decodable {
        struct Match: Decodable {
            let type: String
            let edge_index: UInt64?
            let begin_route_discontinuity: Bool?
            let end_route_discontinuity: Bool?
        }
        let matched_points: [Match]
        let confidence_score: Double
        let raw_score: Double
        let alternate_paths: [Path]?
    }

    func testBoundedOriginalChunksAndSyntheticGaps() throws {
        let env = ProcessInfo.processInfo.environment
        guard let source = env["VALHALLA_TRACE_SOURCE_DIR"],
              let destination = env["VALHALLA_TRACE_EVIDENCE_DIR"] else {
            throw XCTSkip("Explicit retained fixture and evidence directories are required")
        }
        let output = URL(fileURLWithPath: destination)
            .appendingPathComponent("bounded-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let clean = try input(source, "clean-request.json")
        let noisy = try input(source, "modest-noise-request.json")
        XCTAssertEqual(clean.count, 196)
        XCTAssertEqual(noisy.count, clean.count)
        var cases: [(String, [Point], Bool)] = []
        for (name, points) in [("clean", clean), ("noisy", noisy)] {
            cases.append((name + "-first100", Array(points.prefix(100)), false))
            cases.append((name + "-last97", Array(points.suffix(97)), false))
        }
        cases.append(("short-tail3", Array(clean.suffix(3)), false))
        cases.append(("reversed80", Array(clean.prefix(80).reversed()), false))
        cases.append(("loop80", Array(clean.prefix(40)) + Array(clean.prefix(40).reversed()), false))
        // Offsets deliberately generate missing observations, not independently labelled roads.
        for offset in [0.02, 0.05, 0.15] {
            let origin = clean[99]
            let gap = [Point(lat: origin.lat + offset, lon: origin.lon + offset),
                       Point(lat: origin.lat + offset + 0.0001, lon: origin.lon + offset)]
            cases.append(("partial-gap-" + String(offset),
                          Array(clean.prefix(30)) + gap + Array(clean.suffix(30)), true))
        }
        let tiles = Bundle.module.resourceURL!.appendingPathComponent("TestData/valhalla_tiles")
        let actor = try Valhalla(ValhallaConfig(tilesDir: tiles))
        for (name, points, permitsError) in cases {
            try capture(actor, name, points, permitsError, output)
        }
    }

    func testPrefixControlsAndActorReuse() throws {
        let env = ProcessInfo.processInfo.environment
        guard let source = env["VALHALLA_TRACE_SOURCE_DIR"],
              let destination = env["VALHALLA_TRACE_EVIDENCE_DIR"] else {
            throw XCTSkip("Explicit retained fixture and evidence directories are required")
        }
        let output = URL(fileURLWithPath: destination)
            .appendingPathComponent("prefix-controls-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        let tiles = Bundle.module.resourceURL!.appendingPathComponent("TestData/valhalla_tiles")
        let actor = try Valhalla(ValhallaConfig(tilesDir: tiles))
        for (name, file) in [("clean", "clean-request.json"), ("noisy", "modest-noise-request.json")] {
            let points = try input(source, file)
            try capture(actor, name + "-prefix100-no-alternates", Array(points.prefix(100)), false,
                        output, alternates: 0)
            try capture(actor, name + "-prefix99", Array(points.prefix(99)), false, output)
            try capture(actor, name + "-prefix50", Array(points.prefix(50)), false, output)
            try capture(actor, name + "-reused-last97", Array(points.suffix(97)), false, output)
        }
    }

    private func input(_ root: String, _ name: String) throws -> [Point] {
        let data = try Data(contentsOf: URL(fileURLWithPath: root).appendingPathComponent(name))
        XCTAssertLessThan(data.count, 1_048_576)
        return try JSONDecoder().decode(Input.self, from: data).shape
    }

    private func capture(_ actor: Valhalla, _ name: String, _ points: [Point],
                         _ permitsError: Bool, _ output: URL, alternates: Int = 2) throws {
        XCTAssertTrue((2...100).contains(points.count))
        let distance = zip(points, points.dropFirst()).reduce(0.0) { sum, pair in
            // Conservative latitude/longitude distance upper bound, in metres.
            sum + 111_320 * (abs(pair.0.lat - pair.1.lat) + abs(pair.0.lon - pair.1.lon))
        }
        XCTAssertLessThan(distance, 200_000)
        let request: [String: Any] = [
            "shape": try JSONSerialization.jsonObject(with: JSONEncoder().encode(points)),
            "costing": "auto", "shape_match": "map_snap", "alternates": alternates,
            "filters": ["action": "include", "attributes": [
                "shape", "raw_score", "confidence_score", "edge.begin_shape_index",
                "edge.end_shape_index", "edge.length", "matched.type", "matched.edge_index",
                "matched.distance_from_trace_point", "matched.begin_route_discontinuity",
                "matched.end_route_discontinuity"]]]
        let bytes = try JSONSerialization.data(withJSONObject: request, options: .sortedKeys)
        try bytes.write(to: output.appendingPathComponent(name + "-request.json"), options: .withoutOverwriting)
        let raw = actor.traceAttributes(rawRequest: String(decoding: bytes, as: UTF8.self))
        XCTAssertLessThan(raw.utf8.count, 1_048_576)
        let data = Data(raw.utf8)
        try data.write(to: output.appendingPathComponent(name + "-attributes.json"), options: .withoutOverwriting)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        if let code = object["code"] as? Int {
            XCTAssertTrue(permitsError, "Unexpected native error for " + name)
            XCTAssertNotEqual(code, 154, "Distance rejection is not discontinuity evidence")
            return
        }
        let path = try JSONDecoder().decode(Path.self, from: data)
        XCTAssertEqual(path.matched_points.count, points.count)
        XCTAssertTrue(path.confidence_score.isFinite && path.raw_score.isFinite)
        XCTAssertLessThanOrEqual(path.alternate_paths?.count ?? 0, 2)
        // Presence and true counts are recorded from raw output; absence is not invented as false.
        for alternate in path.alternate_paths ?? [] {
            XCTAssertEqual(alternate.matched_points.count, points.count)
            XCTAssertTrue(alternate.confidence_score.isFinite && alternate.raw_score.isFinite)
        }
    }
}
