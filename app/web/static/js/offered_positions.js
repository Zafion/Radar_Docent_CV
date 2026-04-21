(function () {
  const latestDateEl = document.getElementById("op-latest-date");
  const localityInput = document.getElementById("op-locality");
  const specialtyCodeInput = document.getElementById("op-specialty-code");
  const positionTypeInput = document.getElementById("op-position-type");
  const orderInput = document.getElementById("op-order");
  const latestOnlyInput = document.getElementById("op-latest-only");
  const onlyUnmatchedInput = document.getElementById("op-only-unmatched");
  const filtersForm = document.getElementById("op-filters-form");

  const locationStatusEl = document.getElementById("op-location-status");
  const useMyLocationButton = document.getElementById("op-use-my-location");

  const resultsMetaEl = document.getElementById("op-results-meta");
  const tableBody = document.getElementById("op-table-body");

  let latestOffersDate = null;
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

  function parseOrderValue() {
    const [orderBy, orderDir] = (orderInput?.value || "document_date:desc").split(":");
    return { orderBy, orderDir };
  }

  function buildCenterActions(item) {
    const actions = [];

    if (item.center_code) {
      actions.push(
        `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(item.center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>`
      );
    }
    if (item.center_maps_url) {
      actions.push(
        `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>`
      );
    }
    if (item.center_directions_url) {
      actions.push(
        `<a class="button button--ghost button--xs" href="${escapeHtml(item.center_directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>`
      );
    }

    return actions.join(" ");
  }

  async function loadLatestOffersMeta() {
    if (!latestDateEl) return;
    latestDateEl.textContent = "Cargando...";

    try {
      const data = await apiGet("/api/offered-positions?limit=1&order_by=document_date&order_dir=desc");
      latestOffersDate = data.items?.[0]?.document_date_iso || null;
      latestDateEl.textContent = latestOffersDate ? formatDate(latestOffersDate) : "Sin datos";
    } catch (error) {
      latestDateEl.textContent = "No disponible";
      resultsMetaEl.textContent = error.message;
    }
  }

  function renderPositions(items, total) {
    const latestLabel = latestOnlyInput.checked && latestOffersDate
      ? ` · última publicación ${formatDate(latestOffersDate)}`
      : "";
    resultsMetaEl.textContent = `${total} plazas encontradas${latestLabel}`;

    if (!items.length) {
      tableBody.innerHTML = '<tr><td colspan="8" class="muted">No hay plazas para los filtros actuales.</td></tr>';
      return;
    }

    tableBody.innerHTML = items.map((item) => {
      const specialty = [item.specialty_code, item.specialty_name].filter(Boolean).join(" - ") || "—";
      return `
        <tr>
          <td>${escapeHtml(formatDate(item.document_date_iso))}</td>
          <td>${escapeHtml(specialty)}</td>
          <td>${escapeHtml(item.position_type || "—")}</td>
          <td>
            <strong>${escapeHtml(item.center_name || "—")}</strong>
            ${item.center_full_address ? `<br><span class="muted">${escapeHtml(item.center_full_address)}</span>` : ""}
            ${item.center_phone ? `<br><span class="muted">Tel: ${escapeHtml(item.center_phone)}</span>` : ""}
          </td>
          <td>${escapeHtml(item.locality || "—")}</td>
          <td>${escapeHtml(item.position_code || "—")}</td>
          <td>${escapeHtml(formatDistance(item.distance_km))}</td>
          <td>${buildCenterActions(item)}</td>
        </tr>
      `;
    }).join("");
  }

  async function loadPositions() {
    try {
      const { orderBy, orderDir } = parseOrderValue();
      if (orderBy === "distance" && (userOrigin.lat === null || userOrigin.lon === null)) {
        resultsMetaEl.textContent = "Activa tu ubicación para ordenar por distancia.";
        tableBody.innerHTML = '<tr><td colspan="8" class="muted">Activa tu ubicación para ordenar por distancia.</td></tr>';
        return;
      }

      const params = appendOriginParams(new URLSearchParams({ limit: "100", order_by: orderBy, order_dir: orderDir }));

      if (latestOnlyInput.checked && latestOffersDate) params.set("document_date", latestOffersDate);
      if (localityInput.value.trim()) params.set("locality", localityInput.value.trim());
      if (specialtyCodeInput.value.trim()) params.set("specialty_code", specialtyCodeInput.value.trim());
      if (positionTypeInput.value) params.set("position_type", positionTypeInput.value);
      if (onlyUnmatchedInput.checked) params.set("only_unmatched", "true");

      const data = await apiGet(`/api/offered-positions?${params.toString()}`);
      renderPositions(data.items || [], data.total || 0);
    } catch (error) {
      resultsMetaEl.textContent = error.message;
      tableBody.innerHTML = '<tr><td colspan="8" class="muted">No se pudo cargar el listado.</td></tr>';
    }
  }

  filtersForm?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await loadPositions();
  });

  latestOnlyInput?.addEventListener("change", loadPositions);

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
  loadLatestOffersMeta().then(loadPositions);
})();
