/*
 * lgdualup.c - talk to an LG DualUp (28MQ780) over the USB "LG Monitor
 *              Controls" HID device (043e:9a39). macOS, no dependencies.
 *
 * The DualUp ignores standard DDC input-select (VCP 0x60). Input lives on
 * I2C address 0x50 / VCP 0xF4; PBP lives on 0x51 / VCP 0xD7. OnScreen Control
 * wraps those frames in a vendor HID report — same framing as
 * shinyquagsire23's lg_display_manager.
 *
 * Build:
 *     clang -O2 -Wall -o lgdualup macos/lgdualup.c \
 *         -framework IOKit -framework CoreFoundation
 *
 * Usage:
 *     ./lgdualup --info
 *     ./lgdualup input usbc|dp|dp1|dp2|hdmi1|hdmi2
 *     ./lgdualup input-main <name>     (same as input — PBP Main Input List)
 *     ./lgdualup input-sub <name>      (PBP Sub Input List)
 *     ./lgdualup swap                  (Main/Sub Screen Change)
 *     ./lgdualup pbp-assign <main> <sub>
 *     ./lgdualup pbp on|off|full|side|50-50|stacked|1|3|5
 *     ./lgdualup get pbp
 *     ./lgdualup --read 0xD7
 *
 * Codes 0 and 2 are not sent. stacked comes from pbp_splits.
 *
 * VCP 0xF4 @ 0x50 is Main only. After PBP the sub window stays HDMI2 unless
 * we write the sub VCPs (0x55 / 0x5A) and/or put the sub source on Main,
 * swap (0xF6), then restore Main — same pattern as ddcutil 0xF6 swap.
 */

#include <CoreFoundation/CoreFoundation.h>
#include <IOKit/hid/IOHIDManager.h>
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

#define LG_VID 0x043E
#define LG_PID 0x9A39
#define REPORT_LEN 64
#define REPORT_ID 0x08

#define VCP_INPUT 0xF4
#define VCP_INPUT_SUB 0x55   /* firmware: tracks bottom/sub PBP source */
#define VCP_INPUT_SUB2 0x5A  /* firmware: "other pbp input?" */
#define VCP_SWAP 0xF6        /* Main/Sub Screen Change (ddcutil PBP swap) */
#define VCP_PBP 0xD7
#define ADDR_INPUT 0x50
#define ADDR_PBP 0x51

static int32_t prop_int(IOHIDDeviceRef dev, CFStringRef key) {
    CFTypeRef v = IOHIDDeviceGetProperty(dev, key);
    int32_t out = 0;
    if (v && CFGetTypeID(v) == CFNumberGetTypeID())
        CFNumberGetValue((CFNumberRef)v, kCFNumberSInt32Type, &out);
    return out;
}

static void prop_str(IOHIDDeviceRef dev, CFStringRef key, char *out, size_t n) {
    out[0] = '\0';
    CFStringRef p = IOHIDDeviceGetProperty(dev, key);
    if (p && CFGetTypeID(p) == CFStringGetTypeID())
        CFStringGetCString(p, out, (CFIndex)n, kCFStringEncodingUTF8);
}

static uint8_t ddc_checksum(const uint8_t *msg, size_t n) {
    uint8_t sum = 0x6E;
    for (size_t i = 0; i < n; i++)
        sum ^= msg[i];
    return sum;
}

/* Build the 64-byte HID wrapper around a DDC write. */
static size_t wrap_ddc(uint8_t *out, uint8_t addr, const uint8_t *data,
                       size_t data_len, int second) {
    uint8_t ddc[16];
    size_t ddc_len = 0;
    ddc[ddc_len++] = addr;
    ddc[ddc_len++] = (uint8_t)(0x80 | data_len);
    memcpy(ddc + ddc_len, data, data_len);
    ddc_len += data_len;
    ddc[ddc_len] = ddc_checksum(ddc, ddc_len);
    ddc_len++;

    memset(out, 0, REPORT_LEN);
    out[0] = REPORT_ID;
    out[1] = second ? 0x02 : 0x01;
    out[2] = 0x55;
    out[3] = second ? 0x04 : 0x03;
    out[4] = second ? 0x0B : (uint8_t)ddc_len;
    out[5] = 0x00;
    out[6] = second ? 0x0B : 0x03;
    out[7] = 0x37;
    if (!second)
        memcpy(out + 8, ddc, ddc_len);
    return REPORT_LEN;
}

