(function () {
  const SELECTED_POSITION_KEY = "radar_docent_selected_difficult_coverage_position";

  const titleEl = document.getElementById("dc-candidates-title");
  const subtitleEl = document.getElementById("dc-candidates-subtitle");
  const locationStatusEl = document.getElementById("dc-candidates-location-status");
  const useMyLocationButton = document.getElementById("dc-candidates-use-my-location");
  const clearLocationButton = document.getElementById("dc-candidates-clear-location");
  const emptyEl = document.getElementById("dc-candidates-empty");
  const summaryEl = document.getElementById("dc-candidates-position-summary");
  const listSectionEl = document.getElementById("dc-candidates-list-section");
  const metaEl = document.getElementById("dc-candidates-meta");
  const tableBody = document.getElementById("dc-candidates-table-body");

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
        locationStatusEl.textContent = `Activa · distancia disponible (${userOrigin.lat.toFixed(4)}, ${userOrigin.lon.toFixed(4)})`;
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
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";

    try {
      await ensureUserLocation();
      await loadCandidates();
    } catch (error) {
      if (summaryEl) summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
    } finally {
      useMyLocationButton.disabled = false;
      updateLocationStatus();
    }
  });

  clearLocationButton?.addEventListener("click", async () => {
    clearStoredOrigin();
    await loadCandidates();
  });

  updateLocationStatus();
  loadCandidates();
})();
