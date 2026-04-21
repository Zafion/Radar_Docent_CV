(function () {
  const localityInput = document.getElementById("dc-locality");
  const specialtyCodeInput = document.getElementById("dc-specialty-code");
  const selectedOnlyInput = document.getElementById("dc-selected-only");
  const orderInput = document.getElementById("dc-order");
  const latestOnlyInput = document.getElementById("dc-latest-only");
  const filtersForm = document.getElementById("dc-filters-form");

  const locationStatusEl = document.getElementById("dc-location-status");
  const useMyLocationButton = document.getElementById("dc-use-my-location");

  const resultsMetaEl = document.getElementById("dc-results-meta");
  const tableBody = document.getElementById("dc-table-body");

  const candidatesSection = document.getElementById("dc-candidates-section");
  const candidatesMetaEl = document.getElementById("dc-candidates-meta");
  const positionSummaryEl = document.getElementById("dc-position-summary");
  const candidatesTableBody = document.getElementById("dc-candidates-table-body");

  let userOrigin = loadStoredOrigin();
  let latestPublicationDate = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatDate(dateIso) {
    if (!dateIso) return "Sin fecha";
    const [year, month, day] = dateIso.split("-");
    if (!year || !month || !day) return dateIso;
    return `${day}/${month}/${year}`;
  }

  function formatDistance(distanceKm) {
    if (distanceKm === null || distanceKm === undefined || Number.isNaN(Number(distanceKm))) {
      return (userOrigin.lat === null || userOrigin.lon === null)
        ? "Activa ubicación"
        : "—";
    }
    return `${Number(distanceKm).toFixed(2)} km`;
  }

  function loadStoredOrigin() {
    try {
      const raw = localStorage.getItem("radar_docent_user_origin");
      if (!raw) return { lat: null, lon: null };
      const parsed = JSON.parse(raw);
      return {
        lat: typeof parsed.lat === "number" ? parsed.lat : null,
        lon: typeof parsed.lon === "number" ? parsed.lon : null,
      };
    } catch {
      return { lat: null, lon: null };
    }
  }

  function saveStoredOrigin(origin) {
    localStorage.setItem("radar_docent_user_origin", JSON.stringify(origin));
  }

  function updateLocationStatus() {
    if (!locationStatusEl) return;
    if (userOrigin.lat !== null && userOrigin.lon !== null) {
      locationStatusEl.textContent = `Activa (${userOrigin.lat.toFixed(4)}, ${userOrigin.lon.toFixed(4)})`;
      return;
    }
    locationStatusEl.textContent = "No activada · sin distancia calculada";
  }

  async function ensureUserLocation() {
    if (!navigator.geolocation) {
      throw new Error("Tu navegador no permite geolocalización.");
    }

    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          userOrigin = {
            lat: position.coords.latitude,
            lon: position.coords.longitude,
          };
          saveStoredOrigin(userOrigin);
          updateLocationStatus();
          resolve(userOrigin);
        },
        () => reject(new Error("No se pudo obtener tu ubicación.")),
        {
          enableHighAccuracy: true,
          timeout: 10000,
          maximumAge: 300000,
        }
      );
    });
  }

  function appendOriginParams(params) {
    if (userOrigin.lat !== null && userOrigin.lon !== null) {
      params.set("origin_lat", String(userOrigin.lat));
      params.set("origin_lon", String(userOrigin.lon));
    }
    return params;
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

  async function ensureLatestPublicationDate() {
    if (latestPublicationDate) {
      return latestPublicationDate;
    }

    const data = await apiGet("/api/difficult-coverage/positions?limit=1&order_by=document_date&order_dir=desc");
    latestPublicationDate = data.items?.[0]?.document_date_iso || null;
    return latestPublicationDate;
  }

  function parseOrderValue() {
    const [orderBy, orderDir] = (orderInput?.value || "document_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function renderPositions(items, total) {
    resultsMetaEl.textContent = `${total} puestos encontrados`;

    if (!items.length) {
      tableBody.innerHTML = '<tr><td colspan="8" class="muted">No hay puestos para los filtros actuales.</td></tr>';
      return;
    }

    tableBody.innerHTML = items.map((item) => {
      const specialty = [item.specialty_code, item.specialty_name].filter(Boolean).join(" - ") || "—";

      return `
        <tr>
          <td>${escapeHtml(formatDate(item.document_date_iso))}</td>
          <td>${escapeHtml(specialty)}</td>
          <td>
            <strong>${escapeHtml(item.center_name || "—")}</strong>
            ${item.center_full_address ? `<br><span class="muted">${escapeHtml(item.center_full_address)}</span>` : ""}
            ${item.center_phone ? `<br><span class="muted">Tel: ${escapeHtml(item.center_phone)}</span>` : ""}
          </td>
          <td>${escapeHtml(item.locality || "—")}</td>
          <td>${escapeHtml(item.candidate_count ?? "—")}</td>
          <td>${escapeHtml(item.selected_candidate_count ?? "—")}</td>
          <td>${escapeHtml(formatDistance(item.distance_km))}</td>
          <td>
            <button class="button button--secondary button--xs" type="button" data-position-id="${escapeHtml(item.id)}">Candidatos</button>
            ${item.center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(item.center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>` : ""}
            ${item.center_maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>` : ""}
            ${item.center_directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>` : ""}
          </td>
        </tr>
      `;
    }).join("");

    tableBody.querySelectorAll("button[data-position-id]").forEach((button) => {
      button.addEventListener("click", () => loadCandidates(button.dataset.positionId));
    });
  }

  function renderCandidates(position, items) {
    candidatesSection.classList.remove("is-hidden");

    const specialty = [position.specialty_code, position.specialty_name].filter(Boolean).join(" - ") || "—";
    candidatesMetaEl.textContent = `${position.center_name || "Centro"} · ${position.locality || "—"}`;

    positionSummaryEl.innerHTML = `
      <div class="status-card">
        <div class="status-grid">
          <div class="status-grid__item"><span>Fecha</span><strong>${escapeHtml(formatDate(position.document_date_iso))}</strong></div>
          <div class="status-grid__item"><span>Especialidad</span><strong>${escapeHtml(specialty)}</strong></div>
          <div class="status-grid__item"><span>Centro</span><strong>${escapeHtml(position.center_name || "—")}</strong></div>
          <div class="status-grid__item"><span>Distancia</span><strong>${escapeHtml(formatDistance(position.distance_km))}</strong></div>
        </div>
        ${position.center_full_address ? `<p><strong>Dirección:</strong> ${escapeHtml(position.center_full_address)}</p>` : ""}
        ${position.center_phone ? `<p><strong>Teléfono:</strong> ${escapeHtml(position.center_phone)}</p>` : ""}
      </div>
    `;

    if (!items.length) {
      candidatesTableBody.innerHTML = '<tr><td colspan="5" class="muted">No hay candidatos registrados.</td></tr>';
      return;
    }

    candidatesTableBody.innerHTML = items.map((row) => `
      <tr>
        <td>${escapeHtml(row.row_number ?? "—")}</td>
        <td>${escapeHtml(row.is_selected ? "Seleccionado" : "Participante")}</td>
        <td>${escapeHtml(row.full_name || "—")}</td>
        <td>${escapeHtml(row.petition_text || row.petition_number || "—")}</td>
        <td>${escapeHtml(row.assigned_position_code || "—")}</td>
      </tr>
    `).join("");

    candidatesSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function loadPositions() {
    try {
      const { orderBy, orderDir } = parseOrderValue();
      if (orderBy === "distance" && (userOrigin.lat === null || userOrigin.lon === null)) {
        resultsMetaEl.textContent = "Activa tu ubicación para ordenar por distancia.";
        tableBody.innerHTML = '<tr><td colspan="8" class="muted">Activa tu ubicación para ordenar por distancia.</td></tr>';
        return;
      }

      const params = appendOriginParams(new URLSearchParams({ limit: "50", order_by: orderBy, order_dir: orderDir }));

      if (latestOnlyInput?.checked) {
        const latestDate = await ensureLatestPublicationDate();
        if (latestDate) {
          params.set("document_date", latestDate);
        }
      }

      if (localityInput.value.trim()) params.set("locality", localityInput.value.trim());
      if (specialtyCodeInput.value.trim()) params.set("specialty_code", specialtyCodeInput.value.trim());
      if (selectedOnlyInput.value) params.set("selected_only", selectedOnlyInput.value);

      const data = await apiGet(`/api/difficult-coverage/positions?${params.toString()}`);
      renderPositions(data.items || [], data.total || 0);
    } catch (error) {
      resultsMetaEl.textContent = error.message;
      tableBody.innerHTML = '<tr><td colspan="8" class="muted">No se pudo cargar el listado.</td></tr>';
    }
  }

  async function loadCandidates(positionId) {
    try {
      const params = appendOriginParams(new URLSearchParams());
      const qs = params.toString();
      const data = await apiGet(`/api/difficult-coverage/positions/${encodeURIComponent(positionId)}/candidates${qs ? `?${qs}` : ""}`);
      renderCandidates(data.position, data.items || []);
    } catch (error) {
      candidatesSection.classList.remove("is-hidden");
      candidatesMetaEl.textContent = error.message;
      positionSummaryEl.innerHTML = "";
      candidatesTableBody.innerHTML = "";
    }
  }

  filtersForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadPositions();
  });

  useMyLocationButton?.addEventListener("click", async () => {
    const originalText = useMyLocationButton.textContent;
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";
    try {
      await ensureUserLocation();
      await loadPositions();
    } catch (error) {
      resultsMetaEl.textContent = error.message;
    } finally {
      useMyLocationButton.disabled = false;
      useMyLocationButton.textContent = originalText;
    }
  });

  updateLocationStatus();
  loadPositions();
})();
