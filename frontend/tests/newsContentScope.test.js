import test from 'node:test';
import assert from 'node:assert/strict';
import { filterNewsContent, newsContentLabel, newsContentKind } from '../src/lib/newsContent.js';
import { internalVisit, visitPayload } from '../src/lib/visit.js';

test('reported news is the default; community opinions need explicit selection', () => {
  const article = { title: '기사', source: 'CoinDesk' };
  const opinion = { title: '매수 주장', content_type: 'community', source: 'Binance Square' };
  const legacy = { title: '의견', source: 'Binance Square' };
  assert.deepEqual(filterNewsContent([article, opinion, legacy]), [article]);
  assert.deepEqual(filterNewsContent([article, opinion, legacy], 'community'), [opinion, legacy]);
  assert.equal(filterNewsContent([article, opinion], 'all').length, 2);
  assert.equal(newsContentKind(opinion), 'community');
  assert.equal(newsContentLabel(article), '보도 기사');
  assert.equal(newsContentLabel(opinion), '커뮤니티 의견 · 사실 확인 안 됨');
});

test('QA opt-in persists across navigation and can be disabled; automation is internal', () => {
  const values = new Map();
  const storage = { getItem: (k) => values.get(k), setItem: (k,v) => values.set(k,v) };
  assert.equal(internalVisit('?qa=1', storage, {}), true);
  assert.equal(internalVisit('', storage, {}), true);
  assert.equal(internalVisit('?qa=0', storage, {}), false);
  assert.equal(internalVisit('', storage, { webdriver: true }), true);
  assert.equal(visitPayload('/news', { isInternal: true }).is_internal, true);
  const blocked = { getItem() { throw Error('blocked'); }, setItem() { throw Error('blocked'); } };
  assert.equal(internalVisit('?qa=1', blocked, {}), true);
});
