# Recuperación de Funkcionario: apertura reversible en solo consulta

**Preparación; no autoriza operaciones remotas ni despliegue.** Un administrador
debe ejecutar cada bloque únicamente tras la autorización indicada. No ejecutar
un bloque si falla alguno de sus controles. El rollback conserva ambas bases.

## Arquitectura y requisitos

Mantener `radar_docent_cv` intacta. Restaurar la candidata verificada en una
base nueva `radar_docent_cv_recovered_20261126`, en el mismo PostgreSQL 16, con un rol
lector dedicado y una instancia web separada en `127.0.0.1:8001`. Tras la
aceptación, cambiar reversiblemente el origen del Cloudflare Tunnel desde
`http://127.0.0.1:8000` a `http://127.0.0.1:8001`. No apuntar pipeline, backup ni ninguna otra unidad a la nueva base
por accidente. No usar el superusuario PostgreSQL como usuario de la aplicación.

La candidata local protegida es `recovery_19nov_capacity/funk-404-674-independent-trial`,
base `baseline_404_674_trial`; el código de partida es el commit
`881f0697ea377a5433430199c92008f49689c219` de `recovery/2026-11-readonly`.
La unidad en 8001 y este runbook necesitan además el commit local posterior;
registrar el SHA exacto de la release mediante `git rev-parse HEAD` después de
verificar que incluye esos archivos. No desplegar el commit de partida aislado.
El recibo cifrado `comparison_21nov_26nov_content.json` acredita igualdad de
las 31 tablas públicas del 21 y 26 de noviembre, no de funciones, secuencias,
privilegios ni otros esquemas. La base y el directorio del servidor son recursos
distintos: revisar capacidad para una segunda base, índices, WAL, backups,
temporales y margen de rollback; confirmar cifrado del almacenamiento y una
ubicación segura del proxy y secretos. Un dump lógico puede ejecutar código al
restaurarse: solo importar el artefacto local acreditado tras revisar su TOC.

Se requieren: administrador del servidor; PostgreSQL 16 con privilegios para
crear base y rol y restaurar objetos; identidad de lectura efectiva probada;
acceso seguro al proxy; copia previa recuperable de código y configuración sin
exponer secretos; y almacenamiento para conservar la base anterior. La
autorización de lectura de backups históricos no autoriza estos cambios.

## 1. Precondiciones y control justo antes del cambio

**Autorizaciones separadas:** acceso remoto de inspección; respaldo consistente
actual; detención/fence temporal de escritores y servicios que se identifiquen;
y después creación de recursos. Las consultas no prueban por sí solas que una
base no cambiará después de la instantánea. Identificar y bloquear todos los
escritores, incluidas sesiones existentes, administradores, tareas externas y
aplicaciones distintas de la web, y mantener ese fence desde el último backup
hasta terminar el cambio de proxy. Si no puede garantizarse, **cancelar**; no
basta comprobar recuentos dos veces. No conceder permisos nuevos a productores.

Como inspección, un administrador ejecutará y revisará, sin imprimir secretos:

```bash
systemctl is-active funkcionario-web.service funkcionario-pipeline.service funkcionario-centers.service
systemctl is-enabled funkcionario-pipeline.timer funkcionario-centers.timer
systemctl list-timers --all
systemctl cat funkcionario-web.service funkcionario-pipeline.service funkcionario-centers.service
systemctl cat funkcionario-backup.timer funkcionario-backup.service
ss -ltn '( sport = :8000 or sport = :8001 )'
sudo -u postgres psql -X -w -v ON_ERROR_STOP=1 -d radar_docent_cv \
  -c "SELECT current_database(), current_setting('server_version'), pg_database_size(current_database());"
df -h /var/lib/postgresql
```

