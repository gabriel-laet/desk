import AppKit
import Foundation
import SwiftUI

/// Menu-bar companion for desk (`desk-switch` CLI).
/// Strip: quiet `bar_strip`, or a composite NSImage of `slots`
/// (MenuBarExtra flattens nested Image+Text to one symbol — draw one image).
/// HUD: generic Watch-style face from the same slots. No adapter-named chrome.

@main
struct DeskSwitchBarApp: App {
    @StateObject private var model = DeskSwitchModel()

    var body: some Scene {
        MenuBarExtra {
            DeskPanel(model: model)
        } label: {
            MenuBarLabel(model: model)
        }
        .menuBarExtraStyle(.window)
    }
}

struct MenuBarLabel: View {
    @ObservedObject var model: DeskSwitchModel

    var body: some View {
        Group {
            if model.visibleSlots.isEmpty {
                Text(model.stripTitle)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
            } else {
                Image(nsImage: MenuBarStatusItemRenderer.image(for: model.visibleSlots, compact: model.ui.compact))
                    .renderingMode(.original)
                    .help(model.summary)
                    .accessibilityLabel(model.slotAccessibility)
                    .id(model.slotSetID)
            }
        }
    }
}

/// Semantic glyph tokens from core → SF Symbols. Not adapter ids.
enum SlotGlyphMap {
    static func symbolName(for glyph: String) -> String {
        switch glyph {
        case "mug": return "cup.and.saucer"
        case "flame": return "flame"
        case "cloud.rain": return "cloud.rain"
        case "cloud": return "cloud"
        case "sun.max": return "sun.max"
        case "moon.stars": return "moon.stars"
        case "cloud.fog": return "cloud.fog"
        case "cloud.bolt.rain": return "cloud.bolt.rain"
        case "display.split": return "rectangle.split.1x2"
        case "display.full": return "rectangle"
        case "light.on": return "lightbulb.fill"
        case "light.off": return "lightbulb"
        default: return glyph.isEmpty ? "circle" : glyph
        }
    }
}

enum MenuBarStatusItemRenderer {
    private static var cache: ([TraySlot], Bool, NSImage)?

    static func image(for slots: [TraySlot], compact: Bool = false) -> NSImage {
        if let cache, cache.0 == slots, cache.1 == compact {
            return cache.2
        }
        let image = draw(slots, compact: compact)
        cache = (slots, compact, image)
        return image
    }

