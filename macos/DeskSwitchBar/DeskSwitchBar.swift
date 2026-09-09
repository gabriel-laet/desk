import AppKit
import Foundation
import SwiftUI

/// Menu-bar companion for desk-switch. Mirrors the Omarchy bar widget:
/// title is MAC / LNX / ?, menu runs the same `desk-switch` actions.
/// Does not talk to mxswitch / lgdualup directly.

@main
struct DeskSwitchBarApp: App {
    @StateObject private var model = DeskSwitchModel()

    var body: some Scene {
        MenuBarExtra {
            DeskPanel(model: model)
        } label: {
            Text(model.hint)
                .font(.system(size: 12, weight: .semibold, design: .monospaced))
        }
        .menuBarExtraStyle(.window)
    }
}

final class DeskSwitchModel: ObservableObject {
    @Published var hint = "?"
    @Published var summary = "Refresh to probe HHKB / MX / DualUp"
    @Published var dualUpAvailable = false
    @Published var lastLine = ""

    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            self?.refresh()
        }
        if let timer {
            RunLoop.main.add(timer, forMode: .common)
        }
    }

    func refresh() {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                let raw = try DeskSwitchCLI.run(["status", "--json"])
                let parsed = StatusSnapshot.parse(raw)
                DispatchQueue.main.async {
                    self.hint = parsed.hint
                    self.summary = parsed.summary
                    self.dualUpAvailable = parsed.dualUpAvailable
                    self.lastLine = ""
                }
            } catch {
                DispatchQueue.main.async {
                    self.hint = "?"
                    self.summary = error.localizedDescription
                    self.dualUpAvailable = false
                }
            }
        }
    }

    func run(_ args: [String]) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                let out = try DeskSwitchCLI.run(args)
                DispatchQueue.main.async {
                    self.lastLine = out.trimmingCharacters(in: .whitespacesAndNewlines)
                    self.refresh()
                }
            } catch {
                DispatchQueue.main.async {
                    self.lastLine = error.localizedDescription
                    self.refresh()
                }
            }
        }
    }
}

struct DeskPanel: View {
    @ObservedObject var model: DeskSwitchModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Desk → \(model.hint)")
                .font(.system(size: 13, weight: .semibold))
            Text(model.summary)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if !model.lastLine.isEmpty {
                Text(model.lastLine)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            DeskRow(title: "Refresh status") { model.refresh() }
            DeskRow(title: "Switch to Mac") { model.run(["to", "mac"]) }
            DeskRow(title: "Switch to Linux") { model.run(["to", "linux"]) }
            if model.dualUpAvailable {
                DeskRow(title: "DualUp Full") { model.run(["full"]) }
                DeskRow(title: "DualUp PBP") { model.run(["pbp"]) }
            }
            Divider()
            DeskRow(title: "Quit") { NSApplication.shared.terminate(nil) }
        }
        .padding(12)
        .frame(width: 260)
        .onAppear { model.refresh() }
    }
}

struct DeskRow: View {
    let title: String
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .buttonStyle(.plain)
        .padding(.vertical, 5)
        .padding(.horizontal, 8)
        .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 6))
    }
}

enum DeskSwitchCLI {
    enum CLIError: LocalizedError {
        case missing
        case timeout
        case failed(String)

        var errorDescription: String? {
            switch self {
            case .missing:
                return "desk-switch not found — run make install (PATH or ~/.local/bin)"
            case .timeout:
                return "desk-switch timed out"
            case .failed(let message):
                return message
            }
        }
    }

    static func locate() -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for name in ["desk-switch", "hhkb-mx-follow"] {
            let path = "\(home)/.local/bin/\(name)"
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        for name in ["desk-switch", "hhkb-mx-follow"] {
            if let found = which(name) {
                return found
            }
        }
        return nil
    }

    static func which(_ name: String) -> String? {
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        proc.arguments = [name]
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = pathWithLocalBin(env["PATH"])
        proc.environment = env
        let out = Pipe()
        proc.standardOutput = out
        proc.standardError = Pipe()
        do {
            try proc.run()
            proc.waitUntilExit()
        } catch {
            return nil
        }
        guard proc.terminationStatus == 0 else { return nil }
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return text.isEmpty ? nil : text
    }

    static func pathWithLocalBin(_ path: String?) -> String {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let extra = "\(home)/.local/bin"
        guard let path, !path.isEmpty else { return extra }
        if path.split(separator: ":").contains(where: { String($0) == extra }) {
            return path
        }
        return "\(extra):\(path)"
    }

    static func run(_ arguments: [String], timeout: TimeInterval = 20) throws -> String {
        guard let exe = locate() else { throw CLIError.missing }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: exe)
        proc.arguments = arguments
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = pathWithLocalBin(env["PATH"])
        proc.environment = env
        let out = Pipe()
        let err = Pipe()
        proc.standardOutput = out
        proc.standardError = err
        try proc.run()

        let group = DispatchGroup()
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            proc.waitUntilExit()
            group.leave()
        }
        if group.wait(timeout: .now() + timeout) == .timedOut {
            proc.terminate()
            throw CLIError.timeout
        }

        let stdout = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let stderr = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        if proc.terminationStatus != 0, stdout.isEmpty, !stderr.isEmpty {
            throw CLIError.failed(stderr.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return stdout.isEmpty ? stderr : stdout
    }
}

struct StatusSnapshot {
    var hint = "?"
    var summary = "Refresh to probe HHKB / MX / DualUp"
    var dualUpAvailable = false

    static func parse(_ raw: String) -> StatusSnapshot {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.first == "{",
              let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            let first = text.split(whereSeparator: { $0.isWhitespace }).first
            let hint = ["MAC", "LNX", "?"].contains(first.map(String.init) ?? "") ? String(first!) : "?"
            return StatusSnapshot(hint: hint, summary: text.isEmpty ? "Refresh to probe HHKB / MX / DualUp" : text)
        }

        var hint = String(describing: obj["target_hint"] ?? "?")
        if !["MAC", "LNX", "?"].contains(hint) {
            hint = "?"
        }

        let hhkb = String(describing: obj["hhkb"] ?? "?")
        let channel: String
        if let n = obj["mouse_channel"] as? Int {
            channel = "ch \(n)"
        } else if let n = obj["mouse_channel"] as? NSNumber {
            channel = "ch \(n.intValue)"
        } else {
            channel = "ch ?"
        }

        var dual = obj["lgdualup"] as? Bool ?? false
        if let adapters = obj["adapters"] as? [String: Any],
           let dualup = adapters["dualup"] as? [String: Any],
           let available = dualup["available"] as? Bool
        {
            dual = available && (dualup["enabled"] as? Bool ?? true)
        }

        let dualLabel = dual ? "lgdualup" : "no DualUp"
        return StatusSnapshot(
            hint: hint,
            summary: "\(hhkb) · \(channel) · \(dualLabel)",
            dualUpAvailable: dual
        )
    }
}
