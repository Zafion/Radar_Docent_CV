(function () {
  const config = window.CENTER_DETAIL_CONFIG || {};
  const centerCode = config.centerCode;

  const titleEl = document.getElementById("center-title");
  const subtitleEl = document.getElementById("center-subtitle");
  const cardEl = document.getElementById("center-detail-card");

  function t(value) {
    return window.FunkI18n ? window.FunkI18n.t(value) : value;
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  async function apiGet(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data?.detail || `Error ${response.status}`;
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
  }

  async function loadCenter() {
    if (!centerCode || !cardEl) return;

    try {
      const data = await apiGet(`/api/centers/${encodeURIComponent(centerCode)}`);
      const center = data.center;

      titleEl.textContent = center.denomination || `${t("Centro")} ${center.center_code}`;
      subtitleEl.textContent = [center.locality, center.province].filter(Boolean).join(" · ") || t("Ficha de centro");

      cardEl.innerHTML = `
        <div class="status-card">
          <div class="status-grid">
            <div class="status-grid__item"><span>${t("Código")}</span><strong>${escapeHtml(center.center_code || "—")}</strong></div>
            <div class="status-grid__item"><span>${t("Régimen")}</span><strong>${escapeHtml(center.regime || "—")}</strong></div>
            <div class="status-grid__item"><span>${t("Localidad")}</span><strong>${escapeHtml(center.locality || "—")}</strong></div>
            <div class="status-grid__item"><span>${t("Provincia")}</span><strong>${escapeHtml(center.province || "—")}</strong></div>
            <div class="status-grid__item"><span>${t("Comarca")}</span><strong>${escapeHtml(center.comarca || "—")}</strong></div>
            <div class="status-grid__item"><span>${t("Teléfono")}</span><strong>${escapeHtml(center.phone || "—")}</strong></div>
          </div>

          <p><strong>${t("Dirección")}:</strong> ${escapeHtml(center.full_address || "—")}</p>
          <p><strong>${t("Denominación genérica ES")}:</strong> ${escapeHtml(center.generic_name_es || "—")}</p>
          <p><strong>${t("Denominación genérica VAL")}:</strong> ${escapeHtml(center.generic_name_val || "—")}</p>
          <p><strong>${t("Nombre específico")}:</strong> ${escapeHtml(center.specific_name || "—")}</p>

          <p class="stack-actions">
            ${center.maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(center.maps_url)}" target="_blank" rel="noopener noreferrer">${t("Ver mapa")}</a>` : ""}
            ${center.directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(center.directions_url)}" target="_blank" rel="noopener noreferrer">${t("Cómo llegar")}</a>` : ""}
          </p>
        </div>
      `;
    } catch (error) {
      titleEl.textContent = `${t("Centro")} ${centerCode}`;
      subtitleEl.textContent = t("No se pudo cargar la ficha");
      cardEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    }
  }

  loadCenter();
})();