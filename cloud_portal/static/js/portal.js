// Nebula Cloud Gaming - Portal Lobby Logic

document.addEventListener("DOMContentLoaded", () => {
  // 1. Telemetry Polling
  async function updateStatus() {
    try {
      const res = await fetch("/api/status");
      if (!res.ok) return;
      const data = await res.json();

      document.getElementById("gpuName").textContent = data.gpu || "NVIDIA GPU";
      document.getElementById("vramUsed").textContent = data.vram_used || "0";
      document.getElementById("vramTotal").textContent = data.vram_total || "--";

      // Update GTA status
      if (data.games && data.games.gtav) {
        const el = document.getElementById("gtaStatusText");
        if (el) {
          el.textContent = data.games.gtav.installed ? "Wine 9 • Ready" : "Run /gtav to install";
        }
      }
    } catch (e) {
      console.warn("Telemetry fetch error:", e);
    }
  }

  updateStatus();
  setInterval(updateStatus, 5000);

  // 2. Gamepad Detection
  function checkGamepads() {
    const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
    let found = false;
    for (let gp of gamepads) {
      if (gp && gp.connected) {
        found = true;
        document.getElementById("gamepadIndicator").textContent = `🎮 ${gp.id.split("(")[0].trim()} Connected`;
        document.getElementById("gamepadIndicator").style.color = "var(--accent-cyan)";
        break;
      }
    }
    if (!found) {
      document.getElementById("gamepadIndicator").textContent = "🎮 No Gamepad Detected";
      document.getElementById("gamepadIndicator").style.color = "var(--text-muted)";
    }
  }

  window.addEventListener("gamepadconnected", checkGamepads);
  window.addEventListener("gamepaddisconnected", checkGamepads);
  setInterval(checkGamepads, 3000);

  // 3. Settings Modal Handlers
  const modal = document.getElementById("settingsModal");
  const btnOpen = document.getElementById("btnOpenSettings");
  const btnClose = document.getElementById("btnCloseSettings");
  const bitrateSlider = document.getElementById("prefBitrate");
  const bitrateVal = document.getElementById("bitrateVal");

  if (btnOpen) {
    btnOpen.onclick = () => { modal.style.display = "flex"; };
  }
  if (btnClose) {
    btnClose.onclick = () => { modal.style.display = "none"; };
  }
  window.onclick = (e) => {
    if (e.target === modal) modal.style.display = "none";
  };

  if (bitrateSlider) {
    bitrateSlider.oninput = (e) => {
      bitrateVal.textContent = `${e.target.value} Mbps`;
    };
  }

  document.getElementById("btnSaveSettings").onclick = () => {
    localStorage.setItem("nebula_res", document.getElementById("prefResolution").value);
    localStorage.setItem("nebula_bitrate", bitrateSlider.value);
    localStorage.setItem("nebula_codec", document.getElementById("prefCodec").value);
    localStorage.setItem("nebula_mouse", document.getElementById("prefMouse").value);
    modal.style.display = "none";
  };
});

// Launch App Handler
async function launchApp(appId) {
  try {
    const btn = event.currentTarget.querySelector(".btn-play");
    if (btn) btn.textContent = "LAUNCHING...";

    const res = await fetch("/api/launch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ app: appId })
    });
    const data = await res.json();
    console.log("App launch response:", data);
  } catch (e) {
    console.warn("Launch API error:", e);
  }

  // Redirect to stream player
  window.location.href = `/player?app=${appId}`;
}

