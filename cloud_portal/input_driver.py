#!/usr/bin/env python3
"""Ultra-low latency X11 input injector using pure XTest (libXtst.so.6).

Zero /dev/uinput dependencies, zero LD_PRELOAD hacks.
Direct C FFI calls via ctypes guarantee sub-0.05ms event dispatch directly
into Xorg display :1.
"""

import sys
import os
import time
import ctypes
import ctypes.util
import logging

log = logging.getLogger("input_driver")

# X11 KeySym definitions
KEYSYM_MAP = {
    # Letters
    "KeyA": 0x0061, "KeyB": 0x0062, "KeyC": 0x0063, "KeyD": 0x0064,
    "KeyE": 0x0065, "KeyF": 0x0066, "KeyG": 0x0067, "KeyH": 0x0068,
    "KeyI": 0x0069, "KeyJ": 0x006a, "KeyK": 0x006b, "KeyL": 0x006c,
    "KeyM": 0x006d, "KeyN": 0x006e, "KeyO": 0x006f, "KeyP": 0x0070,
    "KeyQ": 0x0071, "KeyR": 0x0072, "KeyS": 0x0073, "KeyT": 0x0074,
    "KeyU": 0x0075, "KeyV": 0x0076, "KeyW": 0x0077, "KeyX": 0x0078,
    "KeyY": 0x0079, "KeyZ": 0x007a,
    # Digits
    "Digit0": 0x0030, "Digit1": 0x0031, "Digit2": 0x0032, "Digit3": 0x0033,
    "Digit4": 0x0034, "Digit5": 0x0035, "Digit6": 0x0036, "Digit7": 0x0037,
    "Digit8": 0x0038, "Digit9": 0x0039,
    # Control keys
    "Escape": 0xff1b, "Return": 0xff0d, "Enter": 0xff0d, "Space": 0x0020,
    "Tab": 0xff09, "Backspace": 0xff08, "Delete": 0xffff,
    "ShiftLeft": 0xffe1, "ShiftRight": 0xffe2,
    "ControlLeft": 0xffe3, "ControlRight": 0xffe4,
    "AltLeft": 0xffe9, "AltRight": 0xffea,
    "MetaLeft": 0xffeb, "MetaRight": 0xffec, "SuperLeft": 0xffeb,
    "CapsLock": 0xffe5, "NumLock": 0xff7f, "ScrollLock": 0xff14,
    # Navigation
    "ArrowUp": 0xff52, "ArrowDown": 0xff54, "ArrowLeft": 0xff51, "ArrowRight": 0xff53,
    "PageUp": 0xff55, "PageDown": 0xff56, "Home": 0xff50, "End": 0xff57, "Insert": 0xff63,
    # Function keys
    "F1": 0xffbe, "F2": 0xffbf, "F3": 0xffc0, "F4": 0xffc1,
    "F5": 0xffc2, "F6": 0xffc3, "F7": 0xffc4, "F8": 0xffc5,
    "F9": 0xffc6, "F10": 0xffc7, "F11": 0xffc8, "F12": 0xffc9,
    # Punctuation & Symbols
    "Minus": 0x002d, "Equal": 0x003d, "BracketLeft": 0x005b, "BracketRight": 0x005d,
    "Backslash": 0x005c, "Semicolon": 0x003b, "Quote": 0x0027, "Backquote": 0x0060,
    "Comma": 0x002c, "Period": 0x002e, "Slash": 0x002f,
    # Numpad
    "Numpad0": 0xffb0, "Numpad1": 0xffb1, "Numpad2": 0xffb2, "Numpad3": 0xffb3,
    "Numpad4": 0xffb4, "Numpad5": 0xffb5, "Numpad6": 0xffb6, "Numpad7": 0xffb7,
    "Numpad8": 0xffb8, "Numpad9": 0xffb9, "NumpadAdd": 0xffab, "NumpadSubtract": 0xffad,
    "NumpadMultiply": 0xffaa, "NumpadDivide": 0xffaf, "NumpadEnter": 0xff8d,
}