static int send_report(IOHIDDeviceRef dev, const uint8_t *pkt) {
    /* hidapi-style: report ID is both the SetReport id and pkt[0]. */
    if (IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, pkt[0], pkt,
                             REPORT_LEN) == kIOReturnSuccess)
        return 1;
    /* Some collections want the ID stripped from the buffer. */
    return IOHIDDeviceSetReport(dev, kIOHIDReportTypeOutput, pkt[0], pkt + 1,
                                REPORT_LEN - 1) == kIOReturnSuccess;
}

static int send_ddc(IOHIDDeviceRef dev, uint8_t addr, const uint8_t *data,
                    size_t data_len) {
    uint8_t pkt[REPORT_LEN];
    wrap_ddc(pkt, addr, data, data_len, 0);
    if (!send_report(dev, pkt))
        return 0;
    usleep(40000);
    wrap_ddc(pkt, addr, data, data_len, 1);
    send_report(dev, pkt); /* reply poke; ignore failure */
    return 1;
}

static int send_vcp(IOHIDDeviceRef dev, uint8_t addr, uint8_t vcp, uint16_t val) {
    uint8_t data[4] = {0x03, vcp, (uint8_t)(val >> 8), (uint8_t)(val & 0xFF)};
    return send_ddc(dev, addr, data, 4);
}

typedef struct {
    IOHIDManagerRef mgr;
    IOHIDDeviceRef dev;
    char name[128];
} lg_dev_t;

static void lg_release(lg_dev_t *t) {
    if (t->dev) {
        IOHIDDeviceClose(t->dev, kIOHIDOptionsTypeNone);
        t->dev = NULL;
    }
    if (t->mgr) {
        CFRelease(t->mgr);
        t->mgr = NULL;
    }
}

static int find_lg(lg_dev_t *t, int list_only) {
    memset(t, 0, sizeof(*t));
    IOHIDManagerRef mgr = IOHIDManagerCreate(kCFAllocatorDefault,
                                             kIOHIDOptionsTypeNone);
    if (!mgr)
        return 0;
    IOHIDManagerSetDeviceMatching(mgr, NULL);
    CFSetRef set = IOHIDManagerCopyDevices(mgr);
    if (!set) {
        CFRelease(mgr);
        return 0;
    }

    CFIndex count = CFSetGetCount(set);
    IOHIDDeviceRef *devices = calloc((size_t)count, sizeof(IOHIDDeviceRef));
    CFSetGetValues(set, (const void **)devices);

    int found = 0;
    for (CFIndex i = 0; i < count; i++) {
        IOHIDDeviceRef d = devices[i];
        if (prop_int(d, CFSTR(kIOHIDVendorIDKey)) != LG_VID)
            continue;
        if (prop_int(d, CFSTR(kIOHIDProductIDKey)) != LG_PID)
            continue;

        char name[128] = "(unnamed)";
        prop_str(d, CFSTR(kIOHIDProductKey), name, sizeof(name));
        int32_t page = prop_int(d, CFSTR(kIOHIDPrimaryUsagePageKey));
        int32_t usage = prop_int(d, CFSTR(kIOHIDPrimaryUsageKey));

        if (list_only) {
            printf("043e:9a39  usage=%04x:%04x  %s\n", page, usage, name);
            continue;
        }

        if (IOHIDDeviceOpen(d, kIOHIDOptionsTypeNone) != kIOReturnSuccess)
            continue;
        t->mgr = mgr;
        t->dev = d;
        snprintf(t->name, sizeof(t->name), "%s", name);
        found = 1;
        break;
    }

    free(devices);
    CFRelease(set);
    if (!found)
        CFRelease(mgr);
    return found || list_only;
}

