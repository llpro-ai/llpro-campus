// n8n node for three LLPRO services. Rules: only the three endpoints below, fixed header X-LLPRO-Via: n8n,
// no telemetry, the credit key is never put in output or error text, no runtime dependencies.
import type {
	IDataObject,
	IExecuteFunctions,
	IHttpRequestOptions,
	INodeExecutionData,
	INodeType,
	INodeTypeDescription,
} from 'n8n-workflow';
import { NodeConnectionTypes, NodeOperationError } from 'n8n-workflow';

export const ENDPOINTS: Record<string, string> = {
	groundTruth: 'https://gt.llpro.ai/v1/ask',
	countersign: 'https://countersign.llpro.ai/v1/stamp',
	agentRecord: 'https://agentrecord.llpro.ai/v1/lookup',
};
export const VIA = 'n8n';
const VERSION = '0.2.0';

const TOPICS = [
	'weather',
	'weather_alerts',
	'river_level',
	'flood',
	'earthquakes',
	'disasters',
	'air_quality',
	'road_closures',
	'power_outages',
	'burn_ban',
];

function asObject(body: unknown): IDataObject {
	return body && typeof body === 'object' ? (body as IDataObject) : {};
}

function tagged(url: unknown): string {
	if (typeof url !== 'string' || !url.startsWith('https://')) return '';
	if (url.includes('via=')) return url;
	return url + (url.includes('?') ? '&' : '?') + 'via=' + VIA;
}

function paymentMessage(body: unknown): string {
	const b = asObject(body);
	const first = String(b.error || 'The free daily allowance for this tool is used up.').trim();
	const credit = asObject(b.credit);
	const buy = tagged(b.get_a_key || credit.buy);
	const how = buy
		? ' Get a prepaid credit key (card, no wallet needed): ' +
			buy +
			' and add it as the LLPRO Credit Key credential.'
		: ' Add a prepaid credit key as the LLPRO Credit Key credential.';
	return first + how;
}

export function failureMessage(status: number, body: unknown): string {
	const b = asObject(body);
	if (status === 402) return paymentMessage(b);
	let msg = String(b.error || b.message || 'request failed (HTTP ' + status + ')');
	if (status === 400) msg = 'Bad input, nothing was charged: ' + msg;
	else if (status === 429) msg = 'Daily ceiling reached, try again later: ' + msg;
	return msg;
}

function hideKey(text: unknown, key: string): string {
	return key ? String(text).split(key).join('[key]') : String(text);
}

export class Llpro implements INodeType {
	description: INodeTypeDescription = {
		displayName: 'LLPRO',
		name: 'llpro',
		icon: { light: 'file:llpro.svg', dark: 'file:llpro.svg' },
		group: ['transform'],
		version: 1,
		subtitle: '={{$parameter["operation"]}}',
		description:
			'Cited public facts, signed checks of AI work and public agent records. Countersign stamps are public: never send private or personal text.',
		defaults: { name: 'LLPRO' },
		inputs: [NodeConnectionTypes.Main],
		outputs: [NodeConnectionTypes.Main],
		usableAsTool: true,
		credentials: [{ name: 'llproApi', required: false }],
		properties: [
			{
				displayName: 'Operation',
				name: 'operation',
				type: 'options',
				noDataExpression: true,
				default: 'groundTruth',
				options: [
					{
						name: 'Get a Cited Public Fact (Ground Truth)',
						value: 'groundTruth',
						action: 'Get a cited public fact',
					},
					{
						name: 'Check Work and Get a Signed Stamp (Countersign)',
						value: 'countersign',
						action: 'Check work and get a signed stamp',
					},
					{
						name: 'Look Up an Agent or MCP Server (Agent Record)',
						value: 'agentRecord',
						action: 'Look up an agent or MCP server',
					},
				],
			},
			{
				displayName: 'Topic',
				name: 'topic',
				type: 'options',
				default: 'weather',
				required: true,
				options: TOPICS.map((t) => ({ name: t, value: t })),
				displayOptions: { show: { operation: ['groundTruth'] } },
			},
			{
				displayName: 'Place',
				name: 'place',
				type: 'string',
				default: '',
				placeholder: 'Lafayette, LA',
				description: 'Place name. Leave empty to use latitude and longitude instead.',
				displayOptions: { show: { operation: ['groundTruth'] } },
			},
			{
				displayName: 'Latitude',
				name: 'lat',
				type: 'number',
				default: 0,
				displayOptions: { show: { operation: ['groundTruth'] } },
			},
			{
				displayName: 'Longitude',
				name: 'lon',
				type: 'number',
				default: 0,
				displayOptions: { show: { operation: ['groundTruth'] } },
			},
			{
				displayName: 'Work to Check',
				name: 'output',
				type: 'string',
				default: '',
				required: true,
				typeOptions: { rows: 4 },
				description:
					'Short claims such as "12 * 4 = 48", a date, a unit conversion or a citation. Stamps are logged publicly: never send private or personal text.',
				displayOptions: { show: { operation: ['countersign'] } },
			},
			{
				displayName: 'Kind',
				name: 'kind',
				type: 'options',
				default: 'general',
				options: ['general', 'calculation', 'citation', 'date'].map((k) => ({ name: k, value: k })),
				displayOptions: { show: { operation: ['countersign'] } },
			},
			{
				displayName: 'Producer Name',
				name: 'producerAgentId',
				type: 'string',
				default: 'n8n-workflow',
				description: 'Shown publicly on the stamp. Do not use identifying names.',
				displayOptions: { show: { operation: ['countersign'] } },
			},
			{
				displayName: 'Target',
				name: 'target',
				type: 'string',
				default: '',
				required: true,
				placeholder: 'https://example.com/mcp',
				description:
					'URL, registry name or agent ID of an agent or MCP server (under 600 characters)',
				displayOptions: { show: { operation: ['agentRecord'] } },
			},
		],
	};

