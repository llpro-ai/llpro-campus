'use strict';
// n8n node for three LLPRO services. Rules: only the three endpoints below, fixed header X-LLPRO-Via: n8n,
// no telemetry, the credit key is never put in output or error text, no runtime dependencies.

const ENDPOINTS = {
  groundTruth: 'https://gt.llpro.ai/v1/ask',
  countersign: 'https://countersign.llpro.ai/v1/stamp',
  agentRecord: 'https://agentrecord.llpro.ai/v1/lookup',
};
const VIA = 'n8n';
const VERSION = '0.1.0';

const TOPICS = ['weather', 'weather_alerts', 'river_level', 'flood', 'earthquakes', 'disasters', 'air_quality',
  'road_closures', 'power_outages', 'burn_ban'];

function tagged(url) {
  if (typeof url !== 'string' || !url.startsWith('https://')) return '';
  if (url.includes('via=')) return url;
  return url + (url.includes('?') ? '&' : '?') + 'via=' + VIA;
}

function paymentMessage(body) {
  const b = body && typeof body === 'object' ? body : {};
  const first = String(b.error || 'The free daily allowance for this tool is used up.').trim();
  const buy = tagged(b.get_a_key || (b.credit && b.credit.buy));
  const how = buy
    ? ' Get a prepaid credit key (card, no wallet needed): ' + buy + ' and add it as the LLPRO Credit Key credential.'
    : ' Add a prepaid credit key as the LLPRO Credit Key credential.';
  return first + how;
}

function failureMessage(status, body) {
  const b = body && typeof body === 'object' ? body : {};
  if (status === 402) return paymentMessage(b);
  let msg = String(b.error || b.message || 'request failed (HTTP ' + status + ')');
  if (status === 400) msg = 'Bad input, nothing was charged: ' + msg;
  else if (status === 429) msg = 'Daily ceiling reached, try again later: ' + msg;
  return msg;
}

function hideKey(text, key) {
  return key ? String(text).split(key).join('[key]') : String(text);
}

class Llpro {
  constructor() {
    this.description = {
      displayName: 'LLPRO',
      name: 'llpro',
      icon: 'file:llpro.svg',
      group: ['transform'],
      version: 1,
      subtitle: '={{$parameter["operation"]}}',
      description: 'Cited public facts, signed checks of AI work and public agent records',
      defaults: { name: 'LLPRO' },
      inputs: ['main'],
      outputs: ['main'],
      credentials: [{ name: 'llproApi', required: false }],
      properties: [
        {
          displayName: 'Operation',
          name: 'operation',
          type: 'options',
          noDataExpression: true,
          default: 'groundTruth',
          options: [
            { name: 'Get a Cited Public Fact (Ground Truth)', value: 'groundTruth', action: 'Get a cited public fact' },
            { name: 'Check Work and Get a Signed Stamp (Countersign)', value: 'countersign', action: 'Check work and get a signed stamp' },
            { name: 'Look Up an Agent or MCP Server (Agent Record)', value: 'agentRecord', action: 'Look up an agent or MCP server' },
          ],
        },
        {
          displayName: 'Topic', name: 'topic', type: 'options', default: 'weather', required: true,
          options: TOPICS.map((t) => ({ name: t, value: t })),
          displayOptions: { show: { operation: ['groundTruth'] } },
        },
        {
          displayName: 'Place', name: 'place', type: 'string', default: '', placeholder: 'Lafayette, LA',
          description: 'Place name. Leave empty to use latitude and longitude instead',
          displayOptions: { show: { operation: ['groundTruth'] } },
        },
        {
          displayName: 'Latitude', name: 'lat', type: 'number', default: 0,
          displayOptions: { show: { operation: ['groundTruth'] } },
        },
        {
          displayName: 'Longitude', name: 'lon', type: 'number', default: 0,
          displayOptions: { show: { operation: ['groundTruth'] } },
        },
        {
          displayName: 'Work to Check', name: 'output', type: 'string', default: '', required: true,
          typeOptions: { rows: 4 },
          description: 'Short claims such as "12 * 4 = 48", a date, a unit conversion or a citation. Stamps are logged publicly: never send private or personal text.',
          displayOptions: { show: { operation: ['countersign'] } },
        },
        {
          displayName: 'Kind', name: 'kind', type: 'options', default: 'general',
          options: ['general', 'calculation', 'citation', 'date'].map((k) => ({ name: k, value: k })),
          displayOptions: { show: { operation: ['countersign'] } },
        },
        {
          displayName: 'Producer Name', name: 'producerAgentId', type: 'string', default: 'n8n-workflow',
          description: 'Shown publicly on the stamp. Do not use identifying names',
          displayOptions: { show: { operation: ['countersign'] } },
        },
        {
          displayName: 'Target', name: 'target', type: 'string', default: '', required: true,
          placeholder: 'https://example.com/mcp',
          description: 'URL, registry name or agent id of an agent or MCP server (under 600 characters)',
          displayOptions: { show: { operation: ['agentRecord'] } },
        },
      ],
    };
  }

  async execute() {
    const items = this.getInputData();
    const out = [];
    let key = '';
    try {
      const cred = await this.getCredentials('llproApi');
      key = cred && typeof cred.apiKey === 'string' ? cred.apiKey.trim() : '';
    } catch (e) {
      key = '';
    }
    for (let i = 0; i < items.length; i++) {
      try {
        const operation = this.getNodeParameter('operation', i);
        const url = ENDPOINTS[operation];
        if (!url) throw new Error('Unknown operation');
        let body;
        if (operation === 'groundTruth') {
          body = { topic: this.getNodeParameter('topic', i) };
          const place = String(this.getNodeParameter('place', i, '') || '').trim();
          if (place) body.place = place;
          else {
            body.lat = this.getNodeParameter('lat', i);
            body.lon = this.getNodeParameter('lon', i);
          }
        } else if (operation === 'countersign') {
          const text = String(this.getNodeParameter('output', i) || '').trim();
          if (!text) throw new Error('Work to Check is empty');
          body = {
            output: text,
            producer: { agent_id: String(this.getNodeParameter('producerAgentId', i, 'n8n-workflow') || 'n8n-workflow') },
          };
          const kind = this.getNodeParameter('kind', i, 'general');
          if (kind && kind !== 'general') body.kind = kind;
        } else {
          const target = String(this.getNodeParameter('target', i) || '').trim();
          if (!target) throw new Error('Target is empty');
          body = { target };
        }
        const headers = {
          'Content-Type': 'application/json',
          Accept: 'application/json',
          'User-Agent': 'n8n-nodes-llpro/' + VERSION,
          'X-LLPRO-Via': VIA,
        };
        if (key) headers.Authorization = 'Bearer ' + key;
        const res = await this.helpers.httpRequest({
          method: 'POST', url, headers, body, json: true,
          returnFullResponse: true, ignoreHttpStatusErrors: true, disableFollowRedirect: true, timeout: 60000,
        });
        const status = Number(res.statusCode);
        if (status < 200 || status >= 300) throw new Error(failureMessage(status, res.body));
        const data = res.body && typeof res.body === 'object' ? res.body : { answer: res.body };
        out.push({ json: data, pairedItem: { item: i } });
      } catch (error) {
        const message = hideKey((error && error.message) || error, key);
        if (this.continueOnFail()) {
          out.push({ json: { error: message }, pairedItem: { item: i } });
          continue;
        }
        throw new Error(message);
      }
    }
    return [out];
  }
}
module.exports = { Llpro, ENDPOINTS, VIA };
