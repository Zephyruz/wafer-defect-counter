const state = {
  batches: [],
  activeBatch: null,
  activeImage: null,
  points: [],
  mode: "one",
  followLatest: true,
  previewReady: false,
  previewUrl: "",
  imageMode: "auto",
  correctionEditing: false,
  correctionImageId: null,
  repointingImageId: null,
  repointingAt: 0,
  view: {
    zoom: 1,
    panX: 0,
    panY: 0,
  },
  drag: null,
};

const els = {
  uploadForm: document.querySelector("#uploadForm"),
  batchName: document.querySelector("#batchName"),
  fileInput: document.querySelector("#fileInput"),
  fileCount: document.querySelector("#fileCount"),
  startBatchBtn: document.querySelector("#startBatchBtn"),
  stopBatchBtn: document.querySelector("#stopBatchBtn"),
  cameraInput: document.querySelector("#cameraInput"),
  appendInput: document.querySelector("#appendInput"),
  batchList: document.querySelector("#batchList"),
  imageList: document.querySelector("#imageList"),
  activeTitle: document.querySelector("#activeTitle"),
  mainImage: document.querySelector("#mainImage"),
  canvas: document.querySelector("#pointCanvas"),
  imageStage: document.querySelector("#imageStage"),
  pointStatus: document.querySelector("#pointStatus"),
  resetPoints: document.querySelector("#resetPoints"),
  excludeCurrent: document.querySelector("#excludeCurrent"),
  submitPoints: document.querySelector("#submitPoints"),
  zoomOut: document.querySelector("#zoomOut"),
  zoomReset: document.querySelector("#zoomReset"),
  zoomIn: document.querySelector("#zoomIn"),
  showOriginal: document.querySelector("#showOriginal"),
  showResult: document.querySelector("#showResult"),
  correctionPanel: document.querySelector("#correctionPanel"),
  ngCorrection: document.querySelector("#ngCorrection"),
  saveCorrection: document.querySelector("#saveCorrection"),
  correctionStatus: document.querySelector("#correctionStatus"),
  zoomLabel: document.querySelector("#zoomLabel"),
  oneMode: document.querySelector("#oneMode"),
  batchMode: document.querySelector("#batchMode"),
  refreshBtn: document.querySelector("#refreshBtn"),
  resultGrid: document.querySelector("#resultGrid"),
  resultMeta: document.querySelector("#resultMeta"),
  batchHint: document.querySelector("#batchHint"),
  sumImages: document.querySelector("#sumImages"),
  sumDone: document.querySelector("#sumDone"),
  sumTotal: document.querySelector("#sumTotal"),
  sumGood: document.querySelector("#sumGood"),
  sumBad: document.querySelector("#sumBad"),
  sumRate: document.querySelector("#sumRate"),
  currentOk: document.querySelector("#currentOk"),
  currentNg: document.querySelector("#currentNg"),
  currentStatus: document.querySelector("#currentStatus"),
  calibrationStatus: document.querySelector("#calibrationStatus"),
};

function formatRate(rate) {
  return `${(Number(rate || 0) * 100).toFixed(2)}%`;
}

