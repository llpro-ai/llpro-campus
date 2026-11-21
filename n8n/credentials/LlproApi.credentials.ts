// Optional prepaid credit key. The free daily allowance needs no credential at all.
import type {
	IAuthenticateGeneric,
	Icon,
	ICredentialTestRequest,
	ICredentialType,
	INodeProperties,
} from 'n8n-workflow';

export class LlproApi implements ICredentialType {
	name = 'llproApi';

	displayName = 'LLPRO Credit Key API';

	icon: Icon = { light: 'file:../nodes/Llpro/llpro.svg', dark: 'file:../nodes/Llpro/llpro.svg' };

	documentationUrl = 'https://llpro.ai/pay/credit?via=github';

	properties: INodeProperties[] = [
		{
			displayName: 'Credit Key',
			name: 'apiKey',
			type: 'string',
			typeOptions: { password: true },
			default: '',
			description:
				'Prepaid credit key (starts with llk_). Buy one by card at llpro.ai/pay/credit (the link in the payment message works too). Only needed past the free daily allowance.',
		},
	];

	authenticate: IAuthenticateGeneric = {
		type: 'generic',
		properties: {
			headers: {
				Authorization: '=Bearer {{$credentials.apiKey.trim()}}',
			},
		},
	};

	// Reads the key's balance (no charge). An unknown or malformed key answers 404 or 401.
	test: ICredentialTestRequest = {
		request: {
			url: 'https://llpro.ai/pay/credit/balance?via=github',
			method: 'GET',
		},
	};
}
