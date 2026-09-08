"""Community posts retain their identity and are never directional news signals."""
from datetime import datetime, timezone

import pytest

from app.agent_features.position_news import service


POST = {
    'content_type': 'community', 'source': 'Binance Square',
    'community_post_id': '123456789', 'author': '시장기록자',
    'url': 'https://www.binance.com/en/square/post/123456789',
    'title': 'CHIP 상승 가능성을 살펴본 개인 의견',
    'original_title': 'A personal outlook on CHIP',
    'published': datetime.now(timezone.utc).isoformat(),
    'excerpt': 'Untranslated community body must never appear in the agent.',
}
SESSION = {'session_id': 41, 'symbol': 'CHIPUSDT', 'position_side': 'long'}


def build(items, assessed=None):
    return service.build_position_news(
        SESSION, {'symbol': 'CHIP', 'items': items},
        {'items': assessed or [], 'overview': '커뮤니티에서 상승이 확인됐다는 잘못된 AI 요약',
         'analysis_source': 'ai', 'analysis_status': 'ready', 'ai': True},
    )


def test_community_fields_survive_without_inheriting_analyzed_sentiment_or_body():
    payload = build([POST], [{'sentiment': 'positive', 'confidence': 'high', 'summary': '상승을 보장하는 잘못된 분석'}])
    item = payload['items'][0]
    for field in ('content_type', 'source', 'community_post_id', 'author', 'url', 'published', 'original_title'):
        assert item[field] == POST[field]
    assert item['asset_sentiment'] == item['position_effect'] == 'unclear'
    assert item['confidence'] == 'low'
    assert item['summary'] == '커뮤니티 작성자의 의견이며 포지션 영향은 확인되지 않았어요.'
    assert 'Untranslated' not in str(payload)
    assert '잘못된 AI 요약' not in payload['overview']['text']
    assert payload['overview']['scope'] == 'community_posts'
    assert payload['analysis_source'] == 'community'
    assert payload['ai'] is False


def test_community_skips_rule_classifier_and_keeps_following_article_analysis_index(monkeypatch):
    monkeypatch.setattr(service.classifier, 'classify_headline', lambda *_args: pytest.fail('no community classification'))
    article = {'title': '공식 파트너십 체결 발표', 'source': 'CoinDesk', 'published': POST['published']}
    payload = build([POST, article], [
        {'sentiment': 'positive', 'confidence': 'high'},
        {'sentiment': 'negative', 'confidence': 'medium'},
    ])
    assert [item['position_effect'] for item in payload['items']] == ['unclear', 'unfavorable']
    assert payload['overview']['scope'] == 'mixed_sources'
    assert '뉴스 1건과 커뮤니티 게시글 1건' in payload['overview']['text']
    assert payload['ai'] is True
    # Missing classifier output also must not trigger headline heuristics for posts.
    assert build([POST])['items'][0]['position_effect'] == 'unclear'


def test_post_identity_survives_title_translation_author_edits_and_url_locale():
    original = service._article_id(POST)
    edited = {**POST, 'title': '번역이 개선된 CHIP 게시글', 'original_title': 'Changed post title',
              'author': '변경된 이름', 'url': 'https://www.binance.com/ko/square/post/123456789'}
    assert service._article_id(edited) == original
    assert build([edited])['snapshot_id'] == build([POST])['snapshot_id']
    assert service._article_id({**POST, 'community_post_id': '987654321'}) != original
    assert service._article_id({**POST, 'source': 'Other Community'}) != original


def test_post_without_explicit_id_uses_source_and_canonical_url_not_title():
    item = {**POST, 'community_post_id': ''}
    assert service._article_id(item) == service._article_id({**item, 'title': '다른 한국어 제목'})
    assert service._article_id(item) != service._article_id({**item, 'url': item['url'] + '0'})


def test_historical_post_keeps_actual_publication_and_has_no_position_effect():
    post = {**POST, 'published': '2022-01-02T01:30:00Z'}
    item = build([post])['items'][0]
    assert item['is_historical'] is True
    assert item['published'] == '2022-01-02T01:30:00Z'
    assert item['position_effect'] == 'unclear'


def test_regular_article_identity_and_classification_are_unchanged():
    article = {'title': '공식 ETF 승인', 'source': 'CoinDesk', 'published': POST['published']}
    payload = build([article], [{'sentiment': 'positive', 'confidence': 'high', 'summary': '정식 승인 발표입니다.'}])
    assert payload['items'][0]['position_effect'] == 'favorable'
    assert payload['items'][0]['confidence'] == 'high'
    assert 'content_type' not in payload['items'][0]
    assert payload['analysis_source'] == 'ai'
    assert payload['ai'] is True
