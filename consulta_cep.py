import json
import base64
import binascii
import hashlib
import hmac
import math
import os
import re
import secrets
import threading
import time
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import psycopg


APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "click_beleza_data.json"
DATABASE_URL = os.environ.get("CLICK_BELEZA_DATABASE_URL")
HOST = os.environ.get("CLICK_BELEZA_HOST", "127.0.0.1")
PORT = int(os.environ.get("CLICK_BELEZA_PORT", "8765"))
COOKIE_SECURE = os.environ.get("CLICK_BELEZA_SECURE", "").lower() in {"1", "true", "yes"}
TRUST_PROXY = os.environ.get("CLICK_BELEZA_TRUST_PROXY", "").lower() in {"1", "true", "yes"}
COOKIE_FLAGS = "; Secure" if COOKIE_SECURE else ""
RATE_LIMIT_PER_MINUTE = int(os.environ.get("CLICK_BELEZA_RATE_LIMIT", "240"))
RATE_WINDOW_SECONDS = 60
RATE_HITS = {}
PRIVACY_REQUEST_TYPES = {"acesso", "correcao", "exclusao", "reclamacao"}
RATE_LOCK = threading.Lock()
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' data: blob:; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
PILOT_REGION = "Marília, SP"
class DataLock:
    """Trava reentrante entre threads e, com PostgreSQL, entre processos (advisory lock)."""
    ADVISORY_KEY = 727_001

    def __init__(self):
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._connection = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            if self._depth == 0 and DATABASE_URL:
                self._connection = psycopg.connect(DATABASE_URL, connect_timeout=5, autocommit=True)
                self._connection.execute("SELECT pg_advisory_lock(%s)", (self.ADVISORY_KEY,))
            self._depth += 1
        except psycopg.Error as error:
            self._close()
            self._thread_lock.release()
            raise RuntimeError("Não foi possível obter a trava do banco de dados.") from error
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def _close(self):
        if self._connection is not None:
            try:
                self._connection.close()
            finally:
                self._connection = None

    def __exit__(self, *_):
        self._depth -= 1
        try:
            if self._depth == 0:
                self._close()
        finally:
            self._thread_lock.release()
        return False


DATA_LOCK = DataLock()
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/favicon.ico": ("favicon.svg", "image/svg+xml"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/professionals.html": ("professionals.html", "text/html; charset=utf-8"),
    "/professional.html": ("professional.html", "text/html; charset=utf-8"),
    "/register.html": ("register.html", "text/html; charset=utf-8"),
    "/agenda.html": ("agenda.html", "text/html; charset=utf-8"),
    "/services.html": ("services.html", "text/html; charset=utf-8"),
    "/anunciar.html": ("anunciar.html", "text/html; charset=utf-8"),
    "/login.html": ("login.html", "text/html; charset=utf-8"),
    "/termos.html": ("termos.html", "text/html; charset=utf-8"),
    "/privacidade.html": ("privacidade.html", "text/html; charset=utf-8"),
    "/legal.js": ("legal.js", "text/javascript; charset=utf-8"),
    "/customer-orders.html": ("customer-orders.html", "text/html; charset=utf-8"),
    "/admin.html": ("admin.html", "text/html; charset=utf-8"),
    "/professional-dashboard.html": ("professional-dashboard.html", "text/html; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/account.js": ("account.js", "text/javascript; charset=utf-8"),
    "/api.js": ("api.js", "text/javascript; charset=utf-8"),
    "/assets/inspiracao-unhas.jpg": ("assets/inspiracao-unhas.jpg", "image/jpeg"),
    "/assets/inspiracao-make.jpg": ("assets/inspiracao-make.jpg", "image/jpeg"),
    "/assets/inspiracao-cabelo.jpg": ("assets/inspiracao-cabelo.jpg", "image/jpeg"),
    "/assets/inspiracao-autocuidado.jpg": ("assets/inspiracao-autocuidado.jpg", "image/jpeg"),
}
OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
CEP_CACHE_SECONDS = 24 * 60 * 60
OVERPASS_CACHE_SECONDS = 10 * 60
CACHE_MAX_ENTRIES = 200
CACHE_LOCK = threading.Lock()
CACHE = {}
PASSWORD_ITERATIONS = 310_000
SESSION_TTL_SECONDS = 8 * 60 * 60
SESSION_COOKIE = "click_beleza_session"
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCKOUT_SECONDS = 15 * 60
LOGIN_ATTEMPTS = {}
LOGIN_LOCK = threading.Lock()


def cache_get(key):
    with CACHE_LOCK:
        entry = CACHE.get(key)
        if entry and entry[0] > time.monotonic():
            return deepcopy(entry[1])
        CACHE.pop(key, None)
    return None


def cache_set(key, value, seconds):
    with CACHE_LOCK:
        if len(CACHE) >= CACHE_MAX_ENTRIES:
            now = time.monotonic()
            for stale in [name for name, entry in CACHE.items() if entry[0] <= now]:
                del CACHE[stale]
            if len(CACHE) >= CACHE_MAX_ENTRIES:
                CACHE.pop(next(iter(CACHE)))
        CACHE[key] = (time.monotonic() + seconds, deepcopy(value))


def clear_caches():
    with CACHE_LOCK:
        CACHE.clear()