Los nombres reales de backup/healthcheck y del proxy deben confirmarse; si la
unidad se llama de otro modo, no inferir su contenido. Revisar crontabs del
usuario de aplicación y de root, timers, contenedores, workers, matcher,
scraper, Telegram, correo y push sin volcar secretos. El `Persistent=true` de
los timers del repositorio puede disparar ejecuciones atrasadas. Parar y
deshabilitar productores únicamente con autorización; verificar procesos y
sesiones inactivos. Healthchecks deben inspeccionarse: no se asume que sean
inofensivos ni se deshabilitan automáticamente.

El `funkcionario-backup.timer` sigue habilitado, su último servicio falló y
**su contenido real es desconocido**.
Determinar base(s), destino, retención, permisos, impacto de I/O, riesgo de
incluir credenciales y si restaura ambas bases. Si no respalda la nueva base,
aprobar una adaptación separada o aceptar explícitamente esa carencia antes de
abrir; no alterar ni desactivar el backup por defecto.

Tras aplicar el fence, adquirir un dump consistente actual bajo autorización
siguiendo [el procedimiento de snapshot](productive_snapshot_comparison.md),
restaurarlo en LUKS y compararlo por identidad y contenido contra el 26/11.
Comparar además esquema completo, extensiones, secuencias, funciones, triggers,
permisos y esquemas no públicos mediante inventarios privados del backup y del
estado actual. El comparador de 31 tablas no cubre esos objetos. **Cancelar ante
cualquier diferencia, objeto no comparable, error, fence incompleto o evidencia
de una escritura posterior al snapshot.** Mantener el fence durante el paso 5.
Las secuencias no forman parte de una instantánea MVCC de pg_dump; documentar y
controlar sus cambios por separado. No sustituir estas pruebas por fechas o
recuentos. Si existen cambios, se necesita un nuevo ensayo aislado y aprobación.

## 2. Artefacto local y transferencia

**Autorización:** transferencia cifrada y restauración en el servidor. El
artefacto definitivo ya se exportó y restauró localmente en PostgreSQL 16.
Su tamaño es `73218752` bytes y su SHA-256 es
`a6ede91564e2b236ba61c9d6025904f1508fe4da873de396df40eb5160a2b308`.
El recibo cifrado `final_candidate_export_20261126_v3/final_validation_receipt_v3.json`
acredita igualdad de contenido en 44 tablas de seis esquemas, 128 índices,
115 restricciones, 38 funciones, 2 disparadores, 3 extensiones y el estado de
27 secuencias; API y rol lector también pasaron. La restauración utiliza
`--no-owner --no-acl`, por lo que propietarios y permisos se configuran y
validan por separado. Ningún dump descifrado fuera de LUKS ni persistente en el
servidor. La recepción por SSH y los permisos efectivos de producción siguen
sin ensayarse; probar primero en un servidor aislado equivalente.

En la estación local, antes de transferir, comprobar el artefacto exacto:

```bash
cd /home/zafion/Proyects/Radar_Docent_CV
V="$PWD/auditoria_local/recovery_19nov_capacity"
test "$(findmnt -n -o SOURCE -T "$V")" = /dev/mapper/funk_recovery_19nov_capacity || exit 1
DUMP="$V/final_candidate_export_20261126_v3/candidate.dump"
printf '%s  %s\n' a6ede91564e2b236ba61c9d6025904f1508fe4da873de396df40eb5160a2b308 "$DUMP" | sha256sum -c - || exit 1
test "$(stat -c %s "$DUMP")" = 73218752 || exit 1
/usr/lib/postgresql/16/bin/pg_restore --list "$DUMP" >/dev/null || exit 1
```

`pg_restore --list` no verifica todos los bloques; el recibo demuestra una
restauración completa local del mismo SHA. No regenerar el dump sin repetir la
restauración y la comparación completas.

El archivo queda cifrado por LUKS en la estación. La transferencia prevista en
el paso 3 entra directamente al proceso `pg_restore` remoto; no crea un dump
temporal en el servidor. Si esa transmisión no es viable, detenerse y preparar
por separado un destino remoto cifrado, privado y con limpieza verificada;
no volcar datos a un directorio remoto sin acreditar su protección.

