import AppKit
import Foundation
import SwiftUI

/// Menu-bar companion for desk-switch. Mirrors the Omarchy bar widget:
/// strip title is quiet `bar_strip` (focus + optional display mark).
/// `ui.tray.density == chips` paints the dense `bar_label` instead.
/// Chips stay in the click panel. Does not talk to adapters directly.

@main
struct DeskSwitchBarApp: App {
    @StateObject private var model = DeskSwitchModel()

    var body: some Scene {
        MenuBarExtra {
            DeskPanel(model: model)
        } label: {
            Text(model.stripTitle)
                .font(.system(size: 11, weight: .semibold, design: .monospaced))
        }
        .menuBarExtraStyle(.window)
    }
}

final class DeskSwitchModel: ObservableObject {
    @Published var hint = ""
    @Published var barLabel = "desk"
    @Published var stripTitle = "desk"
    @Published var summary = "Refresh to probe HHKB / MX / DualUp"
    @Published var hhkbLine = "HHKB  …"
    @Published var mouseLine = "MX  …"
    @Published var dualLine = "DU  …"
    @Published var peerLine = ""
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
                    self.barLabel = parsed.barLabel
                    self.stripTitle = parsed.stripTitle
                    self.summary = parsed.summary
                    self.hhkbLine = parsed.hhkbLine
                    self.mouseLine = parsed.mouseLine
                    self.dualLine = parsed.dualLine
                    self.peerLine = parsed.peerLine
                    self.dualUpAvailable = parsed.dualUpAvailable
                    self.lastLine = ""
                }
            } catch {
                DispatchQueue.main.async {
                    self.hint = ""
                    self.barLabel = "desk"
                    self.stripTitle = "desk"
                    self.summary = error.localizedDescription
                    self.hhkbLine = "HHKB  …"
                    self.mouseLine = "MX  …"
                    self.dualLine = "DU  …"
                    self.peerLine = ""
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
            Text("Desk → \(model.hint.isEmpty ? model.barLabel : model.hint)")
                .font(.system(size: 13, weight: .semibold))
            Text(model.barLabel)
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
            DeskChip(title: model.hhkbLine)
            DeskChip(title: model.mouseLine)
            DeskChip(title: model.dualLine)
            if !model.peerLine.isEmpty {
                DeskChip(title: model.peerLine)
            }
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
                DeskRow(title: "DualUp Full    ⌘⌥⇧F") { model.run(["full"]) }
                DeskRow(title: "DualUp PBP    ⌘⌥⇧P") { model.run(["pbp"]) }
                DeskRow(title: "Auto layout    ⌘⌥U") { model.run(["layout"]) }
            }
            Divider()
            DeskRow(title: "Quit") { NSApplication.shared.terminate(nil) }
        }
        .padding(12)
        .frame(width: 300)
        .onAppear { model.refresh() }
    }
}

struct DeskChip: View {
    let title: String

