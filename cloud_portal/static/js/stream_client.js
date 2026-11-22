// XGS - Xyris Cloud Gaming Ultra-Low Latency WebCodecs + WebAudio Streaming Client

(function() {
  const canvas = document.getElementById("streamCanvas");
  const ctx = canvas.getContext("2d", { alpha: false, desynchronized: true });
  const hudOverlay = document.getElementById("hudOverlay");
  const hudFps = document.getElementById("hudFps");
  const hudPing = document.getElementById("hudPing");
  const hudBitrate = document.getElementById("hudBitrate");
  const hudInput = document.getElementById("hudInput");
  const hudAudio = document.getElementById("hudAudio");

  // Single Settings Gear Button & In-Game Modal
  const btnSettingsGear = document.getElementById("btnSettingsGear");
  const inGameSettingsModal = document.getElementById("inGameSettingsModal");
  const btnCloseInGameSettings = document.getElementById("btnCloseInGameSettings");
  const btnApplyInGameSettings = document.getElementById("btnApplyInGameSettings");

  // In-Game Modal Action Buttons
  const btnRelock = document.getElementById("btnRelock");
  const btnFullscreen = document.getElementById("btnFullscreen");
  const btnMobileKeyboard = document.getElementById("btnMobileKeyboard");
  const btnVirtualMouse = document.getElementById("btnVirtualMouse");
  const btnToggleHud = document.getElementById("btnToggleHud");
  const btnToggleSound = document.getElementById("btnToggleSound");
  const btnExit = document.getElementById("btnExit");
  const liveBitrateSlider = document.getElementById("liveBitrateSlider");
  const liveBitrateVal = document.getElementById("liveBitrateVal");
  const liveMouseSelect = document.getElementById("liveMouseSelect");
  const liveQueueSelect = document.getElementById("liveQueueSelect");

  // Mobile Virtual Mouse & Keyboard Elements
  const virtualMouseOverlay = document.getElementById("virtualMouseOverlay");
  const btnCloseVirtualMouse = document.getElementById("btnCloseVirtualMouse");
  const btnToggleMouseMode = document.getElementById("btnToggleMouseMode");
  const vmTouchpad = document.getElementById("vmTouchpad");
  const btnLMB = document.getElementById("btnLMB");
  const btnRMB = document.getElementById("btnRMB");
  const btnScrollUp = document.getElementById("btnScrollUp");
  const btnScrollDown = document.getElementById("btnScrollDown");

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

  // WebAudio PCM Engine
  let audioCtx = null;
  let audioGain = null;
  let isAudioEnabled = true;
  let nextAudioTime = 0;

  let isPointerLocked = false;
  let frameCount = 0;
  let lastFpsTime = performance.now();
  let bytesReceived = 0;
  let lastBitrateTime = performance.now();

  const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);
  let mouseMode = localStorage.getItem("nebula_mouse") || "client";
  let queueMode = localStorage.getItem("nebula_queue") || "zero";
  let selectedCodec = localStorage.getItem("nebula_codec") || "h264";

  // -------------------------------------------------------------
  // 1. WebAudio Initialization (Instant 48kHz PCM Playback)
  // -------------------------------------------------------------
  function initAudio() {
    if (audioCtx) return;
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      audioCtx = new AudioContextClass({ latencyHint: "interactive", sampleRate: 48000 });
      audioGain = audioCtx.createGain();
      audioGain.gain.value = 1.0;
      audioGain.connect(audioCtx.destination);
      if (audioCtx.state === "suspended") {
        audioCtx.resume();
      }
      console.log("[Audio] WebAudio 48kHz pipeline initialized.");
    } catch (e) {
      console.warn("[Audio] Could not initialize WebAudio:", e);
    }
  }

  function playPcmChunk(pcmData) {
    if (!audioCtx || !isAudioEnabled) return;
    if (audioCtx.state === "suspended") {
      audioCtx.resume();
    }

    try {
      // pcmData is raw 16-bit signed stereo at 48000 Hz
      const int16View = new Int16Array(pcmData.buffer, pcmData.byteOffset, pcmData.byteLength / 2);
      const numSamples = int16View.length / 2;
      if (numSamples <= 0) return;

      const audioBuffer = audioCtx.createBuffer(2, numSamples, 48000);
      const left = audioBuffer.getChannelData(0);
      const right = audioBuffer.getChannelData(1);

      for (let i = 0; i < numSamples; i++) {
        left[i] = int16View[i * 2] / 32768.0;
        right[i] = int16View[i * 2 + 1] / 32768.0;
      }

      const source = audioCtx.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioGain);

      const now = audioCtx.currentTime;
      // Resynchronize if buffer fell behind or drifted ahead (> 80ms)
      if (nextAudioTime < now || nextAudioTime - now > 0.08) {
        nextAudioTime = now + 0.01;
      }
      source.start(nextAudioTime);
      nextAudioTime += audioBuffer.duration;
    } catch (e) {
      // Audio error catch
    }
  }

  // -------------------------------------------------------------
  // 2. WebCodecs VideoDecoder (Zero Buffer, Ultra-Low Latency)
  // -------------------------------------------------------------
  function initVideoDecoder() {
    if (!window.VideoDecoder) {
      alert("WebCodecs VideoDecoder is not supported in this browser. Please use Chrome, Edge, or Brave.");
      return;
    }

    if (videoDecoder && videoDecoder.state !== "closed") {
      try { videoDecoder.close(); } catch (e) {}
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

    const codecStr = (selectedCodec === "av1") ? "av01.0.05M.08" : "avc1.64002a";
    videoDecoder.configure({
      codec: codecStr,
      optimizeForLatency: true
    });
  }

  // -------------------------------------------------------------
  // 3. WebSocket Streams (Video & Audio Out, Control In)
  // -------------------------------------------------------------
  function connectStream() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;

    // Stream WebSocket
    wsStream = new WebSocket(`${protocol}//${host}/ws/stream`);
    wsStream.binaryType = "arraybuffer";

    wsStream.onopen = () => {
      console.log("[Stream] Connected to XGS streaming engine");
      const savedBitrate = localStorage.getItem("nebula_bitrate") || 4;
      wsStream.send(JSON.stringify({ bitrate_mbps: savedBitrate, codec: selectedCodec }));
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

        // Anti-lag zero queue: drop non-key delta frames if decoder is busy
        if (queueMode === "zero" && !isKey && videoDecoder.decodeQueueSize > 0) {
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

      // Tag 0x02: Audio PCM Chunk (Zero-Delay 48kHz Stereo)
      else if (tag === 0x02) {
        initAudio();
        const pcmData = data.subarray(1);
        playPcmChunk(pcmData);
      }
    };

    wsStream.onclose = () => {
      console.warn("[Stream] Connection closed. Reconnecting in 2s...");
      setTimeout(connectStream, 2000);
    };

    // Control WebSocket
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
  // 4. Precision Ping & FPS Loop
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

      const now = performance.now();
      const elapsed = (now - lastFpsTime) / 1000;
      if (elapsed >= 1.0) {
        const fps = Math.round(frameCount / elapsed);
        if (hudFps) hudFps.textContent = `${fps} FPS`;
        frameCount = 0;
        lastFpsTime = now;
      }

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
  // 5. Binary Input Dispatchers (Sub-0.05ms XTest)
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
  // 6. Gaming Mouse Lock & Desktop Inputs
  // -------------------------------------------------------------
  function lockMouseForGaming() {
    try {
      const p = canvas.requestPointerLock({ unadjustedMovement: true });
      if (p && p.catch) p.catch(() => canvas.requestPointerLock());
    } catch (e) {
      canvas.requestPointerLock();
    }
  }

  function initInputs() {
    canvas.addEventListener("click", () => {
      initAudio();
      if (mouseMode === "locked" && !isPointerLocked) {
        lockMouseForGaming();
      }
    });

    document.addEventListener("pointerlockchange", () => {
      isPointerLocked = (document.pointerLockElement === canvas);
      if (btnRelock) {
        btnRelock.textContent = isPointerLocked ? "🔓 UNLOCK (ESC)" : "🔒 LOCK (FPS)";
      }
      if (hudInput) {
        hudInput.textContent = isPointerLocked ? "3D Locked" : "Client Cursor";
      }
    });

    window.addEventListener("mousemove", (e) => {
      if (isPointerLocked || mouseMode === "locked") {
        sendMouseMoveRel(e.movementX, e.movementY);
      } else {
        // Client-side mouse cursor by default
        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;
        const x = Math.max(0, Math.min(canvas.width, (e.clientX - rect.left) * scaleX));
        const y = Math.max(0, Math.min(canvas.height, (e.clientY - rect.top) * scaleY));
        sendMouseMoveAbs(x, y);
      }
    });

    window.addEventListener("mousedown", (e) => {
      initAudio();
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
  // 7. Mobile Touch & Virtual Trackpad
  // -------------------------------------------------------------
  function initMobileTouch() {
    let touchStartTime = 0;
    let lastTouchX = null;
    let lastTouchY = null;
    let isDragging = false;
    const clientCursorDot = document.getElementById("clientCursorDot");

    canvas.addEventListener("touchstart", (e) => {
      initAudio();
      if (e.touches.length === 1) {
        const t = e.touches[0];
        const rect = canvas.getBoundingClientRect();
        const x = (t.clientX - rect.left) * (canvas.width / rect.width);
        const y = (t.clientY - rect.top) * (canvas.height / rect.height);
        touchStartTime = performance.now();
        lastTouchX = t.clientX;
        lastTouchY = t.clientY;
        isDragging = false;

        if (clientCursorDot) {
          clientCursorDot.style.left = `${t.clientX}px`;
          clientCursorDot.style.top = `${t.clientY}px`;
          clientCursorDot.style.display = "block";
        }

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

        if (clientCursorDot) {
          clientCursorDot.style.left = `${t.clientX}px`;
          clientCursorDot.style.top = `${t.clientY}px`;
        }

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
      if (clientCursorDot) {
        setTimeout(() => { if (!isDragging && clientCursorDot) clientCursorDot.style.display = "none"; }, 400);
      }
      if (!isDragging && performance.now() - touchStartTime < 250) {
        sendMouseButton(1, true);
        setTimeout(() => sendMouseButton(1, false), 50);
      }
      isDragging = false;
    });

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
      };
    }

    if (btnVirtualMouse) {
      btnVirtualMouse.onclick = () => {
        virtualMouseOverlay.style.display = (virtualMouseOverlay.style.display === "none" || !virtualMouseOverlay.style.display) ? "flex" : "none";
        inGameSettingsModal.style.display = "none";
      };
    }
    if (btnCloseVirtualMouse) {
      btnCloseVirtualMouse.onclick = () => {
        virtualMouseOverlay.style.display = "none";
      };
    }
  }

  // -------------------------------------------------------------
  // 8. Mobile Native Keyboard
  // -------------------------------------------------------------
  function initKeyboard() {
    function openKeyboard() {
      inGameSettingsModal.style.display = "none";
      mobileKeyboardInput.focus();
      mobileTextDrawer.style.display = "flex";
      mobileTextInput.focus();
    }

    if (btnMobileKeyboard) btnMobileKeyboard.onclick = openKeyboard;
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
  // 9. In-Game Settings Modal (Triggered by ⚙️ Gear Icon)
  // -------------------------------------------------------------
  function initInGameSettings() {
    if (btnSettingsGear) {
      btnSettingsGear.onclick = () => {
        inGameSettingsModal.style.display = (inGameSettingsModal.style.display === "flex") ? "none" : "flex";
      };
    }
    if (btnCloseInGameSettings) {
      btnCloseInGameSettings.onclick = () => {
        inGameSettingsModal.style.display = "none";
      };
    }
    const liveCodecSelect = document.getElementById("liveCodecSelect");
    const liveCodecVal = document.getElementById("liveCodecVal");

    if (btnApplyInGameSettings) {
      btnApplyInGameSettings.onclick = () => {
        mouseMode = liveMouseSelect ? liveMouseSelect.value : mouseMode;
        queueMode = liveQueueSelect ? liveQueueSelect.value : queueMode;
        if (liveCodecSelect) {
          const newCodec = liveCodecSelect.value;
          if (newCodec !== selectedCodec) {
            selectedCodec = newCodec;
            localStorage.setItem("nebula_codec", selectedCodec);
            initVideoDecoder();
            if (wsStream && wsStream.readyState === WebSocket.OPEN) {
              wsStream.send(JSON.stringify({ codec: selectedCodec }));
            }
          }
        }
        localStorage.setItem("nebula_mouse", mouseMode);
        localStorage.setItem("nebula_queue", queueMode);
        if (liveBitrateSlider) localStorage.setItem("nebula_bitrate", liveBitrateSlider.value);
        inGameSettingsModal.style.display = "none";
      };
    }

    if (liveBitrateSlider && liveBitrateVal) {
      liveBitrateSlider.value = localStorage.getItem("nebula_bitrate") || 4;
      liveBitrateVal.textContent = `${liveBitrateSlider.value} Mbps (Low Latency)`;
      liveBitrateSlider.oninput = (e) => {
        liveBitrateVal.textContent = `${e.target.value} Mbps (Low Latency)`;
        if (wsStream && wsStream.readyState === WebSocket.OPEN) {
          wsStream.send(JSON.stringify({ bitrate_mbps: e.target.value }));
        }
      };
    }

    if (liveMouseSelect) liveMouseSelect.value = mouseMode;
    if (liveQueueSelect) liveQueueSelect.value = queueMode;
    if (liveCodecSelect) {
      liveCodecSelect.value = selectedCodec;
      if (liveCodecVal) liveCodecVal.textContent = selectedCodec.toUpperCase();
      liveCodecSelect.onchange = (e) => {
        selectedCodec = e.target.value;
        if (liveCodecVal) liveCodecVal.textContent = selectedCodec.toUpperCase();
      };
    }

    if (btnToggleSound) {
      btnToggleSound.onclick = () => {
        isAudioEnabled = !isAudioEnabled;
        btnToggleSound.textContent = isAudioEnabled ? "🔊 AUDIO (ON)" : "🔇 AUDIO (MUTED)";
        if (hudAudio) hudAudio.textContent = isAudioEnabled ? "Active (48kHz)" : "Muted";
      };
    }

    if (btnToggleHud) {
      btnToggleHud.onclick = () => {
        hudOverlay.style.display = (hudOverlay.style.display === "none") ? "flex" : "none";
      };
    }

    if (btnRelock) {
      btnRelock.onclick = () => {
        inGameSettingsModal.style.display = "none";
        lockMouseForGaming();
      };
    }

    if (btnFullscreen) {
      btnFullscreen.onclick = () => {
        const doc = window.document;
        const docEl = doc.documentElement;
        const req = docEl.requestFullscreen || docEl.webkitRequestFullScreen || docEl.mozRequestFullScreen || docEl.msRequestFullscreen;
        const exit = doc.exitFullscreen || doc.webkitExitFullscreen || doc.mozCancelFullScreen || doc.msExitFullscreen;
        if (!doc.fullscreenElement && !doc.webkitFullscreenElement) {
          if (req) req.call(docEl).catch(() => {});
        } else {
          if (exit) exit.call(doc);
        }
      };
    }

    if (btnExit) {
      btnExit.onclick = () => { window.location.href = "/"; };
    }
  }

  // Startup initialization
  initVideoDecoder();
  connectStream();
  initInputs();
  initMobileTouch();
  initKeyboard();
  initInGameSettings();
})();
