# n8n-nodes-llpro

An n8n node for three LLPRO services that agents and workflows call over plain HTTPS. No sign-up for the free daily allowance.

| Operation | What it does |
|---|---|
| Get a Cited Public Fact (Ground Truth) | One cited, time-stamped public fact for a place: weather, weather alerts, river level, flood, earthquakes, disasters, air quality, road closures, power outages, burn bans. |
| Check Work and Get a Signed Stamp (Countersign) | An independent signed check of work (sums, dates, units, citations). The stamp is logged publicly and anyone can verify it. |
| Look Up an Agent or MCP Server (Agent Record) | The public record of an agent or MCP server: directory listings, uptime checks, receipts and disputes. |

## Install

Not on the npm registry yet. On a self-hosted n8n, build it from this repository and install it into n8n's custom folder
(needs Node.js 18+ and git):

```bash
git clone https://github.com/llpro-ai/llpro-campus
cd llpro-campus/n8n && npm install && npm run build
mkdir -p ~/.n8n/custom && cd ~/.n8n/custom
npm install /path/to/llpro-campus/n8n    # the n8n/ folder you just built
```

Restart n8n; the node is called **LLPRO**. Built and tested with n8n node API version 1 (`@n8n/node-cli` 0.57). No runtime dependencies. The node can also be used as a tool by n8n's
AI Agent node.

## Example workflow

[`examples/check-a-sum.json`](https://github.com/llpro-ai/llpro-campus/blob/main/n8n/examples/check-a-sum.json): a manual trigger, then **LLPRO > Check Work and Get a Signed
Stamp** on `12 * 4 = 48`. Import it in n8n (Workflows > Import from file) and run it: the output holds the verdict and a
link anyone can open to verify the stamp. Swap the operation to **Get a Cited Public Fact** (for example river level for
a place) to feed a cited fact into the rest of a workflow, or to **Look Up an Agent or MCP Server** before your workflow
trusts one.

## Past the free allowance

Each operation has a free daily allowance per caller. Past it, the node stops with a short message that says how to pay
(or returns it as an error item if "Continue On Fail" is on). To keep going, buy a prepaid credit key by card (no wallet
needed) at https://llpro.ai/pay/credit?via=github, then add it under **Credentials > LLPRO Credit Key API** (n8n checks the key by reading its balance, which costs nothing). Each call past the allowance
is then taken from the key's balance. Prices per call: https://llpro.ai/pay/prices?via=github.
Today LLPRO charges for a tool only while it passes that day's known-answer test.

## What it sends, and where

- Only to `gt.llpro.ai`, `countersign.llpro.ai` and `agentrecord.llpro.ai`, and only when the node runs. Redirects are not followed.
- When you save a credit key, n8n checks it once by reading its balance at `llpro.ai/pay/credit/balance` (no charge).
- The node's parameters as JSON (topic and place or latitude/longitude, the work to check, or the target), a User-Agent naming this package, the header `X-LLPRO-Via: n8n` (it tells LLPRO how callers found it; the same for every install), and your credit key if you set one.
- No telemetry, no other network calls. The credit key is never put in node output or error text; n8n adds it to the request from the credential.
- Countersign stamps are public: the checked text and the producer name appear on the stamp. Never send private or personal text.

## Test

    cd n8n && npm install && npm test   # builds, then runs tests/n8n.test.js from the repo root

The test uses a stand-in for n8n's HTTP helpers and never calls llpro.ai.

By LLPRO LLC. Questions: support@llpro.ai or https://llpro.ai/support?via=github
