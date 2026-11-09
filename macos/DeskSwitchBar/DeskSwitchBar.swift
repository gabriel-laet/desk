import AppKit
import Foundation
import SwiftUI

/// Menu-bar companion for desk (`desk` CLI).
/// Strip: short focus token plus the active layout name (`bar_strip.title`).
/// When slot glyphs are showing, that token is drawn into the same NSImage
/// (MenuBarExtra flattens nested Image+Text to one symbol).
/// HUD: one switch button per configured host, named layouts, then slot
/// flavors from `slots[].kind`. Actions stay scoped under their widget.

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

    /// Slots with a label paint the menu bar. An empty label stays in the
    /// click panel (a metrics table) without adding a strip token.
    private var barSlots: [TraySlot] {
        model.visibleSlots.filter { !$0.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    var body: some View {
        Group {
            if model.ui.trayDensity == "chips" || model.visibleSlots.isEmpty {
                Text(model.ui.trayDensity == "chips" ? model.barLabel : model.stripTitle)
                    .font(.system(size: 11, weight: .semibold, design: .monospaced))
                    .help(model.summary)
            } else {
                Image(
                    nsImage: MenuBarStatusItemRenderer.image(
                        prefix: model.menuPrefix,
                        slots: barSlots,
                        compact: model.ui.compact
                    )
                )
                .renderingMode(.original)
                .help(model.summary)
                .accessibilityLabel(model.slotAccessibility)
                .id(model.slotSetID + "|" + model.menuPrefix)
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
        case "cpu": return "cpu"
        default: return glyph.isEmpty ? "circle" : glyph
        }
    }
}

enum MenuBarStatusItemRenderer {
    private static var cache: (String, [TraySlot], Bool, NSImage)?

    static func image(prefix: String, slots: [TraySlot], compact: Bool = false) -> NSImage {
        if let cache, cache.0 == prefix, cache.1 == slots, cache.2 == compact {
            return cache.3
        }
        let image = draw(prefix: prefix, slots: slots, compact: compact)
        cache = (prefix, slots, compact, image)
        return image
    }

    private static func draw(prefix: String, slots: [TraySlot], compact: Bool) -> NSImage {
        let point: CGFloat = compact ? 12 : 13
        let symbolConfig = NSImage.SymbolConfiguration(pointSize: point, weight: .semibold)
        let textFont = NSFont.monospacedDigitSystemFont(ofSize: point, weight: .semibold)
        let textAttrs: [NSAttributedString.Key: Any] = [
            .font: textFont,
            .foregroundColor: NSColor.labelColor,
        ]
        let glyphTemp: CGFloat = 3
        let pairGap: CGFloat = compact ? 6 : 8

        let prefixText = NSAttributedString(string: prefix, attributes: textAttrs)
        let prefixSize = prefix.isEmpty ? NSSize.zero : prefixText.size()
        var measured: [(icon: NSImage, text: NSAttributedString)] = []
        var width: CGFloat = prefixSize.width
        var height: CGFloat = max(18, prefixSize.height)
        if !prefix.isEmpty, !slots.isEmpty {
            width += pairGap
        }
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
        if !prefix.isEmpty {
            prefixText.draw(at: NSPoint(x: x, y: (size.height - prefixSize.height) / 2))
            x += prefixSize.width
            if !slots.isEmpty {
                x += pairGap
            }
        }
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
    @Published var hosts: [DeskHost] = []
    @Published var layouts: [DeskLayout] = []
    @Published var activeLayout = ""
    @Published var ui = DeskUIConfig.defaults
    @Published var configPath = ""
    @Published var showingSettings = false

    var visibleSlots: [TraySlot] {
        DeskUIConfig.apply(slots, ui: ui)
    }

    var slotSetID: String {
        visibleSlots.map { "\($0.id):\($0.kind):\($0.glyph):\($0.label)" }.joined(separator: "|")
    }

    /// Host token, plus the layout name when the display slot is not already showing it.
    var menuPrefix: String {
        var parts: [String] = []
        if !hint.isEmpty {
            parts.append(hint)
        }
        if !activeLayout.isEmpty, !visibleSlots.contains(where: { $0.label == activeLayout }) {
            parts.append(activeLayout)
        }
        return parts.joined(separator: "  ")
    }

    var slotAccessibility: String {
        var parts: [String] = []
        if !menuPrefix.isEmpty {
            parts.append(menuPrefix)
        }
        parts.append(contentsOf: visibleSlots.map { $0.label })
        return parts.joined(separator: ", ")
    }

    var focusLabel: String {
        let host = hint.isEmpty ? "desk" : hint
        if activeLayout.isEmpty {
            return host
        }
        return "\(host)  \(activeLayout)"
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
                    self.hosts = parsed.hosts
                    self.layouts = parsed.layouts
                    self.activeLayout = parsed.activeLayout
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
                    self.hosts = []
                    self.layouts = []
                    self.activeLayout = ""
                    self.ui = DeskUIConfig.defaults
                    self.configPath = ""
                }
            }
        }
    }

    func applyOptimisticLight(on: Bool) {
        let glyph = on ? "light.on" : "light.off"
        let label = on ? "ON" : "OFF"
        let actions = [
            SlotAction(label: "On", argv: ["smarthome", "on"]),
            SlotAction(label: "Off", argv: ["smarthome", "off"]),
        ]
        if let index = slots.firstIndex(where: { $0.id == "lights" }) {
            slots[index].kind = WidgetKind.toggle.rawValue
            slots[index].glyph = glyph
            slots[index].label = label
            slots[index].hot = on
            if slots[index].actions.isEmpty {
                slots[index].actions = actions
            }
        } else {
            slots.append(
                TraySlot(
                    id: "lights",
                    kind: WidgetKind.toggle.rawValue,
                    glyph: glyph,
                    label: label,
                    detail: nil,
                    hot: on,
                    face: false,
                    progress: 0,
                    actions: actions
                )
            )
        }
        if let index = ui.slots.firstIndex(where: { $0.id == "lights" }) {
            ui.slots[index].enabled = true
            ui.lights = true
        }
    }

    func run(_ args: [String]) {
        if args == ["smarthome", "on"] {
            applyOptimisticLight(on: true)
        } else if args == ["smarthome", "off"] {
            applyOptimisticLight(on: false)
        }
        let timeout: TimeInterval = args.first == "smarthome" ? 30 : 20
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            do {
                let out = try DeskSwitchCLI.run(args, timeout: timeout)
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
        .frame(width: panelWidth)
        .onAppear { model.refresh() }
    }

    private var panelWidth: CGFloat {
        model.visibleSlots.contains(where: { WidgetKind.resolve($0) == .table }) ? 360 : 300
    }

    @ViewBuilder
    private var deskHome: some View {
        HostWidget(model: model)
        if !model.layouts.isEmpty {
            WidgetCard(title: "Layouts") {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(model.layouts) { layout in
                        DeskActionButton(title: layout.name, filled: layout.active) {
                            model.run(["layout", layout.name])
                        }
                    }
                }
            }
        }
        ForEach(model.visibleSlots) { slot in
            SlotHUD(model: model, slot: slot)
        }
        if !model.lastLine.isEmpty {
            Text(model.lastLine)
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.secondary)
                .lineLimit(3)
        }
        Divider()
        DeskRow(title: "Refresh status") { model.refresh() }
        DeskRow(title: "Configure tray") { model.showingSettings = true }
        DeskRow(title: "Quit") { NSApplication.shared.terminate(nil) }
    }
}

