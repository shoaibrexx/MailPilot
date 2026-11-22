(() => {
  "use strict";

  const widget = document.querySelector("[data-notification-widget]");
  document.querySelectorAll("form[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm || "Continue with this action?")) event.preventDefault();
    });
  });
  if (!widget) return;

  const toggle = widget.querySelector("[data-notification-toggle], .notification-toggle");
  const dropdown = widget.querySelector(".notification-dropdown");
  const list = widget.querySelector("[data-notification-list]");
  const badge = widget.querySelector("[data-notification-badge]");
  const unreadLabel = widget.querySelector("[data-notification-unread-label]");
  const csrfToken = widget.dataset.csrfToken;
  let loaded = false;

  const setDropdownOpen = (isOpen) => {
    dropdown.hidden = !isOpen;
    toggle.setAttribute("aria-expanded", String(isOpen));
  };

  const updateCount = (count) => {
    const value = Math.max(0, Number(count) || 0);
    badge.textContent = value > 99 ? "99+" : String(value);
    badge.hidden = value === 0;
    toggle.setAttribute("aria-label", value ? `Notifications, ${value} unread` : "Notifications");
    unreadLabel.textContent = `${value} unread`;
    document.querySelectorAll(".navigation-count").forEach((node) => {
      node.textContent = String(value);
      node.hidden = value === 0;
    });
  };

  const actionUrl = (id, action) => `${widget.dataset.actionBaseUrl.replace(/\/$/, "")}/${id}/${action}`;

  const makeItem = (item) => {
    const article = document.createElement("article");
    article.className = `notification-dropdown-item${item.is_read ? "" : " is-unread"}`;
    const copy = document.createElement("div");
    copy.className = "notification-dropdown-copy";
    const title = document.createElement("strong");
    title.textContent = item.title;
    const message = document.createElement("p");
    message.textContent = item.message;
    const meta = document.createElement("small");
    const date = new Date(item.created_at);
    meta.textContent = `${item.category_label} · ${Number.isNaN(date.valueOf()) ? "Just now" : date.toLocaleString()}`;
    copy.append(title, message, meta);

    const actions = document.createElement("div");
    actions.className = "notification-dropdown-actions";
    const readButton = document.createElement("button");
    readButton.type = "button";
    readButton.textContent = item.is_read ? "Mark unread" : "Mark read";
    readButton.addEventListener("click", () => mutate(actionUrl(item.id, item.is_read ? "unread" : "read")));
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.textContent = "Dismiss";
    deleteButton.addEventListener("click", () => mutate(actionUrl(item.id, "delete")));
    actions.append(readButton, deleteButton);
    article.append(copy, actions);
    return article;
  };

  const render = (items) => {
    list.replaceChildren();
    if (!items.length) {
      const empty = document.createElement("p");
      empty.className = "notification-dropdown-empty";
      empty.textContent = "You're all caught up.";
      list.append(empty);
      return;
    }
    items.forEach((item) => list.append(makeItem(item)));
  };

  const loadRecent = async () => {
    list.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(widget.dataset.recentUrl, { headers: { Accept: "application/json" }, credentials: "same-origin" });
      if (!response.ok) throw new Error("Could not load notifications");
      const result = await response.json();
      render(result.notifications || []);
      updateCount(result.unread_count);
      loaded = true;
    } catch (_error) {
      list.textContent = "Notifications could not be loaded. Try opening the history page.";
    } finally {
      list.removeAttribute("aria-busy");
    }
  };

  const mutate = async (url) => {
    try {
      const response = await fetch(url, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Could not update notification");
      loaded = false;
      await loadRecent();
    } catch (_error) {
      list.textContent = "That notification could not be updated. Refresh and try again.";
    }
  };

  toggle.addEventListener("click", async () => {
    const opening = dropdown.hidden;
    setDropdownOpen(opening);
    if (opening && !loaded) await loadRecent();
  });

  widget.querySelector("[data-notification-read-all]")?.addEventListener("click", async () => {
    try {
      const response = await fetch(widget.dataset.readAllUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": csrfToken, Accept: "application/json" },
      });
      if (!response.ok) throw new Error("Could not update notifications");
      const result = await response.json();
      updateCount(result.unread_count);
      loaded = false;
      await loadRecent();
    } catch (_error) {
      list.textContent = "Notifications could not be updated. Refresh and try again.";
    }
  });

  document.addEventListener("click", (event) => {
    if (!widget.contains(event.target)) {
      setDropdownOpen(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dropdown.hidden) {
      setDropdownOpen(false);
      toggle.focus();
    }
  });
})();
