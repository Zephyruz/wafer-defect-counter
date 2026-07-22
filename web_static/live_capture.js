const state = {
  batches: [],
  activeBatchId: "",
  stream: null,
  isUploading: false,
  lastShotAt: 0,
  zoom: 1,
  hardwareZoom: false,
  videoDevices: [],
  selectedDeviceId: "",
  cameraMode: "rear-hd",
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
  cameraSelect: document.querySelector("#cameraSelect"),
  cameraModeSelect: document.querySelector("#cameraModeSelect"),
  fallbackInput: document.querySelector("#fallbackInput"),
  cameraOverlay: document.querySelector("#cameraOverlay"),
  activeBatchName: document.querySelector("#activeBatchName"),
  imageCount: document.querySelector("#imageCount"),
  lastKey: document.querySelector("#lastKey"),
  statusText: document.querySelector("#statusText"),
};

async function fetchWithTimeout(path, options = {}) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 30000);
  try {
    return await fetch(path, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timeoutId);
  }
}

async function api(path, options = {}) {
  const response = await fetchWithTimeout(path, { cache: "no-store", ...options });
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

async function handleRemoteRefocus() {
  const token = activeBatch()?.camera_refocus_token || "";
  if (!token || token === state.lastRefocusToken || state.isUploading || state.isRefocusing) return;
  if (!state.stream || !videoReady()) {
    setStatus("电脑请求重新对焦，请先开启相机");
    return;
  }
  state.isRefocusing = true;
  state.isUploading = true;
  render();
  setStatus("收到电脑指令，正在重新对焦...");
  try {
    await refocusCamera();
    state.lastRefocusToken = token;
    setStatus("重新对焦完成，可以拍照");
  } catch (error) {
    setStatus(`重新对焦失败：${error.message}`);
  } finally {
    state.isRefocusing = false;
    state.isUploading = false;
    render();
  }
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

async function loadCameraDevices() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const devices = await navigator.mediaDevices.enumerateDevices();
  state.videoDevices = devices.filter((device) => device.kind === "videoinput");
  if (state.videoDevices.length) {
    const pdaBackCamera = state.videoDevices.find((device) => /camera2\s*0/i.test(device.label) && /facing\s*back/i.test(device.label));
    const backCamera = state.videoDevices.find((device) => /back|rear|environment|后|背/i.test(device.label));
    const selectedStillExists = state.videoDevices.some((device) => device.deviceId === state.selectedDeviceId);
    if (!selectedStillExists || (!state.selectedDeviceId && pdaBackCamera)) {
      state.selectedDeviceId = (pdaBackCamera || backCamera || state.videoDevices[0]).deviceId;
    }
  }
  renderCameraSelect();
}

function renderCameraSelect() {
  els.cameraSelect.innerHTML = "";
  if (!state.videoDevices.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "自动选择";
    els.cameraSelect.appendChild(option);
    return;
  }
  state.videoDevices.forEach((device, index) => {
    const option = document.createElement("option");
    option.value = device.deviceId;
    option.textContent = device.label || `摄像头 ${index + 1}`;
    option.selected = device.deviceId === state.selectedDeviceId;
    els.cameraSelect.appendChild(option);
  });
}

function cameraConstraints() {
  if (state.selectedDeviceId) {
    return [
      { deviceId: { exact: state.selectedDeviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      { deviceId: { exact: state.selectedDeviceId }, width: { ideal: 1280 }, height: { ideal: 720 } },
      { deviceId: { exact: state.selectedDeviceId }, width: { ideal: 640 }, height: { ideal: 480 } },
    ];
  }

  const modes = {
    "rear-hd": [
      { facingMode: { exact: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
    ],
    "rear-low": [
      { facingMode: { exact: "environment" }, width: { ideal: 640 }, height: { ideal: 480 } },
      { facingMode: { ideal: "environment" }, width: { ideal: 640 }, height: { ideal: 480 } },
    ],
    front: [
      { facingMode: { exact: "user" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      { facingMode: { ideal: "user" }, width: { ideal: 640 }, height: { ideal: 480 } },
    ],
    "any-low": [
      { width: { ideal: 640 }, height: { ideal: 480 } },
      { width: { ideal: 320 }, height: { ideal: 240 } },
    ],
    auto: [
      { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } },
      { width: { ideal: 1280 }, height: { ideal: 720 } },
      { width: { ideal: 640 }, height: { ideal: 480 } },
      true,
    ],
  };
  return modes[state.cameraMode] || modes.auto;
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("这个浏览器不支持网页实时相机");
  }

  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
  }

  let lastError = null;
  for (const video of cameraConstraints()) {
    try {
      state.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
      break;
    } catch (error) {
      lastError = error;
    }
  }
  if (!state.stream) throw lastError || new Error("相机开启失败");

  els.video.srcObject = state.stream;
  await els.video.play();
  await waitForVideoReady();
  await loadCameraDevices();
  setupZoom();
  els.cameraOverlay.classList.add("hiddenPanel");
  setStatus("相机已开启");
  render();
}

async function waitForVideoReady() {
  const started = Date.now();
  while (!videoReady() && Date.now() - started < 1800) {
    await new Promise((resolve) => setTimeout(resolve, 80));
  }
  if (!videoReady()) {
    setStatus("相机已打开，但暂时没有画面");
  }
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

async function setupFocus() {
  const [track] = state.stream?.getVideoTracks() || [];
  const capabilities = track?.getCapabilities ? track.getCapabilities() : {};
  state.focusModes = Array.isArray(capabilities.focusMode) ? capabilities.focusMode : [];
  if (!track?.applyConstraints || !state.focusModes.includes("continuous")) return;
  try {
    await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] });
  } catch (_error) {
    state.focusModes = [];
  }
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

async function uploadFiles(files) {
  if (!files.length) return;
  if (!activeBatch()) {
    setStatus("请先创建或选择批次");
    return;
  }
  const form = new FormData();
  for (const file of files) form.append("files", file);
  state.isUploading = true;
  render();
  setStatus("正在上传备用照片...");
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

function drawCurrentVideoFrame() {
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
}

async function refocusCamera() {
  const [track] = state.stream?.getVideoTracks() || [];
  if (track?.applyConstraints && state.focusModes.includes("single-shot")) {
    try {
      await track.applyConstraints({ advanced: [{ focusMode: "single-shot" }] });
      await new Promise((resolve) => setTimeout(resolve, 750));
      if (state.focusModes.includes("continuous")) {
        await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] });
      }
      return;
    } catch (_error) {
      // Fall through and restart the stream to ask the phone to focus again.
    }
  }
  await startCamera();
  await new Promise((resolve) => setTimeout(resolve, 900));
}

async function unusedLegacyCaptureAndUpload(trigger = "button") {
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
  try {
    setStatus("正在检查清晰度...");
    drawCurrentVideoFrame();
    setStatus("正在拍照...");
    // Automatic focus checks must never sit in the shutter path. Refocusing is manual only.
    const sharpness = Number.POSITIVE_INFINITY;
    if (false) {
      setStatus("画面模糊，正在自动重新对焦...");
      try {
        await refocusCamera();
        drawCurrentVideoFrame();
        sharpness = currentFrameSharpness();
      } catch (_focusError) {
        // Keep the original frame and continue; focus assistance must never lock the shutter.
      }
      if (sharpness < MIN_SHARPNESS_SCORE) {
        setStatus("对焦仍不稳定，继续拍照上传...");
      }
    }

    const blob = await new Promise((resolve) => els.canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) throw new Error("截图失败");

    const filename = `live_${new Date().toISOString().replace(/[:.]/g, "-")}_${trigger}.jpg`;
    const form = new FormData();
    form.append("files", blob, filename);
    setStatus("正在上传...");
    await api(`/api/batches/${state.activeBatchId}/images`, { method: "POST", body: form });
    setStatus("上传完成");
    await loadBatches();
  } catch (error) {
    setStatus(error.message === "截图失败" ? "截图失败" : "上传失败");
    alert(error.message);
  } finally {
    state.isUploading = false;
    render();
  }
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
  try {
    setStatus("正在拍照...");
    drawCurrentVideoFrame();
    const blob = await new Promise((resolve) => els.canvas.toBlob(resolve, "image/jpeg", 0.92));
    if (!blob) throw new Error("截图失败");

    const filename = `live_${new Date().toISOString().replace(/[:.]/g, "-")}_${trigger}.jpg`;
    const form = new FormData();
    form.append("files", blob, filename);
    setStatus("正在上传...");
    await api(`/api/batches/${state.activeBatchId}/images`, { method: "POST", body: form });
    setStatus("上传完成");
    await loadBatches();
  } catch (error) {
    setStatus(error.message === "截图失败" ? "截图失败" : "上传失败");
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
els.cameraSelect.addEventListener("change", async () => {
  state.selectedDeviceId = els.cameraSelect.value;
  if (!state.stream) return;
  stopCamera();
  try {
    await startCamera();
  } catch (error) {
    setStatus("相机开启失败");
    els.cameraOverlay.textContent = error.message;
    els.cameraOverlay.classList.remove("hiddenPanel");
  }
});
els.cameraModeSelect.addEventListener("change", async () => {
  state.cameraMode = els.cameraModeSelect.value;
  state.selectedDeviceId = "";
  renderCameraSelect();
  if (!state.stream) return;
  stopCamera();
  try {
    await startCamera();
  } catch (error) {
    setStatus("相机开启失败");
    els.cameraOverlay.textContent = error.message;
    els.cameraOverlay.classList.remove("hiddenPanel");
  }
});
els.fallbackInput.addEventListener("change", async () => {
  try {
    await uploadFiles(els.fallbackInput.files);
  } finally {
    els.fallbackInput.value = "";
  }
});
els.createBatchBtn.addEventListener("click", async () => {
  try {
    await createEmptyBatch();
  } catch (error) {
    alert(error.message);
  }
});

setInterval(() => loadBatches().catch(() => {}), 1000);
loadBatches().catch((error) => alert(error.message));
loadCameraDevices().catch(() => {});