/// v1 flavor registry. Adapters publish `kind`; this only maps the token
/// to a SwiftUI view. Unknown kinds fall back to a chip.
enum WidgetKind: String {
    case face
    case chip
    case toggle
    case mode
    case host
    case table

    /// v1 id→kind map when an older snapshot omits `kind`. Prefer the
    /// published token so a new adapter does not need a shell change.
    private static let fallback: [String: WidgetKind] = [
        "weather": .chip,
        "kettle": .face,
        "dualup": .mode,
        "lights": .toggle,
        "host": .host,
        "hardware": .table,
    ]

    static func resolve(kind: String, id: String = "", face: Bool = false) -> WidgetKind {
        if let known = WidgetKind(rawValue: kind) {
            return known
        }
        if let mapped = fallback[id] {
            return mapped
        }
        return face ? .face : .chip
    }

    static func resolve(_ slot: TraySlot) -> WidgetKind {
        resolve(kind: slot.kind, id: slot.id, face: slot.face)
    }
}

enum ActionShortcut {
    static func label(for argv: [String]) -> String {
        switch argv {
        case ["full"]: return "⌘⌥⇧F"
        case ["pbp"]: return "⌘⌥⇧P"
        case ["layout"]: return "⌘⌥U"
        default: return ""
        }
    }
}

