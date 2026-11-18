#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <linux/uinput.h>
#include <linux/input.h>
#include <unistd.h>
#include <stdarg.h>

#define MAX_FDS 1024

static int is_gamepad_fd[MAX_FDS] = {0};
static int is_mouse_or_kbd_fd[MAX_FDS] = {0};

static int (*orig_ioctl)(int fd, unsigned long request, ...) = NULL;
static int (*orig_close)(int fd) = NULL;
static ssize_t (*orig_write)(int fd, const void *buf, size_t count) = NULL;

static void init_orig(void) {
    if (!orig_ioctl) {
        orig_ioctl = (int (*)(int, unsigned long, ...))dlsym(RTLD_NEXT, "ioctl");
    }
    if (!orig_close) {
        orig_close = (int (*)(int))dlsym(RTLD_NEXT, "close");
    }
    if (!orig_write) {
        orig_write = (ssize_t (*)(int, const void *, size_t))dlsym(RTLD_NEXT, "write");
    }
}

int close(int fd) {
    init_orig();
    if (fd >= 0 && fd < MAX_FDS) {
        is_gamepad_fd[fd] = 0;
        is_mouse_or_kbd_fd[fd] = 0;
    }
    return orig_close(fd);
}

ssize_t write(int fd, const void *buf, size_t count) {
    init_orig();
    if (fd >= 0 && fd < MAX_FDS && buf) {
        if (count == sizeof(struct uinput_user_dev)) {
            const struct uinput_user_dev *u = (const struct uinput_user_dev *)buf;
            if (u->id.vendor == 0x045e || u->id.vendor == 0x054c || u->id.vendor == 0x057e ||
                strstr(u->name, "Xbox") || strstr(u->name, "Gamepad") || strstr(u->name, "Controller") ||
                strstr(u->name, "x360") || strstr(u->name, "xone")) {
                is_gamepad_fd[fd] = 1;
            } else if (strstr(u->name, "mouse") || strstr(u->name, "keyboard") ||
                       strstr(u->name, "touch") || strstr(u->name, "tablet") ||
                       strstr(u->name, "Mouse") || strstr(u->name, "Keyboard")) {
                is_mouse_or_kbd_fd[fd] = 1;
            }
        }
    }
    return orig_write(fd, buf, count);
}

int ioctl(int fd, unsigned long request, ...) {
    init_orig();
    va_list args;
    va_start(args, request);
    void *argp = va_arg(args, void *);
    va_end(args);

    if (fd >= 0 && fd < MAX_FDS) {
        // 1. Check UI_SET_KEYBIT
        if (request == UI_SET_KEYBIT) {
            unsigned long key = (unsigned long)argp;
            if (key == BTN_GAMEPAD || (key >= BTN_SOUTH && key <= BTN_THUMBR) ||
                (key >= BTN_TRIGGER_HAPPY1 && key <= BTN_TRIGGER_HAPPY40)) {
                is_gamepad_fd[fd] = 1;
            }
            if (key == KEY_A || key == KEY_ENTER || key == KEY_SPACE || key == BTN_LEFT || key == BTN_RIGHT) {
                is_mouse_or_kbd_fd[fd] = 1;
            }
        }

        // 2. Check UI_SET_RELBIT (relative mouse)
        if (request == UI_SET_RELBIT) {
            is_mouse_or_kbd_fd[fd] = 1;
        }

        // 3. Check UI_DEV_SETUP
        if (request == UI_DEV_SETUP && argp) {
            const struct uinput_setup *s = (const struct uinput_setup *)argp;
            if (s->id.vendor == 0x045e || s->id.vendor == 0x054c || s->id.vendor == 0x057e ||
                strstr(s->name, "Xbox") || strstr(s->name, "Gamepad") || strstr(s->name, "Controller") ||
                strstr(s->name, "x360") || strstr(s->name, "xone")) {
                is_gamepad_fd[fd] = 1;
            } else if (strstr(s->name, "mouse") || strstr(s->name, "keyboard") ||
                       strstr(s->name, "touch") || strstr(s->name, "tablet") ||
                       strstr(s->name, "Mouse") || strstr(s->name, "Keyboard")) {
                is_mouse_or_kbd_fd[fd] = 1;
            }
        }

        // 4. Intercept UI_DEV_CREATE
        if (request == UI_DEV_CREATE) {
            if (is_gamepad_fd[fd] && !is_mouse_or_kbd_fd[fd]) {
                fprintf(stderr, "[selective_uinput] >>> ALLOWING Gamepad uinput creation on fd %d\n", fd);
                return orig_ioctl(fd, request, argp);
            } else {
                fprintf(stderr, "[selective_uinput] >>> BLOCKING Mouse/Keyboard uinput creation on fd %d (forcing XTest fallback)\n", fd);
                errno = EPERM;
                return -1;
            }
        }
    }

    return orig_ioctl(fd, request, argp);
}
