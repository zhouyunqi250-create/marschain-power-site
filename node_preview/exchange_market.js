(() => {
  'use strict';
  const panel = document.getElementById('markets');
  if (!panel) return;

  const copy = {
    zh: {title: '交易所行情', caption: '现货报价 · 公开盘口按 0.01 档聚合', volume: '24 小时成交量', turnover: '24 小时成交额', buyDepth: '公开买盘总量', sellDepth: '公开卖盘总量', ticker: '行情时间', trade: '最后成交时间', ok: '已更新', late: '沿用上次报价', stale: '报价时间较早', unavailable: '暂不可用'},
    en: {title: 'Exchange Markets', caption: 'Spot quotes · Public order book grouped by 0.01', volume: '24h Volume', turnover: '24h Turnover', buyDepth: 'Visible bids', sellDepth: 'Visible asks', ticker: 'Quote time', trade: 'Last trade', ok: 'Updated', late: 'Last available quote', stale: 'Earlier quote', unavailable: 'Unavailable'},
    ja: {title: '取引所の相場', caption: '現物相場 · 公開注文板を0.01刻みで集計', volume: '24時間取引量', turnover: '24時間取引代金', buyDepth: '公開買い注文量', sellDepth: '公開売り注文量', ticker: '相場時刻', trade: '最終約定時刻', ok: '更新済み', late: '前回の相場を表示', stale: '以前の相場', unavailable: '取得不可'},
    ko: {title: '거래소 시세', caption: '현물 시세 · 공개 호가를 0.01 단위로 집계', volume: '24시간 거래량', turnover: '24시간 거래대금', buyDepth: '공개 매수 총량', sellDepth: '공개 매도 총량', ticker: '시세 시각', trade: '최근 체결 시각', ok: '갱신됨', late: '마지막 시세 표시', stale: '이전 시세', unavailable: '일시적으로 사용할 수 없음'},
  };
  let data = [];

  const toNumber = value => typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value)
    ? Number(value) : NaN;
  const displayPrice = value => Number.isFinite(toNumber(value)) && Number(value) > 0
    ? value.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') : '—';

  function render() {
    const lang = document.documentElement.lang.split('-')[0];
    const labels = copy[lang] || copy.zh;
    const zh = labels === copy.zh;
    const amount = new Intl.NumberFormat(lang, {maximumFractionDigits: 2});
    const clock = new Intl.DateTimeFormat('sv-SE', {timeZone: zh ? 'Asia/Shanghai' : 'UTC',
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});

    panel.querySelectorAll('[data-market-label]').forEach(node => {
      node.textContent = labels[node.dataset.marketLabel];
    });
    panel.querySelectorAll('[data-market-venue]').forEach(card => {
      const quote = data.find(entry => entry.exchange === card.dataset.marketVenue);
      const price = displayPrice(quote?.price_usdt);
      const received = Date.parse(quote?.received_at);
      const sourceTime = Date.parse(quote?.source_at);
      const now = Date.now();
      let current = 'ok';
      if (price === '—') current = 'unavailable';
      else if (quote.status === 'retained' || !Number.isFinite(received) || now - received > 150000) current = 'late';
      else if (quote.status === 'stale' || now - sourceTime > 180000) current = 'stale';
      const status = card.querySelector('[data-market-status]');
      status.dataset.state = current === 'stale' ? 'late' : current;
      status.textContent = labels[current];
      card.querySelector('[data-market-price]').textContent = price;
      for (const [selector, value] of [['[data-market-volume]', quote?.base_volume_24h],
        ['[data-market-turnover]', quote?.quote_turnover_24h_usdt]]) {
        const number = toNumber(value);
        card.querySelector(selector).textContent = Number.isFinite(number) ? amount.format(number) : '—';
      }
      for (const [selector, value] of [['[data-market-buy-depth]', quote?.buy_depth_mars],
        ['[data-market-sell-depth]', quote?.sell_depth_mars]]) {
        const number = toNumber(value);
        card.querySelector(selector).textContent = Number.isFinite(number) ? amount.format(number) : '—';
      }
      const time = card.querySelector('[data-market-clock]');
      if (Number.isFinite(sourceTime)) {
        time.dateTime = quote.source_at;
        time.textContent = `${clock.format(sourceTime)} ${zh ? '北京时间' : 'UTC'}`;
      } else {
        time.removeAttribute('datetime');
        time.textContent = '—';
      }
    });
  }

  window.addEventListener('mars-website-language', render);
  window.addEventListener('mars-exchange-quotes-data', event => {
    if (Array.isArray(event.detail?.quotes)) data = event.detail.quotes;
    render();
  });
  setInterval(render, 30000);
})();
