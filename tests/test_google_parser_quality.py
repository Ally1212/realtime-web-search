import unittest

from realtime.google_domain import DomainError
from realtime.google_parser_quality import (
    ParserContract,
    ParserQualityGate,
    ParsedMessage,
)


def contract():
    return ParserContract(
        source="fixture",
        source_version="1",
        parser_version="2",
        dom_signature="dom:v1",
        capabilities=frozenset({"organic", "title", "url"}),
        known_result_types=frozenset({"organic"}),
    )


def message(**changes):
    values = dict(
        message_id="message:one",
        source="fixture",
        source_version="1",
        parser_version="2",
        dom_signature="dom:v1",
        provided_fields=frozenset({"organic", "title", "url"}),
        result_types=("organic",),
        html_bytes=100,
        confidence=0.9,
    )
    values.update(changes)
    return ParsedMessage(**values)


class ParserQualityGateTests(unittest.TestCase):
    def setUp(self):
        self.gate = ParserQualityGate(contract())

    def test_provided_fields_cannot_exceed_source_capabilities(self):
        with self.assertRaises(DomainError) as error:
            self.gate.evaluate(
                message(provided_fields=frozenset({"organic", "answer"})))
        self.assertEqual(error.exception.code, "source_error")

    def test_empty_html_is_alerted_and_not_formally_ranked(self):
        result = self.gate.evaluate(message(html_bytes=0))
        self.assertIn("empty_html", result["alerts"])
        self.assertFalse(result["formal_ranking_eligible"])
        self.assertEqual(result["rank_exclusion_reason"], "empty_html")

    def test_new_result_type_is_preserved_not_absent(self):
        result = self.gate.evaluate(
            message(result_types=("organic", "local_pack")))
        self.assertEqual(result["unknown_result_types"], ["local_pack"])
        self.assertIn("new_result_types", result["alerts"])
        self.assertFalse(result["unknown_treated_as_absent"])

    def test_dom_change_is_visible(self):
        result = self.gate.evaluate(message(dom_signature="dom:v2"))
        self.assertIn("dom_signature_changed", result["alerts"])
        self.assertNotEqual(result["dom_signature_sha256"], "")

    def test_low_confidence_never_enters_formal_ranking(self):
        result = self.gate.evaluate(message(confidence=0.79))
        self.assertIn("low_confidence", result["alerts"])
        self.assertFalse(result["formal_ranking_eligible"])
        self.assertEqual(result["rank_exclusion_reason"], "low_confidence")

    def test_source_and_parser_versions_are_bound(self):
        with self.assertRaises(DomainError) as error:
            self.gate.evaluate(message(parser_version="3"))
        self.assertEqual(error.exception.code, "incompatible_scope")


if __name__ == "__main__":
    unittest.main()
