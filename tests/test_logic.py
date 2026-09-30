# Unit tests for the deterministic pieces: matching a spoken name to a contact,
# and the HMAC signatures that protect the webhooks. No LLM, API or database needed.

import time

import pytest
from livekit.agents.llm import ToolError

from crm_api.security import sign, verify_signature
from crm_client import pick_contact, pick_deal

# Scores are the real pg_trgm values for these names (see db.search_contacts).
JOHN_SMITH = {"id": 1, "full_name": "John Smith", "company": "Acme Corp", "score": 1.0}
JOHN_PARK = {"id": 2, "full_name": "John Park", "company": "Globex", "score": 0.45}


def test_exact_name_wins_over_similar_names() -> None:
    assert pick_contact("john smith", [JOHN_SMITH, JOHN_PARK])["id"] == 1


def test_misheard_name_still_matches_a_clear_winner() -> None:
    hits = [{**JOHN_SMITH, "score": 0.62}, {**JOHN_PARK, "score": 0.2}]
    assert pick_contact("Jon Smith", hits)["id"] == 1


def test_first_name_only_is_ambiguous() -> None:
    hits = [{**JOHN_PARK, "score": 1.0}, {**JOHN_SMITH, "score": 1.0}]
    with pytest.raises(ToolError, match="John Smith at Acme Corp"):
        pick_contact("John", hits)


def test_no_match_says_so() -> None:
    with pytest.raises(ToolError, match="No contact"):
        pick_contact("Taylor Swift", [])


def test_deal_choice_prefers_the_only_open_deal() -> None:
    contact = {
        "full_name": "John Smith",
        "deals": [
            {"id": 1, "title": "Old project", "stage": "won"},
            {"id": 2, "title": "Acme CRM rollout", "stage": "contacted"},
        ],
    }
    assert pick_deal(contact)["id"] == 2


def test_several_open_deals_need_a_title() -> None:
    contact = {
        "full_name": "John Smith",
        "deals": [
            {"id": 1, "title": "Pilot", "stage": "new"},
            {"id": 2, "title": "Rollout", "stage": "proposal"},
        ],
    }
    with pytest.raises(ToolError, match="several deals"):
        pick_deal(contact)
    assert pick_deal(contact, "rollout")["id"] == 2


BODY = b'{"full_name": "Maria Rodriguez"}'


def test_valid_signature_is_accepted() -> None:
    ts = str(int(time.time()))
    assert verify_signature("secret", ts, sign("secret", ts, BODY), BODY)


def test_tampered_body_is_rejected() -> None:
    ts = str(int(time.time()))
    signature = sign("secret", ts, BODY)
    assert not verify_signature(
        "secret", ts, signature, BODY.replace(b"Maria", b"Mallory")
    )


def test_wrong_secret_is_rejected() -> None:
    ts = str(int(time.time()))
    assert not verify_signature("secret", ts, sign("guess", ts, BODY), BODY)


def test_stale_timestamp_is_rejected() -> None:
    old = str(int(time.time()) - 600)
    assert not verify_signature("secret", old, sign("secret", old, BODY), BODY)


def test_missing_headers_are_rejected() -> None:
    assert not verify_signature("secret", None, None, BODY)