## 3. Crear destino y rol, sin tocar la base original

**Autorización:** crear la base, instalar extensiones verificadas, restaurar y
crear/configurar un rol lector. Antes, confirmar que el nombre de destino y rol
no existen; detenerse si existen. No instalar extensiones o migraciones cuyo
origen, versión o compatibilidad no hayan sido acreditados. El restaurador local
usa `--no-owner --no-acl` y una adaptación concreta de `normalize_text`: no
asumir que ese mismo procedimiento se puede aplicar al servidor sin ensayarlo.
Inventariar y aprobar el tratamiento de funciones, triggers, extensiones,
propiedad, ACL, secuencias y migraciones de la candidata antes de ejecutar.

```bash
sudo -u postgres psql -X -w -v ON_ERROR_STOP=1 -d postgres \
  -c "SELECT datname FROM pg_database WHERE datname='radar_docent_cv_recovered_20261126';"
sudo -u postgres psql -X -w -v ON_ERROR_STOP=1 -d postgres \
  -c "SELECT rolname FROM pg_roles WHERE rolname='funkcionario_reader';"
```

Solo después del ensayo y autorización, y si ambos SELECT anteriores no
devuelven filas, ejecutar en el servidor:

```bash
sudo -u postgres /usr/lib/postgresql/16/bin/createdb -h /var/run/postgresql \
  -p 5432 -T template0 -E UTF8 radar_docent_cv_recovered_20261126
```

Antes comprobar que `template0` y sus ajustes de locale/collation reproducen los
de la candidata; si no, detenerse y aprobar opciones explícitas. Para transferir
desde la estación local sin archivo remoto persistente, con el SHA ya comprobado,
sudo remoto no interactivo previamente autorizado para ese comando exacto y el
destino aislado recién creado:

```bash
ssh -T -F /dev/null -i /home/zafion/.ssh/funkcionario_prod \
  -o BatchMode=yes -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes \
  -o UserKnownHostsFile=/home/zafion/.ssh/known_hosts -o IdentityAgent=none \
  -o ForwardAgent=no -o ClearAllForwardings=yes -o ProxyCommand=none \
  -o ProxyJump=none -o ControlMaster=no -o ControlPath=none \
  funkcionario@178.104.237.94 \
  'sudo -n -u postgres /usr/lib/postgresql/16/bin/pg_restore --exit-on-error --single-transaction --no-owner --no-acl -h /var/run/postgresql -p 5432 -d radar_docent_cv_recovered_20261126' \
  < "$DUMP"
```

Un error de SSH o de `pg_restore` cancela la operación. Estas opciones no
restauran propietarios ni ACL: reconstruirlos según el inventario aprobado y
validarlos. No reutilizar ni borrar otra base ante un fallo. Comparar el destino con
la candidata por identidad y contenido, no solo por totales; comprobar SHA
documentales, 213 asociaciones, 6 vacantes no adjudicadas y cuarentenas.
Verificar `pg_index.indisvalid`, constraints validadas y estado de secuencias.
Un error de restauración deja el destino aislado y el cambio cancelado.

El rol `funkcionario_reader` necesita `LOGIN`, `CONNECT`, `USAGE` de esquemas y
`SELECT` solo de tablas/vistas requeridas; sin membresías de escritura, ni
ownership, superusuario, `CREATEDB`, `CREATEROLE`, `BYPASSRLS`, replicación,
`CREATE` o `TEMP`. Revisar también privilegios heredados de `PUBLIC`, secuencias,
funciones con efectos laterales, `SECURITY DEFINER`, FDW y extensiones. Aplicar
revocaciones solamente en la **base nueva**, con lista explícita de funciones
seguras si la API las necesita. `default_transaction_read_only` y el flag de la
API son defensas adicionales, no sustitutos de permisos. Comprobar con esa
identidad real que consultas funcionan y escrituras, DDL y funciones peligrosas
fallan incluso si intenta cambiar `transaction_read_only`.

