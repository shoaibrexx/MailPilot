(() => {
  const layout = document.getElementById("app-layout");
  const menuToggle = document.getElementById("mobile-menu-toggle");
  const backdrop = document.getElementById("sidebar-backdrop");
  if (!layout || !menuToggle) return;
  const sidebar = layout.querySelector(".sidebar");
  const mainColumn = layout.querySelector(".main-column");
  const mobileViewport = window.matchMedia("(max-width: 850px)");

  const setMenuOpen = (isOpen) => {
    const wasOpen = layout.classList.contains("sidebar-open");
    layout.classList.toggle("sidebar-open", isOpen);
    document.body.classList.toggle("navigation-open", isOpen);
    if (mainColumn && "inert" in mainColumn) mainColumn.inert = isOpen;
    menuToggle.setAttribute("aria-expanded", String(isOpen));
    menuToggle.setAttribute("aria-label", isOpen ? "Close navigation" : "Open navigation");
    if (isOpen) {
      layout.querySelector(".navigation-link[href]")?.focus();
    } else if (wasOpen && menuToggle.getClientRects().length) {
      menuToggle.focus();
    }
  };

  menuToggle.addEventListener("click", () => {
    setMenuOpen(!layout.classList.contains("sidebar-open"));
  });
  backdrop?.addEventListener("click", () => setMenuOpen(false));
  layout.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (layout.classList.contains("sidebar-open")) {
        event.preventDefault();
        setMenuOpen(false);
      }
      return;
    }
    if (event.key !== "Tab" || !layout.classList.contains("sidebar-open") || !mobileViewport.matches || !sidebar) return;

    const focusable = [...sidebar.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')];
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && (document.activeElement === first || !sidebar.contains(document.activeElement))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (document.activeElement === last || !sidebar.contains(document.activeElement))) {
      event.preventDefault();
      first.focus();
    }
  });

  const closeOnDesktop = () => {
    if (!mobileViewport.matches && layout.classList.contains("sidebar-open")) setMenuOpen(false);
  };
  if (typeof mobileViewport.addEventListener === "function") {
    mobileViewport.addEventListener("change", closeOnDesktop);
  } else {
    mobileViewport.addListener(closeOnDesktop);
  }
})();
