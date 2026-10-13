// Synthetic fixed responses; no model, network, cost incurred, or package imports.
module.exports = class LocalSyntheticProvider {
  id() { return 'local-synthetic-c35'; }
  async callApi(prompt, context) {
    const mode = context.vars.mode;
    if (mode === 'provider_error') return { error: 'Synthetic local provider error' };
    if (mode === 'missing_output') return { cost: 0 };
    if (mode === 'missing_cost') return { output: 'expected' };
    return { output: mode === 'assertion_fail' ? 'wrong' : 'expected', cost: mode === 'cost_high' ? 2.5 : 0, tokenUsage: { total: 0, prompt: 0, completion: 0 } };
  }
};
