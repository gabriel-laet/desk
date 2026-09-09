import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "glaet.desk-switch"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property string hint: "?"
  property string barLabel: "?"
  property bool dualUpAvailable: false
  property string lastStatus: ""
  property string hhkbTransport: "absent"
  property bool hhkbUsb: false
  property bool hhkbBluetooth: false
  property var mouseChannel: null
  property bool mouseOnline: false
  property string mouseHost: ""
  property string dualupMode: "unknown"

  signal actionRequested(string args)
  signal refreshRequested()

  function open() {
    root.refreshRequested()
    root.controller.show()
  }

  function close() {
    root.controller.hide()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.hostWidget || root, direction)
    return false
  }

  function parsedStatus() {
    const raw = String(root.lastStatus || "").trim()
    if (raw.charAt(0) === "{") {
      try {
        return JSON.parse(raw)
      } catch (err) {
        return null
      }
    }
    return null
  }

  function hhkbChip() {
    const data = parsedStatus()
    const usb = data ? data.hhkb_usb === true : root.hhkbUsb
    const bt = data ? data.hhkb_bluetooth === true : root.hhkbBluetooth
    if (usb)
      return "USB on this host"
    if (bt)
      return "BT only"
    if (data && data.hhkb_present)
      return "present · " + String(data.hhkb_transport || "unknown")
    return "absent"
  }

  function mouseChip() {
    const data = parsedStatus()
    const channel = data && data.mouse_channel != null ? data.mouse_channel : root.mouseChannel
    const host = data && data.mouse_host ? String(data.mouse_host).toUpperCase() : String(root.mouseHost || "").toUpperCase()
    const online = data ? data.mouse_online === true : root.mouseOnline
    if (channel == null)
      return "missing"
    const dest = host === "LINUX" ? "LNX" : (host === "MAC" ? "MAC" : host || "?")
    return "ch " + channel + " → " + dest + (online ? " online" : " cached")
  }

  function dualChip() {
    const data = parsedStatus()
    const mode = data && data.dualup_mode ? String(data.dualup_mode) : root.dualupMode
    const inputs = data && data.dualup_inputs ? data.dualup_inputs : { "mac": "hdmi1", "linux": "dp" }
    const label = mode === "pbp" ? "PBP" : (mode === "full" ? "FULL" : "unknown")
    return label + " · mac=" + (inputs.mac || "hdmi1") + " linux=" + (inputs.linux || "dp")
  }

  function peerChip() {
    const data = parsedStatus()
    const peer = data && data.peer
    if (!peer)
      return ""
    if (!peer.reachable)
      return "unreachable"
    const kb = peer.hhkb_usb ? "USB" : (peer.hhkb_bluetooth ? "BT" : (peer.hhkb_transport || "?"))
    return String(peer.this_host || peer.peer || "peer") + " · HHKB " + kb + " · mx " + (peer.mouse_channel != null ? peer.mouse_channel : "?")
  }

  function statusSummary() {
    const raw = String(root.lastStatus || "").trim()
    if (raw.charAt(0) === "{") {
      try {
        const data = JSON.parse(raw)
        if (data.bar_tooltip)
          return String(data.bar_tooltip)
        const hhkb = data.hhkb_usb ? "USB" : (data.hhkb || "?")
        const dual = data.dualup_mode && data.dualup_mode !== "unknown" ? data.dualup_mode : (data.lgdualup ? "lgdualup" : "no DualUp")
        const channel = data.mouse_channel != null ? ("ch " + data.mouse_channel) : "ch ?"
        return hhkb + " · " + channel + " · " + dual
      } catch (err) {
        return raw
      }
    }
    return raw || "Refresh to probe HHKB / MX / DualUp"
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.hostWidget || root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(280))
    contentHeight: panel.fittedContentHeight(content.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: content
        width: parent.width
        spacing: Style.space(8)

        Text {
          width: parent.width
          text: "Desk → " + root.hint
          color: root.barForeground
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.subtitle
          font.bold: true
        }

        Text {
          width: parent.width
          text: root.barLabel
          color: root.barForeground
          opacity: 0.8
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.subtitle
        }

        Flow {
          width: parent.width
          spacing: Style.space(6)

          StatusChip { chipLabel: "HHKB"; chipValue: root.hhkbChip(); chipOk: root.hhkbUsb || root.hhkbBluetooth || (root.parsedStatus() && root.parsedStatus().hhkb_present) }
          StatusChip { chipLabel: "MX"; chipValue: root.mouseChip(); chipOk: root.mouseChannel != null }
          StatusChip { chipLabel: "DU"; chipValue: root.dualChip(); chipOk: root.dualupMode === "pbp" || root.dualupMode === "full" }
          StatusChip { visible: root.peerChip() !== ""; chipLabel: "PEER"; chipValue: root.peerChip(); chipOk: root.peerChip().indexOf("unreachable") === -1 }
        }

        Text {
          width: parent.width
          text: root.statusSummary()
          color: root.barForeground
          opacity: 0.65
          wrapMode: Text.WordWrap
          font.family: root.bar ? root.bar.fontFamily : Style.font.family
          font.pixelSize: Style.font.subtitle
        }

        DeskButton {
          label: "Refresh status"
          onClicked: root.refreshRequested()
        }

        DeskButton {
          label: "Switch to Mac"
          onClicked: root.actionRequested("to mac")
        }

        DeskButton {
          label: "Switch to Linux"
          onClicked: root.actionRequested("to linux")
        }

        DeskButton {
          visible: root.dualUpAvailable
          label: "DualUp Full"
          onClicked: root.actionRequested("full")
        }

        DeskButton {
          visible: root.dualUpAvailable
          label: "DualUp PBP"
          onClicked: root.actionRequested("pbp")
        }
      }
    }
  }

  component StatusChip: Rectangle {
    id: chip
    property string chipLabel: ""
    property string chipValue: ""
    property bool chipOk: false

    implicitWidth: chipText.implicitWidth + Style.space(12)
    implicitHeight: chipText.implicitHeight + Style.space(8)
    radius: 4
    color: Qt.rgba(1, 1, 1, chip.chipOk ? 0.14 : 0.06)

    Text {
      id: chipText
      anchors.centerIn: parent
      text: chip.chipLabel + " " + chip.chipValue
      color: root.barForeground
      opacity: chip.chipOk ? 1 : 0.65
      font.family: root.bar ? root.bar.fontFamily : Style.font.family
      font.pixelSize: Style.font.subtitle
    }
  }

  component DeskButton: Rectangle {
    id: btn
    property string label: ""
    signal clicked()

    width: parent ? parent.width : 200
    implicitHeight: labelText.implicitHeight + Style.space(10)
    radius: 6
    color: Qt.rgba(1, 1, 1, mouse.containsMouse ? 0.14 : 0.07)

    Text {
      id: labelText
      anchors.centerIn: parent
      text: btn.label
      color: root.barForeground
      font.family: root.bar ? root.bar.fontFamily : Style.font.family
      font.pixelSize: Style.font.subtitle
    }

    MouseArea {
      id: mouse
      anchors.fill: parent
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: btn.clicked()
    }
  }
}
