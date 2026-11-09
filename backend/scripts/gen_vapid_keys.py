"""앱(PWA) 알림용 VAPID 키 한 쌍을 만든다 — 한 번 만들어 Render 환경변수에 넣고 바꾸지 않는다.

    python backend/scripts/gen_vapid_keys.py

키를 바꾸면 이미 알림을 켠 기기의 구독이 모두 무효가 된다(푸시 서비스가 403 으로 거절 → 서버가 지움).
사용자가 앱에서 알림을 다시 켜야 하므로, 새 키는 처음 한 번만 만든다.
"""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


key = ec.generate_private_key(ec.SECP256R1())
private = key.private_numbers().private_value.to_bytes(32, "big")
public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
print(f"VAPID_PRIVATE_KEY={b64u(private)}")
print(f"# 공개키(서버가 비밀키에서 계산하므로 따로 넣지 않아도 된다): {b64u(public)}")
