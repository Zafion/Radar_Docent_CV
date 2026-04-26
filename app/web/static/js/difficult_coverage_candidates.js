(function () {
  const SELECTED_POSITION_KEY = "radar_docent_selected_difficult_coverage_position";

  const titleEl = document.getElementById("dc-candidates-title");
  const subtitleEl = document.getElementById("dc-candidates-subtitle");
  const locationStatusEl = document.getElementById("dc-candidates-location-status");
  const useMyLocationButton = document.getElementById("dc-candidates-use-my-location");
  const emptyEl = document.getElementById("dc-candidates-empty");
  const summaryEl = document.getElementById("dc-candidates-position-summary");
  const listSectionEl = document.getElementById("dc-candidates-list-section");
  const metaEl = document.getElementById("dc-candidates-meta");
  const tableBody = document.getElementById("dc-candidates-table-body");

  let userOrigin = loadStoredOrigin();

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

  function getSelectedPositionId() {
    try {
      const raw = sessionStorage.getItem(SELECTED_POSITION_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      const positionId = Number(parsed.positionId);
      return Number.isFinite(positionId) && positionId > 0 ? positionId : null;
    } catch {
      return null;
    }
  }

  function showEmptyState() {
    emptyEl?.classList.remove("is-hidden");
    summaryEl?.classList.add("is-hidden");
    listSectionEl?.classList.add("is-hidden");
    if (titleEl) titleEl.textContent = "No hay puesto seleccionado";
    if (subtitleEl) subtitleEl.textContent = "Vuelve a difícil cobertura y pulsa “Candidatos” en un puesto.";
  }

  function showResultState() {
    emptyEl?.classList.add("is-hidden");
    summaryEl?.classList.remove("is-hidden");
    listSectionEl?.classList.remove("is-hidden");
  }

  function renderPosition(position, totalCandidates) {
    const specialty = [position.specialty_code, position.specialty_name].filter(Boolean).join(" - ") || "—";

    if (titleEl) titleEl.textContent = position.center_name || `Puesto ${position.id}`;
    if (subtitleEl) {
      subtitleEl.textContent = [
        formatDate(position.document_date_iso),
        position.locality,
        specialty,
      ].filter(Boolean).join(" · ");
    }

    summaryEl.innerHTML = `
      <div class="status-card">
        <div class="status-grid">
          <div class="status-grid__item"><span>Fecha</span><strong>${escapeHtml(formatDate(position.document_date_iso))}</strong></div>
          <div class="status-grid__item"><span>Especialidad</span><strong>${escapeHtml(specialty)}</strong></div>
          <div class="status-grid__item"><span>Centro</span><strong>${escapeHtml(position.center_name || "—")}</strong></div>
          <div class="status-grid__item"><span>Localidad</span><strong>${escapeHtml(position.locality || "—")}</strong></div>
          <div class="status-grid__item"><span>Puesto</span><strong>${escapeHtml(position.position_code || "—")}</strong></div>
          <div class="status-grid__item"><span>Candidatos</span><strong>${escapeHtml(totalCandidates ?? "—")}</strong></div>
        </div>

        <p class="stack-actions">
          ${position.center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(position.center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>` : ""}
        </p>
      </div>
    `;
  }

  function renderCandidates(items, total) {
    if (metaEl) metaEl.textContent = `${total} candidatos registrados`;

    if (!items.length) {
      tableBody.innerHTML = '<tr><td colspan="5" class="muted">No hay candidatos registrados.</td></tr>';
      return;
    }

    tableBody.innerHTML = items.map((row) => `
      <tr>
        <td>${escapeHtml(row.row_number ?? "—")}</td>
        <td>${escapeHtml(row.is_selected ? "Seleccionado" : "Participante")}</td>
        <td>${escapeHtml(row.full_name || "—")}</td>
        <td>${escapeHtml(row.petition_text || row.petition_number || "—")}</td>
        <td>${escapeHtml(row.assigned_position_code || "—")}</td>
      </tr>
    `).join("");
  }

  async function loadCandidates() {
    const positionId = getSelectedPositionId();
    if (!positionId) {
      showEmptyState();
      return;
    }

    showResultState();

    try {
      const params = appendOriginParams(new URLSearchParams({ limit: "1000" }));
      const data = await apiGet(`/api/difficult-coverage/positions/${encodeURIComponent(positionId)}/candidates?${params.toString()}`);
      renderPosition(data.position, data.total ?? data.items?.length ?? 0);
      renderCandidates(data.items || [], data.total ?? data.items?.length ?? 0);
    } catch (error) {
      if (summaryEl) summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      if (tableBody) tableBody.innerHTML = "";
      if (metaEl) metaEl.textContent = "No se pudo cargar el detalle.";
    }
  }

  useMyLocationButton?.addEventListener("click", async () => {
    const originalText = useMyLocationButton.textContent;
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";
    try {
      await ensureUserLocation();
      await loadCandidates();
    } catch (error) {
      if (summaryEl) summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    } finally {
      useMyLocationButton.disabled = false;
      useMyLocationButton.textContent = originalText;
    }
  });

  updateLocationStatus();
  loadCandidates();
})();
