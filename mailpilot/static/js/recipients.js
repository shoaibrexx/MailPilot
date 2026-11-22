(() => {
  document.querySelectorAll("form[data-confirm-delete]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm("Delete this recipient? This action cannot be undone.")) {
        event.preventDefault();
      }
    });
  });

  const fileInput = document.getElementById("recipient-csv-file");
  const fileName = document.getElementById("upload-filename");
  const uploadTitle = document.getElementById("upload-title");
  fileInput?.addEventListener("change", () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    if (uploadTitle) uploadTitle.textContent = file.name;
    if (fileName) fileName.textContent = `${(file.size / 1024).toFixed(1)} KB selected`;
  });
})();