    private static func draw(_ slots: [TraySlot], compact: Bool) -> NSImage {
        let point: CGFloat = compact ? 12 : 13
        let symbolConfig = NSImage.SymbolConfiguration(pointSize: point, weight: .semibold)
        let textFont = NSFont.monospacedDigitSystemFont(ofSize: point, weight: .semibold)
        let textAttrs: [NSAttributedString.Key: Any] = [
            .font: textFont,
            .foregroundColor: NSColor.labelColor,
        ]
        let glyphTemp: CGFloat = 3
        let pairGap: CGFloat = compact ? 6 : 8

        var measured: [(icon: NSImage, text: NSAttributedString)] = []
        var width: CGFloat = 0
        var height: CGFloat = 18
        for (index, slot) in slots.enumerated() {
            let icon = NSImage(systemSymbolName: SlotGlyphMap.symbolName(for: slot.glyph), accessibilityDescription: nil)?
                .withSymbolConfiguration(symbolConfig)
                ?? NSImage(size: NSSize(width: 13, height: 13))
            let text = NSAttributedString(string: slot.label, attributes: textAttrs)
            let textSize = text.size()
            height = max(height, icon.size.height, textSize.height)
            if index > 0 {
                width += pairGap
            }
            width += icon.size.width + glyphTemp + textSize.width
            measured.append((icon, text))
        }

        let size = NSSize(width: max(ceil(width), 1), height: max(ceil(height), 18))
        let image = NSImage(size: size)
        image.lockFocus()
        var x: CGFloat = 0
        for (index, item) in measured.enumerated() {
            if index > 0 {
                x += pairGap
            }
            let iconSize = item.icon.size
            item.icon.draw(
                in: NSRect(x: x, y: (size.height - iconSize.height) / 2, width: iconSize.width, height: iconSize.height),
                from: .zero,
                operation: .sourceOver,
                fraction: 1
            )
            x += iconSize.width + glyphTemp
            let textSize = item.text.size()
            item.text.draw(at: NSPoint(x: x, y: (size.height - textSize.height) / 2))
            x += textSize.width
        }
        image.unlockFocus()
        image.isTemplate = true
        return image
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
    @Published var slots: [TraySlot] = []
    @Published var ui = DeskUIConfig.defaults
    @Published var configPath = ""
    @Published var showingSettings = false

    var visibleSlots: [TraySlot] {
        DeskUIConfig.apply(slots, ui: ui)
    }

    var slotSetID: String {
        visibleSlots.map { "\($0.id):\($0.glyph):\($0.label)" }.joined(separator: "|")
    }

    var slotAccessibility: String {
        visibleSlots.map { "\($0.label)" }.joined(separator: ", ")
    }

    var faceSlot: TraySlot? {
        guard ui.showFaces else { return visibleSlots.first }
        return visibleSlots.first(where: { $0.face }) ?? visibleSlots.first
    }

    var complicationSlots: [TraySlot] {
        guard ui.showFaces, let face = faceSlot else { return [] }
        return visibleSlots.filter { $0.id != face.id }
    }

    var slotActions: [SlotAction] {
        visibleSlots.flatMap(\.actions)
    }

    private var timer: Timer?

    init() {
        refresh()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] in
            _ = $0
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
                    self.slots = parsed.slots
                    self.ui = parsed.ui.merging(live: parsed.slots)
                    self.configPath = parsed.configPath
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
                    self.slots = []
                    self.ui = DeskUIConfig.defaults
                    self.configPath = ""
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

    func persistUI() {
        do {
            try DeskUIConfigStore.save(ui, to: configPath)
            refresh()
        } catch {
            lastLine = error.localizedDescription
        }
    }

    func moveSlots(from source: IndexSet, to destination: Int) {
        ui.slots.move(fromOffsets: source, toOffset: destination)
        persistUI()
    }

    func setSlotEnabled(id: String, enabled: Bool) {
        if let index = ui.slots.firstIndex(where: { $0.id == id }) {
            ui.slots[index].enabled = enabled
        }
        if id == "lights" {
            ui.lights = enabled
        }
        persistUI()
    }
}

struct DeskPanel: View {
    @ObservedObject var model: DeskSwitchModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if model.showingSettings {
                TraySettingsPanel(model: model)
            } else {
                deskHome
            }
        }
        .padding(12)
        .frame(width: 300)
        .onAppear { model.refresh() }
    }

    @ViewBuilder
    private var deskHome: some View {
        if !model.visibleSlots.isEmpty {
            SlotHUD(model: model)
        }
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
        ForEach(model.visibleSlots) { slot in
            DeskChip(title: slot.chipTitle)
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
        if !model.slotActions.isEmpty {
            HStack(spacing: 8) {
                ForEach(Array(model.slotActions.enumerated()), id: \.offset) { _, action in
                    Button {
                        model.run(action.argv)
                    } label: {
                        Text(action.label)
                            .font(.system(size: 11, weight: .semibold))
                            .frame(maxWidth: .infinity)
                            .frame(height: 36)
                    }
                    .buttonStyle(WatchOrbStyle(accent: Color.primary, filled: false, shape: .capsule))
                }
            }
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
        DeskRow(title: "Configure tray") { model.showingSettings = true }
        DeskRow(title: "Quit") { NSApplication.shared.terminate(nil) }
    }
}

struct SlotHUD: View {
    @ObservedObject var model: DeskSwitchModel

    private var hudSize: CGFloat { model.ui.compact ? 140 : 176 }
    private var faceFont: CGFloat { model.ui.compact ? 28 : 36 }

    var body: some View {
        Button {
            model.refresh()
        } label: {
            if model.ui.showFaces {
                watchFace
            } else {
                slotStack
            }
        }
        .buttonStyle(.plain)
        .frame(maxWidth: .infinity)
        .accessibilityLabel(model.slotAccessibility)
    }

    private var watchFace: some View {
        ZStack {
            Circle()
                .fill(Color.primary.opacity(0.06))
            Circle()
                .stroke(Color.primary.opacity(0.08), lineWidth: 9)
            Circle()
                .trim(from: 0, to: CGFloat(model.faceSlot?.progress ?? 0))
                .stroke(
                    AngularGradient(
                        colors: [
                            Color.orange.opacity(0.35),
                            Color.orange,
                        ],
                        center: .center
                    ),
                    style: StrokeStyle(lineWidth: 9, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
            VStack(spacing: 3) {
                if let face = model.faceSlot {
                    Image(systemName: SlotGlyphMap.symbolName(for: face.glyph))
                        .font(.system(size: 15, weight: .semibold))
                        .symbolRenderingMode(.hierarchical)
                    Text(face.label)
                        .font(.system(size: faceFont, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                        .minimumScaleFactor(0.7)
                        .lineLimit(1)
                    if let detail = face.detail, !detail.isEmpty {
                        Text(detail)
                            .font(.system(size: 11, weight: .medium, design: .rounded))
                            .foregroundStyle(.secondary)
                            .lineLimit(2)
                            .multilineTextAlignment(.center)
                    }
                }
                if !model.complicationSlots.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(model.complicationSlots) { slot in
                            HStack(spacing: 3) {
                                Image(systemName: SlotGlyphMap.symbolName(for: slot.glyph))
                                    .symbolRenderingMode(.hierarchical)
                                Text(slot.label)
                                    .monospacedDigit()
                            }
                        }
                    }
                    .font(.system(size: 11, weight: .semibold, design: .rounded))
                    .foregroundStyle(.secondary)
                }
            }
        }
        .frame(width: hudSize, height: hudSize)
        .contentShape(Circle())
    }

    private var slotStack: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(model.visibleSlots) { slot in
                HStack(spacing: 6) {
                    Image(systemName: SlotGlyphMap.symbolName(for: slot.glyph))
                        .symbolRenderingMode(.hierarchical)
                    Text(slot.label)
                        .font(.system(size: 13, weight: .semibold, design: .rounded))
                        .monospacedDigit()
                    if let detail = slot.detail, !detail.isEmpty {
                        Text(detail)
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct WatchOrbStyle: ButtonStyle {
    enum ShapeKind {
        case circle
        case capsule
    }

    var accent: Color
    var filled: Bool
    var shape: ShapeKind = .circle

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(filled ? Color.white : accent)
            .background {
                Group {
                    if shape == .capsule {
                        Capsule()
                            .fill(filled ? accent : accent.opacity(configuration.isPressed ? 0.16 : 0.08))
                    } else {
                        Circle()
                            .fill(filled ? accent : accent.opacity(configuration.isPressed ? 0.16 : 0.08))
                    }
                }
            }
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

struct TraySettingsPanel: View {
    @ObservedObject var model: DeskSwitchModel

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Tray slots")
                .font(.system(size: 13, weight: .semibold))
            Text("Drag to reorder. Same file Omarchy will read.")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            List {
                ForEach(model.ui.slots) { slot in
                    HStack(spacing: 8) {
                        Image(systemName: "line.3.horizontal")
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundStyle(.secondary)
                        Text(DeskUIConfig.title(for: slot.id))
                            .font(.system(size: 12, weight: .medium))
                        Spacer()
                        Toggle("", isOn: enabledBinding(slot.id))
                            .labelsHidden()
                            .toggleStyle(.switch)
                            .controlSize(.mini)
                    }
                    .listRowInsets(EdgeInsets(top: 3, leading: 2, bottom: 3, trailing: 2))
                }
                .onMove(perform: model.moveSlots)
            }
            .listStyle(.plain)
            .frame(height: CGFloat(max(model.ui.slots.count, 1)) * 34)
            Text("HUD")
                .font(.system(size: 13, weight: .semibold))
            Toggle("Show altitude", isOn: hudBinding(\.showAltitude))
                .font(.system(size: 12))
            Toggle("Watch faces", isOn: hudBinding(\.showFaces))
                .font(.system(size: 12))
            Toggle("Compact size", isOn: compactBinding)
                .font(.system(size: 12))
            Picker("Bar title", selection: densityBinding) {
                Text("Quiet").tag("strip")
                Text("Chips").tag("chips")
            }
            .pickerStyle(.segmented)
            .controlSize(.small)
            if !model.lastLine.isEmpty {
                Text(model.lastLine)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            DeskRow(title: "Done") { model.showingSettings = false }
        }
    }

    private func enabledBinding(_ id: String) -> Binding<Bool> {
        Binding(
            get: { model.ui.slots.first(where: { $0.id == id })?.enabled ?? false },
            set: { model.setSlotEnabled(id: id, enabled: $0) }
        )
    }

    private func hudBinding(_ keyPath: WritableKeyPath<DeskUIConfig, Bool>) -> Binding<Bool> {
        Binding(
            get: { model.ui[keyPath: keyPath] },
            set: { value in
                model.ui[keyPath: keyPath] = value
                model.persistUI()
            }
        )
    }

    private var compactBinding: Binding<Bool> {
        Binding(
            get: { model.ui.compact },
            set: { value in
                model.ui.hudDensity = value ? "compact" : "regular"
                model.persistUI()
            }
        )
    }

    private var densityBinding: Binding<String> {
        Binding(
            get: { model.ui.trayDensity },
            set: { value in
                model.ui.trayDensity = value
                model.persistUI()
            }
        )
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

struct SlotAction: Equatable {
    var label: String
    var argv: [String]
}

struct TraySlot: Identifiable, Equatable {
    var id: String
    var glyph: String
    var label: String
    var detail: String?
    var hot: Bool
    var face: Bool
    var progress: Double
    var actions: [SlotAction]

    var chipTitle: String {
        if let detail, !detail.isEmpty {
            return "\(label)  \(detail)"
        }
        return label
    }

    static func parse(_ raw: Any) -> TraySlot? {
        guard let obj = raw as? [String: Any] else { return nil }
        let id = obj["id"] as? String ?? ""
        let glyph = obj["glyph"] as? String ?? ""
        let label = obj["label"] as? String ?? ""
        guard !id.isEmpty, !label.isEmpty || !glyph.isEmpty else { return nil }
        var actions: [SlotAction] = []
        if let items = obj["actions"] as? [[String: Any]] {
            for item in items {
                let argv = (item["argv"] as? [Any])?.map { String(describing: $0) } ?? []
                guard !argv.isEmpty else { continue }
                actions.append(SlotAction(label: item["label"] as? String ?? argv[0], argv: argv))
            }
        }
        var progress = 0.0
        if let n = obj["progress"] as? Double {
            progress = n
        } else if let n = obj["progress"] as? NSNumber {
            progress = n.doubleValue
        }
        return TraySlot(
            id: id,
            glyph: glyph,
            label: label.isEmpty ? id : label,
            detail: obj["detail"] as? String,
            hot: obj["hot"] as? Bool ?? false,
            face: obj["face"] as? Bool ?? false,
            progress: min(max(progress, 0), 1),
            actions: actions
        )
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
    var slots: [TraySlot] = []
    var ui = DeskUIConfig.defaults
    var configPath = ""

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

        var slots: [TraySlot] = []
        if let rawSlots = obj["slots"] as? [Any] {
            slots = rawSlots.compactMap(TraySlot.parse)
        }
        let ui = DeskUIConfig.parse(obj["ui"]).merging(live: slots)
        let configPath = obj["config"] as? String ?? ""

        return StatusSnapshot(
            hint: hint,
            barLabel: barLabel,
            stripTitle: stripTitle,
            summary: summary,
            hhkbLine: "HHKB  \(hhkbValue)",
            mouseLine: "MX  \(mouseValue)",
            dualLine: "DU  \(modeLabel) · mac=\(macIn) linux=\(lnxIn)",
            peerLine: peerLine,
            dualUpAvailable: dual,
            slots: slots,
            ui: ui,
            configPath: configPath
        )
    }
}

struct UISlotPref: Identifiable, Equatable {
    var id: String
    var enabled: Bool
}

struct DeskUIConfig: Equatable {
    var slots: [UISlotPref]
    var lights: Bool
    var trayDensity: String
    var showAltitude: Bool
    var showFaces: Bool
    var hudDensity: String

    var compact: Bool { hudDensity == "compact" }

    static let defaultSlots: [UISlotPref] = [
        UISlotPref(id: "weather", enabled: true),
        UISlotPref(id: "kettle", enabled: true),
        UISlotPref(id: "dualup", enabled: true),
        UISlotPref(id: "lights", enabled: false),
    ]

    static let defaults = DeskUIConfig(
        slots: defaultSlots,
        lights: false,
        trayDensity: "strip",
        showAltitude: true,
        showFaces: true,
        hudDensity: "regular"
    )

    static func title(for id: String) -> String {
        switch id {
        case "weather": return "Weather"
        case "kettle": return "Kettle"
        case "dualup": return "Display"
        case "lights": return "Lights"
        default:
            return id.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    static func parse(_ raw: Any?) -> DeskUIConfig {
        var cfg = DeskUIConfig.defaults
        guard let ui = raw as? [String: Any] else { return cfg }
        let tray = ui["tray"] as? [String: Any] ?? [:]
        let hud = ui["hud"] as? [String: Any] ?? [:]
        if let density = tray["density"] as? String, !density.isEmpty {
            cfg.trayDensity = density.lowercased() == "chips" ? "chips" : "strip"
        }
        cfg.lights = tray["lights"] as? Bool ?? false
        if let rawSlots = tray["slots"] as? [Any] {
            var parsed: [UISlotPref] = []
            var seen = Set<String>()
            for item in rawSlots {
                if let id = item as? String, !id.isEmpty, !seen.contains(id) {
                    parsed.append(UISlotPref(id: id, enabled: true))
                    seen.insert(id)
                } else if let obj = item as? [String: Any], let id = obj["id"] as? String, !id.isEmpty, !seen.contains(id) {
                    parsed.append(UISlotPref(id: id, enabled: obj["enabled"] as? Bool ?? true))
                    seen.insert(id)
                }
            }
            if !parsed.isEmpty {
                for item in defaultSlots where !seen.contains(item.id) {
                    var extra = item
                    if extra.id == "lights" {
                        extra.enabled = cfg.lights
                    }
                    parsed.append(extra)
                }
                cfg.slots = parsed
            }
        } else if cfg.lights {
            cfg.slots = defaultSlots.map { slot in
                var next = slot
                if next.id == "lights" { next.enabled = true }
                return next
            }
        }
        if let value = hud["show_altitude"] as? Bool {
            cfg.showAltitude = value
        }
        if let value = hud["show_faces"] as? Bool {
            cfg.showFaces = value
        }
        if let density = hud["density"] as? String {
            cfg.hudDensity = density.lowercased() == "compact" ? "compact" : "regular"
        }
        return cfg
    }

    func merging(live slots: [TraySlot]) -> DeskUIConfig {
        var next = self
        var seen = Set(next.slots.map(\.id))
        for slot in slots where !seen.contains(slot.id) {
            next.slots.append(UISlotPref(id: slot.id, enabled: true))
            seen.insert(slot.id)
        }
        return next
    }

    func jsonObject() -> [String: Any] {
        let lightsOn = lights || slots.contains(where: { $0.id == "lights" && $0.enabled })
        return [
            "tray": [
                "density": trayDensity,
                "lights": lightsOn,
                "slots": slots.map { ["id": $0.id, "enabled": $0.enabled] as [String: Any] },
            ],
            "hud": [
                "show_altitude": showAltitude,
                "show_faces": showFaces,
                "density": hudDensity,
            ],
        ]
    }

    static func apply(_ slots: [TraySlot], ui: DeskUIConfig) -> [TraySlot] {
        let byID = Dictionary(slots.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var out: [TraySlot] = []
        var used = Set<String>()
        for pref in ui.slots {
            used.insert(pref.id)
            guard pref.enabled, var slot = byID[pref.id] else { continue }
            slot = decorate(slot, ui: ui)
            out.append(slot)
        }
        for slot in slots where !used.contains(slot.id) && slot.id != "lights" {
            out.append(decorate(slot, ui: ui))
        }
        return out
    }

    private static func decorate(_ slot: TraySlot, ui: DeskUIConfig) -> TraySlot {
        var next = slot
        if !ui.showAltitude && next.id == "weather" {
            next.detail = stripAltitude(next.detail)
        }
        if !ui.showFaces {
            next.face = false
        }
        return next
    }

    private static func stripAltitude(_ detail: String?) -> String? {
        guard let detail, !detail.isEmpty else { return nil }
        let kept = detail.split(separator: "·").map { $0.trimmingCharacters(in: .whitespaces) }.filter { part in
            let digits = part.dropLast()
            return !(part.hasSuffix("m") && !digits.isEmpty && digits.allSatisfy(\.isNumber))
        }
        return kept.isEmpty ? nil : kept.joined(separator: " · ")
    }
}

enum DeskUIConfigStore {
    static func resolvePath(statusPath: String?) -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        let preferred = home.appendingPathComponent(".config/desk-switch/config.json")
        if let statusPath, !statusPath.isEmpty, statusPath != "(defaults)" {
            return URL(fileURLWithPath: statusPath)
        }
        let legacy = home.appendingPathComponent(".config/hhkb-mx-follow/config.json")
        if FileManager.default.isReadableFile(atPath: legacy.path),
           !FileManager.default.isReadableFile(atPath: preferred.path)
        {
            return legacy
        }
        return preferred
    }

    static func save(_ ui: DeskUIConfig, to statusPath: String?) throws {
        let dest = resolvePath(statusPath: statusPath)
        try FileManager.default.createDirectory(
            at: dest.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        var root: [String: Any] = [:]
        if let data = try? Data(contentsOf: dest),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        {
            root = obj
        }
        root["ui"] = ui.jsonObject()
        let data = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted])
        try data.write(to: dest, options: .atomic)
    }
}