static int parse_input(const char *s, uint16_t *out) {
    if (!strcasecmp(s, "auto")) { *out = 0x00; return 1; }
    if (!strcasecmp(s, "hdmi1")) { *out = 0x90; return 1; }
    if (!strcasecmp(s, "hdmi2")) { *out = 0x91; return 1; }
    if (!strcasecmp(s, "dp") || !strcasecmp(s, "dp1")) { *out = 0xD0; return 1; }
    if (!strcasecmp(s, "dp2")) { *out = 0xD1; return 1; }
    if (!strcasecmp(s, "dp3") || !strcasecmp(s, "usbc") ||
        !strcasecmp(s, "usb-c")) {
        *out = 0xD2;
        return 1;
    }
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        *out = (uint16_t)strtoul(s, NULL, 16);
        return 1;
    }
    return 0;
}

/* side defaults to 50-50 (5). stacked stays unset until config or --splits
 * names it. Codes 0 and 2 stretch this panel and are never stored. */
typedef struct {
    int stacked;
    int side;
    int rejected;
} pbp_splits_t;

static pbp_splits_t g_splits = {-1, 5, 0};

static void note_split(const char *key, long value, int fatal) {
    if (value == 0 || value == 2) {
        fprintf(stderr, "pbp code %ld is not sent (it stretches this panel)\n", value);
        if (fatal)
            g_splits.rejected = 1;
        return;
    }
    if (value < 0 || value > 255)
        return;
    if (!strcasecmp(key, "stacked"))
        g_splits.stacked = (int)value;
    else if (!strcasecmp(key, "side"))
        g_splits.side = (int)value;
}

static void apply_split_blob(const char *blob, int fatal) {
    const char *p = blob ? blob : "";
    while (*p) {
        while (*p == ',' || *p == ' ' || *p == '\n')
            p++;
        if (!*p)
            break;
        const char *eq = strchr(p, '=');
        const char *comma = strchr(p, ',');
        if (!eq || (comma && comma < eq)) {
            p = comma ? comma + 1 : p + strlen(p);
            continue;
        }
        char key[32];
        size_t n = (size_t)(eq - p);
        if (n >= sizeof(key))
            n = sizeof(key) - 1;
        memcpy(key, p, n);
        key[n] = '\0';
        char *start = key;
        while (*start == ' ')
            start++;
        long value = strtol(eq + 1, NULL, 0);
        note_split(start, value, fatal);
        p = comma ? comma + 1 : p + strlen(p);
    }
}

static void take_config_key(const char *json, const char *key) {
    char pat[40];
    snprintf(pat, sizeof(pat), "\"%s\"", key);
    const char *p = json;
    while ((p = strstr(p, pat)) != NULL) {
        const char *window = (p - json > 400) ? p - 400 : json;
        const char *mark = NULL;
        for (const char *scan = window; scan < p;) {
            const char *found = strstr(scan, "\"pbp_splits\"");
            if (!found || found >= p)
                break;
            mark = found;
            scan = found + 12;
        }
        if (mark) {
            const char *v = p + strlen(pat);
            while (*v && !isdigit((unsigned char)*v) && *v != '-')
                v++;
            if (*v)
                note_split(key, strtol(v, NULL, 0), 0);
        }
        p += strlen(pat);
    }
}

static void load_config_splits(void) {
    const char *home = getenv("HOME");
    const char *xdg = getenv("XDG_CONFIG_HOME");
    char paths[4][512];
    int n = 0;
    if (xdg && *xdg)
        snprintf(paths[n++], 512, "%s/desk/config.json", xdg);
    if (home && *home) {
        snprintf(paths[n++], 512, "%s/.config/desk/config.json", home);
        snprintf(paths[n++], 512, "%s/.config/desk-switch/config.json", home);
        snprintf(paths[n++], 512, "%s/.config/hhkb-mx-follow/config.json", home);
    }
    for (int i = 0; i < n; i++) {
        FILE *fh = fopen(paths[i], "r");
        if (!fh)
            continue;
        char *buf = malloc(65536);
        if (!buf) {
            fclose(fh);
            return;
        }
        size_t got = fread(buf, 1, 65535, fh);
        fclose(fh);
        buf[got] = '\0';
        take_config_key(buf, "stacked");
        take_config_key(buf, "side");
        free(buf);
        return;
    }
}

