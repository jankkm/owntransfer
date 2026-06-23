(function () {
  function t(key, vars) {
    return window.__(key, vars);
  }

  function inlineConfirm() {
    return window.InlineFileRemoveConfirm;
  }

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(2) + " MB";
  }

  function initRequestFiles(root) {
    const deleteUrlTemplate = root.dataset.deleteUrlTemplate;
    const titleEl = root.querySelector("[data-files-title]");
    const editBtn = root.querySelector("[data-files-edit-btn]");
    const doneBtn = root.querySelector("[data-files-done-btn]");
    const list = root.querySelector("[data-files-list]");
    const emptyState = root.querySelector("[data-files-empty]");
    const errorEl = root.querySelector("[data-files-error]");

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

    function uploadCount() {
      return list.querySelectorAll("[data-upload-id]").length;
    }

    function fileCount() {
      return list.querySelectorAll("[data-file-id]").length;
    }

    function updateTitle() {
      if (!titleEl) return;
      const uploads = uploadCount();
      const files = fileCount();
      titleEl.textContent = `Received uploads (${uploads}) · ${files} file${files === 1 ? "" : "s"}`;
      if (emptyState) {
        emptyState.classList.toggle("hidden", uploads > 0);
      }
      const downloadAll = root.querySelector("[data-files-download-all]");
      const editing = root.classList.contains("is-editing");
      if (downloadAll) downloadAll.classList.toggle("hidden", files === 0 || editing);
      if (editBtn && !editing) editBtn.classList.toggle("hidden", files === 0);
    }

    function setEditMode(editing) {
      if (!editing) inlineConfirm()?.cancelActive();
      root.classList.toggle("is-editing", editing);
      editBtn.classList.toggle("hidden", editing);
      doneBtn.classList.toggle("hidden", !editing);
      const downloadAll = root.querySelector("[data-files-download-all]");
      if (downloadAll) downloadAll.classList.toggle("hidden", editing);
      list.querySelectorAll("[data-file-remove]").forEach((btn) => {
        btn.classList.toggle("hidden", !editing);
      });
      list.querySelectorAll("[data-file-download]").forEach((link) => {
        link.classList.toggle("hidden", editing);
      });
      if (!editing) setError("");
    }

    function fileNameForRow(row) {
      const nameEl = row.querySelector("[data-file-name]");
      return nameEl ? nameEl.textContent.trim() : t("this file");
    }

    async function removeFile(row) {
      const fileId = row.dataset.fileId || row.getAttribute("data-file-id");
      if (!fileId) return;

      const fileName = fileNameForRow(row);
      const ic = inlineConfirm();
      if (!ic) {
        setError(t("Could not show confirmation dialog."));
        return;
      }

      const confirmed = await ic.ask(row, {
        title: t("Remove file?"),
        message: t('"%(name)s" will be permanently removed. This cannot be undone.', { name: fileName }),
      });
      if (!confirmed) return;

      const deleteUrl = deleteUrlTemplate.replace("{id}", encodeURIComponent(fileId));
      setError("");

      try {
        const response = await fetch(deleteUrl, { method: "DELETE", credentials: "same-origin" });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.detail || t("Could not remove file"));
        }

        const uploadGroup = row.closest("[data-upload-id]");
        ic.clear(row);
        row.remove();
        if (uploadGroup && !uploadGroup.querySelector("[data-file-id]")) {
          uploadGroup.remove();
        }
        if (fileCount() === 0) {
          setEditMode(false);
        }
        updateTitle();
      } catch (err) {
        ic.restore(row);
        setError(err.message || t("Could not remove file"));
      }
    }

    editBtn.addEventListener("click", () => setEditMode(true));
    doneBtn.addEventListener("click", () => setEditMode(false));

    list.addEventListener("click", (event) => {
      const removeBtn = event.target.closest("[data-file-remove]");
      if (!removeBtn || !list.contains(removeBtn)) return;
      event.preventDefault();
      const row = removeBtn.closest("[data-file-id]");
      if (row) removeFile(row);
    });

    list.querySelectorAll("[data-file-id]").forEach((row) => {
      const size = Number(row.dataset.fileSize || 0);
      const meta = row.querySelector("[data-file-meta]");
      if (meta && size) meta.textContent = formatSize(size);
    });

    updateTitle();
  }

  document.querySelectorAll("[data-request-files]").forEach(initRequestFiles);
})();