    var body: some View {
        Text(title)
            .font(.system(size: 11, design: .monospaced))
            .padding(.vertical, 4)
            .padding(.horizontal, 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.primary.opacity(0.07), in: RoundedRectangle(cornerRadius: 4))
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
    var hint = ""
    var barLabel = "desk"
    var stripTitle = "desk"
    var summary = "Refresh to probe HHKB / MX / DualUp"
    var hhkbLine = "HHKB  …"
    var mouseLine = "MX  …"
    var dualLine = "DU  …"
    var peerLine = ""
    var dualUpAvailable = false

    static func intValue(_ obj: [String: Any], _ key: String) -> Int? {
        if let n = obj[key] as? Int { return n }
        if let n = obj[key] as? NSNumber { return n.intValue }
        return nil
    }

    static func parse(_ raw: String) -> StatusSnapshot {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard text.first == "{",
              let data = text.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            let first = text.split(whereSeparator: { $0.isWhitespace }).first
            let hint = ["MAC", "LNX"].contains(first.map(String.init) ?? "") ? String(first!) : ""
            let title = hint.isEmpty ? "desk" : hint
            return StatusSnapshot(hint: hint, barLabel: title, stripTitle: title, summary: text.isEmpty ? "Refresh to probe HHKB / MX / DualUp" : text)
        }

        var hint = String(describing: obj["target_hint"] ?? "")
        if !["MAC", "LNX"].contains(hint) {
            hint = ""
        }

        let barLabel: String
        if let label = obj["bar_label"] as? String, !label.isEmpty, label != "?" {
            barLabel = label
        } else if !hint.isEmpty {
            barLabel = hint
        } else {
            barLabel = "desk"
        }

        var density = "strip"
        if let ui = obj["ui"] as? [String: Any],
           let tray = ui["tray"] as? [String: Any],
           let raw = tray["density"] as? String
        {
            density = raw.lowercased()
        }
        var stripFocus = hint
        var stripDisplay = ""
        if let strip = obj["bar_strip"] as? [String: Any] {
            let focus = strip["focus"] as? String ?? ""
            if ["MAC", "LNX"].contains(focus) {
                stripFocus = focus
            }
            stripDisplay = (strip["display"] as? String ?? "").lowercased()
        }
        let stripTitle: String
        if density == "chips" {
            stripTitle = barLabel
        } else {
            var parts: [String] = []
            if ["MAC", "LNX"].contains(stripFocus) {
                parts.append(stripFocus)
            }
            if stripDisplay == "pbp" {
                parts.append("PBP")
            } else if stripDisplay == "full" {
                parts.append("FULL")
            }
            stripTitle = parts.isEmpty ? "desk" : parts.joined(separator: "  ")
        }

        let usb = obj["hhkb_usb"] as? Bool ?? false
        let bluetooth = obj["hhkb_bluetooth"] as? Bool ?? false
        let present = obj["hhkb_present"] as? Bool ?? false
        let transport = obj["hhkb_transport"] as? String ?? "absent"
        let hhkbValue: String
        if usb {
            hhkbValue = "USB on this host"
        } else if bluetooth {
            hhkbValue = "BT only"
        } else if present {
            hhkbValue = "present · \(transport)"
        } else {
            hhkbValue = "absent"
        }

        let channel = intValue(obj, "mouse_channel")
        let host = (obj["mouse_host"] as? String ?? "").lowercased()
        let dest = host == "linux" ? "LNX" : (host == "mac" ? "MAC" : (host.isEmpty ? "?" : host.uppercased()))
        let online = obj["mouse_online"] as? Bool ?? false
        let mouseValue: String
        if let channel {
            mouseValue = "ch \(channel) → \(dest)" + (online ? " online" : " cached")
        } else {
            mouseValue = "missing"
        }

        var dual = obj["lgdualup"] as? Bool ?? false
        if let adapters = obj["adapters"] as? [String: Any],
           let dualup = adapters["dualup"] as? [String: Any],
           let available = dualup["available"] as? Bool
        {
            dual = available && (dualup["enabled"] as? Bool ?? true)
        }
        let mode = (obj["dualup_mode"] as? String ?? "unknown").lowercased()
        let modeKnown = mode == "pbp" || mode == "full"
        let modeLabel = mode == "pbp" ? "PBP" : (mode == "full" ? "FULL" : "unknown")
        dual = dual || modeKnown
        var macIn = "hdmi1"
        var lnxIn = "dp"
        if let inputs = obj["dualup_inputs"] as? [String: Any] {
            if let mac = inputs["mac"] as? String, !mac.isEmpty { macIn = mac }
            if let linux = inputs["linux"] as? String, !linux.isEmpty { lnxIn = linux }
        }

        var peerLine = ""
        if let peer = obj["peer"] as? [String: Any] {
            if peer["reachable"] as? Bool ?? false {
                let peerHost = peer["this_host"] as? String ?? (peer["peer"] as? String ?? "peer")
                let peerUsb = peer["hhkb_usb"] as? Bool ?? false
                let peerBt = peer["hhkb_bluetooth"] as? Bool ?? false
                let peerKb = peerUsb ? "USB" : (peerBt ? "BT" : (peer["hhkb_transport"] as? String ?? "?"))
                let peerCh = intValue(peer, "mouse_channel").map(String.init) ?? "?"
                peerLine = "PEER  \(peerHost) · HHKB \(peerKb) · mx \(peerCh)"
            } else if let name = peer["peer"] as? String {
                peerLine = "PEER  \(name) unreachable"
            }
        }

        let summary: String
        if let tip = obj["bar_tooltip"] as? String, !tip.isEmpty {
            summary = tip
        } else {
            summary = "\(hhkbValue) · \(mouseValue) · \(modeLabel)"
        }

        return StatusSnapshot(
            hint: hint,
            barLabel: barLabel,
            stripTitle: stripTitle,
            summary: summary,
            hhkbLine: "HHKB  \(hhkbValue)",
            mouseLine: "MX  \(mouseValue)",
            dualLine: "DU  \(modeLabel) · mac=\(macIn) linux=\(lnxIn)",
            peerLine: peerLine,
            dualUpAvailable: dual
        )
    }
}
