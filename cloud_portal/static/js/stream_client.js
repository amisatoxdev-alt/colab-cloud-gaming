// Nebula Cloud Gaming - Ultra-Low Latency WebCodecs + WebGPU Streaming Client

(function() {
  const canvas = document.getElementById("streamCanvas");
  const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
  const hudOverlay = document.getElementById("hudOverlay");
  const hudFps = document.getElementById("hudFps");
  const hudPing = document.getElementById("hudPing");
  const hudBitrate = document.getElementById("hudBitrate");
  const hudCodec = document.getElementById("hudCodec");
  const hudInput = document.getElementById("hudInput");

  // Mobile / Overlay UI Elements
  const playerControls = document.getElementById("playerControls");
  const btnToggleBar = document.getElementById("btnToggleBar");
  const virtualMouseOverlay = document.getElementById("virtualMouseOverlay");
  const btnVirtualMouse = document.getElementById("btnVirtualMouse");
  const btnCloseVirtualMouse = document.getElementById("btnCloseVirtualMouse");
  const btnToggleMouseMode = document.getElementById("btnToggleMouseMode");
  const vmTouchpad = document.getElementById("vmTouchpad");
  const btnLMB = document.getElementById("btnLMB");
  const btnRMB = document.getElementById("btnRMB");
  const btnScrollUp = document.getElementById("btnScrollUp");
  const btnScrollDown = document.getElementById("btnScrollDown");

  const btnMobileKeyboard = document.getElementById("btnMobileKeyboard");
  const mobileKeyboardInput = document.getElementById("mobileKeyboardInput");
  const mobileTextDrawer = document.getElementById("mobileTextDrawer");
  const mobileTextInput = document.getElementById("mobileTextInput");
  const btnSendText = document.getElementById("btnSendText");
  const btnKeyEnter = document.getElementById("btnKeyEnter");
  const btnKeyEsc = document.getElementById("btnKeyEsc");
  const btnKeyTab = document.getElementById("btnKeyTab");
  const btnCloseTextDrawer = document.getElementById("btnCloseTextDrawer");

  let wsStream = null;
  let wsControl = null;
  let videoDecoder = null;
  let audioCtx = null;

  let isPointerLocked = false;
  let frameCount = 0;
  let lastFpsTime = performance.now();
  let bytesReceived = 0;
  let lastBitrateTime = performance.now();

  let mouseMode = "direct"; // "direct" (tap to click) or "trackpad" (relative motion)
  let lastTouchX = null;
  let lastTouchY = null;
  let isTouchDragging = false;

  // Detect mobile
  const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  if (isMobile) {
    if (hudInput) hudInput.textContent = "Touch (Direct)";
    if (virtualMouseOverlay) virtualMouseOverlay.style.display = "flex";
  }

  // -------------------------------------------------------------
  // 1. Initialize WebCodecs VideoDecoder
  // -------------------------------------------------------------
  function initVideoDecoder() {
    if (!window.VideoDecoder) {
      alert("WebCodecs VideoDecoder is not supported in this browser. Please use modern Chrome, Edge, or Brave.");
      return;
    }

    videoDecoder = new VideoDecoder({
      output: (videoFrame) => {
        frameCount++;
        if (canvas.width !== videoFrame.displayWidth || canvas.height !== videoFrame.displayHeight) {
          canvas.width = videoFrame.displayWidth;
          canvas.height = videoFrame.displayHeight;
        }
        ctx.drawImage(videoFrame, 0, 0, canvas.width, canvas.height);
        videoFrame.close();
      },
      error: (e) => {
        console.error("VideoDecoder error:", e);
      }
    });

    // Hardware H.264 High Profile Level 4.2 (Universal support)
    const codecString = "avc1.64002a";
    videoDecoder.configure({
      codec: codecString,
      optimizeForLatency: true
    });
    if (hudCodec) hudCodec.textContent = "H.264 ULL";
  }

  // -------------------------------------------------------------
  // 2. WebSocket Connections
  // -------------------------------------------------------------
  function connectStream() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;

    // Stream Socket (Video + Audio)
    wsStream = new WebSocket(`${protocol}//${host}/ws/stream`);
    wsStream.binaryType = "arraybuffer";

    wsStream.onopen = () => {
      console.log("[Stream] WebSocket Connected");
      const savedBitrate = localStorage.getItem("nebula_bitrate") || 15;
      wsStream.send(JSON.stringify({ bitrate_mbps: savedBitrate }));
    };

    wsStream.onmessage = (event) => {
      const data = new Uint8Array(event.data);
      if (!data || data.length === 0) return;
      bytesReceived += data.length;

      const tag = data[0];

      // Tag 0x01: Video Chunk
      if (tag === 0x01 && videoDecoder && videoDecoder.state === "configured") {
        const isKey = data[1] === 1;
        const nalData = data.subarray(2);

        try {
          const chunk = new EncodedVideoChunk({
            type: isKey ? "key" : "delta",
            timestamp: performance.now() * 1000,
            data: nalData
          });
          videoDecoder.decode(chunk);
        } catch (e) {
          console.warn("Decode error:", e);
        }
      }

      // Tag 0x02: Audio Chunk (Opus / PCM)
      else if (tag === 0x02 && audioCtx) {
        // Audio playback
      }
    };

    wsStream.onclose = () => {
      console.warn("[Stream] WebSocket Closed. Reconnecting in 2s...");
      setTimeout(connectStream, 2000);
    };

    // Control Socket (Inputs & Telemetry)
    wsControl = new WebSocket(`${protocol}//${host}/ws/control`);
    wsControl.binaryType = "arraybuffer";

    wsControl.onopen = () => {
      console.log("[Control] WebSocket Connected");
      startPingLoop();
    };

    wsControl.onmessage = (event) => {
      if (typeof event.data !== "string") {
        const data = new Uint8Array(event.data);
        if (data[0] === 0x07 && data.length >= 9) {
          // Precision Ping Echo
          const sent = new DataView(data.buffer, data.byteOffset).getFloat64(1, false);
          const rtt = Math.round(performance.now() - sent);
          if (hudPing) hudPing.textContent = `${rtt} ms`;
        }
      }
    };

    wsControl.onclose = () => {
      console.warn("[Control] WebSocket Closed. Reconnecting in 2s...");
      setTimeout(connectStream, 2000);
    };
  }

  // -------------------------------------------------------------
  // 3. Precision Ping & FPS Timer
  // -------------------------------------------------------------
  function startPingLoop() {
    setInterval(() => {
      if (wsControl && wsControl.readyState === WebSocket.OPEN) {
        // Embed high-precision Float64 timestamp in ping packet [0x07, 8 bytes]
        const buf = new ArrayBuffer(9);
        const view = new DataView(buf);
        view.setUint8(0, 0x07);
        view.setFloat64(1, performance.now(), false);
        wsControl.send(buf);
      }

      // Calculate FPS
      const now = performance.now();
      const elapsed = (now - lastFpsTime) / 1000;
      if (elapsed >= 1.0) {
        const fps = Math.round(frameCount / elapsed);
        if (hudFps) hudFps.textContent = `${fps} FPS`;
        frameCount = 0;
        lastFpsTime = now;
      }

      // Calculate Bitrate
      const bitElapsed = (now - lastBitrateTime) / 1000;
      if (bitElapsed >= 1.0) {
        const mbps = ((bytesReceived * 8) / (bitElapsed * 1000000)).toFixed(1);
        if (hudBitrate) hudBitrate.textContent = `${mbps} Mbps`;
        bytesReceived = 0;
        lastBitrateTime = now;
      }
    }, 1000);
  }

  // -------------------------------------------------------------
  // 4. Low-Latency Input Senders
  // -------------------------------------------------------------
  function sendMouseMoveAbs(x, y) {
    if (!wsControl || wsControl.readyState !== WebSocket.OPEN) return;
    const packet = new Uint8Array(5);
    packet[0] = 0x01;
    new DataView(packet.buffer).setUint16(1, Math.round(x), false);
    new DataView(packet.buffer).setUint16(3, Math.round(y), false);
    wsControl.send(packet);
  }

  function sendMouseMoveRel(dx, dy) {
    if (!wsControl || wsControl.readyState !== WebSocket.OPEN) return;
    const packet = new Uint8Array(5);
    packet[0] = 0x02;
    new DataView(packet.buffer).setInt16(1, Math.max(-32768, Math.min(32767, Math.round(dx))), false);
    new DataView(packet.buffer).setInt16(3, Math.max(-32768, Math.min(32767, Math.round(dy))), false);
    wsControl.send(packet);
  }

  function sendMouseButton(btn, isPress) {
    if (!wsControl || wsControl.readyState !== WebSocket.OPEN) return;
    const packet = new Uint8Array([0x03, btn, isPress ? 1 : 0]);
    wsControl.send(packet);
  }

  function sendMouseWheel(dy) {
    if (!wsControl || wsControl.readyState !== WebSocket.OPEN) return;
    const packet = new Uint8Array(2);
    packet[0] = 0x04;
    new DataView(packet.buffer).setInt8(1, Math.max(-128, Math.min(127, Math.round(dy))));
    wsControl.send(packet);
  }

  function sendKeyEvent(codeStr, isPress) {
    if (!wsControl || wsControl.readyState !== WebSocket.OPEN) return;
    const encoder = new TextEncoder();
    const codeBytes = encoder.encode(codeStr);
    const packet = new Uint8Array(3 + codeBytes.length);
    packet[0] = 0x05;
    packet[1] = isPress ? 1 : 0;
    packet[2] = codeBytes.length;
    packet.set(codeBytes, 3);
    wsControl.send(packet);
  }

  function sendTypedString(text) {
    if (!wsControl || wsControl.readyState !== WebSocket.OPEN || !text) return;
    wsControl.send(JSON.stringify({ type: "text", text: text }));
  }

  // -------------------------------------------------------------
  // 5. Desktop Mouse & Keyboard Listeners
  // -------------------------------------------------------------
  function initDesktopInputs() {
    canvas.addEventListener("click", () => {
      if (!isPointerLocked && !isMobile) {
        canvas.requestPointerLock();
      }
      if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
    });

    document.addEventListener("pointerlockchange", () => {
      isPointerLocked = (document.pointerLockElement === canvas);
      if (hudInput) {
        hudInput.textContent = isPointerLocked ? "Locked 3D" : (isMobile ? `Touch (${mouseMode})` : "Absolute");
      }
    });

    window.addEventListener("mousemove", (e) => {
      if (isPointerLocked) {
        sendMouseMoveRel(e.movementX, e.movementY);
      } else if (!isMobile) {
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const x = Math.max(0, Math.min(canvas.width, (e.clientX - rect.left) * scaleX));
        const y = Math.max(0, Math.min(canvas.height, (e.clientY - rect.top) * scaleY));
        sendMouseMoveAbs(x, y);
      }
    });

    window.addEventListener("mousedown", (e) => {
      if (isMobile) return;
      let btn = 1;
      if (e.button === 1) btn = 2;
      if (e.button === 2) btn = 3;
      sendMouseButton(btn, true);
    });

    window.addEventListener("mouseup", (e) => {
      if (isMobile) return;
      let btn = 1;
      if (e.button === 1) btn = 2;
      if (e.button === 2) btn = 3;
      sendMouseButton(btn, false);
    });

    window.addEventListener("wheel", (e) => {
      sendMouseWheel(e.deltaY / 10);
    }, { passive: true });

    window.addEventListener("keydown", (e) => {
      if (e.code === "KeyF" && e.ctrlKey && e.shiftKey) {
        hudOverlay.style.display = hudOverlay.style.display === "none" ? "flex" : "none";
        e.preventDefault();
        return;
      }
      if (isPointerLocked && ["Tab", "AltLeft", "AltRight", "MetaLeft", "MetaRight"].includes(e.code)) {
        e.preventDefault();
      }
      sendKeyEvent(e.code, true);
    });

    window.addEventListener("keyup", (e) => {
      sendKeyEvent(e.code, false);
    });

    canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  }

  // -------------------------------------------------------------
  // 6. Mobile Touch & On-Screen Virtual Mouse Handlers
  // -------------------------------------------------------------
  function initMobileTouch() {
    let touchStartTime = 0;
    let touchStartX = 0;
    let touchStartY = 0;

    // Direct Touch on Game Canvas
    canvas.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) {
        const t = e.touches[0];
        const rect = canvas.getBoundingClientRect();
        touchStartX = (t.clientX - rect.left) * (canvas.width / rect.width);
        touchStartY = (t.clientY - rect.top) * (canvas.height / rect.height);
        touchStartTime = performance.now();
        lastTouchX = t.clientX;
        lastTouchY = t.clientY;
        isTouchDragging = false;

        // Move cursor to touch position
        if (mouseMode === "direct") {
          sendMouseMoveAbs(touchStartX, touchStartY);
        }
      } else if (e.touches.length === 2) {
        // Two-finger tap or scroll
        lastTouchY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      }
    }, { passive: false });

    canvas.addEventListener("touchmove", (e) => {
      e.preventDefault();
      if (e.touches.length === 1) {
        const t = e.touches[0];
        isTouchDragging = true;

        if (mouseMode === "direct") {
          const rect = canvas.getBoundingClientRect();
          const x = (t.clientX - rect.left) * (canvas.width / rect.width);
          const y = (t.clientY - rect.top) * (canvas.height / rect.height);
          sendMouseMoveAbs(x, y);
        } else {
          // Trackpad-style relative movement
          const dx = (t.clientX - lastTouchX) * 1.5;
          const dy = (t.clientY - lastTouchY) * 1.5;
          sendMouseMoveRel(dx, dy);
          lastTouchX = t.clientX;
          lastTouchY = t.clientY;
        }
      } else if (e.touches.length === 2) {
        // Two-finger vertical scroll
        const currentY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        const dy = (lastTouchY - currentY);
        sendMouseWheel(dy / 4);
        lastTouchY = currentY;
      }
    }, { passive: false });

    canvas.addEventListener("touchend", (e) => {
      if (!isTouchDragging && performance.now() - touchStartTime < 300) {
        // Quick tap: Left Click
        sendMouseButton(1, true);
        setTimeout(() => sendMouseButton(1, false), 50);
      }
      isTouchDragging = false;
    });

    // Virtual Trackpad Surface
    if (vmTouchpad) {
      let vmLastX = 0;
      let vmLastY = 0;

      vmTouchpad.addEventListener("touchstart", (e) => {
        if (e.touches.length > 0) {
          vmLastX = e.touches[0].clientX;
          vmLastY = e.touches[0].clientY;
        }
      }, { passive: true });

      vmTouchpad.addEventListener("touchmove", (e) => {
        e.preventDefault();
        if (e.touches.length > 0) {
          const dx = (e.touches[0].clientX - vmLastX) * 1.8;
          const dy = (e.touches[0].clientY - vmLastY) * 1.8;
          sendMouseMoveRel(dx, dy);
          vmLastX = e.touches[0].clientX;
          vmLastY = e.touches[0].clientY;
        }
      }, { passive: false });
    }

    // On-Screen Buttons (LMB / RMB / Scroll)
    function bindPressRelease(el, btnNum) {
      if (!el) return;
      const down = (e) => { e.preventDefault(); el.classList.add("active"); sendMouseButton(btnNum, true); };
      const up = (e) => { e.preventDefault(); el.classList.remove("active"); sendMouseButton(btnNum, false); };
      el.addEventListener("mousedown", down);
      el.addEventListener("mouseup", up);
      el.addEventListener("touchstart", down, { passive: false });
      el.addEventListener("touchend", up, { passive: false });
    }

    bindPressRelease(btnLMB, 1);
    bindPressRelease(btnRMB, 3);

    if (btnScrollUp) {
      btnScrollUp.onclick = () => sendMouseWheel(-3);
    }
    if (btnScrollDown) {
      btnScrollDown.onclick = () => sendMouseWheel(3);
    }

    if (btnToggleMouseMode) {
      btnToggleMouseMode.onclick = () => {
        mouseMode = (mouseMode === "direct") ? "trackpad" : "direct";
        btnToggleMouseMode.textContent = `Mode: ${mouseMode === "direct" ? "Direct Tap" : "Trackpad"}`;
        if (hudInput) hudInput.textContent = `Touch (${mouseMode})`;
      };
    }

    if (btnVirtualMouse) {
      btnVirtualMouse.onclick = () => {
        virtualMouseOverlay.style.display = (virtualMouseOverlay.style.display === "none" || !virtualMouseOverlay.style.display) ? "flex" : "none";
      };
    }
    if (btnCloseVirtualMouse) {
      btnCloseVirtualMouse.onclick = () => {
        virtualMouseOverlay.style.display = "none";
      };
    }
  }

  // -------------------------------------------------------------
  // 7. On-Screen Phone Keyboard & Text Drawer
  // -------------------------------------------------------------
  function initKeyboardControls() {
    function openKeyboard() {
      // Focus hidden input to pop up phone's native virtual keyboard
      mobileKeyboardInput.focus();
      // Show text drawer
      mobileTextDrawer.style.display = "flex";
      mobileTextInput.focus();
    }

    if (btnMobileKeyboard) {
      btnMobileKeyboard.onclick = openKeyboard;
    }

    if (btnCloseTextDrawer) {
      btnCloseTextDrawer.onclick = () => {
        mobileTextDrawer.style.display = "none";
      };
    }

    if (btnSendText) {
      btnSendText.onclick = () => {
        const txt = mobileTextInput.value;
        if (txt) {
          sendTypedString(txt);
          mobileTextInput.value = "";
        }
      };
    }

    if (mobileTextInput) {
      mobileTextInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          btnSendText.click();
          sendKeyEvent("Enter", true);
          setTimeout(() => sendKeyEvent("Enter", false), 50);
          e.preventDefault();
        }
      });
    }

    if (btnKeyEnter) {
      btnKeyEnter.onclick = () => {
        sendKeyEvent("Enter", true);
        setTimeout(() => sendKeyEvent("Enter", false), 50);
      };
    }

    if (btnKeyEsc) {
      btnKeyEsc.onclick = () => {
        sendKeyEvent("Escape", true);
        setTimeout(() => sendKeyEvent("Escape", false), 50);
      };
    }

    if (btnKeyTab) {
      btnKeyTab.onclick = () => {
        sendKeyEvent("Tab", true);
        setTimeout(() => sendKeyEvent("Tab", false), 50);
      };
    }

    // Intercept native keyboard keystrokes
    mobileKeyboardInput.addEventListener("keydown", (e) => {
      sendKeyEvent(e.code || e.key, true);
    });
    mobileKeyboardInput.addEventListener("keyup", (e) => {
      sendKeyEvent(e.code || e.key, false);
    });
  }

  // -------------------------------------------------------------
  // 8. Fullscreen & Menu Controls
  // -------------------------------------------------------------
  function toggleFullscreen() {
    const doc = window.document;
    const docEl = doc.documentElement;
    const req = docEl.requestFullscreen || docEl.mozRequestFullScreen || docEl.webkitRequestFullScreen || docEl.msRequestFullscreen;
    const exit = doc.exitFullscreen || doc.mozCancelFullScreen || doc.webkitExitFullscreen || doc.msExitFullscreen;

    if (!doc.fullscreenElement && !doc.mozFullScreenElement && !doc.webkitFullscreenElement && !doc.msFullscreenElement) {
      if (req) {
        req.call(docEl).catch(() => {});
      }
    } else {
      if (exit) {
        exit.call(doc);
      }
    }
  }

  document.getElementById("btnFullscreen").onclick = toggleFullscreen;
  document.getElementById("btnToggleHud").onclick = () => {
    hudOverlay.style.display = hudOverlay.style.display === "none" ? "flex" : "none";
  };
  document.getElementById("btnRelock").onclick = () => {
    canvas.requestPointerLock();
  };
  document.getElementById("btnExit").onclick = () => {
    window.location.href = "/";
  };

  if (btnToggleBar) {
    btnToggleBar.onclick = () => {
      playerControls.style.display = (playerControls.style.display === "none" || !playerControls.style.display) ? "flex" : "none";
    };
  }

  // -------------------------------------------------------------
  // 9. Startup
  // -------------------------------------------------------------
  initVideoDecoder();
  connectStream();
  initDesktopInputs();
  initMobileTouch();
  initKeyboardControls();
})();
