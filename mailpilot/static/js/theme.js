(() => {
  const root = document.documentElement;
  const toggle = document.getElementById("theme-toggle");
  const themeColorMeta = document.querySelector('meta[name="theme-color"]');
  const preferenceKey = "mailpilot-theme";
  const validThemes = new Set(["light", "dark", "system"]);
  const colorSchemeQuery = window.matchMedia("(prefers-color-scheme: dark)");

  let preference = validThemes.has(root.dataset.themePreference)
    ? root.dataset.themePreference
    : "dark";
  try {
    const savedPreference = window.localStorage.getItem(preferenceKey);
    if (validThemes.has(savedPreference)) preference = savedPreference;
  } catch (_error) {
    // Keep the theme usable when browser storage is disabled.
  }

  const applyPreference = () => {
    const darkMode = preference === "dark" ||
      (preference === "system" && colorSchemeQuery.matches);
    root.dataset.theme = darkMode ? "dark" : "light";
    root.dataset.themePreference = preference;
    root.style.colorScheme = darkMode ? "dark" : "light";
    themeColorMeta?.setAttribute("content", darkMode ? "#050B18" : "#F0F7FC");
    if (toggle) {
      const nextTheme = darkMode ? "light" : "dark";
      toggle.setAttribute("aria-label", `Switch to ${nextTheme} theme`);
      toggle.setAttribute("title", `Switch to ${nextTheme} theme`);
      toggle.setAttribute("aria-pressed", String(darkMode));
    }
    root.dispatchEvent(new CustomEvent("mailpilot:themechange", {
      detail: { preference, darkMode },
    }));
  };

  if (toggle) {
    toggle.addEventListener("click", () => {
      const darkMode = preference === "dark" ||
        (preference === "system" && colorSchemeQuery.matches);
      preference = darkMode ? "light" : "dark";
      try {
        window.localStorage.setItem(preferenceKey, preference);
      } catch (_error) {
        // The selected theme stays active for this page session.
      }
      const saveUrl = toggle.dataset.themeSaveUrl;
      const csrfToken = toggle.dataset.csrfToken;
      if (saveUrl && csrfToken && window.fetch) {
        window.fetch(saveUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": csrfToken,
          },
          body: JSON.stringify({ theme: preference }),
          credentials: "same-origin",
          keepalive: true,
        }).catch(() => {
          // The browser preference remains active if the server cannot be reached.
        });
      }
      applyPreference();
    });
  }

  colorSchemeQuery.addEventListener?.("change", () => {
    if (preference === "system") applyPreference();
  });

  applyPreference();
})();
