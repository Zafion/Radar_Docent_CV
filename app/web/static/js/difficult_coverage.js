(function () {
  const localityInput = document.getElementById("dc-locality");
  const specialtyCodeInput = document.getElementById("dc-specialty-code");
  const selectedOnlyInput = document.getElementById("dc-selected-only");
  const orderInput = document.getElementById("dc-order");
  const latestOnlyInput = document.getElementById("dc-latest-only");
  const filtersForm = document.getElementById("dc-filters-form");

  const locationStatusEl = document.getElementById("dc-location-status");
  const useMyLocationButton = document.getElementById("dc-use-my-location");
  const clearLocationButton = document.getElementById("dc-clear-location");

  const resultsMetaEl = document.getElementById("dc-results-meta");
  const tableBody = document.getElementById("dc-table-body");

  const SELECTED_POSITION_KEY = "radar_docent_selected_difficult_coverage_position";

  const LOCATION_STORAGE_KEY = "radar_docent_user_origin";

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

  function selectPositionAndOpenCandidates(positionId) {
    if (!positionId) return;

    sessionStorage.setItem(
      SELECTED_POSITION_KEY,
      JSON.stringify({
        positionId: Number(positionId),
        selectedAt: new Date().toISOString(),
      })
    );

    window.location.href = "/resultado-dificil-cobertura";
  }

  function renderPositions(items, total) {
    resultsMetaEl.textContent = `${total} puestos encontrados`;

    if (!items.length) {
      tableBody.innerHTML = '<tr><td colspan="8" class="muted data-table__empty">No hay puestos para los filtros actuales.</td></tr>';
      return;
    }

    tableBody.innerHTML = items.map((item) => {
      const specialty = [item.specialty_code, item.specialty_name].filter(Boolean).join(" - ") || "—";

      return `
        <tr>
          <td data-label="Fecha">${escapeHtml(formatDate(item.document_date_iso))}</td>
          <td data-label="Especialidad">${escapeHtml(specialty)}</td>
          <td data-label="Centro">
            <strong>${escapeHtml(item.center_name || "—")}</strong>
            ${item.center_full_address ? `<br><span class="muted">${escapeHtml(item.center_full_address)}</span>` : ""}
            ${item.center_phone ? `<br><span class="muted">Tel: ${escapeHtml(item.center_phone)}</span>` : ""}
          </td>
          <td data-label="Localidad">${escapeHtml(item.locality || "—")}</td>
          <td data-label="Candidatos">${escapeHtml(item.candidate_count ?? "—")}</td>
          <td data-label="Seleccionados">${escapeHtml(item.selected_candidate_count ?? "—")}</td>
          <td data-label="Distancia">${escapeHtml(formatDistance(item.distance_km))}</td>
          <td data-label="Acciones" class="data-table__actions">
            <button class="button button--secondary button--xs" type="button" data-position-id="${escapeHtml(item.id)}">Candidatos</button>
            ${item.center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(item.center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>` : ""}
            ${item.center_maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>` : ""}
            ${item.center_directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>` : ""}
          </td>
        </tr>
      `;
    }).join("");

    tableBody.querySelectorAll("button[data-position-id]").forEach((button) => {
      button.addEventListener("click", () => selectPositionAndOpenCandidates(button.dataset.positionId));
    });
  }

  async function loadPositions() {
    try {
      const { orderBy, orderDir } = parseOrderValue();
      if (orderBy === "distance" && (userOrigin.lat === null || userOrigin.lon === null)) {
        resultsMetaEl.textContent = "Activa tu ubicación para ordenar por distancia.";
        tableBody.innerHTML = '<tr><td colspan="8" class="muted data-table__empty">Activa tu ubicación para ordenar por distancia.</td></tr>';
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
      tableBody.innerHTML = '<tr><td colspan="8" class="muted data-table__empty">No se pudo cargar el listado.</td></tr>';
    }
  }

  filtersForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadPositions();
  });

  useMyLocationButton?.addEventListener("click", async () => {
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";

    try {
      await ensureUserLocation();

      if (orderInput) {
        orderInput.value = "distance:asc";
      }

      resultsMetaEl.textContent = "Ubicación activada. Ordenando por distancia...";
      await loadPositions();
    } catch (error) {
      resultsMetaEl.textContent = error.message;
    } finally {
      useMyLocationButton.disabled = false;
      updateLocationStatus();
    }
  });

  clearLocationButton?.addEventListener("click", async () => {
    clearStoredOrigin();

    if (orderInput?.value?.startsWith("distance:")) {
      orderInput.value = "document_date:desc";
    }

    resultsMetaEl.textContent = "Ubicación borrada. Mostrando resultados sin distancia.";
    await loadPositions();
  });

  updateLocationStatus();
  loadPositions();
})();