La autenticación actual del servidor para ese nuevo rol no está acreditada.
Comprobar la regla efectiva de socket/peer o un mecanismo seguro ya existente;
si exige cambiar `pg_hba.conf` o credenciales, preparar y autorizar ese cambio
por separado. Nunca poner contraseñas en argumentos, Git, comandos, recibos ni
chat. La configuración de conexión debe residir en un fichero restringido de
servicio, separado del usado por el pipeline y sin imprimir su contenido.

Una vez confirmados los nombres, el método de autenticación y la lista de
tablas y funciones necesarias, el administrador puede aplicar el siguiente
esqueleto **solo a la base nueva**, con autorización independiente. Debe adaptar
las concesiones de esquemas no públicos a la lista contrastada en el ensayo y
verificar antes las consecuencias de revocar funciones para esa base:

```bash
sudo -u postgres psql -X -w -v ON_ERROR_STOP=1 -h /var/run/postgresql \
  -p 5432 -d radar_docent_cv_recovered_20261126 <<'SQL'
BEGIN;
CREATE ROLE funkcionario_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS NOINHERIT;
ALTER ROLE funkcionario_reader SET default_transaction_read_only = on;
REVOKE ALL ON DATABASE radar_docent_cv_recovered_20261126 FROM PUBLIC;
GRANT CONNECT ON DATABASE radar_docent_cv_recovered_20261126 TO funkcionario_reader;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO funkcionario_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO funkcionario_reader;
DO $$ DECLARE s text; f record; BEGIN
  FOREACH s IN ARRAY ARRAY['recovery_batch','recovery_03nov','recovery_0519','recovery_quarantine'] LOOP
    IF to_regnamespace(s) IS NOT NULL THEN
      EXECUTE format('GRANT USAGE ON SCHEMA %I TO funkcionario_reader',s);
      EXECUTE format('GRANT SELECT ON ALL TABLES IN SCHEMA %I TO funkcionario_reader',s);
    END IF;
  END LOOP;
  FOR f IN SELECT p.oid::regprocedure AS fn FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE (n.nspname NOT IN ('pg_catalog','information_schema') AND (p.prosecdef OR p.provolatile='v'))
       OR (n.nspname='pg_catalog' AND p.proname IN ('pg_notify','lo_create','lo_creat','lo_import','lo_unlink','lo_put'))
  LOOP EXECUTE format('REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC',f.fn); END LOOP;
END $$;
COMMIT;
SQL
```

La concesión de todas las tablas de `public` es un máximo de lectura, no una
lista mínima validada. Reducirla a las tablas comprobadas cuando se disponga del
inventario. Este bloque no configura autenticación; concede cuatro esquemas de
evidencia cuando existan. El `REVOKE` del catálogo afecta a los demás roles de
esta base nueva: ensayar explícitamente su impacto. El código rechaza roles que
puedan crear temporales, usar funciones volátiles no sistémicas o las funciones
sensibles de catálogo enumeradas. El bloqueo no es una auditoría exhaustiva de
todas las funciones de extensiones; revisar y autorizar cualquier concesión
adicional. Verificar ownership, membresías, RLS y privilegios heredados antes de
cualquier apertura.

## 4. Arranque y aceptación sin tráfico público

**Autorización:** instalar el release exacto y arrancar únicamente la unidad
independiente, sin detener `funkcionario-web.service`. Conservar copia protegida
de configuraciones y estado anterior. Revisar dependencias y hashes del release;
copiar `deploy/funkcionario-web-recovery.service.example` a
`/etc/systemd/system/funkcionario-web-recovery.service` solo si no existe;
crear `/etc/funkcionario/recovery-web.env` privado, separado del archivo actual,
con `RADAR_DOCENT_DB_URL` del rol lector y la base nueva. No publicar su valor.
El script `scripts/start_recovery_loopback.sh` fija `FUNK_RECOVERY_READ_ONLY=1`,
`ENABLE_NOTIFICATIONS=0`, `127.0.0.1:8001` y deshabilita access logs. La unidad
suprime stdout/stderr para evitar datos personales: el journal conserva estado y
código de salida del servicio, no diagnósticos detallados. Preparar un canal de
diagnóstico privado antes de abrir si ese límite impide resolver un fallo.

