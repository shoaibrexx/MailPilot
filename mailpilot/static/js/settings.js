(() => {
  "use strict";

  const themeSelect = document.querySelector("[data-theme-setting]");
  const settingsForm = document.getElementById("settings-form");
  const resetForm = document.getElementById("workspace-reset-form");
  const validThemes = new Set(["light", "dark", "system"]);

  if (themeSelect) {
    const root = document.documentElement;
    const savedPreference = root.dataset.themePreference;
    if (validThemes.has(savedPreference)) themeSelect.value = savedPreference;
    try {
      const browserTheme = window.localStorage.getItem("mailpilot-theme");
      if (validThemes.has(browserTheme)) themeSelect.value = browserTheme;
    } catch (_error) {
      // The database preference remains selected when browser storage is disabled.
    }
  }

  settingsForm?.addEventListener("submit", () => {
    if (!themeSelect || !validThemes.has(themeSelect.value)) return;
    try {
      window.localStorage.setItem("mailpilot-theme", themeSelect.value);
    } catch (_error) {
      // The submitted preference is still saved in SQLite.
    }
  });

  resetForm?.addEventListener("submit", (event) => {
    const confirmation = resetForm.dataset.resetConfirmation || "DELETE ALL DATA";
    if (!window.confirm("Delete all recipients, groups, drafts, and campaigns? This cannot be undone.")) {
      event.preventDefault();
      return;
    }
    const input = resetForm.querySelector('[name="confirmation"]');
    if (!input || input.value.trim() !== confirmation) {
      event.preventDefault();
      input?.focus();
      input?.setCustomValidity(`Type ${confirmation} exactly to confirm.`);
      input?.reportValidity();
      return;
    }
    input.setCustomValidity("");
  });

  resetForm?.querySelector('[name="confirmation"]')?.addEventListener("input", (event) => {
    event.currentTarget.setCustomValidity("");
  });
})();
