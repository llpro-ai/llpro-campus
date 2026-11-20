# n8n-nodes-llpro

An n8n node for three LLPRO services that agents and workflows call over plain HTTPS. No sign-up for the free daily allowance.

| Operation | What it does |
|---|---|
| Get a Cited Public Fact (Ground Truth) | One cited, time-stamped public fact for a place: weather, weather alerts, river level, flood, earthquakes, disasters, air quality, road closures, power outages, burn bans. |
| Check Work and Get a Signed Stamp (Countersign) | An independent signed check of work (sums, dates, units, citations). The stamp is logged publicly and anyone can verify it. |
| Look Up an Agent or MCP Server (Agent Record) | The public record of an agent or MCP server: directory listings, uptime checks, receipts and disputes. |

## Install

Not on the npm registry yet. On a self-hosted n8n, install it from this repository into n8n's custom folder:

```bash
git clone https://github.com/llpro-ai/llpro-campus
mkdir -p ~/.n8n/custom && cd ~/.n8n/custom
npm install ../../path/to/llpro-campus/n8n    # path to the cloned n8n/ folder
```

Restart n8n; the node is called **LLPRO**. Needs Node.js 18+. No runtime dependencies.

## Past the free allowance

Each operation has a free daily allowance per caller. Past it, the node stops with a short message that says how to pay
(or returns it as an error item if "Continue On Fail" is on). To keep going, buy a prepaid credit key by card (no wallet
needed) at https://llpro.ai/pay/credit?via=github, then add it under **Credentials > LLPRO Credit Key**. Each call past the allowance
is then taken from the key's balance. Prices per call: https://llpro.ai/pay/prices?via=github.
Today LLPRO charges for a tool only while it passes that day's known-answer test.

## What it sends, and where

- Only to `gt.llpro.ai`, `countersign.llpro.ai` and `agentrecord.llpro.ai`, and only when the node runs. Redirects are not followed.
- Your node input as JSON, a User-Agent naming this package, the header `X-LLPRO-Via: n8n` (it tells LLPRO how callers found it; the same for every install), and your credit key if you set one.
- No telemetry, no other network calls. The credit key is never put in node output or error text.
- Countersign stamps are public: the checked text and the producer name appear on the stamp. Never send private or personal text.

## Test

    node --test tests/n8n.test.js   # from the repo root

The test (in the repo tests folder) uses a stand-in for n8n's HTTP helper and never calls llpro.ai.

By LLPRO LLC. Questions: support@llpro.ai or https://llpro.ai/support?via=github
