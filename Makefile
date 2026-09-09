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
mxswitch: macos/mxswitch.c
	clang -O2 -Wall -o mxswitch macos/mxswitch.c \
		-framework IOKit -framework CoreFoundation -framework CoreGraphics
	codesign -s - mxswitch

lgdualup: macos/lgdualup.c
	clang -O2 -Wall -o lgdualup macos/lgdualup.c \
		-framework IOKit -framework CoreFoundation
	codesign -s - lgdualup
else
mxswitch:
	@echo "Linux mouse adapter uses linux/mxswitch.py; nothing to compile."

lgdualup:
	@echo "Linux dualup adapter uses linux/lgdualup.sh; nothing to compile."
endif

# desk-switch + adapter helpers. Menubar is a separate macOS target.
install: mxswitch lgdualup
	install -d $(BINDIR) $(LIBDIR) $(CONFDIR) $(CONFDIR_NEW)
	install -m 755 desk-switch.py $(BINDIR)/desk-switch
	install -m 755 desk-switch.py $(BINDIR)/hhkb-mx-follow
ifeq ($(UNAME),Darwin)
	install -m 755 mxswitch $(LIBDIR)/mxswitch
	install -m 755 lgdualup $(LIBDIR)/lgdualup
else
	install -m 755 linux/mxswitch.py $(LIBDIR)/mxswitch
	install -m 755 linux/lgdualup.sh $(LIBDIR)/lgdualup
endif
	install -m 755 scripts/desk-switch-adapter-shim $(BINDIR)/mxswitch
	install -m 755 scripts/desk-switch-adapter-shim $(BINDIR)/lgdualup
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
	@echo "  adapters: $(LIBDIR)/mxswitch  $(LIBDIR)/lgdualup"
	@echo "  shims:    $(BINDIR)/mxswitch  $(BINDIR)/lgdualup  $(BINDIR)/hhkb-mx-follow"
	@echo "Linux DualUp USB: sudo cp linux/43-lg-dualup.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger"
ifeq ($(UNAME),Darwin)
	@echo "optional macOS menu bar: make install-menubar"
endif

uninstall:
	rm -f $(BINDIR)/desk-switch $(BINDIR)/hhkb-mx-follow $(BINDIR)/mxswitch $(BINDIR)/lgdualup
	rm -f $(LIBDIR)/mxswitch $(LIBDIR)/lgdualup

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
