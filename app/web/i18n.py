from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from fastapi import Request

DEFAULT_LANGUAGE = "es"
VALENCIAN_LANGUAGE = "va"
SUPPORTED_LANGUAGES = (DEFAULT_LANGUAGE, VALENCIAN_LANGUAGE)


@dataclass(frozen=True)
class LanguageConfig:
    code: str
    label: str
    short_label: str
    html_lang: str
    hreflang: str


LANGUAGES: dict[str, LanguageConfig] = {
    "es": LanguageConfig("es", "Castellano", "ES", "es", "es"),
    "va": LanguageConfig("va", "Valencià", "VA", "ca-ES-valencia", "ca-ES-valencia"),
}

# Páginas HTML públicas que tienen versión en valenciano. Las rutas no indexables
# también se sirven en /va para que el usuario no pierda el idioma al navegar.
TRANSLATABLE_PREFIXES: tuple[str, ...] = (
    "/",
    "/valencia-docentes",
    "/valencia-no-docentes",
    "/avisos",
    "/no-docente/plazas",
    "/no-docente/adjudicaciones",
    "/no-docente/publicaciones",
    "/no-docente/consulta-persona",
    "/no-docente/resultado-persona",
    "/centros",
    "/plazas-ofertadas",
    "/consulta-persona",
    "/resultado-persona",
    "/dificil-cobertura",
    "/resultado-dificil-cobertura",
    "/adjudicaciones",
    "/quienes-somos",
    "/contacto",
    "/politica-privacidad",
    "/politica-cookies",
    "/404",
)

EXCLUDED_LOCALIZED_PATH_PREFIXES: tuple[str, ...] = (
    "/api/",
    "/static/",
    "/feed.xml",
    "/feed.json",
    "/favicon.ico",
    "/sw.js",
    "/robots.txt",
    "/sitemap.xml",
    "/llms.txt",
)

