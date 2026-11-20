'use strict';
// Optional prepaid credit key. The free daily allowance needs no credential at all.
class LlproApi {
  constructor() {
    this.name = 'llproApi';
    this.displayName = 'LLPRO Credit Key';
    this.documentationUrl = 'https://llpro.ai/pay/credit?via=github';
    this.properties = [
      {
        displayName: 'Credit Key',
        name: 'apiKey',
        type: 'string',
        typeOptions: { password: true },
        default: '',
        description: 'Prepaid credit key (starts with llk_). Buy one by card at llpro.ai/pay/credit (the link in the payment message works too). Only needed past the free daily allowance.',
      },
    ];
  }
}
module.exports = { LlproApi };
