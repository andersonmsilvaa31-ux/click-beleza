import copy
import http.cookiejar
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import consulta_cep as backend


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class QuietHandler(backend.AppHandler):
    def log_message(self, *_):
        return


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_data_file = backend.DATA_FILE
        self.original_database_url = backend.DATABASE_URL
        backend.DATA_FILE = Path(self.temporary_directory.name) / "data.json"
        backend.DATABASE_URL = None
        backend.clear_caches()
        backend.RATE_HITS.clear()
        professional = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        professional.update({
            "id": "test-professional",
            "demo": False,
            "name": "Profissional de teste",
            "email": "test@example.com",
            "phone": "11999999999",
        })
        backend.save_data({"professionals": [professional], "bookings": []})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.auth_cookies = http.cookiejar.CookieJar()
        self.auth_client = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.auth_cookies)
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        backend.DATA_FILE = self.original_data_file
        backend.DATABASE_URL = self.original_database_url
        self.temporary_directory.cleanup()

    def test_privacy_requests_are_public_validated_and_admin_managed(self):
        status, _ = self.post_json("/api/privacy/requests", {"type": "hack", "name": "Ana", "contact": "a@b.com", "message": "mensagem valida aqui"})
        self.assertEqual(status, 400)
        status, _ = self.post_json("/api/privacy/requests", {"type": "exclusao", "name": "Ana", "contact": "x", "message": "mensagem valida aqui"})
        self.assertEqual(status, 400)
        status, body = self.post_json("/api/privacy/requests", {"type": "exclusao", "name": "Ana", "contact": "ana@example.com", "message": "Quero excluir meus dados."})
        self.assertEqual(status, 201)
        self.assertTrue(body["protocol"].startswith("CB-"))
        self.assertEqual(self.get_json("/api/admin/overview")[0], 401)
        self.create_user("admin")
        self.assertEqual(self.authenticated_request("/api/auth/login", "POST", {"identifier": "admin@example.com", "password": "test-password-123"})[0], 200)
        status, overview = self.authenticated_request("/api/admin/overview")
        self.assertEqual(status, 200)
        request_id = overview["privacyRequests"][0]["id"]
        status, _ = self.authenticated_request(f"/api/admin/privacy/{request_id}/status", "PATCH", {"status": "concluida"})
        self.assertEqual(status, 200)
        status, _ = self.authenticated_request(f"/api/admin/privacy/{request_id}/status", "PATCH", {"status": "x"})
        self.assertEqual(status, 400)
        for _ in range(5):
            status, _ = self.post_json("/api/privacy/requests", {"type": "acesso", "name": "Ana", "contact": "ana@example.com", "message": "Quero acessar meus dados."})
        self.assertEqual(status, 429)

    def test_mercadopago_checkout_webhook_signature_and_refund(self):
        import hashlib, hmac, time as time_module
        import payments
        env = {"CLICK_BELEZA_MP_ACCESS_TOKEN": "TEST-token", "CLICK_BELEZA_MP_WEBHOOK_SECRET": "segredo",
               "CLICK_BELEZA_PUBLIC_URL": "https://exemplo.com.br", "CLICK_BELEZA_FEE_PERCENT": "10"}
        calls = []
        remote = {"status": "approved", "amount": None}

        def fake_http(method, path, token, payload=None, idempotency_key=None):
            calls.append((method, path, token, payload, idempotency_key))
            if path == "/checkout/preferences":
                return {"id": "pref-1", "init_point": "https://mp.test/pay/pref-1"}
            if method == "GET":
                return {"id": 555, "status": remote["status"], "transaction_amount": remote["amount"],
                        "currency_id": "BRL", "external_reference": booking_id, "payment_type_id": "bank_transfer"}
            return {"id": 1}

        def webhook(signed=True):
            ts = str(int(time_module.time()))
            manifest = f"id:555;request-id:req-1;ts:{ts};"
            v1 = hmac.new(b"segredo", manifest.encode(), hashlib.sha256).hexdigest() if signed else "0" * 64
            request = urllib.request.Request(
                self.base_url + "/api/payments/webhook?data.id=555", method="POST",
                data=json.dumps({"type": "payment", "data": {"id": "555"}}).encode(),
                headers={"Content-Type": "application/json", "x-signature": f"ts={ts},v1={v1}", "x-request-id": "req-1"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code

        with patch.dict(os.environ, env), patch.object(payments, "http", fake_http):
            professional = backend.load_data()["professionals"][0]
            service = professional["services"][0]
            day = (date.today() + timedelta(days=2)).isoformat()
            status, created = self.post_json("/api/bookings", {
                "professionalId": professional["id"], "customerName": "Ana", "customerPhone": "11999999999",
                "date": day, "time": "10:00", "serviceName": service["name"], "price": service["price"]})
            self.assertEqual(status, 201)
            booking_id = created["booking"]["id"]
            token = created["customerToken"]
            remote["amount"] = service["price"]
            self.assertEqual(created["booking"]["paymentMode"], "mercadopago")
            self.assertEqual(created["booking"]["paymentStatus"], "pending")
            # sem o código privado não há cobrança
            self.assertEqual(self.post_json(f"/api/bookings/{booking_id}/checkout", {})[0], 401)
            status, body = self.post_json(f"/api/bookings/{booking_id}/checkout", {"customerToken": token})
            self.assertEqual((status, body["checkoutUrl"]), (200, "https://mp.test/pay/pref-1"))
            preference = calls[0][3]
            self.assertEqual(preference["external_reference"], booking_id)
            self.assertEqual(preference["items"][0]["unit_price"], float(service["price"]))
            # webhook falso ou sem assinatura é recusado e não confirma nada
            self.assertEqual(webhook(signed=False), 401)
            self.assertEqual(backend.load_data()["bookings"][0]["paymentStatus"], "pending")
            # valor divergente não confirma
            remote["amount"] = 1
            self.assertEqual(webhook(), 200)
            self.assertEqual(backend.load_data()["bookings"][0]["paymentStatus"], "pending")
            remote["amount"] = service["price"]
            self.assertEqual(webhook(), 200)
            self.assertEqual(webhook(), 200)  # idempotente
            stored = backend.load_data()["bookings"][0]
            self.assertEqual((stored["paymentStatus"], stored["paymentMethod"], stored["paymentId"]), ("paid", "pix", "555"))
            self.assertEqual(self.post_json(f"/api/bookings/{booking_id}/checkout", {"customerToken": token})[0], 409)
            # cancelar um pedido pago gera estorno
            status, body = self.authenticated_request(f"/api/bookings/{booking_id}/status", "PATCH",
                                             {"status": "cancelado", "customerToken": token})
            self.assertEqual(status, 200)
            self.assertEqual(body["booking"]["paymentStatus"], "refunded")
            self.assertTrue(any(c[1] == "/v1/payments/555/refunds" for c in calls))
        self.assertFalse(payments.verify_signature({"x-signature": "ts=1,v1=aa"}, "1", secret="s"))

    def test_professional_connects_mercadopago_with_oauth_and_split(self):
        import payments
        from cryptography.fernet import Fernet
        from urllib.parse import urlsplit, parse_qs
        env = {"CLICK_BELEZA_MP_ACCESS_TOKEN": "TEST-token", "CLICK_BELEZA_MP_WEBHOOK_SECRET": "segredo",
               "CLICK_BELEZA_PUBLIC_URL": "https://exemplo.com.br", "CLICK_BELEZA_MP_CLIENT_ID": "123",
               "CLICK_BELEZA_MP_CLIENT_SECRET": "cs", "CLICK_BELEZA_ENCRYPTION_KEY": Fernet.generate_key().decode()}
        calls = []

        def fake_http(method, path, token, payload=None, idempotency_key=None):
            calls.append((method, path, token, payload))
            if path == "/oauth/token":
                return {"access_token": "SELLER-1", "refresh_token": "R1", "user_id": 77, "expires_in": 15000000}
            return {"id": "pref-2", "init_point": "https://mp.test/pay/pref-2"}

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        with patch.dict(os.environ, env), patch.object(payments, "http", fake_http):
            self.create_user("professional", "test-professional")
            self.assertEqual(self.authenticated_request("/api/professional/payments")[0], 401)
            self.assertEqual(self.authenticated_request("/api/auth/login", "POST", {"identifier": "professional@example.com", "password": "test-password-123"})[0], 200)
            status, info = self.authenticated_request("/api/professional/payments")
            self.assertEqual((status, info["connected"], info["connectionAvailable"]), (200, False, True))
            status, body = self.authenticated_request("/api/professional/payments/connect", "POST", {})
            self.assertEqual(status, 200)
            state = parse_qs(urlsplit(body["url"]).query)["state"][0]

            def callback(state_value, code="abc"):
                query = urllib.parse.urlencode({"code": code, "state": state_value})
                opener = urllib.request.build_opener(NoRedirect)
                try:
                    opener.open(self.base_url + "/api/payments/oauth/callback?" + query, timeout=5)
                except urllib.error.HTTPError as error:
                    return error.code, error.headers["Location"]

            self.assertEqual(callback(state + "x"), (302, "/professional-dashboard.html?mp=erro"))
            self.assertEqual(callback(state), (302, "/professional-dashboard.html?mp=ok"))
            self.assertEqual(callback(state), (302, "/professional-dashboard.html?mp=erro"))  # uso único
            stored = next(u for u in backend.load_data()["users"] if u["role"] == "professional")
            self.assertNotIn("SELLER-1", json.dumps(stored))  # token cifrado em repouso
            self.assertEqual(self.authenticated_request("/api/professional/payments")[1]["connected"], True)
            self.assertNotIn("mpSeller", json.dumps(self.authenticated_request("/api/auth/me")[1]))
            # a cobrança passa a usar a conta do profissional e retém a comissão
            professional = backend.load_data()["professionals"][0]
            service = professional["services"][0]
            day = (date.today() + timedelta(days=2)).isoformat()
            status, created = self.post_json("/api/bookings", {
                "professionalId": professional["id"], "customerName": "Ana", "customerPhone": "11999999999",
                "date": day, "time": "10:00", "serviceName": service["name"], "price": service["price"]})
            self.assertEqual(status, 201)
            status, _ = self.post_json(f"/api/bookings/{created['booking']['id']}/checkout", {"customerToken": created["customerToken"]})
            self.assertEqual(status, 200)
            pref = next(c for c in calls if c[1] == "/checkout/preferences")
            self.assertEqual(pref[2], "SELLER-1")
            self.assertEqual(pref[3]["marketplace_fee"], round(float(service["price"]) * 0.10, 2))
            self.assertEqual(self.authenticated_request("/api/professional/payments/disconnect", "POST", {})[0], 200)
            self.assertFalse(self.authenticated_request("/api/professional/payments")[1]["connected"])

    def test_admin_overview_includes_financial_balance(self):
        self.create_user("admin")
        self.assertEqual(self.authenticated_request("/api/auth/login", "POST", {"identifier": "admin@example.com", "password": "test-password-123"})[0], 200)
        professional = backend.load_data()["professionals"][0]
        service = professional["services"][0]
        day = (date.today() + timedelta(days=2)).isoformat()
        status, created = self.post_json("/api/bookings", {
            "professionalId": professional["id"], "customerName": "Ana", "customerPhone": "11999999999",
            "date": day, "time": "10:00", "serviceName": service["name"], "price": service["price"]})
        self.assertEqual(status, 201)
        finance = self.authenticated_request("/api/admin/overview")[1]["finance"]
        self.assertEqual(finance["orders"]["open"], 1)
        self.assertEqual(finance["orders"]["openValue"], float(service["price"]))
        self.assertEqual(finance["payments"]["paidGross"], 0)
        self.assertEqual(finance["feePercent"], 10.0)
        row = next(item for item in finance["professionals"] if item["id"] == professional["id"])
        self.assertEqual(row["orders"], 1)
        self.assertEqual(self.get_json("/api/admin/overview")[0], 401)

    def test_static_assets_use_etag_gzip_head_and_options(self):
        request = urllib.request.Request(self.base_url + "/app.js", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.headers["Content-Encoding"], "gzip")
            etag = response.headers["ETag"]
            self.assertNotIn("Python", response.headers.get("Server", ""))
        again = urllib.request.Request(self.base_url + "/app.js", headers={"If-None-Match": etag})
        with self.assertRaises(urllib.error.HTTPError) as cached:
            urllib.request.urlopen(again, timeout=5)
        self.assertEqual(cached.exception.code, 304)
        head = urllib.request.Request(self.base_url + "/style.css", method="HEAD")
        with urllib.request.urlopen(head, timeout=5) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"")
        options = urllib.request.Request(self.base_url + "/api/bookings", method="OPTIONS")
        with urllib.request.urlopen(options, timeout=5) as response:
            self.assertEqual(response.status, 204)

    def test_security_headers_health_and_rate_limit(self):
        with urllib.request.urlopen(self.base_url + "/", timeout=5) as response:
            self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            self.assertEqual(response.headers["X-Frame-Options"], "DENY")
            self.assertIn("camera=()", response.headers["Permissions-Policy"])
        self.assertEqual(self.get_json("/healthz")[0], 200)
        status, body = self.get_json("/readyz")
        self.assertEqual((status, body["status"]), (200, "ready"))
        original = backend.RATE_LIMIT_PER_MINUTE
        backend.RATE_LIMIT_PER_MINUTE = 3
        try:
            codes = [self.get_json("/api/professionals")[0] for _ in range(6)]
        finally:
            backend.RATE_LIMIT_PER_MINUTE = original
        self.assertIn(429, codes)
        self.assertEqual(self.get_json("/healthz")[0], 200)
        with urllib.request.urlopen(self.base_url + "/style.css", timeout=5) as response:
            self.assertEqual(response.status, 200)

    def get_json(self, path):
        try:
            with urllib.request.urlopen(self.base_url + path, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def post_json(self, path, payload):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def authenticated_request(self, path, method="GET", payload=None):
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"} if payload is not None else {},
            method=method,
        )
        try:
            with self.auth_client.open(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def create_user(self, role, professional_id=None, email=None):
        data = backend.load_data()
        user = backend.new_user(
            email or f"{role}@example.com",
            "test-password-123",
            role,
            professional_id,
        )
        data["users"].append(user)
        backend.save_data(data)
        return user

    def test_overpass_places_are_returned_without_invented_prices(self):
        payload = {"elements": [
            {
                "type": "way",
                "id": 123,
                "center": {"lat": -22.21, "lon": -49.94},
                "tags": {
                    "name": "Barbearia mapeada",
                    "craft": "barber",
                    "addr:street": "Rua Exemplo",
                    "addr:housenumber": "42",
                },
            },
            {
                "type": "node",
                "id": 456,
                "lat": -22.22,
                "lon": -49.95,
                "tags": {"name": "Local de beleza", "shop": "beauty"},
            },
        ]}
        with patch.object(backend, "urlopen", return_value=FakeResponse(payload)) as upstream:
            status, response = self.get_json(
                "/api/profissionais/reais?city=Mar%C3%ADlia%2C%20SP&servico=barbeiro"
            )
        self.assertEqual(status, 200)
        self.assertEqual(len(response["professionals"]), 1)
        place = response["professionals"][0]
        self.assertEqual(place["name"], "Barbearia mapeada")
        self.assertEqual(place["address"], "Rua Exemplo, 42")
        self.assertTrue(place["external"])
        self.assertIsNone(place["price"])
        query = urllib.parse.parse_qs(upstream.call_args.args[0].data.decode("utf-8"))["data"][0]
        self.assertIn('area["name"="Marília"]', query)
        self.assertIn('nwr["craft"="barber"]', query)

    def test_tattoo_and_piercing_category_search_includes_both(self):
        data = backend.load_data()
        tattoo = copy.deepcopy(data["professionals"][0])
        tattoo.update({"id": "test-tattoo", "service": "tatuagem"})
        piercing = copy.deepcopy(data["professionals"][0])
        piercing.update({"id": "test-piercing", "service": "piercing"})
        data["professionals"].extend((tattoo, piercing))
        backend.save_data(data)

        status, response = self.get_json(
            "/api/profissionais?servico=tatuagem_piercing&city=Mar%C3%ADlia"
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            {professional["service"] for professional in response["professionals"]},
            {"tatuagem", "piercing"},
        )

    def test_tattoo_and_piercing_map_search_includes_both_business_types(self):
        payload = {"elements": [
            {"type": "node", "id": 123, "lat": -22.21, "lon": -49.94,
             "tags": {"name": "Estúdio de tatuagem", "shop": "tattoo"}},
            {"type": "node", "id": 456, "lat": -22.22, "lon": -49.95,
             "tags": {"name": "Estúdio de piercing", "shop": "piercing"}},
        ]}
        with patch.object(backend, "urlopen", return_value=FakeResponse(payload)) as upstream:
            status, response = self.get_json(
                "/api/profissionais/reais?city=Mar%C3%ADlia&servico=tatuagem_piercing"
            )

        self.assertEqual(status, 200)
        self.assertEqual(len(response["professionals"]), 2)
        self.assertTrue(all(item["service"] == "tatuagem_piercing" for item in response["professionals"]))
        query = urllib.parse.parse_qs(upstream.call_args.args[0].data.decode("utf-8"))["data"][0]
        self.assertIn('nwr["shop"="tattoo"]', query)
        self.assertIn('nwr["shop"="piercing"]', query)

    def test_partner_examples_list_contains_eight_transparently_marked_items(self):
        status, response = self.get_json("/api/partners")

        self.assertEqual(status, 200)
        self.assertEqual(len(response["partners"]), 8)
        self.assertTrue(all(partner["demo"] for partner in response["partners"]))

    def test_overpass_gps_search_uses_twelve_kilometer_radius(self):
        with patch.object(backend, "urlopen", return_value=FakeResponse({"elements": []})) as upstream:
            status, response = self.get_json(
                "/api/profissionais/reais?lat=-22.2&lon=-49.9&servico=manicure"
            )
        self.assertEqual(status, 200)
        self.assertEqual(response["professionals"], [])
        query = urllib.parse.parse_qs(upstream.call_args.args[0].data.decode("utf-8"))["data"][0]
        self.assertIn("around:12000,-22.2,-49.9", query)
        self.assertIn('nwr["shop"="beauty"]', query)

    def test_overpass_failure_is_reported_as_a_gateway_error(self):
        upstream_error = urllib.error.HTTPError(
            "https://overpass-api.de/api/interpreter", 502, "Bad Gateway", {}, None
        )
        with patch.object(backend, "urlopen", side_effect=upstream_error):
            status, response = self.get_json("/api/profissionais/reais?city=Marilia")
        self.assertEqual(status, 502)
        self.assertIn("OpenStreetMap", response["error"])

    def test_invalid_city_is_rejected_before_upstream_request(self):
        with patch.object(backend, "urlopen") as upstream:
            status, response = self.get_json(
                "/api/profissionais/reais?city=%22%3B%20out%3B%20%2F%2F"
            )
        self.assertEqual(status, 400)
        self.assertIn("cidade", response["error"])
        upstream.assert_not_called()

    def test_booking_starts_demo_checkout_and_ignores_client_payment_state(self):
        payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente de teste",
            "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
            "paymentDestination": "estabelecimento",
            "paymentStatus": "pago",
            "paymentMethod": "credito",
            "cardNumber": "4111111111111111",
        }
        status, response = self.post_json("/api/agendamentos", payload)
        self.assertEqual(status, 201)
        booking = response["booking"]
        self.assertEqual(booking["paymentMode"], "demo")
        self.assertEqual(booking["paymentStatus"], "demo_pending")
        self.assertEqual(booking["paymentDestination"], "clickbeleza")
        self.assertIsNone(booking["paymentMethod"])
        self.assertEqual(booking["customerEmail"], "")
        self.assertNotIn("cardNumber", booking)
        self.assertNotIn("customerTokenHash", booking)
        self.assertTrue(response["customerToken"])

    def test_demo_payment_records_method_without_processing_money(self):
        payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente de teste",
            "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
        }
        status, response = self.post_json("/api/agendamentos", payload)
        self.assertEqual(status, 201)
        booking_id = response["booking"]["id"]
        customer_token = response["customerToken"]

        status, response = self.post_json(
            f"/api/agendamentos/{booking_id}/pagamento",
            {"paymentMethod": "pix", "cardNumber": "4111111111111111", "customerToken": customer_token},
        )

        self.assertEqual(status, 200)
        booking = response["booking"]
        self.assertEqual(booking["paymentMode"], "demo")
        self.assertEqual(booking["paymentDestination"], "clickbeleza")
        self.assertEqual(booking["paymentStatus"], "demo_completed")
        self.assertEqual(booking["paymentMethod"], "pix")
        self.assertNotIn("cardNumber", booking)
        self.assertIn("nenhuma cobrança", response["message"].casefold())

        status, duplicate = self.post_json(
            f"/api/agendamentos/{booking_id}/pagamento",
            {"paymentMethod": "pix", "customerToken": customer_token},
        )
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["booking"]["paymentStatus"], "demo_completed")

    def test_demo_payment_rejects_unsupported_method_without_changing_order(self):
        payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente de teste",
            "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
        }
        status, response = self.post_json("/api/agendamentos", payload)
        self.assertEqual(status, 201)

        status, response = self.post_json(
            f"/api/agendamentos/{response['booking']['id']}/pagamento",
            {"paymentMethod": {"card": "not accepted"}},
        )

        self.assertEqual(status, 400)
        self.assertIn("Pix ou cartão", response["error"])

    def test_invalid_optional_email_is_rejected(self):
        payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente de teste",
            "customerEmail": "email-invalido",
            "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "11:00",
            "serviceName": "Corte masculino",
            "price": 45,
        }
        status, response = self.post_json("/api/agendamentos", payload)
        self.assertEqual(status, 400)
        self.assertIn("e-mail", response["error"])

    def test_professional_can_join_with_only_basic_profile_details(self):
        payload = {
            "name": "Studio Novo",
            "service": "manicure",
            "city": "Marília",
            "email": "studio@example.com",
            "password": "senha-segura-123",
        }
        status, response = self.authenticated_request("/api/profissionais", "POST", payload)
        self.assertEqual(status, 201)
        professional_id = response["professional"]["id"]
        self.assertEqual(response["professional"]["profileCompletion"], 50)
        self.assertEqual(response["professional"]["services"], [])

        status, detail = self.get_json(f"/api/profissionais/{professional_id}")
        self.assertEqual(status, 200)
        self.assertEqual(detail["professional"]["profileCompletion"], 50)
        self.assertEqual(detail["professional"]["city"], "Marília")
        self.assertNotIn("phone", detail["professional"])
        self.assertNotIn("email", detail["professional"])
        status, account = self.authenticated_request("/api/auth/me")
        self.assertEqual(status, 200)
        self.assertEqual(account["user"]["role"], "professional")
        self.assertEqual(account["user"]["professionalId"], professional_id)

    def test_professional_can_log_in_with_email_or_validated_cpf_and_eight_character_password(self):
        cpf = "529.982.247-25"
        payload = {
            "name": "Studio CPF",
            "service": "barbeiro",
            "city": "Marília",
            "email": "cpf-professional@example.com",
            "cpf": cpf,
            "password": "senha123",
        }
        status, response = self.post_json("/api/profissionais", payload)
        self.assertEqual(status, 201)
        professional_id = response["professional"]["id"]

        status, login = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"identifier": "52998224725", "password": "senha123"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["user"]["professionalId"], professional_id)
        self.assertNotIn("cpf", login["user"])
        self.assertNotIn("cpfHash", login["user"])

        status, login = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"identifier": "cpf-professional@example.com", "password": "senha123"},
        )
        self.assertEqual(status, 200)
        user = next(user for user in backend.load_data()["users"] if user["email"] == payload["email"])
        self.assertNotIn(cpf, json.dumps(user))
        self.assertIn("cpfHash", user)

        duplicate = {**payload, "name": "Outro Studio", "email": "cpf-duplicate@example.com"}
        status, response = self.post_json("/api/profissionais", duplicate)
        self.assertEqual(status, 400)
        self.assertIn("CPF", response["error"])

        invalid = {**payload, "email": "cpf-invalid@example.com", "cpf": "111.111.111-11"}
        status, response = self.post_json("/api/profissionais", invalid)
        self.assertEqual(status, 400)
        self.assertIn("CPF", response["error"])

        too_short = {**payload, "email": "cpf-short-password@example.com", "cpf": "", "password": "1234567"}
        status, response = self.post_json("/api/profissionais", too_short)
        self.assertEqual(status, 400)
        self.assertIn("8", response["error"])

    def test_admin_overview_requires_admin_and_returns_management_data(self):
        self.assertEqual(self.get_json("/api/admin/overview")[0], 401)
        self.assertEqual(self.get_json("/api/bookings")[0], 401)
        booking_status, _ = self.post_json("/api/bookings", {
            "professionalId": "test-professional",
            "customerName": "Cliente Visível",
            "customerEmail": "cliente@example.com",
            "customerPhone": "11987654321",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
        })
        self.assertEqual(booking_status, 201)
        self.create_user("admin")
        status, login = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"email": "admin@example.com", "password": "test-password-123"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(login["user"]["role"], "admin")

        status, overview = self.authenticated_request("/api/admin/overview")

        self.assertEqual(status, 200)
        self.assertEqual(set(overview), {"professionals", "bookings", "chats", "partners", "users", "clients", "privacyRequests", "finance"})
        self.assertTrue(overview["professionals"])
        self.assertNotIn("passwordHash", overview["users"][0])
        self.assertEqual(len(overview["clients"]), 1)
        self.assertEqual(overview["clients"][0]["email"], "cliente@example.com")

    def test_customers_can_manage_only_orders_with_their_private_token(self):
        first_payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente A",
            "customerPhone": "11911111111",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
        }
        second_payload = {
            **first_payload,
            "customerName": "Cliente B",
            "customerPhone": "11922222222",
            "time": "11:00",
        }
        first_status, first = self.post_json("/api/bookings", first_payload)
        second_status, second = self.post_json("/api/bookings", second_payload)
        self.assertEqual((first_status, second_status), (201, 201))

        status, error = self.post_json(
            f"/api/bookings/{first['booking']['id']}/payment",
            {"paymentMethod": "pix"},
        )
        self.assertEqual(status, 401)
        self.assertIn("código privado", error["error"])

        status, error = self.post_json(
            f"/api/bookings/{first['booking']['id']}/access",
            {"customerToken": second["customerToken"]},
        )
        self.assertEqual(status, 401)
        self.assertIn("código privado", error["error"])

        status, access = self.post_json(
            f"/api/bookings/{first['booking']['id']}/access",
            {"customerToken": first["customerToken"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(access["booking"]["customerName"], "Cliente A")
        self.assertNotIn("customerTokenHash", access["booking"])

        status, error = self.authenticated_request(
            f"/api/bookings/{second['booking']['id']}/status",
            "PATCH",
            {"status": "cancelado", "customerToken": first["customerToken"]},
        )
        self.assertEqual(status, 401)
        self.assertIn("código privado", error["error"])

        status, response = self.authenticated_request(
            f"/api/bookings/{first['booking']['id']}/status",
            "PATCH",
            {"status": "cancelado", "customerToken": first["customerToken"]},
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["booking"]["status"], "cancelado")

        status, error = self.authenticated_request(
            f"/api/bookings/{second['booking']['id']}/status",
            "PATCH",
            {"status": "confirmado", "customerToken": second["customerToken"]},
        )
        self.assertEqual(status, 403)
        self.assertIn("só podem cancelar", error["error"])

    def test_customer_review_requires_the_booking_owner_token(self):
        payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente de teste",
            "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
        }
        status, response = self.post_json("/api/bookings", payload)
        self.assertEqual(status, 201)
        booking_id = response["booking"]["id"]
        customer_token = response["customerToken"]
        stored = backend.load_data()
        next(item for item in stored["bookings"] if item["id"] == booking_id)["status"] = "concluido"
        backend.save_data(stored)

        status, error = self.post_json(
            f"/api/bookings/{booking_id}/review",
            {"rating": 5, "comment": "Bom atendimento."},
        )
        self.assertEqual(status, 401)
        self.assertIn("código privado", error["error"])

        status, response = self.post_json(
            f"/api/bookings/{booking_id}/review",
            {"rating": 5, "comment": "Bom atendimento.", "customerToken": customer_token},
        )
        self.assertEqual(status, 201)
        self.assertEqual(response["review"]["rating"], 5)

    def test_professional_booking_access_is_limited_to_linked_profile(self):
        second = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        second.update({"id": "other-professional", "demo": False, "name": "Outro perfil"})
        future = (date.today() + timedelta(days=1)).isoformat()
        data = backend.load_data()
        data["professionals"].append(second)
        data["bookings"] = [
            {"id": "own-booking", "professionalId": "test-professional", "date": future, "time": "10:00"},
            {"id": "other-booking", "professionalId": "other-professional", "date": future, "time": "11:00"},
        ]
        data["chats"] = [
            {
                "id": "own-chat",
                "professionalId": "test-professional",
                "professionalName": "Profissional de teste",
                "customerName": "Cliente próprio",
                "tokenHash": backend.token_hash("own-customer-token"),
                "createdAt": "2026-01-01T09:00:00",
                "updatedAt": "2026-01-01T09:00:00",
                "messages": [{"from": "customer", "text": "Oi", "at": "2026-01-01T09:00:00"}],
            },
            {
                "id": "other-chat",
                "professionalId": "other-professional",
                "professionalName": "Outro perfil",
                "customerName": "Outro cliente",
                "tokenHash": backend.token_hash("other-customer-token"),
                "createdAt": "2026-01-01T09:00:00",
                "updatedAt": "2026-01-01T09:00:00",
                "messages": [{"from": "customer", "text": "Oi", "at": "2026-01-01T09:00:00"}],
            },
        ]
        backend.save_data(data)
        self.create_user("professional", "test-professional")
        status, _ = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"email": "professional@example.com", "password": "test-password-123"},
        )
        self.assertEqual(status, 200)

        status, response = self.authenticated_request("/api/bookings")

        self.assertEqual(status, 200)
        self.assertEqual([booking["id"] for booking in response["bookings"]], ["own-booking"])
        status, response = self.authenticated_request("/api/chats")
        self.assertEqual(status, 200)
        self.assertEqual([chat["id"] for chat in response["chats"]], ["own-chat"])
        self.assertEqual(
            self.authenticated_request("/api/admin/overview")[0],
            403,
        )
        status, error = self.authenticated_request(
            "/api/bookings/other-booking/status",
            "PATCH",
            {"status": "confirmado"},
        )
        self.assertEqual(status, 403)
        self.assertIn("acesso a este pedido", error["error"])
        status, error = self.authenticated_request(
            "/api/chats/other-chat/reply",
            "POST",
            {"text": "Mensagem indevida"},
        )
        self.assertEqual(status, 404)

    def test_professional_financial_records_are_private_and_manual(self):
        self.assertEqual(self.get_json("/api/professional/transactions")[0], 401)
        second = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        second.update({"id": "other-professional", "demo": False})
        data = backend.load_data()
        data["professionals"].append(second)
        data["financialTransactions"] = [
            {
                "id": "own-record",
                "professionalId": "test-professional",
                "type": "entrada",
                "amount": 90,
                "description": "Atendimento",
                "date": date.today().isoformat(),
                "recordedManually": True,
            },
            {
                "id": "other-record",
                "professionalId": "other-professional",
                "type": "saida",
                "amount": 20,
                "description": "Material",
                "date": date.today().isoformat(),
                "recordedManually": True,
            },
        ]
        backend.save_data(data)
        self.create_user("professional", "test-professional")
        status, _ = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"email": "professional@example.com", "password": "test-password-123"},
        )
        self.assertEqual(status, 200)

        status, response = self.authenticated_request("/api/professional/transactions")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in response["transactions"]], ["own-record"])

        status, response = self.authenticated_request(
            "/api/professional/transactions",
            "POST",
            {
                "type": "saida",
                "amount": "25.50",
                "description": "Produtos",
                "date": date.today().isoformat(),
                "professionalId": "other-professional",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(response["transaction"]["professionalId"], "test-professional")
        self.assertTrue(response["transaction"]["recordedManually"])
        self.assertEqual(response["transaction"]["amount"], 25.5)

        status, response = self.authenticated_request("/api/professional/transactions")
        self.assertEqual(status, 200)
        self.assertEqual({item["professionalId"] for item in response["transactions"]}, {"test-professional"})
        self.assertEqual(
            self.authenticated_request(
                "/api/professional/transactions",
                "POST",
                {"type": "entrada", "amount": 0, "description": "Inválido"},
            )[0],
            400,
        )

        self.create_user("admin")
        status, _ = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"email": "admin@example.com", "password": "test-password-123"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.authenticated_request("/api/professional/transactions")[0], 403)

    def test_concurrent_bookings_logins_and_payments_do_not_lose_or_duplicate_data(self):
        self.create_user("professional", "test-professional")
        service = backend.load_data()["professionals"][0]["services"][0]
        day = (date.today() + timedelta(days=3)).isoformat()

        def book(time_slot, number):
            return self.post_json("/api/bookings", {
                "professionalId": "test-professional", "customerName": f"Cliente {number}",
                "customerPhone": "11999999999", "date": day, "time": time_slot,
                "serviceName": service["name"], "price": service["price"],
            })

        def login(_):
            return self.post_json(
                "/api/auth/login",
                {"identifier": "professional@example.com", "password": "test-password-123"},
            )[0]

        same_slot = []
        threads = [threading.Thread(target=lambda n=n: same_slot.append(book("09:00", n)[0])) for n in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(same_slot), [201] + [409] * 11)

        results = {}
        slots = ["10:00", "11:00", "13:00", "14:00", "15:00", "16:00", "17:00"]
        workers = [threading.Thread(target=lambda n=n: results.__setitem__(f"login{n}", login(n))) for n in range(6)]
        workers += [
            threading.Thread(target=lambda s=s, n=n: results.__setitem__(s, book(s, n)))
            for n, s in enumerate(slots)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertTrue(all(results[f"login{n}"] == 200 for n in range(6)))
        created = [results[s] for s in slots if results[s][0] == 201]
        self.assertEqual(len(created), len(slots))
        self.assertEqual(len(backend.load_data()["bookings"]), len(slots) + 1)

        booking, token = created[0][1]["booking"]["id"], created[0][1]["customerToken"]
        payments = []
        workers = [
            threading.Thread(target=lambda: payments.append(self.post_json(
                f"/api/bookings/{booking}/payment", {"customerToken": token, "paymentMethod": "pix"})[0]))
            for _ in range(8)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(set(payments), {200})
        stored = next(item for item in backend.load_data()["bookings"] if item["id"] == booking)
        self.assertEqual(stored["paymentStatus"], "demo_completed")

    def test_permission_matrix_blocks_anonymous_customers_and_other_professionals(self):
        other = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        other.update({"id": "other-professional", "demo": False, "name": "Outro"})
        data = backend.load_data()
        data["professionals"].append(other)
        backend.save_data(data)
        self.create_user("professional", "other-professional", "other@example.com")
        service = data["professionals"][0]["services"][0]
        _, created = self.post_json("/api/bookings", {
            "professionalId": "test-professional", "customerName": "Cliente", "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=2)).isoformat(), "time": "10:00",
            "serviceName": service["name"], "price": service["price"],
        })
        booking_id = created["booking"]["id"]
        _, chat = self.post_json("/api/chats", {
            "professionalId": "test-professional", "customerName": "Cliente", "text": "Olá",
        })
        chat_id = chat["chat"]["id"]

        for path in ("/api/bookings", "/api/chats", "/api/admin/overview", "/api/professional/transactions"):
            self.assertEqual(self.get_json(path)[0], 401, path)
        self.assertEqual(self.get_json(f"/api/chats/{chat_id}")[0], 404)
        self.assertEqual(self.post_json(f"/api/bookings/{booking_id}/access", {})[0], 401)
        self.assertEqual(self.post_json(f"/api/bookings/{booking_id}/access", {"customerToken": "x" * 40})[0], 401)
        self.assertEqual(self.post_json(f"/api/chats/{chat_id}/reply", {"text": "oi"})[0], 401)
        self.assertEqual(self.post_json("/api/chats/%s/messages" % chat_id, {"text": "oi", "token": "x"})[0], 404)

        status, _ = self.authenticated_request(
            "/api/auth/login", "POST", {"identifier": "other@example.com", "password": "test-password-123"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.authenticated_request("/api/bookings")[1]["bookings"], [])
        self.assertEqual(self.authenticated_request("/api/chats")[1]["chats"], [])
        self.assertEqual(self.authenticated_request(f"/api/chats/{chat_id}")[0], 404)
        self.assertEqual(self.authenticated_request(f"/api/bookings/{booking_id}/access", "POST", {})[0], 403)
        self.assertEqual(
            self.authenticated_request(f"/api/bookings/{booking_id}/status", "PATCH", {"status": "confirmado"})[0], 403
        )
        self.assertEqual(
            self.authenticated_request(f"/api/chats/{chat_id}/reply", "POST", {"text": "invasão"})[0], 404
        )
        self.assertEqual(self.authenticated_request("/api/admin/overview")[0], 403)
        self.assertEqual(
            self.authenticated_request("/api/professionals/test-professional", "PUT", {"name": "Invasor"})[0] in (401, 403, 404),
            True,
        )
        self.assertEqual(backend.load_data()["bookings"][0]["status"], "solicitado")

    def test_schedule_respects_duration_overlap_lunch_cancellation_and_brasilia_time(self):
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        services = {item["name"]: item for item in backend.load_data()["professionals"][0]["services"]}
        long_service, short_service = services["Corte e barba"], services["Corte masculino"]
        self.assertEqual((long_service["duration"], short_service["duration"]), (65, 40))

        def times(service):
            query = urllib.parse.urlencode({"date": tomorrow, "service": service["name"]})
            status, body = self.get_json(f"/api/professionals/test-professional/availability?{query}")
            self.assertEqual(status, 200)
            self.assertEqual(set(body), {"date", "duration", "times"})
            return body["times"]

        def book(service, time_slot):
            return self.post_json("/api/bookings", {
                "professionalId": "test-professional", "customerName": "Cliente", "customerPhone": "11999999999",
                "date": tomorrow, "time": time_slot, "serviceName": service["name"], "price": service["price"],
            })

        free = times(long_service)
        self.assertIn("10:30", free)
        self.assertNotIn("11:00", free)  # 11:00 + 65 min invade o almoço
        self.assertNotIn("12:00", free)
        self.assertIn("13:00", free)
        self.assertIn("16:30", free)
        self.assertNotIn("17:00", free)  # terminaria depois das 18:00

        self.assertEqual(book(long_service, "11:00")[0], 409)
        self.assertEqual(book(short_service, "12:00")[0], 409)
        self.assertEqual(book(short_service, "25:99")[0], 400)

        status, first = book(long_service, "10:00")
        self.assertEqual(status, 201)
        self.assertEqual(first["booking"]["status"], "solicitado")
        for blocked in ("09:30", "10:30", "11:00"):
            self.assertEqual(book(short_service, blocked)[0], 409, blocked)
        self.assertNotIn("10:30", times(short_service))
        self.assertEqual(book(short_service, "09:00")[0], 201)

        status, _ = self.authenticated_request(
            f"/api/bookings/{first['booking']['id']}/status", "PATCH",
            {"status": "cancelado", "customerToken": first["customerToken"]},
        )
        self.assertEqual(status, 200)
        self.assertIn("10:30", times(short_service))
        self.assertEqual(book(short_service, "10:30")[0], 201)

        query = urllib.parse.urlencode({"date": tomorrow, "service": "Serviço inexistente"})
        self.assertEqual(self.get_json(f"/api/professionals/test-professional/availability?{query}")[0], 400)
        self.assertEqual(self.get_json("/api/professionals/nao-existe/availability?date=" + tomorrow + "&service=x")[0], 404)
        self.assertEqual(self.get_json("/api/professionals/test-professional/availability?date=ontem")[0], 400)

        now = datetime.now(backend.BUSINESS_TZ).replace(hour=14, minute=10, second=0, microsecond=0)
        with patch.object(backend, "business_now", return_value=now):
            query = urllib.parse.urlencode({"date": now.date().isoformat(), "service": short_service["name"]})
            _, today = self.get_json(f"/api/professionals/test-professional/availability?{query}")
            self.assertEqual(today["times"][0], "14:30")
            self.assertEqual(book_today := self.post_json("/api/bookings", {
                "professionalId": "test-professional", "customerName": "Cliente", "customerPhone": "11999999999",
                "date": now.date().isoformat(), "time": "14:00", "serviceName": short_service["name"],
                "price": short_service["price"],
            })[0], 409)

    def test_professional_registration_accepts_and_returns_valid_portfolio_media(self):
        png = b"\x89PNG\r\n\x1a\n"
        payload = {
            "name": "Studio Portfólio",
            "service": "manicure",
            "city": "Marília",
            "email": "portfolio@example.com",
            "password": "senha-segura-123",
            "portfolio": [{
                "name": "trabalho.png",
                "type": "image/png",
                "data": "data:image/png;base64," + backend.base64.b64encode(png).decode("ascii"),
            }],
        }
        status, response = self.post_json("/api/profissionais", payload)
        self.assertEqual(status, 201)
        professional_id = response["professional"]["id"]
        status, public_profile = self.get_json(f"/api/profissionais/{professional_id}")
        self.assertEqual(status, 200)
        self.assertEqual(public_profile["professional"]["portfolio"][0]["name"], "trabalho.png")
        status, _ = self.authenticated_request(
            "/api/auth/login",
            "POST",
            {"email": "portfolio@example.com", "password": "senha-segura-123"},
        )
        self.assertEqual(status, 200)
        status, profile = self.authenticated_request(f"/api/profissionais/{professional_id}/editar")
        self.assertEqual(status, 200)
        self.assertEqual(profile["professional"]["portfolio"][0]["type"], "image/png")

    def test_complete_professional_profile_reaches_one_hundred_percent(self):
        profile = {
            "name": "Studio Completo",
            "service": "manicure",
            "city": "Marília",
            "description": "Atendimento com hora marcada.",
            "services": [{"name": "Manicure", "price": 50}],
            "portfolio": [{"name": "Trabalho 1"}],
        }
        self.assertEqual(backend.professional_completion(profile), 100)

    def test_professional_cannot_submit_only_half_of_a_service(self):
        payload = {
            "name": "Studio Novo",
            "service": "manicure",
            "phone": "14999999999",
            "city": "Marília",
            "serviceName": "Manicure",
        }
        status, response = self.post_json("/api/profissionais", payload)
        self.assertEqual(status, 400)
        self.assertIn("nome e o preço", response["error"])

    def test_overpass_uses_backup_mirror_and_caches_result(self):
        payload = {"elements": [{"type": "node", "id": 1, "lat": -22.2, "lon": -49.9, "tags": {"name": "Salão", "shop": "hairdresser"}}]}
        error = urllib.error.HTTPError("https://overpass-api.de/api/interpreter", 502, "Bad Gateway", {}, None)
        with patch.object(backend, "urlopen", side_effect=[error, FakeResponse(payload)]) as upstream:
            status, response = self.get_json("/api/profissionais/reais?city=Marilia&servico=cabeleireiro")
            self.assertEqual(status, 200)
            self.assertEqual(len(response["professionals"]), 1)
            self.assertEqual(upstream.call_count, 2)
            self.assertEqual(upstream.call_args.args[0].full_url, backend.OVERPASS_ENDPOINTS[1])
            status, _ = self.get_json("/api/profissionais/reais?city=Marilia&servico=cabeleireiro")
            self.assertEqual(status, 200)
            self.assertEqual(upstream.call_count, 2)

    def test_cep_uses_geocoding_and_cache(self):
        viacep = {"cep": "17500-000", "logradouro": "Rua A", "bairro": "Centro", "localidade": "Marília", "uf": "SP", "ddd": "14"}
        places = [{"lat": "-22.21", "lon": "-49.94"}]
        with patch.object(backend, "urlopen", side_effect=[FakeResponse(viacep), FakeResponse(places)]) as upstream:
            status, response = self.get_json("/api/cep?cep=17500-000")
            self.assertEqual(status, 200)
            self.assertEqual((response["lat"], response["lon"], response["precision"]), (-22.21, -49.94, "endereço"))
            status, _ = self.get_json("/api/cep?cep=17500000")
            self.assertEqual(status, 200)
            self.assertEqual(upstream.call_count, 2)

    def test_cep_falls_back_to_brasilapi_and_reports_not_found(self):
        brasilapi = {"city": "Marília", "state": "SP", "neighborhood": "Centro", "street": "Rua A",
                     "location": {"coordinates": {"latitude": "-22.2", "longitude": "-49.9"}}}
        down = urllib.error.URLError("offline")
        with patch.object(backend, "urlopen", side_effect=[down, FakeResponse(brasilapi)]):
            status, response = self.get_json("/api/cep?cep=17500000")
        self.assertEqual(status, 200)
        self.assertEqual((response["source"], response["precision"], response["lat"]), ("BrasilAPI", "cep", -22.2))
        with patch.object(backend, "urlopen", return_value=FakeResponse({"erro": True})):
            status, response = self.get_json("/api/cep?cep=00000000")
        self.assertEqual(status, 404)
        status, response = self.get_json("/api/cep?cep=123")
        self.assertEqual(status, 400)

@unittest.skipUnless(os.environ.get("CLICK_BELEZA_TEST_DATABASE_URL"), "defina CLICK_BELEZA_TEST_DATABASE_URL para testar o PostgreSQL")
class PostgresBookingSlotTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.psycopg = psycopg
        self.schema = "cb_test_" + uuid.uuid4().hex[:10]
        base = os.environ["CLICK_BELEZA_TEST_DATABASE_URL"]
        with psycopg.connect(base, autocommit=True) as connection:
            connection.execute(f"CREATE SCHEMA {self.schema}")
        separator = "&" if "?" in base else "?"
        self.original_url = backend.DATABASE_URL
        backend.DATABASE_URL = f"{base}{separator}options=-c%20search_path%3D{self.schema}"
        self.base_url = base

    def tearDown(self):
        backend.DATABASE_URL = self.original_url
        with self.psycopg.connect(self.base_url, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA {self.schema} CASCADE")

    def test_database_rejects_overlapping_reservations_even_under_thread_races(self):
        results = []
        threads = [
            threading.Thread(target=lambda n=n: results.append(
                backend.reserve_booking_slots("pro", "2030-01-10", "10:00", 65, f"B{n}")))
            for n in range(12)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), [False] * 11 + [True])
        self.assertFalse(backend.reserve_booking_slots("pro", "2030-01-10", "11:00", 40, "OVERLAP"))
        self.assertTrue(backend.reserve_booking_slots("pro", "2030-01-10", "11:30", 40, "ADJACENT"))
        self.assertTrue(backend.reserve_booking_slots("other", "2030-01-10", "10:00", 65, "OTHERPRO"))
        winner = next(n for n in range(12) if self.slot_owner(f"B{n}"))
        backend.release_booking_slots(f"B{winner}")
        self.assertTrue(backend.reserve_booking_slots("pro", "2030-01-10", "10:30", 30, "REUSED"))

    def slot_owner(self, booking_id):
        with self.psycopg.connect(backend.DATABASE_URL) as connection:
            return connection.execute(
                "SELECT count(*) FROM booking_slots WHERE booking_id = %s", (booking_id,)
            ).fetchone()[0]

    def test_data_lock_serializes_across_database_sessions(self):
        with self.psycopg.connect(backend.DATABASE_URL, autocommit=True) as probe:
            def free():
                return probe.execute("SELECT pg_try_advisory_lock(%s)", (backend.DataLock.ADVISORY_KEY,)).fetchone()[0]
            with backend.DATA_LOCK:
                with backend.DATA_LOCK:  # reentrante na mesma thread
                    self.assertFalse(free())
                self.assertFalse(free())
            self.assertTrue(free())
            probe.execute("SELECT pg_advisory_unlock(%s)", (backend.DataLock.ADVISORY_KEY,))

    def test_backfill_protects_bookings_created_before_the_slots_table(self):
        professional = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        professional.update({"id": "old-pro", "demo": False})
        old = {"id": "OLD1", "professionalId": "old-pro", "date": "2030-03-01", "time": "14:00",
               "duration": 60, "status": "confirmado"}
        cancelled = {**old, "id": "OLD2", "time": "16:00", "status": "cancelado"}
        backend.save_data({"professionals": [professional], "bookings": [old, cancelled]})
        self.assertEqual(backend.backfill_booking_slots(), 1)
        self.assertEqual(backend.backfill_booking_slots(), 0)  # idempotente
        self.assertFalse(backend.reserve_booking_slots("old-pro", "2030-03-01", "14:30", 30, "NEW"))
        self.assertTrue(backend.reserve_booking_slots("old-pro", "2030-03-01", "16:00", 30, "NEW2"))

    def test_booking_api_persists_slots_and_frees_them_on_cancel(self):
        professional = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        professional.update({"id": "db-pro", "demo": False})
        backend.save_data({"professionals": [professional], "bookings": []})
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        service = professional["services"][0]
        day = (date.today() + timedelta(days=2)).isoformat()

        def request(path, payload, method="POST"):
            req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                         headers={"Content-Type": "application/json"}, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        order = {"professionalId": "db-pro", "customerName": "Ana", "customerPhone": "11999999999",
                 "date": day, "time": "10:00", "serviceName": service["name"], "price": service["price"]}
        status, created = request("/api/bookings", order)
        self.assertEqual(status, 201)
        self.assertEqual(request("/api/bookings", order)[0], 409)
        self.assertGreater(self.slot_owner(created["booking"]["id"]), 0)
        status, _ = request(f"/api/bookings/{created['booking']['id']}/status",
                            {"status": "cancelado", "customerToken": created["customerToken"]}, "PATCH")
        self.assertEqual(status, 200)
        self.assertEqual(self.slot_owner(created["booking"]["id"]), 0)
        self.assertEqual(request("/api/bookings", order)[0], 201)

    def test_relational_round_trip_order_constraints_and_legacy_migration(self):
        pro = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        pro.update({"id": "rel-pro", "demo": False})
        user = {"id": "u1", "email": "Ana@Example.com", "professionalId": "rel-pro"}
        state = {"professionals": [pro], "users": [user],
                 "sessions": [{"tokenHash": "h1", "userId": "u1", "expiresAt": 9999999999}],
                 "bookings": [{"id": "b2", "professionalId": "rel-pro", "date": "2030-01-01", "status": "confirmado"},
                              {"id": "b1", "professionalId": "rel-pro", "date": "2030-01-02", "status": "solicitado"}]}
        backend.save_data(state)
        loaded = backend.load_data()
        self.assertEqual([b["id"] for b in loaded["bookings"]], ["b2", "b1"])
        self.assertEqual(loaded["sessions"][0]["tokenHash"], "h1")
        # remover o usuário remove a sessão em cascata e atualizações são idempotentes
        state["users"], state["sessions"] = [], []
        backend.save_data(state)
        self.assertEqual(backend.load_data()["users"], [])
        # chave estrangeira: agendamento de profissional inexistente falha
        bad = copy.deepcopy(state)
        bad["bookings"].append({"id": "b3", "professionalId": "ghost"})
        with self.assertRaises(RuntimeError):
            backend.save_data(bad)
        # e-mail único sem diferenciar maiúsculas
        dup = copy.deepcopy(state)
        dup["users"] = [user, {"id": "u2", "email": "ana@example.com", "professionalId": None}]
        with self.assertRaises(RuntimeError):
            backend.save_data(dup)
        # a falha não altera o estado salvo
        self.assertEqual(len(backend.load_data()["bookings"]), 2)

    def test_legacy_json_state_is_migrated_once(self):
        base = self.original_url
        backend._SCHEMA_READY.clear()
        pro = copy.deepcopy(backend.SAMPLE_PROFESSIONALS[0])
        pro.update({"id": "legacy-pro", "demo": False})
        legacy = {"professionals": [pro], "bookings": [{"id": "L1", "professionalId": "legacy-pro"}]}
        with self.psycopg.connect(backend.DATABASE_URL, autocommit=True) as connection:
            connection.execute("CREATE TABLE click_beleza_state (id SMALLINT PRIMARY KEY, payload JSONB NOT NULL)")
            connection.execute("INSERT INTO click_beleza_state VALUES (1, %s::jsonb)", (json.dumps(legacy),))
        backend.ensure_schema()
        data = backend.load_data()
        self.assertEqual([p["id"] for p in data["professionals"]], ["legacy-pro"])
        self.assertEqual(data["bookings"][0]["id"], "L1")
        with self.psycopg.connect(backend.DATABASE_URL) as connection:
            self.assertIsNotNone(connection.execute("SELECT to_regclass('click_beleza_state_legacy')").fetchone()[0])
            self.assertIsNone(connection.execute("SELECT to_regclass('click_beleza_state')").fetchone()[0])
        backend._SCHEMA_READY.clear()
        backend.ensure_schema()
        self.assertEqual(len(backend.load_data()["professionals"]), 1)


if __name__ == "__main__":
    unittest.main()