# Traducción de interfaz. Se usa tanto para las plantillas renderizadas como para
# contenido insertado por JavaScript en cliente. No traduce datos oficiales de la API.
ES_TO_VA: dict[str, str] = {
    # SEO y cabecera
    "Funkcionario.com | Plazas y adjudicaciones docentes Comunidad Valenciana": "Funkcionario.com | Places i adjudicacions docents Comunitat Valenciana",
    "Consulta en Funkcionario.com plazas ofertadas, adjudicaciones docentes, difícil cobertura y resultados de personal interino docente en la Comunitat Valenciana.": "Consulta en Funkcionario.com places oferides, adjudicacions docents, difícil cobertura i resultats de personal interí docent en la Comunitat Valenciana.",
    "Plazas y adjudicaciones docentes Comunidad Valenciana | Funkcionario.com": "Places i adjudicacions docents Comunitat Valenciana | Funkcionario.com",
    "Panel de consulta para acceder a plazas ofertadas, consulta por persona, adjudicaciones y puestos de difícil cobertura docente en la Comunitat Valenciana.": "Panell de consulta per a accedir a places oferides, consulta per persona, adjudicacions i llocs de difícil cobertura docent en la Comunitat Valenciana.",
    "Personal no docente Comunidad Valenciana | Plazas, adjudicaciones y bolsas": "Personal no docent Comunitat Valenciana | Places, adjudicacions i borses",
    "Panel de consulta para personal no docente de atención educativa: plazas ADC, adjudicaciones y bolsas de empleo temporal publicadas por Conselleria.": "Panell de consulta per a personal no docent d'atenció educativa: places ADC, adjudicacions i borses d'ocupació temporal publicades per Conselleria.",
    "Avisos de publicaciones oficiales | Funkcionario.com": "Avisos de publicacions oficials | Funkcionario.com",
    "Últimos avisos públicos de Funkcionario.com sobre plazas, adjudicaciones, difícil cobertura y publicaciones de personal docente y no docente en la Comunitat Valenciana.": "Últims avisos públics de Funkcionario.com sobre places, adjudicacions, difícil cobertura i publicacions de personal docent i no docent en la Comunitat Valenciana.",
    "Plazas personal no docente Educación Valencia | Convocatorias ADC EDU": "Places de personal no docent Educació València | Convocatòries ADC EDU",
    "Consulta plazas ADC ofertadas para personal no docente de atención educativa en la Comunitat Valenciana.": "Consulta places ADC oferides per a personal no docent d'atenció educativa en la Comunitat Valenciana.",
    "Adjudicaciones personal no docente Valencia | ADC EDU": "Adjudicacions de personal no docent València | ADC EDU",
    "Consulta adjudicaciones ADC publicadas para personal no docente de atención educativa.": "Consulta adjudicacions ADC publicades per a personal no docent d'atenció educativa.",
    "Publicaciones personal no docente Valencia | Funkcionario.com": "Publicacions de personal no docent València | Funkcionario.com",
    "Consulta publicaciones oficiales de personal no docente de atención educativa detectadas y procesadas desde fuentes de Conselleria.": "Consulta publicacions oficials de personal no docent d'atenció educativa detectades i processades des de fonts de Conselleria.",
    "Consulta personal no docente por persona | Funkcionario.com": "Consulta de personal no docent per persona | Funkcionario.com",
    "Busca una persona en adjudicaciones y bolsas no docentes de atención educativa.": "Busca una persona en adjudicacions i borses no docents d'atenció educativa.",
    "Plazas docentes ofertadas Comunidad Valenciana | Funkcionario.com": "Places docents oferides Comunitat Valenciana | Funkcionario.com",
    "Consulta las últimas plazas docentes ofertadas en la Comunitat Valenciana, con filtros por fecha, localidad, centro, especialidad y distancia aproximada.": "Consulta les últimes places docents oferides en la Comunitat Valenciana, amb filtres per data, localitat, centre, especialitat i distància aproximada.",
    "Consulta adjudicaciones docentes por persona | Funkcionario.com": "Consulta adjudicacions docents per persona | Funkcionario.com",
    "Busca coincidencias por nombre para consultar una ficha de adjudicaciones docentes, participación en procedimientos y difícil cobertura.": "Busca coincidències per nom per a consultar una fitxa d'adjudicacions docents, participació en procediments i difícil cobertura.",
    "funkcionario.com | Quiénes somos": "funkcionario.com | Qui som",
    "Información sobre Funkcionario.com, proyecto de consulta y seguimiento de adjudicaciones docentes e interinos en la Comunitat Valenciana.": "Informació sobre Funkcionario.com, projecte de consulta i seguiment d'adjudicacions docents i interins en la Comunitat Valenciana.",
    "funkcionario.com | Contacto": "funkcionario.com | Contacte",
    "Contacta con Funkcionario.com para consultas, avisos o incidencias relacionadas con la web.": "Contacta amb Funkcionario.com per a consultes, avisos o incidències relacionades amb la web.",
    "Buscador de centros educativos Comunidad Valenciana | Funkcionario.com": "Cercador de centres educatius Comunitat Valenciana | Funkcionario.com",
    "Busca centros educativos de la Comunitat Valenciana por nombre, código, localidad o provincia y consulta mapa, ruta y distancia aproximada.": "Busca centres educatius de la Comunitat Valenciana per nom, codi, localitat o província i consulta mapa, ruta i distància aproximada.",
    "Difícil cobertura docentes Comunidad Valenciana | Funkcionario.com": "Difícil cobertura docent Comunitat Valenciana | Funkcionario.com",
    "Consulta puestos docentes de difícil cobertura en la Comunitat Valenciana, con filtros por especialidad, centro, localidad, fecha y distancia.": "Consulta llocs docents de difícil cobertura en la Comunitat Valenciana, amb filtres per especialitat, centre, localitat, data i distància.",
    "Política de Privacidad y Cookies | Funkcionario.com": "Política de Privacitat i Cookies | Funkcionario.com",
    "Política de privacidad de Funkcionario.com.": "Política de privacitat de Funkcionario.com.",
    "Página no encontrada en Funkcionario.com.": "Pàgina no trobada en Funkcionario.com.",

    # Navegación general
    "Castellano": "Castellà",
    "Valenciano": "Valencià",
    "Idioma": "Idioma",
    "Seleccionar idioma": "Seleccionar idioma",
    "Cambiar a castellano": "Canviar a castellà",
    "Cambiar a valenciano": "Canviar a valencià",
    "funkcionario.com inicio": "funkcionario.com inici",
    "Volver a inicio": "Tornar a l'inici",
    "Logo de funkcionario.com": "Logotip de funkcionario.com",
    "Inicio": "Inici",
    "Quiénes Somos": "Qui som",
    "Quiénes somos": "Qui som",
    "Avisos": "Avisos",
    "Contacto": "Contacte",
    "Privacidad &amp; Cookies": "Privacitat i Cookies",
    "Privacidad & Cookies": "Privacitat i Cookies",
    "Seguimiento de posiciones, plazas ofertadas y adjudicaciones para personal funcionario interino.": "Seguiment de posicions, places oferides i adjudicacions per a personal funcionari interí.",

    # Home
    "Consulta de Plazas y Adjudicaciones Docentes en la Comunitat Valenciana": "Consulta de places i adjudicacions docents en la Comunitat Valenciana",
    "Busca plazas ofertadas, adjudicaciones, participación por persona y puestos de difícil cobertura a partir de publicaciones oficiales de Conselleria.": "Busca places oferides, adjudicacions, participació per persona i llocs de difícil cobertura a partir de publicacions oficials de Conselleria.",
    "Acceder a docentes": "Accedir a docents",
    "Personal no docente de atención educativa en la Comunitat Valenciana": "Personal no docent d'atenció educativa en la Comunitat Valenciana",
    "Consulta plazas ADC, adjudicaciones, publicaciones oficiales y bolsas no docentes detectadas desde fuentes de Conselleria.": "Consulta places ADC, adjudicacions, publicacions oficials i borses no docents detectades des de fonts de Conselleria.",
    "Acceder a no docentes": "Accedir a no docents",
    "Avisos sin registro": "Avisos sense registre",
    "La Conselleria publica novedades en varias páginas y documentos. Funkcionario centraliza avisos públicos y, si quieres recibirlos sin dar email ni teléfono, puedes unirte al canal de Telegram.": "La Conselleria publica novetats en diverses pàgines i documents. Funkcionario centralitza avisos públics i, si vols rebre'ls sense donar correu electrònic ni telèfon, pots unir-te al canal de Telegram.",
    "Ver avisos": "Veure avisos",
    "Canal Telegram": "Canal de Telegram",

    # Avisos
    "Avisos públicos automáticos": "Avisos públics automàtics",
    "Avisos públicos": "Avisos públics",
    "Últimas publicaciones oficiales detectadas": "Últimes publicacions oficials detectades",
    "Esta página recoge avisos generados automáticamente cuando Funkcionario.com detecta publicaciones oficiales nuevas o relevantes. Verifica siempre la información en la fuente oficial antes de realizar trámites.": "Aquesta pàgina recull avisos generats automàticament quan Funkcionario.com detecta publicacions oficials noves o rellevants. Verifica sempre la informació en la font oficial abans de fer tràmits.",
    "Unirme al canal de Telegram": "Unir-me al canal de Telegram",
    "Formatos técnicos para lectores de noticias e integraciones:": "Formats tècnics per a lectors de notícies i integracions:",
    "Avisos recientes": "Avisos recents",
    "avisos publicados": "avisos publicats",
    "Consultar": "Consultar",
    "Fuente oficial": "Font oficial",
    "No hay avisos publicados todavía.": "Encara no hi ha avisos publicats.",

    # Docentes landing
    "Consulta de plazas y adjudicaciones docentes en la Comunitat Valenciana": "Consulta de places i adjudicacions docents en la Comunitat Valenciana",
    "Consulta de Plazas y Adjudicaciones Docentes": "Consulta de places i adjudicacions docents",
    "Consulta docentes": "Consulta docents",
    "Consulta plazas ofertadas, adjudicaciones, situación por persona y puestos de difícil cobertura publicados por Conselleria.": "Consulta places oferides, adjudicacions, situació per persona i llocs de difícil cobertura publicats per Conselleria.",
    "Estado de datos": "Estat de dades",
    "Última publicación procesada": "Última publicació processada",
    "Plazas ofertadas": "Places oferides",
    "Consulta": "Consulta",
    "Elige qué quieres consultar. Cada opción abre una página específica con filtros, resultados y detalle ampliado.": "Tria què vols consultar. Cada opció obri una pàgina específica amb filtres, resultats i detall ampliat.",
    "Ver últimas plazas ofertadas": "Veure últimes places oferides",
    "Consulta por persona": "Consulta per persona",
    "Ver difícil cobertura": "Veure difícil cobertura",
    "Las plazas ofertadas muestran puestos publicados. La consulta por persona permite localizar coincidencias y abrir una ficha completa. Difícil cobertura muestra puestos y candidatos del procedimiento específico.": "Les places oferides mostren llocs publicats. La consulta per persona permet localitzar coincidències i obrir una fitxa completa. Difícil cobertura mostra llocs i candidats del procediment específic.",
    "Ver avisos y alertas": "Veure avisos i alertes",
    "Alertas de navegador": "Alertes del navegador",
    "Activar alertas de novedades": "Activar alertes de novetats",
    "Desactivar alertas de novedades": "Desactivar alertes de novetats",
    "Alertas no disponibles": "Alertes no disponibles",
    "Ubicación": "Ubicació",
    "Usar mi ubicación": "Usar la meua ubicació",
    "Actualizar ubicación": "Actualitzar ubicació",
    "Borrar ubicación": "Esborrar ubicació",
    "Ubicación no disponible": "Ubicació no disponible",
    "No activada · sin distancia calculada": "No activada · sense distància calculada",
    "Activa · distancia disponible": "Activa · distància disponible",

    # Plazas docentes / difícil cobertura
    "Puestos ofertados": "Llocs oferits",
    "Últimas plazas docentes ofertadas": "Últimes places docents oferides",
    "Filtra por fecha, provincia, localidad, centro, cuerpo, especialidad, tipo de puesto y requisitos. Activa tu ubicación para calcular distancias aproximadas.": "Filtra per data, província, localitat, centre, cos, especialitat, tipus de lloc i requisits. Activa la teua ubicació per a calcular distàncies aproximades.",
    "Activa tu ubicación para ordenar resultados por distancia y abrir rutas en Maps. La ubicación solo se guarda en tu navegador.": "Activa la teua ubicació per a ordenar resultats per distància i obrir rutes en Maps. La ubicació només es guarda en el teu navegador.",
    "Fecha": "Data",
    "Provincia": "Província",
    "Localidad": "Localitat",
    "Centro": "Centre",
    "Cuerpo": "Cos",
    "Especialidad": "Especialitat",
    "Tipo": "Tipus",
    "Ordenar por": "Ordenar per",
    "Buscar": "Buscar",
    "Limpiar": "Netejar",
    "Plazas encontradas": "Places trobades",
    "Mostrando los resultados procesados desde publicaciones oficiales.": "Es mostren els resultats processats des de publicacions oficials.",
    "Difícil cobertura": "Difícil cobertura",
    "Puestos de difícil cobertura": "Llocs de difícil cobertura",
    "Consulta puestos de difícil cobertura publicados por Conselleria y accede al detalle de candidatos cuando esté disponible.": "Consulta llocs de difícil cobertura publicats per Conselleria i accedeix al detall de candidats quan estiga disponible.",
    "Candidatos de difícil cobertura": "Candidats de difícil cobertura",
    "Resultado": "Resultat",
    "Nueva búsqueda": "Nova cerca",
    "Ir a difícil cobertura": "Anar a difícil cobertura",
    "No se han cargado datos todavía.": "Encara no s'han carregat dades.",

    # Persona docente
    "Consulta de adjudicaciones por persona": "Consulta d'adjudicacions per persona",
    "Busca por nombre y apellidos para localizar coincidencias en adjudicaciones, participación y difícil cobertura.": "Busca per nom i cognoms per a localitzar coincidències en adjudicacions, participació i difícil cobertura.",
    "Nombre y apellidos": "Nom i cognoms",
    "Ejemplo: GARCIA PEREZ MARIA": "Exemple: GARCIA PEREZ MARIA",
    "Buscar persona": "Buscar persona",
    "Resultado por persona": "Resultat per persona",
    "Resultado individual de consulta por persona en Funkcionario.com.": "Resultat individual de consulta per persona en Funkcionario.com.",
    "Adjudicaciones": "Adjudicacions",
    "Participación": "Participació",
    "Dificil cobertura": "Difícil cobertura",
    "Difícil cobertura": "Difícil cobertura",
    "Volver al buscador": "Tornar al cercador",
    "Sin coincidencias": "Sense coincidències",
    "Prueba con menos términos o revisa el formato del nombre.": "Prova amb menys termes o revisa el format del nom.",
    "registros": "registres",
    "adjudicaciones": "adjudicacions",
    "Ver ficha": "Veure fitxa",
    "Introduce al menos 2 caracteres.": "Introdueix almenys 2 caràcters.",
    "Buscando coincidencias...": "Buscant coincidències...",
    "Abre la ficha completa de la coincidencia que te interese.": "Obri la fitxa completa de la coincidència que t'interesse.",

    # Centros
    "Buscador de centros": "Cercador de centres",
    "Centros educativos": "Centres educatius",
    "Busca centros por nombre, localidad, provincia, código o dirección. Activa tu ubicación para ordenar por distancia aproximada.": "Busca centres per nom, localitat, província, codi o adreça. Activa la teua ubicació per a ordenar per distància aproximada.",
    "Buscar centros": "Buscar centres",
    "Resultados": "Resultats",
    "Ficha de centro": "Fitxa de centre",
    "Ficha": "Fitxa",
    "Cargando ficha del centro...": "Carregant fitxa del centre...",
    "Cargando información...": "Carregant informació...",
    "Cargando detalle...": "Carregant detall...",
    "Cargando asignaciones...": "Carregant assignacions...",
    "Ver mapa": "Veure mapa",
    "Cómo llegar": "Com arribar",
    "Mapa": "Mapa",
    "Ruta": "Ruta",

    # No docentes
    "Personal no docente de atención educativa · Comunitat Valenciana": "Personal no docent d'atenció educativa · Comunitat Valenciana",
    "Personal no docente de atención educativa": "Personal no docent d'atenció educativa",
    "Seguimiento de plazas ADC, adjudicaciones y bolsas publicadas por Conselleria.": "Seguiment de places ADC, adjudicacions i borses publicades per Conselleria.",
    "Consulta no docentes": "Consulta no docents",
    "Los avisos automáticos están centralizados en una página propia, junto con Telegram, RSS y alertas del navegador.": "Els avisos automàtics estan centralitzats en una pàgina pròpia, juntament amb Telegram, RSS i alertes del navegador.",
    "Publicaciones": "Publicacions",
    "Plazas": "Places",
    "Personas en bolsa": "Persones en borsa",
    "Cargando resumen...": "Carregant resum...",
    "Consultas disponibles": "Consultes disponibles",
    "Consulta las plazas ofertadas en convocatorias ADC, adjudicaciones definitivas y situación en bolsas no docentes.": "Consulta les places oferides en convocatòries ADC, adjudicacions definitives i situació en borses no docents.",
    "Ver plazas ofertadas": "Veure places oferides",
    "Ver adjudicaciones": "Veure adjudicacions",
    "Bolsas oficiales": "Borses oficials",
    "Colectivos monitorizados": "Col·lectius monitoritzats",
    "Colectivo": "Col·lectiu",
    "Bolsa": "Borsa",
    "Última fecha": "Última data",
    "Últimas publicaciones": "Últimes publicacions",
    "Publicaciones no docentes detectadas más recientes.": "Publicacions no docents detectades més recents.",
    "Plazas ofertadas · personal no docente": "Places oferides · personal no docent",
    "Plazas no docentes ofertadas": "Places no docents oferides",
    "Esta consulta muestra solo plazas no docentes disponibles. Las convocatorias que ya no aparecen en la fuente oficial o que ya han sido adjudicadas dejan de mostrarse como disponibles. Los centros de estos listados pueden aparecer con textos administrativos no normalizados; usa el buscador de centros para localizar dirección, mapa, ruta y distancia con el catálogo oficial de centros.": "Aquesta consulta mostra només places no docents disponibles. Les convocatòries que ja no apareixen en la font oficial o que ja han sigut adjudicades deixen de mostrar-se com a disponibles. Els centres d'aquests llistats poden aparéixer amb textos administratius no normalitzats; usa el cercador de centres per a localitzar adreça, mapa, ruta i distància amb el catàleg oficial de centres.",
    "Adjudicaciones · personal no docente": "Adjudicacions · personal no docent",
    "Adjudicaciones no docentes": "Adjudicacions no docents",
    "Adjudicaciones definitivas ADC localizadas en los PDFs oficiales.": "Adjudicacions definitives ADC localitzades en els PDF oficials.",
    "Publicaciones oficiales · personal no docente": "Publicacions oficials · personal no docent",
    "Publicaciones oficiales": "Publicacions oficials",
    "Publicaciones no docentes oficiales": "Publicacions no docents oficials",
    "Convocatoria ADC": "Convocatòria ADC",
    "Adjudicación ADC": "Adjudicació ADC",
    "Actualización de bolsa": "Actualització de borsa",
    "Bolsa Función Pública": "Borsa Funció Pública",
    "Consulta por persona · personal no docente": "Consulta per persona · personal no docent",
    "Consulta no docente por persona": "Consulta no docent per persona",
    "Busca coincidencias en adjudicaciones y bolsas.": "Busca coincidències en adjudicacions i borses.",
    "Resultado por persona · personal no docente": "Resultat per persona · personal no docent",
    "Resultado no docente por persona": "Resultat no docent per persona",
    "Ir a búsqueda": "Anar a la cerca",
    "Ver publicaciones oficiales": "Veure publicacions oficials",

    # Legal/contacto/quiénes
    "Política de Privacidad y Cookies": "Política de Privacitat i Cookies",
    "Privacidad": "Privacitat",
    "Esta web no requiere registro para consultar información. No se solicita correo electrónico ni teléfono para usar los buscadores públicos.": "Aquesta web no requereix registre per a consultar informació. No se sol·licita correu electrònic ni telèfon per a usar els cercadors públics.",
    "Cookies y almacenamiento técnico": "Cookies i emmagatzematge tècnic",
    "Actualmente Funkcionario.com no usa cookies de analítica ni publicidad comportamental. Puede utilizar almacenamiento técnico del navegador para recordar preferencias o activar funciones como alertas push, siempre dentro de lo necesario para prestar el servicio solicitado.": "Actualment Funkcionario.com no usa cookies d'analítica ni publicitat comportamental. Pot utilitzar emmagatzematge tècnic del navegador per a recordar preferències o activar funcions com alertes push, sempre dins del necessari per a prestar el servei sol·licitat.",
    "Geolocalización": "Geolocalització",
    "La ubicación solo se usa si el usuario concede permiso en el navegador. Sirve para calcular distancias aproximadas a centros y abrir rutas. No sustituye a mapas oficiales ni implica seguimiento continuo.": "La ubicació només s'usa si l'usuari concedeix permís en el navegador. Serveix per a calcular distàncies aproximades a centres i obrir rutes. No substitueix mapes oficials ni implica seguiment continu.",
    "Telegram": "Telegram",
    "El canal de Telegram es externo a Funkcionario.com y se rige por las condiciones y política de privacidad de Telegram.": "El canal de Telegram és extern a Funkcionario.com i es regeix per les condicions i política de privacitat de Telegram.",
    "Limitación de responsabilidad": "Limitació de responsabilitat",
    "Funkcionario.com no sustituye a la fuente oficial. Para trámites, plazos o decisiones administrativas, verifica siempre la información en la web oficial de Conselleria.": "Funkcionario.com no substitueix la font oficial. Per a tràmits, terminis o decisions administratives, verifica sempre la informació en la web oficial de Conselleria.",
    "Para cualquier consulta relacionada con privacidad, cookies o tratamiento de datos técnicos, puedes contactar en": "Per a qualsevol consulta relacionada amb privacitat, cookies o tractament de dades tècniques, pots contactar en",
    "Proyecto independiente de consulta y seguimiento de publicaciones oficiales relacionadas con personal docente y no docente en la Comunitat Valenciana.": "Projecte independent de consulta i seguiment de publicacions oficials relacionades amb personal docent i no docent en la Comunitat Valenciana.",
    "No es una web oficial": "No és una web oficial",
    "Funkcionario.com no pertenece a la Generalitat Valenciana ni a Conselleria. La información se obtiene de publicaciones oficiales y debe verificarse siempre en la fuente original.": "Funkcionario.com no pertany a la Generalitat Valenciana ni a Conselleria. La informació s'obté de publicacions oficials i ha de verificar-se sempre en la font original.",
    "Responsable del proyecto": "Responsable del projecte",
    "Correo de contacto": "Correu de contacte",
    "LinkedIn": "LinkedIn",
    "Para avisar de errores, sugerir mejoras o comunicar incidencias de funcionamiento puedes escribir a:": "Per a avisar d'errors, suggerir millores o comunicar incidències de funcionament pots escriure a:",
    "También puedes contactar por LinkedIn:": "També pots contactar per LinkedIn:",

    # Estados y botones generales
    "Cargando...": "Carregant...",
    "Sin fecha": "Sense data",
    "Sin datos": "Sense dades",
    "No disponible": "No disponible",
    "PDF oficial": "PDF oficial",
    "Publicación": "Publicació",
    "Sin colectivo": "Sense col·lectiu",
    "No hay publicaciones.": "No hi ha publicacions.",
    "No hay colectivos cargados.": "No hi ha col·lectius carregats.",
    "Datos cargados desde publicaciones oficiales procesadas.": "Dades carregades des de publicacions oficials processades.",
    "No se pudo cargar el resumen:": "No s'ha pogut carregar el resum:",
    "Obteniendo ubicación...": "Obtenint ubicació...",
    "Ubicación activada correctamente. Se usará para calcular distancias en los listados.": "Ubicació activada correctament. S'usarà per a calcular distàncies en els llistats.",
    "Ubicación borrada. Ya no se calcularán distancias con tu posición.": "Ubicació esborrada. Ja no es calcularan distàncies amb la teua posició.",
    "No se pudo obtener tu ubicación.": "No s'ha pogut obtindre la teua ubicació.",
    "Permiso de ubicación denegado. Revisa los permisos del navegador para funkcionario.com.": "Permís d'ubicació denegat. Revisa els permisos del navegador per a funkcionario.com.",
    "No se pudo determinar la ubicación del dispositivo.": "No s'ha pogut determinar la ubicació del dispositiu.",
    "La ubicación ha tardado demasiado. Prueba de nuevo.": "La ubicació ha tardat massa. Torna-ho a provar.",
    "Tu navegador no permite geolocalización.": "El teu navegador no permet geolocalització.",
    "El navegador devolvió una ubicación no válida.": "El navegador ha retornat una ubicació no vàlida.",
    "Las alertas no están configuradas todavía.": "Les alertes encara no estan configurades.",
    "Desactivando alertas...": "Desactivant alertes...",
    "Alertas de novedades desactivadas.": "Alertes de novetats desactivades.",
    "Activando alertas...": "Activant alertes...",
    "Alertas de novedades activadas.": "Alertes de novetats activades.",
    "No se pudo cambiar el estado de las alertas.": "No s'ha pogut canviar l'estat de les alertes.",
    "La página que buscas no existe o se ha movido.": "La pàgina que busques no existeix o s'ha mogut.",
    "Volver al inicio": "Tornar a l'inici",
    "Ir a Valencia Docentes": "Anar a València Docents",
    "Ver publicación oficial": "Veure publicació oficial",
    "Activa tu ubicación para ver la distancia aproximada hasta el centro adjudicado y abrir una ruta directa en Maps.": "Activa la teua ubicació per a veure la distància aproximada fins al centre adjudicat i obrir una ruta directa en Maps.",
}


