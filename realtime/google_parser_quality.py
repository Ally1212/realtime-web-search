"""Fail-closed parser capability and drift checks for source messages."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .google_domain import DomainError, text


@dataclass(frozen=True)
class ParserContract:
    source: str
    source_version: str
    parser_version: str
    dom_signature: str
    capabilities: frozenset[str]
    known_result_types: frozenset[str]

    def __post_init__(self):
        for name in (
                "source", "source_version", "parser_version", "dom_signature"):
            text(getattr(self, name), name)
        capabilities = frozenset(self.capabilities)
        result_types = frozenset(self.known_result_types)
        if not capabilities or any(
                not isinstance(value, str) or not value for value in capabilities):
            raise DomainError("invalid_argument", "Parser capabilities are empty")
        if any(not isinstance(value, str) or not value for value in result_types):
            raise DomainError("invalid_argument", "Invalid result type")
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "known_result_types", result_types)


@dataclass(frozen=True)
class ParsedMessage:
    message_id: str
    source: str
    source_version: str
    parser_version: str
    dom_signature: str
    provided_fields: frozenset[str]
    result_types: tuple[str, ...]
    html_bytes: int
    confidence: float

    def __post_init__(self):
        for name in (
                "message_id", "source", "source_version",
                "parser_version", "dom_signature"):
            text(getattr(self, name), name)
        fields = frozenset(self.provided_fields)
        if any(not isinstance(value, str) or not value for value in fields):
            raise DomainError("invalid_argument", "Invalid provided capability")
        types = tuple(self.result_types)
        if any(not isinstance(value, str) or not value for value in types):
            raise DomainError("invalid_argument", "Invalid parsed result type")
        if type(self.html_bytes) is not int or self.html_bytes < 0:
            raise DomainError("invalid_argument", "HTML bytes must be nonnegative")
        if not isinstance(self.confidence, (int, float)) or not 0 <= self.confidence <= 1:
            raise DomainError("invalid_argument", "Confidence must be between 0 and 1")
        object.__setattr__(self, "provided_fields", fields)
        object.__setattr__(self, "result_types", types)


class ParserQualityGate:
    def __init__(self, contract):
        if not isinstance(contract, ParserContract):
            raise DomainError("invalid_argument", "Expected typed parser contract")
        self.contract = contract

    def evaluate(self, message):
        if not isinstance(message, ParsedMessage):
            raise DomainError("invalid_argument", "Expected typed parsed message")
        if (
                message.source != self.contract.source
                or message.source_version != self.contract.source_version
                or message.parser_version != self.contract.parser_version):
            raise DomainError(
                "incompatible_scope",
                "Message source or parser version differs from the contract",
                409,
            )
        undeclared = sorted(
            message.provided_fields - self.contract.capabilities)
        if undeclared:
            raise DomainError(
                "source_error",
                "Message fields exceed provided source capabilities",
                503,
            )
        unknown_types = tuple(sorted(
            set(message.result_types) - self.contract.known_result_types))
        alerts = []
        if message.html_bytes == 0:
            alerts.append("empty_html")
        if message.dom_signature != self.contract.dom_signature:
            alerts.append("dom_signature_changed")
        if unknown_types:
            alerts.append("new_result_types")
        if message.confidence < 0.8:
            alerts.append("low_confidence")
        rank_eligible = (
            message.html_bytes > 0
            and message.confidence >= 0.8
        )
        return {
            "message_id": message.message_id,
            "source": message.source,
            "source_version": message.source_version,
            "parser_version": message.parser_version,
            "capabilities": sorted(message.provided_fields),
            "result_types": list(message.result_types),
            "unknown_result_types": list(unknown_types),
            "unknown_treated_as_absent": False,
            "dom_signature": message.dom_signature,
            "dom_signature_sha256": hashlib.sha256(
                message.dom_signature.encode()).hexdigest(),
            "alerts": alerts,
            "quality_status": (
                "ok" if not alerts else "quality_alert"),
            "formal_ranking_eligible": rank_eligible,
            "rank_exclusion_reason": (
                None if rank_eligible
                else "empty_html" if message.html_bytes == 0
                else "low_confidence"
            ),
        }
