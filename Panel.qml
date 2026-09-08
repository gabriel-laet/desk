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
  property bool dualUpAvailable: false
  property string lastStatus: ""

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

  function statusSummary() {
    const raw = String(root.lastStatus || "").trim()
    if (raw.charAt(0) === "{") {
      try {
        const data = JSON.parse(raw)
        const hhkb = data.hhkb || "?"
        const dual = data.lgdualup ? "lgdualup" : "no DualUp"
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
    contentWidth: panel.fittedContentWidth(Style.space(260))
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
          text: root.statusSummary()
          color: root.barForeground
          opacity: 0.75
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
