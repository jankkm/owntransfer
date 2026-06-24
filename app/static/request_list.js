(function () {
  const COLLAPSED_MAX_PX = 288;

  function initUploadsSection(section) {
    const content = section.querySelector("[data-request-uploads-content]");
    const expandBtn = section.querySelector("[data-request-uploads-expand]");
    const fade = section.querySelector("[data-request-uploads-fade]");
    if (!content || !expandBtn) return;

    const expandLabel = expandBtn.dataset.expandLabel || "Show all uploads";
    const collapseLabel = expandBtn.dataset.collapseLabel || "Show less";
    let expanded = false;

    function refresh() {
      if (expanded) {
        content.style.maxHeight = content.scrollHeight + "px";
        if (fade) fade.classList.add("hidden");
        expandBtn.classList.remove("hidden");
        return;
      }

      content.style.maxHeight = COLLAPSED_MAX_PX + "px";
      const overflows = content.scrollHeight > COLLAPSED_MAX_PX + 4;
      expandBtn.classList.toggle("hidden", !overflows);
      if (fade) fade.classList.toggle("hidden", !overflows);
      expandBtn.textContent = expandLabel;
    }

    expandBtn.addEventListener("click", () => {
      expanded = !expanded;
      if (expanded) {
        content.style.maxHeight = content.scrollHeight + "px";
        expandBtn.textContent = collapseLabel;
        if (fade) fade.classList.add("hidden");
      } else {
        content.style.maxHeight = COLLAPSED_MAX_PX + "px";
        expandBtn.textContent = expandLabel;
        refresh();
      }
    });

    content.classList.add("overflow-hidden", "transition-[max-height]", "duration-300", "ease-in-out");
    content.style.maxHeight = COLLAPSED_MAX_PX + "px";
    refresh();
    window.addEventListener("resize", refresh);
  }

  document.querySelectorAll("[data-request-uploads]").forEach(initUploadsSection);
})();
