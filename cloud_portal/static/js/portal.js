// XGS - Xyris Cloud Gaming Portal Lobby Logic

document.addEventListener("DOMContentLoaded", () => {
  // 1. Telemetry Polling
  async function updateStatus() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();

      const elGpu = document.getElementById("gpuName");
      const elUsed = document.getElementById("vramUsed");
      const elTotal = document.getElementById("vramTotal");

      if (elGpu) elGpu.textContent = data.gpu || "NVIDIA L4";
      if (elUsed) elUsed.textContent = data.vram_used || "0";
      if (elTotal) elTotal.textContent = data.vram_total || "23 GB";
    } catch (e) {
      console.warn("Telemetry fetch error:", e);
    }
  }

  updateStatus();
  setInterval(updateStatus, 5000);

  // 2. Gamepad API Detection
  function checkGamepads() {
    const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
    let found = false;
    const el = document.getElementById("gamepadIndicator");
    if (!el) return;

    for (let gp of gamepads) {
      if (gp && gp.connected) {
        found = true;
        el.textContent = `🎮 ${gp.id.split("(")[0].trim().toUpperCase()} CONNECTED`;
        el.style.color = "#ffffff";
        break;
      }
    }
    if (!found) {
      el.textContent = "🎮 NO GAMEPAD DETECTED";
      el.style.color = "var(--text-muted)";
    }
  }

  window.addEventListener("gamepadconnected", checkGamepads);
  window.addEventListener("gamepaddisconnected", checkGamepads);
  setInterval(checkGamepads, 3000);

  // 3. Settings Modal Handlers
  const modal = document.getElementById("settingsModal");
  const btnOpen = document.getElementById("btnOpenSettings");
  const btnClose = document.getElementById("btnCloseSettings");
  const btnCancel = document.getElementById("btnCancelSettings");
  const btnSave = document.getElementById("btnSaveSettings");
  const bitrateSlider = document.getElementById("prefBitrate");
  const bitrateVal = document.getElementById("bitrateVal");

  // Load saved preferences
  if (localStorage.getItem("nebula_res") && document.getElementById("prefResolution")) {
    document.getElementById("prefResolution").value = localStorage.getItem("nebula_res");
  }
  if (localStorage.getItem("nebula_fps") && document.getElementById("prefFps")) {
    document.getElementById("prefFps").value = localStorage.getItem("nebula_fps");
  }
  if (localStorage.getItem("nebula_bitrate") && bitrateSlider) {
    bitrateSlider.value = localStorage.getItem("nebula_bitrate");
    if (bitrateVal) bitrateVal.textContent = `${bitrateSlider.value} Mbps`;
  }
  if (localStorage.getItem("nebula_codec") && document.getElementById("prefCodec")) {
    document.getElementById("prefCodec").value = localStorage.getItem("nebula_codec");
  }
  if (localStorage.getItem("nebula_mouse") && document.getElementById("prefMouse")) {
    document.getElementById("prefMouse").value = localStorage.getItem("nebula_mouse");
  }
  if (localStorage.getItem("nebula_queue") && document.getElementById("prefQueue")) {
    document.getElementById("prefQueue").value = localStorage.getItem("nebula_queue");
  }

  if (btnOpen) {
    btnOpen.onclick = () => { modal.style.display = "flex"; };
  }
  if (btnClose) {
    btnClose.onclick = () => { modal.style.display = "none"; };
  }
  if (btnCancel) {
    btnCancel.onclick = () => { modal.style.display = "none"; };
  }
  window.onclick = (e) => {
    if (e.target === modal) modal.style.display = "none";
  };

  if (bitrateSlider && bitrateVal) {
    bitrateSlider.oninput = (e) => {
      bitrateVal.textContent = `${e.target.value} Mbps`;
    };
  }

  if (btnSave) {
    btnSave.onclick = () => {
      localStorage.setItem("nebula_res", document.getElementById("prefResolution").value);
      localStorage.setItem("nebula_fps", document.getElementById("prefFps").value);
      localStorage.setItem("nebula_bitrate", bitrateSlider.value);
      localStorage.setItem("nebula_codec", document.getElementById("prefCodec").value);
      localStorage.setItem("nebula_mouse", document.getElementById("prefMouse").value);
      localStorage.setItem("nebula_queue", document.getElementById("prefQueue").value);
      modal.style.display = "none";
    };
  }
});

// -------------------------------------------------------------
// Cinematic Launch Animation Handler
// -------------------------------------------------------------
function launchGameWithAnimation(appId, appName) {
  const overlay = document.getElementById("launchOverlay");
  const targetTitle = document.getElementById("launchTargetName");
  const bar = document.getElementById("launchProgressBar");
  const t1 = document.getElementById("tickerLine1");
  const t2 = document.getElementById("tickerLine2");
  const t3 = document.getElementById("tickerLine3");

  if (targetTitle) targetTitle.textContent = `LAUNCHING ${appName.toUpperCase()}...`;
  if (overlay) overlay.style.display = "flex";
  if (bar) bar.style.width = "0%";

  // Trigger backend app launcher
  fetch("/api/launch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ app: appId })
  }).catch(() => {});

  // Progress animation sequence
  setTimeout(() => {
    if (bar) bar.style.width = "35%";
    if (t1) t1.classList.add("active");
  }, 150);

  setTimeout(() => {
    if (bar) bar.style.width = "75%";
    if (t2) t2.classList.add("active");
  }, 500);

  setTimeout(() => {
    if (bar) bar.style.width = "100%";
    if (t3) t3.classList.add("active");
  }, 900);

  // Smooth redirection into player
  setTimeout(() => {
    window.location.href = `/player?app=${appId}`;
  }, 1250);
}