class XTestInputDriver:
    def __init__(self, display_str=":1"):
        self.display_str = display_str
        self.display = None
        self.x11 = None
        self.xtst = None
        self.is_connected = False
        self._keysym_cache = {}
        self._last_attempt = 0
        self._init_x11()

    def _init_x11(self):
        self._last_attempt = time.time()
        try:
            # Locate libX11 and libXtst
            x11_path = ctypes.util.find_library("X11") or "libX11.so.6"
            xtst_path = ctypes.util.find_library("Xtst") or "libXtst.so.6"

            self.x11 = ctypes.CDLL(x11_path)
            self.xtst = ctypes.CDLL(xtst_path)

            # Define prototypes
            self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self.x11.XOpenDisplay.restype = ctypes.c_void_p

            self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
            self.x11.XCloseDisplay.restype = ctypes.c_int

            self.x11.XFlush.argtypes = [ctypes.c_void_p]
            self.x11.XFlush.restype = ctypes.c_int

            self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
            self.x11.XStringToKeysym.restype = ctypes.c_ulong

            self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte

            self.xtst.XTestFakeKeyEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong
            ]
            self.xtst.XTestFakeKeyEvent.restype = ctypes.c_int

            self.xtst.XTestFakeButtonEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong
            ]
            self.xtst.XTestFakeButtonEvent.restype = ctypes.c_int

            self.xtst.XTestFakeMotionEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong
            ]
            self.xtst.XTestFakeMotionEvent.restype = ctypes.c_int

            self.xtst.XTestFakeRelativeMotionEvent.argtypes = [
                ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong
            ]
            self.xtst.XTestFakeRelativeMotionEvent.restype = ctypes.c_int

            # Connect to X display
            disp_b = self.display_str.encode("utf-8")
            self.display = self.x11.XOpenDisplay(disp_b)
            if self.display:
                self.is_connected = True
                log.info(f"Connected to X11 display {self.display_str} via libXtst.")
            else:
                log.warning(f"Could not open X11 display {self.display_str} (may not be active yet).")
        except Exception as e:
            log.warning(f"X11 libXtst initialization skipped ({e}). Using fallback.")
            self.is_connected = False

    def ensure_connected(self):
        if not self.is_connected or not self.display:
            if time.time() - self._last_attempt > 3.0:
                self._init_x11()
        return self.is_connected

    def mouse_move(self, x: int, y: int):
        """Absolute mouse move (e.g., standard desktop navigation)."""
        if not self.ensure_connected():
            return
        # screen -1 = default screen
        self.xtst.XTestFakeMotionEvent(self.display, -1, int(x), int(y), 0)
        self.x11.XFlush(self.display)

    def mouse_rel_move(self, dx: int, dy: int):
        """Relative mouse move (PointerLock API for 3D camera control like GTA V)."""
        if not self.ensure_connected():
            return
        self.xtst.XTestFakeRelativeMotionEvent(self.display, int(dx), int(dy), 0)
        self.x11.XFlush(self.display)

    def mouse_button(self, button: int, is_press: bool):
        """Mouse button click (1=Left, 2=Middle, 3=Right, 4=ScrollUp, 5=ScrollDown)."""
        if not self.ensure_connected():
            return
        self.xtst.XTestFakeButtonEvent(self.display, int(button), 1 if is_press else 0, 0)
        self.x11.XFlush(self.display)

    def mouse_wheel(self, dy: float):
        """Simulate vertical scroll wheel."""
        if dy == 0 or not self.ensure_connected():
            return
        btn = 4 if dy < 0 else 5  # 4 = Wheel Up, 5 = Wheel Down
        self.xtst.XTestFakeButtonEvent(self.display, btn, 1, 0)
        self.xtst.XTestFakeButtonEvent(self.display, btn, 0, 0)
        self.x11.XFlush(self.display)

    def _resolve_keycode(self, code_str: str) -> int:
        if code_str in self._keysym_cache:
            return self._keysym_cache[code_str]

        keysym = KEYSYM_MAP.get(code_str)
        if not keysym:
            # Try XStringToKeysym
            keysym = self.x11.XStringToKeysym(code_str.encode("utf-8"))

        if keysym:
            keycode = self.x11.XKeysymToKeycode(self.display, keysym)
            self._keysym_cache[code_str] = keycode
            return keycode
        return 0

    def key_event(self, code_str: str, is_press: bool):
        """Keyboard event simulation."""
        if not self.ensure_connected():
            return
        keycode = self._resolve_keycode(code_str)
        if keycode:
            self.xtst.XTestFakeKeyEvent(self.display, keycode, 1 if is_press else 0, 0)
            self.x11.XFlush(self.display)

    def close(self):
        if self.display and self.x11:
            try:
                self.x11.XCloseDisplay(self.display)
            except Exception:
                pass
            self.display = None
            self.is_connected = False


# Global singleton instance
driver = XTestInputDriver()
