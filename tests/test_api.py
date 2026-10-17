import copy
import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
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
        self.assertEqual(set(overview), {"professionals", "bookings", "chats", "partners", "users", "clients"})
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

if __name__ == "__main__":
    unittest.main()
