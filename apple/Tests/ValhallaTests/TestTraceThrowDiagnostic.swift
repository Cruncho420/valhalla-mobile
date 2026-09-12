// PURPOSE: Expose the original C++ throw to an external debugger without altering matching.
// RESPONSIBILITY: Replay exact retained bytes after actor construction and a bounded rendezvous.
// DEPENDENCIES: XCTest, Valhalla, bundled Andorra graph, and explicit diagnostic environment.
// CONSUMERS: Manual owned-simulator LLDB evidence run; no product or acceptance behavior.
import XCTest
import ValhallaConfigModels
@testable import Valhalla

final class TestTraceThrowDiagnostic: XCTestCase {
    func testExactPrefixWithExternalThrowCapture() throws {
        let env = ProcessInfo.processInfo.environment
        guard let input = env["VALHALLA_THROW_REQUEST"],
              let directory = env["VALHALLA_THROW_DIRECTORY"] else {
            throw XCTSkip("Explicit throw diagnostic environment is required")
        }
        let output = URL(fileURLWithPath: directory)
        let bytes = try Data(contentsOf: URL(fileURLWithPath: input))
        XCTAssertLessThan(bytes.count, 1_048_576)
        let request = try XCTUnwrap(String(data: bytes, encoding: .utf8))
        try bytes.write(to: output.appendingPathComponent("request.json"), options: .withoutOverwriting)
        let tiles = Bundle.module.resourceURL!.appendingPathComponent("TestData/valhalla_tiles")
        let actor = try Valhalla(ValhallaConfig(tilesDir: tiles))
        try Data(String(ProcessInfo.processInfo.processIdentifier).utf8)
            .write(to: output.appendingPathComponent("ready.pid"), options: .withoutOverwriting)
        let deadline = Date().addingTimeInterval(90)
        let permission = output.appendingPathComponent("debugger-ready")
        while !FileManager.default.fileExists(atPath: permission.path), Date() < deadline {
            Thread.sleep(forTimeInterval: 0.1)
        }
        // Missing debugger admission is a diagnostic failure, not permission to change matching.
        XCTAssertTrue(FileManager.default.fileExists(atPath: permission.path))
        guard FileManager.default.fileExists(atPath: permission.path) else { return }
        let response = actor.traceAttributes(rawRequest: request)
        try Data(response.utf8).write(to: output.appendingPathComponent("response.json"),
                                     options: .withoutOverwriting)
        XCTAssertFalse(response.isEmpty)
    }
}
