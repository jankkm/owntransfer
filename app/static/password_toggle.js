(function () {
  const CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%&*";

  function t(key, vars) {
    return window.__(key, vars);
  }

  function generatePassword(length) {
    const bytes = new Uint8Array(length);
    crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => CHARSET[byte % CHARSET.length]).join("");
  }

  function setPasswordVisible(input, visible, btn) {
    input.type = visible ? "text" : "password";
    btn.setAttribute("aria-pressed", visible ? "true" : "false");
    btn.setAttribute("aria-label", visible ? t("Hide password") : t("Show password"));
    const showIcon = btn.querySelector("[data-password-eye-show]");
    const hideIcon = btn.querySelector("[data-password-eye-hide]");
    if (showIcon) showIcon.classList.toggle("hidden", visible);
    if (hideIcon) hideIcon.classList.toggle("hidden", !visible);
  }

  document.querySelectorAll("[data-password-toggle]").forEach((root) => {
    const checkbox = root.querySelector('input[name="use_password"]');
    const field = root.querySelector("[data-password-field]");
    if (!checkbox || !field) return;

    const passwordInput = field.querySelector('input[name="password"]');
    const generateBtn = root.querySelector("[data-password-generate-btn]");
    const visibilityBtn = root.querySelector("[data-password-visibility-btn]");
    const hasPassword = root.dataset.hasPassword === "true";
    const passwordLength = parseInt(root.dataset.passwordLength, 10) || 16;
    const requiredLength = Math.max(8, passwordLength);
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
        if (visibilityBtn) {
          setPasswordVisible(passwordInput, false, visibilityBtn);
        }
      }
    }

    checkbox.addEventListener("change", sync);

    if (generateBtn && passwordInput) {
      generateBtn.addEventListener("click", () => {
        if (!checkbox.checked) {
          checkbox.checked = true;
          sync();
        }
        passwordInput.value = generatePassword(passwordLength);
        passwordInput.setCustomValidity("");
        passwordInput.dispatchEvent(new Event("input", { bubbles: true }));
      });
    }

    if (visibilityBtn && passwordInput) {
      visibilityBtn.addEventListener("click", () => {
        const visible = passwordInput.type === "password";
        setPasswordVisible(passwordInput, visible, visibilityBtn);
      });
    }

    if (passwordInput) {
      passwordInput.addEventListener("input", () => {
        passwordInput.setCustomValidity("");
      });
    }

    if (form && passwordInput) {
      form.addEventListener("submit", (event) => {
        if (!checkbox.checked) {
          passwordInput.setCustomValidity("");
          return;
        }
        const value = passwordInput.value.trim();
        if (!hasPassword && !value) {
          event.preventDefault();
          passwordInput.setCustomValidity(t("Enter a password to enable protection"));
          passwordInput.reportValidity();
          return;
        }
        if (value && value.length < requiredLength) {
          event.preventDefault();
          passwordInput.setCustomValidity(
            t("Password must be at least %(n)s characters", { n: String(requiredLength) })
          );
          passwordInput.reportValidity();
          return;
        }
        passwordInput.setCustomValidity("");
      });
    }

    sync();
  });
})();
