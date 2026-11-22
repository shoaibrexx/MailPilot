(() => {
  const allowedTags = new Set([
    "A", "BLOCKQUOTE", "BR", "DIV", "EM", "H1", "H2", "H3", "HR",
    "I", "LI", "OL", "P", "SPAN", "STRONG", "U", "UL",
  ]);
  const droppedTags = new Set(["IFRAME", "MATH", "OBJECT", "SCRIPT", "STYLE", "SVG", "TEMPLATE"]);
  const alignmentTags = new Set(["BLOCKQUOTE", "DIV", "H1", "H2", "H3", "LI", "P", "SPAN"]);

  const safeLink = (value) => {
    const link = String(value || "").trim();
    if (!link || /[\u0000-\u001f\u007f]/.test(link)) return "";
    if (link.startsWith("#")) return link;
    try {
      const protocol = new URL(link, window.location.href).protocol.toLowerCase();
      return ["http:", "https:", "mailto:"].includes(protocol) ? link : "";
    } catch (_error) {
      return "";
    }
  };

  const cleanHtmlNode = (node, targetDocument) => {
    if (node.nodeType === Node.TEXT_NODE) return targetDocument.createTextNode(node.nodeValue || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = node.tagName.toUpperCase();
    if (droppedTags.has(tag)) return null;
    if (!allowedTags.has(tag)) {
      const fragment = targetDocument.createDocumentFragment();
      [...node.childNodes].forEach((child) => {
        const clean = cleanHtmlNode(child, targetDocument);
        if (clean) fragment.appendChild(clean);
      });
      return fragment;
    }

    const clean = targetDocument.createElement(tag.toLowerCase());
    if (tag === "A") {
      const href = safeLink(node.getAttribute("href"));
      if (href) clean.setAttribute("href", href);
      const title = node.getAttribute("title");
      if (title) clean.setAttribute("title", title.slice(0, 200));
    }
    if (alignmentTags.has(tag)) {
      const match = (node.getAttribute("style") || "").match(/(?:^|;)\s*text-align\s*:\s*(left|center|right|justify)\s*(?:;|$)/i);
      if (match) clean.setAttribute("style", `text-align: ${match[1].toLowerCase()}`);
    }
    [...node.childNodes].forEach((child) => {
      const safeChild = cleanHtmlNode(child, targetDocument);
      if (safeChild) clean.appendChild(safeChild);
    });
    return clean;
  };

  const sanitizeHtml = (html) => {
    const parsed = new DOMParser().parseFromString(String(html || ""), "text/html");
    const root = document.createElement("div");
    [...parsed.body.childNodes].forEach((node) => {
      const clean = cleanHtmlNode(node, document);
      if (clean) root.appendChild(clean);
    });
    return root.innerHTML;
  };

  const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[character]);

  const draftSelect = document.getElementById("email-draft-id");
  const draftDataElement = document.getElementById("campaign-draft-data");
  const previewSubject = document.querySelector("[data-preview-subject]");
  const previewFrame = document.querySelector("[data-preview-frame]");
  let draftData = [];
  try {
    draftData = JSON.parse(draftDataElement?.textContent || "[]");
  } catch (_error) {
    draftData = [];
  }
  const updateDraftPreview = () => {
    if (!draftSelect || !previewSubject || !previewFrame) return;
    const draft = draftData.find((item) => String(item.id) === draftSelect.value);
    previewSubject.textContent = draft ? draft.subject || "Untitled draft" : "Choose a draft to preview its subject";
    const body = draft ? sanitizeHtml(draft.body_html) : "";
    previewFrame.srcdoc = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{font:14px/1.6 Arial,sans-serif;color:#1f2937;padding:18px;background:#fff}p:first-child{margin-top:0}a{color:#00749A}</style></head><body>${body || "<p style=\"color:#94a3b8\">Select a saved draft to preview email content.</p>"}</body></html>`;
  };
  draftSelect?.addEventListener("change", updateDraftPreview);
  if (draftSelect) updateDraftPreview();

  const statusSelect = document.getElementById("campaign-status");
  const scheduleField = document.querySelector("[data-schedule-field]");
  const scheduleInput = document.getElementById("scheduled-at");
  const updateScheduleVisibility = () => {
    if (!statusSelect || !scheduleField) return;
    const scheduled = statusSelect.value === "scheduled";
    scheduleField.hidden = !scheduled;
    scheduleInput?.toggleAttribute("required", scheduled);
  };
  statusSelect?.addEventListener("change", updateScheduleVisibility);
  if (statusSelect) updateScheduleVisibility();

  document.querySelectorAll("[data-recipient-picker]").forEach((picker) => {
    const hiddenIds = picker.querySelector(".recipient-picker-ids");
    const searchInput = picker.querySelector(".recipient-search");
    const resultList = picker.querySelector(".recipient-search-results");
    const selectedList = picker.querySelector(".selected-recipient-list");
    if (!hiddenIds || !searchInput || !resultList || !selectedList) return;

    let selected = new Map();
    try {
      const initial = JSON.parse(picker.dataset.selected || "[]");
      initial.forEach((recipient) => selected.set(String(recipient.id), recipient));
    } catch (_error) {
      selected = new Map();
    }

    const syncIds = () => {
      hiddenIds.value = [...selected.keys()].join(",");
    };
    const renderSelected = () => {
      selectedList.replaceChildren();
      selected.forEach((recipient, id) => {
        const chip = document.createElement("span");
        chip.className = "selected-recipient";
        chip.dataset.selectedRecipient = id;
        const label = document.createElement("span");
        label.textContent = `${recipient.name || recipient.email} · ${recipient.email}`;
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "×";
        remove.setAttribute("aria-label", `Remove ${recipient.email}`);
        remove.addEventListener("click", () => {
          selected.delete(id);
          syncIds();
          renderSelected();
          renderSearchResults(lastResults);
        });
        chip.append(label, remove);
        selectedList.appendChild(chip);
      });
      syncIds();
    };
    const showMessage = (message) => {
      resultList.replaceChildren();
      const paragraph = document.createElement("p");
      paragraph.className = "recipient-picker-message";
      paragraph.textContent = message;
      resultList.appendChild(paragraph);
    };

    let lastResults = [];
    const renderSearchResults = (recipients) => {
      lastResults = recipients;
      resultList.replaceChildren();
      if (!recipients.length) {
        showMessage("No active recipients matched your search.");
        return;
      }
      recipients.forEach((recipient) => {
        const row = document.createElement("label");
        row.className = "audience-search-result";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = selected.has(String(recipient.id));
        checkbox.setAttribute("aria-label", `Select ${recipient.email}`);
        const copy = document.createElement("span");
        copy.className = "audience-search-result-copy";
        const name = document.createElement("strong");
        name.textContent = recipient.name || recipient.email;
        const email = document.createElement("span");
        email.textContent = recipient.company ? `${recipient.email} · ${recipient.company}` : recipient.email;
        copy.append(name, email);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) selected.set(String(recipient.id), recipient);
          else selected.delete(String(recipient.id));
          syncIds();
          renderSelected();
        });
        row.append(checkbox, copy);
        resultList.appendChild(row);
      });
    };

    let timer;
    let controller;
    const searchRecipients = async () => {
      controller?.abort();
      controller = new AbortController();
      showMessage("Searching active recipients…");
      const endpoint = new URL(picker.dataset.searchUrl, window.location.origin);
      endpoint.searchParams.set("q", searchInput.value.trim());
      try {
        const response = await fetch(endpoint, {
          headers: { Accept: "application/json" },
          credentials: "same-origin",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("Recipient search failed.");
        const data = await response.json();
        renderSearchResults(data.recipients || []);
      } catch (error) {
        if (error.name !== "AbortError") showMessage("Could not load recipients. Please try again.");
      }
    };
    searchInput.addEventListener("input", () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(searchRecipients, 180);
    });
    renderSelected();
  });

  document.querySelectorAll("[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Delete this item? This cannot be undone.")) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-campaign-send]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (form.dataset.submitting === "true") {
        event.preventDefault();
        return;
      }
      const consent = form.querySelector('[name="consent_confirmed"]');
      if (!consent?.checked) {
        event.preventDefault();
        consent?.focus();
        return;
      }
      if (!window.confirm("Send this campaign now? Recipients will be Bcc'd, and completed or uncertain attempts cannot be submitted again.")) {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "true";
      const button = form.querySelector("[data-send-button]");
      button?.setAttribute("aria-disabled", "true");
      const label = button?.querySelector("span");
      if (label) label.textContent = "Sending…";
    });
  });
})();
