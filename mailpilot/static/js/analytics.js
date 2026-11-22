(() => {
  "use strict";

  const payload = document.getElementById("analytics-chart-data");
  if (!payload) return;

  let report;
  try {
    report = JSON.parse(payload.textContent || "{}");
  } catch (error) {
    return;
  }

  const SVG_NS = "http://www.w3.org/2000/svg";
  const numberFormat = new Intl.NumberFormat();

  function makeSvgElement(name, attributes = {}) {
    const element = document.createElementNS(SVG_NS, name);
    Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
    return element;
  }

  function renderLineChart(container, points, title, unit) {
    if (!container || !Array.isArray(points) || points.length === 0) return;
    const hint = container.nextElementSibling;
    const width = 720;
    const height = 220;
    const pad = { top: 14, right: 18, bottom: 32, left: 42 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const maxValue = Math.max(1, ...points.map((point) => Number(point.value) || 0));
    const svg = makeSvgElement("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": title,
      preserveAspectRatio: "none",
    });
    const coordinates = points.map((point, index) => ({
      ...point,
      x: pad.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth),
      y: pad.top + plotHeight - ((Number(point.value) || 0) / maxValue) * plotHeight,
    }));

    [0, 0.5, 1].forEach((fraction) => {
      const y = pad.top + plotHeight * fraction;
      svg.appendChild(makeSvgElement("line", {
        x1: pad.left,
        x2: width - pad.right,
        y1: y,
        y2: y,
        class: "chart-grid-line",
      }));
      const label = makeSvgElement("text", {
        x: pad.left - 8,
        y: y + 3,
        class: "chart-axis-label",
        "text-anchor": "end",
      });
      label.textContent = numberFormat.format(Math.round(maxValue * (1 - fraction)));
      svg.appendChild(label);
    });

    const pathData = coordinates.map((point, index) => `${index ? "L" : "M"}${point.x},${point.y}`).join(" ");
    const areaData = `${pathData} L${coordinates[coordinates.length - 1].x},${pad.top + plotHeight} L${coordinates[0].x},${pad.top + plotHeight} Z`;
    svg.appendChild(makeSvgElement("path", { d: areaData, class: "chart-area" }));
    svg.appendChild(makeSvgElement("path", { d: pathData, class: "chart-line" }));

    const labelInterval = Math.max(1, Math.ceil(points.length / 6));
    coordinates.forEach((point, index) => {
      const marker = makeSvgElement("circle", {
        cx: point.x,
        cy: point.y,
        r: 4.5,
        tabindex: "0",
        role: "img",
        class: "chart-point",
        "aria-label": `${point.label}: ${numberFormat.format(point.value)} ${unit}`,
      });
      const titleNode = makeSvgElement("title");
      titleNode.textContent = `${point.label}: ${numberFormat.format(point.value)} ${unit}`;
      marker.appendChild(titleNode);
      const announcePoint = () => {
        if (hint) hint.textContent = `${point.label}: ${numberFormat.format(point.value)} ${unit}.`;
      };
      marker.addEventListener("pointerenter", announcePoint);
      marker.addEventListener("focus", announcePoint);
      marker.addEventListener("blur", () => {
        if (hint) hint.textContent = "Focus or hover a point to inspect its value.";
      });
      svg.appendChild(marker);

      if (index % labelInterval === 0 || index === coordinates.length - 1) {
        const xLabel = makeSvgElement("text", {
          x: point.x,
          y: height - 7,
          class: "chart-axis-label",
          "text-anchor": index === 0 ? "start" : index === coordinates.length - 1 ? "end" : "middle",
        });
        xLabel.textContent = point.label;
        svg.appendChild(xLabel);
      }
    });
    container.replaceChildren(svg);
  }

  function renderOutcomeChart(container, points) {
    if (!container || !Array.isArray(points) || points.length === 0) return;
    const hint = container.nextElementSibling;
    const width = 720;
    const height = 220;
    const pad = { top: 14, right: 18, bottom: 32, left: 42 };
    const plotWidth = width - pad.left - pad.right;
    const plotHeight = height - pad.top - pad.bottom;
    const series = [
      { key: "accepted", label: "SMTP accepted messages", className: "accepted" },
      { key: "failed", label: "Known failed attempts", className: "failed" },
    ];
    const maxValue = Math.max(1, ...points.flatMap((point) =>
      series.map((item) => Number(point[item.key]) || 0)
    ));
    const svg = makeSvgElement("svg", {
      viewBox: `0 0 ${width} ${height}`,
      role: "img",
      "aria-label": "SMTP accepted and known failed email message outcomes over time",
      preserveAspectRatio: "none",
    });
    const coordinates = points.map((point, index) => ({
      ...point,
      x: pad.left + (points.length === 1 ? plotWidth / 2 : (index / (points.length - 1)) * plotWidth),
    }));

    [0, 0.5, 1].forEach((fraction) => {
      const y = pad.top + plotHeight * fraction;
      svg.appendChild(makeSvgElement("line", {
        x1: pad.left, x2: width - pad.right, y1: y, y2: y, class: "chart-grid-line",
      }));
      const label = makeSvgElement("text", {
        x: pad.left - 8, y: y + 3, class: "chart-axis-label", "text-anchor": "end",
      });
      label.textContent = numberFormat.format(Math.round(maxValue * (1 - fraction)));
      svg.appendChild(label);
    });

    const labelInterval = Math.max(1, Math.ceil(points.length / 6));
    series.forEach((item) => {
      const linePath = coordinates.map((point, index) => {
        const value = Number(point[item.key]) || 0;
        const y = pad.top + plotHeight - (value / maxValue) * plotHeight;
        return `${index ? "L" : "M"}${point.x},${y}`;
      }).join(" ");
      svg.appendChild(makeSvgElement("path", { d: linePath, class: `chart-line chart-line--${item.className}` }));

      coordinates.forEach((point) => {
        const value = Number(point[item.key]) || 0;
        const y = pad.top + plotHeight - (value / maxValue) * plotHeight;
        const description = `${point.label}: ${numberFormat.format(value)} ${item.label}`;
        const marker = makeSvgElement("circle", {
          cx: point.x, cy: y, r: 4.5, tabindex: "0", role: "img",
          class: `chart-point chart-point--${item.className}`, "aria-label": description,
        });
        const titleNode = makeSvgElement("title");
        titleNode.textContent = description;
        marker.appendChild(titleNode);
        const announce = () => { if (hint) hint.textContent = `${description}.`; };
        marker.addEventListener("pointerenter", announce);
        marker.addEventListener("focus", announce);
        marker.addEventListener("blur", () => {
          if (hint) hint.textContent = "Focus or hover a point to inspect its value.";
        });
        svg.appendChild(marker);
      });
    });

    coordinates.forEach((point, index) => {
      if (index % labelInterval !== 0 && index !== coordinates.length - 1) return;
      const label = makeSvgElement("text", {
        x: point.x, y: height - 7, class: "chart-axis-label",
        "text-anchor": index === 0 ? "start" : index === coordinates.length - 1 ? "end" : "middle",
      });
      label.textContent = point.label;
      svg.appendChild(label);
    });
    container.replaceChildren(svg);
  }

  function renderStatusDistribution(container, statuses) {
    if (!container || !Array.isArray(statuses) || statuses.length === 0) return;
    const maxValue = Math.max(1, ...statuses.map((status) => Number(status.value) || 0));
    const fragment = document.createDocumentFragment();
    statuses.forEach((status) => {
      const value = Number(status.value) || 0;
      const row = document.createElement("div");
      row.className = "status-bar-row";
      row.dataset.value = String(value);
      row.setAttribute("aria-label", `${status.label}: ${numberFormat.format(value)} campaigns`);
      row.setAttribute("role", "img");
      row.setAttribute("tabindex", "0");
      row.title = `${status.label}: ${numberFormat.format(value)} campaigns`;

      const label = document.createElement("span");
      label.className = "status-bar-label";
      label.textContent = status.label;

      const track = document.createElement("span");
      track.className = "status-bar-track";
      track.setAttribute("aria-hidden", "true");
      const fill = document.createElement("span");
      fill.className = `status-bar-fill status-dot--${status.key}`;
      fill.style.setProperty("--bar-width", `${(value / maxValue) * 100}%`);
      track.appendChild(fill);

      const count = document.createElement("strong");
      count.className = "status-bar-value";
      count.textContent = numberFormat.format(value);
      row.append(label, track, count);
      fragment.appendChild(row);
    });
    container.replaceChildren(fragment);
  }

  renderLineChart(
    document.querySelector('[data-chart="campaign-activity"]'),
    report.campaignActivity,
    "Campaign records created over time",
    "campaigns",
  );
  renderLineChart(
    document.querySelector('[data-chart="recipient-growth"]'),
    report.recipientGrowth,
    "Cumulative saved recipients over time",
    "recipients",
  );
  renderOutcomeChart(
    document.querySelector('[data-chart="sending-activity"]'),
    report.sendingActivity,
  );
  renderStatusDistribution(
    document.querySelector('[data-chart="status-distribution"]'),
    report.statusDistribution,
  );
})();