ES_TO_VA.update({
    "Seguimiento útil para personal funcionario interino": "Seguiment útil per a personal funcionari interí",
    "Consulta plazas ofertadas, tu posición y posibles adjudicaciones a partir de publicaciones oficiales.": "Consulta places oferides, la teua posició i possibles adjudicacions a partir de publicacions oficials.",
    "Consulta plazas ADC, adjudicaciones y bolsas de educadores, fisioterapeutas, TGEI, ILS, educadores sociales y terapeutas ocupacionales.": "Consulta places ADC, adjudicacions i borses d'educadors, fisioterapeutes, TGEI, ILS, educadors socials i terapeutes ocupacionals.",
    "Recibe novedades sin ceder email ni teléfono": "Rep novetats sense cedir correu electrònic ni telèfon",
    "Consulta avisos públicos automáticos sobre nuevas publicaciones oficiales. También puedes unirte al canal público de Telegram para recibir las novedades sin que Funkcionario.com almacene tu email ni tu teléfono.": "Consulta avisos públics automàtics sobre noves publicacions oficials. També pots unir-te al canal públic de Telegram per a rebre les novetats sense que Funkcionario.com emmagatzeme el teu correu electrònic ni el teu telèfon.",
    "Comunitat Valenciana · maestros y profesorado interino": "Comunitat Valenciana · mestres i professorat interí",
    "Información": "Informació",
    "Última actualización detectada:": "Última actualització detectada:",
    "Última publicación detectada:": "Última publicació detectada:",
    "No activada": "No activada",
    "Activa tu ubicación para calcular la distancia aproximada hasta cada centro y abrir rutas directas en Maps. La ubicación se guarda solo en tu navegador.": "Activa la teua ubicació per a calcular la distància aproximada fins a cada centre i obrir rutes directes en Maps. La ubicació es guarda només en el teu navegador.",
    "La web resume y facilita la consulta, pero la fuente oficial sigue siendo la Conselleria. Usa el enlace oficial para contrastar publicaciones, resoluciones y trámites.": "La web resumeix i facilita la consulta, però la font oficial continua sent la Conselleria. Usa l'enllaç oficial per a contrastar publicacions, resolucions i tràmits.",
    "Visitar Conselleria": "Visitar Conselleria",
    "La ubicación requiere permisos del navegador. Esta web puede utilizar almacenamiento técnico del navegador para funciones como las notificaciones. Consulta la": "La ubicació requereix permisos del navegador. Aquesta web pot utilitzar emmagatzematge tècnic del navegador per a funcions com les notificacions. Consulta la",
    "Política de Privacidad": "Política de Privacitat",
    "Política de Cookies": "Política de Cookies",
    "y la": "i la",
    "Consulta avanzada de plazas disponibles con filtros, distancia y acceso rápido al centro.": "Consulta avançada de places disponibles amb filtres, distància i accés ràpid al centre.",
    "Activa tu ubicación para ordenar por distancia y priorizar las plazas más cercanas. Por defecto se muestran plazas disponibles de la última publicación detectada.": "Activa la teua ubicació per a ordenar per distància i prioritzar les places més pròximes. Per defecte es mostren places disponibles de l'última publicació detectada.",
    "Las plazas adjudicadas dejan de mostrarse como disponibles.": "Les places adjudicades deixen de mostrar-se com a disponibles.",
    "Todas las localidades": "Totes les localitats",
    "Todas las especialidades": "Totes les especialitats",
    "Todos los tipos": "Tots els tipus",
    "Todas": "Totes",
    "Todos": "Tots",
    "Orden": "Ordre",
    "Fecha más reciente": "Data més recent",
    "Fecha más antigua": "Data més antiga",
    "Fecha más antigua": "Data més antiga",
    "Localidad A-Z": "Localitat A-Z",
    "Localidad Z-A": "Localitat Z-A",
    "Provincia A-Z": "Província A-Z",
    "Centro A-Z": "Centre A-Z",
    "Centro Z-A": "Centre Z-A",
    "Nombre A-Z": "Nom A-Z",
    "Título A-Z": "Títol A-Z",
    "Código de puesto asc": "Codi de lloc asc",
    "Código de puesto desc": "Codi de lloc desc",
    "Código de puesto": "Codi de lloc",
    "Código de centro": "Codi de centre",
    "Distancia más cercana": "Distància més pròxima",
    "Distancia más lejana": "Distància més llunyana",
    "Solo última publicación": "Només última publicació",
    "Tipo de puesto": "Tipus de lloc",
    "Código": "Codi",
    "Distancia": "Distància",
    "Acciones": "Accions",
    "Búsqueda": "Cerca",
    "Régimen": "Règim",
    "Centros encontrados": "Centres trobats",
    "Puestos disponibles, centros, distancia y candidatos registrados.": "Llocs disponibles, centres, distància i candidats registrats.",
    "Activa tu ubicación para ver la distancia aproximada hasta cada centro y priorizar mejor los puestos más cercanos. Por defecto se muestran puestos disponibles de la última publicación detectada.": "Activa la teua ubicació per a veure la distància aproximada fins a cada centre i prioritzar millor els llocs més pròxims. Per defecte es mostren llocs disponibles de l'última publicació detectada.",
    "Los puestos ya seleccionados dejan de mostrarse como disponibles.": "Els llocs ja seleccionats deixen de mostrar-se com a disponibles.",
    "Selección": "Selecció",
    "Con seleccionados": "Amb seleccionats",
    "Sin seleccionados": "Sense seleccionats",
    "Más candidatos": "Més candidats",
    "Menos candidatos": "Menys candidats",
    "Puestos encontrados": "Llocs trobats",
    "Candidatos": "Candidats",
    "Seleccionados": "Seleccionats",
    "Detalle de candidatos": "Detall de candidats",
    "Para revisar los candidatos de un puesto, pulsa “Candidatos” en el listado. El detalle se abrirá en una página propia.": "Per a revisar els candidats d'un lloc, prem “Candidats” en el llistat. El detall s'obrirà en una pàgina pròpia.",
    "Candidatos del puesto": "Candidats del lloc",
    "Volver a difícil cobertura": "Tornar a difícil cobertura",
    "Esta página muestra el detalle de candidatos del puesto seleccionado en la consulta de difícil cobertura.": "Aquesta pàgina mostra el detall de candidats del lloc seleccionat en la consulta de difícil cobertura.",
    "No hay puesto seleccionado": "No hi ha cap lloc seleccionat",
    "Para ver candidatos, primero entra en difícil cobertura, localiza un puesto y pulsa “Candidatos”.": "Per a veure candidats, primer entra en difícil cobertura, localitza un lloc i prem “Candidats”.",
    "Cargando resumen del puesto...": "Carregant resum del lloc...",
    "Candidatos registrados": "Candidats registrats",
    "Fila": "Fila",
    "Nombre": "Nom",
    "Petición": "Petició",
    "Puesto asignado": "Lloc assignat",
    "Ficha de candidato": "Fitxa de candidat",
    "Perfil por persona": "Perfil per persona",
    "Ver adjudicaciones oficiales": "Veure adjudicacions oficials",
    "Ver difícil cobertura oficial": "Veure difícil cobertura oficial",
    "Esta ficha reúne el resumen principal, el histórico de adjudicaciones y los registros de difícil cobertura de la persona seleccionada.": "Aquesta fitxa reuneix el resum principal, l'històric d'adjudicacions i els registres de difícil cobertura de la persona seleccionada.",
    "Cargando historial...": "Carregant historial...",
    "Consulta por persona · adjudicaciones y difícil cobertura": "Consulta per persona · adjudicacions i difícil cobertura",
    "Busca coincidencias y abre una ficha completa del resultado seleccionado": "Busca coincidències i obri una fitxa completa del resultat seleccionat",
    "Cómo usar esta consulta": "Com usar aquesta consulta",
    "Introduce al menos dos caracteres del nombre o apellidos. La búsqueda mostrará coincidencias encontradas en adjudicaciones y procedimientos de difícil cobertura.": "Introdueix almenys dos caràcters del nom o cognoms. La cerca mostrarà coincidències trobades en adjudicacions i procediments de difícil cobertura.",
    "Al pulsar": "En prémer",
    ", se abrirá una página de resultado con el resumen, histórico disponible y enlaces relacionados. La persona seleccionada se guarda temporalmente en la sesión del navegador.": ", s'obrirà una pàgina de resultat amb el resum, l'històric disponible i enllaços relacionats. La persona seleccionada es guarda temporalment en la sessió del navegador.",
    "Si necesitas calcular distancias a centros, activa primero tu ubicación desde la página principal de consulta.": "Si necessites calcular distàncies a centres, activa primer la teua ubicació des de la pàgina principal de consulta.",
    "Volver a consulta principal": "Tornar a la consulta principal",
    "Convocatorias ADC con puestos disponibles publicados en el anexo.": "Convocatòries ADC amb llocs disponibles publicats en l'annex.",
    "Resultados": "Resultats",
    "Puesto": "Lloc",
    "Persona": "Persona",
    "Puntuación": "Puntuació",
    "Puntuación mayor": "Puntuació major",
    "Registros de bolsa": "Registres de borsa",
    "Zona": "Zona",
    "Estado": "Estat",
    "Fuente": "Font",
    "Qué muestra": "Què mostra",
    "La ficha cruza adjudicaciones ADC y registros de bolsa disponibles en los documentos cargados.": "La fitxa creua adjudicacions ADC i registres de borsa disponibles en els documents carregats.",
    "Los datos deben contrastarse siempre con la publicación oficial enlazada.": "Les dades s'han de contrastar sempre amb la publicació oficial enllaçada.",
    "Publicaciones detectadas desde fuentes oficiales y procesadas por Funkcionario.com.": "Publicacions detectades des de fonts oficials i processades per Funkcionario.com.",
    "Desde": "Des de",
    "Hasta": "Fins a",
    "Título": "Títol",
    "Datos": "Dades",
    "Información legal": "Informació legal",
    "Información sobre datos técnicos, permisos del navegador, alertas y almacenamiento local.": "Informació sobre dades tècniques, permisos del navegador, alertes i emmagatzematge local.",
    "Funkcionario.com no requiere registro de usuarios para consultar la información publicada.": "Funkcionario.com no requereix registre d'usuaris per a consultar la informació publicada.",
    "La web se ha diseñado para evitar la recogida de datos personales innecesarios. No necesitas crear una cuenta, indicar tu email ni facilitar tu teléfono para consultar plazas, adjudicaciones o publicaciones.": "La web s'ha dissenyat per a evitar la recollida de dades personals innecessàries. No necessites crear un compte, indicar el teu correu electrònic ni facilitar el teu telèfon per a consultar places, adjudicacions o publicacions.",
    "Si activas funciones opcionales del navegador, como la geolocalización o las alertas push, el sitio puede tratar datos técnicos necesarios para prestar dichas funciones.": "Si actives funcions opcionals del navegador, com la geolocalització o les alertes push, el lloc pot tractar dades tècniques necessàries per a prestar aquestes funcions.",
    "En el caso de las alertas del navegador, se almacena la suscripción técnica generada por el propio navegador para poder enviar avisos generales de nuevas publicaciones. Esta suscripción no contiene tu nombre, email ni teléfono.": "En el cas de les alertes del navegador, s'emmagatzema la subscripció tècnica generada pel mateix navegador per a poder enviar avisos generals de noves publicacions. Aquesta subscripció no conté el teu nom, correu electrònic ni telèfon.",
    "La geolocalización solo se utiliza si concedes permiso expresamente en tu navegador. Se usa para calcular distancias aproximadas y abrir rutas hacia centros educativos. Funkcionario.com no almacena una posición histórica de tus movimientos.": "La geolocalització només s'utilitza si concedeixes permís expressament en el teu navegador. S'usa per a calcular distàncies aproximades i obrir rutes cap a centres educatius. Funkcionario.com no emmagatzema una posició històrica dels teus moviments.",
    "El canal público de Telegram permite recibir avisos sin que Funkcionario.com tenga que almacenar tu email ni tu teléfono. La relación con Telegram queda sujeta a las condiciones y política de privacidad de Telegram.": "El canal públic de Telegram permet rebre avisos sense que Funkcionario.com haja d'emmagatzemar el teu correu electrònic ni el teu telèfon. La relació amb Telegram queda subjecta a les condicions i política de privacitat de Telegram.",
    "Este sitio puede utilizar almacenamiento técnico del navegador y tecnologías similares para funciones estrictamente necesarias o solicitadas por el usuario, como recordar preferencias o activar avisos.": "Aquest lloc pot utilitzar emmagatzematge tècnic del navegador i tecnologies similars per a funcions estrictament necessàries o sol·licitades per l'usuari, com recordar preferències o activar avisos.",
    "Funkcionario.com no utiliza cookies de analítica ni cookies de publicidad comportamental.": "Funkcionario.com no utilitza cookies d'analítica ni cookies de publicitat comportamental.",
    "Las funciones opcionales del navegador, como geolocalización o notificaciones, requieren permiso expreso del usuario y pueden revocarse desde la configuración del navegador.": "Les funcions opcionals del navegador, com geolocalització o notificacions, requereixen permís exprés de l'usuari i poden revocar-se des de la configuració del navegador.",
    "Si en el futuro se incorporan herramientas de medición, analítica o publicidad, se informará de forma clara y se solicitará el consentimiento cuando corresponda.": "Si en el futur s'incorporen eines de mesurament, analítica o publicitat, s'informarà de forma clara i se sol·licitarà el consentiment quan corresponga.",
    "Feedback, sugerencias y necesidades de mejora": "Feedback, suggeriments i necessitats de millora",
    "Canales de contacto": "Canals de contacte",
    "Se acepta feedback, propuestas de mejora y necesidades funcionales que puedan ayudar a priorizar futuras evoluciones de la plataforma.": "S'accepta feedback, propostes de millora i necessitats funcionals que puguen ajudar a prioritzar futures evolucions de la plataforma.",
    "Responsable:": "Responsable:",
    "Email:": "Email:",
    "Perfil profesional": "Perfil professional",
    "Formulario de contacto": "Formulari de contacte",
    "Asunto": "Assumpte",
    "Mensaje": "Missatge",
    "Preparar email": "Preparar email",
    "Información general del proyecto": "Informació general del projecte",
    "es un proyecto creado para facilitar a los funcionarios interinos sin plaza fija el seguimiento de sus posiciones, de las plazas ofertadas, de las posibles adjudicaciones y de la información publicada por las fuentes oficiales.": "és un projecte creat per a facilitar als funcionaris interins sense plaça fixa el seguiment de les seues posicions, de les places oferides, de les possibles adjudicacions i de la informació publicada per les fonts oficials.",
    "En esta primera etapa, la plataforma está enfocada en maestros y profesorado de la Comunitat Valenciana, con una interfaz pensada para que consultar publicaciones oficiales sea más sencillo.": "En aquesta primera etapa, la plataforma està enfocada en mestres i professorat de la Comunitat Valenciana, amb una interfície pensada perquè consultar publicacions oficials siga més senzill.",
    "El objetivo a medio plazo es ampliar la cobertura a otros sectores y a otras comunidades autónomas, atendiendo a las necesidades reales del personal funcionario interino y priorizando siempre fuentes oficiales.": "L'objectiu a mitjà termini és ampliar la cobertura a altres sectors i a altres comunitats autònomes, atenent les necessitats reals del personal funcionari interí i prioritzant sempre fonts oficials.",
    "Últimas novedades detectadas": "Últimes novetats detectades",
    "Publicaciones oficiales procesadas por Funkcionario.com. No necesitas registrarte ni ceder email o teléfono para consultar los avisos.": "Publicacions oficials processades per Funkcionario.com. No necessites registrar-te ni cedir correu electrònic o telèfon per a consultar els avisos.",
    "Recibe avisos automáticos": "Rep avisos automàtics",
    "El canal principal de avisos es Telegram. También puedes activar alertas del navegador en este dispositivo.": "El canal principal d'avisos és Telegram. També pots activar alertes del navegador en aquest dispositiu.",
    "Activar alertas de navegador": "Activar alertes del navegador",
    "Telegram no requiere que Funkcionario.com guarde tu teléfono, email ni datos personales. Las alertas del navegador dependen de los permisos de este dispositivo.": "Telegram no requereix que Funkcionario.com guarde el teu telèfon, correu electrònic ni dades personals. Les alertes del navegador depenen dels permisos d'aquest dispositiu.",
    "Histórico público": "Històric públic",
    "Aún no hay avisos públicos registrados.": "Encara no hi ha avisos públics registrats.",
    "Cuando el pipeline detecte nuevas publicaciones oficiales, aparecerán aquí automáticamente.": "Quan el pipeline detecte noves publicacions oficials, apareixeran ací automàticament.",
})


