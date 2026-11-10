from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from lead_hub.bitrix import BitrixGateway
from lead_hub.config import ConfigError, load_project
from lead_hub.fields import build_bitrix_fields


ROOT = Path(__file__).resolve().parents[1]


class BitrixTests(unittest.TestCase):
    def setUp(self):
        self.project = load_project(ROOT / "config/praktikm.yaml")
        self.gateway = BitrixGateway()
        self.env = patch.dict("os.environ", {
            self.project.bitrix.webhook_env: "https://example.test/rest/test/token",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.fields = build_bitrix_fields(self.project, {
            "source_lead": "Synthetic", "phone": "+70000000001",
        }, "2775")

    def test_deal_creation_creates_contact_and_keeps_attribution(self):
        self.gateway._call = Mock(side_effect=[[], {}, "10", "20"])
        created = self.gateway.create(self.project, self.fields, "praktikm:test")
        calls = self.gateway._call.call_args_list
        self.assertEqual([c.args[1] for c in calls], [
            "crm.contact.list", "crm.duplicate.findbycomm", "crm.contact.add", "crm.deal.add",
        ])
        contact = calls[2].args[2]["fields"]
        self.assertEqual(contact["PHONE"], self.fields["PHONE"])
        self.assertEqual(contact["ORIGIN_ID"], "praktikm:test")
        deal = calls[3].args[2]["fields"]
        self.assertEqual(deal["CONTACT_IDS"], ["10"])
        self.assertNotIn("PHONE", deal)
        self.assertEqual(deal["CATEGORY_ID"], 49)
        self.assertEqual(deal["STAGE_ID"], "C49:NEW")
        self.assertEqual(deal["ASSIGNED_BY_ID"], "2775")
        self.assertEqual(deal["UTM_SOURCE"], "leadgenburo_dmp")
        self.assertEqual(deal["ORIGIN_ID"], "praktikm:test")
        self.assertEqual(created.lead_url, "https://example.test/crm/deal/details/20/")
        self.assertIn("PHONE", self.fields)

    def test_existing_contact_is_recovered_by_origin(self):
        self.gateway._call = Mock(side_effect=[[{"ID": "10"}], "20"])
        self.gateway.create(self.project, self.fields, "praktikm:test")
        self.assertEqual([c.args[1] for c in self.gateway._call.call_args_list], [
            "crm.contact.list", "crm.deal.add",
        ])

    def test_existing_phone_contact_is_reused_without_modifying_it(self):
        self.gateway._call = Mock(side_effect=[[], {"CONTACT": ["12", "9"]}, "20"])
        self.gateway.create(self.project, self.fields, "praktikm:test")
        self.assertEqual(self.gateway._call.call_args.args[2]["fields"]["CONTACT_IDS"], ["9"])
        self.assertEqual(self.gateway._call.call_count, 3)

    def test_contact_failure_prevents_deal_creation(self):
        self.gateway._call = Mock(side_effect=[[], {}, RuntimeError("contact failed")])
        with self.assertRaisesRegex(RuntimeError, "contact failed"):
            self.gateway.create(self.project, self.fields, "praktikm:test")
        self.assertEqual(self.gateway._call.call_count, 3)

    def test_deal_without_phone_fails_before_contact_creation(self):
        self.gateway._call = Mock(return_value=[])
        with self.assertRaises(ConfigError):
            self.gateway.create(self.project, {**self.fields, "PHONE": []}, "praktikm:test")
        self.assertEqual(self.gateway._call.call_count, 1)

    def test_deal_recovery_does_not_depend_on_current_stage_or_category(self):
        self.gateway._call = Mock(return_value=[{"ID": "20"}])
        recovered = self.gateway.find_by_origin(self.project, "praktikm:test")
        self.assertTrue(recovered.recovered)
        self.assertIn("/deal/details/20/", recovered.lead_url)
        query = self.gateway._call.call_args.args[2]
        self.assertEqual(query["filter"], {
            "ORIGINATOR_ID": "leads_to_b24_hub", "ORIGIN_ID": "praktikm:test",
        })
        self.assertEqual(self.gateway._call.call_count, 1)

    def test_legacy_lead_is_recovered_instead_of_creating_a_deal(self):
        self.gateway._call = Mock(side_effect=[[], [{"ID": "11"}]])
        recovered = self.gateway.find_by_origin(self.project, "praktikm:test")
        self.assertIn("/lead/details/11/", recovered.lead_url)
        self.assertTrue(recovered.recovered)
        self.assertEqual([c.args[1] for c in self.gateway._call.call_args_list], [
            "crm.deal.list", "crm.lead.list",
        ])

    def test_other_projects_still_create_leads(self):
        project = replace(self.project, bitrix=replace(self.project.bitrix, entity="lead"))
        self.gateway._call = Mock(return_value="11")
        created = self.gateway.create(project, self.fields, "other:test")
        self.assertEqual(self.gateway._call.call_args.args[1], "crm.lead.add")
        self.assertIn("PHONE", self.gateway._call.call_args.args[2]["fields"])
        self.assertIn("/lead/details/11/", created.lead_url)

    def test_add_is_not_retried_after_uncertain_timeout(self):
        self.gateway.session.post = Mock(side_effect=requests.Timeout("secret URL"))
        with self.assertRaisesRegex(RuntimeError, "network request failed") as error:
            self.gateway._call(self.project, "crm.deal.add", {"fields": self.fields})
        self.assertEqual(self.gateway.session.post.call_count, 1)
        self.assertTrue(error.exception.__suppress_context__)
        self.assertNotIn("secret", str(error.exception))

    def test_invalid_origin_response_never_allows_creation(self):
        for result in (None, {}, [{"TITLE": "missing ID"}]):
            self.gateway._call = Mock(return_value=result)
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                self.gateway.find_by_origin(self.project, "praktikm:test")

    def test_invalid_duplicate_response_never_creates_contact(self):
        self.gateway._call = Mock(side_effect=[[], None])
        with self.assertRaises(RuntimeError):
            self.gateway.create(self.project, self.fields, "praktikm:test")
        self.assertEqual(self.gateway._call.call_count, 2)

    def test_add_is_not_retried_after_server_error(self):
        response = Mock(status_code=503, ok=False, content=b"error")
        response.json.return_value = {}
        self.gateway.session.post = Mock(return_value=response)
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.gateway._call(self.project, "crm.contact.add", {})
        self.assertEqual(self.gateway.session.post.call_count, 1)

    def test_reads_still_retry_transient_errors(self):
        good = Mock(status_code=200, ok=True, content=b"result")
        good.json.return_value = {"result": []}
        self.gateway.session.post = Mock(side_effect=[requests.Timeout(), good])
        with patch("lead_hub.bitrix.time.sleep"):
            self.assertEqual(self.gateway._call(self.project, "crm.deal.list", {}), [])
        self.assertEqual(self.gateway.session.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()
