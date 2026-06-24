(function () {
  function t(key) {
    return window.__(key);
  }

  function formSnapshot(form) {
    const data = new FormData(form);
    const values = {};
    for (const [key, value] of data.entries()) {
      if (key === "csrf_token") continue;
      if (values[key] === undefined) {
        values[key] = value;
        continue;
      }
      if (!Array.isArray(values[key])) {
        values[key] = [values[key]];
      }
      values[key].push(value);
    }
    return JSON.stringify(values);
  }

  function hasStagedFiles(form) {
    const picker = form.querySelector("[data-file-picker]");
    if (!picker) return false;
    const list = picker.querySelector("[data-file-list]");
    return Boolean(list && list.children.length > 0);
  }

  function initUnsavedGuard(form) {
    const initial = formSnapshot(form);
    let allowLeave = false;
    // After a failed save the browser may restore field values, so the snapshot
    // matches "initial" even though nothing was persisted.
    const forceDirty = Boolean(document.querySelector("[data-page-error]"));

    function isDirty() {
      if (forceDirty) return true;
      if (hasStagedFiles(form)) return true;
      return formSnapshot(form) !== initial;
    }

    function confirmLeave() {
      return window.confirm(t("You have unsaved changes. Leave this page?"));
    }

    form.addEventListener("submit", (event) => {
      // Run after other submit handlers (e.g. file-picker validation) so we
      // do not treat a cancelled submit as an intentional leave.
      queueMicrotask(() => {
        if (!event.defaultPrevented) {
          allowLeave = true;
        }
      });
    });

    window.addEventListener("beforeunload", (event) => {
      if (allowLeave || !isDirty()) return;
      event.preventDefault();
      event.returnValue = "";
    });

    document.addEventListener(
      "click",
      (event) => {
        if (allowLeave || !isDirty()) return;

        const link = event.target.closest("a[href]");
        if (!link) return;
        if (link.target === "_blank" || link.hasAttribute("download")) return;

        const href = link.getAttribute("href");
        if (!href || href.startsWith("#") || href.startsWith("javascript:")) return;

        if (!confirmLeave()) {
          event.preventDefault();
          event.stopPropagation();
        } else {
          allowLeave = true;
        }
      },
      true,
    );
  }

  document.querySelectorAll("form[data-unsaved-guard]").forEach(initUnsavedGuard);
})();
