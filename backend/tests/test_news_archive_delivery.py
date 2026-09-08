"""Sparse ticker briefings include dated history without semiconductor false matches."""
from datetime import datetime, timedelta, timezone

from app import news


def article(title, days, slug='article'):
    return {'title': title, 'source': 'Example News', 'url': 'https://example.com/'+slug,
            'published': (datetime.now(timezone.utc)-timedelta(days=days)).isoformat()}


def test_chip_matches_project_news_without_the_ticker():
    assert news._matches_asset(article('Bullish backs USD.AI with $100 million', 10), 'CHIP', 'CHIP')
    assert news._matches_asset(article('유에스디에이아이, 대출 감사 보고서 공개', 7), 'CHIP', 'CHIP')
    assert not news._matches_asset(article('Nvidia chip demand rises on AI spending', 1), 'CHIP', 'CHIP')
    assert not news._matches_asset(article('CHIP makers increase Bitcoin mining GPU output', 1), 'CHIP', 'CHIP')


def test_sparse_ticker_uses_archive_and_keeps_recent_first(monkeypatch):
    calls=[]
    def fetch(query, **kwargs):
        calls.append(query)
        if 'when:5y' in query:
            return [article('Bubblemaps old project launch', 500, 'old'),
                    article('Bubblemaps recent update', 3, 'recent'),
                    article('Bubblemaps obsolete article', 2200, 'ancient')]
        return [article('Bubblemaps recent update', 3, 'recent')]
    monkeypatch.setattr(news, '_fetch_news', fetch)
    payload=news._coin_news_envelope('BMT', strict=True, relevant_only=True, include_archive=True)
    assert [i['url'].rsplit('/',1)[-1] for i in payload['items']] == ['recent','old']
    assert payload['items'][1]['is_historical'] is True
    assert payload['content_scope']=='mixed'
    assert sum('when:5y' in q for q in calls)==2


def test_full_recent_page_does_not_fetch_archive(monkeypatch):
    calls=[]
    def fetch(query, **kwargs):
        calls.append(query)
        return [article(f'Bubblemaps update {i}',i+1,str(i)) for i in range(10)]
    monkeypatch.setattr(news, '_fetch_news', fetch)
    result=news._coin_news_envelope('BMT',strict=True,relevant_only=True,include_archive=True)
    assert len(result['items'])==10
    assert not any('when:5y' in q for q in calls)


def test_undated_archive_result_cannot_become_a_recent_article(monkeypatch):
    def fetch(query, **kwargs):
        return ([{'title':'Bubblemaps undated launch','url':'https://example.com/undated'},
                 article('Bubblemaps dated launch',500)] if 'when:5y' in query else [])
    monkeypatch.setattr(news,'_fetch_news',fetch)
    result=news._coin_news_envelope('BMT',strict=True,relevant_only=True,include_archive=True)
    assert len(result['items'])==1
    assert result['items'][0]['is_historical'] is True
    assert result['content_scope']=='archive'


def test_coin_history_survives_localization_but_market_stays_recent(monkeypatch):
    monkeypatch.setattr(news, '_ensure_title_translations', lambda _: None)
    old=article('버블맵스 프로젝트 출시',500)
    coin=news._localize_news_payload({'symbol':'BMT','items':[old]})
    assert len(coin['items'])==1
    assert coin['items'][0]['is_historical'] is True
    assert coin['content_scope']=='archive'
    market=news._localize_news_payload({'items':[old]})
    assert market['items']==[]


def test_unfamiliar_ticker_uses_catalog_project_name_without_network_in_filter(monkeypatch):
    from app import news_asset_catalog
    prepared=[]
    monkeypatch.setattr(news_asset_catalog, 'get_asset_name', lambda asset: prepared.append(asset) or 'Example Protocol')
    monkeypatch.setattr(news_asset_catalog, 'peek_asset_name', lambda asset: 'Example Protocol' if asset in prepared else '')
    queries=[]
    def fetch(query, **kwargs):
        queries.append(query)
        return [article('Example Protocol launches a network upgrade',1)]
    monkeypatch.setattr(news, '_fetch_news', fetch)
    result=news._coin_news_envelope('ZZNEW',strict=True,relevant_only=True)
    assert prepared==['ZZNEW']
    assert any('Example Protocol' in q for q in queries)
    assert result['items'][0]['title']=='Example Protocol launches a network upgrade'
    assert news._matches_asset(article('Example Protocol project news',1),'ZZNEW','ZZNEW')
    assert prepared==['ZZNEW']


