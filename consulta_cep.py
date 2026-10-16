import json
import base64
import binascii
import hashlib
import math
import re
import secrets
import threading
import time
import uuid
from copy import deepcopy
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen


APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "click_beleza_data.json"
HOST = "127.0.0.1"
PORT = 8765
PILOT_REGION = "Marília, SP"
DATA_LOCK = threading.RLock()
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
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
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


def load_data():
    with DATA_LOCK:
        if not DATA_FILE.exists():
            return {"professionals": deepcopy(SAMPLE_PROFESSIONALS), "bookings": []}
        try:
            with DATA_FILE.open("r", encoding="utf-8") as data_file:
                data = json.load(data_file)
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Não foi possível ler {DATA_FILE.name}: {error}") from error
        if not isinstance(data, dict) or not isinstance(data.get("professionals"), list) or not isinstance(data.get("bookings"), list):
            raise RuntimeError("O arquivo de dados do Click Beleza tem formato inválido.")
        return data


def save_data(data):
    with DATA_LOCK:
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
    if "partners" not in data:
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


class AppHandler(BaseHTTPRequestHandler):
    def _send(self, status, content_type, body):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _json(self, status, payload):
        self._send(status, "application/json; charset=utf-8", json.dumps(payload, ensure_ascii=False))

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

    def do_GET(self):
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        if parsed.path == "/api/cep":
            self._lookup_cep(parse_qs(parsed.query).get("cep", [""])[0])
        elif parsed.path == "/api/professionals/reais":
            self._search_real_professionals(parse_qs(parsed.query))
        elif parsed.path == "/api/professionals":
            self._list_professionals(parse_qs(parsed.query))
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
        elif parsed.path in STATIC_FILES:
            self._serve_static(parsed.path)
        else:
            self._json(404, {"error": "Página não encontrada."})

    def do_POST(self):
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        try:
            data = self._read_json()
            if parsed.path == "/api/professionals":
                self._create_professional(data)
            elif parsed.path == "/api/bookings":
                self._create_booking(data)
            elif re.fullmatch(r"/api/bookings/[^/]+/payment", parsed.path):
                self._simulate_booking_payment(parsed.path.split("/")[-2], data)
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
        parsed = urlsplit(self.path)
        parsed = parsed._replace(path=canonical_api_path(parsed.path))
        match = re.fullmatch(r"/api/bookings/([^/]+)/status", parsed.path)
        if not match:
            self._json(404, {"error": "Operação não encontrada."})
            return
        try:
            data = self._read_json()
            self._update_booking_status(match.group(1), data)
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
        if not professional:
            self._json(404, {"error": "Profissional não encontrado."})
            return
        self._json(200, {"professional": public_professional(professional, include_portfolio=True)})

    def _get_professional_for_edit(self, professional_id):
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
        result.update({"email": professional.get("email", ""), "phone": professional.get("phone", "")})
        service = professional.get("services", [{}])[0]
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
        optional_fields = ("email", "phone", "description", "address", "neighborhood", "serviceName", "price", "duration", "lat", "lon")
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
        with DATA_LOCK:
            stored = load_data()
            stored["professionals"].append(professional)
            save_data(stored)
        self._json(201, {"professional": public_professional(professional)})

    def _update_professional(self, professional_id, data):
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
            updated["rating"] = existing.get("rating", 0)
            updated["reviewCount"] = existing.get("reviewCount", 0)
            updated["reviews"] = existing.get("reviews", [])
            updated["portfolio"] = self._validated_portfolio(data) if "portfolio" in data else existing.get("portfolio", [])
            stored["professionals"] = [
                updated if item["id"] == professional_id else item for item in stored["professionals"]
            ]
            save_data(stored)
        self._json(200, {"professional": public_professional(updated)})

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
        if booking_date < date.today():
            raise ValueError("Escolha uma data de hoje ou futura.")
        if not re.fullmatch(r"(09|10|11|13|14|15|16|17):00", data["time"]):
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
            occupied = any(
                booking.get("professionalId") == professional["id"]
                and booking.get("date") == data["date"]
                and booking.get("time") == data["time"]
                and booking.get("status") in ("solicitado", "confirmado")
                for booking in stored["bookings"]
            )
            if occupied:
                self._json(409, {"error": "Este horário já foi solicitado. Escolha outro horário."})
                return
            booking = {
                "id": uuid.uuid4().hex[:8].upper(), "professionalId": professional["id"],
                "professionalName": professional["name"], "customerName": data["customerName"].strip()[:100],
                "customerEmail": email, "customerPhone": data["customerPhone"].strip()[:30],
                "date": data["date"], "time": data["time"], "serviceName": data["serviceName"].strip()[:100],
                "price": price, "status": "solicitado", "review": False,
                "paymentMode": "demo", "paymentStatus": "demo_pending",
                "paymentDestination": "clickbeleza", "paymentMethod": None,
            }
            stored["bookings"].append(booking)
            save_data(stored)
        self._json(201, {"booking": booking})

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
                self._json(200, {"booking": booking, "message": "Etapa demonstrativa já concluída; nenhuma cobrança foi feita."})
                return
            if booking.get("paymentStatus") != "demo_pending":
                self._json(409, {"error": "A etapa demonstrativa deste pedido não está disponível."})
                return
            booking["paymentStatus"] = "demo_completed"
            booking["paymentMethod"] = payment_method
            save_data(stored)
        self._json(200, {
            "booking": booking,
            "message": "Pagamento apenas simulado. Nenhuma cobrança, transferência ou repasse foi realizado.",
        })

    def _list_bookings(self):
        try:
            data = load_data()
        except RuntimeError as error:
            self._json(500, {"error": str(error)})
            return
        self._json(200, {"bookings": sorted(data["bookings"], key=lambda item: (item["date"], item["time"]))})

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
            allowed = {"solicitado": {"confirmado", "cancelado"}, "confirmado": {"concluido", "cancelado"}}
            if status not in allowed.get(booking["status"], set()):
                raise ValueError("Esta mudança de status não é permitida.")
            booking["status"] = status
            save_data(stored)
        self._json(200, {"booking": booking})

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
        with DATA_LOCK:
            chat = next((c for c in load_data().get("chats", []) if c["id"] == chat_id), None)
        if not chat or not token or not secrets.compare_digest(chat["tokenHash"], token_hash(token)):
            self._json(404, {"error": "Conversa não encontrada."})
            return
        self._json(200, {"chat": self._public_chat(chat)})

    def _list_chats(self):
        with DATA_LOCK:
            chats = load_data().get("chats", [])
        chats = sorted(chats, key=lambda c: c["updatedAt"], reverse=True)
        self._json(200, {"chats": [self._public_chat(c) for c in chats]})

    def _chat_message(self, chat_id, data, from_professional):
        text = self._clean_message(data)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with DATA_LOCK:
            stored = load_data()
            chat = next((c for c in stored.get("chats", []) if c["id"] == chat_id), None)
            valid = chat is not None and (from_professional or (
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
            partner = self._find_partner_by_token(stored, data.get("token"))
            if not partner:
                self._json(404, {"error": "Código de acesso inválido."})
                return
            partner["status"] = "paused" if action == "pause" else "active"
            save_data(stored)
        self._json(200, {"status": partner["status"]})

    def log_message(self, format_string, *args):
        print(f"[{self.log_date_time_string()}] {format_string % args}")


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"Click Beleza local: http://{HOST}:{PORT}")
    print("Protótipo sem autenticação: não exponha na internet nem use dados pessoais reais.")
    print("Pressione Ctrl+C para encerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        server.server_close()
