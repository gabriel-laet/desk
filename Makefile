PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
LIBDIR := $(PREFIX)/lib/desk-switch
CONFDIR := $(HOME)/.config/hhkb-mx-follow
CONFDIR_NEW := $(HOME)/.config/desk-switch
UNAME := $(shell uname -s)
APPDIR := $(HOME)/Applications
MENUBAR_APP := DeskSwitchBar.app
MENUBAR_BUILD := build/$(MENUBAR_APP)

.PHONY: all mxswitch lgdualup menubar install install-menubar uninstall uninstall-menubar validate-plugin test

all: mxswitch lgdualup

ifeq ($(UNAME),Darwin)
mxswitch: adapters/mxswitch/macos/mxswitch.c
	clang -O2 -Wall -o mxswitch adapters/mxswitch/macos/mxswitch.c \
		-framework IOKit -framework CoreFoundation -framework CoreGraphics
	codesign -s - mxswitch

lgdualup: adapters/lgdualup/macos/lgdualup.c
	clang -O2 -Wall -o lgdualup adapters/lgdualup/macos/lgdualup.c \
		-framework IOKit -framework CoreFoundation
	codesign -s - lgdualup
else
mxswitch:
	@echo "Linux mouse adapter uses adapters/mxswitch/linux/mxswitch.py; nothing to compile."

lgdualup:
	@echo "Linux display adapter uses adapters/lgdualup/linux/lgdualup.sh; nothing to compile."
endif

# desk CLI + adapter helpers. Menubar is a separate macOS target.
# Installed helper paths stay ~/.local/lib/desk-switch/<id> (TCC / shims).
# Shells: macos/DeskSwitchBar + linux/omarchy (plugin manifest stays at git root).
install: mxswitch lgdualup
	install -d $(BINDIR) $(LIBDIR) $(CONFDIR) $(CONFDIR_NEW)
	install -m 755 desk-switch.py $(BINDIR)/desk-switch
	install -m 755 desk-switch.py $(BINDIR)/hhkb-mx-follow
ifeq ($(UNAME),Darwin)
	install -m 755 mxswitch $(LIBDIR)/mxswitch
	install -m 755 lgdualup $(LIBDIR)/lgdualup
	install -m 755 adapters/lgdualup/macos/dualup-layout $(LIBDIR)/dualup-layout
else
	install -m 755 adapters/mxswitch/linux/mxswitch.py $(LIBDIR)/mxswitch
	install -m 755 adapters/lgdualup/linux/lgdualup.sh $(LIBDIR)/lgdualup
	install -m 755 adapters/lgdualup/linux/dualup-layout $(LIBDIR)/dualup-layout
endif
	install -m 755 adapters/hhkb/hhkb.py $(LIBDIR)/hhkb
	install -m 755 adapters/alexa/alexa.py $(LIBDIR)/alexa
	install -m 755 adapters/kettle/kettle.py $(LIBDIR)/kettle
	install -m 755 adapters/weather/weather.py $(LIBDIR)/weather
	install -m 644 adapters/mxswitch/manifest.json $(LIBDIR)/mxswitch.manifest.json
	install -m 644 adapters/lgdualup/manifest.json $(LIBDIR)/lgdualup.manifest.json
	install -m 644 adapters/hhkb/manifest.json $(LIBDIR)/hhkb.manifest.json
	install -m 644 adapters/alexa/manifest.json $(LIBDIR)/alexa.manifest.json
	install -m 644 adapters/kettle/manifest.json $(LIBDIR)/kettle.manifest.json
	install -m 644 adapters/weather/manifest.json $(LIBDIR)/weather.manifest.json
	install -m 755 scripts/desk-switch-adapter-shim $(BINDIR)/mxswitch
	install -m 755 scripts/desk-switch-adapter-shim $(BINDIR)/lgdualup
	install -m 755 scripts/desk-switch-adapter-shim $(BINDIR)/kettle
	@if [ ! -f $(CONFDIR)/config.json ] && [ ! -f $(CONFDIR_NEW)/config.json ]; then \
		if [ "$(UNAME)" = Darwin ]; then \
			sed -e 's#"this_host": "mac"#"this_host": "mac"#' \
				-e 's#"follow_channel": 2#"follow_channel": 2#' \
				config.example.json > $(CONFDIR_NEW)/config.json; \
		else \
			sed -e 's#"this_host": "mac"#"this_host": "linux"#' \
				-e 's#"follow_channel": 2#"follow_channel": 1#' \
				config.example.json > $(CONFDIR_NEW)/config.json; \
		fi; \
		cp $(CONFDIR_NEW)/config.json $(CONFDIR)/config.json; \
		echo "wrote $(CONFDIR_NEW)/config.json — set adapters.dualup.inputs from desk-switch status / DualUp --list"; \
	fi
	@echo "installed $(BINDIR)/desk-switch"
	@echo "  adapters: $(LIBDIR)/mxswitch  $(LIBDIR)/lgdualup  $(LIBDIR)/dualup-layout  $(LIBDIR)/hhkb  $(LIBDIR)/alexa  $(LIBDIR)/kettle  $(LIBDIR)/weather"
	@echo "  manifests: $(LIBDIR)/*.manifest.json"
	@echo "  shims:    $(BINDIR)/mxswitch  $(BINDIR)/lgdualup  $(BINDIR)/kettle  $(BINDIR)/hhkb-mx-follow"
	@echo "Linux mouse hidraw: sudo cp adapters/mxswitch/linux/42-logitech-hidpp.rules /etc/udev/rules.d/"
	@echo "Linux DualUp USB: sudo cp adapters/lgdualup/linux/43-lg-dualup.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger"
