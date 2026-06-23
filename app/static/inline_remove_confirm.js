(function () {
  function t(key) {
    return window.__(key);
  }

  const backups = new WeakMap();
  let activeRow = null;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function confirmHtml(title, message) {
    return `
      <p class="font-medium mb-1 text-red-900">${escapeHtml(title)}</p>
      <p class="mb-3 text-red-800">${escapeHtml(message)}</p>
      <div class="flex gap-2">
        <button type="button" data-inline-confirm-yes
                class="rounded-lg px-3 py-1.5 text-white text-sm bg-red-600 hover:bg-red-700 disabled:opacity-60">
          ${t("Yes, remove file")}
        </button>
        <button type="button" data-inline-confirm-cancel
                class="rounded-lg px-3 py-1.5 border border-slate-300 bg-white text-sm hover:bg-slate-50 disabled:opacity-60">
          ${t("Cancel")}
        </button>
      </div>
    `;
  }

  function restoreRow(row) {
    const backup = backups.get(row);
    if (!backup) return;
    row.innerHTML = backup.html;
    row.className = backup.className;
    backups.delete(row);
    delete row.dataset.confirmActive;
    if (activeRow === row) activeRow = null;
  }

  function cancelActive() {
    if (activeRow) restoreRow(activeRow);
  }

  function setBusy(row, busy) {
    const yesBtn = row.querySelector("[data-inline-confirm-yes]");
    const cancelBtn = row.querySelector("[data-inline-confirm-cancel]");
    if (yesBtn) {
      yesBtn.disabled = busy;
      yesBtn.textContent = busy ? t("Removing…") : t("Yes, remove file");
    }
    if (cancelBtn) cancelBtn.disabled = busy;
  }

  function ask(row, { title, message }) {
    if (activeRow && activeRow !== row) {
      restoreRow(activeRow);
    }
    if (row.dataset.confirmActive === "true") {
      return Promise.resolve(false);
    }

    backups.set(row, { html: row.innerHTML, className: row.className });
    row.dataset.confirmActive = "true";
    activeRow = row;
    row.className = "rounded-lg border border-red-200 bg-red-50 p-4 text-sm";
    row.innerHTML = confirmHtml(title, message);
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });

    return new Promise((resolve) => {
      const yesBtn = row.querySelector("[data-inline-confirm-yes]");
      const cancelBtn = row.querySelector("[data-inline-confirm-cancel]");
      if (!yesBtn || !cancelBtn) {
        restoreRow(row);
        resolve(false);
        return;
      }
      yesBtn.addEventListener(
        "click",
        () => {
          setBusy(row, true);
          resolve(true);
        },
        { once: true }
      );
      cancelBtn.addEventListener(
        "click",
        () => {
          restoreRow(row);
          resolve(false);
        },
        { once: true }
      );
    });
  }

  function clear(row) {
    backups.delete(row);
    delete row.dataset.confirmActive;
    if (activeRow === row) activeRow = null;
  }

  window.InlineFileRemoveConfirm = { ask, restore: restoreRow, cancelActive, setBusy, clear };
})();
