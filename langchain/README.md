# langchain-llpro

LangChain tools for three LLPRO services that agents call over plain HTTPS. No sign-up for the free daily allowance.

| Tool | What it does |
|---|---|
| `llpro_ground_truth` | One cited, time-stamped public fact for a place: weather, weather alerts, river level, flood, earthquakes, disasters, air quality, road closures, power outages, burn bans. |
| `llpro_countersign` | An independent signed check of an agent's work (sums, dates, units, citations). The stamp is logged publicly and anyone can verify it. |
| `llpro_agent_record` | The public record of an agent or MCP server: directory listings, uptime checks, receipts and disputes. |

## Install

```bash
pip install "langchain-llpro @ git+https://github.com/llpro-ai/llpro-campus#subdirectory=langchain"
```

Needs Python 3.9+ and `langchain-core` (installed with it).

## Use

```python
from langchain_llpro import get_tools

tools = get_tools()  # free daily allowance, no key needed
gt, stamp, record = tools

print(gt.invoke({"topic": "river_level", "place": "Lafayette, LA"}))
print(stamp.invoke({"output": "12 * 4 = 48", "kind": "calculation"}))
print(record.invoke({"target": "https://gt.llpro.ai/mcp"}))
```

Give `tools` to any LangChain or LangGraph agent, for example `create_react_agent(model, tools)`.

## Past the free allowance

Each tool has a free daily allowance per caller. Once it is used up, the tool returns a short note saying how to pay
instead of an answer (it does not crash your agent). To keep going, buy a prepaid credit key by card (no wallet needed) at
https://llpro.ai/pay/credit?via=github, then:

```python
tools = get_tools(api_key="llk_...")   # or set the LLPRO_API_KEY environment variable
```

Each call past the allowance is then taken from the key's balance. Prices per call: https://llpro.ai/pay/prices?via=github.
Today LLPRO charges for a tool only while it passes that day's known-answer test; otherwise calls past the allowance are
free, within the services' overall daily ceilings.
The same endpoints also accept pay-per-call with x402 (USDC on Base); this package does not sign x402 payments.

## What it sends, and where

- Only to `gt.llpro.ai`, `countersign.llpro.ai` and `agentrecord.llpro.ai`, and only when a tool is called.
- Your tool input as JSON, a User-Agent naming this package, the header `X-LLPRO-Via: langchain` (it tells LLPRO how
  callers found it), your credit key if you set one, and `X-Agent-Id` if you pass `agent_id`.
- No telemetry, no other network calls.
- Countersign stamps are public: the checked text and the producer fields this package adds (`agent_id`, default
  `langchain-agent`, set with `producer_agent_id`; `model` only if you set `producer_model`) appear on the stamp.
  Never send private or personal text, and do not put identifying names in those fields.

By LLPRO LLC. Questions: support@llpro.ai or https://llpro.ai/support?via=github
