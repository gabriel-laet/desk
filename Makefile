PREFIX ?= $(HOME)/.local
BINDIR := $(PREFIX)/bin
CONFDIR := $(HOME)/.config/hhkb-mx-follow
UNAME := $(shell uname -s)

.PHONY: all mxswitch install uninstall

all: mxswitch

ifeq ($(UNAME),Darwin)
mxswitch: macos/mxswitch.c
	clang -O2 -Wall -o mxswitch macos/mxswitch.c \
		-framework IOKit -framework CoreFoundation -framework CoreGraphics
	codesign -s - mxswitch
else
mxswitch:
	@echo "Linux uses linux/mxswitch.py; nothing to compile."
endif

install: mxswitch
	install -d $(BINDIR) $(CONFDIR)
	install -m 755 hhkb-mx-follow.py $(BINDIR)/hhkb-mx-follow
ifeq ($(UNAME),Darwin)
	install -m 755 mxswitch $(BINDIR)/mxswitch
else
	install -m 755 linux/mxswitch.py $(BINDIR)/mxswitch
endif
	@if [ ! -f $(CONFDIR)/config.json ]; then \
		sed 's#"mxswitch": .*#"mxswitch": "$(BINDIR)/mxswitch"#' \
			config.example.json > $(CONFDIR)/config.json; \
		echo "wrote $(CONFDIR)/config.json — set target_channel"; \
	fi
	@echo "installed $(BINDIR)/hhkb-mx-follow and $(BINDIR)/mxswitch"

uninstall:
	rm -f $(BINDIR)/hhkb-mx-follow $(BINDIR)/mxswitch
