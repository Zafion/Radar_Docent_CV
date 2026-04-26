# Despliegue en Hetzner CX43 con Cloudflare Tunnel

Arquitectura recomendada:

```text
Cloudflare
  ↓ Tunnel
cloudflared en Hetzner
  ↓ http://127.0.0.1:8000
Uvicorn / FastAPI
  ↓
PostgreSQL local en el CX43
```

## Decisiones aplicadas en este paquete

- Ruta de aplicación en producción: `/srv/funkcionario/app`
- Usuario del servicio: `funkcionario`
- Entorno persistente: `/etc/funkcionario/funkcionario.env`
- Uvicorn limitado a `127.0.0.1:8000`
- Pipeline programado en ventanas de publicación
- Sincronización de centros separada del pipeline principal
- Centros sincronizados una vez al mes, el primer sábado disponible de cada mes

## Servicios incluidos

```text
deploy/funkcionario-web.service
deploy/funkcionario-pipeline.service
deploy/funkcionario-pipeline.timer
deploy/funkcionario-centers.service
deploy/funkcionario-centers.timer
```

## Comandos base

```bash
sudo cp deploy/funkcionario-web.service /etc/systemd/system/
sudo cp deploy/funkcionario-pipeline.service /etc/systemd/system/
sudo cp deploy/funkcionario-pipeline.timer /etc/systemd/system/
sudo cp deploy/funkcionario-centers.service /etc/systemd/system/
sudo cp deploy/funkcionario-centers.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now funkcionario-web.service
sudo systemctl enable --now funkcionario-pipeline.timer
sudo systemctl enable --now funkcionario-centers.timer
```

## Comprobaciones

```bash
curl http://127.0.0.1:8000/
curl http://127.0.0.1:8000/api/push/public-key
systemctl list-timers 'funkcionario-*' --no-pager
```
