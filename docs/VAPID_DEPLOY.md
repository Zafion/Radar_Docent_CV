# VAPID fijo para web y pipeline

Objetivo: que la web y el pipeline usen siempre las mismas claves VAPID sin depender de `export ...` manuales.

## 1. Generar claves una sola vez
```bash
cd /home/zafion/Proyects/Radar_Docent_CV
source .venv/bin/activate
python run_generate_vapid_keys.py \
  --private-key-path /etc/funckcionario/secrets/vapid_private_key.pem \
  --env-output /etc/funckcionario/funckcionario.env
```

Luego edita `/etc/funckcionario/funckcionario.env` y añade también:

```bash
RADAR_DOCENT_DB_URL=postgresql://radar_docent:2107@localhost:5432/radar_docent_cv
PYTHONUNBUFFERED=1
UVICORN_HOST=127.0.0.1
UVICORN_PORT=8000
```

## 2. Instalar servicios systemd
Copia:
- `deploy/funckcionario-web.service` -> `/etc/systemd/system/funckcionario-web.service`
- `deploy/funckcionario-pipeline.service` -> `/etc/systemd/system/funckcionario-pipeline.service`
- `deploy/funckcionario-pipeline.timer` -> `/etc/systemd/system/funckcionario-pipeline.timer`

Ajusta `User`, `Group`, `WorkingDirectory` y `ExecStart` si la ruta final no es `/home/zafion/Proyects/Radar_Docent_CV`.

## 3. Recargar systemd y activar
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now funckcionario-web.service
sudo systemctl enable --now funckcionario-pipeline.timer
```

## 4. Verificar
```bash
sudo systemctl status funckcionario-web.service --no-pager
sudo systemctl status funckcionario-pipeline.timer --no-pager
curl http://127.0.0.1:8000/api/push/public-key
```

Lo esperado es:
- `configured: true`
- la clave pública real
- suscripciones push persistentes en PostgreSQL

## 5. Nota sobre el envío
La web necesita las variables VAPID para:
- exponer `/api/push/public-key`
- aceptar suscripciones

El pipeline necesita las mismas variables para:
- enviar las notificaciones cuando `run_register_documents.py` detecta documentos accionables
