import copy
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
        backend.DATA_FILE = Path(self.temporary_directory.name) / "data.json"
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

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        backend.DATA_FILE = self.original_data_file
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

    def test_booking_is_always_paid_at_the_establishment(self):
        payload = {
            "professionalId": "test-professional",
            "customerName": "Cliente de teste",
            "customerPhone": "11999999999",
            "date": (date.today() + timedelta(days=1)).isoformat(),
            "time": "10:00",
            "serviceName": "Corte masculino",
            "price": 45,
            "paymentDestination": "app",
            "paymentMethod": "credito",
        }
        status, response = self.post_json("/api/agendamentos", payload)
        self.assertEqual(status, 201)
        booking = response["booking"]
        self.assertEqual(booking["paymentDestination"], "estabelecimento")
        self.assertIsNone(booking["paymentMethod"])
        self.assertEqual(booking["customerEmail"], "")
        self.assertNotIn("cardNumber", booking)

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
        }
        status, response = self.post_json("/api/profissionais", payload)
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
