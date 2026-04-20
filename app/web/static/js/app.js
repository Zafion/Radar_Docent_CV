(function () {
  const latestOffersDateEl = document.getElementById("latest-offers-date");
  const offersSection = document.getElementById("offers-section");
  const offersMetaEl = document.getElementById("offers-publication-meta");
  const offersTableBody = document.getElementById("offers-table-body");
  const loadLatestOffersButton = document.getElementById("load-latest-offers");

  const personSearchForm = document.getElementById("person-search-form");
  const personQueryInput = document.getElementById("person-query");
  const personSearchFeedback = document.getElementById("person-search-feedback");
  const personSearchResults = document.getElementById("person-search-results");
  const personProfileSection = document.getElementById("person-profile-section");
  const personProfileSummary = document.getElementById("person-profile-summary");
  const personProfileHistory = document.getElementById("person-profile-history");

  const locationStatusEl = document.getElementById("location-status");
  const useMyLocationButton = document.getElementById("use-my-location");

  let userOrigin = {
    lat: null,
    lon: null,
  };

  let latestOffersDate = null;

  function setFeedback(message, isError = false) {
    if (!personSearchFeedback) return;
    personSearchFeedback.textContent = message || "";
    personSearchFeedback.classList.toggle("is-error", Boolean(isError));
  }

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
          userOrigin.lat = position.coords.latitude;
          userOrigin.lon = position.coords.longitude;
          updateLocationStatus();
          resolve(userOrigin);
        },
        () => {
          reject(new Error("No se pudo obtener tu ubicación."));
        },
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

  async function loadLatestOffersMeta() {
    if (!latestOffersDateEl) return;

    latestOffersDateEl.textContent = "Cargando...";

    try {
      const data = await apiGet("/api/offered-positions?limit=1&order_by=document_date&order_dir=desc");
      const latestItem = data.items?.[0];
      latestOffersDate = latestItem?.document_date_iso || null;
      latestOffersDateEl.textContent = latestOffersDate ? formatDate(latestOffersDate) : "Sin datos";
    } catch (error) {
      latestOffersDateEl.textContent = "No disponible";
    }
  }

  function renderOffers(items, documentDate) {
    if (!offersSection || !offersTableBody || !offersMetaEl) return;

    offersSection.classList.remove("is-hidden");
    offersMetaEl.textContent = documentDate
      ? `Publicación más reciente detectada: ${formatDate(documentDate)}`
      : "No se ha podido determinar la fecha de publicación.";

    if (!items.length) {
            offersTableBody.innerHTML = '<tr><td colspan="8" class="muted">No hay plazas ofertadas disponibles para la última fecha publicada.</td></tr>';
      return;
    }

    offersTableBody.innerHTML = items.map((item) => {
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

  async function loadLatestOffers() {
    if (!loadLatestOffersButton) return;
    loadLatestOffersButton.disabled = true;
    loadLatestOffersButton.textContent = "Cargando...";

    try {
      if (!latestOffersDate) {
        const meta = await apiGet("/api/offered-positions?limit=1&order_by=document_date&order_dir=desc");
        latestOffersDate = meta.items?.[0]?.document_date_iso || null;
      }

      const params = appendOriginParams(
        new URLSearchParams({ limit: "50", order_by: "locality", order_dir: "asc" })
      );

      if (latestOffersDate) {
        params.set("document_date", latestOffersDate);
      }

      const data = await apiGet(`/api/offered-positions?${params.toString()}`);
      renderOffers(data.items || [], latestOffersDate);
      offersSection?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (error) {
      if (offersTableBody) {
        offersTableBody.innerHTML = `<tr><td colspan="8" class="muted">${escapeHtml(error.message)}</td></tr>`;
      }
      offersSection?.classList.remove("is-hidden");
    } finally {
      loadLatestOffersButton.disabled = false;
      loadLatestOffersButton.textContent = "Ver últimas plazas ofertadas";
    }
  }

  function renderSearchResults(items) {
    if (!personSearchResults) return;

    if (!items.length) {
      personSearchResults.innerHTML = '<div class="result-item"><div><h3>Sin coincidencias</h3><p>Prueba con menos términos o revisa el formato del nombre.</p></div></div>';
      return;
    }

    personSearchResults.innerHTML = items.map((item) => `
      <div class="result-item">
        <div>
          <h3>${escapeHtml(item.display_name)}</h3>
          <p>${escapeHtml(item.total_records)} registros · ${escapeHtml(item.total_awarded)} adjudicaciones · ${escapeHtml(item.total_difficult_positions)} difícil cobertura</p>
        </div>
        <button class="button button--secondary" type="button" data-normalized-name="${escapeHtml(item.normalized_name)}">Seleccionar</button>
      </div>
    `).join("");

    personSearchResults.querySelectorAll("button[data-normalized-name]").forEach((button) => {
      button.addEventListener("click", () => {
        loadPersonProfile(button.dataset.normalizedName);
      });
    });
  }

  function renderProfile(profile) {
    if (!personProfileSection || !personProfileSummary || !personProfileHistory) return;

    const userView = profile.user_view || fallbackUserView(profile);
    const latestAwardWithId = (profile.awards || []).find((award) => award?.id);

    personProfileSection.classList.remove("is-hidden");

    personProfileSummary.innerHTML = `
      <div class="status-card">
        <span class="${buildStatusPillClass(userView.current_result)}">${escapeHtml(userView.current_result_label || 'Estado')}</span>
        <div>
          <h3>${escapeHtml(userView.display_name || profile.person.display_name)}</h3>
          <p>${escapeHtml(userView.current_result_message || '')}</p>
        </div>
        <div class="status-grid">
          <div class="status-grid__item"><span>Última fecha</span><strong>${escapeHtml(formatDate(userView.latest_date))}</strong></div>
          <div class="status-grid__item"><span>Ámbito</span><strong>${escapeHtml(userView.latest_scope_label || '—')}</strong></div>
          <div class="status-grid__item"><span>Especialidad</span><strong>${escapeHtml(userView.latest_specialty_label || '—')}</strong></div>
          <div class="status-grid__item"><span>Centro / localidad</span><strong>${escapeHtml([userView.assigned_center, userView.assigned_locality].filter(Boolean).join(' · ') || '—')}</strong></div>
          <div class="status-grid__item"><span>Distancia</span><strong>${escapeHtml(formatDistance(userView.assigned_distance_km))}</strong></div>        </div>
                  ${userView.assigned_center_address ? `<p><strong>Dirección:</strong> ${escapeHtml(userView.assigned_center_address)}</p>` : ''}
        ${userView.assigned_center_phone ? `<p><strong>Teléfono:</strong> ${escapeHtml(userView.assigned_center_phone)}</p>` : ''}
        ${(userView.assigned_center_maps_url || userView.assigned_center_directions_url || userView.assigned_center_code) ? `
          <p class="stack-actions">
            ${userView.assigned_center_code ? `<a class="button button--ghost button--xs" href="/centros/${encodeURIComponent(userView.assigned_center_code)}" target="_blank" rel="noopener noreferrer">Centro</a>` : ''}
            ${userView.assigned_center_maps_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(userView.assigned_center_maps_url)}" target="_blank" rel="noopener noreferrer">Mapa</a>` : ''}
            ${userView.assigned_center_directions_url ? `<a class="button button--ghost button--xs" href="${escapeHtml(userView.assigned_center_directions_url)}" target="_blank" rel="noopener noreferrer">Ruta</a>` : ''}
          </p>
        ` : ''}
                ${latestAwardWithId ? `
          <p class="stack-actions">
            <a class="button button--ghost button--xs" href="/adjudicaciones/${encodeURIComponent(latestAwardWithId.id)}" target="_blank" rel="noopener noreferrer">
              Ver detalle de adjudicación
            </a>
          </p>
        ` : ''}
        ${userView.recommended_action ? `<p><strong>Siguiente paso orientativo:</strong> ${escapeHtml(userView.recommended_action)}</p>` : ''}
      </div>
    `;

    const awardsRows = (profile.awards || []).map((award) => {
      const firstAssignment = award.assignments?.[0] || null;
      return `
        <tr>
          <td>${escapeHtml(formatDate(award.document_date_iso))}</td>
          <td>${escapeHtml(award.status || '—')}</td>
          <td>${escapeHtml([award.specialty_code, award.specialty_name].filter(Boolean).join(' - ') || '—')}</td>
          <td>${escapeHtml(firstAssignment?.center_name || '—')}</td>
          <td>${escapeHtml(firstAssignment?.locality || '—')}</td>
          <td>${escapeHtml(firstAssignment?.position_code || '—')}</td>
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
        <td>${escapeHtml(row.is_selected ? 'Seleccionado' : 'Participante')}</td>
        <td>${escapeHtml([row.specialty_code, row.specialty_name].filter(Boolean).join(' - ') || '—')}</td>
        <td>${escapeHtml(row.center_name || '—')}</td>
        <td>${escapeHtml(row.locality || '—')}</td>
        <td>${escapeHtml(row.assigned_position_code || row.position_code || '—')}</td>
      </tr>
    `).join("") || '<tr><td colspan="7" class="muted">No hay registros de difícil cobertura.</td></tr>';

    personProfileHistory.innerHTML = `
      <div class="content-card section-space--sm">
        <div class="content-card__header">
          <div>
            <h3>Histórico de adjudicaciones</h3>
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
            <h3>Difícil cobertura</h3>
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

    personProfileSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  updateLocationStatus();

  async function loadPersonProfile(normalizedName) {
    if (!normalizedName) return;

    setFeedback("Cargando perfil...");
    try {
      const params = appendOriginParams(new URLSearchParams({ normalized_name: normalizedName }));
      const data = await apiGet(`/api/persons/profile?${params.toString()}`);
      setFeedback("");
      renderProfile(data);
    } catch (error) {
      setFeedback(error.message, true);
    }
  }

  async function handleSearchSubmit(event) {
    event.preventDefault();
    if (!personQueryInput) return;

    const query = personQueryInput.value.trim();
    if (query.length < 2) {
      setFeedback("Introduce al menos 2 caracteres.", true);
      return;
    }

    setFeedback("Buscando coincidencias...");
    personSearchResults.innerHTML = "";

    try {
      const data = await apiGet(`/api/search/persons?q=${encodeURIComponent(query)}&limit=10`);
      setFeedback(data.count ? "Selecciona una coincidencia para ver el detalle." : "");
      renderSearchResults(data.items || []);
    } catch (error) {
      setFeedback(error.message, true);
    }
  }

    useMyLocationButton?.addEventListener("click", async () => {
    const originalText = useMyLocationButton.textContent;
    useMyLocationButton.disabled = true;
    useMyLocationButton.textContent = "Obteniendo ubicación...";

    try {
      await ensureUserLocation();
      setFeedback("Ubicación activada correctamente.");
    } catch (error) {
      setFeedback(error.message, true);
    } finally {
      useMyLocationButton.disabled = false;
      useMyLocationButton.textContent = originalText;
    }
  });

  loadLatestOffersMeta();
  loadLatestOffersButton?.addEventListener("click", loadLatestOffers);
  personSearchForm?.addEventListener("submit", handleSearchSubmit);
})();
