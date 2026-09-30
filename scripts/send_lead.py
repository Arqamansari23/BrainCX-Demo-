"""Send a signed test lead to the CRM's inbound webhook, like a website form or Zapier.

    uv run python scripts/send_lead.py                  # valid signature  -> 201
    uv run python scripts/send_lead.py --bad-signature  # tampered         -> 401
    uv run python scripts/send_lead.py --name "Ana Diaz" --email ana@example.com

The body is signed with INBOUND_WEBHOOK_SECRET from .env.local:
    X-Timestamp: <unix seconds>
    X-Signature: sha256=HMAC_SHA256(secret, "<timestamp>." + raw body)
"""

import argparse
import json
import os
import time

import httpx
from dotenv import load_dotenv

from crm_api.security import sign

load_dotenv(".env.local")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--name", default="Maria Rodriguez")
    parser.add_argument("--email", default="maria.rodriguez@novatech.example")
    parser.add_argument("--company", default="NovaTech")
    parser.add_argument("--deal", default="Website chat assistant")
    parser.add_argument("--value", type=float, default=16000)
    parser.add_argument(
        "--bad-signature", action="store_true", help="sign with the wrong secret"
    )
    args = parser.parse_args()

    body = json.dumps(
        {
            "full_name": args.name,
            "email": args.email,
            "company": args.company,
            "deal_title": args.deal,
            "deal_value": args.value,
        }
    ).encode()
    timestamp = str(int(time.time()))
    secret = os.environ["INBOUND_WEBHOOK_SECRET"]
    if args.bad_signature:
        secret = "not-the-real-secret"

    response = httpx.post(
        os.getenv("CRM_API_URL", "http://localhost:8001") + "/api/webhooks/leads",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Timestamp": timestamp,
            "X-Signature": sign(secret, timestamp, body),
        },
        timeout=10,
    )
    print(response.status_code, response.text)


if __name__ == "__main__":
    main()