static int parse_pbp(const char *s, uint16_t *out) {
    if (!strcasecmp(s, "off") || !strcasecmp(s, "none") ||
        !strcasecmp(s, "solo") || !strcasecmp(s, "full")) {
        *out = 0x01;
        return 1;
    }
    if (!strcasecmp(s, "side") || !strcasecmp(s, "on") || !strcasecmp(s, "50") ||
        !strcasecmp(s, "50/50") || !strcasecmp(s, "50-50")) {
        *out = (uint16_t)g_splits.side;
        return 1;
    }
    if (!strcasecmp(s, "66") || !strcasecmp(s, "66/33") || !strcasecmp(s, "66-33")) {
        *out = 0x03;
        return 1;
    }
    if (!strcasecmp(s, "stacked") || !strcasecmp(s, "top-bottom") ||
        !strcasecmp(s, "tb")) {
        if (g_splits.stacked < 0) {
            fprintf(stderr,
                    "pbp split \"stacked\" has no code. Set the picture in the OSD, "
                    "run `lgdualup get pbp` or `lgdualup --read 0xD7`, and set "
                    "pbp_splits.stacked to that value. Codes 0 and 2 are not sent.\n");
            return 0;
        }
        *out = (uint16_t)g_splits.stacked;
        return 1;
    }
    char *end = NULL;
    long v = strtol(s, &end, 0);
    if (end != s && *end == '\0' && v >= 0 && v <= 255) {
        if (v == 0 || v == 2) {
            fprintf(stderr, "pbp code %ld is not sent (it stretches this panel)\n", v);
            return 0;
        }
        *out = (uint16_t)v;
        return 1;
    }
    fprintf(stderr, "unknown pbp mode: %s\n", s);
    return 0;
}

typedef struct {
    uint8_t buf[REPORT_LEN];
    CFIndex len;
    int got;
} hid_read_t;

static void hid_input_cb(void *context, IOReturn result, void *sender,
                         IOHIDReportType type, uint32_t reportID,
                         uint8_t *report, CFIndex reportLength) {
    (void)sender;
    (void)type;
    (void)reportID;
    hid_read_t *ctx = context;
    if (!ctx || result != kIOReturnSuccess || reportLength <= 0)
        return;
    CFIndex n = reportLength;
    if (n > (CFIndex)sizeof(ctx->buf))
        n = (CFIndex)sizeof(ctx->buf);
    memcpy(ctx->buf, report, (size_t)n);
    ctx->len = n;
    ctx->got = 1;
}

/* Reply bytes follow the gist layout: length at [5], opcode 0x02, VCP at
 * [8], current value at [12..13]. Checksum seed is 0x6E ^ 0x50. A one-byte
 * shift covers stacks that omit the report id. */
static int parse_vcp_reply(const uint8_t *data, size_t n, uint8_t vcp,
                           uint16_t *value) {
    if (n >= 14) {
        size_t span = (size_t)(data[5] & 0x7F) + 2;
        if (5 + span <= n && data[8] == vcp) {
            uint8_t sum = (uint8_t)(0x6E ^ 0x50);
            for (size_t i = 0; i < span; i++)
                sum ^= data[5 + i];
            if (sum == 0) {
                *value = (uint16_t)((data[12] << 8) | data[13]);
                return 1;
            }
        }
    }
    for (size_t i = 0; i + 8 < n; i++) {
        if (data[i] == 0x02 && data[i + 2] == vcp &&
            (data[i + 1] == 0x00 || data[i + 1] == 0x01)) {
            *value = (uint16_t)((data[i + 6] << 8) | data[i + 7]);
            return 1;
        }
    }
    return 0;
}

static int parse_vcp_reply_either(const uint8_t *data, size_t n, uint8_t vcp,
                                  uint16_t *value) {
    if (parse_vcp_reply(data, n, vcp, value))
        return 1;
    if (n > 1 && parse_vcp_reply(data + 1, n - 1, vcp, value))
        return 1;
    return 0;
}

/* DDC/CI Get VCP Feature is opcode 0x01. The same HID wrapper as a set
 * carries it; the monitor answers with opcode 0x02 on the input report. */