ifeq ($(UNAME),Darwin)
	@echo "optional macOS menu bar: make install-menubar"
endif

uninstall:
	rm -f $(BINDIR)/desk-switch $(BINDIR)/hhkb-mx-follow $(BINDIR)/mxswitch $(BINDIR)/lgdualup $(BINDIR)/kettle
	rm -f $(LIBDIR)/mxswitch $(LIBDIR)/lgdualup $(LIBDIR)/dualup-layout $(LIBDIR)/hhkb $(LIBDIR)/alexa $(LIBDIR)/kettle $(LIBDIR)/weather
	rm -f $(LIBDIR)/mxswitch.manifest.json $(LIBDIR)/lgdualup.manifest.json $(LIBDIR)/hhkb.manifest.json $(LIBDIR)/alexa.manifest.json
	rm -f $(LIBDIR)/kettle.manifest.json $(LIBDIR)/weather.manifest.json

menubar:
ifeq ($(UNAME),Darwin)
	@command -v swiftc >/dev/null || { echo "swiftc not found — install Xcode Command Line Tools (xcode-select --install)"; exit 1; }
	mkdir -p $(MENUBAR_BUILD)/Contents/MacOS $(MENUBAR_BUILD)/Contents/Resources
	swiftc -O -parse-as-library \
		-framework SwiftUI -framework AppKit \
		-o $(MENUBAR_BUILD)/Contents/MacOS/DeskSwitchBar \
		macos/DeskSwitchBar/DeskSwitchBar.swift
	cp macos/DeskSwitchBar/Info.plist $(MENUBAR_BUILD)/Contents/Info.plist
	printf 'APPL????' > $(MENUBAR_BUILD)/Contents/PkgInfo
	codesign -s - --force $(MENUBAR_BUILD)
	@echo "built $(MENUBAR_BUILD)"
else
	@echo "DeskSwitchBar is macOS-only. Sources: macos/DeskSwitchBar/"
endif

install-menubar: menubar
ifeq ($(UNAME),Darwin)
	install -d $(APPDIR)
	rm -rf $(APPDIR)/$(MENUBAR_APP)
	cp -R $(MENUBAR_BUILD) $(APPDIR)/$(MENUBAR_APP)
	@echo "installed $(APPDIR)/$(MENUBAR_APP)"
	@echo "run: open -a DeskSwitchBar"
	@echo "login item (optional): macos/local.desk-switch-bar.plist.example"
endif

uninstall-menubar:
	rm -rf $(APPDIR)/$(MENUBAR_APP)

validate-plugin:
	./scripts/omarchy-plugin-validate .

test:
	python3 tests/test_desk_switch.py
	./scripts/omarchy-plugin-validate .