function clampCorrection(value) {
  return Math.max(-20, Math.min(20, value));
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

async function loadBatches() {
  const payload = await api("/api/batches");
  state.batches = payload.batches;
  renderBatches();
  if (state.activeBatch && state.batches.some((batch) => batch.id === state.activeBatch.id && batch.stopped)) {
    state.activeBatch = null;
    state.activeImage = null;
    state.points = [];
    state.previewReady = false;
    state.previewUrl = "";
    state.imageMode = "auto";
  }
  if (!state.activeBatch) {
    const current = state.batches.find((batch) => !batch.stopped);
    if (current) {
      await openBatch(current.id);
    }
  } else {
    const currentSummary = state.batches.find((batch) => batch.id === state.activeBatch.id);
    const hasNewImage = currentSummary && state.activeBatch?.summary && currentSummary.image_count > state.activeBatch.summary.image_count;
    const activelyPicking = state.points.length > 0 && state.points.length < 4;
    const shouldFollow = !activelyPicking && (hasNewImage || state.followLatest);
    await openBatch(state.activeBatch.id, shouldFollow ? null : state.activeImage?.id, {
      preservePoints: activelyPicking || !hasNewImage,
      followLatest: shouldFollow,
    });
  }
}

async function openBatch(batchId, imageId = null, options = {}) {
  const previousImageId = state.activeImage?.id;
  state.activeBatch = await api(`/api/batches/${batchId}`);
  const images = state.activeBatch.images || [];
  const latest = [...images].reverse().find((image) => !image.excluded) || images[images.length - 1] || null;
  const selected = imageId && !options.followLatest ? images.find((image) => image.id === imageId) || latest : latest;
  state.activeImage = selected;
  if (previousImageId !== selected?.id) {
    state.correctionEditing = false;
    state.correctionImageId = selected?.id || null;
    state.repointingImageId = null;
    state.repointingAt = 0;
  }
  const activelyPickingSameImage = state.points.length > 0 && state.points.length < 4 && previousImageId === selected?.id;
  if ((!options.preservePoints && !activelyPickingSameImage) || previousImageId !== selected?.id) {
    state.points = [];
    state.previewReady = false;
    state.previewUrl = "";
    state.imageMode = "auto";
  }
  renderAll();
}

async function waitForImageResult(batchId, imageId, timeoutMs = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    await new Promise((resolve) => setTimeout(resolve, 300));
    await openBatch(batchId, imageId, { preservePoints: true, followLatest: false });
    if (state.activeImage?.id === imageId && state.activeImage.status === "done" && state.activeImage.summary) {
      return true;
    }
    if (state.activeImage?.id === imageId && state.activeImage.status === "failed") {
      return false;
    }
  }
  return false;
}

function renderAll() {
  renderBatches();
  renderSummary();
  renderImages();
  renderActiveImage();
  renderResults();
}

function renderBatches() {
  els.batchHint.textContent = `${state.batches.length} 条记录`;
  if (!state.batches.length) {
    els.batchList.innerHTML = `<div class="emptyState">还没有批次</div>`;
    return;
  }
  els.batchList.innerHTML = "";
  for (const batch of state.batches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `batchItem ${state.activeBatch?.id === batch.id ? "active" : ""}`;
    button.innerHTML = `
      <strong>${batch.name}</strong>
      <span class="metaLine">
        <span>${batch.image_count} 张</span>
        <span>OK ${batch.good_chips || 0}</span>
        <span>NG ${batch.defective_chips || 0}</span>
        <span>${batch.stopped ? "已停止" : batch.has_calibration ? "已校准" : "待校准"}</span>
      </span>
    `;
    button.addEventListener("click", () => openBatch(batch.id));
    els.batchList.appendChild(button);
  }
}

function renderSummary() {
  const summary = state.activeBatch?.summary || {};
  els.sumImages.textContent = summary.image_count || 0;
  els.sumDone.textContent = summary.done_count || 0;
  els.sumTotal.textContent = summary.total_chips || 0;
  els.sumGood.textContent = summary.good_chips || 0;
  els.sumBad.textContent = summary.defective_chips || 0;
  els.sumRate.textContent = formatRate(summary.defect_rate);
  els.calibrationStatus.textContent = state.activeBatch ? "每张手动" : "未开始";
}

function statusText(status) {
  return {
    waiting_points: "待点角",
    processing: "处理中",
    done: "完成",
    failed: "失败",
    excluded: "已作废",
  }[status] || status || "待处理";
}

function renderImages() {
  const images = state.activeBatch?.images || [];
  if (!images.length) {
    els.imageList.innerHTML = `<div class="emptyState">暂无图片</div>`;
    return;
  }
  els.imageList.innerHTML = "";
  for (const image of [...images].reverse()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `imageItem ${state.activeImage?.id === image.id ? "active" : ""}`;
    const ok = image.summary ? image.summary.total_chips - image.summary.defective_chips : 0;
    const result = image.summary ? `OK ${ok} / NG ${image.summary.defective_chips}` : "";
    const err = image.error ? `错误：${image.error}` : "";
    button.innerHTML = `
      <strong>${image.filename}</strong>
      <span class="metaLine">
        <span class="status ${image.status}">${statusText(image.status)}</span>
        <span>${result || err}</span>
      </span>
    `;
    button.addEventListener("click", () => {
      state.followLatest = false;
      state.activeImage = image;
      state.points = [];
      state.previewReady = false;
      state.previewUrl = "";
      state.imageMode = "auto";
      clearCorrectionEditing(image.id);
      renderActiveImage();
      renderImages();
      renderResults();
    });
    els.imageList.appendChild(button);
  }
}