struct WidgetCard<Content: View>: View {
    let title: String
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title)
                .font(.system(size: 10, weight: .semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
            content()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.primary.opacity(0.06), in: RoundedRectangle(cornerRadius: 10))
    }
}

struct HostWidget: View {
    @ObservedObject var model: DeskSwitchModel

    var body: some View {
        WidgetCard(title: "Focus") {
            VStack(alignment: .leading, spacing: 8) {
                Text("Desk → \(model.focusLabel)")
                    .font(.system(size: 14, weight: .semibold))
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.hhkbLine)
                    Text(model.mouseLine)
                    if !model.peerLine.isEmpty {
                        Text(model.peerLine)
                    }
                }
                .font(.system(size: 11, design: .monospaced))
                .foregroundStyle(.secondary)
                if !model.hosts.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(model.hosts) { host in
                            DeskActionButton(title: host.switchTitle, filled: host.current) {
                                model.run(["to", host.id])
                            }
                        }
                    }
                }
            }
        }
    }
}

struct SlotHUD: View {
    @ObservedObject var model: DeskSwitchModel
    let slot: TraySlot

    var body: some View {
        WidgetCard(title: DeskUIConfig.title(for: slot.id)) {
            switch WidgetKind.resolve(slot) {
            case .face:
                FaceFlavor(model: model, slot: slot)
            case .toggle:
                ToggleFlavor(model: model, slot: slot)
            case .mode:
                ModeFlavor(model: model, slot: slot)
            case .host:
                ChipFlavor(slot: slot)
            case .chip:
                ChipFlavor(slot: slot)
            case .table:
                TableFlavor(slot: slot)
            }
        }
        .accessibilityLabel(slot.chipTitle)
    }
}

struct TableFlavor: View {
    let slot: TraySlot

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if !slot.rows.isEmpty {
                metrics
            } else if !slot.label.isEmpty {
                ChipFlavor(slot: slot)
            }
            if !slot.aiText.isEmpty {
                Text(slot.aiText)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var metrics: some View {
        VStack(alignment: .leading, spacing: 2) {
            if !slot.columns.isEmpty {
                HStack(spacing: 4) {
                    Text(" ")
                        .frame(width: 48, alignment: .leading)
                    ForEach(slot.columns) { column in
                        Text(column.label)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .foregroundStyle(.secondary)
            }
            ForEach(slot.rows) { row in
                HStack(spacing: 4) {
                    Text(row.label)
                        .fontWeight(row.focused ? .bold : .regular)
                        .frame(width: 48, alignment: .leading)
                    ForEach(Array(row.cells.enumerated()), id: \.offset) { _, cell in
                        Text(cell.isEmpty ? "—" : cell)
                            .frame(maxWidth: .infinity, alignment: .trailing)
                            .foregroundStyle(cell.isEmpty || cell == "—" ? .tertiary : .primary)
                    }
                }
                .font(.system(size: 10, design: .monospaced))
                .opacity(row.marker.isEmpty ? 1 : 0.55)
                if !row.marker.isEmpty {
                    Text(row.marker)
                        .font(.system(size: 9, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
            }
        }
    }
}

struct ChipFlavor: View {
    let slot: TraySlot

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: SlotGlyphMap.symbolName(for: slot.glyph))
                .font(.system(size: 14, weight: .semibold))
                .symbolRenderingMode(.hierarchical)
            Text(slot.label)
                .font(.system(size: 16, weight: .semibold, design: .rounded))
                .monospacedDigit()
            if let detail = slot.detail, !detail.isEmpty {
                Text(detail)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            }
            Spacer(minLength: 0)
        }
    }
}

struct FaceFlavor: View {
    @ObservedObject var model: DeskSwitchModel
    let slot: TraySlot

    private var hudSize: CGFloat { model.ui.compact ? 140 : 176 }
    private var faceFont: CGFloat { model.ui.compact ? 28 : 36 }

    var body: some View {
        VStack(spacing: 10) {
            if model.ui.showFaces {
                gauge
            } else {
                ChipFlavor(slot: slot)
            }
            SlotActionRow(model: model, slot: slot)
        }
        .frame(maxWidth: .infinity)
    }

    private var gauge: some View {
        ZStack {
            Circle()
                .fill(Color.primary.opacity(0.06))
            Circle()
                .stroke(Color.primary.opacity(0.08), lineWidth: 9)
            Circle()
                .trim(from: 0, to: CGFloat(slot.progress))
                .stroke(
                    AngularGradient(
                        colors: [
                            (slot.hot ? Color.orange : Color.primary).opacity(0.35),
                            slot.hot ? Color.orange : Color.primary,
                        ],
                        center: .center
                    ),
                    style: StrokeStyle(lineWidth: 9, lineCap: .round)
                )
                .rotationEffect(.degrees(-90))
            VStack(spacing: 3) {
                Image(systemName: SlotGlyphMap.symbolName(for: slot.glyph))
                    .font(.system(size: 15, weight: .semibold))
                    .symbolRenderingMode(.hierarchical)
                Text(slot.label)
                    .font(.system(size: faceFont, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .minimumScaleFactor(0.7)
                    .lineLimit(1)
                if let detail = slot.detail, !detail.isEmpty {
                    Text(detail)
                        .font(.system(size: 11, weight: .medium, design: .rounded))
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.center)
                }
            }
        }
        .frame(width: hudSize, height: hudSize)
        .frame(maxWidth: .infinity)
        .contentShape(Circle())
        .onTapGesture { model.refresh() }
    }
}

struct ToggleFlavor: View {
    @ObservedObject var model: DeskSwitchModel
    let slot: TraySlot

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ChipFlavor(slot: slot)
            SlotActionRow(model: model, slot: slot, highlightCurrent: true)
        }
    }
}

struct ModeFlavor: View {
    @ObservedObject var model: DeskSwitchModel
    let slot: TraySlot

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ChipFlavor(slot: slot)
            SlotActionRow(model: model, slot: slot, highlightCurrent: true, shortcuts: true)
        }
    }
}

