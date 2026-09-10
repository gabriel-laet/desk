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
  property string hint: ""
  property string barLabel: "desk"
  property string stripTitle: "desk"
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

  function slotList() {
    if (root.slots && root.slots.length)
      return root.slots
    const data = root.parsedStatus()
    if (data && Array.isArray(data.slots))
      return data.slots
    return []
  }

  function faceSlot() {
    const items = root.slotList()
    for (let i = 0; i < items.length; i++) {
      if (items[i] && items[i].face)
        return items[i]
    }
    return items.length ? items[0] : null
  }

  function faceSlotLabel() {
    const face = root.faceSlot()
    return face && face.label ? String(face.label) : "desk"
  }

  function faceSlotDetail() {
    const face = root.faceSlot()
    return face && face.detail ? String(face.detail) : ""
  }

  function complicationLine() {
    const face = root.faceSlot()
    const items = root.slotList()
    const parts = []
    for (let i = 0; i < items.length; i++) {
      const slot = items[i]
      if (!slot || (face && slot.id === face.id))
        continue
      const label = String(slot.label || "")
      if (label)
        parts.push(label)
    }
    return parts.join(" · ")
  }

  function slotActions() {
    const items = root.slotList()
    const out = []
    for (let i = 0; i < items.length; i++) {
      const actions = items[i] && items[i].actions
      if (!Array.isArray(actions))
        continue
      for (let j = 0; j < actions.length; j++) {
        const action = actions[j] || {}
        const argv = action.argv
        if (!Array.isArray(argv) || !argv.length)
          continue
        out.push({
          label: String(action.label || argv[0]),
          args: argv.map(function(part) { return String(part) }).join(" ")
        })
      }
    }
    return out
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
          text: "Desk → " + (root.hint || (root.dualupMode === "pbp" ? "PBP" : (root.dualupMode === "full" ? "FULL" : "desk")))
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

        Rectangle {
          visible: root.slots && root.slots.length
          width: 176
          height: 176
          radius: 88
          anchors.horizontalCenter: parent.horizontalCenter
          color: Qt.rgba(1, 1, 1, 0.06)
          border.color: Qt.rgba(1, 1, 1, 0.10)
          border.width: 8

          Column {
            anchors.centerIn: parent
            spacing: 4
            width: 140

            Text {
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              text: root.faceSlotLabel()
              color: root.barForeground
              font.pixelSize: 28
              font.bold: true
            }
            Text {
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              text: root.faceSlotDetail()
              color: root.barForeground
              opacity: 0.7
              wrapMode: Text.WordWrap
              font.pixelSize: Style.font.subtitle
            }
            Text {
              width: parent.width
              horizontalAlignment: Text.AlignHCenter
              text: root.complicationLine()
              color: root.barForeground
              opacity: 0.75
              font.pixelSize: Style.font.subtitle
            }
          }
        }

        Flow {
          width: parent.width
          spacing: Style.space(6)

          StatusChip { chipLabel: "HHKB"; chipValue: root.hhkbChip(); chipOk: root.hhkbUsb || root.hhkbBluetooth || (root.parsedStatus() && root.parsedStatus().hhkb_present) }
          StatusChip { chipLabel: "MX"; chipValue: root.mouseChip(); chipOk: root.mouseChannel != null }
          StatusChip { chipLabel: "DU"; chipValue: root.dualChip(); chipOk: root.dualupMode === "pbp" || root.dualupMode === "full" }
          StatusChip { visible: root.peerChip() !== ""; chipLabel: "PEER"; chipValue: root.peerChip(); chipOk: root.peerChip().indexOf("unreachable") === -1 }
        }

        Flow {
          visible: root.slots && root.slots.length
          width: parent.width
          spacing: Style.space(6)
          Repeater {
            model: root.slots || []
            delegate: StatusChip {
              chipLabel: String(modelData.glyph || "")
              chipValue: String(modelData.label || "") + (modelData.detail ? " · " + modelData.detail : "")
              chipOk: modelData.hot === true || !!modelData.label
            }
          }
        }

        Repeater {
          model: root.slotActions()
          delegate: DeskButton {
            label: modelData.label
            onClicked: root.actionRequested(modelData.args)
          }
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
          label: "DualUp Full    ⌘⌥⇧F"
          onClicked: root.actionRequested("full")
        }

        DeskButton {
          visible: root.dualUpAvailable
          label: "DualUp PBP    ⌘⌥⇧P"
          onClicked: root.actionRequested("pbp")
        }

        DeskButton {
          visible: root.dualUpAvailable
          label: "Auto layout    ⌘⌥U"
          onClicked: root.actionRequested("layout")
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
