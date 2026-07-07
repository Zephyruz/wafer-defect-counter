const state = {
  batches: [],
  activeBatchId: "",
  stream: null,
  isUploading: false,
  lastShotAt: 0,
  zoom: 1,
  hardwareZoom: false,
};

const els = {
  refreshBtn: document.querySelector("#refreshBtn"),
  startCameraBtn: document.querySelector("#startCameraBtn"),
  shutterBtn: document.querySelector("#shutterBtn"),
  createBatchBtn: document.querySelector("#createBatchBtn"),
  batchName: document.querySelector("#batchName"),
  batchHint: document.querySelector("#batchHint"),
  batchList: document.querySelector("#batchList"),
  video: document.querySelector("#video"),
  canvas: document.querySelector("#canvas"),
  zoomSlider: document.querySelector("#zoomSlider"),
  zoomValue: document.querySelector("#zoomValue"),
  cameraOverlay: document.querySelector("#cameraOverlay"),
  activeBatchName: document.querySelector("#activeBatchName"),
  imageCount: document.querySelector("#imageCount"),
  lastKey: document.querySelector("#lastKey"),
  statusText: document.querySelector("#statusText"),
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

function setStatus(text) {
  els.statusText.textContent = text;
}

function activeBatch() {
  return state.batches.find((batch) => batch.id === state.activeBatchId);
}

async function loadBatches() {
  const payload = await api("/api/batches");
  state.batches = payload.batches;
  if (state.activeBatchId && !state.batches.some((batch) => batch.id === state.activeBatchId && !batch.stopped)) {
    state.activeBatchId = "";
  }
  if (!state.activeBatchId) {
    const current = state.batches.find((batch) => !batch.stopped);
    if (current) state.activeBatchId = current.id;
  }
  render();
}

function render() {
  const active = activeBatch();
  els.batchHint.textContent = `${state.batches.length} 个批次`;
  els.activeBatchName.textContent = active ? active.name : "未开始";
  els.imageCount.textContent = active ? active.image_count : "0";
  els.shutterBtn.disabled = !active || !state.stream || state.isUploading;
  els.startCameraBtn.textContent = state.stream ? "关闭相机" : "开启相机";
  els.startCameraBtn.classList.toggle("secondary", !!state.stream);

  if (!state.batches.length) {
    els.batchList.innerHTML = `<div class="emptyState">还没有批次</div>`;
    return;
  }

  els.batchList.innerHTML = "";
  for (const batch of state.batches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `batchItem ${batch.id === state.activeBatchId ? "active" : ""}`;
    button.innerHTML = `
      <strong>${batch.name}</strong>
      <span class="metaLine">
        <span>${batch.image_count} 张</span>
        <span>${batch.stopped ? "已停止" : "可上传"}</span>
      </span>
    `;
    button.disabled = batch.stopped;
    button.addEventListener("click", () => {
      state.activeBatchId = batch.id;
      render();
    });
    els.batchList.appendChild(button);
  }
}

async function createEmptyBatch() {
  const form = new FormData();
  form.set("name", els.batchName.value.trim() || `实时拍照_${new Date().toLocaleString()}`);
  form.set("allow_empty", "1");
  const payload = await api("/api/batches", { method: "POST", body: form });
  state.activeBatchId = payload.id;
  setStatus("新批次已创建");
  await loadBatches();
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("这个浏览器不支持网页实时相机");
  }

  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
  }

  state.stream = await navigator.mediaDevices.getUserMedia({
    video: {
      facingMode: { ideal: "environment" },
      width: { ideal: 1920 },
      height: { ideal: 1080 },
    },
    audio: false,
  });

  els.video.srcObject = state.stream;
  await els.video.play();
  setupZoom();
  els.cameraOverlay.classList.add("hiddenPanel");
  setStatus("相机已开启");
  render();
}

function stopCamera() {
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
  }
  state.stream = null;
  state.hardwareZoom = false;
  els.video.srcObject = null;
  els.video.style.transform = "";
  els.cameraOverlay.textContent = "相机已关闭";
  els.cameraOverlay.classList.remove("hiddenPanel");
  setStatus("相机已关闭");
  render();
}

function setupZoom() {
  const [track] = state.stream?.getVideoTracks() || [];
  const capabilities = track?.getCapabilities ? track.getCapabilities() : {};
  state.hardwareZoom = !!capabilities.zoom;
  const min = capabilities.zoom?.min || 1;
  const max = Math.min(capabilities.zoom?.max || 4, 8);
  const step = capabilities.zoom?.step || 0.1;
  els.zoomSlider.min = String(min);
  els.zoomSlider.max = String(max);
  els.zoomSlider.step = String(step);
  state.zoom = Math.max(min, Math.min(Number(els.zoomSlider.value) || 1, max));
  els.zoomSlider.value = String(state.zoom);
  applyZoom();
}

