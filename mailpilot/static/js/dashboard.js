(() => {
  const dashboard = document.getElementById("dashboard");
  if (!dashboard) return;

  const numberFormat = new Intl.NumberFormat();
  const chartSvg = document.getElementById("activity-chart-svg");
  const chartEmptyState = document.querySelector(".chart-empty-state");
  const campaignsBody = document.getElementById("recent-campaigns-body");
  const campaignsEmptyRow = document.getElementById("campaigns-empty-row");
  const dataNote = document.getElementById("dashboard-data-note");
  let latestActivity = [];

  const svgElement = (name, attributes = {}) => {
    const element = document.createElementNS("http://www.w3.org/2000/svg", name);
    for (const [key, value] of Object.entries(attributes)) {
      element.setAttribute(key, String(value));
    }
    return element;
  };

  const renderStats = (stats = {}) => {
    for (const [key, stat] of Object.entries(stats)) {
      const card = document.querySelector(`[data-stat-card="${key}"]`);
      const valueElement = document.querySelector(`[data-stat-value="${key}"]`);
      const messageElement = document.querySelector(`[data-stat-message="${key}"]`);
      if (!card || !valueElement || !messageElement) continue;

      const hasValue = stat && Number.isFinite(stat.value);
      valueElement.textContent = hasValue ? numberFormat.format(stat.value) : "—";
      messageElement.textContent = stat?.message || "No data available yet.";
      card.dataset.available = String(Boolean(hasValue));
    }
  };

  const renderChart = (activity) => {
    if (!chartSvg || !chartEmptyState) return;
    const points = Array.isArray(activity)
      ? activity.map((point) => ({
          label: String(point.label || point.date || ""),
          accepted: point.accepted,
          failed: point.failed,
        })).filter((point) => point.label &&
          Number.isFinite(point.accepted) && Number.isFinite(point.failed))
      : [];

    if (points.length < 2) {
      chartSvg.hidden = true;
      chartEmptyState.hidden = false;
      return;
    }

    chartSvg.replaceChildren();
    chartSvg.hidden = false;
    chartEmptyState.hidden = true;

    const width = 720;
    const height = 270;
    const left = 42;
    const right = 12;
    const top = 14;
    const bottom = 34;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;
    const maxValue = Math.max(1, ...points.flatMap((point) => [point.accepted, point.failed]));
    const step = maxValue / 4;
    const colors = getComputedStyle(document.documentElement);
    const gridColor = colors.getPropertyValue("--color-chart-grid").trim();
    const mutedColor = colors.getPropertyValue("--color-muted").trim();
    const sentColor = colors.getPropertyValue("--color-success").trim();
    const failedColor = colors.getPropertyValue("--color-danger").trim();

    for (let index = 0; index <= 4; index += 1) {
      const y = top + plotHeight - (plotHeight * index) / 4;
      chartSvg.append(svgElement("line", {
        x1: left, y1: y, x2: width - right, y2: y,
        stroke: gridColor, "stroke-width": 1,
      }));
      const label = svgElement("text", {
        x: left - 9, y: y + 4, fill: mutedColor,
        "font-size": 10, "text-anchor": "end",
      });
      label.textContent = numberFormat.format(Math.round(step * index));
      chartSvg.append(label);
    }

    const xFor = (index) => left + (plotWidth * index) / (points.length - 1);
    const yFor = (value) => top + plotHeight - (plotHeight * value) / maxValue;

    for (const [key, color] of [["accepted", sentColor], ["failed", failedColor]]) {
      const line = points.map((point, index) =>
        `${index === 0 ? "M" : "L"}${xFor(index)},${yFor(point[key])}`
      ).join(" ");
      chartSvg.append(svgElement("path", {
        d: line, fill: "none", stroke: color,
        "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round",
      }));
      points.forEach((point, index) => {
        chartSvg.append(svgElement("circle", {
          cx: xFor(index), cy: yFor(point[key]), r: 3.5,
          fill: color,
          stroke: getComputedStyle(document.documentElement)
            .getPropertyValue("--color-surface").trim(),
          "stroke-width": 2,
        }));
      });
    }

    const labelIndexes = [...new Set([
      0,
      Math.floor((points.length - 1) / 2),
      points.length - 1,
    ])];
    labelIndexes.forEach((index) => {
      const label = svgElement("text", {
        x: xFor(index), y: height - 8, fill: mutedColor,
        "font-size": 10, "text-anchor": index === 0 ? "start" :
          (index === points.length - 1 ? "end" : "middle"),
      });
      label.textContent = points[index].label;
      chartSvg.append(label);
    });
  };

  const formatDate = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime())
      ? "—"
      : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
  };

  const renderCampaigns = (campaigns) => {
    if (!campaignsBody || !campaignsEmptyRow) return;
    campaignsBody.querySelectorAll("tr:not(#campaigns-empty-row)").forEach((row) => row.remove());
    const rows = Array.isArray(campaigns) ? campaigns : [];
    campaignsEmptyRow.hidden = rows.length > 0;

    rows.forEach((campaign) => {
      const row = document.createElement("tr");
      const name = document.createElement("td");
      name.className = "campaign-name-cell";
      const nameLink = document.createElement("a");
      nameLink.className = "campaign-name-cell-link";
      nameLink.href = `/campaigns/${encodeURIComponent(campaign.id)}`;
      nameLink.textContent = String(campaign.name || "Untitled campaign");
      name.append(nameLink);

      const statusCell = document.createElement("td");
      const status = String(campaign.status || "unknown").toLowerCase();
      const statusBadge = document.createElement("span");
      statusBadge.className = `campaign-status campaign-status--${
        ["sent", "completed", "failed", "draft", "scheduled", "ready"].includes(status) ? status : "pending"
      }`;
      statusBadge.textContent = status.charAt(0).toUpperCase() + status.slice(1);
      statusCell.append(statusBadge);

      const recipients = document.createElement("td");
      recipients.textContent = Number.isFinite(campaign.recipient_count)
        ? numberFormat.format(campaign.recipient_count)
        : "—";

      const date = document.createElement("td");
      const schedule = campaign.scheduled_at || campaign.updated_at;
      date.textContent = schedule ? formatDate(schedule) : "—";

      row.append(name, statusCell, recipients, date);
      campaignsBody.append(row);
    });
  };

  const fetchDashboard = async () => {
    try {
      const response = await fetch(dashboard.dataset.dashboardUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error("Dashboard data request failed.");
      const data = await response.json();
      renderStats(data.stats);
      latestActivity = Array.isArray(data.activity) ? data.activity : [];
      renderChart(latestActivity);
      renderCampaigns(data.recent_campaigns);
      if (dataNote && data.data_status === "partial") {
        dataNote.textContent = "SMTP acceptance is recorded but does not confirm inbox delivery. Partial and uncertain outcomes are available in Analytics.";
      }
    } catch (_error) {
      if (dataNote) {
        dataNote.textContent = "Dashboard data could not be loaded. Refresh the page to try again.";
      }
    }
  };

  window.addEventListener("mailpilot:themechange", () => renderChart(latestActivity));
  fetchDashboard();
})();
