const state = {
  batches: [],
  activeBatchId: "",
};

const els = {
  refreshBtn: document.querySelector("#refreshBtn"),
  createBatchBtn: document.querySelector("#createBatchBtn"),
  batchName: document.querySelector("#batchName"),
  batchHint: document.querySelector("#batchHint"),
  batchList: document.querySelector("#batchList"),
  activeName: document.querySelector("#activeName"),
  uploadStatus: document.querySelector("#uploadStatus"),
  cameraInput: document.querySelector("#cameraInput"),
  fileInput: document.querySelector("#fileInput"),
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
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
  els.batchHint.textContent = `${state.batches.length} 个批次`;
  const active = state.batches.find((batch) => batch.id === state.activeBatchId);
  els.activeName.textContent = active ? "当前生产中" : "未开始";
  els.uploadStatus.textContent = active
    ? `已拍 ${active.image_count} 张，电脑端会自动看到新照片。`
    : "先点“开始”，再拍照上传。";

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
        <span>${batch.stopped ? "已停止" : batch.has_calibration ? "已校准" : "待校准"}</span>
      </span>
    `;
    if (batch.stopped) {
      button.disabled = true;
    }
    button.addEventListener("click", () => {
      state.activeBatchId = batch.id;
      render();
    });
    els.batchList.appendChild(button);
  }
}

async function createEmptyBatch() {
  const form = new FormData();
  form.set("name", els.batchName.value.trim() || `生产_${new Date().toLocaleString()}`);
  form.set("allow_empty", "1");
  const payload = await api("/api/batches", { method: "POST", body: form });
  state.activeBatchId = payload.id;
  await loadBatches();
}

async function uploadFiles(files) {
  if (!files.length) return;
  if (!state.activeBatchId) {
    alert("请先点“开始”");
    return;
  }
  els.uploadStatus.textContent = "正在上传...";

  const form = new FormData();
  for (const file of files) form.append("files", file);
  await api(`/api/batches/${state.activeBatchId}/images`, { method: "POST", body: form });

  els.uploadStatus.textContent = "上传完成，电脑端会自动刷新。";
  await loadBatches();
}

els.refreshBtn.addEventListener("click", loadBatches);
els.createBatchBtn.addEventListener("click", async () => {
  try {
    await createEmptyBatch();
  } catch (error) {
    alert(error.message);
  }
});

els.cameraInput.addEventListener("change", async () => {
  try {
    await uploadFiles(els.cameraInput.files);
    els.cameraInput.value = "";
  } catch (error) {
    els.uploadStatus.textContent = "上传失败";
    alert(error.message);
  }
});

els.fileInput.addEventListener("change", async () => {
  try {
    await uploadFiles(els.fileInput.files);
    els.fileInput.value = "";
  } catch (error) {
    els.uploadStatus.textContent = "上传失败";
    alert(error.message);
  }
});

setInterval(loadBatches, 3000);
loadBatches().catch((error) => alert(error.message));
