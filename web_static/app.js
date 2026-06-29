const state = {
  batches: [],
  activeBatch: null,
  activeImage: null,
  points: [],
  mode: "one",
};

const els = {
  uploadForm: document.querySelector("#uploadForm"),
  batchName: document.querySelector("#batchName"),
  fileInput: document.querySelector("#fileInput"),
  fileCount: document.querySelector("#fileCount"),
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
  submitPoints: document.querySelector("#submitPoints"),
  oneMode: document.querySelector("#oneMode"),
  batchMode: document.querySelector("#batchMode"),
  refreshBtn: document.querySelector("#refreshBtn"),
  resultGrid: document.querySelector("#resultGrid"),
  resultMeta: document.querySelector("#resultMeta"),
  batchHint: document.querySelector("#batchHint"),
  sumImages: document.querySelector("#sumImages"),
  sumDone: document.querySelector("#sumDone"),
  sumTotal: document.querySelector("#sumTotal"),
  sumBad: document.querySelector("#sumBad"),
  sumRate: document.querySelector("#sumRate"),
};

function formatRate(rate) {
  return `${(Number(rate || 0) * 100).toFixed(2)}%`;
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
  if (!state.activeBatch && state.batches.length) {
    await openBatch(state.batches[0].id);
  } else if (state.activeBatch) {
    await openBatch(state.activeBatch.id, state.activeImage?.id);
  }
}

async function openBatch(batchId, imageId = null) {
  state.activeBatch = await api(`/api/batches/${batchId}`);
  const images = state.activeBatch.images || [];
  state.activeImage = images.find((image) => image.id === imageId) || images[0] || null;
  state.points = [];
  renderAll();
}

function renderAll() {
  renderBatches();
  renderSummary();
  renderImages();
  renderActiveImage();
  renderResults();
}

function renderBatches() {
  els.batchHint.textContent = `${state.batches.length} 个批次`;
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
        <span>${batch.done_count} 完成</span>
        <span>不良 ${batch.defective_chips}</span>
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
  els.sumBad.textContent = summary.defective_chips || 0;
  els.sumRate.textContent = formatRate(summary.defect_rate);
}

function statusText(status) {
  return {
    waiting_points: "待点角",
    processing: "处理中",
    done: "完成",
    failed: "失败",
  }[status] || status;
}

function renderImages() {
  const images = state.activeBatch?.images || [];
  if (!images.length) {
    els.imageList.innerHTML = `<div class="emptyState">暂无图片</div>`;
    return;
  }
  els.imageList.innerHTML = "";
  for (const image of images) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `imageItem ${state.activeImage?.id === image.id ? "active" : ""}`;
    const bad = image.summary ? `不良 ${image.summary.defective_chips}/${image.summary.total_chips}` : "";
    const err = image.error ? `错误：${image.error}` : "";
    button.innerHTML = `
      <strong>${image.filename}</strong>
      <span class="metaLine">
        <span class="status ${image.status}">${statusText(image.status)}</span>
        <span>${bad || err}</span>
      </span>
    `;
    button.addEventListener("click", () => {
      state.activeImage = image;
      state.points = [];
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
  if (els.mainImage.src !== location.origin + state.activeImage.url) {
    els.mainImage.src = state.activeImage.url;
  }
  els.pointStatus.textContent = `${state.points.length}/4`;
  drawPoints();
}

function renderResults() {
  const image = state.activeImage;
  if (!image?.summary) {
    els.resultMeta.textContent = image?.error || "";
    els.resultGrid.innerHTML = `<div class="emptyState">完成统计后显示结果图和网格检查图</div>`;
    return;
  }
  els.resultMeta.textContent = `不良 ${image.summary.defective_chips}/${image.summary.total_chips}，${formatRate(image.summary.defect_rate)}`;
  els.resultGrid.innerHTML = `
    <figure class="resultFigure">
      <img src="${image.summary.result_url}" alt="结果图">
      <figcaption>结果图：红框为不良，绿框为正常</figcaption>
    </figure>
    <figure class="resultFigure">
      <img src="${image.summary.debug_url}" alt="网格检查图">
      <figcaption>网格检查图：用于确认 401 个框是否对齐</figcaption>
    </figure>
  `;
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
    ctx.arc(x, y, 7, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#fff";
    ctx.fillText(String(index + 1), x + 10, y - 10);
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
  await loadBatches();
  await openBatch(payload.id);
}

async function appendFilesToActiveBatch(files) {
  if (!files.length) return;
  if (!state.activeBatch) {
    await createBatchWithFiles(files, `手机拍照_${new Date().toLocaleString()}`);
    return;
  }
  const form = new FormData();
  for (const file of files) form.append("files", file);
  await api(`/api/batches/${state.activeBatch.id}/images`, { method: "POST", body: form });
  await openBatch(state.activeBatch.id);
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

els.canvas.addEventListener("click", (event) => {
  if (!state.activeImage || state.points.length >= 4 || !els.mainImage.naturalWidth) return;
  const point = canvasPointToImage(event);
  if (!point) return;
  state.points.push(point);
  drawPoints();
});

els.resetPoints.addEventListener("click", () => {
  state.points = [];
  drawPoints();
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
  els.submitPoints.disabled = true;
  try {
    await api(`/api/batches/${state.activeBatch.id}/points`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image_id: state.activeImage.id, points: state.points, mode: state.mode }),
    });
    await openBatch(state.activeBatch.id, state.activeImage.id);
  } catch (error) {
    alert(error.message);
  } finally {
    els.submitPoints.disabled = false;
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

els.refreshBtn.addEventListener("click", loadBatches);
els.mainImage.addEventListener("load", drawPoints);
window.addEventListener("resize", drawPoints);

setInterval(() => {
  if (state.activeBatch) openBatch(state.activeBatch.id, state.activeImage?.id).catch(() => {});
}, 2500);

loadBatches().catch((error) => alert(error.message));
