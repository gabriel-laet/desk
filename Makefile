PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
CONFDIR := $(HOME)/.config/hhkb-mx-follow
CONFDIR_NEW := $(HOME)/.config/desk-switch
UNAME := $(shell uname -s)

.PHONY: all mxswitch lgdualup install uninstall validate-plugin test

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
	@echo "Linux uses linux/mxswitch.py; nothing to compile."

lgdualup:
	@echo "Linux uses linux/lgdualup.sh; nothing to compile."
endif

install: mxswitch lgdualup
	install -d $(BINDIR) $(CONFDIR) $(CONFDIR_NEW)
	install -m 755 desk-switch.py $(BINDIR)/desk-switch
	install -m 755 desk-switch.py $(BINDIR)/hhkb-mx-follow
ifeq ($(UNAME),Darwin)
	install -m 755 mxswitch $(BINDIR)/mxswitch
	install -m 755 lgdualup $(BINDIR)/lgdualup
else
	install -m 755 linux/mxswitch.py $(BINDIR)/mxswitch
	install -m 755 linux/lgdualup.sh $(BINDIR)/lgdualup
endif
	@if [ ! -f $(CONFDIR)/config.json ] && [ ! -f $(CONFDIR_NEW)/config.json ]; then \
		if [ "$(UNAME)" = Darwin ]; then \
			sed -e 's#"mxswitch": .*#"mxswitch": "$(BINDIR)/mxswitch",#' \
				-e 's#"lgdualup": .*#"lgdualup": "$(BINDIR)/lgdualup",#' \
				-e 's#"this_host": .*#"this_host": "mac",#' \
				-e 's#"target_channel": .*#"target_channel": 2,#' \
				config.example.json > $(CONFDIR_NEW)/config.json; \
		else \
			sed -e 's#"mxswitch": .*#"mxswitch": "$(BINDIR)/mxswitch",#' \
				-e 's#"lgdualup": .*#"lgdualup": "$(BINDIR)/lgdualup",#' \
				-e 's#"this_host": .*#"this_host": "linux",#' \
				-e 's#"target_channel": .*#"target_channel": 1,#' \
				config.example.json > $(CONFDIR_NEW)/config.json; \
		fi; \
		cp $(CONFDIR_NEW)/config.json $(CONFDIR)/config.json; \
		echo "wrote $(CONFDIR_NEW)/config.json — set hosts.*.dualup_input from desk-switch / lgdualup --list"; \
	fi
	@echo "installed $(BINDIR)/desk-switch, $(BINDIR)/hhkb-mx-follow, $(BINDIR)/mxswitch, $(BINDIR)/lgdualup"
	@echo "Linux DualUp USB: sudo cp linux/43-lg-dualup.rules /etc/udev/rules.d/ && sudo udevadm control --reload-rules && sudo udevadm trigger"

uninstall:
	rm -f $(BINDIR)/desk-switch $(BINDIR)/hhkb-mx-follow $(BINDIR)/mxswitch $(BINDIR)/lgdualup

validate-plugin:
	./scripts/omarchy-plugin-validate .

test:
	python3 tests/test_desk_switch.py
	./scripts/omarchy-plugin-validate .
