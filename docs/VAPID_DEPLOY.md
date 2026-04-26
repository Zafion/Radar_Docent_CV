# VAPID fijo para web y pipeline

Objetivo: que la web y el pipeline usen siempre las mismas claves VAPID sin depender de `export ...` manuales.

Ruta recomendada en producción para Hetzner:

```bash
/srv/funkcionario/app
```

Usuario recomendado:

```bash
funkcionario
```

## 1. Generar claves una sola vez

```bash
cd /srv/funkcionario/app
source .venv/bin/activate

sudo mkdir -p /etc/funkcionario/secrets
sudo chown -R funkcionario:funkcionario /etc/funkcionario

python run_generate_vapid_keys.py \
  --private-key-path /etc/funkcionario/secrets/vapid_private_key.pem \
  --env-output /etc/funkcionario/funkcionario.env
```

Luego edita `/etc/funkcionario/funkcionario.env` y añade o revisa:

```bash
RADAR_DOCENT_DB_URL=postgresql://radar_docent:REPLACE_WITH_REAL_PASSWORD@localhost:5432/radar_docent_cv
RADAR_PUSH_VAPID_SUBJECT=mailto:zafion+funkcionario@gmail.com
PYTHONUNBUFFERED=1
UVICORN_HOST=127.0.0.1
UVICORN_PORT=8000
```

Con Cloudflare Tunnel, Uvicorn debe escuchar solo en `127.0.0.1:8000`.

## 2. Instalar servicios systemd

Copia:

```bash
sudo cp deploy/funkcionario-web.service /etc/systemd/system/funkcionario-web.service
sudo cp deploy/funkcionario-pipeline.service /etc/systemd/system/funkcionario-pipeline.service
sudo cp deploy/funkcionario-pipeline.timer /etc/systemd/system/funkcionario-pipeline.timer
sudo cp deploy/funkcionario-centers.service /etc/systemd/system/funkcionario-centers.service
sudo cp deploy/funkcionario-centers.timer /etc/systemd/system/funkcionario-centers.timer
```

Los servicios incluidos están preparados para:

```bash
User=funkcionario
Group=funkcionario
WorkingDirectory=/srv/funkcionario/app
EnvironmentFile=/etc/funkcionario/funkcionario.env
```

## 3. Recargar systemd y activar

```bash
sudo systemctl daemon-reload

sudo systemctl enable --now funkcionario-web.service
sudo systemctl enable --now funkcionario-pipeline.timer
sudo systemctl enable --now funkcionario-centers.timer
```

## 4. Verificar web

```bash
sudo systemctl status funkcionario-web.service --no-pager
curl http://127.0.0.1:8000/api/push/public-key
```

Lo esperado es:

- `configured: true`
- la clave pública real
- suscripciones push persistentes en PostgreSQL

## 5. Verificar timers

```bash
systemctl list-timers 'funkcionario-*' --no-pager
sudo systemctl status funkcionario-pipeline.timer --no-pager
sudo systemctl status funkcionario-centers.timer --no-pager
```

Para lanzar una ejecución manual:

```bash
sudo systemctl start funkcionario-pipeline.service
sudo systemctl start funkcionario-centers.service
```

## 6. Nota sobre el envío push

La web necesita las variables VAPID para:

- exponer `/api/push/public-key`
- aceptar suscripciones

El pipeline necesita las mismas variables para:

- enviar las notificaciones cuando `run_register_documents.py` detecta documentos accionables

## 7. Nota sobre Cloudflare Tunnel

La arquitectura esperada es:

```text
Cloudflare
  ↓ Tunnel
cloudflared en Hetzner
  ↓ http://127.0.0.1:8000
Uvicorn / FastAPI
  ↓
PostgreSQL local
```

No abras el puerto `8000` públicamente. Si usas Cloudflare Tunnel, el servicio local debe quedar enlazado a `127.0.0.1`.