En el servidor, después de autorizar instalación y verificar los archivos del
release, un administrador puede instalar la unidad y preparar el archivo de
entorno privado **sin sobrescribir archivos existentes**:

```bash
test ! -e /etc/systemd/system/funkcionario-web-recovery.service && test ! -L /etc/systemd/system/funkcionario-web-recovery.service || exit 1
test ! -e /etc/funkcionario/recovery-web.env && test ! -L /etc/funkcionario/recovery-web.env || exit 1
sudo cp -n -- \
  /srv/funkcionario/app/deploy/funkcionario-web-recovery.service.example \
  /etc/systemd/system/funkcionario-web-recovery.service
sudo chown root:root /etc/systemd/system/funkcionario-web-recovery.service
sudo chmod 0644 /etc/systemd/system/funkcionario-web-recovery.service
sudo sh -c 'set -C; umask 027; : > /etc/funkcionario/recovery-web.env'
sudo chown root:funkcionario /etc/funkcionario/recovery-web.env
sudoedit /etc/funkcionario/recovery-web.env
```

Introducir privadamente `RADAR_DOCENT_DB_URL` con el rol lector y la base nueva;
no copiar el archivo de entorno anterior, no imprimirlo y verificar por separado
propietario, grupo y permisos. Este archivo es obligatorio para el servicio.

Antes de arrancar, probar que `IPAddressDeny`/`IPAddressAllow` funcionan en ese
host y que únicamente loopback queda permitido; si systemd no aplica esos filtros,
cancelar. Permitir PostgreSQL local por TCP o socket y Cloudflared por loopback.
El ejemplo **no** detiene timers, workers ni emisores. Tras instalar la unidad y
configuración, con autorización expresa:

```bash
sudo systemctl daemon-reload
sudo systemctl start funkcionario-web-recovery.service
systemctl is-active funkcionario-web.service funkcionario-web-recovery.service
ss -ltn '( sport = :8000 or sport = :8001 )'
curl --noproxy '*' -fsS http://127.0.0.1:8001/health
```

Confirmar que 8000 sigue activo y 8001 escucha solo en 127.0.0.1. No habilitar
la nueva unidad para el arranque automático ni modificar la unidad existente.

Comprobaciones mínimas antes de abrir, en la instancia nueva y sin registrar
identidades personales: `/health`; búsqueda y perfil de una persona conocida;
bolsas, adjudicaciones, centros, difícil cobertura y `/api/historical-procedures`
y sus documentos. Contrastar contenido y recuentos contra SQL de la base nueva.
Verificar 213 asociaciones estrictas, 6 vacantes no adjudicadas, 598 plazas en
cuarentena no visibles como vigentes, puestos 674 `unknown`, fechas oficiales
desconocidas y avisos de extracción parcial. Probar navegador de escritorio y
móvil, castellano y valenciano. Confirmar que alertas/posts/push no cambian y que
la red saliente, scraping, cron, workers y emisores siguen bloqueados.

## 5. Cambio reversible y rollback

**Autorización independiente:** abrir tráfico en modo consulta. En Zero Trust,
abrir la configuración del túnel y sus rutas publicadas; identificar ambas
entradas de `funkcionario.com` por ID y orden, y guardar privadamente sus
ajustes completos. Se ha observado que ambas apuntan a `http://127.0.0.1:8000`
y que la primera tiene prioridad; verificarlo otra vez en el panel y en la
documentación/configuración efectiva antes de editar. Si hay reglas solapadas no
explicadas o no se puede comprobar su precedencia, cancelar.

