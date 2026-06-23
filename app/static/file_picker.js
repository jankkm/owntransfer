(function () {
  function t(key) {
    return window.__(key);
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function initFilePicker(root) {
    const uploadUrl = root.dataset.uploadUrl;
    const deleteUrlTemplate = root.dataset.deleteUrlTemplate;
    const input = root.querySelector("[data-file-input]");
    const list = root.querySelector("[data-file-list]");
    const dropzone = root.querySelector("[data-dropzone]");
    const emptyState = root.querySelector("[data-empty-state]");
    const errorEl = root.querySelector("[data-file-error]");
    const form = root.closest("form");
    const submitBtn = form ? form.querySelector("[data-submit-btn]") : null;
    const requireFiles = root.dataset.requireFiles === "true";
    const requiredMessage = root.dataset.requiredMessage || t("Add at least one file");
    const files = new Map();

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

    function updateSubmitState() {
      if (!submitBtn || !requireFiles) return;
      const hasUploading = [...files.values()].some((entry) => entry.status === "uploading");
      submitBtn.disabled = hasUploading;
    }

    function notifyChange() {
      const hasReady = [...files.values()].some((entry) => entry.status === "done");
      if (hasReady) {
        setError("");
      }
      root.dispatchEvent(new CustomEvent("filepicker:change", { bubbles: true }));
      updateSubmitState();
    }

    function renderEntry(clientId, entry) {
      let row = list.querySelector(`[data-client-id="${clientId}"]`);
      if (!row) {
        row = document.createElement("li");
        row.dataset.clientId = clientId;
        row.className = "border border-slate-200 rounded-lg p-3 bg-white";
        list.appendChild(row);
      }

      let statusText = "";
      if (entry.status === "uploading") statusText = t("Uploading…");
      if (entry.status === "done") statusText = t("Ready");
      if (entry.status === "error") statusText = entry.error || t("Upload failed");

      row.innerHTML = `
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <p class="text-sm font-medium truncate">${entry.name}</p>
            <p class="text-xs text-slate-500">${formatSize(entry.size)} · ${statusText}</p>
            <div class="mt-2 h-1.5 bg-slate-100 rounded-full overflow-hidden">
              <div class="h-full bg-[var(--primary)] transition-all duration-150" style="width:${entry.progress}%"></div>
            </div>
            ${entry.status === "error" ? `<p class="text-xs text-red-600 mt-1">${entry.error}</p>` : ""}
          </div>
          <button type="button" data-remove="${clientId}"
                  class="text-xs text-slate-500 hover:text-red-600 shrink-0"
                  ${entry.status === "uploading" ? "disabled" : ""}>${t("Remove")}</button>
        </div>
      `;

      row.querySelector("[data-remove]").addEventListener("click", () => removeEntry(clientId));
    }

    function uploadEntry(clientId, file) {
      const entry = {
        name: file.name,
        size: file.size,
        status: "uploading",
        progress: 0,
        serverId: null,
        error: null,
      };
      files.set(clientId, entry);
      renderEntry(clientId, entry);
      updateEmptyState();
      notifyChange();

      const xhr = new XMLHttpRequest();
      const formData = new FormData();
      formData.append("file", file, file.name);
      entry.xhr = xhr;

      xhr.upload.addEventListener("progress", (event) => {
        if (!event.lengthComputable) return;
        entry.progress = Math.round((event.loaded / event.total) * 100);
        renderEntry(clientId, entry);
      });

      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          const payload = JSON.parse(xhr.responseText);
          entry.status = "done";
          entry.progress = 100;
          entry.serverId = payload.id;
          renderEntry(clientId, entry);
          notifyChange();
          return;
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
        entry.status = "error";
        entry.error = message;
        renderEntry(clientId, entry);
        notifyChange();
      });

      xhr.addEventListener("error", () => {
        entry.status = "error";
        entry.error = t("Network error");
        renderEntry(clientId, entry);
        notifyChange();
      });

      xhr.open("POST", uploadUrl);
      xhr.withCredentials = true;
      xhr.send(formData);
    }

    async function removeEntry(clientId) {
      const entry = files.get(clientId);
      if (!entry) return;
      if (entry.status === "uploading" && entry.xhr) {
        entry.xhr.abort();
      }
      if (entry.serverId && deleteUrlTemplate) {
        const deleteUrl = deleteUrlTemplate.replace("{id}", encodeURIComponent(entry.serverId));
        await fetch(deleteUrl, { method: "DELETE", credentials: "same-origin" });
      }
      files.delete(clientId);
      const row = list.querySelector(`[data-client-id="${clientId}"]`);
      if (row) row.remove();
      updateEmptyState();
      notifyChange();
    }

    function addFiles(fileList) {
      [...fileList].forEach((file) => {
        const clientId = crypto.randomUUID();
        uploadEntry(clientId, file);
      });
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
        const hasUploading = [...files.values()].some((entry) => entry.status === "uploading");
        if (hasUploading) {
          event.preventDefault();
          setError(t("Wait for uploads to finish"));
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
    updateSubmitState();
  }

  document.querySelectorAll("[data-file-picker]").forEach(initFilePicker);
})();