	async execute(this: IExecuteFunctions): Promise<INodeExecutionData[][]> {
		const items = this.getInputData();
		const out: INodeExecutionData[] = [];
		// The key is read only to keep it out of error text; the request itself is signed by n8n.
		let key = '';
		if (this.getNode().credentials?.llproApi) {
			try {
				const cred = await this.getCredentials('llproApi');
				key = cred && typeof cred.apiKey === 'string' ? cred.apiKey.trim() : '';
			} catch {
				key = '';
			}
		}
		for (let i = 0; i < items.length; i++) {
			try {
				const operation = this.getNodeParameter('operation', i) as string;
				const url = ENDPOINTS[operation];
				if (!url) throw new NodeOperationError(this.getNode(), 'Unknown operation');
				let body: IDataObject;
				if (operation === 'groundTruth') {
					body = { topic: this.getNodeParameter('topic', i) as string };
					const place = String(this.getNodeParameter('place', i, '') || '').trim();
					if (place) body.place = place;
					else {
						body.lat = this.getNodeParameter('lat', i) as number;
						body.lon = this.getNodeParameter('lon', i) as number;
					}
				} else if (operation === 'countersign') {
					const text = String(this.getNodeParameter('output', i) || '').trim();
					if (!text) throw new NodeOperationError(this.getNode(), 'Work to Check is empty');
					body = {
						output: text,
						producer: {
							agent_id: String(
								this.getNodeParameter('producerAgentId', i, 'n8n-workflow') || 'n8n-workflow',
							),
						},
					};
					const kind = this.getNodeParameter('kind', i, 'general') as string;
					if (kind && kind !== 'general') body.kind = kind;
				} else {
					const target = String(this.getNodeParameter('target', i) || '').trim();
					if (!target) throw new NodeOperationError(this.getNode(), 'Target is empty');
					body = { target };
				}
				const headers: IDataObject = {
					'Content-Type': 'application/json',
					Accept: 'application/json',
					'User-Agent': 'n8n-nodes-llpro/' + VERSION,
					'X-LLPRO-Via': VIA,
				};
				const request: IHttpRequestOptions = {
					method: 'POST',
					url,
					headers,
					body,
					json: true,
					returnFullResponse: true,
					ignoreHttpStatusErrors: true,
					disableFollowRedirect: true,
					timeout: 60000,
				};
				const res = (
					key
						? await this.helpers.httpRequestWithAuthentication.call(this, 'llproApi', request)
						: // eslint-disable-next-line @n8n/community-nodes/no-http-request-with-manual-auth -- free path, no credential used
							await this.helpers.httpRequest(request)
				) as IDataObject;
				const status = Number(res.statusCode);
				if (status < 200 || status >= 300) {
					throw new NodeOperationError(this.getNode(), failureMessage(status, res.body));
				}
				const data =
					res.body && typeof res.body === 'object'
						? (res.body as IDataObject)
						: { answer: res.body as string };
				out.push({ json: data, pairedItem: { item: i } });
			} catch (error) {
				const message = hideKey((error && (error as Error).message) || error, key);
				if (this.continueOnFail()) {
					out.push({ json: { error: message }, pairedItem: { item: i } });
					continue;
				}
				throw new NodeOperationError(this.getNode(), message, { itemIndex: i });
			}
		}
		return [out];
	}
}