function renderActiveImage() {
  els.activeTitle.textContent = state.activeBatch ? state.activeBatch.name : "未选择批次";
  if (!state.activeImage) {
    els.mainImage.style.display = "none";
    els.imageStage.querySelector(".emptyState").style.display = "block";
    drawPoints();
    return;
  }
  els.imageStage.querySelector(".emptyState").style.display = "none";
  els.mainImage.style.display = "block";
  let displayUrl = state.activeImage.url;
  if (state.previewUrl) {
    displayUrl = state.previewUrl;
  } else if (state.imageMode !== "original" && state.activeImage.summary?.result_url) {
    displayUrl = state.activeImage.summary.review_url || state.activeImage.summary.result_url;
  }
  if (els.mainImage.src !== new URL(displayUrl, location.origin).href) {
    els.mainImage.src = displayUrl;
    resetView();
  }
  els.pointStatus.textContent = `${state.points.length}/4`;
  applyView();
  drawPoints();
}

function clampZoom(value) {
  return Math.max(1, Math.min(8, value));
}

function resetView() {
  state.view.zoom = 1;
  state.view.panX = 0;
  state.view.panY = 0;
  applyView();
}

function applyView() {
  els.mainImage.style.transform = `translate(${state.view.panX}px, ${state.view.panY}px) scale(${state.view.zoom})`;
  els.zoomLabel.textContent = `${Math.round(state.view.zoom * 100)}%`;
  drawPoints();
}

function zoomBy(factor) {
  state.view.zoom = clampZoom(state.view.zoom * factor);
  if (state.view.zoom === 1) {
    state.view.panX = 0;
    state.view.panY = 0;
  }
  applyView();
}

function canAddPoint() {
  if (!state.activeImage || !els.mainImage.naturalWidth) return false;
  if (state.points.length >= 4) return false;
  if (state.previewUrl) return false;
  if (state.activeImage.summary && state.imageMode !== "original") return false;
  return true;
}

function undoLastPoint() {
  if (!state.points.length) return;
  state.followLatest = false;
  state.points.pop();
  state.previewReady = false;
  state.previewUrl = "";
  state.imageMode = "original";
  els.submitPoints.textContent = "预览网格";
  renderActiveImage();
  renderResults();
}

function clearCorrectionEditing(imageId = null) {
  state.correctionEditing = false;
  state.correctionImageId = imageId || state.activeImage?.id || null;
}

function renderResults() {
  const image = state.activeImage;
  if (!image) {
    els.currentOk.textContent = "0";
    els.currentNg.textContent = "0";
    els.currentStatus.textContent = "待拍摄";
    els.correctionPanel.classList.add("hiddenPanel");
    els.resultMeta.textContent = "";
    els.resultGrid.innerHTML = "";
    return;
  }

  els.currentStatus.textContent = statusText(image.status);
  if (state.previewUrl) {
    els.resultMeta.textContent = "网格预览：确认对齐后再统计";
    els.resultGrid.innerHTML = "";
    return;
  }

  if (state.repointingImageId === image.id && Number(image.finished_at || 0) > state.repointingAt) {
    state.repointingImageId = null;
    state.repointingAt = 0;
  }

  if (state.repointingImageId === image.id) {
    els.currentOk.textContent = "0";
    els.currentNg.textContent = "0";
    els.currentStatus.textContent = "重新点位中";
    els.correctionPanel.classList.add("hiddenPanel");
    els.ngCorrection.value = 0;
    els.correctionStatus.textContent = "";
    els.resultMeta.textContent = "重新点位后会生成新的结果";
    els.resultGrid.innerHTML = "";
    return;
  }

  if (!image.summary) {
    els.currentOk.textContent = "0";
    els.currentNg.textContent = "0";
    els.correctionPanel.classList.add("hiddenPanel");
    els.resultMeta.textContent = image.error || "";
    els.resultGrid.innerHTML = "";
    return;
  }

  const ok = image.summary.total_chips - image.summary.defective_chips;
  const rawNg = image.summary.raw_defective_chips ?? image.summary.defective_chips;
  const delta = image.summary.manual_ng_delta || 0;
  const editingThisImage = state.correctionEditing && state.correctionImageId === image.id;
  els.currentOk.textContent = ok;
  els.currentNg.textContent = image.summary.defective_chips;
  els.correctionPanel.classList.remove("hiddenPanel");
  if (!editingThisImage) {
    els.ngCorrection.value = delta;
  }
  const shownDelta = editingThisImage ? clampCorrection(Number.parseInt(els.ngCorrection.value || "0", 10)) : delta;
  const correctionLabel = editingThisImage ? "待保存" : "已修正";
  els.correctionStatus.textContent = shownDelta ? `原始 NG ${rawNg}，${correctionLabel} ${shownDelta > 0 ? "+" : ""}${shownDelta}` : `原始 NG ${rawNg}`;
  els.resultMeta.textContent = `当前张 OK ${ok}，NG ${image.summary.defective_chips}，${formatRate(image.summary.defect_rate)}`;
  els.resultGrid.innerHTML = "";
}

