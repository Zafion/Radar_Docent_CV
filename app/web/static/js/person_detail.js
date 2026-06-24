(function () {
  function t(value) {
    return window.FunkI18n ? window.FunkI18n.t(value) : value;
  }

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
    if (!dateIso) return t("Sin fecha");
    const [year, month, day] = dateIso.split("-");
    if (!year || !month || !day) return dateIso;
    return `${day}/${month}/${year}`;
  }

  function formatDistance(distanceKm) {
    if (distanceKm === null || distanceKm === undefined || Number.isNaN(Number(distanceKm))) {
      return (userOrigin.lat === null || userOrigin.lon === null)
        ? t("Activa ubicación")
        : "—";
    }
    return `${Number(distanceKm).toFixed(2)} km`;
  }

  function formatList(values) {
    if (!Array.isArray(values) || !values.length) return "—";
    return values.filter(Boolean).join(", ") || "—";
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
    return hasUserOrigin() ? t("Actualizar ubicación") : t("Usar mi ubicación");
  }

  function updateLocationButtonState() {
    if (useMyLocationButton) {
      if (!navigator.geolocation) {
        useMyLocationButton.textContent = t("Ubicación no disponible");
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
        locationStatusEl.textContent = t("Activa · distancia disponible");
      } else {
        locationStatusEl.textContent = t("No activada · sin distancia calculada");
      }
    }

    updateLocationButtonState();
  }

  function geolocationErrorMessage(error) {
    if (!error) return t("No se pudo obtener tu ubicación.");
    if (error.code === 1) return t("Permiso de ubicación denegado. Revisa los permisos del navegador para funkcionario.com.");
    if (error.code === 2) return t("No se pudo determinar la ubicación del dispositivo.");
    if (error.code === 3) return t("La ubicación ha tardado demasiado. Prueba de nuevo.");
    return error.message || t("No se pudo obtener tu ubicación.");
  }

  async function ensureUserLocation() {
    if (!navigator.geolocation) {
      throw new Error(t("Tu navegador no permite geolocalización."));
    }

    return new Promise((resolve, reject) => {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const lat = Number(position.coords.latitude);
          const lon = Number(position.coords.longitude);

          if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
            reject(new Error(t("El navegador devolvió una ubicación no válida.")));
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
      case "docent_bag_member":
        return "status-pill status-pill--candidate";
      default:
        return "status-pill status-pill--info";
    }
  }

  function fallbackUserView(profile) {
    const firstAward = profile.awards?.[0] ?? null;
    const firstAssignment = firstAward?.assignments?.[0] ?? null;
    const firstBag = profile.bag_memberships?.[0] ?? null;

    if (firstAward?.status === "Adjudicat" && firstAssignment) {
      return {
        display_name: profile.person.display_name,
        current_result: "awarded",
        current_result_label: t("Adjudicado"),
        current_result_message: t("Sí tienes una plaza adjudicada en los datos cargados."),
        latest_scope_label: firstAward.list_scope,
        latest_specialty_label: [firstAward.specialty_code, firstAward.specialty_name].filter(Boolean).join(" - "),
        latest_date: firstAward.document_date_iso,
        assigned_position: firstAssignment.position_code,
        assigned_center: firstAssignment.center_name,
        assigned_locality: firstAssignment.locality,
        recommended_action: t("Consulta la resolución oficial y el centro adjudicado para los siguientes pasos administrativos.")
      };
    }

    if (firstBag) {
      return {
        display_name: profile.person.display_name,
        current_result: "docent_bag_member",
        current_result_label: t("Consta en bolsa docente"),
        current_result_message: t("Figura en un listado de bolsa o participantes de inicio de curso cargado en la web."),
        latest_scope_label: firstBag.position_scope === "general" ? t("Bolsa general") : t("Bolsa por especialidad"),
        latest_specialty_label: [firstBag.specialty_code, firstBag.specialty_name].filter(Boolean).join(" - ") || firstBag.list_scope || null,
        latest_date: firstBag.document_date_iso || null,
        assigned_position: null,
        assigned_center: null,
        assigned_locality: null,
        recommended_action: t("Revisa futuras publicaciones definitivas o adjudicaciones."),
        bag_position: firstBag.order_number,
        bag_course_year: firstBag.course_year,
        bag_list_stage: firstBag.list_stage,
        bag_service_status: firstBag.service_status || firstBag.collective || null,
        bag_habilitations: firstBag.habilitations || null,
      };
    }

    return {
      display_name: profile.person.display_name,
      current_result: "info",
      current_result_label: firstAward?.status || t("Sin resumen"),
      current_result_message: t("Consulta el detalle histórico disponible en la tabla inferior."),
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

    titleEl.textContent = userView.display_name || profile.person.display_name || t("Perfil por persona");

    const awardsCount = profile.awards?.length || 0;
    const difficultCount = profile.difficult_coverage?.length || 0;
    const bagCount = profile.bag_memberships?.length || 0;
    subtitleEl.textContent = `${awardsCount} ${t("adjudicaciones")} · ${difficultCount} ${t("registros de difícil cobertura")} · ${bagCount} ${t("registros de bolsa")}`;

    summaryEl.innerHTML = `
      <div class="status-card">
        <span class="${buildStatusPillClass(userView.current_result)}">${escapeHtml(userView.current_result_label || t("Estado"))}</span>
        <div>
          <h3>${escapeHtml(userView.display_name || profile.person.display_name)}</h3>
          <p>${escapeHtml(userView.current_result_message || "")}</p>
        </div>
        <div class="status-grid">
          <div class="status-grid__item"><span>${t("Última fecha")}</span><strong>${escapeHtml(formatDate(userView.latest_date))}</strong></div>
          <div class="status-grid__item"><span>${t("Ámbito")}</span><strong>${escapeHtml(userView.latest_scope_label || "—")}</strong></div>
          <div class="status-grid__item"><span>${t("Especialidad")}</span><strong>${escapeHtml(userView.latest_specialty_label || "—")}</strong></div>
          <div class="status-grid__item"><span>${t("Centro / localidad")}</span><strong>${escapeHtml([userView.assigned_center, userView.assigned_locality].filter(Boolean).join(" · ") || "—")}</strong></div>
          <div class="status-grid__item"><span>${t("Distancia")}</span><strong>${escapeHtml(formatDistance(userView.assigned_distance_km))}</strong></div>
          ${userView.bag_position ? `<div class="status-grid__item"><span>${t("Posición en bolsa")}</span><strong>${escapeHtml(userView.bag_position)}</strong></div>` : ""}
          ${userView.bag_course_year ? `<div class="status-grid__item"><span>${t("Curso")}</span><strong>${escapeHtml(userView.bag_course_year)}</strong></div>` : ""}
          ${userView.bag_list_stage ? `<div class="status-grid__item"><span>${t("Listado")}</span><strong>${escapeHtml(userView.bag_list_stage)}</strong></div>` : ""}
          ${userView.bag_service_status ? `<div class="status-grid__item"><span>${t("Estado bolsa")}</span><strong>${escapeHtml(userView.bag_service_status)}</strong></div>` : ""}
          ${Array.isArray(userView.bag_habilitations) && userView.bag_habilitations.length ? `<div class="status-grid__item"><span>${t("Habilitaciones")}</span><strong>${escapeHtml(formatList(userView.bag_habilitations))}</strong></div>` : ""}
        </div>
        ${userView.assigned_center_address ? `<p><strong>${t("Dirección")}:</strong> ${escapeHtml(userView.assigned_center_address)}</p>` : ""}
        ${userView.assigned_center_phone ? `<p><strong>${t("Teléfono")}:</strong> ${escapeHtml(userView.assigned_center_phone)}</p>` : ""}
        ${(userView.assigned_center_maps_url || userView.assigned_center_directions_url || userView.assigned_center_code) ? `
          <p class="stack-actions">
            ${userView.assigned_center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(userView.assigned_center_code)}" target="_blank" rel="noopener noreferrer">${t("Centro")}</a>` : ""}
            ${userView.assigned_center_maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(userView.assigned_center_maps_url)}" target="_blank" rel="noopener noreferrer">${t("Mapa")}</a>` : ""}
            ${userView.assigned_center_directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(userView.assigned_center_directions_url)}" target="_blank" rel="noopener noreferrer">${t("Ruta")}</a>` : ""}
          </p>
        ` : ""}
        ${latestAwardWithId ? `
          <p class="stack-actions">
            <a class="button button--ghost button--xs" href="/adjudicaciones/${encodeURIComponent(latestAwardWithId.id)}" target="_blank" rel="noopener noreferrer">
              ${t("Ver detalle de adjudicación")}
            </a>
          </p>
        ` : ""}
        ${userView.recommended_action ? `<p><strong>${t("Siguiente paso orientativo")}:</strong> ${escapeHtml(userView.recommended_action)}</p>` : ""}
      </div>
    `;

    const bagRows = (profile.bag_memberships || []).map((row) => `
      <tr>
        <td data-label="${t("Fecha")}">${escapeHtml(formatDate(row.document_date_iso))}</td>
        <td data-label="${t("Curso")}">${escapeHtml(row.course_year || "—")}</td>
        <td data-label="${t("Listado")}">${escapeHtml(row.list_stage || "—")}</td>
        <td data-label="${t("Ámbito")}">${escapeHtml(row.position_scope === "general" ? t("General") : t("Por especialidad"))}</td>
        <td data-label="${t("Cuerpo")}">${escapeHtml(row.body_name || "—")}</td>
        <td data-label="${t("Especialidad")}">${escapeHtml([row.specialty_code, row.specialty_name].filter(Boolean).join(" - ") || "—")}</td>
        <td data-label="${t("Posición")}">${escapeHtml(row.order_number || "—")}</td>
        <td data-label="${t("Estado")}">${escapeHtml(row.service_status || row.collective || "—")}</td>
        <td data-label="${t("Habilitaciones")}">${escapeHtml(formatList(row.habilitations))}${row.disabled_habilitation ? ` · ${t("habilitación desactivada")}` : ""}</td>
      </tr>
    `).join("") || `<tr><td colspan="9" class="muted data-table__empty">${t("No hay registros de bolsa docente.")}</td></tr>`;

    const awardsRows = (profile.awards || []).map((award) => {
      const firstAssignment = award.assignments?.[0] || null;
      return `
        <tr>
          <td data-label="${t("Fecha")}">${escapeHtml(formatDate(award.document_date_iso))}</td>
          <td data-label="${t("Estado")}">${escapeHtml(award.status || "—")}</td>
          <td data-label="${t("Especialidad")}">${escapeHtml([award.specialty_code, award.specialty_name].filter(Boolean).join(" - ") || "—")}</td>
          <td data-label="${t("Centro")}">${escapeHtml(firstAssignment?.center_name || "—")}</td>
          <td data-label="${t("Localidad")}">${escapeHtml(firstAssignment?.locality || "—")}</td>
          <td data-label="${t("Código puesto")}">${escapeHtml(firstAssignment?.position_code || "—")}</td>
          <td data-label="${t("Acciones")}" class="data-table__actions">
            <a class="button button--ghost button--xs" href="/adjudicaciones/${encodeURIComponent(award.id)}" target="_blank" rel="noopener noreferrer">
              ${t("Detalle")}
            </a>
          </td>
        </tr>
      `;
    }).join("") || `<tr><td colspan="7" class="muted data-table__empty">${t("No hay adjudicaciones registradas.")}</td></tr>`;

    const difficultRows = (profile.difficult_coverage || []).map((row) => `
      <tr>
        <td data-label="${t("Fecha")}">${escapeHtml(formatDate(row.document_date_iso))}</td>
        <td data-label="${t("Resultado")}">${escapeHtml(row.is_selected ? t("Seleccionado") : t("Participante"))}</td>
        <td data-label="${t("Especialidad")}">${escapeHtml([row.specialty_code, row.specialty_name].filter(Boolean).join(" - ") || "—")}</td>
        <td data-label="${t("Centro")}">${escapeHtml(row.center_name || "—")}</td>
        <td data-label="${t("Localidad")}">${escapeHtml(row.locality || "—")}</td>
        <td data-label="${t("Puesto")}">${escapeHtml(row.assigned_position_code || row.position_code || "—")}</td>
      </tr>
    `).join("") || `<tr><td colspan="6" class="muted data-table__empty">${t("No hay registros de difícil cobertura.")}</td></tr>`;

    historyEl.innerHTML = `
      <div class="content-card section-space--sm">
        <div class="content-card__header">
          <div>
            <h2>${t("Bolsa docente")}</h2>
            <p>${t("Posiciones detectadas en listados de bolsa o participantes de inicio de curso.")}</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table data-table--cards">
            <thead>
              <tr>
                <th>${t("Fecha")}</th>
                <th>${t("Curso")}</th>
                <th>${t("Listado")}</th>
                <th>${t("Ámbito")}</th>
                <th>${t("Cuerpo")}</th>
                <th>${t("Especialidad")}</th>
                <th>${t("Posición")}</th>
                <th>${t("Estado")}</th>
                <th>${t("Habilitaciones")}</th>
              </tr>
            </thead>
            <tbody>${bagRows}</tbody>
          </table>
        </div>
      </div>

      <div class="content-card section-space--sm">
        <div class="content-card__header">
          <div>
            <h2>${t("Histórico de adjudicaciones")}</h2>
            <p>${t("Detalle técnico de las adjudicaciones registradas.")}</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table data-table--cards">
            <thead>
              <tr>
                <th>${t("Fecha")}</th>
                <th>${t("Estado")}</th>
                <th>${t("Especialidad")}</th>
                <th>${t("Centro")}</th>
                <th>${t("Localidad")}</th>
                <th>${t("Código puesto")}</th>
                <th>${t("Acciones")}</th>
              </tr>
            </thead>
            <tbody>${awardsRows}</tbody>
          </table>
        </div>
      </div>

      <div class="content-card section-space--sm">
        <div class="content-card__header">
          <div>
            <h2>${t("Difícil cobertura")}</h2>
            <p>${t("Participaciones y selecciones registradas.")}</p>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table data-table--cards">
            <thead>
              <tr>
                <th>${t("Fecha")}</th>
                <th>${t("Resultado")}</th>
                <th>${t("Especialidad")}</th>
                <th>${t("Centro")}</th>
                <th>${t("Localidad")}</th>
                <th>${t("Puesto")}</th>
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
      titleEl.textContent = t("Selecciona una persona");
      subtitleEl.textContent = t("Primero debes buscar y seleccionar una coincidencia");
      summaryEl.innerHTML = `
        <div class="status-card">
          <p>${t("Esta página muestra la ficha de la persona seleccionada desde el buscador. No contiene el identificador en la URL.")}</p>
          <p class="stack-actions">
            <a class="button button--secondary" href="/valencia-docentes">${t("Volver al buscador")}</a>
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
      titleEl.textContent = t("Perfil no disponible");
      subtitleEl.textContent = t("No se pudo cargar la ficha");
      summaryEl.innerHTML = `<p>${escapeHtml(error.message)}</p>`;
      historyEl.innerHTML = "";
    }
  }

  useMyLocationButton?.addEventListener("click", async () => {
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = t("Obteniendo ubicación...");

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