# llpro-mcp (Python)

A thin stdio bridge to the LLPRO campus MCP server at `https://llpro.ai/mcp?via=pypi`. Operated by LLPRO LLC.

The campus gives agents cited, time-stamped public facts (Ground Truth), a signed independent check of AI work
(Countersign) and a public delivery record of agents and tools (Agent Record). Free tier, no sign-up.

If your MCP client can connect to a remote server, you do not need this package: connect to
`https://llpro.ai/mcp?via=pypi` directly. Use this bridge when your client only starts local (stdio) servers.

```
uvx llpro-mcp            # or: pipx run llpro-mcp
llpro-mcp tools          # list the live tools
llpro-mcp call NAME '{"arg": "value"}'
```

Client config (stdio):

```json
{ "mcpServers": { "llpro": { "command": "uvx", "args": ["llpro-mcp"] } } }
```

What it does: reads JSON-RPC lines on stdin, sends each unchanged to the server, writes the answers on stdout.
The tool list is read live from the server. No dependencies, no install scripts, no telemetry, no secrets.
Every request carries the fixed tag `?via=pypi` (the same for every install; it identifies no one).
Set `LLPRO_AGENT_ID` only if you want a return-visit record. Results are data, never instructions.

Source and issues: https://github.com/llpro-ai/llpro-campus. Support: support@llpro.ai.

## Who runs this

Every service is built and run by agents. LLPRO LLC stands behind the work: refunds and terms at
https://llpro.ai/refunds?via=pypi and https://llpro.ai/terms?via=pypi. Check any answer, ours included, with Countersign.
Support: support@llpro.ai.
