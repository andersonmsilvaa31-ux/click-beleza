"""Integração com o Mercado Pago (Checkout Pro) para o Click Beleza.

Configuração por variáveis de ambiente (nada fica no código):
  CLICK_BELEZA_MP_ACCESS_TOKEN     token da conta da plataforma (habilita pagamentos reais)
  CLICK_BELEZA_MP_WEBHOOK_SECRET   segredo de assinatura dos webhooks
  CLICK_BELEZA_PUBLIC_URL          URL pública HTTPS do site (retorno e webhook)
  CLICK_BELEZA_FEE_PERCENT         comissão da plataforma em % (padrão 10)

Sem o token o sistema permanece no modo demonstrativo e não cobra ninguém.
A confirmação do pagamento nunca vem do corpo do webhook: sempre é consultada na API.
"""
import hashlib
import hmac
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

API = "https://api.mercadopago.com"
APPROVED = "approved"


def access_token():
    return os.environ.get("CLICK_BELEZA_MP_ACCESS_TOKEN", "").strip()


def webhook_secret():
    return os.environ.get("CLICK_BELEZA_MP_WEBHOOK_SECRET", "").strip()


def public_url():
    return os.environ.get("CLICK_BELEZA_PUBLIC_URL", "").strip().rstrip("/")


def enabled():
    return bool(access_token() and webhook_secret() and public_url().startswith("https://"))


def fee_percent():
    try:
        value = float(os.environ.get("CLICK_BELEZA_FEE_PERCENT", "10"))
    except ValueError:
        return 10.0
    return min(max(value, 0.0), 50.0)


def marketplace_fee(price):
    return round(price * fee_percent() / 100, 2)


def _http(method, path, token, payload=None, idempotency_key=None):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if idempotency_key:
        headers["X-Idempotency-Key"] = idempotency_key
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(API + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except HTTPError as error:
        raise PaymentError(f"O Mercado Pago recusou a operação ({error.code}).") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise PaymentError("Não foi possível falar com o Mercado Pago agora.") from error


class PaymentError(RuntimeError):
    pass


# Os testes substituem este ponto para não acessar a rede.
http = _http


def create_preference(booking, seller_token=None):
    """Cria a cobrança. Com seller_token o valor vai à conta do profissional e a plataforma retém a comissão."""
    price = round(float(booking["price"]), 2)
    payload = {
        "items": [{
            "id": booking["id"], "title": f"{booking['serviceName']} - {booking['professionalName']}"[:120],
            "quantity": 1, "currency_id": "BRL", "unit_price": price,
        }],
        "external_reference": booking["id"],
        "notification_url": f"{public_url()}/api/payments/webhook",
        "back_urls": {key: f"{public_url()}/customer-orders.html?pedido={booking['id']}" for key in ("success", "pending", "failure")},
        "auto_return": "approved",
        "expires": False,
        "statement_descriptor": "CLICKBELEZA",
    }
    token = access_token()
    if seller_token:
        token = seller_token
        payload["marketplace_fee"] = marketplace_fee(price)
    result = http("POST", "/checkout/preferences", token, payload, idempotency_key=f"pref-{booking['id']}")
    return {"id": result.get("id"), "url": result.get("init_point"), "split": bool(seller_token)}


def fetch_payment(payment_id, token=None):
    safe_id = "".join(ch for ch in str(payment_id) if ch.isdigit())
    if not safe_id:
        raise PaymentError("Identificador de pagamento inválido.")
    return http("GET", f"/v1/payments/{safe_id}", token or access_token())


def refund_payment(payment_id, booking_id, token=None):
    safe_id = "".join(ch for ch in str(payment_id) if ch.isdigit())
    if not safe_id:
        raise PaymentError("Pagamento sem identificador para estorno.")
    return http("POST", f"/v1/payments/{safe_id}/refunds", token or access_token(), {}, idempotency_key=f"refund-{booking_id}")


def verify_signature(headers, data_id, secret=None, now=None, tolerance=600):
    """Valida o cabeçalho x-signature (HMAC-SHA256) do webhook do Mercado Pago."""
    secret = secret or webhook_secret()
    signature = headers.get("x-signature", "")
    if not secret or not signature or not data_id:
        return False
    parts = dict(item.strip().split("=", 1) for item in signature.split(",") if "=" in item)
    timestamp, received = parts.get("ts", ""), parts.get("v1", "")
    if not timestamp.isdigit() or not received:
        return False
    stamp = int(timestamp)
    stamp = stamp / 1000 if stamp > 10**11 else stamp
    if abs((now or time.time()) - stamp) > tolerance:
        return False
    manifest = f"id:{str(data_id).lower()};request-id:{headers.get('x-request-id', '')};ts:{timestamp};"
    expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)


