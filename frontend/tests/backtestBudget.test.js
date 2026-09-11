import test from 'node:test';
import assert from 'node:assert/strict';
import { backtestBudget, validBacktestLimits } from '../src/lib/backtestBudget.js';
import { defaultForm, buildMacro, macroToForm, PERIOD_PRESETS } from '../src/lib/macro.js';

const limits = { max_bars: 20000, interval_ms: { '1m':60000,'5m':300000,'15m':900000,'1h':3600000,'4h':14400000,'1d':86400000 }, preset_days: {'1y':365,'6m':182,'3m':91,'1m':30,'1w':7,'1d':1} };

test('one-year minute request is rejected and neither input nor fidelity changes', () => {
  const form = {...defaultForm(),candle_interval:'1m',preset:'1y'};
  const original = structuredClone(form);
  const result = backtestBudget(form,limits);
  assert.equal(result.bars,525600);
  assert.equal(result.allowed,false);
  assert.deepEqual(form,original);
  assert.deepEqual(result.suggestions.map(choice=>choice.patch),[{candle_interval:'1h'},{preset:'1w',start:'',end:''}]);
  for (const choice of result.suggestions) assert.equal(backtestBudget({...form,...choice.patch},limits).allowed,true);
});

test('uses actual server cap and exact preset days', () => {
  assert.equal(backtestBudget({...defaultForm(),preset:'6m',candle_interval:'15m'},limits).bars,17472);
  const dynamic=backtestBudget({...defaultForm(),candle_interval:'1m'}, {...limits,max_bars:1234});
  assert.equal(dynamic.maxBars,1234);
  assert.equal(dynamic.suggestions[0].value,'1d');
});

test('custom period boundary uses every original candle', () => {
  const form={...defaultForm(),preset:'custom',start:'2026-01-01',end:'2026-01-08',candle_interval:'1m'};
  assert.equal(backtestBudget(form,{...limits,max_bars:10080}).allowed,true);
  assert.equal(backtestBudget(form,{...limits,max_bars:10079}).allowed,false);
  assert.equal(backtestBudget({...form,end:'2026-01-01'},limits).allowed,false);
  assert.equal(backtestBudget({...form,end:''},limits).allowed,false);
});

test('short presets survive save and restore with unchanged candle interval', () => {
  for (const preset of ['1m','1w','1d']) {
    assert.ok(PERIOD_PRESETS.some(item=>item.value===preset));
    const form={...defaultForm(),preset,candle_interval:'1m'};
    const restored=macroToForm(buildMacro(form));
    assert.equal(restored.preset,preset);
    assert.equal(restored.candle_interval,'1m');
  }
});

test('missing limits do not invent a local cap', () => {
  assert.equal(backtestBudget(defaultForm(),null),null);
  assert.equal(validBacktestLimits({max_bars:20000}),false);
  assert.equal(validBacktestLimits({...limits,max_bars:NaN}),false);
});
