"""Small stdlib client for LLPRO's paid agent tools: Ground Truth, Countersign and Agent Record.

The free daily allowance needs no sign-up. Past it, pass a prepaid credit key (llk_...) as api_key or set
LLPRO_API_KEY: the call is then taken from the key's balance. Without a key, the error says how to get one.
The only network addresses used are the three endpoints below.
"""
import http.client
import json
import os
import urllib.error
import urllib.request

__version__ = "0.1.0"
VIA = "langchain"
ENDPOINTS = {
    "ground_truth": "https://gt.llpro.ai/v1/ask",
    "countersign": "https://countersign.llpro.ai/v1/stamp",
    "agent_record": "https://agentrecord.llpro.ai/v1/lookup",
}


class LLPROError(Exception):
    """A call that did not return an answer. status is the HTTP status (0 = no response)."""

    def __init__(self, message, status=0, body=None):
        super().__init__(message)
        self.status = status
        self.body = body if isinstance(body, dict) else {}


class PaymentRequired(LLPROError):
    """The free daily allowance is used up (HTTP 402). The message says how to pay."""


def _tagged(url):
    if not isinstance(url, str) or not url.startswith("https://"):
        return ""
    if "via=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "via=" + VIA


def _payment_message(body):
    first = str(body.get("error") or "The free daily allowance for this tool is used up.").strip()
    buy = _tagged(body.get("get_a_key") or (body.get("credit") or {}).get("buy"))
    how = (" Get a prepaid credit key (card, no wallet needed): %s, then pass it as api_key or set LLPRO_API_KEY."
           % buy) if buy else " Pass a prepaid credit key as api_key or set LLPRO_API_KEY."
    return first + how + " Pay-per-call with x402 (USDC on Base) also works on this endpoint."


def call(tool, payload, api_key=None, agent_id=None, timeout=60.0):
    """POST payload (a dict) to one tool and return the JSON answer as a dict. Raises LLPROError / PaymentRequired."""
    if tool not in ENDPOINTS:
        raise ValueError("unknown tool %r (use one of %s)" % (tool, ", ".join(sorted(ENDPOINTS))))
    if not isinstance(payload, dict) or not payload:
        raise ValueError("payload must be a non-empty dict")
    key = api_key if api_key is not None else os.environ.get("LLPRO_API_KEY", "")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "langchain-llpro/" + __version__,
        "X-LLPRO-Via": VIA,
    }
    if key:
        headers["Authorization"] = "Bearer " + key.strip()
    if agent_id:
        headers["X-Agent-Id"] = str(agent_id)
    req = urllib.request.Request(ENDPOINTS[tool], data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if e.code == 402:
            raise PaymentRequired(_payment_message(body), 402, body)
        msg = str(body.get("error") or body.get("message") or e.reason or "request failed")
        if e.code == 400:
            msg = "Bad input, nothing was charged: " + msg
        elif e.code == 429:
            msg = "Daily ceiling reached, try again later: " + msg
        raise LLPROError(msg, e.code, body)
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        raise LLPROError("Could not reach %s: %s" % (ENDPOINTS[tool], getattr(e, "reason", e)), 0)
    try:
        out = json.loads(raw or b"{}")
    except ValueError:
        raise LLPROError("The answer was not JSON", 200)
    return out if isinstance(out, dict) else {"answer": out}
