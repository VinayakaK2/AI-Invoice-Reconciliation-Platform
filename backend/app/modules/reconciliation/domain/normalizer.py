"""String normalizer and token extractor for counterparty identification."""

import re
import unicodedata
from typing import List, Optional, Set

from app.modules.reconciliation.domain.stopwords import (
    BANKING_GENERIC_STOPWORDS,
    CORPORATE_LEGAL_SUFFIXES,
)

# Bidirectional and hidden control characters regex
BIDI_AND_CONTROL_REGEX = re.compile(r"[\x00\u200e\u200f\u202a-\u202e\u2066-\u2069]")

# Non-alphanumeric replacement (preserving spaces)
NON_ALPHANUMERIC_REGEX = re.compile(r"[^a-zA-Z0-9\s]")

# Consecutive whitespace regex
CONSECUTIVE_WHITESPACE_REGEX = re.compile(r"\s+")

# ReDoS-safe UPI VPA pattern: bounded lengths, non-backtracking
UPI_VPA_REGEX = re.compile(r"\b[a-zA-Z0-9.\-_]{2,64}@[a-zA-Z0-9.\-_]{2,32}\b")

# Numeric account sequence: 9 to 18 digits bounded by word boundaries
ACCOUNT_NUMBER_REGEX = re.compile(r"\b\d{9,18}\b")

# Rail prefix stripper for VPAs embedded with transfer rails (e.g., 'UPI-12345-user@icici')
RAIL_PREFIX_REGEX = re.compile(
    r"^(?:upi|neft|imps|rtgs|cr|dr|clg|ift|tfr|trf)[-_/]+(?:\d+[-_/]+)?",
    re.IGNORECASE,
)


class PayerStringNormalizer:
    """Deterministic string normalizer, token extractor, and identifier extractor."""

    @staticmethod
    def clean_text(s: Optional[str]) -> str:
        """Normalize unicode, strip hidden control chars, replace symbols, and clean whitespace."""
        if not s or not isinstance(s, str):
            return ""

        # Step 1: NFKD Unicode normalization and diacritics/accent stripping
        normalized = unicodedata.normalize("NFKD", s)
        without_accents = "".join(
            c for c in normalized if unicodedata.category(c) != "Mn"
        )

        # Step 2: Strip bidi controls and null bytes
        sanitized = BIDI_AND_CONTROL_REGEX.sub("", without_accents)

        # Step 3: Semantic symbol conversion
        sanitized = sanitized.replace("&", " and ").replace("@", " at ")

        # Step 4: Punctuation removal
        cleaned = NON_ALPHANUMERIC_REGEX.sub(" ", sanitized).lower()

        # Step 5: Collapse whitespace and strip
        return CONSECUTIVE_WHITESPACE_REGEX.sub(" ", cleaned).strip()

    @classmethod
    def normalize_legal_name(cls, name: Optional[str]) -> str:
        """Strip corporate suffixes while preserving distinct business and industry nouns."""
        cleaned = cls.clean_text(name)
        if not cleaned:
            return ""

        # Normalize or remove trailing legal suffixes
        words = cleaned.split()
        if not words:
            return ""

        # Check multi-word suffixes first, then single-word
        for suffix, _ in sorted(
            CORPORATE_LEGAL_SUFFIXES.items(), key=lambda x: len(x[0].split()), reverse=True
        ):
            suffix_words = suffix.split()
            if len(words) > len(suffix_words):
                if words[-len(suffix_words) :] == suffix_words:
                    words = words[: -len(suffix_words)]
                    break

        stripped = " ".join(words).strip()
        # If stripping leaves nothing (e.g. name was literally 'Company Ltd'), return original cleaned
        return stripped if stripped else cleaned

    @classmethod
    def extract_tokens(cls, text: Optional[str], min_len: int = 3) -> Set[str]:
        """Extract meaningful, non-stopword tokens from unstructured text."""
        cleaned = cls.clean_text(text)
        if not cleaned:
            return set()

        tokens = cleaned.split()
        result: Set[str] = set()

        for token in tokens:
            # Must be at least min_len characters
            if len(token) < min_len:
                continue
            # Must not be pure digits
            if token.isdigit():
                continue
            # Must not be a banking stopword
            if token in BANKING_GENERIC_STOPWORDS:
                continue
            result.add(token)

        return result

    @staticmethod
    def normalize_identifier(identifier: Optional[str]) -> str:
        """Normalize banking coordinates (remove spaces, hyphens, lowercase)."""
        if not identifier or not isinstance(identifier, str):
            return ""
        sanitized = BIDI_AND_CONTROL_REGEX.sub("", identifier).strip()
        if "@" in sanitized:
            user, handle = sanitized.split("@", 1)
            clean_user = re.sub(r"[^a-zA-Z0-9.\-_]", "", user).lower().strip()
            clean_handle = re.sub(r"[^a-zA-Z0-9.\-_]", "", handle).lower().strip()
            return f"{clean_user}@{clean_handle}"
        else:
            return re.sub(r"[\s\-_/.]", "", sanitized).lower().strip()

    @staticmethod
    def extract_potential_upi_vpas(narration: Optional[str]) -> List[str]:
        """Extract potential UPI VPAs from unstructured narration."""
        if not narration or not isinstance(narration, str):
            return []
        matches = UPI_VPA_REGEX.findall(narration)
        valid_vpas: List[str] = []
        for match in matches:
            norm = match.lower().strip()
            parts = norm.split("@")
            if len(parts) == 2:
                user_part, handle_part = parts
                # Strip leading rail or transaction reference prefix (e.g. 'upi-12345-')
                clean_user = RAIL_PREFIX_REGEX.sub("", user_part)
                clean_vpa = f"{clean_user}@{handle_part}"
                if clean_user and clean_user not in BANKING_GENERIC_STOPWORDS:
                    if clean_vpa not in valid_vpas:
                        valid_vpas.append(clean_vpa)
                if norm != clean_vpa and user_part not in BANKING_GENERIC_STOPWORDS:
                    if norm not in valid_vpas:
                        valid_vpas.append(norm)
        return valid_vpas

    @staticmethod
    def extract_potential_bank_accounts(narration: Optional[str]) -> List[str]:
        """Extract continuous 9-18 digit account sequences from narration."""
        if not narration or not isinstance(narration, str):
            return []
        return ACCOUNT_NUMBER_REGEX.findall(narration)