Sin eliminar rutas, editar primero la ruta prioritaria a
`http://127.0.0.1:8001`, guardar y comprobar la aplicación pública. Editar la
segunda con el mismo destino, guardar y confirmar que las dos rutas conservan
su hostname, orden y demás parámetros, y que ambas señalan 8001. Durante la
transición la segunda ruta aún puede servir 8000 si cambia la precedencia: no
abrir si esa posibilidad no es aceptable. Verificar API, web, idiomas y
ausencia de escrituras/notificaciones; no modificar `cloudflared.service`.

Ante un fallo, en el mismo panel restaurar **ambas** rutas por ID a sus valores
capturados (`http://127.0.0.1:8000` si siguen coincidiendo), guardar y verificar
orden, propagación y contenido público; después detener solo
`funkcionario-web-recovery.service`. Si la ruta anterior no es segura para
consulta, mantener la web cerrada en lugar de restablecerla automáticamente.
No borrar ninguna base ni activar productores. Ensayar primero rollback del
túnel en un entorno autorizado; la interfaz y precedencia reales no se han
verificado en esta intervención.

## 6. Publicación del código y bloqueos

La referencia remota **local** sitúa `origin/recovery/2026-11-readonly` en
`881f069`; no se ha consultado GitHub en esta intervención. El commit de la
unidad 8001 y este runbook aún no se ha publicado. Antes de publicar, revisar selectivamente contenido y
dependencias, probar desde checkout limpio y solicitar autorización para mostrar
la lista exacta de archivos y commits; no incluir `auditoria_local`, dumps,
LUKS, datos personales ni secretos. No hacer push ni merge automáticamente.

Bloqueos estrictos para abrir: fence y comparación inmediatamente anterior al
cambio; backup actual y rollback verificables; ensayo de transferencia y
restauración en un servidor aislado equivalente; identidad/autenticación y privilegios del rol;
revisión del proxy, productores, timers y backup reales; aceptación completa;
y autorizaciones explícitas. Las cuarentenas y fechas desconocidas pueden
permanecer como tales si la interfaz las presenta de manera conservadora.

## Corrección de la instancia de recuperación — 27/11

En un clon local se reprodujo que `REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA public
FROM PUBLIC` impide las búsquedas. `public.normalize_text(text)` es inmutable,
pero llama a `public.unaccent(regdictionary,text)`, que PostgreSQL declara
`STABLE`: conceder solo la primera no basta. No abrir ni reiniciar la instancia
hasta que se apruebe una solución que respete el alcance de permisos autorizado
y se pruebe con la identidad real. Nunca restaurar EXECUTE general a PUBLIC.

Para desplegar una corrección futura, bajo autorización independiente: guardar
el commit, la unidad y el entorno actuales de `/srv/funkcionario/recovery-app`;
preparar y verificar en un directorio nuevo un archivo de código del commit
aprobado y sus dependencias, sin copiar secretos; ensayar el cambio y la vuelta
en un entorno aislado. Comprobar que la unidad de recuperación sigue apuntando
a esa ruta, al puerto 8001 y a la base recuperada. Una vez autorizadas y
aplicadas únicamente las concesiones individuales aprobadas, cambiar la ruta de
release de la instancia de recuperación y reiniciar exclusivamente su unidad;
no tocar `/srv/funkcionario/app`, el servicio del puerto 8000, Cloudflare ni
productores. Validar `/health`, búsqueda `garcia` con resultados, perfiles y
tablas históricas, en lugar de limitarse al estado HTTP de `/health`.

Si falla, volver a la release anterior y reiniciar solo la misma unidad. Revertir
únicamente permisos añadidos por ese cambio y registrados en el preflight, sin
revocar concesiones anteriores; la versión previa puede seguir presentando la
regresión y no debe declararse sana. Conservar ambas bases y configuraciones.