# Correcciones específicas de la versión valenciana detectadas en QA.
# Se añaden fuera del literal principal para facilitar mantenerlas sin tocar bloques previos.
ES_TO_VA.update({
    # Textos largos: quiénes somos
    "es un proyecto creado para facilitar a los funcionarios interinos sin plaza fija el seguimiento de sus posiciones, de las plazas ofertadas, de las posibles adjudicaciones y de la información relacionada con las mismas.": "és un projecte creat per a facilitar als funcionaris interins sense plaça fixa el seguiment de les seues posicions, de les places oferides, de les possibles adjudicacions i de la informació relacionada amb aquestes.",
    "En esta primera etapa, la plataforma está enfocada en maestros y profesorado de la Comunitat Valenciana, con una interfaz pensada para que consultar publicaciones oficiales sea más rápido, más claro y más útil para el usuario final.": "En aquesta primera etapa, la plataforma està enfocada en mestres i professorat de la Comunitat Valenciana, amb una interfície pensada perquè consultar publicacions oficials siga més ràpid, més clar i més útil per a l'usuari final.",
    "El objetivo a medio plazo es ampliar la cobertura a otros sectores y a otras comunidades autónomas, atendiendo a las necesidades reales del personal funcionario interino y priorizando siempre la consulta práctica de información relevante.": "L'objectiu a mitjà termini és ampliar la cobertura a altres sectors i a altres comunitats autònomes, atenent les necessitats reals del personal funcionari interí i prioritzant sempre la consulta pràctica d'informació rellevant.",
    "Responsable del proyecto": "Responsable del projecte",

    # Formulario de contacto y placeholders
    "Nombre": "Nom",
    "Tu nombre o alias": "El teu nom o àlies",
    "Feedback, incidencia o propuesta de mejora": "Feedback, incidència o proposta de millora",
    "Cuéntanos tu sugerencia, problema o necesidad.": "Conta'ns el teu suggeriment, problema o necessitat.",
    "Completa todos los campos antes de continuar.": "Completa tots els camps abans de continuar.",
    "Email de contacto": "Email de contacte",
    "Mensaje:": "Missatge:",
    "Enviado desde el formulario de contacto de funkcionario.com": "Enviat des del formulari de contacte de funkcionario.com",
    "Se ha abierto tu aplicación de correo para enviar el mensaje.": "S'ha obert la teua aplicació de correu per a enviar el missatge.",

    # Privacidad y cookies: versión actual de la plantilla
    "La web se ha diseñado para evitar la recogida de datos personales innecesarios. No necesitas crear una cuenta, indicar tu email ni facilitar tu teléfono para consultar plazas, adjudicaciones, avisos o publicaciones.": "La web s'ha dissenyat per a evitar la recollida de dades personals innecessàries. No necessites crear un compte, indicar el teu email ni facilitar el teu telèfon per a consultar places, adjudicacions, avisos o publicacions.",
    "En el caso de las alertas del navegador, se almacena la suscripción técnica generada por el propio navegador para poder enviar avisos generales de nuevas publicaciones. Esta suscripción no permite identificar directamente a una persona por nombre, email o teléfono.": "En el cas de les alertes del navegador, s'emmagatzema la subscripció tècnica generada pel mateix navegador per a poder enviar avisos generals de noves publicacions. Aquesta subscripció no permet identificar directament una persona per nom, email o telèfon.",
    "La geolocalización solo se utiliza si concedes permiso expresamente en tu navegador. Se usa para calcular distancias aproximadas y abrir rutas hacia centros educativos. Funkcionario.com no necesita una cuenta de usuario para esta función.": "La geolocalització només s'utilitza si concedeixes permís expressament en el teu navegador. S'usa per a calcular distàncies aproximades i obrir rutes cap a centres educatius. Funkcionario.com no necessita un compte d'usuari per a aquesta funció.",
    "El canal público de Telegram permite recibir avisos sin que Funkcionario.com tenga que almacenar tu email ni tu teléfono. La relación con Telegram queda sujeta a las condiciones y configuración de privacidad propias de Telegram.": "El canal públic de Telegram permet rebre avisos sense que Funkcionario.com haja d'emmagatzemar el teu email ni el teu telèfon. La relació amb Telegram queda subjecta a les condicions i configuració de privacitat pròpies de Telegram.",
    "Este sitio puede utilizar almacenamiento técnico del navegador y tecnologías similares para funciones estrictamente necesarias o solicitadas por el usuario, como recordar preferencias locales, gestionar alertas push o permitir el funcionamiento correcto de la aplicación web.": "Aquest lloc pot utilitzar emmagatzematge tècnic del navegador i tecnologies similars per a funcions estrictament necessàries o sol·licitades per l'usuari, com recordar preferències locals, gestionar alertes push o permetre el funcionament correcte de l'aplicació web.",
    "Para cualquier consulta relacionada con privacidad, cookies o tratamiento de datos técnicos, puedes contactar en": "Per a qualsevol consulta relacionada amb privacitat, cookies o tractament de dades tècniques, pots contactar en",

    # Consulta por persona / no docente
    "Nombre o apellidos": "Nom o cognoms",
    "Apellido1 Apellido2, Nombre": "Cognom1 Cognom2, Nom",
    "Ejemplo": "Exemple",
    "Ejemplo: García, López, Martínez...": "Exemple: García, López, Martínez...",
    "Ejemplo: Gómez Valencia, Cristina Lucia": "Exemple: Gómez Valencia, Cristina Lucia",
    "Mostrando hasta 20 coincidencias. Si no encuentras el resultado, afina la búsqueda con nombre y apellidos.": "Mostrant fins a 20 coincidències. Si no trobes el resultat, afina la cerca amb nom i cognoms.",
    "Introduce al menos dos caracteres.": "Introdueix almenys dos caràcters.",
    "Buscando...": "Buscant...",
    "Ver plazas": "Veure places",

    # Resultados, paginación y metadatos dinámicos
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

    # Ficha de centros
    "Localidad": "Localitat",
    "Provincia": "Província",
    "Teléfono": "Telèfon",
    "Dirección": "Direcció",
    "Denominación genérica ES": "Denominació genèrica ÉS",
    "Denominación genérica VAL": "Denominació genèrica VAL",
    "Nombre específico": "Nom específic",
    "No se pudo cargar la ficha": "No s'ha pogut carregar la fitxa",

    # Avisos
    "Avisos públicos automáticos": "Avisos públics automàtics",
    "Avisos recientes": "Avisos recents",
    "Unirme al canal de Telegram": "Unir-me al canal de Telegram",
    "Formatos técnicos para lectores de noticias e integraciones:": "Formats tècnics per a lectors de notícies i integracions:",
    "Consultar": "Consultar",
})

