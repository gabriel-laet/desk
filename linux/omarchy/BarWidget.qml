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
  property var slots: []

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
    panelLoader.item.slots = root.slots
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
    if (raw.charAt(0) === "{") {
      try {
        const data = JSON.parse(raw)
        hint = String(data.target_hint || "?")
        label = String(data.bar_label || hint)
        density = data.ui && data.ui.tray && data.ui.tray.density ? String(data.ui.tray.density) : "strip"
        const stripObj = data.bar_strip || {}
        const focus = String(stripObj.focus || hint)
        const display = String(stripObj.display || "")
        const stripParts = []
        if (["MAC", "LNX"].indexOf(focus) !== -1)
          stripParts.push(focus)
        if (display === "pbp")
          stripParts.push("PBP")
        else if (display === "full")
          stripParts.push("FULL")
        strip = stripParts.length ? stripParts.join("  ") : (hint && hint !== "?" ? hint : "desk")
        tooltip = String(data.bar_tooltip || ("desk — " + hint))
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
      } catch (err) {
        hint = ""
        label = "desk"
        strip = "desk"
      }
    } else if (raw.length > 0) {
      hint = raw.split(/\s+/)[0]
      label = raw
      strip = raw
    }
    if (["MAC", "LNX"].indexOf(hint) === -1)
      hint = ""
    if (!label || label === "?")
      label = hint
    if (!label)
      label = mode === "pbp" ? "PBP" : (mode === "full" ? "FULL" : "desk")
    if (density !== "chips") {
      const slotTitle = root.slotsTitle(slots)
      if (slotTitle)
        strip = slotTitle
      else if (!strip || strip === "?")
        strip = hint && hint !== "?" ? hint : (mode === "pbp" ? "PBP" : (mode === "full" ? "FULL" : "desk"))
    } else if (!label || label === "?") {
      label = hint
    }
    root.hint = hint
    root.barLabel = label
    root.stripTitle = density === "chips" ? (label || strip || "desk") : strip
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
    root.slots = slots
    root.injectPanel()
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
      "light.off": "○"
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
    actionProc.command = ["bash", "-lc", "export PATH=\"$HOME/.local/bin:$PATH\"; desk-switch " + args]
    if (actionProc.running)
      actionProc.running = false
    actionProc.running = true
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
    command: ["bash", "-lc", "export PATH=\"$HOME/.local/bin:$PATH\"; if command -v desk-switch >/dev/null; then desk-switch status --json; elif command -v hhkb-mx-follow >/dev/null; then hhkb-mx-follow status --json; else echo '{\"target_hint\":\"\",\"bar_label\":\"desk\",\"lgdualup\":false}'; fi"]
    stdout: StdioCollector {
      onStreamFinished: root.applyStatus(this.text)
    }
  }

  Process {
    id: actionProc
    stdout: StdioCollector {
      onStreamFinished: root.refresh()
    }
    stderr: StdioCollector {
      onStreamFinished: root.refresh()
    }
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
