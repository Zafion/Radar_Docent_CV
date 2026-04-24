from __future__ import annotations

import argparse
import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid01


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera claves VAPID para notificaciones push."
    )
    parser.add_argument(
        "--private-key-path",
        default="data/secrets/vapid_private_key.pem",
        help="Ruta donde guardar la clave privada en formato PEM.",
    )
    parser.add_argument(
        "--subject",
        default="mailto:funkcionarios@gmail.com",
        help="Valor sub para VAPID.",
    )
    parser.add_argument(
        "--env-output",
        default="data/secrets/vapid.env",
        help="Ruta donde guardar un bloque listo para source / EnvironmentFile.",
    )
    args = parser.parse_args()

    key = Vapid01()
    key.generate_keys()

    private_pem = key.private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    public_raw = key.public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = b64url(public_raw)

    private_key_path = Path(args.private_key_path)
    private_key_path.parent.mkdir(parents=True, exist_ok=True)
    private_key_path.write_bytes(private_pem)

    env_output_path = Path(args.env_output)
    env_output_path.parent.mkdir(parents=True, exist_ok=True)
    env_output_path.write_text(
        "\n".join(
            [
                f'RADAR_PUSH_VAPID_PUBLIC_KEY="{public_b64}"',
                f'RADAR_PUSH_VAPID_PRIVATE_KEY="{private_key_path.resolve()}"',
                f'RADAR_PUSH_VAPID_SUBJECT="{args.subject}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"Clave privada guardada en: {private_key_path.resolve()}")
    print(f"Snippet env guardado en: {env_output_path.resolve()}")
    print()
    print(f'RADAR_PUSH_VAPID_PUBLIC_KEY="{public_b64}"')
    print(f'RADAR_PUSH_VAPID_PRIVATE_KEY="{private_key_path.resolve()}"')
    print(f'RADAR_PUSH_VAPID_SUBJECT="{args.subject}"')


if __name__ == "__main__":
    main()
