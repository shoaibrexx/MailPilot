(() => {
  "use strict";

  const root = document.documentElement;
  const themeKey = "mailpilot-public-demo-theme";
  const themeButton = document.getElementById("theme-toggle");
  const themeSelect = document.getElementById("settings-theme");
  const views = Array.from(document.querySelectorAll(".view"));
  const navLinks = Array.from(document.querySelectorAll("[data-view-link]"));
  const title = document.getElementById("current-page");
  const sidebar = document.getElementById("sidebar");
  const scrim = document.getElementById("mobile-scrim");
  const popover = document.getElementById("notification-popover");
  const toastRegion = document.getElementById("toast-region");
  let unreadTotal = 3;

  const applyTheme = (theme, persist) => {
    const next = theme === "light" ? "light" : "dark";
    root.dataset.theme = next;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", next === "dark" ? "#050B18" : "#F0F7FC");
    if (themeSelect) themeSelect.value = next;
    document.querySelectorAll("[data-theme-choice]").forEach((button) => {
      const active = button.dataset.themeChoice === next;
      button.classList.toggle("is-selected", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (themeButton) {
      const target = next === "dark" ? "light" : "dark";
      themeButton.setAttribute("aria-label", "Switch to " + target + " theme");
      themeButton.setAttribute("title", "Switch to " + target + " theme");
      themeButton.setAttribute("aria-pressed", String(next === "dark"));
    }
    if (persist) {
      try { window.localStorage.setItem(themeKey, next); } catch (_error) { /* Theme remains active for this visit. */ }
    }
  };

  let initialTheme = "dark";
  try {
    const savedTheme = window.localStorage.getItem(themeKey);
    if (savedTheme === "light" || savedTheme === "dark") initialTheme = savedTheme;
  } catch (_error) { /* The dark demo default remains available without storage. */ }
  applyTheme(initialTheme, false);

  const showToast = (message, success) => {
    if (!toastRegion) return;
    const toast = document.createElement("div");
    toast.className = "toast" + (success ? " toast-success" : "");
    toast.setAttribute("role", "status");
    toast.textContent = message;
    toastRegion.append(toast);
    window.setTimeout(() => toast.remove(), 3600);
  };

  const closeMobileNav = () => {
    sidebar?.classList.remove("is-open");
    if (scrim) scrim.hidden = true;
    document.getElementById("mobile-menu")?.setAttribute("aria-expanded", "false");
  };

  const showView = (name, updateHash) => {
    const id = views.some((view) => view.id === name) ? name : "dashboard";
    views.forEach((view) => {
      const active = view.id === id;
      view.hidden = !active;
      view.classList.toggle("is-visible", active);
    });
    navLinks.forEach((link) => {
      const active = link.dataset.viewLink === id;
      link.classList.toggle("is-active", active);
      if (active) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    });
    const activeView = document.getElementById(id);
    if (title && activeView) title.textContent = activeView.dataset.title || "MailPilot";
    if (updateHash && window.location.hash !== "#" + id) history.pushState(null, "", "#" + id);
    closeMobileNav();
    if (popover) popover.hidden = true;
    document.getElementById("notification-toggle")?.setAttribute("aria-expanded", "false");
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  navLinks.forEach((link) => link.addEventListener("click", (event) => {
    event.preventDefault();
    showView(link.dataset.viewLink, true);
  }));
  document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => showView(button.dataset.go, true)));
  window.addEventListener("popstate", () => showView(window.location.hash.slice(1), false));
  showView(window.location.hash.slice(1), false);

  themeButton?.addEventListener("click", () => {
    applyTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
    showToast("Theme updated on this device.", true);
  });
  themeSelect?.addEventListener("change", () => applyTheme(themeSelect.value, true));
  document.querySelectorAll("[data-theme-choice]").forEach((button) => button.addEventListener("click", () => applyTheme(button.dataset.themeChoice, true)));

  document.getElementById("mobile-menu")?.addEventListener("click", (event) => {
    const open = !sidebar?.classList.contains("is-open");
    sidebar?.classList.toggle("is-open", open);
    if (scrim) scrim.hidden = !open;
    event.currentTarget.setAttribute("aria-expanded", String(open));
  });
  scrim?.addEventListener("click", closeMobileNav);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeMobileNav();
      if (popover) popover.hidden = true;
      document.getElementById("notification-toggle")?.setAttribute("aria-expanded", "false");
    }
  });

  const notificationToggle = document.getElementById("notification-toggle");
  notificationToggle?.addEventListener("click", () => {
    const open = popover?.hidden ?? true;
    if (popover) popover.hidden = !open;
    notificationToggle.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (event) => {
    if (popover && !popover.hidden && !event.target.closest(".notification-wrap")) {
      popover.hidden = true;
      notificationToggle?.setAttribute("aria-expanded", "false");
    }
  });
  const setUnread = (count) => {
    unreadTotal = Math.max(0, count);
    document.querySelectorAll("[data-unread-count]").forEach((node) => {
      node.textContent = String(unreadTotal);
      node.hidden = unreadTotal === 0;
    });
    document.querySelectorAll("[data-unread-text]").forEach((node) => { node.textContent = String(unreadTotal); });
    const dot = document.querySelector(".notification-dot");
    if (dot) dot.hidden = unreadTotal === 0;
  };
  const markAll = () => {
    document.querySelectorAll("[data-notification-row]").forEach((row) => {
      row.classList.remove("is-unread");
      row.querySelector("[data-mark-one]")?.setAttribute("aria-label", "Mark notification as unread");
      const action = row.querySelector("[data-mark-one]");
      if (action) action.textContent = "Mark unread";
      row.querySelector(".unread-label")?.remove();
    });
    document.querySelectorAll(".popover-item.is-unread").forEach((item) => item.classList.remove("is-unread"));
    setUnread(0);
    filterNotifications();
    showToast("All notifications marked as read.", true);
  };
  document.querySelectorAll("[data-mark-all]").forEach((button) => button.addEventListener("click", markAll));
  document.querySelectorAll("[data-open-notifications]").forEach((button) => button.addEventListener("click", () => showView("notifications", true)));

  const recipientSearch = document.getElementById("recipient-search");
  const recipientStatus = document.getElementById("recipient-status");
  const recipientRows = Array.from(document.querySelectorAll("[data-recipient-row]"));
  const filterRecipients = () => {
    const term = (recipientSearch?.value || "").trim().toLowerCase();
    const status = recipientStatus?.value || "all";
    let visible = 0;
    recipientRows.forEach((row) => {
      const matches = row.textContent.toLowerCase().includes(term) && (status === "all" || row.dataset.status === status);
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    document.getElementById("recipient-visible-count").textContent = String(visible);
    document.getElementById("recipient-empty").hidden = visible !== 0;
  };
  recipientSearch?.addEventListener("input", filterRecipients);
  recipientStatus?.addEventListener("change", filterRecipients);

  const campaignRows = Array.from(document.querySelectorAll("[data-campaign-row]"));
  let campaignStatus = "all";
  const filterCampaigns = () => {
    const term = (document.getElementById("campaign-search")?.value || "").trim().toLowerCase();
    let visible = 0;
    campaignRows.forEach((row) => {
      const matches = (campaignStatus === "all" || row.dataset.status === campaignStatus) && row.dataset.search.includes(term);
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    document.getElementById("campaign-visible-count").textContent = String(visible);
    document.getElementById("campaign-empty").hidden = visible !== 0;
  };
  document.querySelectorAll("[data-campaign-filter]").forEach((button) => button.addEventListener("click", () => {
    campaignStatus = button.dataset.campaignFilter;
    document.querySelectorAll("[data-campaign-filter]").forEach((tab) => tab.classList.toggle("is-selected", tab === button));
    filterCampaigns();
  }));
  document.getElementById("campaign-search")?.addEventListener("input", filterCampaigns);

  let notificationFilter = "all";
  const filterNotifications = () => {
    const type = document.getElementById("notification-type")?.value || "all";
    let visible = 0;
    document.querySelectorAll("[data-notification-row]").forEach((row) => {
      const match = (notificationFilter === "all" || (notificationFilter === "unread" && row.classList.contains("is-unread"))) && (type === "all" || row.dataset.kind === type);
      row.hidden = !match;
      if (match) visible += 1;
    });
    document.getElementById("notification-visible-count").textContent = String(visible);
    document.getElementById("notification-empty").hidden = visible !== 0;
  };
  document.querySelectorAll("[data-notification-filter]").forEach((button) => button.addEventListener("click", () => {
    notificationFilter = button.dataset.notificationFilter;
    document.querySelectorAll("[data-notification-filter]").forEach((tab) => tab.classList.toggle("is-selected", tab === button));
    filterNotifications();
  }));
  document.getElementById("notification-type")?.addEventListener("change", filterNotifications);
  document.querySelectorAll("[data-mark-one]").forEach((button) => button.addEventListener("click", () => {
    const row = button.closest("[data-notification-row]");
    if (!row) return;
    const wasUnread = row.classList.contains("is-unread");
    row.classList.toggle("is-unread", !wasUnread);
    if (!wasUnread && !row.querySelector(".unread-label")) {
      const titleLine = row.querySelector(".notification-title-line");
      const badge = document.createElement("span");
      badge.className = "unread-label";
      badge.textContent = "NEW";
      titleLine?.append(badge);
    } else if (wasUnread) row.querySelector(".unread-label")?.remove();
    button.textContent = wasUnread ? "Mark unread" : "Mark read";
    button.setAttribute("aria-label", wasUnread ? "Mark notification as unread" : "Mark notification as read");
    const count = document.querySelectorAll("[data-notification-row].is-unread").length;
    setUnread(count);
    filterNotifications();
    showToast(wasUnread ? "Notification marked as read." : "Notification marked as unread.", true);
  }));
  document.querySelector("[data-clear-notifications]")?.addEventListener("click", () => {
    document.querySelectorAll("[data-notification-row]").forEach((row) => row.remove());
    setUnread(0);
    filterNotifications();
    showToast("Notification history cleared in this preview.", true);
  });

  document.querySelectorAll("[data-toggle-field]").forEach((button) => button.addEventListener("click", () => {
    const field = document.getElementById(button.dataset.toggleField);
    const opening = !field?.classList.contains("is-open");
    field?.classList.toggle("is-open", opening);
    if (opening) field.querySelector("input")?.focus();
  }));
  document.querySelectorAll("[data-remove-chip]").forEach((button) => button.addEventListener("click", () => button.closest(".recipient-chip")?.remove()));

  const editor = document.getElementById("message-editor");
  const safePreviewHtml = (markup) => {
    const parser = new DOMParser();
    const parsed = parser.parseFromString(markup, "text/html");
    const allowed = new Set(["P", "BR", "STRONG", "B", "EM", "I", "U", "UL", "OL", "LI", "A", "BLOCKQUOTE"]);
    parsed.body.querySelectorAll("*").forEach((element) => {
      if (!allowed.has(element.tagName)) {
        element.replaceWith(...element.childNodes);
        return;
      }
      Array.from(element.attributes).forEach((attribute) => {
        if (element.tagName === "A" && attribute.name === "href") {
          const href = attribute.value.trim();
          if (!/^https:\/\//i.test(href) && !/^mailto:/i.test(href)) element.removeAttribute("href");
        } else element.removeAttribute(attribute.name);
      });
    });
    return parsed.body.innerHTML;
  };
  const updatePreview = () => {
    const subject = document.getElementById("composer-subject").value.trim() || "(No subject)";
    document.getElementById("preview-subject").textContent = subject;
    document.getElementById("preview-body").innerHTML = safePreviewHtml(editor.innerHTML);
  };
  editor?.addEventListener("input", updatePreview);
  document.getElementById("composer-subject")?.addEventListener("input", updatePreview);
  updatePreview();
  document.querySelectorAll("[data-command]").forEach((button) => button.addEventListener("click", () => {
    if (button.dataset.command === "createLink") {
      showToast("Link insertion is simulated in this public preview.");
      return;
    }
    editor?.focus();
    document.execCommand(button.dataset.command, false, null);
    updatePreview();
  }));
  document.querySelector("[data-confirm-send]")?.addEventListener("click", () => {
    const accepted = window.confirm("This is a static preview. No email will be sent. Continue to the simulated review?");
    if (accepted) showToast("Preview checked. Sending is disabled in this demo.", true);
  });

  const modal = document.getElementById("recipient-modal");
  document.querySelector("[data-open-modal='recipient-modal']")?.addEventListener("click", () => modal?.showModal());
  document.querySelectorAll("[data-close-modal]").forEach((button) => button.addEventListener("click", () => modal?.close()));
  document.getElementById("recipient-demo-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const first = String(data.get("first") || "").trim();
    const last = String(data.get("last") || "").trim();
    const email = String(data.get("email") || "").trim();
    const company = String(data.get("company") || "").trim() || "Independent";
    if (!first || !last || !/^\S+@\S+\.example$/i.test(email)) {
      showToast("Use a fictional address ending in .example to add a sample contact.");
      return;
    }
    const row = document.createElement("tr");
    row.dataset.recipientRow = "";
    row.dataset.status = "active";
    const initials = (first.charAt(0) + last.charAt(0)).toUpperCase();
    const cells = Array.from({ length: 7 }, () => document.createElement("td"));
    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "row-check";
    check.setAttribute("aria-label", "Select " + first + " " + last);
    cells[0].append(check);
    const person = document.createElement("span"); person.className = "person-cell";
    const avatar = document.createElement("span"); avatar.className = "avatar avatar-cyan"; avatar.textContent = initials;
    const personCopy = document.createElement("span");
    const personName = document.createElement("strong"); personName.textContent = first + " " + last;
    const personEmail = document.createElement("small"); personEmail.textContent = email;
    personCopy.append(personName, personEmail); person.append(avatar, personCopy); cells[1].append(person);
    const status = document.createElement("span"); status.className = "status status-active";
    const statusDot = document.createElement("i"); status.append(statusDot, document.createTextNode("Active")); cells[2].append(status);
    cells[3].textContent = company; cells[4].textContent = "Just now"; cells[5].textContent = "—";
    const more = document.createElement("button"); more.type = "button"; more.className = "icon-button table-more"; more.setAttribute("aria-label", "More options for " + first + " " + last); more.dataset.toast = "Recipient actions are simulated.";
    const moreIcon = document.createElementNS("http://www.w3.org/2000/svg", "svg"); moreIcon.setAttribute("class", "icon");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use"); use.setAttribute("href", "#i-more"); moreIcon.append(use); more.append(moreIcon); cells[6].append(more);
    cells.forEach((cell) => row.append(cell));
    document.querySelector("#recipient-table tbody").prepend(row);
    recipientRows.unshift(row);
    filterRecipients();
    modal?.close();
    event.currentTarget.reset();
    showToast("Sample recipient added to this page preview.", true);
  });

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-toast]");
    if (button) showToast(button.dataset.toast);
  });
  document.querySelector("[data-save-settings]")?.addEventListener("click", () => showToast("Preferences previewed. Changes are not sent to a server.", true));
  document.getElementById("analytics-range")?.addEventListener("change", (event) => {
    const labelByRange = { "7": "7 days", "30": "30 days", "90": "90 days", all: "all time" };
    document.getElementById("analytics-range-copy").textContent = "Campaign activity in the last " + labelByRange[event.currentTarget.value];
    showToast("Date range changed. Chart values remain illustrative.", true);
  });
  document.querySelector("[data-export-csv]")?.addEventListener("click", () => {
    const csv = "Campaign,Status,Recipients,Delivered,Open rate\nOctober product update,Completed,1842,1816,42.8%\nStudio notes Issue 08,Scheduled,2106,,,\nWelcome to Northstar,Completed,486,481,58.2%\n";
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "mailpilot-demo-report.csv"; anchor.hidden = true;
    document.body.append(anchor); anchor.click(); anchor.remove(); URL.revokeObjectURL(url);
    showToast("Fictional sample report downloaded.", true);
  });
  // All interactions above remain local to this document. No requests or service connections are made.
})();