def test_curated_ambiguous_aliases_are_not_weakened_by_catalog(monkeypatch):
    from app import news_asset_catalog
    monkeypatch.setattr(news_asset_catalog,'peek_asset_name',lambda _: 'Threshold')
    assert not news._matches_asset(article('Threshold Network GARCH Model for time series analysis',1),'T','쓰레스홀드')


def test_collector_prepares_identity_before_api_and_browser_descriptors(monkeypatch):
    from app import coindesk_api, news_asset_catalog
    prepared=[]
    monkeypatch.setattr(news_asset_catalog,'get_asset_name',lambda asset:prepared.append(asset) or 'Example Protocol')
    monkeypatch.setattr(news_asset_catalog,'peek_asset_name',lambda asset:'Example Protocol' if asset in prepared else '')
    searches=[]
    monkeypatch.setattr(coindesk_api,'configuration',lambda:{'enabled':True})
    monkeypatch.setattr(coindesk_api,'fetch_news',lambda query:searches.append(query) or {'items':[], 'source':{'status':'empty'}})
    monkeypatch.setattr(news,'_fetch_news',lambda *_args,**_kwargs:[])
    monkeypatch.setattr(news,'_fetch_coindesk_news',lambda **_kwargs:[])
    monkeypatch.setattr(news,'_fetch_shared_publisher_rss',lambda *_args,**_kwargs:[])
    news.fetch_coin_news_for_collector('ZZNEW')
    assert searches==['example protocol']
    prepared.clear()
    def pages(asset,name):
        assert news._coindesk_asset_search_terms(asset,name)==['example protocol']
        return []
    monkeypatch.setenv('POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED','true')
    monkeypatch.setattr(news,'_browser_news_pages',pages)
    monkeypatch.setattr(news,'_cached_browser_pages',lambda _: {})
    news.enrich_coin_news_for_collector('ZZNEW',{'items':[]})


def test_historical_article_is_not_a_current_position_signal(monkeypatch):
    from app.agent_features.position_news import service
    payload=service.build_position_news(
        {'symbol':'BMTUSDT','position_side':'long'},
        {'symbol':'BMT','items':[article('버블맵스 자금 조달 소식',500)]},
        {'items':[{'sentiment':'positive','summary':'자금 조달 발표','confidence':'high'}]},
    )
    assert payload['items'][0]['is_historical'] is True
    assert payload['items'][0]['position_effect']=='unclear'


def test_successful_empty_search_is_not_reported_as_collection_outage(monkeypatch):
    import time
    from app.agent_features.position_news import service
    stored={'news_payload':{'symbol':'CHIP','items':[article('CHIPUSDT Perpetual Chart | Binance Futures',1)]},
            'analysis':{}, 'collection':{'status':'empty','last_attempt_ms':int(time.time()*1000),
                                         'last_success_ms':int(time.time()*1000)-86400000}}
    monkeypatch.setattr(service,'_load_latest_snapshot',lambda _:stored)
    result=service.get_position_news({'symbol':'CHIPUSDT','position_side':'long'})
    assert result['items']==[]
    assert result['analysis_status']=='empty'
    assert result['collection']['freshness']=='fresh'


def test_stale_or_invalid_snapshot_does_not_block_fast_rss_publication(monkeypatch):
    import time
    from types import SimpleNamespace
    from app.agent_features.position_news import collector
    fresh={'symbol':'CHIP','items':[article('유에스디에이아이 새 대출 발표',1)]}
    calls=[]
    monkeypatch.setattr(collector,'collect_payload',lambda symbol,payload,**kwargs:
                        calls.append((payload,kwargs)) or {'status':'stored'})
    for old,age in [(article('CHIPUSDT Perpetual Chart | Binance Futures',1),0),
                    (article('유에스디에이아이 기존 대출',1),86400000)]:
        stored={'news_payload':{'items':[old]},'collection':{'last_success_ms':int(time.time()*1000)-age}}
        repo=SimpleNamespace(get_latest_snapshot=lambda _:stored)
        assert collector.publish_initial_payload('CHIP',fresh,repo=repo)['status']=='stored'
    assert len(calls)==2
    assert all(not opts['allow_ai'] and not opts['localize'] for _,opts in calls)
    stored={'news_payload':fresh,'collection':{'last_success_ms':int(time.time()*1000)}}
    repo=SimpleNamespace(get_latest_snapshot=lambda _:stored)
    assert collector.publish_initial_payload('CHIP',fresh,repo=repo)['status']=='reused'
    assert len(calls)==2
