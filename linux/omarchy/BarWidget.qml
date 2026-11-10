import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "glaet.desk-switch"

  property string hint: ""
  property string barLabel: "desk"
  property string stripTitle: "desk"
  property string trayDensity: "strip"
  property string barTooltip: "desk"
  property bool dualUpAvailable: false
  property string lastStatus: ""
  property string hhkbTransport: "absent"
  property bool hhkbUsb: false
  property bool hhkbBluetooth: false
  property var mouseChannel: null
  property bool mouseOnline: false
  property string mouseHost: ""
  property string dualupMode: "unknown"
  property string activeLayout: ""
  property var hosts: []
  property var layouts: []
  property var slots: []
  // Shared HUD/tray prefs from ~/.config/desk/config.json (status.ui).
  // Core already applies slot order / visibility / kind / per-slot altitude.
  // Mac writes this file. TODO: Omarchy drag-reorder settings UI — read
  // the same schema, do not fork it. Modular kind HUD is macOS-first.
  property var uiConfig: ({})
  property bool barSlots: false
  property var barMark: ({ text: "desk" })

  readonly property bool opened: panelLoader.item
    ? panelLoader.item.opened === true
    : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false

  function open() {
    refresh()
    if (panelLoader.item) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item) panelLoader.item.close()
  }

  function toggle() {
    refresh()
    if (panelLoader.item) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item) panelLoader.item.closeForPopoutSwitch()
  }

  function injectPanel() {
    if (!panelLoader.item) return
    panelLoader.item.bar = root.bar
    panelLoader.item.anchorItem = button
    panelLoader.item.hostWidget = root
    panelLoader.item.hint = root.hint
    panelLoader.item.barLabel = root.barLabel
    panelLoader.item.stripTitle = root.stripTitle
    panelLoader.item.dualUpAvailable = root.dualUpAvailable
    panelLoader.item.lastStatus = root.lastStatus
    panelLoader.item.hhkbTransport = root.hhkbTransport
    panelLoader.item.hhkbUsb = root.hhkbUsb
    panelLoader.item.hhkbBluetooth = root.hhkbBluetooth
    panelLoader.item.mouseChannel = root.mouseChannel
    panelLoader.item.mouseOnline = root.mouseOnline
    panelLoader.item.mouseHost = root.mouseHost
    panelLoader.item.dualupMode = root.dualupMode
    panelLoader.item.activeLayout = root.activeLayout
    panelLoader.item.hosts = root.hosts
    panelLoader.item.layouts = root.layouts
    panelLoader.item.slots = root.slots
    panelLoader.item.uiConfig = root.uiConfig
  }

  function applyStatus(text) {
    const raw = String(text || "").trim()
    root.lastStatus = raw
    let hint = ""
    let label = "desk"
    let strip = "desk"
    let density = "strip"
    let tooltip = "desk"
    let dual = false
    let transport = "absent"
    let usb = false
    let bluetooth = false
    let channel = null
    let online = false
    let host = ""
    let mode = "unknown"
    let slots = []
    let uiConfig = {}
    let layoutName = ""
    let hosts = []
    let layouts = []
    let focus = ""
    let parsed = null
    if (raw.charAt(0) === "{") {
      try {
        const data = JSON.parse(raw)
        parsed = data
        hint = String(data.target_hint || "")
        if (hint === "?")
          hint = ""
        label = String(data.bar_label || hint || "desk")
        density = data.ui && data.ui.tray && data.ui.tray.density ? String(data.ui.tray.density) : "strip"
        const stripObj = data.bar_strip || {}
        focus = String(stripObj.focus || hint || "")
        if (focus === "?")
          focus = ""
        const display = String(stripObj.display || "")
        layoutName = String(stripObj.layout || "")
        hosts = root.parseHosts(data)
        layouts = root.parseLayouts(data)
        if (!layoutName) {
          for (let i = 0; i < layouts.length; i++) {
            if (layouts[i] && layouts[i].active)
              layoutName = String(layouts[i].name || "")
          }
        }
        const coreTitle = String(stripObj.title || "")
        if (coreTitle && coreTitle !== "?")
          strip = coreTitle
        else {
          const stripParts = []
          if (focus)
            stripParts.push(focus)
          if (layoutName)
            stripParts.push(layoutName)
          else if (display === "pbp")
            stripParts.push("PBP")
          else if (display === "full")
            stripParts.push("FULL")
          strip = stripParts.length ? stripParts.join("  ") : "desk"
        }
        if (!hint)
          hint = focus
        tooltip = String(data.bar_tooltip || ("desk — " + (hint || "desk")))
        dual = data.lgdualup === true
        if (data.adapters && data.adapters.dualup && data.adapters.dualup.available === true)
          dual = true
        if (String(data.dualup_mode || "") === "pbp" || String(data.dualup_mode || "") === "full")
          dual = true
        transport = String(data.hhkb_transport || "absent")
        usb = data.hhkb_usb === true
        bluetooth = data.hhkb_bluetooth === true
        if (data.mouse_channel === null || data.mouse_channel === undefined)
          channel = null
        else
          channel = data.mouse_channel
        online = data.mouse_online === true
        host = String(data.mouse_host || "")
        mode = String(data.dualup_mode || "unknown")
        if (Array.isArray(data.slots))
          slots = data.slots
        if (data.ui && typeof data.ui === "object")
          uiConfig = data.ui
      } catch (err) {
        hint = ""
        label = "desk"
        strip = "desk"
        layoutName = ""
        hosts = []
        layouts = []
      }
    } else if (raw.length > 0) {
      hint = raw.split(/\s+/)[0]
      if (hint === "?")
        hint = ""
      label = hint || "desk"
      strip = hint || "desk"
    }
    if (!label || label === "?")
      label = hint
    if (!label)
      label = mode === "pbp" ? "PBP" : (mode === "full" ? "FULL" : "desk")
    const barSlots = !!(uiConfig && uiConfig.tray && uiConfig.tray.bar_slots === true)
    const mark = parsed && parsed.bar_mark && typeof parsed.bar_mark === "object" ? parsed.bar_mark : null
    if (!barSlots && mark && mark.text) {
      strip = String(mark.text)
    } else if (density === "chips") {
      strip = label || strip || "desk"
    } else if (barSlots && slots.length) {
      const slotTitle = root.slotsTitle(slots)
      const head = []
      if (focus)
        head.push(focus)
      else if (hint)
        head.push(hint)
      if (layoutName && !root.slotsHaveLabel(slots, layoutName))
        head.push(layoutName)
      const quiet = head.join("  ")
      if (slotTitle)
        strip = quiet ? (quiet + "  " + slotTitle) : slotTitle
      else if (quiet)
        strip = quiet
    }
    if (!strip || strip === "?")
      strip = hint || (layoutName || (mode === "pbp" ? "PBP" : (mode === "full" ? "FULL" : "desk")))
    root.hint = hint
    root.barLabel = label
    root.stripTitle = (density === "chips" && barSlots) ? (label || strip || "desk") : strip
    root.trayDensity = density
    root.barTooltip = tooltip
    root.dualUpAvailable = dual
    root.hhkbTransport = transport
    root.hhkbUsb = usb
    root.hhkbBluetooth = bluetooth
    root.mouseChannel = channel
    root.mouseOnline = online
    root.mouseHost = host
    root.dualupMode = mode
    root.activeLayout = layoutName
    root.hosts = hosts
    root.layouts = layouts
    root.slots = slots
    root.uiConfig = uiConfig
    root.barSlots = barSlots
    root.barMark = mark || {}
    root.injectPanel()
  }

  function parseHosts(data) {
    const list = data && Array.isArray(data.host_list) ? data.host_list : []
    const hint = String((data && data.target_hint) || "")
    const targetKnown = hint && hint !== "?"
    const thisHost = String((data && data.this_host) || "")
    const out = []
    for (let i = 0; i < list.length; i++) {
      const item = list[i] || {}
      const id = String(item.id || "")
      if (!id)
        continue
      const itemHint = String(item.hint || id)
      let current = false
      if (item.current === true || item.current === false)
        current = item.current === true
      else if (targetKnown)
        current = itemHint === hint || id === hint
      else
        current = id === thisHost
      out.push({ id: id, hint: itemHint, current: current })
    }
    return out
  }

  function parseLayouts(data) {
    const list = data && Array.isArray(data.layouts) ? data.layouts : []
    const activeName = String((data && data.active_layout) || "")
    const out = []
    for (let i = 0; i < list.length; i++) {
      const item = list[i]
      if (item && typeof item === "object") {
        const name = String(item.name || "")
        if (!name)
          continue
        const active = item.active === true || item.active === false ? item.active === true : name === activeName
        out.push({ name: name, active: active })
      } else if (item) {
        const name = String(item)
        if (name)
          out.push({ name: name, active: name === activeName })
      }
    }
    return out
  }

  function slotsHaveLabel(items, label) {
    const want = String(label || "")
    if (!want)
      return false
    const list = items || []
    for (let i = 0; i < list.length; i++) {
      const text = String((list[i] || {}).label || "")
      if (text === want || text.indexOf(want + " ") === 0)
        return true
    }
    return false
  }

  function glyphMark(glyph) {
    const map = {
      "mug": "☕",
      "flame": "🔥",
      "cloud.rain": "🌧",
      "cloud": "☁",
      "sun.max": "☀",
      "moon.stars": "🌙",
      "cloud.fog": "🌫",
      "cloud.bolt.rain": "⛈",
      "display.split": "▣",
      "display.full": "▭",
      "light.on": "💡",
      "light.off": "○",
      "awake.on": "⚡",
      "awake.off": "☾"
    }
    return map[String(glyph || "")] || ""
  }

  function slotsTitle(items) {
    const parts = []
    const list = items || []
    for (let i = 0; i < list.length; i++) {
      const slot = list[i] || {}
      const mark = root.glyphMark(slot.glyph)
      const text = String(slot.label || "")
      const piece = (mark ? mark + " " : "") + text
      if (piece.trim())
        parts.push(piece.trim())
    }
    return parts.join("  ")
  }

  function refresh() {
    if (statusProc.running)
      statusProc.running = false
    statusProc.running = true
  }

  function runAction(args) {
    root.actionOut = ""
    root.actionErr = ""
    actionProc.command = ["bash", "-lc", "export PATH=\"$HOME/.local/bin:$PATH\"; desk " + args]
    if (actionProc.running)
      actionProc.running = false
    actionProc.running = true
  }

  property string actionOut: ""
  property string actionErr: ""

  function finishAction() {
    const err = String(root.actionErr || "").trim()
    const out = String(root.actionOut || "").trim()
    const ok = err.length === 0
    const body = ok ? out : err
    const line = body ? body.split("\n")[0] : (ok ? "ok" : "failed")
    const mode = root.barMark && root.barMark.display ? String(root.barMark.display) : root.dualupMode
    const host = root.barMark && root.barMark.host ? String(root.barMark.host) : root.hint
    const layoutName = root.barMark && root.barMark.layout ? String(root.barMark.layout) : root.activeLayout
    if (hudLoader.item)
      hudLoader.item.show(host, layoutName, mode, line, ok)
    root.refresh()
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  onBarChanged: injectPanel()
  Component.onCompleted: refresh()

  Timer {
    interval: 15000
    running: true
    repeat: true
    onTriggered: root.refresh()
  }

  Process {
    id: statusProc
    command: ["bash", "-lc", "export PATH=\"$HOME/.local/bin:$PATH\"; if command -v desk >/dev/null; then desk status --json; else echo '{\"target_hint\":\"\",\"bar_label\":\"desk\",\"lgdualup\":false}'; fi"]
    stdout: StdioCollector {
      onStreamFinished: root.applyStatus(this.text)
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector {
      onStreamFinished: {
        root.actionOut = this.text
        flashTimer.restart()
      }
    }
    stderr: StdioCollector {
      onStreamFinished: {
        root.actionErr = this.text
        flashTimer.restart()
      }
    }
  }

  Timer {
    id: flashTimer
    interval: 40
    repeat: false
    onTriggered: root.finishAction()
  }

  Loader {
    id: hudLoader
    active: true
    source: Qt.resolvedUrl("HudToast.qml")
  }

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      if (panelLoader.item) {
        panelLoader.item.actionRequested.connect(root.runAction)
        panelLoader.item.refreshRequested.connect(root.refresh)
      }
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.stripTitle
    tooltipText: root.barTooltip
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }
}