# ---- Conexão do profissional (OAuth do Mercado Pago) para repasse automático ----
OAUTH_AUTH_URL = "https://auth.mercadopago.com/authorization"
STATE_TTL = 900


def client_id():
    return os.environ.get("CLICK_BELEZA_MP_CLIENT_ID", "").strip()


def client_secret():
    return os.environ.get("CLICK_BELEZA_MP_CLIENT_SECRET", "").strip()


def _fernet():
    key = os.environ.get("CLICK_BELEZA_ENCRYPTION_KEY", "").strip()
    if not key:
        return None
    from cryptography.fernet import Fernet
    return Fernet(key.encode())


def oauth_enabled():
    return bool(client_id() and client_secret() and public_url().startswith("https://") and _fernet())


def encrypt(value):
    fernet = _fernet()
    if fernet is None:
        raise PaymentError("Defina CLICK_BELEZA_ENCRYPTION_KEY para guardar credenciais de forma segura.")
    return fernet.encrypt(value.encode()).decode()


def decrypt(value):
    fernet = _fernet()
    if fernet is None or not value:
        return None
    from cryptography.fernet import InvalidToken
    try:
        return fernet.decrypt(value.encode()).decode()
    except InvalidToken:
        return None


def redirect_uri():
    return f"{public_url()}/api/payments/oauth/callback"


def make_state(user_id, nonce, now=None):
    expires = int((now or time.time()) + STATE_TTL)
    body = f"{user_id}.{nonce}.{expires}"
    signature = hmac.new(client_secret().encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def read_state(state, now=None):
    """Devolve (user_id, nonce) se a assinatura e o prazo forem válidos; senão None."""
    parts = str(state or "").split(".")
    if len(parts) != 4 or not client_secret():
        return None
    user_id, nonce, expires, signature = parts
    expected = hmac.new(client_secret().encode(), f"{user_id}.{nonce}.{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature) or not expires.isdigit() or int(expires) < (now or time.time()):
        return None
    return user_id, nonce


def authorization_url(state):
    from urllib.parse import urlencode
    query = urlencode({"client_id": client_id(), "response_type": "code", "platform_id": "mp",
                       "state": state, "redirect_uri": redirect_uri()})
    return f"{OAUTH_AUTH_URL}?{query}"


def _seller_record(result):
    if not result.get("access_token"):
        raise PaymentError("O Mercado Pago não autorizou a conexão.")
    return {
        "token": encrypt(result["access_token"]),
        "refresh": encrypt(result["refresh_token"]) if result.get("refresh_token") else None,
        "mpUserId": str(result.get("user_id", "")),
        "expiresAt": time.time() + int(result.get("expires_in") or 0),
        "connectedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def exchange_code(code):
    result = http("POST", "/oauth/token", client_secret(), {
        "client_id": client_id(), "client_secret": client_secret(), "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri(),
    })
    return _seller_record(result)


def refresh_seller(record):
    refresh = decrypt(record.get("refresh"))
    if not refresh:
        raise PaymentError("Conexão expirada; o profissional precisa conectar novamente.")
    result = http("POST", "/oauth/token", client_secret(), {
        "client_id": client_id(), "client_secret": client_secret(), "grant_type": "refresh_token",
        "refresh_token": refresh,
    })
    renewed = _seller_record(result)
    renewed["connectedAt"] = record.get("connectedAt", renewed["connectedAt"])
    return renewed