async function applyZoom() {
  state.zoom = Number(els.zoomSlider.value) || 1;
  els.zoomValue.textContent = `${state.zoom.toFixed(1)}x`;

  const [track] = state.stream?.getVideoTracks() || [];
  if (state.hardwareZoom && track?.applyConstraints) {
    try {
      await track.applyConstraints({ advanced: [{ zoom: state.zoom }] });
      els.video.style.transform = "";
      setStatus(`硬件变焦 ${state.zoom.toFixed(1)}x`);
      return;
    } catch (_error) {
      state.hardwareZoom = false;
    }
  }

  els.video.style.transform = `scale(${state.zoom})`;
  els.video.style.transformOrigin = "center center";
  setStatus(`数字变焦 ${state.zoom.toFixed(1)}x`);
}

function videoReady() {
  return els.video.videoWidth > 0 && els.video.videoHeight > 0;
}

async function captureAndUpload(trigger = "button") {
  const now = Date.now();
  if (state.isUploading || now - state.lastShotAt < 900) return;
  if (!activeBatch()) {
    setStatus("请先创建或选择批次");
    return;
  }
  if (!state.stream || !videoReady()) {
    setStatus("请先开启相机");
    return;
  }

  state.isUploading = true;
  state.lastShotAt = now;
  render();
  setStatus("正在上传...");

  const width = els.video.videoWidth;
  const height = els.video.videoHeight;
  els.canvas.width = width;
  els.canvas.height = height;
  const ctx = els.canvas.getContext("2d");
  if (state.hardwareZoom || state.zoom <= 1) {
    ctx.drawImage(els.video, 0, 0, width, height);
  } else {
    const cropWidth = width / state.zoom;
    const cropHeight = height / state.zoom;
    const sx = (width - cropWidth) / 2;
    const sy = (height - cropHeight) / 2;
    ctx.drawImage(els.video, sx, sy, cropWidth, cropHeight, 0, 0, width, height);
  }

  const blob = await new Promise((resolve) => els.canvas.toBlob(resolve, "image/jpeg", 0.92));
  if (!blob) {
    state.isUploading = false;
    setStatus("截图失败");
    render();
    return;
  }

  const filename = `live_${new Date().toISOString().replace(/[:.]/g, "-")}_${trigger}.jpg`;
  const form = new FormData();
  form.append("files", blob, filename);

  try {
    await api(`/api/batches/${state.activeBatchId}/images`, { method: "POST", body: form });
    setStatus("上传完成");
    await loadBatches();
  } catch (error) {
    setStatus("上传失败");
    alert(error.message);
  } finally {
    state.isUploading = false;
    render();
  }
}

function keyLabel(event) {
  const parts = [];
  if (event.ctrlKey) parts.push("Ctrl");
  if (event.altKey) parts.push("Alt");
  if (event.shiftKey) parts.push("Shift");
  if (event.metaKey) parts.push("Meta");
  parts.push(event.code || event.key || "Unknown");
  return parts.join("+");
}

function shouldTriggerShutter(event) {
  const key = event.key;
  const code = event.code;
  return (
    key === "Enter" ||
    key === " " ||
    key === "Spacebar" ||
    key === "AudioVolumeUp" ||
    key === "AudioVolumeDown" ||
    code === "Enter" ||
    code === "Space" ||
    code === "VolumeUp" ||
    code === "VolumeDown"
  );
}

document.addEventListener("keydown", (event) => {
  const label = keyLabel(event);
  els.lastKey.textContent = label;
  setStatus(`收到按键 ${label}`);
  if (shouldTriggerShutter(event)) {
    event.preventDefault();
    captureAndUpload(label);
  }
});

els.refreshBtn.addEventListener("click", loadBatches);
els.startCameraBtn.addEventListener("click", async () => {
  if (state.stream) {
    stopCamera();
    return;
  }
  try {
    await startCamera();
  } catch (error) {
    setStatus("相机开启失败");
    els.cameraOverlay.textContent = error.message;
    els.cameraOverlay.classList.remove("hiddenPanel");
  }
});
els.shutterBtn.addEventListener("click", () => captureAndUpload("button"));
els.zoomSlider.addEventListener("input", applyZoom);
els.createBatchBtn.addEventListener("click", async () => {
  try {
    await createEmptyBatch();
  } catch (error) {
    alert(error.message);
  }
});

setInterval(loadBatches, 3000);
loadBatches().catch((error) => alert(error.message));
