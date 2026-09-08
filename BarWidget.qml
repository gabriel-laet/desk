import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui

BarWidget {
  id: root
  moduleName: "glaet.desk-switch"

  property string hint: "?"
  property bool dualUpAvailable: false
  property string lastStatus: ""

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
    panelLoader.item.dualUpAvailable = root.dualUpAvailable
    panelLoader.item.lastStatus = root.lastStatus
  }

  function applyStatus(text) {
    const raw = String(text || "").trim()
    root.lastStatus = raw
    let hint = "?"
    let dual = false
    if (raw.charAt(0) === "{") {
      try {
        const data = JSON.parse(raw)
        hint = String(data.target_hint || "?")
        dual = data.lgdualup === true
      } catch (err) {
        hint = "?"
      }
    } else if (raw.length > 0) {
      hint = raw.split(/\s+/)[0]
    }
    if (["MAC", "LNX", "?"].indexOf(hint) === -1)
      hint = "?"
    root.hint = hint
    root.dualUpAvailable = dual
    root.injectPanel()
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
    command: ["bash", "-lc", "export PATH=\"$HOME/.local/bin:$PATH\"; if command -v desk-switch >/dev/null; then desk-switch status --json; elif command -v hhkb-mx-follow >/dev/null; then hhkb-mx-follow status --json; else echo '{\"target_hint\":\"?\",\"lgdualup\":false}'; fi"]
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
    text: root.hint
    tooltipText: "Desk switch — " + root.hint
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.LeftButton) root.toggle()
    }
  }
}
