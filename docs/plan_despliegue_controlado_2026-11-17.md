# Funkcionario.com — Plan de despliegue controlado

Estado a 17/11/2026: cambios consolidados localmente en `audit/gva-review-2026-11-05`. Sin push ni acceso a producción. Instalar el HEAD completo revisado; los commits son grupos de revisión, no releases parciales desplegables.

## Evidencia reutilizada

- Consolidación: 235/235 tests offline, sin fallos/omitidos; 35 plantillas compiladas; AST Python, sintaxis de los dos pipelines y diff-check correctos. No se repitieron las suites PostgreSQL.
- Recuperación real del 16/11: PostgreSQL 16 validado, 25 tablas y 70 índices del archivo cubiertos, DR01/F07/DR03 completadas y datos/secuencias/metadatos originales preservados. Sin huella contemporánea de filas de producción: no se afirma igualdad con su estado actual.
- PostgreSQL del laboratorio detenido; LUKS cerrado/desmontado y unidades propias retiradas según recibo final del operador. No reabrir para preparar este despliegue.
- Perfiles y locks con hashes, wheelhouses y entornos limpios consumidor/fetcher ya fueron verificados. Reutilizarlos; no resolver dependencias nuevas desde requirements.txt ni copiar una .venv local como instalación portable. Compatibilidad con el destino e identidad de materiales se verifican en el preflight autorizado, sin repetir auditorías históricas.

## Bloqueantes de la ventana productiva

1. Autorización específica de acceso/ventana: identidad del host y base actuales, commit instalado, propietarios, espacio/límites y configuración efectiva. Los inventarios históricos no acreditan el estado del 17/11.
2. Quiescencia de productores, fetcher externo, importadores, parsers, reconciliadores, emisores y lectores legacy durante la transición. Confirmar agendas reales y cron privilegiado; los timers del repositorio difieren del inventario anterior y no deben sobrescribirlos ni activarse automáticamente. Conservar scripts externos de respaldo y configuraciones fuera del release.
3. Respaldo reciente de producción y procedimiento de recuperación acordado. El ensayo restaurado valida el backup del 31/10 y la arquitectura, no contiene necesariamente las escrituras posteriores. No basta con disponer de un TAR reciente sin integridad acreditada; no repetir el laboratorio ya validado como trámite general.
4. Artefacto del HEAD completo, manifiesto/hashes y entorno compatible. Sin evidencias, bases, PDFs, secretos ni launchers de recuperación. El builder de revisión aporta archivo e inventario, no una autorización de instalación. Reutilizar locks con hashes previamente aprobados; navegador/librerías Playwright deben estar acreditados antes de reactivar catálogo de centros. No bloquear una instalación inactiva por no haber habilitado aún ese job.
5. Configuración de avisos y origen: `ENABLE_NOTIFICATIONS=0`, corte F07 sin configurar inicialmente, baseline histórico conservado. No copiar `deploy/funkcionario.env.example` a producción: contiene una contraseña literal de ejemplo cuya validez no se ha acreditado y carece de la política F07. No modificar credenciales existentes ni exportarlas.

## Orden de la ejecución futura, previa autorización

1. Autorizar preflight remoto de solo lectura limitado a esos estados cambiantes. Identificar servicios, productores y versión actuales sin lanzar pipelines, consultas de datos personales ni emisores.
2. Aprobar ventana y respaldo; pausar actores identificados y proteger lectores incompatibles. Mantener transportes OFF y no tocar túneles, firewall ni timers ajenos.
3. Preparar release y entorno nuevos con materiales fijados; preservar configuraciones y release anterior. No hacer pull/instalaciones sobre la aplicación activa ni borrar el árbol existente.
4. Con destino y rol DDL verificados, aplicar **DR01 → F07 → DR03 en una única transacción** y con ON_ERROR_STOP. No usar schema.sql sobre la base existente. Comando de migración, solo dentro de una ventana autorizada y desde el release verificado:

```bash
psql -X --dbname="service=${VERIFIED_PGSERVICE:?verified libpq service required}" \
  --set=ON_ERROR_STOP=1 --single-transaction \
  --file=app/storage/migrations/20261106_normalize_text_restore.sql \
  --file=app/storage/migrations/20261105_notification_eligibility.sql \
  --file=app/storage/migrations/20261106_notification_dispatch.sql
```

No definir ese servicio por aproximación ni incluir contraseñas en argv. No reaplicar F07 sola después de DR03: sustituye una función de historial que DR03 completa.

