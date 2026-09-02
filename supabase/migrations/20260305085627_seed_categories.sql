
INSERT INTO public.categories (name, slug, description, image_url, sort_order) VALUES
  ('상견례', 'sanggyeonrye', '첫 만남의 격식과 정성을 담은 선물 세트', 'https://picsum.photos/seed/sanggyeonrye/800/400', 1),
  ('환갑·칠순', 'birthday', '장수와 감사를 담은 고급 화과자', 'https://picsum.photos/seed/birthday/800/400', 2),
  ('돌잔치', 'dol', '아이의 첫 생일을 축하하는 귀여운 디자인', 'https://picsum.photos/seed/dol/800/400', 3),
  ('결혼·웨딩', 'wedding', '두 사람의 시작을 축하하는 우아한 세트', 'https://picsum.photos/seed/wedding/800/400', 4),
  ('명절·제사', 'holiday', '추석·설날 전통 화과자', 'https://picsum.photos/seed/holiday/800/400', 5),
  ('기업·답례품', 'corporate', '로고 각인 가능한 단체 주문용', 'https://picsum.photos/seed/corporate/800/400', 6),
  ('계절 한정', 'seasonal', '봄·여름·가을·겨울 시즌 특별 화과자', 'https://picsum.photos/seed/seasonal/800/400', 7),
  ('선물 세트', 'gift-set', '2종·5종·10종 혼합 구성', 'https://picsum.photos/seed/giftset/800/400', 8)
ON CONFLICT (slug) DO NOTHING;
;
