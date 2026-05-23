(function () {
  const lang = document.documentElement.getAttribute("lang") || window.FUNKCIONARIO_LANG || "es";
  const isVa = lang.toLowerCase().startsWith("ca") || lang === "va";
  const excludedPrefixes = ["/api/", "/static/", "/feed.xml", "/feed.json", "/favicon.ico", "/sw.js", "/robots.txt", "/sitemap.xml", "/llms.txt"];

  const dictionary = {
    "Centro": "Centre",
    "Centros": "Centres",
    "Centro educativo": "Centre educatiu",
    "Centros educativos": "Centres educatius",
    "Ficha": "Fitxa",
    "Mapa": "Mapa",
    "Ruta": "Ruta",
    "Ver mapa": "Veure mapa",
    "Cómo llegar": "Com arribar",
    "Buscar centro": "Buscar centre",
    "Adjudicación": "Adjudicació",
    "Adjudicaciones": "Adjudicacions",
    "Plazas": "Places",
    "Plazas ofertadas": "Places oferides",
    "Puestos ofertados": "Llocs oferits",
    "Puestos de difícil cobertura": "Llocs de difícil cobertura",
    "Difícil cobertura": "Difícil cobertura",
    "Consulta por persona": "Consulta per persona",
    "Resultado por persona": "Resultat per persona",
    "Nueva búsqueda": "Nova cerca",
    "Fuente oficial": "Font oficial",
    "Ver publicación oficial": "Veure publicació oficial",
    "PDF oficial": "PDF oficial",
    "Publicación": "Publicació",
    "Publicaciones": "Publicacions",
    "Publicaciones oficiales": "Publicacions oficials",
    "Convocatoria ADC": "Convocatòria ADC",
    "Adjudicación ADC": "Adjudicació ADC",
    "Actualización de bolsa": "Actualització de borsa",
    "Bolsa Función Pública": "Borsa Funció Pública",
    "Sin fecha": "Sense data",
    "Sin datos": "Sense dades",
    "No disponible": "No disponible",
    "Cargando...": "Carregant...",
    "Cargando información...": "Carregant informació...",
    "Cargando detalle...": "Carregant detall...",
    "Cargando asignaciones...": "Carregant assignacions...",
    "Sin coincidencias": "Sense coincidències",
    "Prueba con menos términos o revisa el formato del nombre.": "Prova amb menys termes o revisa el format del nom.",
    "registros": "registres",
    "adjudicaciones": "adjudicacions",
    "difícil cobertura": "difícil cobertura",
    "Ver ficha": "Veure fitxa",
    "Introduce al menos 2 caracteres.": "Introdueix almenys 2 caràcters.",
    "Buscando coincidencias...": "Buscant coincidències...",
    "Abre la ficha completa de la coincidencia que te interese.": "Obri la fitxa completa de la coincidència que t'interesse.",
    "Ubicación": "Ubicació",
    "Usar mi ubicación": "Usar la meua ubicació",
    "Actualizar ubicación": "Actualitzar ubicació",
    "Borrar ubicación": "Esborrar ubicació",
    "Ubicación no disponible": "Ubicació no disponible",
    "No activada · sin distancia calculada": "No activada · sense distància calculada",
    "Activa · distancia disponible": "Activa · distància disponible",
    "Obteniendo ubicación...": "Obtenint ubicació...",
    "Ubicación activada correctamente. Se usará para calcular distancias en los listados.": "Ubicació activada correctament. S'usarà per a calcular distàncies en els llistats.",
    "Ubicación borrada. Ya no se calcularán distancias con tu posición.": "Ubicació esborrada. Ja no es calcularan distàncies amb la teua posició.",
    "No se pudo obtener tu ubicación.": "No s'ha pogut obtindre la teua ubicació.",
    "Permiso de ubicación denegado. Revisa los permisos del navegador para funkcionario.com.": "Permís d'ubicació denegat. Revisa els permisos del navegador per a funkcionario.com.",
    "No se pudo determinar la ubicación del dispositivo.": "No s'ha pogut determinar la ubicació del dispositiu.",
    "La ubicación ha tardado demasiado. Prueba de nuevo.": "La ubicació ha tardat massa. Torna-ho a provar.",
    "Tu navegador no permite geolocalización.": "El teu navegador no permet geolocalització.",
    "El navegador devolvió una ubicación no válida.": "El navegador ha retornat una ubicació no vàlida.",
    "Alertas no disponibles": "Alertes no disponibles",
    "Activar alertas de novedades": "Activar alertes de novetats",
    "Desactivar alertas de novedades": "Desactivar alertes de novetats",
    "Las alertas no están configuradas todavía.": "Les alertes encara no estan configurades.",
    "Desactivando alertas...": "Desactivant alertes...",
    "Alertas de novedades desactivadas.": "Alertes de novetats desactivades.",
    "Activando alertas...": "Activant alertes...",
    "Alertas de novedades activadas.": "Alertes de novetats activades.",
    "No se pudo cambiar el estado de las alertas.": "No s'ha pogut canviar l'estat de les alertes.",
    "Ir a búsqueda": "Anar a la cerca",
    "Volver al buscador": "Tornar al cercador",
    "No hay publicaciones.": "No hi ha publicacions.",
    "No hay colectivos cargados.": "No hi ha col·lectius carregats.",
    "colectivos con datos detectados": "col·lectius amb dades detectades",
    "Datos cargados desde publicaciones oficiales procesadas.": "Dades carregades des de publicacions oficials processades.",
    "No se pudo cargar el resumen:": "No s'ha pogut carregar el resum:",
    "No se pudo cargar el resumen.": "No s'ha pogut carregar el resum.",
    "Publicaciones no docentes detectadas más recientes.": "Publicacions no docents detectades més recents.",
    "Sin colectivo": "Sense col·lectiu",
    "Personas en bolsa": "Persones en borsa",
    "Última fecha": "Última data",
    "Colectivo": "Col·lectiu",
    "Bolsa": "Borsa",
    "Ver plazas ofertadas": "Veure places oferides",
    "Ver adjudicaciones": "Veure adjudicacions",
    "Ver publicaciones oficiales": "Veure publicacions oficials"
  };

  Object.assign(dictionary, {
    "Seguimiento útil para personal funcionario interino": "Seguiment útil per a personal funcionari interí",
    "Recibe novedades sin ceder email ni teléfono": "Rep novetats sense cedir correu electrònic ni telèfon",
    "Comunitat Valenciana · maestros y profesorado interino": "Comunitat Valenciana · mestres i professorat interí",
    "Información": "Informació",
    "Última actualización detectada:": "Última actualització detectada:",
    "Última publicación detectada:": "Última publicació detectada:",
    "Activa tu ubicación para calcular la distancia aproximada hasta cada centro y abrir rutas directas en Maps. La ubicación se guarda solo en tu navegador.": "Activa la teua ubicació per a calcular la distància aproximada fins a cada centre i obrir rutes directes en Maps. La ubicació es guarda només en el teu navegador.",
    "Visitar Conselleria": "Visitar Conselleria",
    "Política de Privacidad": "Política de Privacitat",
    "Política de Cookies": "Política de Cookies",
    "Orden": "Ordre",
    "Fecha más reciente": "Data més recent",
    "Fecha más antigua": "Data més antiga",
    "Todas": "Totes",
    "Todos": "Tots",
    "Todas las localidades": "Totes les localitats",
    "Todas las especialidades": "Totes les especialitats",
    "Todos los tipos": "Tots els tipus",
    "Tipo de puesto": "Tipus de lloc",
    "Solo última publicación": "Només última publicació",
    "Código": "Codi",
    "Acciones": "Accions",
    "Búsqueda": "Cerca",
    "Régimen": "Règim",
    "Centros encontrados": "Centres trobats",
    "Puestos disponibles, centros, distancia y candidatos registrados.": "Llocs disponibles, centres, distància i candidats registrats.",
    "Selección": "Selecció",
    "Con seleccionados": "Amb seleccionats",
    "Sin seleccionados": "Sense seleccionats",
    "Más candidatos": "Més candidats",
    "Menos candidatos": "Menys candidats",
    "Puestos encontrados": "Llocs trobats",
    "Candidatos": "Candidats",
    "Seleccionados": "Seleccionats",
    "Detalle de candidatos": "Detall de candidats",
    "Candidatos del puesto": "Candidats del lloc",
    "Volver a difícil cobertura": "Tornar a difícil cobertura",
    "No hay puesto seleccionado": "No hi ha cap lloc seleccionat",
    "Candidatos registrados": "Candidats registrats",
    "Fila": "Fila",
    "Nombre": "Nom",
    "Petición": "Petició",
    "Puesto asignado": "Lloc assignat",
    "Ficha de candidato": "Fitxa de candidat",
    "Perfil por persona": "Perfil per persona",
    "Ver adjudicaciones oficiales": "Veure adjudicacions oficials",
    "Ver difícil cobertura oficial": "Veure difícil cobertura oficial",
    "Cargando historial...": "Carregant historial...",
    "Cómo usar esta consulta": "Com usar aquesta consulta",
    "Volver a consulta principal": "Tornar a la consulta principal",
    "Resultados": "Resultats",
    "Puesto": "Lloc",
    "Puntuación": "Puntuació",
    "Puntuación mayor": "Puntuació major",
    "Registros de bolsa": "Registres de borsa",
    "Zona": "Zona",
    "Estado": "Estat",
    "Fuente": "Font",
    "Qué muestra": "Què mostra",
    "Desde": "Des de",
    "Hasta": "Fins a",
    "Título": "Títol",
    "Datos": "Dades",
    "Información legal": "Informació legal",
    "Cookies y almacenamiento técnico": "Cookies i emmagatzematge tècnic",
    "Feedback, sugerencias y necesidades de mejora": "Feedback, suggeriments i necessitats de millora",
    "Canales de contacto": "Canals de contacte",
    "Perfil profesional": "Perfil professional",
    "Formulario de contacto": "Formulari de contacte",
    "Asunto": "Assumpte",
    "Mensaje": "Missatge",
    "Preparar email": "Preparar email",
    "Información general del proyecto": "Informació general del projecte",
    "Últimas novedades detectadas": "Últimes novetats detectades",
    "Recibe avisos automáticos": "Rep avisos automàtics",
    "Activar alertas de navegador": "Activar alertes del navegador",
    "Histórico público": "Històric públic"
  });


  Object.assign(dictionary, {
    // Correcciones específicas QA valenciano
    "es un proyecto creado para facilitar a los funcionarios interinos sin plaza fija el seguimiento de sus posiciones, de las plazas ofertadas, de las posibles adjudicaciones y de la información relacionada con las mismas.": "és un projecte creat per a facilitar als funcionaris interins sense plaça fixa el seguiment de les seues posicions, de les places oferides, de les possibles adjudicacions i de la informació relacionada amb aquestes.",
    "En esta primera etapa, la plataforma está enfocada en maestros y profesorado de la Comunitat Valenciana, con una interfaz pensada para que consultar publicaciones oficiales sea más rápido, más claro y más útil para el usuario final.": "En aquesta primera etapa, la plataforma està enfocada en mestres i professorat de la Comunitat Valenciana, amb una interfície pensada perquè consultar publicacions oficials siga més ràpid, més clar i més útil per a l'usuari final.",
    "El objetivo a medio plazo es ampliar la cobertura a otros sectores y a otras comunidades autónomas, atendiendo a las necesidades reales del personal funcionario interino y priorizando siempre la consulta práctica de información relevante.": "L'objectiu a mitjà termini és ampliar la cobertura a altres sectors i a altres comunitats autònomes, atenent les necessitats reals del personal funcionari interí i prioritzant sempre la consulta pràctica d'informació rellevant.",
    "Responsable del proyecto": "Responsable del projecte",
    "Tu nombre o alias": "El teu nom o àlies",
    "Feedback, incidencia o propuesta de mejora": "Feedback, incidència o proposta de millora",
    "Cuéntanos tu sugerencia, problema o necesidad.": "Conta'ns el teu suggeriment, problema o necessitat.",
    "Completa todos los campos antes de continuar.": "Completa tots els camps abans de continuar.",
    "Email de contacto": "Email de contacte",
    "Mensaje:": "Missatge:",
    "Enviado desde el formulario de contacto de funkcionario.com": "Enviat des del formulari de contacte de funkcionario.com",
    "Se ha abierto tu aplicación de correo para enviar el mensaje.": "S'ha obert la teua aplicació de correu per a enviar el missatge.",
    "La web se ha diseñado para evitar la recogida de datos personales innecesarios. No necesitas crear una cuenta, indicar tu email ni facilitar tu teléfono para consultar plazas, adjudicaciones, avisos o publicaciones.": "La web s'ha dissenyat per a evitar la recollida de dades personals innecessàries. No necessites crear un compte, indicar el teu email ni facilitar el teu telèfon per a consultar places, adjudicacions, avisos o publicacions.",
    "En el caso de las alertas del navegador, se almacena la suscripción técnica generada por el propio navegador para poder enviar avisos generales de nuevas publicaciones. Esta suscripción no permite identificar directamente a una persona por nombre, email o teléfono.": "En el cas de les alertes del navegador, s'emmagatzema la subscripció tècnica generada pel mateix navegador per a poder enviar avisos generals de noves publicacions. Aquesta subscripció no permet identificar directament una persona per nom, email o telèfon.",
    "La geolocalización solo se utiliza si concedes permiso expresamente en tu navegador. Se usa para calcular distancias aproximadas y abrir rutas hacia centros educativos. Funkcionario.com no necesita una cuenta de usuario para esta función.": "La geolocalització només s'utilitza si concedeixes permís expressament en el teu navegador. S'usa per a calcular distàncies aproximades i obrir rutes cap a centres educatius. Funkcionario.com no necessita un compte d'usuari per a aquesta funció.",
    "El canal público de Telegram permite recibir avisos sin que Funkcionario.com tenga que almacenar tu email ni tu teléfono. La relación con Telegram queda sujeta a las condiciones y configuración de privacidad propias de Telegram.": "El canal públic de Telegram permet rebre avisos sense que Funkcionario.com haja d'emmagatzemar el teu email ni el teu telèfon. La relació amb Telegram queda subjecta a les condicions i configuració de privacitat pròpies de Telegram.",
    "Este sitio puede utilizar almacenamiento técnico del navegador y tecnologías similares para funciones estrictamente necesarias o solicitadas por el usuario, como recordar preferencias locales, gestionar alertas push o permitir el funcionamiento correcto de la aplicación web.": "Aquest lloc pot utilitzar emmagatzematge tècnic del navegador i tecnologies similars per a funcions estrictament necessàries o sol·licitades per l'usuari, com recordar preferències locals, gestionar alertes push o permetre el funcionament correcte de l'aplicació web.",
    "Para cualquier consulta relacionada con privacidad, cookies o tratamiento de datos técnicos, puedes contactar en": "Per a qualsevol consulta relacionada amb privacitat, cookies o tractament de dades tècniques, pots contactar en",
    "Nombre o apellidos": "Nom o cognoms",
    "Apellido1 Apellido2, Nombre": "Cognom1 Cognom2, Nom",
    "Ejemplo": "Exemple",
    "Ejemplo: García, López, Martínez...": "Exemple: García, López, Martínez...",
    "Ejemplo: Gómez Valencia, Cristina Lucia": "Exemple: Gómez Valencia, Cristina Lucia",
    "Mostrando hasta 20 coincidencias. Si no encuentras el resultado, afina la búsqueda con nombre y apellidos.": "Mostrant fins a 20 coincidències. Si no trobes el resultat, afina la cerca amb nom i cognoms.",
    "Introduce al menos dos caracteres.": "Introdueix almenys dos caràcters.",
    "Buscando...": "Buscant...",
    "Ver plazas": "Veure places",
    "plazas disponibles encontradas": "places disponibles trobades",
    "centros encontrados": "centres trobats",
    "adjudicaciones encontradas": "adjudicacions trobades",
    "publicaciones encontradas": "publicacions trobades",
    "puestos disponibles encontrados": "llocs disponibles trobats",
    "Mostrando": "Mostrant",
    "por página": "per pàgina",
    "Si no encuentras el resultado, afina la búsqueda": "Si no trobes el resultat, afina la cerca",
    "Siguiente": "Següent",
    "última publicación": "darrera publicació",
    "última fecha": "última data",
    "avisos registrados": "avisos registrats",
    "No hay plazas no docentes disponibles actualmente para los filtros seleccionados.": "No hi ha places no docents disponibles actualment per als filtres seleccionats.",
    "No hay centros para los filtros actuales.": "No hi ha centres per als filtres actuals.",
    "No se pudo cargar el listado de centros.": "No s'ha pogut carregar el llistat de centres.",
    "No se pudo cargar el listado.": "No s'ha pogut carregar el llistat.",
    "No hay plazas ofertadas disponibles actualmente para los filtros seleccionados.": "No hi ha places oferides disponibles actualment per als filtres seleccionats.",
    "No hay puestos de difícil cobertura disponibles actualmente.": "No hi ha llocs de difícil cobertura disponibles actualment.",
    "Activa ubicación": "Activa ubicació",
    "Activa tu ubicación para ordenar por distancia.": "Activa la teua ubicació per a ordenar per distància.",
    "Localidad": "Localitat",
    "Provincia": "Província",
    "Teléfono": "Telèfon",
    "Dirección": "Direcció",
    "Denominación genérica ES": "Denominació genèrica ÉS",
    "Denominación genérica VAL": "Denominació genèrica VAL",
    "Nombre específico": "Nom específic",
    "No se pudo cargar la ficha": "No s'ha pogut carregar la fitxa",
    "Avisos públicos automáticos": "Avisos públics automàtics",
    "Avisos recientes": "Avisos recents",
    "Unirme al canal de Telegram": "Unir-me al canal de Telegram",
    "Formatos técnicos para lectores de noticias e integraciones:": "Formats tècnics per a lectors de notícies i integracions:",
    "Consultar": "Consultar",
    "No hay publicaciones.": "No hi ha publicacions.",
    "Activa ubicación": "Activa ubicació"
  });

  function translateText(value) {
    if (!isVa || !value) return value;
    let result = String(value);
    Object.keys(dictionary).sort((a, b) => b.length - a.length).forEach((source) => {
      result = result.split(source).join(dictionary[source]);
    });
    return result;
  }

  function isLocalPath(path) {
    if (!isVa || !path || !path.startsWith("/") || path.startsWith("//")) return false;
    if (path === "/va" || path.startsWith("/va/") || path === "/es" || path.startsWith("/es/")) return false;
    return !excludedPrefixes.some((prefix) => path.startsWith(prefix));
  }

  function langPath(path) {
    if (!isLocalPath(path)) return path;
    return path === "/" ? "/va" : "/va" + path;
  }

  function translateNode(root) {
    if (!isVa || !root) return;

    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        const parent = node.parentElement;
        if (!parent || ["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA"].includes(parent.tagName)) {
          return NodeFilter.FILTER_REJECT;
        }
        return node.nodeValue && node.nodeValue.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
      }
    });

    const textNodes = [];
    while (walker.nextNode()) textNodes.push(walker.currentNode);
    textNodes.forEach((node) => {
      const translated = translateText(node.nodeValue);
      if (translated !== node.nodeValue) node.nodeValue = translated;
    });

    root.querySelectorAll?.("a[href], form[action]").forEach((el) => {
      const attr = el.hasAttribute("href") ? "href" : "action";
      const value = el.getAttribute(attr);
      const translated = langPath(value);
      if (translated !== value) el.setAttribute(attr, translated);
    });

    root.querySelectorAll?.("[placeholder], [title], [alt], [aria-label], [data-label]").forEach((el) => {
      ["placeholder", "title", "alt", "aria-label", "data-label"].forEach((attr) => {
        if (!el.hasAttribute(attr)) return;
        const value = el.getAttribute(attr);
        const translated = translateText(value);
        if (translated !== value) el.setAttribute(attr, translated);
      });
    });
  }

  if (isVa) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", () => translateNode(document.body));
    } else {
      translateNode(document.body);
    }

    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        mutation.addedNodes.forEach((node) => {
          if (node.nodeType === Node.ELEMENT_NODE) translateNode(node);
          if (node.nodeType === Node.TEXT_NODE && node.parentElement) translateNode(node.parentElement);
        });
      });
    });

    document.addEventListener("DOMContentLoaded", () => {
      if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    });
  }

  window.FunkI18n = {
    lang,
    isVa,
    t: translateText,
    path: langPath,
    translateNode
  };
})();
