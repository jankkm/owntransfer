(function () {
  const DEFAULT_CONCURRENCY = 5;

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

  function t(key, vars) {
    return window.__(key, vars);
  }

  function csrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.content : "";
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function parseUploadError(xhr) {
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
      if (xhr.responseText) message = xhr.responseText;
    }
    return message;
  }

  function readConcurrency(root, fallback) {
    const raw = root.dataset.uploadConcurrency;
    if (raw === undefined || raw === "") return fallback;
    const value = Number(raw);
    return Number.isFinite(value) && value >= 1 ? value : fallback;
  }

  function readBlocklist(root) {
    const raw = root.dataset.fileTypeBlocklist;
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.map((ext) => String(ext).toLowerCase()) : [];
    } catch (_) {
      return [];
    }
  }

  function readMaxFileSize(root) {
    const value = Number(root.dataset.maxFileSizeBytes);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function fileExtension(filename) {
    const base = String(filename || "").split(/[/\\]/).pop() || "";
    const dot = base.lastIndexOf(".");
    return dot >= 0 ? base.slice(dot).toLowerCase() : "";
  }

  function isExtensionBlocked(filename, blocklist) {
    if (!blocklist.length) return false;
    return blocklist.includes(fileExtension(filename));
  }

  function blockedFileMessage(filename) {
    return t("File type not allowed: %(filename)s", { filename });
  }

  function oversizedFileMessage(maxFileSizeBytes) {
    return t("File exceeds maximum size (%(max_mb)s MB)", {
      max_mb: Math.floor(maxFileSizeBytes / (1024 * 1024)),
    });
  }

  function initFilePicker(root) {
    const uploadUrl = root.dataset.uploadUrl;
    const deleteUrlTemplate = root.dataset.deleteUrlTemplate;
    const clearAllUrl = root.dataset.clearAllUrl || "";
    const stagingBatch = root.dataset.stagingBatch || "";
    const restoreStaged = root.dataset.restoreStaged === "true";
    if (root.dataset.persistBatchInUrl === "true" && stagingBatch) {
      const currentUrl = new URL(window.location.href);
      currentUrl.searchParams.set("batch", stagingBatch);
      window.history.replaceState({}, "", currentUrl);
    }
    const input = root.querySelector("[data-file-input]");
    const list = root.querySelector("[data-file-list]");
    const dropzone = root.querySelector("[data-dropzone]");
    const emptyState = root.querySelector("[data-empty-state]");
    const errorEl = root.querySelector("[data-file-error]");
    const stagedFileIdsInput = root.querySelector("[data-staged-file-ids]");
    const restoreHint = root.querySelector("[data-restore-hint]");
    const clearAllBtn = root.querySelector("[data-clear-all]");
    const form = root.closest("form");
    const submitBtn = form ? form.querySelector("[data-submit-btn]") : null;
    const requireFiles = root.dataset.requireFiles === "true";
    const requiredMessage = root.dataset.requiredMessage || t("Add at least one file");
    const concurrency = readConcurrency(root, DEFAULT_CONCURRENCY);
    const blocklist = readBlocklist(root);
    const maxFileSize = readMaxFileSize(root);
    const uploadQueue = createUploadQueue(concurrency);
    const files = new Map();
    let restoredFromServer = false;

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

    function updateEmptyState() {
      if (!emptyState) return;
      emptyState.classList.toggle("hidden", files.size > 0);
    }

    function updateClearAllState() {
      if (!clearAllBtn) return;
      clearAllBtn.classList.toggle("hidden", files.size === 0);
    }

    function updateRestoreHint() {
      if (!restoreHint) return;
      restoreHint.classList.toggle("hidden", !restoredFromServer || files.size === 0);
    }

    function isBusy(entry) {
      return entry.status === "uploading" || entry.status === "queued";
    }

    function updateSubmitState() {
      if (!submitBtn || !requireFiles) return;
      submitBtn.disabled = [...files.values()].some(isBusy);
    }

    function notifyChange() {
      const hasReady = [...files.values()].some((entry) => entry.status === "done");
      if (hasReady) setError("");
      if (stagedFileIdsInput) {
        stagedFileIdsInput.value = JSON.stringify(
          [...files.values()]
            .filter((entry) => entry.status === "done" && entry.serverId)
            .map((entry) => entry.serverId)
        );
      }
      updateClearAllState();
      updateRestoreHint();
      root.dispatchEvent(new CustomEvent("filepicker:change", { bubbles: true }));
      updateSubmitState();
    }

    function statusText(entry) {
      if (entry.status === "queued") return t("Waiting…");
      if (entry.status === "uploading") return t("Uploading…");
      if (entry.status === "done") return t("Ready");
      if (entry.status === "error") return entry.error || t("Upload failed");
      return "";
    }

    function ensureRow(clientId) {
      let row = list.querySelector(`[data-client-id="${clientId}"]`);
      if (row) return row;
      row = document.createElement("li");
      row.dataset.clientId = clientId;
      row.className = "border border-slate-200 rounded-lg p-3 bg-white";
      row.innerHTML = `
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium truncate" data-fp-name></p>
            <p class="text-xs text-slate-500" data-fp-meta></p>
            <div class="mt-2 h-1.5 bg-slate-100 rounded-full overflow-hidden" data-fp-progress-wrap>
              <div class="h-full bg-[var(--primary)] transition-all duration-150" data-fp-progress></div>
            </div>
            <p class="text-xs text-red-600 mt-1 hidden" data-fp-error></p>
          </div>
          <div class="flex flex-col items-end gap-1 shrink-0">
            <button type="button" data-fp-retry class="text-xs text-primary hover:underline hidden">${t("Retry")}</button>
            <button type="button" data-fp-remove class="text-xs text-slate-500 hover:text-red-600">${t("Remove")}</button>
          </div>
        </div>
      `;
      list.appendChild(row);
      return row;
    }

    function renderEntry(clientId, entry) {
      const row = ensureRow(clientId);
      row.querySelector("[data-fp-name]").textContent = entry.name;
      row.querySelector("[data-fp-meta]").textContent = `${formatSize(entry.size)} · ${statusText(entry)}`;

      const progressWrap = row.querySelector("[data-fp-progress-wrap]");
      const progressBar = row.querySelector("[data-fp-progress]");
      const showProgress = entry.status === "queued" || entry.status === "uploading";
      progressWrap.classList.toggle("hidden", !showProgress);
      progressBar.style.width = `${entry.progress}%`;

      const errorLine = row.querySelector("[data-fp-error]");
      if (entry.status === "error" && entry.error) {
        errorLine.textContent = entry.error;
        errorLine.classList.remove("hidden");
      } else {
        errorLine.textContent = "";
        errorLine.classList.add("hidden");
      }

      const retryBtn = row.querySelector("[data-fp-retry]");
      retryBtn.classList.toggle("hidden", entry.status !== "error" || entry.blocked);

      const removeBtn = row.querySelector("[data-fp-remove]");
      removeBtn.disabled = false;
      removeBtn.textContent = isBusy(entry) ? t("Cancel") : t("Remove");
    }

    function queueUpload(clientId) {
      const entry = files.get(clientId);
      if (!entry || !entry.file) return;
      const validationError = isExtensionBlocked(entry.name, blocklist)
        ? blockedFileMessage(entry.name)
        : maxFileSize !== null && entry.size > maxFileSize
          ? oversizedFileMessage(maxFileSize)
          : null;
      if (validationError) {
        entry.status = "error";
        entry.blocked = true;
        entry.error = validationError;
        entry.progress = 0;
        entry.cancelQueue = null;
        renderEntry(clientId, entry);
        notifyChange();
        return;
      }
      entry.status = "queued";
      entry.progress = 0;
      entry.error = null;
      entry.blocked = false;
      entry.aborted = false;
      entry.cancelQueue = uploadQueue.enqueue(() => startUpload(clientId));
      renderEntry(clientId, entry);
      notifyChange();
    }

    function uploadEntry(clientId, file) {
      const validationError = isExtensionBlocked(file.name, blocklist)
        ? blockedFileMessage(file.name)
        : maxFileSize !== null && file.size > maxFileSize
          ? oversizedFileMessage(maxFileSize)
          : null;
      const blocked = validationError !== null;
      const entry = {
        file,
        name: file.name,
        size: file.size,
        status: blocked ? "error" : "queued",
        progress: 0,
        serverId: null,
        error: validationError,
        blocked,
        xhr: null,
        aborted: false,
        cancelQueue: null,
      };
      files.set(clientId, entry);
      updateEmptyState();
      if (blocked) {
        renderEntry(clientId, entry);
        notifyChange();
        return;
      }
      queueUpload(clientId);
    }

    function startUpload(clientId) {
      const entry = files.get(clientId);
      if (!entry || !entry.file) return Promise.resolve();

      entry.status = "uploading";
      renderEntry(clientId, entry);

      return new Promise((resolve) => {
        const file = entry.file;
        const xhr = new XMLHttpRequest();
        entry.xhr = xhr;

        xhr.upload.addEventListener("progress", (event) => {
          if (!event.lengthComputable) return;
          entry.progress = Math.round((event.loaded / event.total) * 100);
          const row = list.querySelector(`[data-client-id="${clientId}"]`);
          if (!row) return;
          row.querySelector("[data-fp-progress]").style.width = `${entry.progress}%`;
        });

        xhr.addEventListener("load", () => {
          entry.xhr = null;
          if (xhr.status >= 200 && xhr.status < 300) {
            const payload = JSON.parse(xhr.responseText);
            entry.status = "done";
            entry.progress = 100;
            entry.serverId = payload.id;
            renderEntry(clientId, entry);
            notifyChange();
            resolve();
            return;
          }
          entry.status = "error";
          entry.error = parseUploadError(xhr);
          renderEntry(clientId, entry);
          notifyChange();
          resolve();
        });

        xhr.addEventListener("error", () => {
          entry.xhr = null;
          if (entry.aborted) {
            resolve();
            return;
          }
          entry.status = "error";
          entry.error = t("Network error");
          renderEntry(clientId, entry);
          notifyChange();
          resolve();
        });

        xhr.addEventListener("abort", () => {
          entry.xhr = null;
          resolve();
        });

        xhr.open("POST", uploadUrl);
        xhr.withCredentials = true;
        xhr.setRequestHeader("X-CSRF-Token", csrfToken());
        xhr.setRequestHeader("X-Upload-Batch", stagingBatch);
        xhr.setRequestHeader("X-Upload-Filename", encodeURIComponent(file.name));
        xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
        xhr.send(file);
      });
    }

    function retryEntry(clientId) {
      const entry = files.get(clientId);
      if (!entry || entry.status !== "error" || entry.blocked) return;
      if (entry.cancelQueue) entry.cancelQueue();
      queueUpload(clientId);
    }

    async function removeEntry(clientId) {
      const entry = files.get(clientId);
      if (!entry) return;
      if (entry.cancelQueue) entry.cancelQueue();
      if (entry.status === "uploading" && entry.xhr) {
        entry.aborted = true;
        entry.xhr.abort();
      }
      if (entry.serverId && deleteUrlTemplate) {
        const deleteUrl = deleteUrlTemplate.replace("{id}", encodeURIComponent(entry.serverId));
        await fetch(deleteUrl, {
          method: "DELETE",
          credentials: "same-origin",
          headers: {
            "X-CSRF-Token": csrfToken(),
            "X-Upload-Batch": stagingBatch,
          },
        });
      }
      files.delete(clientId);
      const row = list.querySelector(`[data-client-id="${clientId}"]`);
      if (row) row.remove();
      updateEmptyState();
      notifyChange();
    }

    async function clearAllEntries() {
      if (!files.size) return;
      if (clearAllUrl) {
        await fetch(clearAllUrl, {
          method: "DELETE",
          credentials: "same-origin",
          headers: {
            "X-CSRF-Token": csrfToken(),
            "X-Upload-Batch": stagingBatch,
          },
        });
      } else {
        await Promise.all([...files.keys()].map((clientId) => removeEntry(clientId)));
        return;
      }
      for (const clientId of [...files.keys()]) {
        const row = list.querySelector(`[data-client-id="${clientId}"]`);
        if (row) row.remove();
      }
      files.clear();
      restoredFromServer = false;
      updateEmptyState();
      notifyChange();
    }

    function addRestoredEntry(item) {
      const blocked = isExtensionBlocked(item.name, blocklist);
      const entry = {
        file: null,
        name: item.name,
        size: item.size_bytes,
        status: blocked ? "error" : "done",
        progress: blocked ? 0 : 100,
        serverId: item.id,
        error: blocked ? blockedFileMessage(item.name) : null,
        blocked,
        xhr: null,
        aborted: false,
        cancelQueue: null,
      };
      files.set(item.id, entry);
      renderEntry(item.id, entry);
    }

    async function loadExistingStaged() {
      if (!restoreStaged) return;
      try {
        const resp = await fetch(uploadUrl, {
          credentials: "same-origin",
          headers: { "X-Upload-Batch": stagingBatch },
        });
        if (!resp.ok) return;
        const existing = await resp.json();
        if (!Array.isArray(existing) || existing.length === 0) return;
        restoredFromServer = true;
        for (const item of existing) {
          addRestoredEntry(item);
        }
        updateEmptyState();
        notifyChange();
      } catch (_) {
        // ignore restore failures; user can still upload fresh files
      }
    }

    if (clearAllBtn) {
      clearAllBtn.addEventListener("click", () => {
        clearAllEntries();
      });
    }

    list.addEventListener("click", (event) => {
      const retryBtn = event.target.closest("[data-fp-retry]");
      if (retryBtn) {
        const row = retryBtn.closest("[data-client-id]");
        if (row) retryEntry(row.dataset.clientId);
        return;
      }
      const removeBtn = event.target.closest("[data-fp-remove]");
      if (removeBtn) {
        const row = removeBtn.closest("[data-client-id]");
        if (row) removeEntry(row.dataset.clientId);
      }
    });

    function addFiles(fileList) {
      [...fileList].forEach((file) => uploadEntry(crypto.randomUUID(), file));
    }

    input.addEventListener("change", () => {
      if (input.files && input.files.length) addFiles(input.files);
      input.value = "";
    });

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

    if (form) {
      form.addEventListener("submit", (event) => {
        if (!requireFiles) return;
        const hasReady = [...files.values()].some((entry) => entry.status === "done");
        const hasBusy = [...files.values()].some(isBusy);
        const hasErrors = [...files.values()].some((entry) => entry.status === "error");
        if (hasBusy) {
          event.preventDefault();
          setError(t("Wait for uploads to finish"));
          errorEl?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return;
        }
        if (hasErrors) {
          event.preventDefault();
          setError(t("Remove blocked or failed files before submitting"));
          errorEl?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return;
        }
        if (!hasReady) {
          event.preventDefault();
          setError(requiredMessage);
          errorEl?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      });
    }

    updateEmptyState();
    updateClearAllState();
    updateRestoreHint();
    updateSubmitState();
    loadExistingStaged();
  }

  document.querySelectorAll("[data-file-picker]").forEach(initFilePicker);
})();