static int get_vcp(IOHIDDeviceRef dev, uint8_t addr, uint8_t vcp, uint16_t *value) {
    hid_read_t ctx;
    memset(&ctx, 0, sizeof(ctx));
    uint8_t storage[REPORT_LEN];
    memset(storage, 0, sizeof(storage));
    IOHIDDeviceRegisterInputReportCallback(dev, storage, sizeof(storage),
                                           hid_input_cb, &ctx);
    IOHIDDeviceScheduleWithRunLoop(dev, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    int ok = 0;
    for (int attempt = 0; attempt < 4 && !ok; attempt++) {
        uint8_t data[2] = {0x01, vcp};
        ctx.got = 0;
        if (!send_ddc(dev, addr, data, 2))
            break;
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, 0.30, false);
        if (ctx.got && parse_vcp_reply_either(ctx.buf, (size_t)ctx.len, vcp, value))
            ok = 1;
    }
    IOHIDDeviceUnscheduleFromRunLoop(dev, CFRunLoopGetCurrent(), kCFRunLoopDefaultMode);
    return ok;
}

static int parse_vcp_arg(const char *s, uint8_t *vcp, uint8_t *addr) {
    if (!s || !*s)
        return 0;
    if (!strcasecmp(s, "pbp")) {
        *vcp = VCP_PBP;
        *addr = ADDR_PBP;
        return 1;
    }
    char *end = NULL;
    long v = strtol(s, &end, 0);
    if (end == s || *end != '\0' || v < 0 || v > 255)
        return 0;
    *vcp = (uint8_t)v;
    *addr = (*vcp == VCP_PBP) ? ADDR_PBP : ADDR_INPUT;
    return 1;
}

static int print_vcp(IOHIDDeviceRef dev, uint8_t addr, uint8_t vcp, const char *label) {
    uint16_t value = 0;
    if (!get_vcp(dev, addr, vcp, &value)) {
        fprintf(stderr,
                "no VCP reply for 0x%02x @ 0x%02x (DDC get 0x01, reply 0x02)\n",
                vcp, addr);
        return 0;
    }
    printf("%s 0x%02x @ 0x%02x = %u (0x%02x)\n", label, vcp, addr, value, value);
    if (vcp == VCP_PBP && value != 1 && value != 3 && value != 5) {
        fprintf(stderr,
                "If this is the stacked picture, set pbp_splits.stacked to %u. "
                "Codes 0 and 2 are not sent.\n",
                value);
    }
    return 1;
}

static const char *USAGE =
    "usage: %s --info | --list | input|input-main|input-sub <name> | "
    "swap | pbp-assign <main> <sub> | pbp <mode> | get pbp | --read <vcp>\n";

