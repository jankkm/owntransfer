(function () {
  function t(key, vars) {
    return window.__(key, vars);
  }

  function tn(singular, plural, n, vars) {
    return window.__n(singular, plural, n, vars);
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function inlineConfirm() {
    return window.InlineFileRemoveConfirm;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function parseErrorResponse(xhr) {
    if (xhr.status === 502 || xhr.status === 504) {
      return t("Upload timed out — try Retry");
    }
    if (xhr.status === 0) {
      return t("Network error");
    }
    let message = t("Upload failed");
    try {
      const payload = JSON.parse(xhr.responseText);
      if (typeof payload.detail === "string") {
        message = payload.detail;
      } else if (Array.isArray(payload.detail)) {
        message = payload.detail.map((item) => item.msg || item).join(", ");
      }
    } catch (_) {
      message = xhr.responseText || message;
    }
    return message;
  }

  function createUploadQueue(maxConcurrent) {
    const pending = [];
    let active = 0;

    function pump() {
      while (active < maxConcurrent && pending.length > 0) {
        const job = pending.shift();
        if (job.cancelled) continue;
        active++;
        Promise.resolve()
          .then(() => job.run())
          .catch(() => {})
          .finally(() => {
            active--;
            pump();
          });
      }
    }

    function enqueue(run) {
      const job = { run, cancelled: false };
      pending.push(job);
      pump();
      return function cancel() {
        job.cancelled = true;
      };
    }

    return { enqueue };
  }

  function readConcurrency(root, fallback) {
    const raw = root.dataset.uploadConcurrency;
    if (raw === undefined || raw === "") return fallback;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 1 ? value : fallback;
  }

  function initTransferFiles(root) {
    const uploadUrl = root.dataset.uploadUrl;
    const deleteUrlTemplate = root.dataset.deleteUrlTemplate;
    const titleEl = root.querySelector("[data-files-title]");
    const editBtn = root.querySelector("[data-files-edit-btn]");
    const doneBtn = root.querySelector("[data-files-done-btn]");
    const list = root.querySelector("[data-files-list]");
    const editPanel = root.querySelector("[data-files-edit-panel]");
    const dropzone = root.querySelector("[data-dropzone]");
    const input = root.querySelector("[data-file-input]");
    const errorEl = root.querySelector("[data-files-error]");
    const pendingUploads = new Set();
    const uploadStates = new Map();
    const concurrency = readConcurrency(root, 5);
    const uploadQueue = createUploadQueue(concurrency);

    function setError(message) {
      if (!errorEl) return;
      if (!message) {
        errorEl.textContent = "";
        errorEl.classList.add("hidden");
        return;
      }
      errorEl.textContent = message;
      errorEl.classList.remove("hidden");
    }

    function fileCount() {
      return list.querySelectorAll("[data-file-id]").length;
    }

    function updateTitle() {
      if (!titleEl) return;
      titleEl.textContent = t("Files (%(count)s)", { count: fileCount() });
    }

    function updateDoneState() {
      if (!doneBtn) return;
      doneBtn.disabled = pendingUploads.size > 0;
    }

    function createFileRow(fileId, name, sizeBytes, options = {}) {
      const { uploading = false, progress = 0, error = "" } = options;
      const row = document.createElement("li");
      row.dataset.fileId = fileId;
      row.className = "border border-slate-200 rounded-lg p-3 bg-white";

      const wrap = document.createElement("div");
      wrap.className = "flex items-start justify-between gap-3";

      const info = document.createElement("div");
      info.className = "min-w-0 flex-1";

      const nameEl = document.createElement("p");
      nameEl.className = "text-sm font-medium truncate";
      nameEl.dataset.fileName = "true";
      nameEl.textContent = name;

      const metaEl = document.createElement("p");
      metaEl.className = "text-xs text-slate-500";
      metaEl.dataset.fileMeta = "true";

      const progressWrap = document.createElement("div");
      progressWrap.className = "mt-2 h-1.5 bg-slate-100 rounded-full overflow-hidden";
      progressWrap.dataset.fileProgress = "true";

      const progressBar = document.createElement("div");
      progressBar.className = "h-full bg-[var(--primary)] transition-all duration-150";
      progressWrap.appendChild(progressBar);

      const errorElRow = document.createElement("p");
      errorElRow.className = "text-xs text-red-600 mt-1 hidden";
      errorElRow.dataset.fileError = "true";

      info.appendChild(nameEl);
      info.appendChild(metaEl);
      info.appendChild(progressWrap);
      info.appendChild(errorElRow);

      const actions = document.createElement("div");
      actions.className = "flex flex-col items-end gap-1 shrink-0";

      const retryBtn = document.createElement("button");
      retryBtn.type = "button";
      retryBtn.dataset.fileRetry = "";
      retryBtn.className = "text-xs text-primary hover:underline hidden";
      retryBtn.textContent = t("Retry");

      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.dataset.fileRemove = "";
      removeBtn.className = "text-xs text-slate-500 hover:text-red-600 shrink-0 hidden";
      removeBtn.textContent = t("Remove");

      actions.appendChild(retryBtn);
      actions.appendChild(removeBtn);
      wrap.appendChild(info);
      wrap.appendChild(actions);
      row.appendChild(wrap);

      updateRowState(row, { uploading, progress, error, sizeBytes });
      return row;
    }

    function updateRowState(row, { uploading, queued, progress, error, sizeBytes }) {
      const metaEl = row.querySelector("[data-file-meta]");
      const progressWrap = row.querySelector("[data-file-progress]");
      const progressBar = progressWrap ? progressWrap.firstElementChild : null;
      const errorElRow = row.querySelector("[data-file-error]");
      const removeBtn = row.querySelector("[data-file-remove]");
      const retryBtn = row.querySelector("[data-file-retry]");

      if (uploading) {
        metaEl.textContent = `${formatSize(sizeBytes || 0)} · ${t("Uploading…")}`;
        progressWrap.classList.remove("hidden");
        if (progressBar) progressBar.style.width = `${progress}%`;
        removeBtn.disabled = true;
        retryBtn.classList.add("hidden");
      } else if (queued) {
        metaEl.textContent = `${formatSize(sizeBytes || 0)} · ${t("Waiting…")}`;
        progressWrap.classList.remove("hidden");
        if (progressBar) progressBar.style.width = "0%";
        removeBtn.disabled = false;
        retryBtn.classList.add("hidden");
      } else if (error) {
        metaEl.textContent = `${formatSize(sizeBytes || 0)} · ${t("Upload failed")}`;
        progressWrap.classList.add("hidden");
        errorElRow.textContent = error;
        errorElRow.classList.remove("hidden");
        removeBtn.disabled = false;
        retryBtn.classList.remove("hidden");
      } else {
        metaEl.textContent = formatSize(sizeBytes || 0);
        progressWrap.classList.add("hidden");
        errorElRow.classList.add("hidden");
        removeBtn.disabled = false;
        retryBtn.classList.add("hidden");
      }
    }

    function applyRowPresentation(editing) {
      list.querySelectorAll("[data-file-id]").forEach((row) => {
        if (row.dataset.confirmActive === "true") return;
        if (editing) {
          row.className = "border border-slate-200 rounded-lg p-3 bg-white";
        } else {
          row.className = "text-slate-600";
        }
      });
    }

    function setEditMode(editing) {
      if (!editing) inlineConfirm()?.cancelActive();
      root.classList.toggle("is-editing", editing);
      editBtn.classList.toggle("hidden", editing);
      doneBtn.classList.toggle("hidden", !editing);
      editPanel.classList.toggle("hidden", !editing);
      list.querySelectorAll("[data-file-remove]").forEach((btn) => {
        btn.classList.toggle("hidden", !editing);
      });
      applyRowPresentation(editing);
      if (!editing) setError("");
    }

    function fileNameForRow(row) {
      const nameEl = row.querySelector("[data-file-name]");
      return nameEl ? nameEl.textContent.trim() : t("this file");
    }

    function rowFileId(row) {
      return row.dataset.fileId || row.getAttribute("data-file-id") || "";
    }

    function cancelPendingUpload(clientId) {
      const upload = uploadStates.get(clientId);
      if (!upload) return;
      upload.state.cancelQueue?.();
      if (upload.state.xhr) {
        upload.state.aborted = true;
        upload.state.xhr.abort();
      }
      pendingUploads.delete(clientId);
      uploadStates.delete(clientId);
      upload.row.remove();
      delete upload.row.dataset.pendingClientId;
      updateTitle();
      updateDoneState();
    }

    async function removeFile(row) {
      const pendingClientId = row.dataset.pendingClientId;
      if (pendingClientId) {
        cancelPendingUpload(pendingClientId);
        return;
      }

      const fileId = rowFileId(row);
      if (!fileId) return;

      const fileName = fileNameForRow(row);
      const isServer = row.dataset.serverFile === "true";
      const title = isServer ? t("Remove file from transfer?") : t("Remove file?");
      const message = isServer
        ? t('"%(name)s" will be permanently removed from this transfer. This cannot be undone.', { name: fileName })
        : t('Remove "%(name)s" from the list?', { name: fileName });

      const ic = inlineConfirm();
      if (!ic) {
        setError(t("Could not show confirmation dialog."));
        return;
      }

      const confirmed = await ic.ask(row, { title, message });
      if (!confirmed) return;

      if (isServer && fileCount() <= 1) {
        ic.restore(row);
        setError(t("Transfer must have at least one file."));
        return;
      }

      if (!isServer) {
        ic.clear(row);
        row.remove();
        updateTitle();
        return;
      }

      const deleteUrl = deleteUrlTemplate.replace("{id}", encodeURIComponent(fileId));
      setError("");

      try {
        const response = await fetch(deleteUrl, {
          method: "DELETE",
          credentials: "same-origin",
          headers: { "X-CSRF-Token": csrfToken() },
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || t("Could not remove file"));
        }
        ic.clear(row);
        row.remove();
        updateTitle();
      } catch (err) {
        ic.restore(row);
        setError(err.message || t("Could not remove file"));
      }
    }

    function retryUpload(clientId) {
      const upload = uploadStates.get(clientId);
      if (!upload) return;
      if (upload.state.cancelQueue) upload.state.cancelQueue();
      pendingUploads.add(clientId);
      upload.row.dataset.pendingClientId = clientId;
      updateDoneState();
      setError("");
      updateRowState(upload.row, { queued: true, sizeBytes: upload.file.size });
      upload.state.cancelQueue = uploadQueue.enqueue(() =>
        startUpload(clientId, upload.file, upload.row, upload.state)
      );
    }

    function uploadFile(file) {
      const clientId = crypto.randomUUID();
      const row = createFileRow(clientId, file.name, file.size, { queued: true });
      row.dataset.pendingClientId = clientId;
      list.appendChild(row);
      setEditMode(true);
      pendingUploads.add(clientId);
      updateDoneState();
      setError("");

      const state = { xhr: null, aborted: false, cancelQueue: null };
      uploadStates.set(clientId, { row, state, file });
      state.cancelQueue = uploadQueue.enqueue(() => startUpload(clientId, file, row, state));
    }

    function startUpload(clientId, file, row, state) {
      if (!pendingUploads.has(clientId)) return Promise.resolve();

      updateRowState(row, { uploading: true, progress: 0, sizeBytes: file.size });

      return new Promise((resolve) => {
        const xhr = new XMLHttpRequest();
        const formData = new FormData();
        formData.append("file", file, file.name);
        state.xhr = xhr;

        xhr.upload.addEventListener("progress", (event) => {
          if (!event.lengthComputable) return;
          updateRowState(row, {
            uploading: true,
            progress: Math.round((event.loaded / event.total) * 100),
            sizeBytes: file.size,
          });
        });

        xhr.addEventListener("load", () => {
          pendingUploads.delete(clientId);
          updateDoneState();
          row.dataset.uploading = "false";

          if (xhr.status >= 200 && xhr.status < 300) {
            uploadStates.delete(clientId);
            delete row.dataset.pendingClientId;
            const payload = JSON.parse(xhr.responseText);
            row.dataset.fileId = payload.id;
            row.dataset.serverFile = "true";
            updateRowState(row, { sizeBytes: payload.size_bytes });
            row.querySelector("[data-file-remove]").classList.remove("hidden");
            updateTitle();
            resolve();
            return;
          }

          updateRowState(row, { error: parseErrorResponse(xhr), sizeBytes: file.size });
          resolve();
        });

        xhr.addEventListener("error", () => {
          pendingUploads.delete(clientId);
          updateDoneState();
          row.dataset.uploading = "false";
          if (!state.aborted) {
            updateRowState(row, { error: t("Network error"), sizeBytes: file.size });
          }
          resolve();
        });

        xhr.open("POST", uploadUrl);
        xhr.withCredentials = true;
        xhr.setRequestHeader("X-CSRF-Token", csrfToken());
        xhr.send(formData);
      });
    }

    function addFiles(fileList) {
      [...fileList].forEach((file) => uploadFile(file));
    }

    editBtn.addEventListener("click", () => setEditMode(true));
    doneBtn.addEventListener("click", () => {
      if (pendingUploads.size > 0) return;
      setEditMode(false);
    });

    list.addEventListener("click", (event) => {
      const retryBtn = event.target.closest("[data-file-retry]");
      if (retryBtn && list.contains(retryBtn)) {
        event.preventDefault();
        event.stopPropagation();
        const row = retryBtn.closest("[data-file-id]");
        const clientId = row?.dataset.pendingClientId;
        if (clientId) retryUpload(clientId);
        return;
      }
      const removeBtn = event.target.closest("[data-file-remove]");
      if (!removeBtn || !list.contains(removeBtn)) return;
      event.preventDefault();
      event.stopPropagation();
      const row = removeBtn.closest("li[data-file-id], [data-file-id]");
      if (row) removeFile(row);
    });

    if (input) {
      input.addEventListener("change", () => {
        if (input.files && input.files.length) addFiles(input.files);
        input.value = "";
      });
    }

    if (dropzone) {
      ["dragenter", "dragover"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          dropzone.classList.add("border-primary", "bg-slate-50");
        });
      });
      ["dragleave", "drop"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          dropzone.classList.remove("border-primary", "bg-slate-50");
        });
      });
      dropzone.addEventListener("drop", (event) => {
        if (event.dataTransfer && event.dataTransfer.files.length) {
          addFiles(event.dataTransfer.files);
        }
      });
      dropzone.addEventListener("click", () => input.click());
    }

    list.querySelectorAll("[data-file-id]").forEach((row) => {
      const size = Number(row.dataset.fileSize || 0);
      const meta = row.querySelector("[data-file-meta]");
      if (meta && size) meta.textContent = formatSize(size);
    });

    applyRowPresentation(false);
    updateTitle();
  }

  document.querySelectorAll("[data-transfer-files]").forEach(initTransferFiles);
})();