# Reemplazos de rutas internas que no deben tocar /api ni recursos técnicos.
_LINK_ATTR_RE = re.compile(r'(?P<prefix>\s(?:href|action)=["\'])(?P<url>/[^"\']*)(?P<suffix>["\'])')
_JS_LOCATION_RE = re.compile(r'(?P<prefix>window\.location\.href\s*=\s*["\'])(?P<url>/[^"\']+)(?P<suffix>["\'])')
_HTML_TOKEN_RE = re.compile(r'(<[^>]+>)')
_TRANSLATABLE_ATTR_RE = re.compile(r'(?P<prefix>\s(?:placeholder|title|alt|aria-label|data-label|value)=["\'])(?P<value>[^"\']*)(?P<suffix>["\'])', re.IGNORECASE)
_RAW_HTML_START_RE = re.compile(r'^<\s*(script|style|noscript)\b', re.IGNORECASE)
_RAW_HTML_END_RE = re.compile(r'^<\s*/\s*(script|style|noscript)\s*>', re.IGNORECASE)


def get_language_from_path(path: str) -> str:
    if path == f"/{VALENCIAN_LANGUAGE}" or path.startswith(f"/{VALENCIAN_LANGUAGE}/"):
        return VALENCIAN_LANGUAGE
    return DEFAULT_LANGUAGE