int main(int argc, char **argv) {
    load_config_splits();
    const char *env_splits = getenv("LGDUALUP_PBP_SPLITS");
    if (env_splits && *env_splits)
        apply_split_blob(env_splits, 1);

    int w = 1;
    for (int r = 1; r < argc; r++) {
        if (!strcmp(argv[r], "--splits") && r + 1 < argc) {
            apply_split_blob(argv[++r], 1);
            continue;
        }
        if (!strncmp(argv[r], "--splits=", 9)) {
            apply_split_blob(argv[r] + 9, 1);
            continue;
        }
        argv[w++] = argv[r];
    }
    argv[w] = NULL;
    argc = w;

    if (g_splits.rejected)
        return 2;

    if (argc < 2) {
        fprintf(stderr, USAGE, argv[0]);
        return 2;
    }

    int list_only = !strcmp(argv[1], "--list");
    int info_only = !strcmp(argv[1], "--info");

    /* Refuse a bad split before touching USB, so a missing device is not
     * the error the operator sees. */
    if (!strcmp(argv[1], "pbp")) {
        if (argc < 3) {
            fprintf(stderr, "unknown pbp mode:\n");
            return 2;
        }
        uint16_t ignore = 0;
        if (!parse_pbp(argv[2], &ignore))
            return 2;
    }
    if ((!strcmp(argv[1], "get") || !strcmp(argv[1], "--read")) && argc < 3) {
        fprintf(stderr, USAGE, argv[0]);
        return 2;
    }

    lg_dev_t t;
    if (!find_lg(&t, list_only)) {
        fprintf(stderr, "No LG Monitor Controls device (043e:9a39) found.\n");
        fprintf(stderr, "Plug the DualUp USB-C (or USB upstream) into this Mac.\n");
        return 1;
    }
    if (list_only)
        return 0;

    if (info_only) {
        printf("device : %s\n", t.name);
        printf("usb    : 043e:9a39\n");
        lg_release(&t);
        return 0;
    }

    int ok = 0;
    if ((!strcmp(argv[1], "input") || !strcmp(argv[1], "input-main")) &&
        argc >= 3) {
        uint16_t val;
        if (!parse_input(argv[2], &val)) {
            fprintf(stderr, "unknown input: %s\n", argv[2]);
            lg_release(&t);
            return 2;
        }
        ok = send_vcp(t.dev, ADDR_INPUT, VCP_INPUT, val);
        if (ok)
            printf("input-main -> %s (0x%02x)\n", argv[2], val);
    } else if (!strcmp(argv[1], "input-sub") && argc >= 3) {
        uint16_t val;
        if (!parse_input(argv[2], &val)) {
            fprintf(stderr, "unknown input: %s\n", argv[2]);
            lg_release(&t);
            return 2;
        }
        /* DualUp OSD Sub Input List. 0xF4 never reaches the sub pane. */
        ok = send_vcp(t.dev, ADDR_INPUT, VCP_INPUT_SUB, val);
        usleep(80000);
        send_vcp(t.dev, ADDR_INPUT, VCP_INPUT_SUB2, val);
        if (ok)
            printf("input-sub -> %s (0x%02x) via 0x55/0x5A\n", argv[2], val);
    } else if (!strcmp(argv[1], "swap")) {
        ok = send_vcp(t.dev, ADDR_INPUT, VCP_SWAP, 1);
        usleep(40000);
        send_vcp(t.dev, ADDR_PBP, VCP_SWAP, 1);
        if (ok)
            printf("pbp swap -> main/sub (0xF6=1)\n");
    } else if (!strcmp(argv[1], "pbp-assign") && argc >= 4) {
        uint16_t main_v, sub_v;
        if (!parse_input(argv[2], &main_v) || !parse_input(argv[3], &sub_v)) {
            fprintf(stderr, "unknown input: %s / %s\n", argv[2], argv[3]);
            lg_release(&t);
            return 2;
        }
        /* Dedicated sub VCPs (best-effort), then swap-dance:
         * 0xF4 only sets Main. Put the desired sub on Main, swap, restore Main.
         */
        send_vcp(t.dev, ADDR_INPUT, VCP_INPUT_SUB, sub_v);
        usleep(80000);
        send_vcp(t.dev, ADDR_INPUT, VCP_INPUT_SUB2, sub_v);
        usleep(150000);
        ok = send_vcp(t.dev, ADDR_INPUT, VCP_INPUT, sub_v);
        usleep(200000);
        send_vcp(t.dev, ADDR_INPUT, VCP_SWAP, 1);
        send_vcp(t.dev, ADDR_PBP, VCP_SWAP, 1);
        usleep(200000);
        ok = send_vcp(t.dev, ADDR_INPUT, VCP_INPUT, main_v) && ok;
        if (ok)
            printf("pbp-assign main=%s (0x%02x) sub=%s (0x%02x)\n",
                   argv[2], main_v, argv[3], sub_v);
    } else if (!strcmp(argv[1], "pbp") && argc >= 3) {
        uint16_t val;
        if (!parse_pbp(argv[2], &val)) {
            lg_release(&t);
            return 2;
        }
        ok = send_vcp(t.dev, ADDR_PBP, VCP_PBP, val);
        if (ok)
            printf("pbp -> %s (0x%02x)\n", argv[2], val);
    } else if ((!strcmp(argv[1], "get") || !strcmp(argv[1], "--read")) && argc >= 3) {
        uint8_t vcp = 0, addr = 0;
        if (!parse_vcp_arg(argv[2], &vcp, &addr)) {
            fprintf(stderr, USAGE, argv[0]);
            lg_release(&t);
            return 2;
        }
        const char *label = (!strcmp(argv[1], "get") && !strcasecmp(argv[2], "pbp"))
                                ? "pbp"
                                : "vcp";
        ok = print_vcp(t.dev, addr, vcp, label);
    } else {
        fprintf(stderr, USAGE, argv[0]);
        lg_release(&t);
        return 2;
    }

    lg_release(&t);
    if (!ok) {
        if (strcmp(argv[1], "get") != 0 && strcmp(argv[1], "--read") != 0)
            fprintf(stderr, "HID write failed\n");
        return 1;
    }
    return 0;
}
