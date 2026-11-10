import QtQuick
import Quickshell

// Transient switch card. It does not take keyboard focus. Same shape as
// the macOS flash: mode icon, host and layout, one result line, 1.5s.
PanelWindow {
  id: root

  property string host: ""
  property string layoutName: ""
  property string mode: ""
  property string line: ""
  property bool ok: true

  exclusionMode: ExclusionMode.Ignore
  exclusiveZone: 0
  focusable: false
  visible: false
  color: "transparent"
  anchors.top: true
  anchors.bottom: true
  anchors.left: true
  anchors.right: true

  function show(host, layoutName, mode, line, ok) {
    root.host = String(host || "")
    root.layoutName = String(layoutName || "")
    root.mode = String(mode || "")
    root.line = String(line || (ok ? "ok" : "failed"))
    root.ok = ok === true
    root.visible = true
    hideTimer.restart()
  }

  Timer {
    id: hideTimer
    interval: 1500
    repeat: false
    onTriggered: root.visible = false
  }

  SystemPalette {
    id: pal
    colorGroup: SystemPalette.Active
  }

  Rectangle {
    anchors.centerIn: parent
    width: 380
    height: 132
    radius: 16
    color: pal.window
    border.color: Qt.rgba(pal.windowText.r, pal.windowText.g, pal.windowText.b, 0.12)
    border.width: 1

    Row {
      anchors.fill: parent
      anchors.margins: 16
      spacing: 14

      Text {
        width: 44
        height: parent.height
        verticalAlignment: Text.AlignVCenter
        horizontalAlignment: Text.AlignHCenter
        text: root.mode === "pbp" ? "▣" : (root.mode === "full" ? "▭" : "·")
        color: pal.windowText
        font.pixelSize: 28
        font.bold: true
      }

      Column {
        width: parent.width - 58
        spacing: 4
        anchors.verticalCenter: parent.verticalCenter

        Text {
          width: parent.width
          text: {
            const host = root.host || "desk"
            if (root.layoutName)
              return host + "  " + root.layoutName
            if (root.mode === "pbp")
              return host + "  PBP"
            if (root.mode === "full")
              return host + "  FULL"
            return host
          }
          color: pal.windowText
          font.pixelSize: 22
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          width: parent.width
          text: root.line
          color: root.ok ? pal.windowText : "#ff6b6b"
          opacity: root.ok ? 0.75 : 1
          font.pixelSize: 13
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          elide: Text.ElideRight
        }
      }
    }
  }
}
