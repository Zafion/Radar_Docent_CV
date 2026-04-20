import base64

from cryptography.hazmat.primitives.asymmetric import ec


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


private_key = ec.generate_private_key(ec.SECP256R1())
private_numbers = private_key.private_numbers()
public_numbers = private_key.public_key().public_numbers()

private_bytes = private_numbers.private_value.to_bytes(32, "big")
public_bytes = (
    b"\x04"
    + public_numbers.x.to_bytes(32, "big")
    + public_numbers.y.to_bytes(32, "big")
)

print("RADAR_PUSH_VAPID_PUBLIC_KEY=" + b64url(public_bytes))
print("RADAR_PUSH_VAPID_PRIVATE_KEY=" + b64url(private_bytes))
print("RADAR_PUSH_VAPID_SUBJECT=mailto:funkcionarios@gmail.com")