5. Comprobar catálogos, índices, restricciones, normalización y las cuatro columnas token/snapshot, conservando las huellas del baseline productivo autorizado. Cambiar al código completo compatible antes de reactivar lectores/jobs. Fallo de migración: rollback transaccional y STOP, sin arranque parcial.
6. Autorizar por separado baseline/reconciliación: versiones previas unknown/historical_import, detecciones y pendientes legacy retenidos; no convertir antiguos success en acreditaciones nuevas. Los avisos validados y detecciones neutrales se presentan por separado; RSS/JSON no deben reexponer legacy como validado. Un feed vacío tras el gate puede ser el resultado esperado, no motivo para relajarlo.
7. Si se aprueba observar ingesta live: un solo productor, corte UTC explícito y ventana de edad revisada. Importación remota: `--notification-origin` y registro `--origin` coherentes; default historical_import. No retroceder corte ni promover archivos conocidos a live_new. Observar ledger sin envíos; activar Telegram/push exige otra autorización, con resultados inciertos retenidos.

## Parsers y reprocesado: decisión separada del despliegue

| Componente | Versión consolidada | Candidatos a revisar, sin procesarlos ahora |
|---|---|---|
| Clasificador | 0.8.0 | Unknown con patrones de inicio de curso/carrera; reclasificación exige plan propio. |
| Maestros | 0.4.0 | Interinos y carrera/prácticas con extracción anterior incompleta. |
| Secundaria | 0.4.0 | Carrera/prácticas y límites de destino/adjudicación. |
| Plazas ofertadas | 0.5.0 | Códigos partidos, cabeceras y horas; revisión F04 previa. |
| Difícil cobertura | 0.4.0 | Cabeceras/candidatos antes no reconocidos. |
| No docente | 0.2.0 | Formatos acreditados; preservar filas ante formatos desconocidos o vacíos. |
| Bolsa docente | 0.1.0 | Sin replay automático; mantiene retención ante fallo. |

Cambiar versión no autoriza replay. Obtener lista finita por SHA y motivo, preview de solo lectura tras esquema compatible y plan aprobado antes de `run_parse_selection.py --execute-plan ... --authorize-reprocess`. No borrar parse_runs ni parsed_at para forzar selección. Tras sustituir plazas, ejecutar matcher y actualización de ciclo de vida en el alcance expresamente autorizado: los IDs derivados pueden cambiar y las relaciones quedar obsoletas.

## Riesgos que limitan activaciones, sin exigir otra auditoría general

- **F04:** reinterpretación de horas pendiente de revisión por documento (82 casos históricos señalados). Bloquea replay afectado, no instalación con histórico retenido.
- **F07:** implementación y esquema validados; pendientes corte/origen/colas productivos y autorización de envío. ENABLE_NOTIFICATIONS=0 no neutraliza lectores ni registradores legacy; deben salir de servicio durante transición.
- **F09:** cobertura semántica parcial por familias. Mantener rechazo/rollback/held de formatos no acreditados; no aceptar cero como prueba de completitud.
- **F10:** queries de HTML preservadas; identidad de documentos parametrizados sigue limitada por compatibilidad. No cambiar claves canónicas ni fusionar variantes automáticamente. Colisión real observada: STOP del flujo afectado y corrección específica.
- Envío incierto o parcial: no reintentar automáticamente ni marcar enviado artificialmente. Los tokens cercan confirmaciones obsoletas; no garantizan cancelación de una petición ya en vuelo ni exactamente una recepción.

## Rollback y STOP

Preferir roll-forward manteniendo esquema aditivo, procedencia, ledger, tokens e historias. Ante fallo: apagar transportes y pausar actores propios acreditados; conservar evidencias y release anterior. No volver a lectores/emisores antiguos que ignoren elegibilidad; una reversión parcial conserva gates o mantiene esos endpoints/jobs deshabilitados. No DROP, borrados de colas ni restore a ciegas después de posibles entregas: requiere conciliación y autorización.

STOP por identidad/esquema inesperados, productor activo no controlado, hash distinto, dependencia no acreditada, pérdida de baseline, liberación de histórico o entrega no autorizada. No corregir el resultado reduciendo controles.

## Siguiente acción mínima exacta

Solicitar autorización de **preflight remoto de solo lectura** del host/base vigentes y de las agendas del productor real, con salida saneada. Primer comando de identidad de release propuesto (no ejecutado):

```bash
ssh -T -F /dev/null -i "$HOME/.ssh/funkcionario_prod" \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes \
  -o UpdateHostKeys=no -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
  -o IdentityAgent=none -o ForwardAgent=no -o ClearAllForwardings=yes \
  -o ProxyCommand=none -o ProxyJump=none -o ControlMaster=no -o ControlPath=none \
  -o PreferredAuthentications=publickey -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no -o ConnectTimeout=10 \
  funkcionario@178.104.237.94 'git -C /srv/funkcionario/app rev-parse HEAD'
```

Esta lectura no despliega ni acredita por sí sola los demás requisitos. Una vez confirmado el preflight dirigido: autorización separada de respaldo/ventana/migración/instalación del HEAD completo. Push, reprocesado histórico, activación de pipelines y notificaciones continúan sin autorizar.