struct SlotActionRow: View {
    @ObservedObject var model: DeskSwitchModel
    let slot: TraySlot
    var highlightCurrent: Bool = false
    var shortcuts: Bool = false

    var body: some View {
        if !slot.actions.isEmpty {
            HStack(spacing: 8) {
                ForEach(Array(slot.actions.enumerated()), id: \.offset) { _, action in
                    let mark = ActionShortcut.label(for: action.argv)
                    let title = shortcuts && !mark.isEmpty ? "\(action.label)    \(mark)" : action.label
                    DeskActionButton(
                        title: title,
                        filled: highlightCurrent && actionMatchesCurrent(action)
                    ) {
                        model.run(action.argv)
                    }
                }
            }
        }
    }

    private func actionMatchesCurrent(_ action: SlotAction) -> Bool {
        let current = slot.label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let label = action.label.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return !current.isEmpty && (label == current || label.hasPrefix(current))
    }
}

struct DeskActionButton: View {
    let title: String
    var filled: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .frame(maxWidth: .infinity)
                .frame(height: 32)
        }
        .buttonStyle(WatchOrbStyle(accent: Color.primary, filled: filled, shape: .capsule))
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
            if model.ui.slots.contains(where: { WidgetKind(rawValue: $0.kind) == .chip }) {
                Toggle("Show altitude", isOn: chipAltitudeBinding)
                    .font(.system(size: 12))
            }
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

    /// Altitude is a chip-slot pref (weather owns the number). The toggle
    /// writes every chip slot rather than a global HUD flag.
    private var chipAltitudeBinding: Binding<Bool> {
        Binding(
            get: {
                if let pref = model.ui.slots.first(where: { WidgetKind(rawValue: $0.kind) == .chip }) {
                    return pref.showAltitude ?? model.ui.showAltitude
                }
                return model.ui.showAltitude
            },
            set: { value in
                for index in model.ui.slots.indices where WidgetKind(rawValue: model.ui.slots[index].kind) == .chip {
                    model.ui.slots[index].showAltitude = value
                }
                model.ui.showAltitude = value
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
                return "desk not found — run make install (PATH or ~/.local/bin)"
            case .timeout:
                return "desk timed out"
            case .failed(let message):
                return message
            }
        }
    }

    static func locate() -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        for name in ["desk"] {
            let path = "\(home)/.local/bin/\(name)"
            if FileManager.default.isExecutableFile(atPath: path) {
                return path
            }
        }
        for name in ["desk"] {
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

struct HardwareColumn: Identifiable, Equatable {
    var id: String
    var label: String
}

struct HardwareRow: Identifiable, Equatable {
    var id: String
    var label: String
    var focused: Bool
    var marker: String
    var cells: [String]
}

struct TraySlot: Identifiable, Equatable {
    var id: String
    var kind: String
    var glyph: String
    var label: String
    var detail: String?
    var hot: Bool
    var face: Bool
    var progress: Double
    var actions: [SlotAction]
    var columns: [HardwareColumn] = []
    var rows: [HardwareRow] = []
    var aiText: String = ""

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
        let kind = WidgetKind.resolve(
            kind: (obj["kind"] as? String ?? "").lowercased(),
            id: id,
            face: obj["face"] as? Bool ?? false
        ).rawValue
        var columns: [HardwareColumn] = []
        if let rawColumns = obj["columns"] as? [Any] {
            for item in rawColumns {
                guard let col = item as? [String: Any] else { continue }
                let colID = col["id"] as? String ?? ""
                let colLabel = col["label"] as? String ?? colID
                guard !colID.isEmpty || !colLabel.isEmpty else { continue }
                columns.append(HardwareColumn(id: colID.isEmpty ? colLabel : colID, label: colLabel))
            }
        }
        var rows: [HardwareRow] = []
        if let rawRows = obj["rows"] as? [Any] {
            for (index, item) in rawRows.enumerated() {
                guard let row = item as? [String: Any] else { continue }
                let rowID = row["id"] as? String ?? "row-\(index)"
                let rowLabel = row["label"] as? String ?? rowID
                let cells = (row["cells"] as? [Any])?.map { String(describing: $0) } ?? []
                rows.append(
                    HardwareRow(
                        id: rowID,
                        label: rowLabel,
                        focused: row["focused"] as? Bool ?? false,
                        marker: row["marker"] as? String ?? "",
                        cells: cells
                    )
                )
            }
        }
        let aiText = obj["ai_text"] as? String ?? ""
        let quietLabel = kind == WidgetKind.table.rawValue && label.isEmpty
        return TraySlot(
            id: id,
            kind: kind,
            glyph: glyph,
            label: quietLabel ? "" : (label.isEmpty ? id : label),
            detail: obj["detail"] as? String,
            hot: obj["hot"] as? Bool ?? false,
            face: obj["face"] as? Bool ?? false,
            progress: min(max(progress, 0), 1),
            actions: actions,
            columns: columns,
            rows: rows,
            aiText: aiText
        )
    }
}