def strip_language_prefix(path: str) -> str:
    if path == f"/{VALENCIAN_LANGUAGE}":
        return "/"
    if path.startswith(f"/{VALENCIAN_LANGUAGE}/"):
        stripped = path[len(f"/{VALENCIAN_LANGUAGE}") :]
        return stripped or "/"
    if path == f"/{DEFAULT_LANGUAGE}":
        return "/"
    if path.startswith(f"/{DEFAULT_LANGUAGE}/"):
        stripped = path[len(f"/{DEFAULT_LANGUAGE}") :]
        return stripped or "/"
    return path or "/"


def localized_path(path: str, lang: str) -> str:
    clean_path = strip_language_prefix(path)
    if not clean_path.startswith("/"):
        clean_path = f"/{clean_path}"

    if lang == VALENCIAN_LANGUAGE:
        if clean_path == "/":
            return f"/{VALENCIAN_LANGUAGE}"
        return f"/{VALENCIAN_LANGUAGE}{clean_path}"

    return clean_path


def is_localizable_url(url: str) -> bool:
    if not url.startswith("/") or url.startswith("//"):
        return False
    if url == f"/{VALENCIAN_LANGUAGE}" or url.startswith(f"/{VALENCIAN_LANGUAGE}/"):
        return False
    if url == f"/{DEFAULT_LANGUAGE}" or url.startswith(f"/{DEFAULT_LANGUAGE}/"):
        return False
    return not url.startswith(EXCLUDED_LOCALIZED_PATH_PREFIXES)


