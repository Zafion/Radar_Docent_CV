(function () {
  const config = window.AWARD_DETAIL_CONFIG || {};
  const awardResultId = config.awardResultId;

  const titleEl = document.getElementById("award-title");
  const subtitleEl = document.getElementById("award-subtitle");
  const summaryEl = document.getElementById("award-summary");
  const assignmentsEl = document.getElementById("award-assignments");
  const locationStatusEl = document.getElementById("award-location-status");
  const useMyLocationButton = document.getElementById("award-use-my-location");
  const clearLocationButton = document.getElementById("award-clear-location");

  const LOCATION_STORAGE_KEY = "radar_docent_user_origin";

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
      const raw = localStorage.getItem(LOCATION_STORAGE_KEY);
      if (!raw) return { lat: null, lon: null };

      const parsed = JSON.parse(raw);
      const lat = Number(parsed.lat);
      const lon = Number(parsed.lon);

      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        localStorage.removeItem(LOCATION_STORAGE_KEY);
        return { lat: null, lon: null };
      }

      return { lat, lon };
    } catch {
      return { lat: null, lon: null };
    }
  }

  function hasUserOrigin() {
    return Number.isFinite(userOrigin.lat) && Number.isFinite(userOrigin.lon);
  }

  function saveStoredOrigin(origin) {
    localStorage.setItem(LOCATION_STORAGE_KEY, JSON.stringify(origin));
  }

  function clearStoredOrigin() {
    try {
      localStorage.removeItem(LOCATION_STORAGE_KEY);
    } catch (_) {
      // No hay nada que hacer si el navegador impide modificar localStorage.
    }

    userOrigin = { lat: null, lon: null };
    updateLocationStatus();
  }

  function locationButtonText() {
    return hasUserOrigin() ? "Actualizar ubicación" : "Usar mi ubicación";
  }

  function updateLocationButtonState() {
    if (useMyLocationButton) {
      if (!navigator.geolocation) {
        useMyLocationButton.textContent = "Ubicación no disponible";
        useMyLocationButton.disabled = true;
      } else {
        useMyLocationButton.textContent = locationButtonText();
        useMyLocationButton.classList.toggle("is-active", hasUserOrigin());
      }
    }

    if (typeof clearLocationButton !== "undefined" && clearLocationButton) {
      clearLocationButton.disabled = !hasUserOrigin();
      clearLocationButton.classList.toggle("is-hidden", !hasUserOrigin());
    }
  }

  function updateLocationStatus() {
    if (locationStatusEl) {
      locationStatusEl.classList.toggle("location-status--active", hasUserOrigin());

      if (hasUserOrigin()) {
        locationStatusEl.textContent = "Activa · distancia disponible";
      } else {
        locationStatusEl.textContent = "No activada · sin distancia calculada";
      }
    }

    updateLocationButtonState();
  }

  function geolocationErrorMessage(error) {
    if (!error) return "No se pudo obtener tu ubicación.";
    if (error.code === 1) return "Permiso de ubicación denegado. Revisa los permisos del navegador para funkcionario.com.";
    if (error.code === 2) return "No se pudo determinar la ubicación del dispositivo.";
    if (error.code === 3) return "La ubicación ha tardado demasiado. Prueba de nuevo.";
    return error.message || "No se pudo obtener tu ubicación.";
  }

  async function ensureUserLocation() {
    if (!navigator.geolocation) {
      throw new Error("Tu navegador no permite geolocalización.");
    }

    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const lat = Number(position.coords.latitude);
          const lon = Number(position.coords.longitude);

          if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
            reject(new Error("El navegador devolvió una ubicación no válida."));
            return;
          }

          userOrigin = { lat, lon };
          saveStoredOrigin(userOrigin);
          updateLocationStatus();
          resolve(userOrigin);
        },
        (error) => reject(new Error(geolocationErrorMessage(error))),
        {
          enableHighAccuracy: false,
          timeout: 15000,
          maximumAge: 600000,
        }
      );
    });
  }

  function appendOriginParams(params) {
    if (hasUserOrigin()) {
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
        <td data-label="Tipo">${escapeHtml(item.assignment_kind || "—")}</td>
        <td data-label="Centro">
          <strong>${escapeHtml(item.center_name || "—")}</strong>
          ${item.center_full_address ? `<br><span class="muted">${escapeHtml(item.center_full_address)}</span>` : ""}
          ${item.center_phone ? `<br><span class="muted">Tel: ${escapeHtml(item.center_phone)}</span>` : ""}
        </td>
        <td data-label="Localidad">${escapeHtml(item.locality || "—")}</td>
        <td data-label="Puesto">${escapeHtml(item.position_code || "—")}</td>
        <td data-label="Distancia">${escapeHtml(formatDistance(item.distance_km))}</td>
        <td data-label="Acciones" class="data-table__actions">
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
        <table class="data-table data-table--cards">
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
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";

    try {
      await ensureUserLocation();
      await loadAward();
    } catch (error) {
      summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    } finally {
      useMyLocationButton.disabled = false;
      updateLocationStatus();
    }
  });

  clearLocationButton?.addEventListener("click", async () => {
    clearStoredOrigin();
    await loadAward();
  });

  updateLocationStatus();
  loadAward();
})();