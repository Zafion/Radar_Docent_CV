(function () {
  const config = window.AWARD_DETAIL_CONFIG || {};
  const awardResultId = config.awardResultId;

  const titleEl = document.getElementById("award-title");
  const subtitleEl = document.getElementById("award-subtitle");
  const summaryEl = document.getElementById("award-summary");
  const assignmentsEl = document.getElementById("award-assignments");
  const locationStatusEl = document.getElementById("award-location-status");
  const useMyLocationButton = document.getElementById("award-use-my-location");

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

  function renderSummary(award) {
    titleEl.textContent = award.person_display_name || `Adjudicación ${award.id}`;
    subtitleEl.textContent = `${formatDate(award.document_date_iso)} · ${award.status || "Sin estado"}`;

    const specialty = [award.specialty_code, award.specialty_name].filter(Boolean).join(" - ") || "—";
    const scope = award.list_scope || "—";

    summaryEl.innerHTML = `
      <div class="status-card">
        <div class="status-grid">
          <div class="status-grid__item"><span>Persona</span><strong>${escapeHtml(award.person_display_name || "—")}</strong></div>
          <div class="status-grid__item"><span>Estado</span><strong>${escapeHtml(award.status || "—")}</strong></div>
          <div class="status-grid__item"><span>Fecha</span><strong>${escapeHtml(formatDate(award.document_date_iso))}</strong></div>
          <div class="status-grid__item"><span>Ámbito</span><strong>${escapeHtml(scope)}</strong></div>
          <div class="status-grid__item"><span>Especialidad</span><strong>${escapeHtml(specialty)}</strong></div>
          <div class="status-grid__item"><span>Documento</span><strong>${escapeHtml(award.document_title || "—")}</strong></div>
        </div>
      </div>
    `;
  }

  function renderAssignments(assignments) {
    if (!assignments.length) {
      assignmentsEl.innerHTML = `
        <div class="content-card__header">
          <div>
            <h2>Asignaciones</h2>
            <p>No hay asignaciones registradas para esta adjudicación.</p>
          </div>
        </div>
      `;
      return;
    }

    const rows = assignments.map((item) => `
      <tr>
        <td>${escapeHtml(item.assignment_kind || "—")}</td>
        <td>
          <strong>${escapeHtml(item.center_name || "—")}</strong>
          ${item.center_full_address ? `<br><span class="muted">${escapeHtml(item.center_full_address)}</span>` : ""}
          ${item.center_phone ? `<br><span class="muted">Tel: ${escapeHtml(item.center_phone)}</span>` : ""}
        </td>
        <td>${escapeHtml(item.locality || "—")}</td>
        <td>${escapeHtml(item.position_code || "—")}</td>
        <td>${escapeHtml(formatDistance(item.distance_km))}</td>
        <td>
          ${item.center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(item.center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>` : ""}
          ${item.center_maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>` : ""}
          ${item.center_directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>` : ""}
        </td>
      </tr>
    `).join("");

    assignmentsEl.innerHTML = `
      <div class="content-card__header">
        <div>
          <h2>Asignaciones</h2>
          <p>Detalle del centro adjudicado y accesos rápidos.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>Tipo</th>
              <th>Centro</th>
              <th>Localidad</th>
              <th>Puesto</th>
              <th>Distancia</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  async function loadAward() {
    try {
      const params = appendOriginParams(new URLSearchParams());
      const qs = params.toString();
      const data = await apiGet(`/api/awards/${encodeURIComponent(awardResultId)}${qs ? `?${qs}` : ""}`);
      renderSummary(data.award);
      renderAssignments(data.assignments || []);
    } catch (error) {
      summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      assignmentsEl.innerHTML = "";
    }
  }

  useMyLocationButton?.addEventListener("click", async () => {
    const originalText = useMyLocationButton.textContent;
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";
    try {
      await ensureUserLocation();
      await loadAward();
    } catch (error) {
      summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    } finally {
      useMyLocationButton.disabled = false;
      useMyLocationButton.textContent = originalText;
    }
  });

  updateLocationStatus();
  loadAward();
})();