async function requestGridPreview() {
  if (!state.activeBatch || !state.activeImage || state.points.length !== 4) return;
  els.submitPoints.disabled = true;
  els.submitPoints.textContent = "正在生成预览";
  try {
    const payload = await api(`/api/batches/${state.activeBatch.id}/images/${state.activeImage.id}/preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points: state.points }),
    });
    state.previewReady = true;
    state.previewUrl = payload.preview_url;
    els.submitPoints.textContent = "确认网格并统计";
    renderActiveImage();
    renderResults();
  } catch (error) {
    state.previewReady = false;
    state.previewUrl = "";
    els.submitPoints.textContent = "预览网格";
    alert(error.message);
  } finally {
    els.submitPoints.disabled = false;
  }
}

function imageDrawRect() {
  const imgRect = els.mainImage.getBoundingClientRect();
  const stageRect = els.imageStage.getBoundingClientRect();
  return {
    left: imgRect.left - stageRect.left,
    top: imgRect.top - stageRect.top,
    width: imgRect.width,
    height: imgRect.height,
  };
}

function resizeCanvas() {
  const rect = els.imageStage.getBoundingClientRect();
  const scale = window.devicePixelRatio || 1;
  els.canvas.width = Math.round(rect.width * scale);
  els.canvas.height = Math.round(rect.height * scale);
  els.canvas.style.width = `${rect.width}px`;
  els.canvas.style.height = `${rect.height}px`;
  const ctx = els.canvas.getContext("2d");
  ctx.setTransform(scale, 0, 0, scale, 0, 0);
}

function drawPoints() {
  resizeCanvas();
  const ctx = els.canvas.getContext("2d");
  const stageRect = els.imageStage.getBoundingClientRect();
  ctx.clearRect(0, 0, stageRect.width, stageRect.height);
  els.pointStatus.textContent = `${state.points.length}/4`;
  if (!state.activeImage || !els.mainImage.naturalWidth) return;
  if (state.activeImage.summary && state.imageMode !== "original" && !state.previewUrl) return;
  const rect = imageDrawRect();
  ctx.lineWidth = 2;
  ctx.strokeStyle = "#ffe08a";
  ctx.fillStyle = "#e6382e";
  ctx.font = "16px Microsoft YaHei, sans-serif";
  const shown = state.points.map(([x, y]) => [
    rect.left + (x / els.mainImage.naturalWidth) * rect.width,
    rect.top + (y / els.mainImage.naturalHeight) * rect.height,
  ]);
  if (shown.length > 1) {
    ctx.beginPath();
    shown.forEach(([x, y], index) => index ? ctx.lineTo(x, y) : ctx.moveTo(x, y));
    if (shown.length === 4) ctx.closePath();
    ctx.stroke();
  }
  shown.forEach(([x, y], index) => {
    ctx.beginPath();
    ctx.arc(x, y, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.fillText(String(index + 1), x + 7, y - 7);
    ctx.fillStyle = "#e6382e";
  });
}

function canvasPointToImage(event) {
  const canvasRect = els.canvas.getBoundingClientRect();
  const rect = imageDrawRect();
  const x = event.clientX - canvasRect.left;
  const y = event.clientY - canvasRect.top;
  if (x < rect.left || x > rect.left + rect.width || y < rect.top || y > rect.top + rect.height) return null;
  return [
    ((x - rect.left) / rect.width) * els.mainImage.naturalWidth,
    ((y - rect.top) / rect.height) * els.mainImage.naturalHeight,
  ];
}

async function createBatchWithFiles(files, name = "") {
  const form = new FormData();
  form.set("name", name || els.batchName.value.trim());
  for (const file of files) form.append("files", file);
  const payload = await api("/api/batches", { method: "POST", body: form });
  state.followLatest = true;
  state.points = [];
  state.previewReady = false;
  state.previewUrl = "";
  state.imageMode = "auto";
  await loadBatches();
  await openBatch(payload.id);
}

async function createEmptyBatch(name = "") {
  const form = new FormData();
  form.set("name", name || els.batchName.value.trim() || `生产_${new Date().toLocaleString()}`);
  form.set("allow_empty", "1");
  const payload = await api("/api/batches", { method: "POST", body: form });
  state.followLatest = true;
  state.points = [];
  state.previewReady = false;
  state.previewUrl = "";
  state.imageMode = "auto";
  await loadBatches();
  await openBatch(payload.id);
}

async function appendFilesToActiveBatch(files) {
  if (!files.length) return;
  if (!state.activeBatch) {
    alert("请先点“开始新批次”");
    return;
  }
  const form = new FormData();
  for (const file of files) form.append("files", file);
  const payload = await api(`/api/batches/${state.activeBatch.id}/images`, { method: "POST", body: form });
  const newestId = payload.image_ids?.[payload.image_ids.length - 1];
  state.followLatest = true;
  await openBatch(state.activeBatch.id, newestId);
  await loadBatches();
}

els.fileInput.addEventListener("change", () => {
  const count = els.fileInput.files.length;
  els.fileCount.textContent = count ? `已选择 ${count} 张图片` : "支持一次上传多张";
});

els.uploadForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!els.fileInput.files.length) {
    alert("请先选择图片");
    return;
  }
  const button = els.uploadForm.querySelector("button");
  button.disabled = true;
  try {
    await createBatchWithFiles(els.fileInput.files);
    els.fileInput.value = "";
    els.fileCount.textContent = "支持一次上传多张";
  } catch (error) {
    alert(error.message);
  } finally {
    button.disabled = false;
  }
});

els.startBatchBtn.addEventListener("click", async () => {
  try {
    await createEmptyBatch();
  } catch (error) {
    alert(error.message);
  }
});

els.stopBatchBtn.addEventListener("click", async () => {
  if (!state.activeBatch) {
    alert("还没有当前批次");
    return;
  }
  if (!confirm("停止并清除当前数据？")) return;
  try {
    await api(`/api/batches/${state.activeBatch.id}/stop`, { method: "POST" });
    state.activeBatch = null;
    state.activeImage = null;
    state.points = [];
    state.previewReady = false;
    state.previewUrl = "";
    state.imageMode = "auto";
    state.followLatest = true;
    await loadBatches();
    renderAll();
  } catch (error) {
    alert(error.message);
  }
});

els.cameraInput.addEventListener("change", async () => {
  try {
    await appendFilesToActiveBatch(els.cameraInput.files);
    els.cameraInput.value = "";
  } catch (error) {
    alert(error.message);
  }
});

els.appendInput.addEventListener("change", async () => {
  try {
    await appendFilesToActiveBatch(els.appendInput.files);
    els.appendInput.value = "";
  } catch (error) {
    alert(error.message);
  }
});

els.canvas.addEventListener("pointerdown", (event) => {
  if (!state.activeImage || !els.mainImage.naturalWidth) return;
  state.drag = {
    pointerId: event.pointerId,
    startX: event.clientX,
    startY: event.clientY,
    lastX: event.clientX,
    lastY: event.clientY,
    moved: false,
  };
  els.imageStage.classList.add("dragging");
  els.canvas.setPointerCapture(event.pointerId);
});

els.canvas.addEventListener("pointermove", (event) => {
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  const dx = event.clientX - state.drag.lastX;
  const dy = event.clientY - state.drag.lastY;
  const total = Math.hypot(event.clientX - state.drag.startX, event.clientY - state.drag.startY);
  if (total > 4) state.drag.moved = true;
  if (state.view.zoom > 1 && state.drag.moved) {
    state.view.panX += dx;
    state.view.panY += dy;
    applyView();
  }
  state.drag.lastX = event.clientX;
  state.drag.lastY = event.clientY;
});

els.canvas.addEventListener("pointerup", (event) => {
  if (!state.drag || state.drag.pointerId !== event.pointerId) return;
  const wasDrag = state.drag.moved;
  state.drag = null;
  els.imageStage.classList.remove("dragging");
  if (els.canvas.hasPointerCapture(event.pointerId)) {
    els.canvas.releasePointerCapture(event.pointerId);
  }
  if (wasDrag || !canAddPoint()) return;
  const point = canvasPointToImage(event);
  if (!point) return;
  state.followLatest = false;
  state.points.push(point);
  drawPoints();
  if (state.points.length === 4) {
    requestGridPreview();
  }
});

els.canvas.addEventListener("pointercancel", () => {
  state.drag = null;
  els.imageStage.classList.remove("dragging");
});

els.imageStage.addEventListener("wheel", (event) => {
  if (!state.activeImage) return;
  event.preventDefault();
  zoomBy(event.deltaY < 0 ? 1.18 : 1 / 1.18);
}, { passive: false });

async function resetCurrentPoints() {
  if (!state.activeBatch || !state.activeImage) return;
  state.followLatest = false;
  state.points = [];
  state.previewReady = false;
  state.previewUrl = "";
  state.imageMode = "original";
  clearCorrectionEditing();
  state.repointingImageId = state.activeImage?.id || null;
  state.repointingAt = Date.now();
  els.ngCorrection.value = 0;
  els.correctionStatus.textContent = "";
  els.submitPoints.textContent = "预览网格";
  renderActiveImage();
  drawPoints();
  renderResults();
  try {
    await api(`/api/batches/${state.activeBatch.id}/images/${state.activeImage.id}/reset-correction`, {
      method: "POST",
    });
    if (state.activeImage?.summary) {
      state.activeImage.summary.manual_ng_delta = 0;
      state.activeImage.summary.defective_chips = state.activeImage.summary.raw_defective_chips ?? state.activeImage.summary.defective_chips;
      state.activeImage.summary.good_chips = state.activeImage.summary.total_chips - state.activeImage.summary.defective_chips;
      state.activeImage.summary.defect_rate = state.activeImage.summary.total_chips
        ? state.activeImage.summary.defective_chips / state.activeImage.summary.total_chips
        : 0;
    }
    await openBatch(state.activeBatch.id, state.activeImage.id, { preservePoints: true, followLatest: false });
  } catch (error) {
    alert(error.message);
  }
}

els.resetPoints.addEventListener("click", () => {
  resetCurrentPoints();
});

els.submitPoints.addEventListener("click", async () => {
  if (!state.activeBatch || !state.activeImage) {
    alert("请先选择图片");
    return;
  }
  if (state.points.length !== 4) {
    alert("需要按顺序点满四个点");
    return;
  }
  if (!state.previewReady) {
    await requestGridPreview();
    return;
  }
  els.submitPoints.disabled = true;
  els.submitPoints.textContent = "正在统计";
  try {
    const batchId = state.activeBatch.id;
    const imageId = state.activeImage.id;
    await api(`/api/batches/${batchId}/points`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_id: imageId, points: state.points, mode: "one" }),
    });
    state.followLatest = false;
    clearCorrectionEditing(imageId);
    state.previewReady = false;
    state.previewUrl = "";
    state.imageMode = "result";
    await waitForImageResult(batchId, imageId);
    await loadBatches();
  } catch (error) {
    alert(error.message);
  } finally {
    els.submitPoints.disabled = false;
    els.submitPoints.textContent = state.activeImage?.summary ? "确认网格并统计" : "预览网格";
  }
});

els.ngCorrection.addEventListener("input", () => {
  state.correctionEditing = true;
  state.correctionImageId = state.activeImage?.id || null;
  const delta = clampCorrection(Number.parseInt(els.ngCorrection.value || "0", 10));
  els.correctionStatus.textContent = Number.isFinite(delta)
    ? `待保存 ${delta > 0 ? "+" : ""}${delta}`
    : "请输入 -20 到 20";
});

els.ngCorrection.addEventListener("blur", () => {
  if (!state.correctionEditing) return;
  const delta = clampCorrection(Number.parseInt(els.ngCorrection.value || "0", 10));
  if (Number.isFinite(delta)) els.ngCorrection.value = delta;
});

els.ngCorrection.addEventListener("keydown", (event) => {
  if (event.key !== "Enter") return;
  event.preventDefault();
  if (!els.saveCorrection.disabled) els.saveCorrection.click();
});

els.saveCorrection.addEventListener("click", async () => {
  if (!state.activeBatch || !state.activeImage || !state.activeImage.summary) {
    alert("当前图片还没有统计结果");
    return;
  }
  if (state.correctionEditing && state.correctionImageId && state.correctionImageId !== state.activeImage.id) {
    alert("当前图片已经切换，请重新输入这张图片的修正值");
    clearCorrectionEditing(state.activeImage.id);
    renderResults();
    return;
  }
  const delta = clampCorrection(Number.parseInt(els.ngCorrection.value || "0", 10));
  if (!Number.isFinite(delta)) {
    alert("请输入 -20 到 20 之间的整数，比如 +2 或 -1");
    return;
  }
  els.ngCorrection.value = delta;
  els.saveCorrection.disabled = true;
  els.correctionStatus.textContent = "正在保存";
  try {
    await api(`/api/batches/${state.activeBatch.id}/images/${state.activeImage.id}/correction`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ delta }),
    });
    state.correctionEditing = false;
    state.correctionImageId = state.activeImage.id;
    state.followLatest = false;
    state.imageMode = "result";
    await openBatch(state.activeBatch.id, state.activeImage.id, { preservePoints: true, followLatest: false });
    await loadBatches();
  } catch (error) {
    alert(error.message);
  } finally {
    els.saveCorrection.disabled = false;
  }
});

els.excludeCurrent.addEventListener("click", async () => {
  if (!state.activeBatch || !state.activeImage) return;
  if (!confirm("作废当前张？作废后不会计入本批累计。")) return;
  try {
    await api(`/api/batches/${state.activeBatch.id}/images/${state.activeImage.id}/exclude`, { method: "POST" });
    await openBatch(state.activeBatch.id);
    await loadBatches();
  } catch (error) {
    alert(error.message);
  }
});

els.oneMode.addEventListener("click", () => {
  state.mode = "one";
  els.oneMode.classList.add("active");
  els.batchMode.classList.remove("active");
});

els.batchMode.addEventListener("click", () => {
  state.mode = "batch";
  els.batchMode.classList.add("active");
  els.oneMode.classList.remove("active");
});

els.zoomOut.addEventListener("click", () => zoomBy(1 / 1.25));
els.zoomIn.addEventListener("click", () => zoomBy(1.25));
els.zoomReset.addEventListener("click", resetView);
els.showOriginal.addEventListener("click", () => {
  state.followLatest = false;
  state.previewReady = false;
  state.previewUrl = "";
  state.imageMode = "original";
  renderActiveImage();
  renderResults();
});
els.showResult.addEventListener("click", () => {
  state.previewReady = false;
  state.previewUrl = "";
  state.imageMode = "result";
  renderActiveImage();
  renderResults();
});
els.refreshBtn.addEventListener("click", loadBatches);
els.mainImage.addEventListener("load", () => {
  applyView();
  drawPoints();
});
window.addEventListener("resize", drawPoints);
window.addEventListener("keydown", (event) => {
  const target = event.target;
  const isTyping = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  if (event.key === "Enter" && !isTyping && state.previewReady && !els.submitPoints.disabled) {
    event.preventDefault();
    els.submitPoints.click();
    return;
  }
  const wantsUndo = event.key === "Backspace" || (event.key.toLowerCase() === "z" && (event.ctrlKey || event.metaKey));
  if (wantsUndo && !isTyping && state.points.length > 0) {
    event.preventDefault();
    undoLastPoint();
  }
});

setInterval(() => {
  if (!state.activeBatch) return;
  const activelyPicking = state.points.length > 0 && state.points.length < 4;
  const shouldFollow = state.followLatest && !activelyPicking;
  openBatch(state.activeBatch.id, shouldFollow ? null : state.activeImage?.id, {
    preservePoints: !shouldFollow,
    followLatest: shouldFollow,
  }).catch(() => {});
}, 1500);

loadBatches().catch((error) => alert(error.message));