def translate_text(value: str, lang: str) -> str:
    if lang != VALENCIAN_LANGUAGE or not value:
        return value

    # Primero frases largas para evitar que una palabra corta rompa una frase mayor.
    result = value
    for source in sorted(ES_TO_VA, key=len, reverse=True):
        result = result.replace(source, ES_TO_VA[source])
    return result


def localize_links(html: str, lang: str) -> str:
    if lang != VALENCIAN_LANGUAGE:
        return html

    def replace_link(match: re.Match[str]) -> str:
        url = match.group("url")
        if not is_localizable_url(url):
            return match.group(0)
        return f'{match.group("prefix")}{localized_path(url, lang)}{match.group("suffix")}'

    return _LINK_ATTR_RE.sub(replace_link, html)


def _translate_html_tag(tag: str, lang: str) -> str:
    # No traducir href/action/src ni otros atributos técnicos: traducirlos puede romper rutas.
    # Solo se traducen atributos visibles para el usuario.
    if lang != VALENCIAN_LANGUAGE:
        return tag

    def replace_attr(match: re.Match[str]) -> str:
        return f'{match.group("prefix")}{translate_text(match.group("value"), lang)}{match.group("suffix")}'

    return _TRANSLATABLE_ATTR_RE.sub(replace_attr, tag)


