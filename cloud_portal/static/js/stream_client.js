// XGS - Xyris Cloud Gaming Ultra-Low Latency WebCodecs + WebGPU Streaming Client

(function() {
  const canvas = document.getElementById("streamCanvas");
  const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
  const hudOverlay = document.getElementById("hudOverlay");
  const hudFps = document.getElementById("hudFps");
  const hudPing = document.getElementById("hudPing");
  const hudBitrate = document.getElementById("hudBitrate");
  const hudCodec = document.getElementById("hudCodec");
  const hudInput = document.getElementById("hudInput");

  // In-Game Toolbar & Controls
  const playerControls = document.getElementById("playerControls");
  const btnToggleBar = document.getElementById("btnToggleBar");
  const btnRelock = document.getElementById("btnRelock");
  const btnFullscreen = document.getElementById("btnFullscreen");
  const btnVirtualMouse = document.getElementById("btnVirtualMouse");
  const btnMobileKeyboard = document.getElementById("btnMobileKeyboard");
  const btnOpenInGameSettings = document.getElementById("btnOpenInGameSettings");
  const btnToggleHud = document.getElementById("btnToggleHud");
  const btnSound = document.getElementById("btnSound");
  const btnExit = document.getElementById("btnExit");

  // In-Game Settings Modal Elements
  const inGameSettingsModal = document.getElementById("inGameSettingsModal");
  const btnCloseInGameSettings = document.getElementById("btnCloseInGameSettings");
  const btnApplyInGameSettings = document.getElementById("btnApplyInGameSettings");
  const liveBitrateSlider = document.getElementById("liveBitrateSlider");
  const liveBitrateVal = document.getElementById("liveBitrateVal");
  const liveMouseSelect = document.getElementById("liveMouseSelect");
  const liveQueueSelect = document.getElementById("liveQueueSelect");

  // Mobile / Virtual Mouse Elements
  const virtualMouseOverlay = document.getElementById("virtualMouseOverlay");
  const btnCloseVirtualMouse = document.getElementById("btnCloseVirtualMouse");
  const btnToggleMouseMode = document.getElementById("btnToggleMouseMode");
  const vmTouchpad = document.getElementById("vmTouchpad");
  const btnLMB = document.getElementById("btnLMB");
  const btnRMB = document.getElementById("btnRMB");
  const btnScrollUp = document.getElementById("btnScrollUp");
  const btnScrollDown = document.getElementById("btnScrollDown");

  // Mobile Keyboard Elements
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

  const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  let mouseMode = localStorage.getItem("nebula_mouse") || (isMobile ? "touch" : "locked");
  let queueMode = localStorage.getItem("nebula_queue") || "zero";

  if (isMobile) {
    if (hudInput) hudInput.textContent = "Touch Controls";
    if (virtualMouseOverlay) virtualMouseOverlay.style.display = "flex";
  } else {
    if (hudInput) hudInput.textContent = (mouseMode === "locked") ? "Gaming Lock (Click to Lock)" : "Desktop Absolute";
  }

  // -------------------------------------------------------------
  // 1. WebCodecs VideoDecoder Initialization (Zero Buffer)
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

    // Hardware H.264 High Profile Level 4.2 (Universal zero-latency decoding)
    videoDecoder.configure({
      codec: "avc1.64002a",
      optimizeForLatency: true
    });
    if (hudCodec) hudCodec.textContent = "H.264 ULL";
  }

  // -------------------------------------------------------------
  // 2. WebSocket Connections (Streaming & Controls)
  // -------------------------------------------------------------
  function connectStream() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;

    // Stream WebSocket (Video & Audio)
    wsStream = new WebSocket(`${protocol}//${host}/ws/stream`);
    wsStream.binaryType = "arraybuffer";

    wsStream.onopen = () => {
      console.log("[Stream] Connected to XGS stream engine");
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

        // Zero-queue latency protection: drop delta frame if decoder is backed up
        if (queueMode === "zero" && !isKey && videoDecoder.decodeQueueSize > 2) {
          return;
        }

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

      // Tag 0x02: Audio Chunk
      else if (tag === 0x02 && audioCtx) {
        // Audio playback
      }
    };

    wsStream.onclose = () => {
      console.warn("[Stream] Connection closed. Reconnecting in 2s...");
      setTimeout(connectStream, 2000);
    };

    // Control WebSocket (Inputs & Precision Ping)
    wsControl = new WebSocket(`${protocol}//${host}/ws/control`);
    wsControl.binaryType = "arraybuffer";

    wsControl.onopen = () => {
      console.log("[Control] Connected to XGS input driver");
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
      console.warn("[Control] Connection closed. Reconnecting in 2s...");
      setTimeout(connectStream, 2000);
    };
  }

  // -------------------------------------------------------------
  // 3. High-Precision Ping & Telemetry
  // -------------------------------------------------------------
  function startPingLoop() {
    setInterval(() => {
      if (wsControl && wsControl.readyState === WebSocket.OPEN) {
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
  // 4. Binary Input Dispatchers (Sub-0.05ms XTest Calls)
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
  // 5. Gaming Pointer Lock & Mouse Listeners
  // -------------------------------------------------------------
  function lockMouseForGaming() {
    try {
      const lockPromise = canvas.requestPointerLock({ unadjustedMovement: true });
      if (lockPromise && lockPromise.catch) {
        lockPromise.catch(() => canvas.requestPointerLock());
      }
    } catch (e) {
      canvas.requestPointerLock();
    }
  }

  function initMouseAndKeyboard() {
    // Canvas click locks mouse if Gaming mode selected
    canvas.addEventListener("click", () => {
      if (!isMobile && mouseMode === "locked" && !isPointerLocked) {
        lockMouseForGaming();
      }
      if (!audioCtx) {
        audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      }
    });

    document.addEventListener("pointerlockchange", () => {
      isPointerLocked = (document.pointerLockElement === canvas);
      if (btnRelock) {
        if (isPointerLocked) {
          btnRelock.classList.add("active");
          btnRelock.textContent = "🔓 UNLOCK (ESC)";
        } else {
          btnRelock.classList.remove("active");
          btnRelock.textContent = "🔒 LOCK MOUSE";
        }
      }
      if (hudInput) {
        hudInput.textContent = isPointerLocked ? "Gaming Locked (3D)" : (isMobile ? `Touch (${mouseMode})` : "Desktop Absolute");
      }
    });

    window.addEventListener("mousemove", (e) => {
      if (isPointerLocked || mouseMode === "locked") {
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
      if (e.button === 1) btn = 2; // Middle
      if (e.button === 2) btn = 3; // Right
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

    // Keyboard capture
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
  // 6. Mobile Touch & Virtual Mouse
  // -------------------------------------------------------------
  function initMobileTouch() {
    let touchStartTime = 0;
    let lastTouchX = null;
    let lastTouchY = null;
    let isDragging = false;

    canvas.addEventListener("touchstart", (e) => {
      if (e.touches.length === 1) {
        const t = e.touches[0];
        const rect = canvas.getBoundingClientRect();
        const x = (t.clientX - rect.left) * (canvas.width / rect.width);
        const y = (t.clientY - rect.top) * (canvas.height / rect.height);
        touchStartTime = performance.now();
        lastTouchX = t.clientX;
        lastTouchY = t.clientY;
        isDragging = false;

        if (mouseMode !== "trackpad") {
          sendMouseMoveAbs(x, y);
        }
      } else if (e.touches.length === 2) {
        lastTouchY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
      }
    }, { passive: false });

    canvas.addEventListener("touchmove", (e) => {
      e.preventDefault();
      if (e.touches.length === 1) {
        const t = e.touches[0];
        isDragging = true;

        if (mouseMode === "trackpad") {
          const dx = (t.clientX - lastTouchX) * 1.6;
          const dy = (t.clientY - lastTouchY) * 1.6;
          sendMouseMoveRel(dx, dy);
          lastTouchX = t.clientX;
          lastTouchY = t.clientY;
        } else {
          const rect = canvas.getBoundingClientRect();
          const x = (t.clientX - rect.left) * (canvas.width / rect.width);
          const y = (t.clientY - rect.top) * (canvas.height / rect.height);
          sendMouseMoveAbs(x, y);
        }
      } else if (e.touches.length === 2) {
        const currentY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
        const dy = (lastTouchY - currentY);
        sendMouseWheel(dy / 4);
        lastTouchY = currentY;
      }
    }, { passive: false });

    canvas.addEventListener("touchend", (e) => {
      if (!isDragging && performance.now() - touchStartTime < 250) {
        sendMouseButton(1, true);
        setTimeout(() => sendMouseButton(1, false), 50);
      }
      isDragging = false;
    });

    // Virtual Trackpad Pad
    if (vmTouchpad) {
      let vmX = 0;
      let vmY = 0;
      vmTouchpad.addEventListener("touchstart", (e) => {
        if (e.touches.length > 0) {
          vmX = e.touches[0].clientX;
          vmY = e.touches[0].clientY;
        }
      }, { passive: true });

      vmTouchpad.addEventListener("touchmove", (e) => {
        e.preventDefault();
        if (e.touches.length > 0) {
          const dx = (e.touches[0].clientX - vmX) * 1.8;
          const dy = (e.touches[0].clientY - vmY) * 1.8;
          sendMouseMoveRel(dx, dy);
          vmX = e.touches[0].clientX;
          vmY = e.touches[0].clientY;
        }
      }, { passive: false });
    }

    // Touch Buttons
    function bindPress(el, btnNum) {
      if (!el) return;
      const down = (e) => { e.preventDefault(); el.classList.add("active"); sendMouseButton(btnNum, true); };
      const up = (e) => { e.preventDefault(); el.classList.remove("active"); sendMouseButton(btnNum, false); };
      el.addEventListener("mousedown", down);
      el.addEventListener("mouseup", up);
      el.addEventListener("touchstart", down, { passive: false });
      el.addEventListener("touchend", up, { passive: false });
    }

    bindPress(btnLMB, 1);
    bindPress(btnRMB, 3);

    if (btnScrollUp) btnScrollUp.onclick = () => sendMouseWheel(-3);
    if (btnScrollDown) btnScrollDown.onclick = () => sendMouseWheel(3);

    if (btnToggleMouseMode) {
      btnToggleMouseMode.onclick = () => {
        mouseMode = (mouseMode === "trackpad") ? "direct" : "trackpad";
        btnToggleMouseMode.textContent = `MODE: ${mouseMode.toUpperCase()}`;
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
  // 7. Mobile Keyboard Trigger & Quick Input
  // -------------------------------------------------------------
  function initKeyboard() {
    function openMobileKeyboard() {
      mobileKeyboardInput.focus();
      mobileTextDrawer.style.display = "flex";
      mobileTextInput.focus();
    }

    if (btnMobileKeyboard) btnMobileKeyboard.onclick = openMobileKeyboard;
    if (btnCloseTextDrawer) btnCloseTextDrawer.onclick = () => { mobileTextDrawer.style.display = "none"; };

    if (btnSendText) {
      btnSendText.onclick = () => {
        const val = mobileTextInput.value;
        if (val) {
          sendTypedString(val);
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

    mobileKeyboardInput.addEventListener("keydown", (e) => sendKeyEvent(e.code || e.key, true));
    mobileKeyboardInput.addEventListener("keyup", (e) => sendKeyEvent(e.code || e.key, false));
  }

  // -------------------------------------------------------------
  // 8. In-Game Settings Modal
  // -------------------------------------------------------------
  function initInGameSettings() {
    if (btnOpenInGameSettings) {
      btnOpenInGameSettings.onclick = () => {
        inGameSettingsModal.style.display = "flex";
      };
    }
    if (btnCloseInGameSettings) {
      btnCloseInGameSettings.onclick = () => {
        inGameSettingsModal.style.display = "none";
      };
    }

    if (liveBitrateSlider && liveBitrateVal) {
      liveBitrateSlider.value = localStorage.getItem("nebula_bitrate") || 15;
      liveBitrateVal.textContent = `${liveBitrateSlider.value} Mbps`;
      liveBitrateSlider.oninput = (e) => {
        liveBitrateVal.textContent = `${e.target.value} Mbps`;
        if (wsStream && wsStream.readyState === WebSocket.OPEN) {
          wsStream.send(JSON.stringify({ bitrate_mbps: e.target.value }));
        }
      };
    }

    if (liveMouseSelect) {
      liveMouseSelect.value = mouseMode;
    }
    if (liveQueueSelect) {
      liveQueueSelect.value = queueMode;
    }

    if (btnApplyInGameSettings) {
      btnApplyInGameSettings.onclick = () => {
        mouseMode = liveMouseSelect.value;
        queueMode = liveQueueSelect.value;
        localStorage.setItem("nebula_mouse", mouseMode);
        localStorage.setItem("nebula_queue", queueMode);
        localStorage.setItem("nebula_bitrate", liveBitrateSlider.value);
        if (hudInput) {
          hudInput.textContent = (mouseMode === "locked") ? "Gaming Lock" : (isMobile ? `Touch (${mouseMode})` : "Desktop Absolute");
        }
        inGameSettingsModal.style.display = "none";
      };
    }
  }

  // -------------------------------------------------------------
  // 9. Fullscreen & Navigation Handlers
  // -------------------------------------------------------------
  function toggleFullscreen() {
    const doc = window.document;
    const docEl = doc.documentElement;
    const req = docEl.requestFullscreen || docEl.mozRequestFullScreen || docEl.webkitRequestFullScreen || docEl.msRequestFullscreen;
    const exit = doc.exitFullscreen || doc.mozCancelFullScreen || doc.webkitExitFullscreen || doc.msExitFullscreen;

    if (!doc.fullscreenElement && !doc.mozFullScreenElement && !doc.webkitFullscreenElement && !doc.msFullscreenElement) {
      if (req) req.call(docEl).catch(() => {});
    } else {
      if (exit) exit.call(doc);
    }
  }

  if (btnFullscreen) btnFullscreen.onclick = toggleFullscreen;
  if (btnRelock) btnRelock.onclick = lockMouseForGaming;
  if (btnToggleHud) {
    btnToggleHud.onclick = () => {
      hudOverlay.style.display = hudOverlay.style.display === "none" ? "flex" : "none";
    };
  }
  if (btnToggleBar) {
    btnToggleBar.onclick = () => {
      playerControls.style.display = (playerControls.style.display === "none" || !playerControls.style.display) ? "flex" : "none";
    };
  }
  if (btnExit) {
    btnExit.onclick = () => { window.location.href = "/"; };
  }

  // Startup execution
  initVideoDecoder();
  connectStream();
  initMouseAndKeyboard();
  initMobileTouch();
  initKeyboard();
  initInGameSettings();
})();
