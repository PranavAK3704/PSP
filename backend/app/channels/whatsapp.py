"""WhatsApp channel adapter (the near-term primary channel).

WhatsApp and the Captain Panel are two *versions* of the same engine — different
transports, one brain. This adapter maps the WhatsApp Business Cloud API webhook
payload to a conversation turn and formats the reply back. The engine is unchanged;
only this thin layer is WhatsApp-specific.

FEASIBILITY (see PRODUCTION_DELTA): a *real* WhatsApp connection needs a Meta
Business account, a verified phone number, a permanent access token, and a public
HTTPS webhook. None of that is available in this environment, so this adapter is
wired and shaped correctly but drives the engine locally; going live = provisioning
those credentials + swapping `_send()` for a real Graph API POST.
"""
from __future__ import annotations

import os

# Map a WhatsApp phone number to a Valmo captain id. In prod this is a lookup
# against the captain master (phone -> captain_id). Demo: a small table.
PHONE_TO_CAPTAIN = {
    "919000000001": "VLMO-CPT-4471",
    "919000000002": "VLMO-CPT-2290",
}


def parse_webhook(payload: dict) -> dict | None:
    """Extract (captain_id, text, media[]) from a WhatsApp Cloud API webhook.

    Accepts either the real nested Meta shape or a simplified {from, text} shape
    for the demo/simulator. Returns None if there's no user message.
    """
    # Simplified shape (demo / simulator)
    if "from" in payload and ("text" in payload or "media" in payload):
        phone = str(payload["from"])
        return {"phone": phone, "captain_id": PHONE_TO_CAPTAIN.get(phone),
                "text": payload.get("text", ""), "media": payload.get("media", [])}

    # Real Meta Cloud API shape: entry[].changes[].value.messages[]
    try:
        msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        phone = str(msg["from"])
        text = msg.get("text", {}).get("body", "")
        media = []
        for t in ("image", "audio", "document"):
            if t in msg:
                media.append({"type": t, "id": msg[t].get("id"), "mime": msg[t].get("mime_type")})
        return {"phone": phone, "captain_id": PHONE_TO_CAPTAIN.get(phone), "text": text, "media": media}
    except (KeyError, IndexError, TypeError):
        return None


def send(phone: str, text: str) -> dict:
    """Send a reply. Live = Graph API POST; demo = echo the outbound payload."""
    token = os.environ.get("WHATSAPP_TOKEN", "")
    phone_id = os.environ.get("WHATSAPP_PHONE_ID", "")
    if token and phone_id:
        raise NotImplementedError(
            "Wire POST https://graph.facebook.com/v20.0/{phone_id}/messages here at go-live.")
    return {"to": phone, "type": "text", "text": {"body": text}, "_sent": "demo-echo"}
