// PURPOSE: Preserve frozen prospective trace inputs and every raw native return.
// RESPONSIBILITY: Capture only; no response-derived labels or consumer acceptance.
// DEPENDENCIES: XCTest, CryptoKit, the actual wrapper, and an explicitly staged source graph.
// CONSUMERS: Opt-in artifact-only native evidence workflow.
import CryptoKit
import XCTest
@testable import Valhalla

final class TestProspectiveTraceCapture: XCTestCase {
    private func refuse() -> NSError {
        NSError(domain: "ProspectiveCaptureIncomplete", code: 1)
    }

    private func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private func read(_ url: URL, limit: Int) throws -> Data {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .fileSizeKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true,
              let size = values.fileSize, size <= limit else { throw refuse() }
        let bytes = try Data(contentsOf: url)
        guard bytes.count <= limit else { throw refuse() }
        return bytes
    }

    func testFrozenProspectiveCapture() throws {
        let env = ProcessInfo.processInfo.environment
        guard let input = env["VALHALLA_PROSPECTIVE_INPUT"],
              let output = env["VALHALLA_PROSPECTIVE_OUTPUT"],
              let expectedStage = env["VALHALLA_PROSPECTIVE_STAGE_SHA256"] else {
            throw XCTSkip("Explicit prospective staging is required")
        }
        guard input.hasPrefix("/"), output.hasPrefix("/") else { throw refuse() }
        let abi: String
        #if targetEnvironment(simulator) && arch(arm64)
        abi = "arm64-ios-simulator"
        #elseif targetEnvironment(simulator) && arch(x86_64)
        abi = "x64-ios-simulator"
        #else
        throw refuse()
        #endif
        let root = URL(fileURLWithPath: input)
        let destination = URL(fileURLWithPath: output)
        guard !FileManager.default.fileExists(atPath: output) else { throw refuse() }
        let stage = try read(root.appendingPathComponent("stage.json"), limit: 262144)
        guard digest(stage) == expectedStage,
              let plan = try JSONSerialization.jsonObject(with: stage) as? [String: Any],
              let rows = plan["rows"] as? [[String: Any]], rows.count == 261 else { throw refuse() }
        let graph = try read(root.appendingPathComponent("graph.tar"), limit: 4194304)
        guard digest(graph) == "c0957c92bb71833ed3763e4b2c42a536cb28f2bcb69c991264532485edee75d4" else {
            throw refuse()
        }
        // Admit every input before entering the actor; never partially execute an edited stage.
        var requests = [(action: String, data: Data)]()
        for (index, row) in rows.enumerated() {
            guard let action = row["action"] as? String,
                  action == "trace_attributes" || action == "trace_route" else { throw refuse() }
            let kind = action == "trace_attributes" ? "diagnostic" : "request"
            guard let item = row[kind] as? [String: Any],
                  item["file"] as? String == String(format: "%03d.%@.json", index, kind),
                  let expected = item["sha256"] as? String else { throw refuse() }
            let data = try read(root.appendingPathComponent(String(format: "%03d.%@.json", index, kind)), limit: 8192)
            guard digest(data) == expected, String(data: data, encoding: .utf8) != nil else { throw refuse() }
            requests.append((action, data))
        }
        let template = try read(root.appendingPathComponent("config-template.json"), limit: 65536)
        guard digest(template) == plan["configTemplateSha256"] as? String,
              var config = try JSONSerialization.jsonObject(with: template) as? [String: Any],
              var mjolnir = config["mjolnir"] as? [String: Any] else { throw refuse() }
        mjolnir["tile_extract"] = root.appendingPathComponent("graph.tar").path
        config["mjolnir"] = mjolnir
        let configBytes = try JSONSerialization.data(withJSONObject: config, options: [.sortedKeys])
        try FileManager.default.createDirectory(at: destination, withIntermediateDirectories: false)
        func save(_ data: Data, _ name: String) throws {
            try data.write(to: destination.appendingPathComponent(name), options: [.withoutOverwriting])
        }
        try save(stage, "stage.json")
        try save(configBytes, "config.json")
        let actor = try Valhalla(configPath: destination.appendingPathComponent("config.json").path)
        var captured = [[String: Any]]()
        var total = 0
        for (index, request) in requests.enumerated() {
            try save(request.data, String(format: "%03d.request.json", index))
            let raw = String(data: request.data, encoding: .utf8)!
            let response: Data
            if request.action == "trace_attributes" {
                response = Data(actor.traceAttributes(rawRequest: raw).utf8)
            } else {
                response = Data(actor.traceRoute(rawRequest: raw).utf8)
            }
            total += response.count
            guard response.count <= 1048576, total <= 120 * 1048576 else { throw refuse() }
            try save(response, String(format: "%03d.response.raw", index))
            captured.append(["identity": rows[index], "requestSha256": digest(request.data),
                             "requestBytes": request.data.count, "responseSha256": digest(response),
                             "responseBytes": response.count, "returned": true])
        }
        // The executed XCTest bundle is the actual statically linked consumer executable.
        guard let executable = Bundle(for: Self.self).executableURL else { throw refuse() }
        let handle = try FileHandle(forReadingFrom: executable)
        defer { try? handle.close() }
        var executableHasher = SHA256()
        var executableBytes = 0
        while let chunk = try handle.read(upToCount: 65536), !chunk.isEmpty {
            executableBytes += chunk.count
            guard executableBytes <= 256 * 1048576 else { throw refuse() }
            executableHasher.update(data: chunk)
        }
        let executableSha = executableHasher.finalize().map { String(format: "%02x", $0) }.joined()
        let receipt: [String: Any] = ["version": 1, "complete": true, "platform": "ios", "abi": abi,
                                     "executableSha256": executableSha, "executableBytes": executableBytes,
                                     "stageSha256": digest(stage), "extractSha256": digest(graph),
                                     "configSha256": digest(configBytes), "rows": captured]
        try save(JSONSerialization.data(withJSONObject: receipt, options: [.sortedKeys]), "receipt.json")
    }
}
