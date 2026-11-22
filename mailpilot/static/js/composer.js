(() => {
  const main = document.getElementById("composer-main");
  const form = document.getElementById("composer-form");
  const editor = document.getElementById("message-editor");
  const source = document.getElementById("body-html");
  if (!main || !form || !editor || !source) return;

  const subject = document.getElementById("subject");
  const sender = document.getElementById("sender_identity");
  const iframe = document.getElementById("email-preview");
  const previewToggle = document.getElementById("toggle-preview");
  const counter = document.getElementById("editor-count");
  const feedback = document.getElementById("save-feedback");
  const saveAction = form.dataset.isEdit === "true" ? "Save changes" : "Save draft";
  const allowedTags = new Set([
    "A", "BLOCKQUOTE", "BR", "DIV", "EM", "H1", "H2", "H3", "HR",
    "I", "LI", "OL", "P", "SPAN", "STRONG", "U", "UL",
  ]);
  const droppedTags = new Set(["IFRAME", "MATH", "OBJECT", "SCRIPT", "STYLE", "SVG", "TEMPLATE"]);
  const alignmentTags = new Set(["BLOCKQUOTE", "DIV", "H1", "H2", "H3", "LI", "P", "SPAN"]);
  const editorCommands = new Set([
    "bold", "italic", "underline", "insertUnorderedList", "insertOrderedList",
    "justifyLeft", "justifyCenter", "justifyRight",
  ]);

  const safeLink = (value) => {
    const link = String(value || "").trim();
    if (!link || /[\u0000-\u001f\u007f]/.test(link)) return "";
    if (link.startsWith("#")) return link;
    try {
      const parsed = new URL(link, window.location.href);
      return ["http:", "https:", "mailto:"].includes(parsed.protocol.toLowerCase()) ? link : "";
    } catch (_error) {
      return "";
    }
  };

  const safeAlignment = (value) => {
    const match = String(value || "").match(/(?:^|;)\s*text-align\s*:\s*(left|center|right|justify)\s*(?:;|$)/i);
    return match ? match[1].toLowerCase() : "";
  };

  const cleanNode = (node, targetDocument) => {
    if (node.nodeType === Node.TEXT_NODE) return targetDocument.createTextNode(node.nodeValue || "");
    if (node.nodeType !== Node.ELEMENT_NODE) return null;
    const tag = node.tagName.toUpperCase();
    if (droppedTags.has(tag)) return null;
    if (!allowedTags.has(tag)) {
      const fragment = targetDocument.createDocumentFragment();
      [...node.childNodes].forEach((child) => {
        const clean = cleanNode(child, targetDocument);
        if (clean) fragment.appendChild(clean);
      });
      return fragment;
    }

    const element = targetDocument.createElement(tag.toLowerCase());
    if (tag === "A") {
      const href = safeLink(node.getAttribute("href"));
      if (href) element.setAttribute("href", href);
      const title = node.getAttribute("title");
      if (title) element.setAttribute("title", title.slice(0, 200));
    }
    if (alignmentTags.has(tag)) {
      const alignment = safeAlignment(node.getAttribute("style"));
      if (alignment) element.setAttribute("style", `text-align: ${alignment}`);
    }
    [...node.childNodes].forEach((child) => {
      const clean = cleanNode(child, targetDocument);
      if (clean) element.appendChild(clean);
    });
    return element;
  };

  const sanitizeHtml = (html) => {
    const parsed = new DOMParser().parseFromString(String(html || ""), "text/html");
    const cleaned = document.createElement("div");
    [...parsed.body.childNodes].forEach((node) => {
      const safe = cleanNode(node, document);
      if (safe) cleaned.appendChild(safe);
    });
    return cleaned.innerHTML;
  };

  const syncSource = (markDirty = true) => {
    source.value = editor.innerHTML;
    const count = (editor.textContent || "").length;
    counter.textContent = `${count.toLocaleString()} character${count === 1 ? "" : "s"}`;
    if (markDirty) feedback.textContent = `Unsaved changes · choose ${saveAction} to keep them.`;
  };

  const syncToolbarState = () => {
    document.querySelectorAll("[data-command]").forEach((button) => {
      const command = button.dataset.command;
      if (!editorCommands.has(command)) return;
      let active = false;
      try {
        active = document.queryCommandState(command);
      } catch (_error) {
        active = false;
      }
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });
  };

  const markDirty = () => {
    feedback.textContent = `Unsaved changes · choose ${saveAction} to keep them.`;
  };

  const escapeHtml = (value) => String(value || "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
  })[character]);

  const updatePreview = () => {
    if (!iframe) return;
    const safeBody = sanitizeHtml(editor.innerHTML);
    const safeSubject = escapeHtml(subject?.value || "Untitled email");
    const safeSender = escapeHtml(sender?.selectedOptions[0]?.textContent || "MailPilot Workspace");
    iframe.srcdoc = `<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;background:#fff;color:#1f2937;font:14px/1.65 Arial,sans-serif}.mail{max-width:680px;margin:24px auto;padding:0 14px}.meta,.content{background:#fff;border:1px solid #e5e7eb}.meta{padding:17px 20px;border-radius:10px 10px 0 0;color:#64748b;font-size:12px}.meta strong{display:block;margin-bottom:4px;color:#1f2937;font-size:14px}.content{min-height:210px;padding:24px 20px;border-top:0;border-radius:0 0 10px 10px;overflow-wrap:anywhere}.content p:first-child{margin-top:0}a{color:#00749A}</style></head><body><main class="mail"><header class="meta"><strong>${safeSubject || "Untitled email"}</strong>From ${safeSender}</header><article class="content">${safeBody || "<p style=\"color:#94a3b8\">Your email content will appear here.</p>"}</article></main></body></html>`;
  };

  editor.addEventListener("input", () => {
    syncSource();
    if (!iframe.hidden) updatePreview();
  });
  subject?.addEventListener("input", () => {
    markDirty();
    if (!iframe.hidden) updatePreview();
  });
  sender?.addEventListener("change", () => {
    markDirty();
    if (!iframe.hidden) updatePreview();
  });
  ["to_recipients", "cc_recipients", "bcc_recipients"].forEach((name) => {
    document.getElementById(name)?.addEventListener("input", markDirty);
  });

  editor.addEventListener("paste", (event) => {
    event.preventDefault();
    const clipboard = event.clipboardData;
    const html = clipboard?.getData("text/html");
    const text = clipboard?.getData("text/plain") || "";
    if (html) {
      document.execCommand("insertHTML", false, sanitizeHtml(html));
    } else {
      document.execCommand("insertText", false, text);
    }
  });

  document.querySelectorAll("[data-command]").forEach((button) => {
    button.addEventListener("mousedown", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      const command = button.dataset.command;
      if (!editorCommands.has(command)) return;
      editor.focus();
      document.execCommand(command, false, null);
      syncSource();
      syncToolbarState();
      if (!iframe.hidden) updatePreview();
    });
  });

  form.addEventListener("submit", (event) => {
    const recipientFields = ["to_recipients", "cc_recipients", "bcc_recipients"]
      .map((name) => document.getElementById(name)?.value || "");
    const hasRecipient = recipientFields.some((value) => value.split(/[,;\r\n]+/).some((part) => part.trim()));
    if (!hasRecipient) {
      event.preventDefault();
      feedback.textContent = "Add at least one email address before saving this draft.";
      document.getElementById("to_recipients")?.focus();
      return;
    }
    if (!subject?.value.trim()) {
      event.preventDefault();
      feedback.textContent = "Add a subject before saving this draft.";
      subject?.focus();
      return;
    }
    if (!(editor.textContent || "").trim()) {
      event.preventDefault();
      feedback.textContent = "Add some message text before saving this draft.";
      editor.focus();
      return;
    }
    source.value = editor.innerHTML;
    feedback.textContent = saveAction === "Save changes" ? "Saving changes…" : "Saving draft…";
  });

  form.addEventListener("submit", (event) => {
    if (event.defaultPrevented || !event.submitter?.matches?.("[data-send-button]")) return;
    const consent = form.querySelector('[name="consent_confirmed"]');
    if (!consent?.checked) {
      event.preventDefault();
      feedback.textContent = "Confirm recipient consent before sending this email.";
      consent?.focus();
      return;
    }
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    const count = ["to_recipients", "cc_recipients", "bcc_recipients"]
      .map((name) => document.getElementById(name)?.value || "")
      .reduce((total, value) => total + value.split(/[,;\r\n]+/).filter((part) => part.trim()).length, 0);
    if (!window.confirm(`Send this email to ${count} recipient${count === 1 ? "" : "s"}? This action cannot be undone.`)) {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = "true";
    event.submitter.setAttribute("aria-disabled", "true");
    const label = event.submitter.querySelector("span");
    if (label) label.textContent = "Sending…";
    feedback.textContent = "Sending email… Keep this page open until MailPilot responds.";
  });

  previewToggle?.addEventListener("click", () => {
    const showPreview = previewToggle.getAttribute("aria-pressed") !== "true";
    previewToggle.setAttribute("aria-pressed", String(showPreview));
    previewToggle.textContent = showPreview ? "Edit message" : "Preview";
    editor.hidden = showPreview;
    iframe.hidden = !showPreview;
    if (showPreview) updatePreview();
  });

  // Keep the editor transport value synchronized for keyboard and initial content.
  syncSource(false);
  syncToolbarState();
  document.addEventListener("selectionchange", () => {
    const anchor = window.getSelection()?.anchorNode;
    if (anchor && (anchor === editor || editor.contains(anchor))) syncToolbarState();
  });

  const contactDialog = document.getElementById("contact-dialog");
  const contactSearch = document.getElementById("contact-search");
  const contactDestination = document.getElementById("contact-destination");
  const contactResults = document.getElementById("contact-results");
  const pickerButton = document.getElementById("open-contact-picker");
  let contactTimer;
  let contactController;

  const showContactMessage = (message) => {
    contactResults.replaceChildren();
    const paragraph = document.createElement("p");
    paragraph.className = "contact-results-message";
    paragraph.textContent = message;
    contactResults.appendChild(paragraph);
  };

  const appendContact = (contact) => {
    const field = document.getElementById(contactDestination.value);
    if (!field) return;
    const addresses = field.value.split(/[,;\r\n]+/).map((part) => part.trim().toLowerCase()).filter(Boolean);
    if (addresses.includes(contact.email.toLowerCase())) {
      feedback.textContent = `${contact.email} is already in this field.`;
      return;
    }
    const trimmed = field.value.trim();
    field.value = trimmed ? `${trimmed.replace(/[,;\s]+$/, "")}, ${contact.email}` : contact.email;
    feedback.textContent = `${contact.email} added to ${contactDestination.options[contactDestination.selectedIndex].text}.`;
  };

  const renderContacts = (contacts) => {
    contactResults.replaceChildren();
    if (!contacts.length) {
      showContactMessage("No active recipients matched. Add contacts in Recipient Management first.");
      return;
    }
    contacts.forEach((contact) => {
      const row = document.createElement("div");
      row.className = "contact-result";
      const avatar = document.createElement("span");
      avatar.className = "contact-result-avatar";
      avatar.textContent = (contact.name || contact.email).trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
      const copy = document.createElement("span");
      copy.className = "contact-result-copy";
      const name = document.createElement("strong");
      name.textContent = contact.name || "Saved recipient";
      const email = document.createElement("span");
      email.textContent = contact.company ? `${contact.email} · ${contact.company}` : contact.email;
      copy.append(name, email);
      const add = document.createElement("button");
      add.className = "contact-add-button";
      add.type = "button";
      add.textContent = "Add";
      add.addEventListener("click", () => appendContact(contact));
      row.append(avatar, copy, add);
      contactResults.appendChild(row);
    });
  };

  const searchContacts = async () => {
    if (contactController) contactController.abort();
    contactController = new AbortController();
    showContactMessage("Searching saved recipients…");
    const endpoint = new URL(main.dataset.contactsUrl, window.location.origin);
    endpoint.searchParams.set("q", contactSearch.value.trim());
    try {
      const response = await fetch(endpoint, {
        headers: { Accept: "application/json" },
        signal: contactController.signal,
      });
      if (!response.ok) throw new Error("Contact search failed.");
      const data = await response.json();
      renderContacts(data.recipients || []);
    } catch (error) {
      if (error.name !== "AbortError") showContactMessage("Could not load saved contacts. Please try again.");
    }
  };

  pickerButton?.addEventListener("click", () => {
    contactDialog.showModal();
    contactSearch.value = "";
    searchContacts();
    contactSearch.focus();
  });
  contactSearch?.addEventListener("input", () => {
    window.clearTimeout(contactTimer);
    contactTimer = window.setTimeout(searchContacts, 180);
  });

  const linkDialog = document.getElementById("link-dialog");
  const linkButton = document.getElementById("open-link-dialog");
  const linkForm = document.getElementById("link-form");
  const linkUrl = document.getElementById("link-url");
  let savedSelection;

  linkButton?.addEventListener("mousedown", (event) => event.preventDefault());
  linkButton?.addEventListener("click", () => {
    const selection = window.getSelection();
    savedSelection = selection && selection.rangeCount ? selection.getRangeAt(0).cloneRange() : null;
    linkUrl.value = "";
    linkDialog.showModal();
    linkUrl.focus();
  });
  document.getElementById("close-link-dialog")?.addEventListener("click", () => linkDialog.close());
  document.getElementById("cancel-link")?.addEventListener("click", () => linkDialog.close());
  linkForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    const href = safeLink(linkUrl.value);
    if (!href) {
      linkUrl.setCustomValidity("Enter a safe http, https or mailto link.");
      linkUrl.reportValidity();
      return;
    }
    linkUrl.setCustomValidity("");
    editor.focus();
    if (savedSelection) {
      const selection = window.getSelection();
      selection.removeAllRanges();
      selection.addRange(savedSelection);
    }
    document.execCommand("createLink", false, href);
    syncSource();
    if (!iframe.hidden) updatePreview();
    linkDialog.close();
  });
  linkUrl?.addEventListener("input", () => linkUrl.setCustomValidity(""));

  document.querySelectorAll("[data-confirm-delete]").forEach((deleteForm) => {
    deleteForm.addEventListener("submit", (event) => {
      if (!window.confirm("Delete this saved draft? This cannot be undone.")) {
        event.preventDefault();
      }
    });
  });
})();