class CepLookupError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def fetch_json(url, timeout=8):
    request = Request(url, headers={"User-Agent": "ClickBeleza/1.0 (local prototype)", "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _cep_from_viacep(cep):
    data = fetch_json(f"https://viacep.com.br/ws/{cep}/json/")
    if not isinstance(data, dict):
        raise ValueError("resposta inválida")
    if data.get("erro"):
        return None
    return {
        "cep": data.get("cep", ""), "logradouro": data.get("logradouro", ""),
        "complemento": data.get("complemento", ""), "bairro": data.get("bairro", ""),
        "localidade": data.get("localidade", ""), "uf": data.get("uf", ""), "ddd": data.get("ddd", ""),
        "lat": None, "lon": None, "source": "ViaCEP",
    }


def _cep_from_brasilapi(cep):
    data = fetch_json(f"https://brasilapi.com.br/api/cep/v2/{cep}")
    if not isinstance(data, dict) or not data.get("city"):
        raise ValueError("resposta inválida")
    coordinates = (data.get("location") or {}).get("coordinates") or {}
    try:
        lat, lon = float(coordinates["latitude"]), float(coordinates["longitude"])
    except (KeyError, TypeError, ValueError):
        lat = lon = None
    return {
        "cep": f"{cep[:5]}-{cep[5:]}", "logradouro": data.get("street") or "", "complemento": "",
        "bairro": data.get("neighborhood") or "", "localidade": data.get("city") or "",
        "uf": data.get("state") or "", "ddd": "", "lat": lat, "lon": lon, "source": "BrasilAPI",
    }


def _geocode_address(address):
    # Melhor esforço: sem coordenadas, a busca segue por nome da cidade.
    parts = [address["logradouro"], address["bairro"], address["localidade"], address["uf"], "Brasil"]
    full_query = ", ".join(part for part in parts if part)
    for query, precision in ((full_query, "endereço"), (f'{address["localidade"]}, {address["uf"]}, Brasil', "cidade")):
        try:
            places = fetch_json(
                "https://nominatim.openstreetmap.org/search?" + urlencode({"q": query, "format": "jsonv2", "limit": 1, "countrycodes": "br"}),
                timeout=5,
            )
            if isinstance(places, list) and places:
                return float(places[0]["lat"]), float(places[0]["lon"]), precision
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            return None
    return None


def lookup_cep(raw_cep):
    cep = re.sub(r"\D", "", str(raw_cep))
    if not re.fullmatch(r"\d{8}", cep):
        raise CepLookupError(400, "Informe um CEP com 8 números.")
    cached = cache_get(("cep", cep))
    if cached:
        return cached
    address = None
    errors = []
    for provider in (_cep_from_viacep, _cep_from_brasilapi):
        try:
            address = provider(cep)
        except HTTPError as error:
            if error.code == 404 or (provider is _cep_from_viacep and error.code == 400):
                raise CepLookupError(404, "CEP não encontrado.") from None
            errors.append(error)
            continue
        except (TimeoutError, URLError, OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            errors.append(error)
            continue
        if address is None:
            raise CepLookupError(404, "CEP não encontrado.")
        break
    if address is None:
        raise CepLookupError(502, "Não foi possível consultar o CEP agora. Verifique a internet e tente novamente.")
    address["precision"] = "cep" if address["lat"] is not None else None
    if address["lat"] is None and address["localidade"]:
        located = _geocode_address(address)
        if located:
            address["lat"], address["lon"], address["precision"] = located
    cache_set(("cep", cep), address, CEP_CACHE_SECONDS)
    return address


VALID_STATUSES = {"solicitado", "confirmado", "concluido", "cancelado"}
ACTIVE_BOOKING_STATUSES = ("solicitado", "confirmado")
# Janelas de atendimento em minutos desde 00:00; o intervalo de 12h às 13h fica fechado.
WORKING_WINDOWS = ((9 * 60, 12 * 60), (13 * 60, 18 * 60))
SLOT_STEP_MINUTES = 30
DEFAULT_SERVICE_MINUTES = 60
try:
    BUSINESS_TZ = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    BUSINESS_TZ = timezone(timedelta(hours=-3))


def business_now():
    return datetime.now(BUSINESS_TZ)


def to_minutes(clock):
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def service_minutes(service):
    try:
        return max(15, min(int(service.get("duration", DEFAULT_SERVICE_MINUTES)), 600))
    except (TypeError, ValueError):
        return DEFAULT_SERVICE_MINUTES


def busy_intervals(bookings, professional_id, day):
    intervals = []
    for booking in bookings:
        if (
            booking.get("professionalId") == professional_id
            and booking.get("date") == day
            and booking.get("status") in ACTIVE_BOOKING_STATUSES
        ):
            start = to_minutes(booking["time"])
            intervals.append((start, start + int(booking.get("duration") or DEFAULT_SERVICE_MINUTES)))
    return intervals


def available_start_times(bookings, professional_id, day, duration):
    """Horários de início livres: o serviço inteiro cabe numa janela e não colide com reservas ativas."""
    now = business_now()
    today = now.date().isoformat()
    earliest = now.hour * 60 + now.minute if day == today else 0
    busy = busy_intervals(bookings, professional_id, day)
    times = []
    for window_start, window_end in WORKING_WINDOWS:
        for start in range(window_start, window_end - duration + 1, SLOT_STEP_MINUTES):
            if start <= earliest and day == today:
                continue
            if any(start < busy_end and busy_start < start + duration for busy_start, busy_end in busy):
                continue
            times.append(f"{start // 60:02d}:{start % 60:02d}")
    return times
SERVICE_LABELS = {
    "cabeleireiro": "cabelo", "barbeiro": "barbeiro barbearia", "manicure": "manicure",
    "pedicure": "pedicure", "sobrancelhas": "design de sobrancelhas",
    "cilios": "cílios", "maquiagem": "maquiagem", "estetica": "estética",
    "massagem": "massagem", "depilacao": "depilação", "tatuagem": "tatuagem",
    "piercing": "piercing", "tatuagem_piercing": "tatuagem e piercing",
}

SAMPLE_PROFESSIONALS = [
    {
        "id": "demo-marilia-barbeiro", "name": "Barbearia Exemplo Marília", "service": "barbeiro",
        "description": "Perfil demonstrativo para testar a busca, os serviços e os agendamentos do Click Beleza. Não representa um estabelecimento real.",
        "address": "Endereço ilustrativo — região central", "neighborhood": "Centro", "city": "Marília",
        "lat": -22.2175, "lon": -49.9505, "rating": 4.8, "reviewCount": 12,
        "color": "#c6d0c2", "initials": "BE", "demo": True,
        "services": [{"name": "Corte masculino", "price": 45, "duration": 40}, {"name": "Corte e barba", "price": 75, "duration": 65}],
        "reviews": [{"rating": 5, "customerName": "Avaliação exemplo", "comment": "Perfil demonstrativo."}],
    },
    {
        "id": "demo-marilia-cabelo", "name": "Studio Exemplo Beleza", "service": "cabeleireiro",
        "description": "Perfil demonstrativo para testar a busca, os serviços e os agendamentos do Click Beleza. Não representa um estabelecimento real.",
        "address": "Endereço ilustrativo — região central", "neighborhood": "Centro", "city": "Marília",
        "lat": -22.2100, "lon": -49.9400, "rating": 4.9, "reviewCount": 18,
        "color": "#f4d0c5", "initials": "SE", "demo": True,
        "services": [{"name": "Corte e escova", "price": 65, "duration": 60}, {"name": "Hidratação", "price": 80, "duration": 60}],
        "reviews": [],
    },
    {
        "id": "demo-marilia-manicure", "name": "Ateliê Exemplo de Unhas", "service": "manicure",
        "description": "Perfil demonstrativo para testar a busca, os serviços e os agendamentos do Click Beleza. Não representa um estabelecimento real.",
        "address": "Endereço ilustrativo — região central", "neighborhood": "Jardim Tangará", "city": "Marília",
        "lat": -22.2250, "lon": -49.9600, "rating": 4.7, "reviewCount": 9,
        "color": "#f4d0c5", "initials": "AU", "demo": True,
        "services": [{"name": "Manicure tradicional", "price": 40, "duration": 45}, {"name": "Esmaltação em gel", "price": 75, "duration": 70}],
        "reviews": [],
    },
    {
        "id": "demo-camila", "name": "Ateliê Camila", "service": "manicure",
        "description": "Unhas cuidadas com atenção aos detalhes. Este perfil e seus dados são apenas ilustrativos.",
        "address": "Rua Augusta, 1000", "neighborhood": "Consolação", "city": "São Paulo",
        "lat": -23.5532, "lon": -46.6584, "rating": 4.9, "reviewCount": 28,
        "color": "#f4d0c5", "initials": "AC", "demo": True,
        "services": [{"name": "Manicure tradicional", "price": 45, "duration": 45}, {"name": "Esmaltação em gel", "price": 85, "duration": 75}],
        "reviews": [{"rating": 5, "customerName": "Cliente exemplo", "comment": "Atendimento cuidadoso e resultado lindo."}],
    },
    {
        "id": "demo-rafael", "name": "Rafael Studio Barber", "service": "barbeiro",
        "description": "Cortes clássicos e modernos. Este perfil e seus dados são apenas ilustrativos.",
        "address": "Rua Haddock Lobo, 450", "neighborhood": "Cerqueira César", "city": "São Paulo",
        "lat": -23.5571, "lon": -46.6622, "rating": 4.8, "reviewCount": 19,
        "color": "#c6d0c2", "initials": "RS", "demo": True,
        "services": [{"name": "Corte masculino", "price": 60, "duration": 45}, {"name": "Corte e barba", "price": 95, "duration": 70}],
        "reviews": [],
    },
    {
        "id": "demo-luiza", "name": "Studio Luiza Pele", "service": "sobrancelhas",
        "description": "Design de sobrancelhas e cuidados faciais. Este perfil e seus dados são apenas ilustrativos.",
        "address": "Alameda Santos, 700", "neighborhood": "Jardim Paulista", "city": "São Paulo",
        "lat": -23.5651, "lon": -46.6508, "rating": 4.7, "reviewCount": 14,
        "color": "#e7d5b6", "initials": "SL", "demo": True,
        "services": [{"name": "Design de sobrancelhas", "price": 55, "duration": 40}, {"name": "Design com henna", "price": 75, "duration": 50}],
        "reviews": [],
    },
]


def _validate_data(data):
    if not isinstance(data, dict) or not isinstance(data.get("professionals"), list) or not isinstance(data.get("bookings"), list):
        raise RuntimeError("Os dados do Click Beleza têm formato inválido.")
    data.setdefault("users", [])
    data.setdefault("sessions", [])
    data.setdefault("chats", [])
    data.setdefault("partners", [])
    data.setdefault("financialTransactions", [])
    data.setdefault("privacyRequests", [])
    if not isinstance(data["financialTransactions"], list):
        raise RuntimeError("Os registros financeiros do Click Beleza têm formato inválido.")
    return data


def _load_file_data():
    if not DATA_FILE.exists():
        return {"professionals": deepcopy(SAMPLE_PROFESSIONALS), "bookings": []}
    try:
        with DATA_FILE.open("r", encoding="utf-8") as data_file:
            return _validate_data(json.load(data_file))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Não foi possível ler {DATA_FILE.name}: {error}") from error


def booking_slot_points(clock, duration):
    start = to_minutes(clock)
    return list(range(start, start + duration, SLOT_STEP_MINUTES))


def reserve_booking_slots(professional_id, day, clock, duration, booking_id):
    """Reserva as faixas de 30 min no banco; a chave primária impede sobreposição mesmo fora desta trava."""
    if not DATABASE_URL:
        return True
    ensure_schema()
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.executemany(
                    "INSERT INTO booking_slots (professional_id, day, slot, booking_id) VALUES (%s, %s, %s, %s)",
                    [(professional_id, day, point, booking_id) for point in booking_slot_points(clock, duration)],
                )
        return True
    except psycopg.errors.UniqueViolation:
        return False
    except psycopg.Error as error:
        raise RuntimeError("Não foi possível reservar o horário no banco de dados.") from error


def release_booking_slots(booking_id):
    if not DATABASE_URL:
        return
    ensure_schema()
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM booking_slots WHERE booking_id = %s", (booking_id,))
    except psycopg.Error as error:
        raise RuntimeError("Não foi possível liberar o horário no banco de dados.") from error


_SCHEMA_READY = set()
_SCHEMA_LOCK = threading.Lock()


def ensure_schema():
    """Cria as tabelas uma vez por URL, com trava; CREATE TABLE IF NOT EXISTS concorrente falha no PostgreSQL."""
    key = DATABASE_URL
    if not key or key in _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if key in _SCHEMA_READY:
            return
        try:
            with psycopg.connect(key, connect_timeout=5, autocommit=True) as connection:
                connection.execute("SELECT pg_advisory_lock(%s)", (DataLock.ADVISORY_KEY + 1,))
                try:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS click_beleza_state (
                            id SMALLINT PRIMARY KEY CHECK (id = 1),
                            payload JSONB NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS booking_slots (
                            professional_id TEXT NOT NULL,
                            day DATE NOT NULL,
                            slot SMALLINT NOT NULL CHECK (slot BETWEEN 0 AND 1439),
                            booking_id TEXT NOT NULL,
                            PRIMARY KEY (professional_id, day, slot)
                        )
                        """
                    )
                    connection.execute("CREATE INDEX IF NOT EXISTS booking_slots_booking_id ON booking_slots (booking_id)")
                finally:
                    connection.execute("SELECT pg_advisory_unlock(%s)", (DataLock.ADVISORY_KEY + 1,))
        except psycopg.Error as error:
            raise RuntimeError("Não foi possível preparar as tabelas do banco de dados.") from error
        _SCHEMA_READY.add(key)


def _ensure_database_state(cursor):
    cursor.execute("SELECT payload FROM click_beleza_state WHERE id = 1")
    row = cursor.fetchone()
    if row is None:
        initial_data = _load_file_data()
        cursor.execute(
            """
            INSERT INTO click_beleza_state (id, payload)
            VALUES (1, %s::jsonb)
            ON CONFLICT (id) DO NOTHING
            """,
            (json.dumps(initial_data, ensure_ascii=False),),
        )


def load_data():
    with DATA_LOCK:
        if not DATABASE_URL:
            return _load_file_data()
        try:
            ensure_schema()
            with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
                with connection.cursor() as cursor:
                    _ensure_database_state(cursor)
                    cursor.execute("SELECT payload FROM click_beleza_state WHERE id = 1")
                    return _validate_data(cursor.fetchone()[0])
        except psycopg.Error as error:
            raise RuntimeError(
                "Não foi possível conectar ao PostgreSQL do Click Beleza. "
                "Verifique o serviço, a URL e a senha local do banco."
            ) from error


def save_data(data):
    with DATA_LOCK:
        _validate_data(data)
        if DATABASE_URL:
            ensure_schema()
            try:
                with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
                    with connection.cursor() as cursor:
                        _ensure_database_state(cursor)
                        cursor.execute(
                            """
                            UPDATE click_beleza_state SET payload = %s::jsonb WHERE id = 1
                            """,
                            (json.dumps(data, ensure_ascii=False),),
                        )
                return
            except psycopg.Error as error:
                raise RuntimeError(
                    "Não foi possível salvar os dados no PostgreSQL do Click Beleza. "
                    "Verifique o serviço e as permissões do banco."
                ) from error
        temporary_file = DATA_FILE.with_suffix(".tmp")
        try:
            with temporary_file.open("w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
            temporary_file.replace(DATA_FILE)
        except OSError as error:
            raise RuntimeError(f"Não foi possível salvar os dados: {error}") from error


def public_professional(professional, include_reviews=True, include_portfolio=False):
    fields = ("id", "name", "service", "description", "address", "neighborhood",
              "city", "lat", "lon", "rating", "reviewCount", "color", "initials",
              "demo", "services")
    result = {field: professional.get(field) for field in fields}
    result["profileCompletion"] = professional_completion(professional)
    if include_reviews:
        result["reviews"] = professional.get("reviews", [])
    if include_portfolio:
        result["portfolio"] = professional.get("portfolio", [])
    return result


def professional_completion(professional):
    services = professional.get("services", [])
    checks = (
        bool(professional.get("name")),
        bool(professional.get("service")),
        bool(professional.get("city")),
        bool(professional.get("description")),
        any(item.get("name") and item.get("price") for item in services),
        bool(professional.get("portfolio")),
    )
    return round(sum(checks) * 100 / len(checks))


def haversine_km(lat1, lon1, lat2, lon2):
    earth_radius_km = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    value = math.sin(delta_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    return 2 * earth_radius_km * math.atan2(math.sqrt(value), math.sqrt(1 - value))


PARTNER_CATEGORIES = {
    "cosmeticos": "Cosméticos", "cabelo": "Produtos para cabelo", "unhas": "Unhas e esmaltes",
    "barba": "Barba e barbearia", "perfumaria": "Perfumaria", "estetica": "Estética e skincare",
    "clinica": "Clínica / spa", "outros": "Outros",
}
PARTNER_PLANS = {
    "basico": {"label": "Básico", "price": 49, "rank": 1},
    "destaque": {"label": "Destaque", "price": 99, "rank": 2},
    "premium": {"label": "Premium", "price": 199, "rank": 3},
}
SAMPLE_PARTNERS = [
    {"id": "demo-pura", "name": "Beleza Pura Cosméticos", "category": "cosmeticos", "description": "Maquiagem e skincare com entrega rápida em Marília.", "url": "", "plan": "premium", "color": "#1fe36a", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-01"},
    {"id": "demo-hairpro", "name": "Hair Pro Store", "category": "cabelo", "description": "Shampoos, tintas e ferramentas para profissionais.", "url": "", "plan": "destaque", "color": "#5df59b", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-02"},
    {"id": "demo-nail", "name": "Nail Lab Suprimentos", "category": "unhas", "description": "Esmaltes, gel e acessórios para nail designers.", "url": "", "plan": "destaque", "color": "#a6f7c8", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-03"},
    {"id": "demo-barba", "name": "Barba & Cia", "category": "barba", "description": "Pomadas, óleos e máquinas para barbearias.", "url": "", "plan": "basico", "color": "#7ff0ad", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-04"},
    {"id": "demo-essencia", "name": "Essência Aromas", "category": "perfumaria", "description": "Perfumes e hidratantes para presente.", "url": "", "plan": "basico", "color": "#c8ffe0", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-05"},
    {"id": "demo-bem-estar", "name": "Loja Exemplo de Bem-estar", "category": "estetica", "description": "Produtos de autocuidado. Marca ilustrativa para demonstração.", "url": "", "plan": "basico", "color": "#9cefc0", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-06"},
    {"id": "demo-studio-pro", "name": "Studio Pro Exemplo", "category": "clinica", "description": "Produtos profissionais de beleza. Marca ilustrativa para demonstração.", "url": "", "plan": "basico", "color": "#7ce3a6", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-07"},
    {"id": "demo-cuidado-diario", "name": "Cuidado Diário Exemplo", "category": "cosmeticos", "description": "Cosméticos e itens de cuidado pessoal. Marca ilustrativa para demonstração.", "url": "", "plan": "basico", "color": "#b1f2cc", "status": "active", "clicks": 0, "demo": True, "createdAt": "2026-01-08"},
]


def get_partners(data):
    if not data.get("partners"):
        data["partners"] = deepcopy(SAMPLE_PARTNERS)
    return data["partners"]


def public_partner(partner):
    fields = ("id", "name", "category", "description", "url", "plan", "color", "demo")
    result = {field: partner.get(field) for field in fields}
    result["categoryLabel"] = PARTNER_CATEGORIES.get(partner.get("category"), "Outros")
    result["planLabel"] = PARTNER_PLANS.get(partner.get("plan"), PARTNER_PLANS["basico"])["label"]
    return result


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def password_record(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return {
        "passwordSalt": salt.hex(),
        "passwordHash": digest.hex(),
        "passwordIterations": PASSWORD_ITERATIONS,
    }


def verify_password(password, user):
    try:
        salt = bytes.fromhex(user["passwordSalt"])
        expected = bytes.fromhex(user["passwordHash"])
        iterations = int(user["passwordIterations"])
    except (KeyError, TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def normalize_cpf(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9.\-\s]+", value):
        raise ValueError("Informe um CPF válido.")
    digits = re.sub(r"[^0-9]", "", value)
    if len(digits) != 11 or len(set(digits)) == 1:
        raise ValueError("Informe um CPF válido.")
    numbers = [int(char) for char in digits]
    first = (sum(number * weight for number, weight in zip(numbers[:9], range(10, 1, -1))) * 10) % 11
    first = 0 if first == 10 else first
    second = (sum(number * weight for number, weight in zip(numbers[:10], range(11, 1, -1))) * 10) % 11
    second = 0 if second == 10 else second
    if numbers[9:] != [first, second]:
        raise ValueError("Informe um CPF válido.")
    return digits


def private_identifier_record(value):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", value.encode("ascii"), salt, PASSWORD_ITERATIONS)
    return {
        "cpfSalt": salt.hex(),
        "cpfHash": digest.hex(),
        "cpfIterations": PASSWORD_ITERATIONS,
    }


def verify_private_identifier(value, user):
    try:
        salt = bytes.fromhex(user["cpfSalt"])
        expected = bytes.fromhex(user["cpfHash"])
        iterations = int(user["cpfIterations"])
    except (KeyError, TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", value.encode("ascii"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def public_user(user, professional=None):
    result = {
        "id": user["id"],
        "email": user["email"],
        "role": user["role"],
        "professionalId": user.get("professionalId"),
        "active": user.get("active", True),
    }
    if professional is not None:
        result["professionalName"] = professional.get("name", "")
    return result


def public_booking(booking):
    return {key: value for key, value in booking.items() if key != "customerTokenHash"}


def customer_key(email, phone, name):
    if isinstance(email, str) and email.strip():
        return f"email:{email.strip().casefold()}"
    if isinstance(phone, str) and phone.strip():
        phone_digits = re.sub(r"\D", "", phone)
        return f"phone:{phone_digits}"
    return f"name:{name.strip().casefold()}" if isinstance(name, str) and name.strip() else "unknown"


def new_user(email, password, role, professional_id=None, cpf=None):
    return {
        "id": uuid.uuid4().hex,
        "email": email.strip().casefold(),
        "role": role,
        "professionalId": professional_id,
        "active": True,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **(private_identifier_record(cpf) if cpf else {}),
        **password_record(password),
    }


def canonical_api_path(path):
    if path == "/api/lojas":
        return "/api/partners"
    if path.startswith("/api/lojas/"):
        return "/api/partners/" + path[len("/api/lojas/"):]

    if path == "/api/profissionais":
        return "/api/professionals"
    if path.startswith("/api/profissionais/"):
        suffix = path[len("/api/profissionais/"):]
        suffix = re.sub(r"/editar$", "/edit", suffix)
        return "/api/professionals/" + suffix
    if path == "/api/agendamentos":
        return "/api/bookings"
    if path.startswith("/api/agendamentos/"):
        suffix = path[len("/api/agendamentos/"):]
        suffix = re.sub(r"/pagamento$", "/payment", suffix)
        return "/api/bookings/" + suffix
    return path


def client_ip(handler):
    """IP do cliente; só confia em X-Forwarded-For quando há proxy reverso declarado."""
    if TRUST_PROXY:
        forwarded = handler.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()[:64]
    return handler.client_address[0]


def rate_limited(key, limit, window=RATE_WINDOW_SECONDS):
    """Janela deslizante simples por chave; devolve True quando excedeu."""
    now = time.monotonic()
    with RATE_LOCK:
        hits = [moment for moment in RATE_HITS.get(key, ()) if now - moment < window]
        if len(hits) >= limit:
            RATE_HITS[key] = hits
            return True
        hits.append(now)
        RATE_HITS[key] = hits
        if len(RATE_HITS) > 20_000:
            for stale in [name for name, items in RATE_HITS.items() if not items or now - items[-1] >= window]:
                RATE_HITS.pop(stale, None)
    return False


class AppHandler(BaseHTTPRequestHandler):
    timeout = 30

    def _security_headers(self):
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store")
        if COOKIE_SECURE:
            self.send_header("Strict-Transport-Security", "max-age=63072000; includeSubDomains")

    def _send(self, status, content_type, body):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, status, payload, headers=None):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self._security_headers()
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Tamanho de solicitação inválido.") from None
        if length <= 0 or length > 28 * 1024 * 1024:
            raise ValueError("Envie dados válidos com até 28 MB.")
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("O conteúdo enviado não é um JSON válido.") from None
        if not isinstance(value, dict):
            raise ValueError("O conteúdo enviado precisa ser um objeto JSON.")
        return value

    def _current_user(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except (CookieError, TypeError, ValueError):
            return None
        session_cookie = cookie.get(SESSION_COOKIE)
        if not session_cookie:
            return None
        digest = token_hash(session_cookie.value)
        data = load_data()
        session = next((item for item in data["sessions"] if secrets.compare_digest(item.get("tokenHash", ""), digest)), None)
        if not session or session.get("expiresAt", 0) <= time.time():
            return None
        user = next((item for item in data["users"] if item["id"] == session.get("userId")), None)
        return user if user and user.get("active", True) else None

    def _require_user(self, roles=None):
        try:
            user = self._current_user()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return None
        if not user:
            self._json(401, {"error": "Entre na sua conta para continuar."})
            return None
        if roles and user.get("role") not in roles:
            self._json(403, {"error": "Você não tem permissão para esta função."})
            return None
        return user

    def _auth_me(self):
        try:
            user = self._current_user()
            if not user:
                self._json(200, {"user": None})
                return
            data = load_data()
            professional = next(
                (item for item in data["professionals"] if item["id"] == user.get("professionalId")),
                None,
            )
            self._json(200, {"user": public_user(user, professional)})
        except RuntimeError as error:
            self._json(500, {"error": str(error)})

    def _auth_login(self, data):
        identifier = data.get("identifier", data.get("email"))
        password = data.get("password")
        if not isinstance(identifier, str) or not isinstance(password, str):
            raise ValueError("Informe seu CPF ou e-mail e sua senha.")
        identifier = identifier.strip()
        if len(identifier) > 200 or len(password) > 128:
            raise ValueError("E-mail ou senha inválidos.")
        remote = client_ip(self)
        now = time.monotonic()
        with LOGIN_LOCK:
            attempts = [attempt for attempt in LOGIN_ATTEMPTS.get(remote, []) if now - attempt < LOGIN_LOCKOUT_SECONDS]
            LOGIN_ATTEMPTS[remote] = attempts
            if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
                self._json(429, {"error": "Muitas tentativas. Aguarde alguns minutos e tente novamente."})
                return
        try:
            stored = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", identifier):
            user = next((item for item in stored["users"] if item.get("email", "").casefold() == identifier.casefold()), None)
        else:
            try:
                cpf = normalize_cpf(identifier)
            except ValueError:
                cpf = None
            user = next(
                (item for item in stored["users"] if cpf and verify_private_identifier(cpf, item)),
                None,
            )
        if not user or not user.get("active", True) or not verify_password(password, user):
            with LOGIN_LOCK:
                LOGIN_ATTEMPTS.setdefault(remote, []).append(now)
            self._json(401, {"error": "E-mail ou senha incorretos."})
            return
        with LOGIN_LOCK:
            LOGIN_ATTEMPTS.pop(remote, None)
        session_token = secrets.token_urlsafe(32)
        # Recarrega sob trava: o hash da senha é lento e outras requisições podem ter gravado nesse intervalo.
        try:
            with DATA_LOCK:
                stored = load_data()
                current_user = next((item for item in stored["users"] if item["id"] == user["id"]), None)
                if not current_user or not current_user.get("active", True):
                    self._json(401, {"error": "E-mail ou senha incorretos."})
                    return
                stored["sessions"] = [
                    session for session in stored["sessions"] if session.get("expiresAt", 0) > time.time()
                ]
                stored["sessions"].append({
                    "tokenHash": token_hash(session_token),
                    "userId": user["id"],
                    "expiresAt": time.time() + SESSION_TTL_SECONDS,
                })
                save_data(stored)
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        professional = next(
            (item for item in stored["professionals"] if item["id"] == user.get("professionalId")),
            None,
        )
        self._json(
            200,
            {"user": public_user(user, professional)},
            {"Set-Cookie": f"{SESSION_COOKIE}={session_token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL_SECONDS}{COOKIE_FLAGS}"},
        )

    def _auth_logout(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except (CookieError, TypeError, ValueError):
            cookie = SimpleCookie()
        session_cookie = cookie.get(SESSION_COOKIE)
        if session_cookie:
            digest = token_hash(session_cookie.value)
            try:
                with DATA_LOCK:
                    stored = load_data()
                    stored["sessions"] = [
                        session for session in stored["sessions"]
                        if not secrets.compare_digest(session.get("tokenHash", ""), digest)
                    ]
                    save_data(stored)
            except RuntimeError as error:
                self._json(500, {"error": str(error)})
                return
        self._json(
            200,
            {"ok": True},
            {"Set-Cookie": f"{SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0{COOKIE_FLAGS}"},
        )

    def _throttled(self):
        if rate_limited(("global", client_ip(self)), RATE_LIMIT_PER_MINUTE):
            self._json(429, {"error": "Muitas solicitações. Aguarde um instante e tente novamente."}, {"Retry-After": "30"})
            return True
        return False

    def _health(self, ready):
        if not ready:
            self._json(200, {"status": "ok"})
            return
        try:
            with DATA_LOCK:
                load_data()
            self._json(200, {"status": "ready", "database": "postgresql" if DATABASE_URL else "arquivo"})
        except Exception:
            self._json(503, {"status": "unavailable"})

    def do_GET(self):
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        if parsed.path in {"/healthz", "/readyz"}:
            self._health(parsed.path == "/readyz")
            return
        if self._throttled():
            return
        if parsed.path == "/api/auth/me":
            self._auth_me()
        elif parsed.path == "/api/admin/overview":
            self._admin_overview()
        elif parsed.path == "/api/cep":
            self._lookup_cep(parse_qs(parsed.query).get("cep", [""])[0])
        elif parsed.path == "/api/professionals/reais":
            self._search_real_professionals(parse_qs(parsed.query))
        elif parsed.path == "/api/professionals":
            self._list_professionals(parse_qs(parsed.query))
        elif re.fullmatch(r"/api/professionals/[^/]+/availability", parsed.path):
            self._availability(parsed.path.split("/")[-2], parse_qs(parsed.query))
        elif re.fullmatch(r"/api/professionals/[^/]+/edit", parsed.path):
            self._get_professional_for_edit(parsed.path.split("/")[-2])
        elif parsed.path.startswith("/api/professionals/"):
            self._get_professional(parsed.path.rsplit("/", 1)[-1])
        elif parsed.path == "/api/chats":
            self._list_chats()
        elif re.fullmatch(r"/api/chats/[^/]+", parsed.path):
            self._get_chat(parsed.path.rsplit("/", 1)[-1], parse_qs(parsed.query).get("token", [""])[0])
        elif parsed.path == "/api/partners":
            self._list_partners(parse_qs(parsed.query))
        elif parsed.path == "/api/partners/stats":
            self._partner_stats(parse_qs(parsed.query).get("token", [""])[0])
        elif parsed.path == "/api/bookings":
            self._list_bookings()
        elif parsed.path == "/api/professional/transactions":
            self._list_professional_transactions()
        elif parsed.path in STATIC_FILES:
            self._serve_static(parsed.path)
        else:
            self._json(404, {"error": "Página não encontrada."})

    def do_POST(self):
        if self._throttled():
            return
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        try:
            data = self._read_json()
            if parsed.path == "/api/auth/login":
                self._auth_login(data)
            elif parsed.path == "/api/auth/logout":
                self._auth_logout()
            elif parsed.path == "/api/privacy/requests":
                self._create_privacy_request(data)
            elif parsed.path == "/api/professionals":
                self._create_professional(data)
            elif parsed.path == "/api/professional/transactions":
                self._create_professional_transaction(data)
            elif parsed.path == "/api/bookings":
                self._create_booking(data)
            elif re.fullmatch(r"/api/bookings/[^/]+/payment", parsed.path):
                self._simulate_booking_payment(parsed.path.split("/")[-2], data)
            elif re.fullmatch(r"/api/bookings/[^/]+/access", parsed.path):
                self._access_booking(parsed.path.split("/")[-2], data)
            elif parsed.path == "/api/chats":
                self._create_chat(data)
            elif re.fullmatch(r"/api/chats/[^/]+/messages", parsed.path):
                self._chat_message(parsed.path.split("/")[-2], data, from_professional=False)
            elif re.fullmatch(r"/api/chats/[^/]+/reply", parsed.path):
                self._chat_message(parsed.path.split("/")[-2], data, from_professional=True)
            elif parsed.path == "/api/partners":
                self._create_partner(data)
            elif parsed.path == "/api/partners/manage":
                self._manage_partner(data)
            elif re.fullmatch(r"/api/partners/[^/]+/click", parsed.path):
                self._partner_click(parsed.path.split("/")[-2])
            elif re.fullmatch(r"/api/bookings/[^/]+/review", parsed.path):
                self._create_review(parsed.path.split("/")[-2], data)
            else:
                self._json(404, {"error": "Operação não encontrada."})
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(500, {"error": str(error)})

    def do_PUT(self):
        if self._throttled():
            return
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        match = re.fullmatch(r"/api/professionals/([^/]+)", parsed.path)
        if not match:
            self._json(404, {"error": "Operação não encontrada."})
            return
        try:
            data = self._read_json()
            self._update_professional(match.group(1), data)
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(500, {"error": str(error)})

    def do_PATCH(self):
        if self._throttled():
            return
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        match = re.fullmatch(r"/api/bookings/([^/]+)/status", parsed.path)
        admin_match = re.fullmatch(r"/api/admin/(professionals|users|privacy)/([^/]+)/status", parsed.path)
        if not match and not admin_match:
            self._json(404, {"error": "Operação não encontrada."})
            return
        try:
            data = self._read_json()
            if match:
                self._update_booking_status(match.group(1), data)
            else:
                self._admin_update_status(admin_match.group(1), admin_match.group(2), data)
        except ValueError as error:
            self._json(400, {"error": str(error)})
        except RuntimeError as error:
            self._json(500, {"error": str(error)})

    def _serve_static(self, path):
        filename, content_type = STATIC_FILES[path]
        try:
            content = (APP_DIR / filename).read_bytes()
        except OSError:
            self._send(500, "text/plain; charset=utf-8", f"Não foi possível abrir {filename}.")
            return
        self._send(200, content_type, content)

    def _lookup_cep(self, raw_cep):
        try:
            self._json(200, lookup_cep(raw_cep))
        except CepLookupError as error:
            self._json(error.status, {"error": error.message})

    def _list_professionals(self, query):
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        service = query.get("servico", [""])[0].strip().lower()
        requested_city = query.get("city", [""])[0].strip()
        city = requested_city.casefold()
        city = re.sub(r",\s*(sp|são paulo|sao paulo)$", "", city)
        try:
            lat = float(query["lat"][0]) if query.get("lat") else None
            lon = float(query["lon"][0]) if query.get("lon") else None
            if (lat is None) != (lon is None) or (lat is not None and not -90 <= lat <= 90) or (lon is not None and not -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "Localização inválida."})
            return
        if not city and lat is None:
            city = PILOT_REGION.split(",", 1)[0].strip().casefold()
        results = []
        for professional in data["professionals"]:
            if professional.get("status", "active") != "active":
                continue
            professional_service = professional.get("service", "").casefold()
            if service and not (
                professional_service == service
                or service == "tatuagem_piercing" and professional_service in {"tatuagem", "piercing"}
            ):
                continue
            if city and city not in professional.get("city", "").casefold():
                continue
            result = public_professional(professional, include_reviews=False)
            result["distance"] = None
            if lat is not None and professional.get("lat") is not None and professional.get("lon") is not None:
                result["distance"] = haversine_km(lat, lon, professional["lat"], professional["lon"])
            results.append(result)
        order = query.get("ordem", ["distance"])[0]
        if order == "price":
            results.sort(key=lambda item: min((service_item.get("price", float("inf")) for service_item in item.get("services", [])), default=float("inf")))
        elif order == "rating":
            results.sort(key=lambda item: item.get("rating", 0), reverse=True)
        elif lat is not None:
            results.sort(key=lambda item: item["distance"] if item["distance"] is not None else float("inf"))
        message = ""
        if lat is None and not requested_city:
            message = f"Exibindo cadastros de {PILOT_REGION}. Autorize o GPS para calcular a distância exata."
        elif lat is None and city:
            message = "Distâncias são calculadas com GPS; os resultados por cidade não indicam proximidade exata."
        maps_location = requested_city or (f"{lat},{lon}" if lat is not None else PILOT_REGION)
        maps_query = " ".join(filter(None, (
            SERVICE_LABELS.get(service, service or "serviços de beleza"),
            "beleza",
            maps_location,
        )))
        self._json(200, {
            "professionals": results,
            "locationMessage": message,
            "googleMapsUrl": "https://www.google.com/maps/search/?api=1&query=" + quote(maps_query),
        })

    def _search_real_professionals(self, query):
        city = query.get("city", [""])[0].strip()
        service = query.get("servico", [""])[0].strip().lower()
        city = re.sub(r",\s*(sp|são paulo|sao paulo)$", "", city, flags=re.IGNORECASE).strip()
        try:
            lat = float(query["lat"][0]) if query.get("lat") else None
            lon = float(query["lon"][0]) if query.get("lon") else None
            if (lat is None) != (lon is None) or (lat is not None and not -90 <= lat <= 90) or (lon is not None and not -180 <= lon <= 180):
                raise ValueError
        except ValueError:
            self._json(400, {"error": "Localização inválida para a busca no OpenStreetMap."})
            return
        if not city and lat is None:
            city = PILOT_REGION.split(",", 1)[0]
        if city and not re.fullmatch(r"[A-Za-zÀ-ÿ0-9 .'-]{2,80}", city):
            self._json(400, {"error": "Informe uma cidade válida para buscar no OpenStreetMap."})
            return
        if service and service not in SERVICE_LABELS:
            self._json(400, {"error": "A categoria selecionada não é válida."})
            return

        if lat is not None:
            location_filter = f"(around:12000,{lat},{lon})"
            area_query = ""
        else:
            location_filter = "(area.searchArea)"
            area_query = f'area["name"="{city}"]["boundary"="administrative"]->.searchArea;\n'
        shop_by_service = {
            "cabeleireiro": ("hairdresser",),
            "barbeiro": ("hairdresser",),
            "manicure": ("beauty",),
            "pedicure": ("beauty",),
            "sobrancelhas": ("beauty",),
            "cilios": ("beauty",),
            "maquiagem": ("beauty",),
            "estetica": ("beauty",),
            "depilacao": ("beauty",),
            "massagem": ("massage",),
            "tatuagem": ("tattoo",),
            "piercing": ("piercing",),
            "tatuagem_piercing": ("tattoo", "piercing"),
        }
        shops = shop_by_service.get(service, ("hairdresser", "beauty", "massage", "tattoo", "piercing"))
        selectors = [
            f'nwr["shop"="{shop}"]{location_filter};'
            for shop in shops
        ]
        if not service or service == "barbeiro":
            selectors.append(f'nwr["craft"="barber"]{location_filter};')
        overpass_query = (
            f"[out:json][timeout:15];\n{area_query}(\n  "
            + "\n  ".join(selectors)
            + "\n);\nout center tags 40;"
        )
        cache_key = ("overpass", overpass_query)
        osm_data = cache_get(cache_key)
        last_failure = None
        for endpoint in () if osm_data is not None else OVERPASS_ENDPOINTS:
            request = Request(
                endpoint,
                data=urlencode({"data": overpass_query}).encode("utf-8"),
                headers={
                    "User-Agent": "ClickBeleza/1.0 (local prototype)",
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                },
                method="POST",
            )
            try:
                with urlopen(request, timeout=20) as response:
                    osm_data = json.loads(response.read().decode("utf-8"))
            except HTTPError as error:
                if error.code == 429:
                    last_failure = (503, "O serviço OpenStreetMap está temporariamente ocupado. Tente novamente em instantes.")
                else:
                    last_failure = (502, f"O serviço OpenStreetMap respondeu com HTTP {error.code}. Tente novamente mais tarde.")
                continue
            except (TimeoutError, URLError):
                last_failure = (504, "A busca no OpenStreetMap demorou demais. Tente novamente em instantes.")
                continue
            except (UnicodeDecodeError, json.JSONDecodeError):
                last_failure = (502, "O serviço OpenStreetMap retornou uma resposta inválida.")
                continue
            if isinstance(osm_data, dict) and isinstance(osm_data.get("elements"), list):
                cache_set(cache_key, osm_data, OVERPASS_CACHE_SECONDS)
                break
            last_failure = (502, "A resposta do OpenStreetMap não contém uma lista de locais válida.")
            osm_data = None
        if osm_data is None:
            self._json(last_failure[0], {"error": last_failure[1]})
            return
        elements = osm_data["elements"]
        real_professionals = []
        for element in elements:
            if not isinstance(element, dict) or not isinstance(element.get("tags"), dict):
                continue
            osm_type = element.get("type")
            osm_id = element.get("id")
            if osm_type not in {"node", "way", "relation"} or not isinstance(osm_id, int):
                continue
            tags = element["tags"]
            name = tags.get("name", "").strip() if isinstance(tags.get("name"), str) else ""
            if not name or not self._matches_osm_service(tags, service):
                continue
            center = element.get("center")
            center = center if isinstance(center, dict) else {}
            latitude = element.get("lat", center.get("lat"))
            longitude = element.get("lon", center.get("lon"))
            try:
                latitude = float(latitude) if latitude is not None else None
                longitude = float(longitude) if longitude is not None else None
            except (TypeError, ValueError):
                latitude = longitude = None
            if latitude is not None and not -90 <= latitude <= 90:
                latitude = None
            if longitude is not None and not -180 <= longitude <= 180:
                longitude = None
            address_parts = [tags.get(key, "") for key in ("addr:street", "addr:housenumber")]
            address_parts.append(tags.get("addr:suburb", tags.get("addr:neighbourhood", "")))
            address_parts = [part for part in address_parts if isinstance(part, str) and part.strip()]
            address = ", ".join(address_parts) or "Endereço não informado no OpenStreetMap"
            service_key = self._osm_service_key(tags, service)
            distance = haversine_km(lat, lon, latitude, longitude) if lat is not None and latitude is not None and longitude is not None else None
            real_professionals.append({
                "id": f"osm-{osm_type}-{osm_id}",
                "name": name,
                "service": service_key,
                "category": SERVICE_LABELS.get(service_key, "serviços de beleza"),
                "address": address,
                "city": city,
                "lat": latitude,
                "lon": longitude,
                "rating": None,
                "reviewCount": 0,
                "price": None,
                "distance": distance,
                "external": True,
                "source": "OpenStreetMap",
                "sourceUrl": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
                "googleMapsUrl": "https://www.google.com/maps/search/?api=1&query=" + quote(f"{name} {address} {city}"),
            })

        self._json(200, {
            "professionals": real_professionals,
            "city": city or "Próximo de você",
            "source": "OpenStreetMap",
            "attribution": "Dados © OpenStreetMap contributors (ODbL).",
        })

    @staticmethod
    def _osm_service_key(tags, selected_service=""):
        if selected_service:
            return selected_service
        if tags.get("craft") == "barber":
            return "barbeiro"
        shop = tags.get("shop")
        return {
            "hairdresser": "cabeleireiro",
            "beauty": "estetica",
            "massage": "massagem",
            "tattoo": "tatuagem",
            "piercing": "piercing",
        }.get(shop, "estetica")

    @classmethod
    def _matches_osm_service(cls, tags, service):
        if not service:
            return True
        if service == "barbeiro":
            return tags.get("craft") == "barber" or tags.get("shop") == "hairdresser"
        if service == "cabeleireiro":
            return tags.get("shop") == "hairdresser"
        if service == "massagem":
            return tags.get("shop") == "massage"
        if service == "tatuagem":
            return tags.get("shop") == "tattoo"
        if service == "piercing":
            return tags.get("shop") == "piercing"
        if service == "tatuagem_piercing":
            return tags.get("shop") in {"tattoo", "piercing"}
        if service in {"manicure", "pedicure", "sobrancelhas", "cilios", "maquiagem", "estetica", "depilacao"}:
            return tags.get("shop") == "beauty"
        return False

    def _get_professional(self, professional_id):
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        professional = next((item for item in data["professionals"] if item["id"] == professional_id), None)
        if not professional or professional.get("status", "active") != "active":
            self._json(404, {"error": "Profissional não encontrado."})
            return
        self._json(200, {"professional": public_professional(professional, include_portfolio=True)})

    def _get_professional_for_edit(self, professional_id):
        user = self._require_user({"admin", "professional"})
        if not user:
            return
        if user["role"] == "professional" and user.get("professionalId") != professional_id:
            self._json(403, {"error": "Você só pode consultar o seu próprio perfil."})
            return
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        professional = next((item for item in data["professionals"] if item["id"] == professional_id), None)
        if not professional:
            self._json(404, {"error": "Profissional não encontrado."})
            return
        if professional.get("demo"):
            self._json(400, {"error": "Perfis de demonstração não podem ser editados."})
            return
        result = public_professional(professional, include_portfolio=True)
        result.update({
            "email": professional.get("email", ""),
            "phone": professional.get("phone", ""),
            "status": professional.get("status", "active"),
        })
        service = (professional.get("services") or [{}])[0]
        result.update({
            "serviceName": service.get("name", ""),
            "price": service.get("price", ""),
            "duration": service.get("duration", 60),
        })
        self._json(200, {"professional": result})

    def _validated_portfolio(self, data):
        portfolio = data.get("portfolio", [])
        if not isinstance(portfolio, list) or len(portfolio) > 8:
            raise ValueError("O portfólio pode conter até 8 fotos ou vídeos.")
        validated = []
        total_size = 0
        for item in portfolio:
            if not isinstance(item, dict):
                raise ValueError("Há um arquivo inválido no portfólio.")
            name = item.get("name")
            mime_type = item.get("type")
            data_url = item.get("data")
            if not all(isinstance(value, str) for value in (name, mime_type, data_url)):
                raise ValueError("Há um arquivo incompleto no portfólio.")
            if mime_type not in ("image/jpeg", "image/png", "image/webp", "video/mp4", "video/webm"):
                raise ValueError("Use fotos JPG/PNG/WebP ou vídeos MP4/WebM.")
            match = re.fullmatch(r"data:([a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+);base64,([A-Za-z0-9+/]*={0,2})", data_url)
            if not match or match.group(1) != mime_type:
                raise ValueError("O conteúdo de um arquivo do portfólio é inválido.")
            try:
                binary = base64.b64decode(match.group(2), validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("O conteúdo de um arquivo do portfólio é inválido.") from None
            is_video = mime_type.startswith("video/")
            max_size = (10 if is_video else 4) * 1024 * 1024
            if not binary or len(binary) > max_size:
                raise ValueError(f"Cada {'vídeo' if is_video else 'foto'} deve ter até {max_size // (1024 * 1024)} MB.")
            signature_ok = (
                (mime_type == "image/jpeg" and binary.startswith(b"\xff\xd8\xff"))
                or (mime_type == "image/png" and binary.startswith(b"\x89PNG\r\n\x1a\n"))
                or (mime_type == "image/webp" and binary.startswith(b"RIFF") and binary[8:12] == b"WEBP")
                or (mime_type == "video/mp4" and binary[4:8] == b"ftyp")
                or (mime_type == "video/webm" and binary.startswith(b"\x1a\x45\xdf\xa3"))
            )
            if not signature_ok:
                raise ValueError("O conteúdo do arquivo não corresponde ao formato informado.")
            total_size += len(binary)
            if total_size > 18 * 1024 * 1024:
                raise ValueError("O portfólio deve ter no máximo 18 MB no total.")
            validated.append({
                "name": name.strip()[:120] or "Mídia do portfólio",
                "type": mime_type,
                "data": data_url,
            })
        return validated

    def _professional_from_form(self, data, professional_id=None, portfolio=None):
        required = ("name", "service", "city")
        if any(not isinstance(data.get(field), str) or not data[field].strip() for field in required):
            raise ValueError("Informe seu nome, categoria e cidade para criar o perfil.")
        optional_fields = ("email", "cpf", "phone", "description", "address", "neighborhood", "serviceName", "price", "duration", "lat", "lon")
        if any(field in data and not isinstance(data[field], str) for field in optional_fields):
            raise ValueError("Confira os dados opcionais do perfil.")
        service_key = data["service"].strip().lower()
        if service_key not in SERVICE_LABELS:
            raise ValueError("Escolha uma categoria de serviço válida.")
        email = data.get("email", "").strip()
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Informe um e-mail válido ou deixe o campo em branco.")
        service_name = data.get("serviceName", "").strip()
        price_value = data.get("price", "").strip()
        if bool(service_name) != bool(price_value):
            raise ValueError("Para cadastrar um serviço, informe o nome e o preço.")
        try:
            price = float(price_value) if price_value else None
            duration = int(data.get("duration", "60")) if service_name else 60
            lat = float(data["lat"]) if data.get("lat") else None
            lon = float(data["lon"]) if data.get("lon") else None
        except (TypeError, ValueError):
            raise ValueError("Informe preço, duração e coordenadas válidos.") from None
        if (price is not None and (not math.isfinite(price) or not 1 <= price <= 100000)) or (service_name and not 15 <= duration <= 600):
            raise ValueError("O preço ou a duração estão fora do intervalo permitido.")
        if (lat is None) != (lon is None) or (lat is not None and not -90 <= lat <= 90) or (lon is not None and not -180 <= lon <= 180):
            raise ValueError("Informe latitude e longitude válidas, ou deixe ambas em branco.")
        return {
            "id": professional_id or uuid.uuid4().hex[:12], "name": data["name"].strip()[:100],
            "service": service_key, "description": data.get("description", "").strip()[:800],
            "address": data.get("address", "").strip()[:200], "neighborhood": data.get("neighborhood", "").strip()[:100],
            "city": data["city"].strip()[:100], "lat": lat, "lon": lon,
            "rating": 0, "reviewCount": 0, "color": "#f2d8d0",
            "initials": "".join(part[0] for part in data["name"].split()[:2]).upper(),
            "demo": False, "email": email[:200], "phone": data.get("phone", "").strip()[:30],
            "services": ([{"name": service_name[:100], "price": price, "duration": duration}] if service_name else []),
            "reviews": [], "portfolio": portfolio if portfolio is not None else self._validated_portfolio(data),
        }

    def _create_professional(self, data):
        professional = self._professional_from_form(data)
        email = data.get("email")
        password = data.get("password")
        if not isinstance(email, str) or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()):
            raise ValueError("Informe um e-mail válido para acessar sua conta.")
        if not isinstance(password, str) or not 8 <= len(password) <= 128:
            raise ValueError("A senha deve ter entre 8 e 128 caracteres.")
        raw_cpf = data.get("cpf", "").strip()
        cpf = normalize_cpf(raw_cpf) if raw_cpf else None
        professional["status"] = "active"
        session_token = secrets.token_urlsafe(32)
        with DATA_LOCK:
            stored = load_data()
            email = email.strip().casefold()
            if any(user.get("email", "").casefold() == email for user in stored["users"]):
                raise ValueError("Já existe uma conta com este e-mail.")
            if cpf and any(verify_private_identifier(cpf, item) for item in stored["users"]):
                raise ValueError("Já existe uma conta vinculada a este CPF.")
            user = new_user(email, password, "professional", professional["id"], cpf)
            stored["professionals"].append(professional)
            stored["users"].append(user)
            stored["sessions"].append({
                "tokenHash": token_hash(session_token),
                "userId": user["id"],
                "expiresAt": time.time() + SESSION_TTL_SECONDS,
            })
            save_data(stored)
        self._json(
            201,
            {"professional": public_professional(professional), "user": public_user(user, professional)},
            {"Set-Cookie": f"{SESSION_COOKIE}={session_token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={SESSION_TTL_SECONDS}{COOKIE_FLAGS}"},
        )

    def _update_professional(self, professional_id, data):
        user = self._require_user({"admin", "professional"})
        if not user:
            return
        if user["role"] == "professional" and user.get("professionalId") != professional_id:
            self._json(403, {"error": "Você só pode alterar o seu próprio perfil."})
            return
        with DATA_LOCK:
            stored = load_data()
            existing = next((item for item in stored["professionals"] if item["id"] == professional_id), None)
            if not existing:
                self._json(404, {"error": "Profissional não encontrado."})
                return
            if existing.get("demo"):
                self._json(400, {"error": "Perfis de demonstração não podem ser editados."})
                return
            updated = self._professional_from_form(data, professional_id)
            updated["status"] = existing.get("status", "active")
            updated["rating"] = existing.get("rating", 0)
            updated["reviewCount"] = existing.get("reviewCount", 0)
            updated["reviews"] = existing.get("reviews", [])
            updated["portfolio"] = self._validated_portfolio(data) if "portfolio" in data else existing.get("portfolio", [])
            new_email = updated["email"].casefold()
            linked_user = next(
                (item for item in stored["users"] if item.get("professionalId") == professional_id),
                None,
            )
            duplicate = next(
                (item for item in stored["users"] if item.get("email", "").casefold() == new_email and item is not linked_user),
                None,
            )
            if new_email and duplicate:
                raise ValueError("Este e-mail já pertence a outra conta.")
            if linked_user and new_email:
                linked_user["email"] = new_email
            stored["professionals"] = [
                updated if item["id"] == professional_id else item for item in stored["professionals"]
            ]
            save_data(stored)
        self._json(200, {"professional": public_professional(updated)})

    def _availability(self, professional_id, query):
        day = query.get("date", [""])[0]
        service_name = query.get("service", [""])[0]
        try:
            parsed_day = date.fromisoformat(day)
        except ValueError:
            self._json(400, {"error": "Informe uma data válida."})
            return
        if parsed_day < business_now().date() or parsed_day > business_now().date() + timedelta(days=180):
            self._json(200, {"date": day, "times": []})
            return
        try:
            stored = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        professional = next((item for item in stored["professionals"] if item["id"] == professional_id), None)
        if not professional or professional.get("demo") or professional.get("status") == "paused":
            self._json(404, {"error": "Profissional não encontrado."})
            return
        service = next((item for item in professional.get("services", []) if item.get("name") == service_name), None)
        if service is None:
            self._json(400, {"error": "Escolha um serviço deste profissional."})
            return
        times = available_start_times(stored["bookings"], professional_id, day, service_minutes(service))
        self._json(200, {"date": day, "duration": service_minutes(service), "times": times})

    def _create_booking(self, data):
        fields = ("professionalId", "customerName", "customerPhone", "date", "time", "serviceName")
        if any(not isinstance(data.get(field), str) or not data[field].strip() for field in fields):
            raise ValueError("Preencha nome, telefone, serviço, data e horário.")
        email = data.get("customerEmail")
        email = email.strip()[:200] if isinstance(email, str) else ""
        if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Informe um e-mail válido ou deixe o campo em branco.")
        try:
            booking_date = date.fromisoformat(data["date"])
            price = float(data.get("price", ""))
        except (ValueError, TypeError):
            raise ValueError("Informe uma data e um preço válidos.") from None
        data["date"] = booking_date.isoformat()
        if booking_date < business_now().date():
            raise ValueError("Escolha uma data de hoje ou futura.")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", data["time"]):
            raise ValueError("Escolha um dos horários disponíveis.")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("O preço do serviço é inválido.")
        with DATA_LOCK:
            stored = load_data()
            professional = next((item for item in stored["professionals"] if item["id"] == data["professionalId"]), None)
            if not professional:
                self._json(404, {"error": "Profissional não encontrado."})
                return
            if professional.get("demo"):
                self._json(400, {"error": "Este perfil é apenas ilustrativo. Escolha um profissional cadastrado para solicitar um horário."})
                return
            service = next((
                item for item in professional.get("services", [])
                if item.get("name") == data["serviceName"].strip()
            ), None)
            if service is None or not math.isclose(float(service["price"]), price, rel_tol=0, abs_tol=0.01):
                self._json(400, {"error": "O serviço escolhido não pertence a este perfil ou teve o preço alterado."})
                return
            duration = service_minutes(service)
            free_times = available_start_times(
                stored["bookings"], professional["id"], data["date"], duration,
            )
            if data["time"] not in free_times:
                self._json(409, {"error": "Este horário não está mais disponível para a duração do serviço. Escolha outro horário."})
                return
            booking_id = uuid.uuid4().hex[:8].upper()
            if not reserve_booking_slots(professional["id"], data["date"], data["time"], duration, booking_id):
                self._json(409, {"error": "Este horário acabou de ser reservado. Escolha outro horário."})
                return
            booking = {
                "id": booking_id, "professionalId": professional["id"],
                "professionalName": professional["name"], "customerName": data["customerName"].strip()[:100],
                "customerEmail": email, "customerPhone": data["customerPhone"].strip()[:30],
                "date": data["date"], "time": data["time"], "serviceName": data["serviceName"].strip()[:100],
                "duration": duration,
                "price": price, "status": "solicitado", "review": False,
                "paymentMode": "demo", "paymentStatus": "demo_pending",
                "paymentDestination": "clickbeleza", "paymentMethod": None,
            }
            customer_token = secrets.token_urlsafe(32)
            booking["customerTokenHash"] = token_hash(customer_token)
            stored["bookings"].append(booking)
            try:
                save_data(stored)
            except RuntimeError:
                release_booking_slots(booking_id)
                raise
        self._json(201, {"booking": public_booking(booking), "customerToken": customer_token})

    def _authorize_booking(self, booking, customer_token):
        user = self._current_user()
        if user:
            if user.get("role") == "admin":
                return user
            if user.get("role") == "professional" and user.get("professionalId") == booking.get("professionalId"):
                return user
            self._json(403, {"error": "Você não tem acesso a este pedido."})
            return None
        stored_hash = booking.get("customerTokenHash", "")
        if (
            isinstance(customer_token, str)
            and customer_token
            and stored_hash
            and secrets.compare_digest(stored_hash, token_hash(customer_token))
        ):
            return {"role": "customer"}
        self._json(401, {"error": "Use o código privado deste pedido para continuar."})
        return None

    def _access_booking(self, booking_id, data):
        with DATA_LOCK:
            booking = next((item for item in load_data()["bookings"] if item["id"] == booking_id), None)
            if not booking:
                self._json(404, {"error": "Pedido de agendamento não encontrado."})
                return
            if not self._authorize_booking(booking, data.get("customerToken")):
                return
            self._json(200, {"booking": public_booking(booking)})

    def _simulate_booking_payment(self, booking_id, data):
        payment_method = data.get("paymentMethod")
        if not isinstance(payment_method, str) or payment_method not in {"pix", "cartao"}:
            raise ValueError("Escolha Pix ou cartão para a demonstração.")
        with DATA_LOCK:
            stored = load_data()
            booking = next((item for item in stored["bookings"] if item["id"] == booking_id), None)
            if not booking:
                self._json(404, {"error": "Pedido de agendamento não encontrado."})
                return
            if not self._authorize_booking(booking, data.get("customerToken")):
                return
            if booking.get("status") not in {"solicitado", "confirmado"}:
                self._json(409, {"error": "Este pedido não aceita mais uma etapa de pagamento."})
                return
            if booking.get("paymentMode") != "demo" or booking.get("paymentDestination") != "clickbeleza":
                self._json(409, {"error": "Este pedido não está configurado para o checkout demonstrativo."})
                return
            if booking.get("paymentStatus") == "demo_completed":
                if booking.get("paymentMethod") != payment_method:
                    self._json(409, {"error": "A etapa demonstrativa deste pedido já foi concluída."})
                    return
                self._json(200, {"booking": public_booking(booking), "message": "Etapa demonstrativa já concluída; nenhuma cobrança foi feita."})
                return
            if booking.get("paymentStatus") != "demo_pending":
                self._json(409, {"error": "A etapa demonstrativa deste pedido não está disponível."})
                return
            booking["paymentStatus"] = "demo_completed"
            booking["paymentMethod"] = payment_method
            save_data(stored)
        self._json(200, {
            "booking": public_booking(booking),
            "message": "Pagamento apenas simulado. Nenhuma cobrança, transferência ou repasse foi realizado.",
        })

    def _list_bookings(self):
        user = self._require_user({"admin", "professional"})
        if not user:
            return
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        bookings = data["bookings"]
        if user["role"] == "professional":
            bookings = [item for item in bookings if item.get("professionalId") == user.get("professionalId")]
        self._json(200, {"bookings": [public_booking(item) for item in sorted(bookings, key=lambda item: (item["date"], item["time"]))]})

    def _list_professional_transactions(self):
        user = self._require_user({"professional"})
        if not user:
            return
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        transactions = [
            item for item in data["financialTransactions"]
            if item.get("professionalId") == user.get("professionalId")
        ]
        transactions.sort(key=lambda item: (item.get("date", ""), item.get("createdAt", "")), reverse=True)
        self._json(200, {"transactions": transactions})

    def _create_professional_transaction(self, data):
        user = self._require_user({"professional"})
        if not user:
            return
        transaction_type = data.get("type")
        if not isinstance(transaction_type, str) or transaction_type not in {"entrada", "saida"}:
            raise ValueError("Escolha entrada ou saída.")
        raw_amount = data.get("amount")
        if isinstance(raw_amount, bool) or not isinstance(raw_amount, (str, int, float)):
            raise ValueError("Informe um valor válido.")
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            raise ValueError("Informe um valor válido.") from None
        if not math.isfinite(amount) or not 0 < amount <= 1_000_000:
            raise ValueError("O valor deve ser maior que zero e não pode ultrapassar R$ 1.000.000.")
        amount = round(amount, 2)
        if amount <= 0:
            raise ValueError("O valor deve ser maior que zero.")
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Descreva o lançamento.")
        description = description.strip()
        if len(description) > 160:
            raise ValueError("A descrição deve ter até 160 caracteres.")
        transaction_date = data.get("date", date.today().isoformat())
        if not isinstance(transaction_date, str):
            raise ValueError("Informe uma data válida.")
        try:
            parsed_date = date.fromisoformat(transaction_date)
        except ValueError:
            raise ValueError("Informe uma data válida.") from None
        if parsed_date.isoformat() != transaction_date:
            raise ValueError("Informe uma data válida.")

        with DATA_LOCK:
            try:
                stored = load_data()
            except RuntimeError as error:
                self._json(500, {"error": str(error)})
                return
            transactions = stored["financialTransactions"]
            own_count = sum(
                item.get("professionalId") == user.get("professionalId")
                for item in transactions
            )
            if own_count >= 1000:
                raise ValueError("O limite de 1.000 lançamentos foi atingido.")
            transaction = {
                "id": uuid.uuid4().hex[:12],
                "professionalId": user["professionalId"],
                "type": transaction_type,
                "amount": amount,
                "description": description,
                "date": transaction_date,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "recordedManually": True,
            }
            transactions.append(transaction)
            try:
                save_data(stored)
            except RuntimeError as error:
                self._json(500, {"error": str(error)})
                return
        self._json(201, {"transaction": transaction})

    def _admin_overview(self):
        if not self._require_user({"admin"}):
            return
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        professionals = []
        for item in data["professionals"]:
            result = public_professional(item, include_portfolio=True)
            result.update({"email": item.get("email", ""), "phone": item.get("phone", ""),
                           "status": item.get("status", "active")})
            professionals.append(result)
        partners = [
            {key: value for key, value in item.items() if key != "tokenHash"}
            for item in get_partners(data)
        ]
        clients_by_key = {}
        clients_by_name = {}
        for booking in data["bookings"]:
            client_key = customer_key(booking.get("customerEmail"), booking.get("customerPhone"), booking.get("customerName"))
            if isinstance(booking.get("customerName"), str) and booking["customerName"].strip():
                clients_by_name.setdefault(booking["customerName"].strip().casefold(), client_key)
            client = clients_by_key.setdefault(client_key, {
                "name": booking.get("customerName", ""),
                "email": booking.get("customerEmail", ""),
                "phone": booking.get("customerPhone", ""),
                "bookings": 0,
                "conversations": 0,
            })
            client["bookings"] += 1
        for chat in data["chats"]:
            client_name = chat.get("customerName", "")
            client_key = clients_by_name.get(
                client_name.strip().casefold() if isinstance(client_name, str) else "",
                customer_key("", "", client_name),
            )
            client = clients_by_key.setdefault(client_key, {
                "name": chat.get("customerName", ""),
                "email": "",
                "phone": "",
                "bookings": 0,
                "conversations": 0,
            })
            client["conversations"] += 1
        self._json(200, {
            "professionals": professionals,
            "bookings": [public_booking(item) for item in sorted(data["bookings"], key=lambda item: (item["date"], item["time"]))],
            "chats": [self._public_chat(item) for item in sorted(data["chats"], key=lambda chat: chat["updatedAt"], reverse=True)],
            "partners": partners,
            "users": [public_user(item) for item in data["users"]],
            "clients": sorted(clients_by_key.values(), key=lambda item: item["name"].casefold()),
            "privacyRequests": sorted(data["privacyRequests"], key=lambda item: item["createdAt"], reverse=True),
        })

    def _create_privacy_request(self, data):
        if rate_limited(("privacy", client_ip(self)), 5, 3600):
            self._json(429, {"error": "Limite de solicitações atingido. Tente novamente mais tarde."})
            return
        kind = data.get("type")
        if kind not in PRIVACY_REQUEST_TYPES:
            raise ValueError("Escolha o tipo da solicitação.")
        name = data.get("name")
        contact = data.get("contact")
        message = data.get("message")
        if not all(isinstance(value, str) for value in (name, contact, message)):
            raise ValueError("Preencha nome, contato e mensagem.")
        name, contact, message = name.strip(), contact.strip(), message.strip()
        if not (2 <= len(name) <= 120):
            raise ValueError("Informe seu nome.")
        if not (5 <= len(contact) <= 160) or not (re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact) or len(re.sub(r"\D", "", contact)) >= 10):
            raise ValueError("Informe um e-mail ou telefone válido para retorno.")
        if not (10 <= len(message) <= 2000):
            raise ValueError("Descreva a solicitação com 10 a 2000 caracteres.")
        protocol = f"CB-{datetime.now(timezone.utc):%Y%m%d}-{secrets.token_hex(3).upper()}"
        with DATA_LOCK:
            stored = load_data()
            stored["privacyRequests"].append({
                "id": uuid.uuid4().hex,
                "protocol": protocol,
                "type": kind,
                "name": name,
                "contact": contact,
                "message": message,
                "status": "aberta",
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            save_data(stored)
        self._json(201, {"protocol": protocol, "message": f"Solicitação registrada. Guarde o protocolo {protocol}."})

    def _admin_update_status(self, kind, record_id, data):
        if not self._require_user({"admin"}):
            return
        with DATA_LOCK:
            stored = load_data()
            if kind == "professionals":
                status = data.get("status")
                if status not in {"active", "paused"}:
                    raise ValueError("Escolha um status válido para o perfil.")
                record = next((item for item in stored["professionals"] if item["id"] == record_id), None)
                if not record:
                    self._json(404, {"error": "Perfil profissional não encontrado."})
                    return
                record["status"] = status
                response = {"status": status}
            elif kind == "privacy":
                status = data.get("status")
                if status not in {"aberta", "em_andamento", "concluida"}:
                    raise ValueError("Escolha um status válido para a solicitação.")
                record = next((item for item in stored["privacyRequests"] if item["id"] == record_id), None)
                if not record:
                    self._json(404, {"error": "Solicitação não encontrada."})
                    return
                record["status"] = status
                record["updatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                response = {"status": status}
            else:
                active = data.get("active")
                if not isinstance(active, bool):
                    raise ValueError("Informe se a conta deve ficar ativa.")
                record = next((item for item in stored["users"] if item["id"] == record_id), None)
                if not record or record.get("role") != "professional":
                    self._json(404, {"error": "Conta profissional não encontrada."})
                    return
                record["active"] = active
                if not active:
                    stored["sessions"] = [
                        session for session in stored["sessions"] if session.get("userId") != record_id
                    ]
                if record.get("professionalId"):
                    professional = next(
                        (item for item in stored["professionals"] if item["id"] == record["professionalId"]),
                        None,
                    )
                    if professional:
                        professional["status"] = "active" if active else "paused"
                response = {"active": active}
            save_data(stored)
        self._json(200, response)

    def _update_booking_status(self, booking_id, data):
        status = data.get("status")
        if status not in VALID_STATUSES:
            raise ValueError("Status de agendamento inválido.")
        with DATA_LOCK:
            stored = load_data()
            booking = next((item for item in stored["bookings"] if item["id"] == booking_id), None)
            if not booking:
                self._json(404, {"error": "Agendamento não encontrado."})
                return
            user = self._authorize_booking(booking, data.get("customerToken"))
            if not user:
                return
            if user["role"] == "customer" and status != "cancelado":
                self._json(403, {"error": "Clientes só podem cancelar o próprio pedido."})
                return
            allowed = {"solicitado": {"confirmado", "cancelado"}, "confirmado": {"concluido", "cancelado"}}
            if status not in allowed.get(booking["status"], set()):
                raise ValueError("Esta mudança de status não é permitida.")
            booking["status"] = status
            save_data(stored)
            if status == "cancelado":
                release_booking_slots(booking["id"])
        self._json(200, {"booking": public_booking(booking)})

    def _create_review(self, booking_id, data):
        rating = data.get("rating")
        comment = data.get("comment", "")
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError("A nota deve ser um número inteiro entre 1 e 5.")
        if not isinstance(comment, str) or len(comment) > 500:
            raise ValueError("O comentário deve ter até 500 caracteres.")
        with DATA_LOCK:
            stored = load_data()
            booking = next((item for item in stored["bookings"] if item["id"] == booking_id), None)
            if not booking:
                self._json(404, {"error": "Agendamento não encontrado."})
                return
            user = self._authorize_booking(booking, data.get("customerToken"))
            if not user:
                return
            if user["role"] == "professional":
                self._json(403, {"error": "Somente o cliente atendido pode avaliar este pedido."})
                return
            if booking["status"] != "concluido" or booking.get("review"):
                raise ValueError("Só é possível avaliar um atendimento concluído uma única vez.")
            professional = next(item for item in stored["professionals"] if item["id"] == booking["professionalId"])
            reviews = professional.setdefault("reviews", [])
            review = {"rating": rating, "customerName": booking["customerName"], "comment": comment.strip()}
            reviews.append(review)
            professional["reviewCount"] = len(reviews)
            professional["rating"] = round(sum(item["rating"] for item in reviews) / len(reviews), 1)
            booking["review"] = True
            save_data(stored)
        self._json(201, {"review": review})

    def _clean_message(self, data):
        text = data.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not text:
            raise ValueError("Escreva uma mensagem.")
        return text[:1000]

    def _create_chat(self, data):
        text = self._clean_message(data)
        name = data.get("customerName")
        name = name.strip()[:100] if isinstance(name, str) else ""
        if not name:
            raise ValueError("Informe seu nome para iniciar a conversa.")
        token = secrets.token_urlsafe(18)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with DATA_LOCK:
            stored = load_data()
            professional = next((p for p in stored["professionals"] if p["id"] == data.get("professionalId")), None)
            if not professional or professional.get("demo"):
                raise ValueError("Este perfil é apenas ilustrativo. Converse com um profissional cadastrado.")
            chat = {
                "id": uuid.uuid4().hex[:12], "professionalId": professional["id"], "professionalName": professional["name"],
                "customerName": name, "tokenHash": token_hash(token), "createdAt": now, "updatedAt": now,
                "messages": [{"from": "customer", "text": text, "at": now}],
            }
            stored.setdefault("chats", []).append(chat)
            save_data(stored)
        self._json(201, {"chat": self._public_chat(chat), "token": token})

    @staticmethod
    def _public_chat(chat):
        return {key: chat[key] for key in ("id", "professionalId", "professionalName", "customerName", "createdAt", "updatedAt", "messages")}

    def _get_chat(self, chat_id, token):
        try:
            with DATA_LOCK:
                chat = next((c for c in load_data().get("chats", []) if c["id"] == chat_id), None)
            user = self._current_user()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        customer_access = bool(
            chat and token and secrets.compare_digest(chat["tokenHash"], token_hash(token))
        )
        professional_access = bool(
            chat and user and user.get("role") == "admin"
        ) or bool(
            chat and user and user.get("role") == "professional"
            and user.get("professionalId") == chat.get("professionalId")
        )
        if not chat or not (customer_access or professional_access):
            self._json(404, {"error": "Conversa não encontrada."})
            return
        self._json(200, {"chat": self._public_chat(chat)})

    def _list_chats(self):
        user = self._require_user({"admin", "professional"})
        if not user:
            return
        with DATA_LOCK:
            chats = load_data().get("chats", [])
        if user["role"] == "professional":
            chats = [chat for chat in chats if chat.get("professionalId") == user.get("professionalId")]
        chats = sorted(chats, key=lambda c: c["updatedAt"], reverse=True)
        self._json(200, {"chats": [self._public_chat(c) for c in chats]})

    def _chat_message(self, chat_id, data, from_professional):
        text = self._clean_message(data)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        user = self._current_user() if from_professional else None
        if from_professional and not user:
            self._json(401, {"error": "Entre na sua conta profissional para responder."})
            return
        if from_professional and user.get("role") not in {"admin", "professional"}:
            self._json(403, {"error": "Você não tem permissão para responder a conversas."})
            return
        with DATA_LOCK:
            stored = load_data()
            chat = next((c for c in stored.get("chats", []) if c["id"] == chat_id), None)
            valid = chat is not None and (not from_professional or (
                user.get("role") == "admin" or user.get("professionalId") == chat.get("professionalId")
            )) and (from_professional or (
                isinstance(data.get("token"), str) and secrets.compare_digest(chat["tokenHash"], token_hash(data["token"]))))
            if not valid:
                self._json(404, {"error": "Conversa não encontrada."})
                return
            if len(chat["messages"]) >= 200:
                raise ValueError("Esta conversa atingiu o limite de mensagens.")
            chat["messages"].append({"from": "professional" if from_professional else "customer", "text": text, "at": now})
            chat["updatedAt"] = now
            save_data(stored)
        self._json(201, {"chat": self._public_chat(chat)})

    def _list_partners(self, query):
        category = query.get("categoria", [""])[0]
        with DATA_LOCK:
            partners = get_partners(load_data())
            active = [p for p in partners if p.get("status") == "active"]
        if category in PARTNER_CATEGORIES:
            active = [p for p in active if p.get("category") == category]
        active.sort(key=lambda p: (-PARTNER_PLANS.get(p.get("plan"), PARTNER_PLANS["basico"])["rank"], p.get("createdAt", "")))
        self._json(200, {"partners": [public_partner(p) for p in active]})

    def _create_partner(self, data):
        def text(field, limit):
            value = data.get(field)
            return value.strip()[:limit] if isinstance(value, str) else ""
        name, description, email, phone = text("name", 80), text("description", 160), text("email", 200), text("phone", 30)
        category, plan, url = text("category", 30), text("plan", 20), text("url", 300)
        if not all((name, description, email, phone)):
            raise ValueError("Preencha nome da marca, descrição, e-mail e telefone.")
        if len(description) < 10:
            raise ValueError("Descreva sua marca em pelo menos 10 caracteres.")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Informe um e-mail válido.")
        if category not in PARTNER_CATEGORIES:
            raise ValueError("Escolha uma categoria válida.")
        if plan not in PARTNER_PLANS:
            raise ValueError("Escolha um plano de anúncio.")
        if url:
            parts = urlsplit(url)
            if parts.scheme not in ("http", "https") or not parts.netloc:
                raise ValueError("O link do site deve começar com http:// ou https://.")
        token = secrets.token_urlsafe(18)
        partner = {
            "id": uuid.uuid4().hex[:12], "name": name, "category": category, "description": description,
            "url": url, "plan": plan, "color": "#1fe36a", "status": "active", "clicks": 0, "demo": False,
            "email": email, "phone": phone, "tokenHash": token_hash(token), "createdAt": date.today().isoformat(),
        }
        with DATA_LOCK:
            stored = load_data()
            get_partners(stored).append(partner)
            save_data(stored)
        self._json(201, {"partner": public_partner(partner), "token": token})

    def _partner_click(self, partner_id):
        with DATA_LOCK:
            stored = load_data()
            partner = next((p for p in get_partners(stored) if p["id"] == partner_id and p.get("status") == "active"), None)
            if not partner:
                self._json(404, {"error": "Anúncio não encontrado."})
                return
            partner["clicks"] = int(partner.get("clicks", 0)) + 1
            save_data(stored)
        self._json(200, {"clicks": partner["clicks"]})

    def _find_partner_by_token(self, stored, token):
        if not isinstance(token, str) or not token:
            return None
        digest = token_hash(token.strip())
        return next((p for p in get_partners(stored) if secrets.compare_digest(p.get("tokenHash", ""), digest)), None)

    def _partner_stats(self, token):
        with DATA_LOCK:
            partner = self._find_partner_by_token(load_data(), token)
        if not partner:
            self._json(404, {"error": "Código de acesso inválido."})
            return
        plan = PARTNER_PLANS.get(partner.get("plan"), PARTNER_PLANS["basico"])
        self._json(200, {"partner": public_partner(partner), "clicks": partner.get("clicks", 0),
                         "status": partner.get("status"), "createdAt": partner.get("createdAt"), "planPrice": plan["price"]})

    def _manage_partner(self, data):
        action = data.get("action")
        if action not in ("pause", "activate"):
            raise ValueError("Ação inválida.")
        with DATA_LOCK:
            stored = load_data()
            user = self._current_user()
            if user and user.get("role") == "admin":
                partner = next((item for item in get_partners(stored) if item["id"] == data.get("partnerId")), None)
            else:
                partner = self._find_partner_by_token(stored, data.get("token"))
            if not partner:
                self._json(404, {"error": "Código de acesso inválido."})
                return
            partner["status"] = "paused" if action == "pause" else "active"
            save_data(stored)
        self._json(200, {"status": partner["status"]})

    def log_message(self, format_string, *args):
        print(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ip": client_ip(self),
            "msg": (format_string % args)[:300],
        }, ensure_ascii=False), flush=True)


def backfill_booking_slots():
    """Migração: cria faixas para agendamentos ativos que existiam antes da tabela booking_slots."""
    if not DATABASE_URL:
        return 0
    created = 0
    with DATA_LOCK:
        for booking in load_data()["bookings"]:
            if booking.get("status") not in ACTIVE_BOOKING_STATUSES:
                continue
            try:
                duration = int(booking.get("duration") or DEFAULT_SERVICE_MINUTES)
                with psycopg.connect(DATABASE_URL, connect_timeout=5) as connection:
                    exists = connection.execute(
                        "SELECT 1 FROM booking_slots WHERE booking_id = %s LIMIT 1", (booking["id"],)
                    ).fetchone()
                if not exists and reserve_booking_slots(
                    booking["professionalId"], booking["date"], booking["time"], duration, booking["id"],
                ):
                    created += 1
            except (KeyError, ValueError, psycopg.Error) as error:
                print(f"Agendamento {booking.get('id')} não migrado: {error}")
    return created


class ProductionServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128


def serve():
    import signal

    migrated = backfill_booking_slots()
    if DATABASE_URL:
        print(f"Faixas de horário migradas: {migrated}", flush=True)
    server = ProductionServer((HOST, PORT), AppHandler)
    print(f"Click Beleza em http://{HOST}:{PORT}", flush=True)

    def stop(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        print("Servidor encerrado.", flush=True)


if __name__ == "__main__":
    serve()