struct DeskHost: Identifiable, Equatable {
    var id: String
    var hint: String
    var current: Bool

    var switchTitle: String {
        let name: String
        if let first = id.first {
            name = String(first).uppercased() + id.dropFirst()
        } else {
            name = id
        }
        return "Switch to \(name)"
    }
}

struct DeskLayout: Identifiable, Equatable {
    var name: String
    var active: Bool
    var id: String { name }
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
    var hosts: [DeskHost] = []
    var layouts: [DeskLayout] = []
    var activeLayout = ""
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
            let token = text.split(whereSeparator: { $0.isWhitespace }).first.map(String.init) ?? ""
            let hint = token.isEmpty || token == "?" ? "" : token
            let title = hint.isEmpty ? "desk" : hint
            return StatusSnapshot(hint: hint, barLabel: title, stripTitle: title, summary: text.isEmpty ? "Refresh to probe HHKB / MX / DualUp" : text)
        }

        var hint = obj["target_hint"] as? String ?? ""
        if hint == "?" {
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
        var stripLayout = ""
        var stripTitleFromCore = ""
        if let strip = obj["bar_strip"] as? [String: Any] {
            let focus = strip["focus"] as? String ?? ""
            if !focus.isEmpty, focus != "?" {
                stripFocus = focus
            }
            stripDisplay = (strip["display"] as? String ?? "").lowercased()
            stripLayout = strip["layout"] as? String ?? ""
            stripTitleFromCore = strip["title"] as? String ?? ""
        }
        let thisHost = obj["this_host"] as? String ?? ""
        let hosts = parseHosts(obj["host_list"], hint: stripFocus, thisHost: thisHost)
        let activeFromList = (obj["layouts"] as? [Any])?.compactMap { item -> String? in
            guard let row = item as? [String: Any], row["active"] as? Bool == true else { return nil }
            let name = row["name"] as? String ?? ""
            return name.isEmpty ? nil : name
        }.first
        let activeLayout = stripLayout.isEmpty ? (activeFromList ?? "") : stripLayout
        let layouts = parseLayouts(obj["layouts"], activeName: activeLayout)
        let stripTitle: String
        if density == "chips" {
            stripTitle = barLabel
        } else if !stripTitleFromCore.isEmpty, stripTitleFromCore != "?" {
            stripTitle = stripTitleFromCore
        } else {
            var parts: [String] = []
            if !stripFocus.isEmpty, stripFocus != "?" {
                parts.append(stripFocus)
            }
            if !activeLayout.isEmpty {
                parts.append(activeLayout)
            } else if stripDisplay == "pbp" {
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
        let dest: String
        if let match = hosts.first(where: { $0.id == host }) {
            dest = match.hint
        } else if host == "linux" {
            dest = "LNX"
        } else if host == "mac" {
            dest = "MAC"
        } else if host.isEmpty {
            dest = "?"
        } else {
            dest = host.uppercased()
        }
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
        var inputBits: [String] = []
        if let inputs = obj["dualup_inputs"] as? [String: Any] {
            for key in inputs.keys.sorted() {
                if let value = inputs[key] as? String, !value.isEmpty {
                    inputBits.append("\(key)=\(value)")
                }
            }
        }
        let dualDetail = inputBits.isEmpty ? modeLabel : "\(modeLabel) · \(inputBits.joined(separator: " "))"

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

        let shownHint = !hint.isEmpty ? hint : (stripFocus == "?" ? "" : stripFocus)
        return StatusSnapshot(
            hint: shownHint,
            barLabel: barLabel,
            stripTitle: stripTitle,
            summary: summary,
            hhkbLine: "HHKB  \(hhkbValue)",
            mouseLine: "MX  \(mouseValue)",
            dualLine: "DU  \(dualDetail)",
            peerLine: peerLine,
            dualUpAvailable: dual,
            slots: slots,
            hosts: hosts,
            layouts: layouts,
            activeLayout: activeLayout,
            ui: ui,
            configPath: configPath
        )
    }

    static func parseHosts(_ raw: Any?, hint: String, thisHost: String) -> [DeskHost] {
        guard let items = raw as? [Any] else { return [] }
        let targetKnown = !hint.isEmpty && hint != "?"
        var hosts: [DeskHost] = []
        for item in items {
            guard let obj = item as? [String: Any] else { continue }
            let id = obj["id"] as? String ?? ""
            guard !id.isEmpty else { continue }
            let itemHint = (obj["hint"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? id
            let current: Bool
            if let flag = obj["current"] as? Bool {
                current = flag
            } else if targetKnown {
                current = itemHint == hint || id == hint
            } else {
                current = id == thisHost
            }
            hosts.append(DeskHost(id: id, hint: itemHint, current: current))
        }
        return hosts
    }

    static func parseLayouts(_ raw: Any?, activeName: String) -> [DeskLayout] {
        guard let items = raw as? [Any] else { return [] }
        var layouts: [DeskLayout] = []
        for item in items {
            if let obj = item as? [String: Any] {
                let name = obj["name"] as? String ?? ""
                guard !name.isEmpty else { continue }
                let active = (obj["active"] as? Bool) ?? (name == activeName)
                layouts.append(DeskLayout(name: name, active: active))
            } else if let name = item as? String, !name.isEmpty {
                layouts.append(DeskLayout(name: name, active: name == activeName))
            }
        }
        return layouts
    }
}

struct UISlotPref: Identifiable, Equatable {
    var id: String
    var enabled: Bool
    var kind: String
    var showAltitude: Bool?

    init(id: String, enabled: Bool, kind: String? = nil, showAltitude: Bool? = nil) {
        self.id = id
        self.enabled = enabled
        self.kind = WidgetKind.resolve(kind: kind ?? "", id: id).rawValue
        self.showAltitude = showAltitude
    }
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
        UISlotPref(id: "weather", enabled: true, kind: "chip", showAltitude: true),
        UISlotPref(id: "kettle", enabled: true, kind: "face"),
        UISlotPref(id: "dualup", enabled: true, kind: "mode"),
        UISlotPref(id: "hardware", enabled: false, kind: "table"),
        UISlotPref(id: "lights", enabled: false, kind: "toggle"),
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
        case "hardware": return "Hardware"
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
                    parsed.append(
                        UISlotPref(
                            id: id,
                            enabled: obj["enabled"] as? Bool ?? true,
                            kind: obj["kind"] as? String,
                            showAltitude: obj["show_altitude"] as? Bool
                        )
                    )
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
        if let chip = cfg.slots.first(where: { WidgetKind(rawValue: $0.kind) == .chip && $0.showAltitude != nil }) {
            cfg.showAltitude = chip.showAltitude ?? cfg.showAltitude
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
            next.slots.append(UISlotPref(id: slot.id, enabled: true, kind: slot.kind))
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
                "slots": slots.map { pref -> [String: Any] in
                    var obj: [String: Any] = [
                        "id": pref.id,
                        "enabled": pref.enabled,
                        "kind": pref.kind,
                    ]
                    if let altitude = pref.showAltitude {
                        obj["show_altitude"] = altitude
                    } else if WidgetKind(rawValue: pref.kind) == .chip {
                        obj["show_altitude"] = showAltitude
                    }
                    return obj
                },
            ],
            "hud": [
                "show_altitude": showAltitude,
                "show_faces": showFaces,
                "density": hudDensity,
            ],
        ]
    }

    static func placeholderLights(state: String = "unknown") -> TraySlot {
        let on = state == "on"
        return TraySlot(
            id: "lights",
            kind: WidgetKind.toggle.rawValue,
            glyph: on ? "light.on" : "light.off",
            label: on ? "ON" : (state == "off" ? "OFF" : "?"),
            detail: nil,
            hot: on,
            face: false,
            progress: 0,
            actions: [
                SlotAction(label: "On", argv: ["smarthome", "on"]),
                SlotAction(label: "Off", argv: ["smarthome", "off"]),
            ]
        )
    }

    static func apply(_ slots: [TraySlot], ui: DeskUIConfig) -> [TraySlot] {
        let byID = Dictionary(slots.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var out: [TraySlot] = []
        var used = Set<String>()
        for pref in ui.slots {
            used.insert(pref.id)
            guard pref.enabled else { continue }
            if var slot = byID[pref.id] {
                out.append(decorate(slot, ui: ui))
            } else if pref.id == "lights" {
                // Core omitted the slot (unknown / adapter timeout). Keep the
                // bulb and On/Off actions so the tray can still command + flip.
                out.append(decorate(placeholderLights(), ui: ui))
            }
        }
        for slot in slots where !used.contains(slot.id) && slot.id != "lights" {
            out.append(decorate(slot, ui: ui))
        }
        return out
    }

    private static func decorate(_ slot: TraySlot, ui: DeskUIConfig) -> TraySlot {
        var next = slot
        let kind = WidgetKind.resolve(next)
        next.kind = kind.rawValue
        let showAlt = ui.slots.first(where: { $0.id == next.id })?.showAltitude ?? ui.showAltitude
        if !showAlt && kind == .chip {
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
        let preferred = home.appendingPathComponent(".config/desk/config.json")
        if let statusPath, !statusPath.isEmpty, statusPath != "(defaults)" {
            return URL(fileURLWithPath: statusPath)
        }
        let legacySwitch = home.appendingPathComponent(".config/desk-switch/config.json")
        let legacyFollow = home.appendingPathComponent(".config/hhkb-mx-follow/config.json")
        if FileManager.default.isReadableFile(atPath: preferred.path) {
            return preferred
        }
        if FileManager.default.isReadableFile(atPath: legacySwitch.path) {
            return legacySwitch
        }
        if FileManager.default.isReadableFile(atPath: legacyFollow.path) {
            return legacyFollow
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
