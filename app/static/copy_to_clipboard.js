(function () {
  function t(key) {
    return (window.__i18n && window.__i18n[key]) || key;
  }

  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const el = document.createElement("textarea");
    el.value = text;
    el.setAttribute("readonly", "");
    el.style.position = "absolute";
    el.style.left = "-9999px";
    document.body.appendChild(el);
    el.select();
    document.execCommand("copy");
    document.body.removeChild(el);
  }

  function showCopied(btn) {
    const copyIcon = btn.querySelector("[data-copy-icon]");
    const copiedIcon = btn.querySelector("[data-copied-icon]");
    const copiedLabel = btn.dataset.copiedLabel || t("Copied!");
    const originalTitle = btn.getAttribute("title") || "";

    if (copyIcon) copyIcon.classList.add("hidden");
    if (copiedIcon) copiedIcon.classList.remove("hidden");
    btn.setAttribute("title", copiedLabel);
    btn.setAttribute("aria-label", copiedLabel);

    window.setTimeout(() => {
      if (copyIcon) copyIcon.classList.remove("hidden");
      if (copiedIcon) copiedIcon.classList.add("hidden");
      btn.setAttribute("title", originalTitle);
      btn.setAttribute("aria-label", originalTitle);
    }, 1500);
  }

  document.querySelectorAll("[data-copy-url-btn]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const root = btn.closest("[data-copy-url-field]");
      const input = root?.querySelector("[data-copy-url-input]");
      if (!input) return;

      try {
        await copyText(input.value);
        showCopied(btn);
      } catch (_) {
        input.focus();
        input.select();
      }
    });
  });
})();
