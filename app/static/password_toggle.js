(function () {
  function t(key) {
    return window.__(key);
  }

  document.querySelectorAll("[data-password-toggle]").forEach((root) => {
    const checkbox = root.querySelector('input[name="use_password"]');
    const field = root.querySelector("[data-password-field]");
    if (!checkbox || !field) return;

    const passwordInput = field.querySelector('input[name="password"]');
    const hasPassword = root.dataset.hasPassword === "true";
    const form = root.closest("form");

    function sync() {
      const enabled = checkbox.checked;
      field.classList.toggle("hidden", !enabled);
      if (passwordInput) {
        passwordInput.required = enabled && !hasPassword;
        passwordInput.setCustomValidity("");
      }
      if (!enabled && passwordInput) {
        passwordInput.value = "";
      }
    }

    checkbox.addEventListener("change", sync);

    if (form && passwordInput) {
      form.addEventListener("submit", (event) => {
        if (!checkbox.checked) {
          passwordInput.setCustomValidity("");
          return;
        }
        if (!hasPassword && !passwordInput.value.trim()) {
          event.preventDefault();
          passwordInput.setCustomValidity(t("Enter a password to enable protection"));
          passwordInput.reportValidity();
          return;
        }
        passwordInput.setCustomValidity("");
      });
    }

    sync();
  });
})();