def translate_html(html: str, lang: str) -> str:
    if lang != VALENCIAN_LANGUAGE:
        return html

    parts = _HTML_TOKEN_RE.split(html)
    translated_parts: list[str] = []
    raw_depth = 0

    for part in parts:
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            if _RAW_HTML_END_RE.match(part):
                raw_depth = max(0, raw_depth - 1)
                translated_parts.append(part)
                continue
            if raw_depth:
                translated_parts.append(part)
                continue
            if _RAW_HTML_START_RE.match(part):
                raw_depth += 1
                translated_parts.append(part)
                continue
            translated_parts.append(_translate_html_tag(part, lang))
        else:
            translated_parts.append(part if raw_depth else translate_text(part, lang))

    translated = "".join(translated_parts)
    translated = localize_links(translated, lang)
    return translated


def get_language_links(path: str) -> list[dict[str, str]]:
    clean_path = strip_language_prefix(path)
    return [
        {
            "code": config.code,
            "label": config.label,
            "short_label": config.short_label,
            "html_lang": config.html_lang,
            "hreflang": config.hreflang,
            "path": localized_path(clean_path, config.code),
        }
        for config in LANGUAGES.values()
    ]


def add_language_context(request: Request, context: dict) -> dict:
    lang = get_language_from_path(request.url.path)
    clean_path = strip_language_prefix(request.url.path)
    canonical_language_links = get_language_links(clean_path)
    selector_language_links = []
    for item in canonical_language_links:
        selector_item = dict(item)
        # En páginas /va, el enlace de castellano usa /es como alias técnico para
        # que el reescritor de enlaces internos no lo convierta en /va. /es redirige
        # después a la URL canónica sin prefijo.
        if item["code"] == DEFAULT_LANGUAGE and lang == VALENCIAN_LANGUAGE:
            selector_item["path"] = "/es" if clean_path == "/" else f"/es{clean_path}"
        selector_language_links.append(selector_item)

    context.update(
        {
            "current_lang": lang,
            "html_lang": LANGUAGES[lang].html_lang,
            "language_links": selector_language_links,
            "alternate_urls": [
                {
                    **item,
                    "url": f"{str(request.base_url).rstrip('/')}{item['path']}",
                }
                for item in canonical_language_links
            ],
        }
    )
    return context


def localize_json_ld(value, lang: str):
    if lang != VALENCIAN_LANGUAGE:
        return value
    if isinstance(value, str):
        return translate_text(value, lang)
    if isinstance(value, list):
        return [localize_json_ld(item, lang) for item in value]
    if isinstance(value, dict):
        return {key: localize_json_ld(item, lang) for key, item in value.items()}
    return value
