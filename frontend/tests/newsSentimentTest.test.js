import test from 'node:test';
import assert from 'node:assert/strict';
import { elapsedSeconds, phaseLabel, liveStartedAt } from '../src/lib/newsSentimentTest.js';

test('duration uses server milliseconds, including subsecond results', () => {
  assert.equal(elapsedSeconds(1250), '1.25');
  assert.equal(elapsedSeconds(87), '0.09');
});
test('live timer compensates for browser clock skew', () => {
  assert.equal(liveStartedAt({started_at: 1000}, {server_now_ms: 2500}, 999000), 997500);
});
test('automatic state distinguishes waiting, processing, and lost connection', () => {
  assert.equal(phaseLabel(null), '불러오는 중');
  assert.equal(phaseLabel({enabled: false}), '서버 연결 대기');
  assert.equal(phaseLabel({enabled: true, current: {status: 'processing'}}), '판단 중');
  assert.equal(phaseLabel({enabled: true, stats: {pending: 2}}), '순차 판단 대기');
  assert.equal(phaseLabel({enabled: true, stats: {pending: 0}}), '새 뉴스 대기');
  assert.equal(phaseLabel({enabled: true}, 'network error'), '연결 확인 중');
});
