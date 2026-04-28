(function () {
  const selectedPerson = loadSelectedPerson();
  const normalizedName = selectedPerson.normalizedName;

  const titleEl = document.getElementById("person-title");
  const subtitleEl = document.getElementById("person-subtitle");
  const summaryEl = document.getElementById("person-profile-summary");
  const historyEl = document.getElementById("person-profile-history");
  const locationStatusEl = document.getElementById("person-location-status");
  const useMyLocationButton = document.getElementById("person-use-my-location");
  const clearLocationButton = document.getElementById("person-clear-location");

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

  function loadSelectedPerson() {
    try {
      const raw = sessionStorage.getItem("radar_docent_selected_person");
      if (!raw) return { normalizedName: null, displayName: null };
      const parsed = JSON.parse(raw);
      return {
        normalizedName: typeof parsed.normalizedName === "string" ? parsed.normalizedName : null,
        displayName: typeof parsed.displayName === "string" ? parsed.displayName : null,
      };
    } catch {
      return { normalizedName: null, displayName: null };
    }
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

  function buildStatusPillClass(resultKey) {
    switch (resultKey) {
      case "awarded":
      case "selected_difficult_coverage":
        return "status-pill status-pill--awarded";
      case "not_awarded":
        return "status-pill status-pill--not-awarded";
      case "deactivated":
      case "not_participated":
        return "status-pill status-pill--deactivated";
      case "participated_without_award":
        return "status-pill status-pill--participated";
      case "difficult_coverage_candidate":
        return "status-pill status-pill--candidate";
      default:
        return "status-pill status-pill--info";
    }
  }

  function fallbackUserView(profile) {
    const firstAward = profile.awards?.[0] ?? null;
    const firstAssignment = firstAward?.assignments?.[0] ?? null;

    if (firstAward?.status === "Adjudicat" && firstAssignment) {
      return {
        display_name: profile.person.display_name,
        current_result: "awarded",
        current_result_label: "Adjudicado",
        current_result_message: "Sí tienes una plaza adjudicada en los datos cargados.",
        latest_scope_label: firstAward.list_scope,
        latest_specialty_label: [firstAward.specialty_code, firstAward.specialty_name].filter(Boolean).join(" - "),
        latest_date: firstAward.document_date_iso,
        assigned_position: firstAssignment.position_code,
        assigned_center: firstAssignment.center_name,
        assigned_locality: firstAssignment.locality,
        recommended_action: "Consulta la resolución oficial y el centro adjudicado para los siguientes pasos administrativos."
      };
    }

    return {
      display_name: profile.person.display_name,
      current_result: "info",
      current_result_label: firstAward?.status || "Sin resumen",
      current_result_message: "Consulta el detalle histórico disponible en la tabla inferior.",
      latest_scope_label: firstAward?.list_scope || null,
      latest_specialty_label: [firstAward?.specialty_code, firstAward?.specialty_name].filter(Boolean).join(" - ") || null,
      latest_date: firstAward?.document_date_iso || null,
      assigned_position: null,
      assigned_center: null,
      assigned_locality: null,
      recommended_action: null,
    };
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

  function renderProfile(profile) {
    const userView = profile.user_view || fallbackUserView(profile);
    const latestAwardWithId = (profile.awards || []).find((award) => award?.id);

    titleEl.textContent = userView.display_name || profile.person.display_name || "Perfil por persona";

    const awardsCount = profile.awards?.length || 0;
    const difficultCount = profile.difficult_coverage?.length || 0;
    subtitleEl.textContent = `${awardsCount} adjudicaciones · ${difficultCount} registros de difícil cobertura`;

    summaryEl.innerHTML = `
      <div class="status-card">
        <span class="${buildStatusPillClass(userView.current_result)}">${escapeHtml(userView.current_result_label || "Estado")}</span>
        <div>
          <h3>${escapeHtml(userView.display_name || profile.person.display_name)}</h3>
          <p>${escapeHtml(userView.current_result_message || "")}</p>
        </div>
        <div class="status-grid">
          <div class="status-grid__item"><span>Última fecha</span><strong>${escapeHtml(formatDate(userView.latest_date))}</strong></div>
          <div class="status-grid__item"><span>Ámbito</span><strong>${escapeHtml(userView.latest_scope_label || "—")}</strong></div>
          <div class="status-grid__item"><span>Especialidad</span><strong>${escapeHtml(userView.latest_specialty_label || "—")}</strong></div>
          <div class="status-grid__item"><span>Centro / localidad</span><strong>${escapeHtml([userView.assigned_center, userView.assigned_locality].filter(Boolean).join(" · ") || "—")}</strong></div>
          <div class="status-grid__item"><span>Distancia</span><strong>${escapeHtml(formatDistance(userView.assigned_distance_km))}</strong></div>
        </div>
        ${userView.assigned_center_address ? `<p><strong>Dirección:</strong> ${escapeHtml(userView.assigned_center_address)}</p>` : ""}
        ${userView.assigned_center_phone ? `<p><strong>Teléfono:</strong> ${escapeHtml(userView.assigned_center_phone)}</p>` : ""}
        ${(userView.assigned_center_maps_url || userView.assigned_center_directions_url || userView.assigned_center_code) ? `
          <p class="stack-actions">
            ${userView.assigned_center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(userView.assigned_center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>` : ""}
            ${userView.assigned_center_maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(userView.assigned_center_maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>` : ""}
            ${userView.assigned_center_directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(userView.assigned_center_directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>` : ""}
          </p>
        ` : ""}
        ${latestAwardWithId ? `
          <p class="stack-actions">
            <a class="button button--ghost button--xs" href="/adjudicaciones/${encodeURIComponent(latestAwardWithId.id)}" target="_blank" rel="noopener noreferrer">
              Ver detalle de adjudicación
            </a>
          </p>
        ` : ""}
        ${userView.recommended_action ? `<p><strong>Siguiente paso orientativo:</strong> ${escapeHtml(userView.recommended_action)}</p>` : ""}
      </div>
    `;

    const awardsRows = (profile.awards || []).map((award) => {
      const firstAssignment = award.assignments?.[0] || null;
      return `
        <tr>
          <td>${escapeHtml(formatDate(award.document_date_iso))}</td>
          <td>${escapeHtml(award.status || "—")}</td>
          <td>${escapeHtml([award.specialty_code, award.specialty_name].filter(Boolean).join(" - ") || "—")}</td>
          <td>${escapeHtml(firstAssignment?.center_name || "—")}</td>
          <td>${escapeHtml(firstAssignment?.locality || "—")}</td>
          <td>${escapeHtml(firstAssignment?.position_code || "—")}</td>
          <td>
            <a class="button button--ghost button--xs" href="/adjudicaciones/${encodeURIComponent(award.id)}" target="_blank" rel="noopener noreferrer">
              Detalle
            </a>
          </td>
        </tr>
      `;
    }).join("") || '<tr><td colspan="7" class="muted">No hay adjudicaciones registradas.</td></tr>';

    const difficultRows = (profile.difficult_coverage || []).map((row) => `
      <tr>
        <td>${escapeHtml(formatDate(row.document_date_iso))}</td>
        <td>${escapeHtml(row.is_selected ? "Seleccionado" : "Participante")}</td>
        <td>${escapeHtml([row.specialty_code, row.specialty_name].filter(Boolean).join(" - ") || "—")}</td>
        <td>${escapeHtml(row.center_name || "—")}</td>
        <td>${escapeHtml(row.locality || "—")}</td>
        <td>${escapeHtml(row.assigned_position_code || row.position_code || "—")}</td>
      </tr>
    `).join("") || '<tr><td colspan="6" class="muted">No hay registros de difícil cobertura.</td></tr>';

    historyEl.innerHTML = `
      <div class="content-card section-space--sm">
        <div class="content-card__header">
          <div>
            <h2>Histórico de adjudicaciones</h2>
            <p>Detalle técnico de las adjudicaciones registradas.</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Estado</th>
                <th>Especialidad</th>
                <th>Centro</th>
                <th>Localidad</th>
                <th>Código puesto</th>
                <th>Acciones</th>
              </tr>
            </thead>
            <tbody>${awardsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="content-card section-space--sm">
        <div class="content-card__header">
          <div>
            <h2>Difícil cobertura</h2>
            <p>Participaciones y selecciones registradas.</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>Fecha</th>
                <th>Resultado</th>
                <th>Especialidad</th>
                <th>Centro</th>
                <th>Localidad</th>
                <th>Puesto</th>
              </tr>
            </thead>
            <tbody>${difficultRows}</tbody>
          </table>
        </div>
      </div>
    `;
  }

  async function loadProfile() {
    if (!normalizedName) {
      titleEl.textContent = "Selecciona una persona";
      subtitleEl.textContent = "Primero debes buscar y seleccionar una coincidencia";
      summaryEl.innerHTML = `
        <div class="status-card">
          <p>Esta página muestra la ficha de la persona seleccionada desde el buscador. No contiene el identificador en la URL.</p>
          <p class="stack-actions">
            <a class="button button--secondary" href="/valencia-docentes">Volver al buscador</a>
          </p>
        </div>
      `;
      historyEl.innerHTML = "";
      return;
    }

    try {
      const params = appendOriginParams(new URLSearchParams({ normalized_name: normalizedName }));
      const data = await apiGet(`/api/persons/profile?${params.toString()}`);
      renderProfile(data);
    } catch (error) {
      titleEl.textContent = "Perfil no disponible";
      subtitleEl.textContent = "No se pudo cargar la ficha";
      summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      historyEl.innerHTML = "";
    }
  }

  useMyLocationButton?.addEventListener("click", async () => {
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";

    try {
      await ensureUserLocation();
      await loadProfile();
    } catch (error) {
      summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      historyEl.innerHTML = "";
    } finally {
      useMyLocationButton.disabled = false;
      updateLocationStatus();
    }
  });

  clearLocationButton?.addEventListener("click", async () => {
    clearStoredOrigin();
    await loadProfile();
  });

  updateLocationStatus();
  loadProfile();
})();