ITEM_DETAIL_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Item History · WoW Auction Tracker</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f4f6f8;
      --surface: #ffffff;
      --surface-2: #edf1f5;
      --text: #18202a;
      --muted: #5e6b78;
      --line: #d7dee7;
      --accent: #176b87;
      --accent-2: #9a5a14;
      --quartile: #237a57;
      --median: #176b87;
      --third: #8a5a1f;
      --bad: #b2413a;
      --shadow: 0 1px 2px rgba(18, 28, 38, .08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    a { color: var(--accent); }
    header {
      position: sticky;
      top: 0;
      z-index: 5;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      min-height: 74px;
      padding: 14px 24px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, .96);
    }
    .identity, .item-title, .toolbar, .chart-controls, .mode-controls, .range-controls, .legend {
      display: flex;
      align-items: center;
    }
    .identity { gap: 16px; min-width: 0; }
    .back-link { white-space: nowrap; text-decoration: none; font-weight: 700; }
    .item-title { gap: 10px; min-width: 0; }
    .item-title img {
      width: 40px;
      height: 40px;
      border: 1px solid var(--line);
      border-radius: 7px;
    }
    h1 { margin: 0; font-size: 21px; line-height: 1.15; }
    .subtitle { color: var(--muted); font-size: 12px; }
    .toolbar { gap: 8px; }
    button, select {
      height: 36px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
      padding: 0 11px;
      font: inherit;
    }
    button { cursor: pointer; }
    button:hover, select:hover { border-color: var(--accent); }
    main { max-width: 1500px; margin: 0 auto; padding: 20px 24px 34px; }
    .status { margin-bottom: 14px; color: var(--muted); }
    .metrics {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 18px;
    }
    .metric, section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: var(--shadow);
    }
    .metric { min-height: 88px; padding: 14px; }
    .metric span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .04em;
      text-transform: uppercase;
    }
    .metric strong { display: block; margin-top: 8px; font-size: 22px; line-height: 1.1; }
    .metric small { display: block; margin-top: 5px; color: var(--muted); }
    section { min-width: 0; }
    .section-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 52px;
      padding: 11px 14px;
      border-bottom: 1px solid var(--line);
    }
    h2 { margin: 0; font-size: 15px; }
    .section-note { color: var(--muted); font-size: 12px; }
    .recommendation { margin-bottom: 18px; }
    .recommendation-body { padding: 15px; }
    .recommendation-summary {
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 12px;
    }
    .recommendation-stat { padding-right: 10px; border-right: 1px solid var(--line); }
    .recommendation-stat:last-child { border-right: 0; }
    .recommendation-stat span { display: block; color: var(--muted); font-size: 11px; text-transform: uppercase; }
    .recommendation-stat strong { display: block; margin-top: 5px; font-size: 17px; }
    .reasons { margin: 14px 0 0; padding-left: 20px; color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 9px;
      border-radius: 999px;
      background: #e6f2ed;
      color: var(--quartile);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .chart-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      margin-bottom: 18px;
    }
    .chart-wide { grid-column: 1 / -1; }
    .chart-body { padding: 12px 14px 14px; }
    canvas { display: block; width: 100%; height: 300px; }
    .chart-controls { flex-wrap: wrap; justify-content: flex-end; gap: 10px; }
    .mode-controls, .range-controls { gap: 5px; }
    .mode-button, .range-button { height: 30px; padding: 0 9px; color: var(--muted); font-size: 12px; }
    .mode-button.active, .range-button.active {
      border-color: var(--accent);
      background: #e7f2f7;
      color: var(--accent);
      font-weight: 700;
    }
    .legend { flex-wrap: wrap; gap: 12px; min-height: 24px; color: var(--muted); font-size: 12px; }
    .legend-item { display: inline-flex; align-items: center; gap: 5px; }
    .legend-swatch { width: 18px; height: 3px; border-radius: 2px; }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
      margin-bottom: 18px;
    }
    .table-wrap { overflow: auto; max-height: 420px; }
    table { width: 100%; border-collapse: collapse; min-width: 700px; }
    th, td { padding: 9px 11px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
    th {
      position: sticky;
      top: 0;
      z-index: 2;
      background: var(--surface-2);
      color: var(--muted);
      font-size: 11px;
      letter-spacing: .03em;
      text-transform: uppercase;
    }
    th:first-child, td:first-child { text-align: left; }
    .empty { padding: 24px; color: var(--muted); text-align: center; }
    .error { color: var(--bad); }
    .muted { color: var(--muted); }
    @media (max-width: 1050px) {
      .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .recommendation-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }
      .recommendation-stat:nth-child(3) { border-right: 0; }
    }
    @media (max-width: 760px) {
      header { position: static; align-items: flex-start; flex-direction: column; padding: 14px; }
      main { padding: 14px; }
      .identity { align-items: flex-start; flex-direction: column; gap: 8px; }
      .toolbar { width: 100%; }
      .toolbar select { flex: 1; }
      .chart-grid, .detail-grid { grid-template-columns: 1fr; }
      .chart-wide { grid-column: auto; }
      .recommendation-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .recommendation-stat { border-right: 0; }
    }
  </style>
</head>
<body>
  <header>
    <div class="identity">
      <a class="back-link" href="/market">← Market</a>
      <div class="item-title">
        <img id="item-icon" alt="" hidden>
        <div>
          <h1 id="item-name">Item history</h1>
          <div class="subtitle" id="item-meta">Loading item details…</div>
        </div>
      </div>
    </div>
    <div class="toolbar">
      <select id="timezone" aria-label="History timezone">
        <option value="America/New_York" selected>Eastern</option>
        <option value="UTC">UTC</option>
        <option value="America/Chicago">Central</option>
        <option value="America/Denver">Mountain</option>
        <option value="America/Los_Angeles">Pacific</option>
        <option value="America/Phoenix">Arizona</option>
      </select>
      <button id="refresh" type="button">Refresh</button>
    </div>
  </header>

  <main>
    <div class="status" id="status">Loading history…</div>

    <div class="metrics">
      <div class="metric"><span>Current First Quartile</span><strong id="current-q1">-</strong><small>Conservative market level</small></div>
      <div class="metric"><span>Current Median</span><strong id="current-median">-</strong><small>Middle observed listing price</small></div>
      <div class="metric"><span>Current Third Quartile</span><strong id="current-q3">-</strong><small>Upper market level</small></div>
      <div class="metric"><span>Current Quantity</span><strong id="current-quantity">-</strong><small id="current-listings">- listings</small></div>
      <div class="metric"><span>7-Day Average Q1</span><strong id="week-q1">-</strong><small>Average first quartile</small></div>
      <div class="metric"><span>7-Day Low</span><strong id="week-low">-</strong><small>Lowest listing price</small></div>
      <div class="metric"><span>Estimated Sell-through</span><strong id="sell-through">-</strong><small id="sell-confidence">Inferred, not confirmed sales</small></div>
      <div class="metric"><span>Snapshots</span><strong id="snapshot-count">-</strong><small id="history-range">No history yet</small></div>
    </div>

    <section class="recommendation">
      <div class="section-head">
        <h2>Current Recommendation</h2>
        <span class="pill" id="recommendation-action">Waiting</span>
      </div>
      <div class="recommendation-body" id="recommendation-body">
        <div class="empty">Recommendation data is loading.</div>
      </div>
    </section>

    <div class="chart-grid">
      <section class="chart-wide">
        <div class="section-head">
          <div>
            <h2>Price History</h2>
            <div class="section-note" id="price-chart-note">Median listing price with adaptive smoothing</div>
          </div>
          <div class="chart-controls">
            <div class="mode-controls" aria-label="Price chart smoothing">
              <button class="mode-button active" data-mode="smoothed" type="button">Smoothed</button>
              <button class="mode-button" data-mode="raw" type="button">Raw</button>
            </div>
            <div class="range-controls" aria-label="History range">
              <button class="range-button" data-hours="24" type="button">24h</button>
              <button class="range-button active" data-hours="168" type="button">7d</button>
              <button class="range-button" data-hours="720" type="button">30d</button>
              <button class="range-button" data-hours="all" type="button">All</button>
            </div>
          </div>
        </div>
        <div class="chart-body">
          <canvas id="price-history" width="1200" height="340"></canvas>
          <div class="legend" id="price-history-legend"></div>
        </div>
      </section>

      <section>
        <div class="section-head"><div><h2>Inventory History</h2><div class="section-note">Total available quantity</div></div></div>
        <div class="chart-body">
          <canvas id="quantity-history" width="760" height="320"></canvas>
          <div class="legend" id="quantity-history-legend"></div>
        </div>
      </section>

      <section>
        <div class="section-head"><div><h2>Price by Hour of Day</h2><div class="section-note">Typical median price in the selected timezone</div></div></div>
        <div class="chart-body">
          <canvas id="hour-history" width="760" height="320"></canvas>
          <div class="legend" id="hour-history-legend"></div>
        </div>
      </section>

      <section class="chart-wide">
        <div class="section-head"><div><h2>Price by Day of Week</h2><div class="section-note">Typical median price for Monday through Sunday</div></div></div>
        <div class="chart-body">
          <canvas id="weekday-history" width="1200" height="320"></canvas>
          <div class="legend" id="weekday-history-legend"></div>
        </div>
      </section>
    </div>

    <div class="detail-grid">
      <section>
        <div class="section-head"><h2>Recent Market Events</h2></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Detected</th><th>Type</th><th>Severity</th><th>Observed</th><th>Explanation</th></tr></thead>
            <tbody id="anomalies"></tbody>
          </table>
        </div>
      </section>
      <section>
        <div class="section-head"><h2>My Auction Outcomes</h2></div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Observed</th><th>Outcome</th><th>Quantity</th><th>Proceeds</th><th>Character</th><th>Match</th></tr></thead>
            <tbody id="player-outcomes"></tbody>
          </table>
        </div>
      </section>
    </div>

    <section>
      <div class="section-head">
        <div><h2>Recent Snapshot Statistics</h2><div class="section-note">Latest 100 observations</div></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Time</th><th>First Quartile</th><th>Median</th><th>Third Quartile</th><th>Minimum</th><th>Quantity</th><th>Listings</th><th>Sell-through</th><th>Confidence</th></tr>
          </thead>
          <tbody id="snapshot-history"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    const itemId = Number(window.location.pathname.split('/').filter(Boolean).pop());
    const colors = { q1: '#237a57', median: '#176b87', q3: '#8a5a1f', quantity: '#176b87' };
    let detail = null;
    let rangeHours = 168;
    let priceMode = 'smoothed';

    const els = {
      status: document.getElementById('status'),
      timezone: document.getElementById('timezone'),
      refresh: document.getElementById('refresh'),
      itemName: document.getElementById('item-name'),
      itemMeta: document.getElementById('item-meta'),
      itemIcon: document.getElementById('item-icon'),
      currentQ1: document.getElementById('current-q1'),
      currentMedian: document.getElementById('current-median'),
      currentQ3: document.getElementById('current-q3'),
      currentQuantity: document.getElementById('current-quantity'),
      currentListings: document.getElementById('current-listings'),
      weekQ1: document.getElementById('week-q1'),
      weekLow: document.getElementById('week-low'),
      sellThrough: document.getElementById('sell-through'),
      sellConfidence: document.getElementById('sell-confidence'),
      snapshotCount: document.getElementById('snapshot-count'),
      historyRange: document.getElementById('history-range'),
      priceChartNote: document.getElementById('price-chart-note'),
      recommendationAction: document.getElementById('recommendation-action'),
      recommendationBody: document.getElementById('recommendation-body'),
      anomalies: document.getElementById('anomalies'),
      playerOutcomes: document.getElementById('player-outcomes'),
      snapshotHistory: document.getElementById('snapshot-history')
    };

    function escapeHtml(value) {
      return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
      })[character]);
    }

    function gold(copper) {
      if (copper === null || copper === undefined) return '-';
      return `${(Number(copper) / 10000).toLocaleString(undefined, { maximumFractionDigits: 2 })}g`;
    }

    function integer(value) {
      if (value === null || value === undefined) return '-';
      return Number(value).toLocaleString();
    }

    function percentBps(value) {
      if (value === null || value === undefined) return '-';
      return `${(Number(value) / 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    }

    function shortTime(value) {
      if (!value) return '-';
      const normalized = String(value).replace(' ', 'T');
      const withZone = /(?:Z|[+-]\\d{2}:?\\d{2})$/.test(normalized) ? normalized : `${normalized}Z`;
      return new Date(withZone).toLocaleString();
    }

    async function loadItemDetail() {
      els.refresh.disabled = true;
      els.status.textContent = 'Loading history…';
      els.status.classList.remove('error');
      try {
        const timezone = encodeURIComponent(els.timezone.value);
        const response = await fetch(`/api/items/${itemId}?timezone=${timezone}&t=${Date.now()}`, { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Unable to load item');
        detail = payload;
        render();
        els.status.textContent = `Updated ${new Date().toLocaleTimeString()} · ${payload.display_timezone}`;
      } catch (error) {
        els.status.textContent = error.message || 'Unable to load item';
        els.status.classList.add('error');
      } finally {
        els.refresh.disabled = false;
      }
    }

    function render() {
      const item = detail.item;
      const summary = detail.summary;
      const latest = summary.latest || {};
      document.title = `${item.name} · WoW Auction Tracker`;
      els.itemName.textContent = item.name;
      els.itemMeta.textContent = `#${item.item_id} · ${item.market} · ${[item.item_class, item.item_subclass].filter(Boolean).join(' / ') || 'Auction item'}`;
      if (item.icon_url) {
        els.itemIcon.src = item.icon_url;
        els.itemIcon.hidden = false;
      }
      els.currentQ1.textContent = gold(latest.first_quartile_unit_price);
      els.currentMedian.textContent = gold(latest.median_unit_price);
      els.currentQ3.textContent = gold(latest.third_quartile_unit_price);
      els.currentQuantity.textContent = integer(latest.total_quantity);
      els.currentListings.textContent = `${integer(latest.listing_count)} listings`;
      els.weekQ1.textContent = gold(summary.seven_day_average_first_quartile_unit_price);
      els.weekLow.textContent = gold(summary.seven_day_low_unit_price);
      els.sellThrough.textContent = percentBps(latest.sell_through_ratio_bps);
      els.sellConfidence.textContent = latest.sell_through_confidence === null || latest.sell_through_confidence === undefined
        ? 'Inferred, not confirmed sales'
        : `${integer(latest.sell_through_confidence)}% confidence · inferred`;
      els.snapshotCount.textContent = integer(summary.snapshot_count);
      els.historyRange.textContent = summary.snapshot_count
        ? `${shortTime(summary.first_seen)} to ${shortTime(summary.last_seen)}`
        : 'No history yet';
      renderRecommendation();
      renderCharts();
      renderTables();
    }

    function renderRecommendation() {
      const recommendation = detail.recommendation;
      if (!recommendation) {
        els.recommendationAction.textContent = 'No signal';
        els.recommendationBody.innerHTML = '<div class="empty">More snapshot history is needed before a recommendation can be calculated.</div>';
        return;
      }
      els.recommendationAction.textContent = recommendation.action || 'Watch';
      const stats = [
        ['Buy target', gold(recommendation.recommended_buy_price)],
        ['Sell target', gold(recommendation.recommended_sell_price)],
        ['Profit / unit', gold(recommendation.estimated_profit_unit_price)],
        ['Score', integer(recommendation.score)],
        ['Confidence', `${integer(recommendation.confidence)}%`],
        ['Trend', integer(recommendation.price_trend_score)]
      ];
      const reasons = (recommendation.reasons || []).map((reason) => `<li>${escapeHtml(reason)}</li>`).join('');
      els.recommendationBody.innerHTML = `
        <div class="recommendation-summary">
          ${stats.map(([label, value]) => `<div class="recommendation-stat"><span>${label}</span><strong>${value}</strong></div>`).join('')}
        </div>
        ${reasons ? `<ul class="reasons">${reasons}</ul>` : '<div class="empty">No explanation is available yet.</div>'}
      `;
    }

    function historyForRange() {
      const rows = detail.history || [];
      if (rangeHours === 'all' || !rows.length) return rows;
      const latest = Number(rows[rows.length - 1].started_at_epoch);
      const cutoff = latest - (Number(rangeHours) * 60 * 60);
      return rows.filter((row) => Number(row.started_at_epoch) >= cutoff);
    }

    function smoothedHistoryForRange() {
      const rangeKey = rangeHours === 'all' ? 'all' : String(rangeHours);
      return detail.smoothed_price_history?.[rangeKey] || historyForRange();
    }

    function renderCharts() {
      const rawRows = historyForRange();
      const priceRows = priceMode === 'smoothed' ? smoothedHistoryForRange() : rawRows;
      els.priceChartNote.textContent = priceMode === 'smoothed'
        ? 'Median listing price with adaptive smoothing'
        : 'Raw median listing price for every recorded snapshot';
      drawLineChart('price-history', 'price-history-legend', priceRows, [
        { key: 'median_unit_price', label: 'Median price', color: colors.median }
      ], gold, (row) => row.display_time, { robustUpper: priceMode === 'smoothed' });
      drawLineChart('quantity-history', 'quantity-history-legend', rawRows, [
        { key: 'total_quantity', label: 'Available quantity', color: colors.quantity }
      ], integer, (row) => row.display_time);
      drawLineChart('hour-history', 'hour-history-legend', detail.time_of_day || [], [
        { key: 'typical_median_unit_price', label: 'Typical median price', color: colors.median }
      ], gold, (row) => row.label);
      drawLineChart('weekday-history', 'weekday-history-legend', detail.day_of_week || [], [
        { key: 'typical_median_unit_price', label: 'Typical median price', color: colors.median }
      ], gold, (row) => row.label);
    }

    function drawLineChart(canvasId, legendId, rows, series, formatter, labelForRow, options = {}) {
      const canvas = document.getElementById(canvasId);
      const ctx = canvas.getContext('2d');
      const width = canvas.width;
      const height = canvas.height;
      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);
      const values = rows.flatMap((row) => series.map((line) => row[line.key])).filter((value) => value !== null && value !== undefined);
      renderLegend(legendId, series);
      if (!rows.length || !values.length) {
        ctx.fillStyle = '#5e6b78';
        ctx.font = '14px system-ui';
        ctx.fillText('Not enough history for this view yet', 24, 42);
        return;
      }

      const pad = { left: 82, right: 24, top: 22, bottom: 48 };
      const chartWidth = width - pad.left - pad.right;
      const chartHeight = height - pad.top - pad.bottom;
      const numericValues = values.map(Number);
      const minimum = Math.min(...numericValues);
      const rawMaximum = Math.max(...numericValues);
      const sortedValues = [...numericValues].sort((left, right) => left - right);
      const robustMaximum = sortedValues[Math.floor((sortedValues.length - 1) * 0.99)];
      const maximum = options.robustUpper && sortedValues.length >= 100 && rawMaximum > robustMaximum * 1.5
        ? robustMaximum
        : rawMaximum;
      const spread = Math.max(maximum - minimum, 1);
      const x = (index) => pad.left + (index / Math.max(rows.length - 1, 1)) * chartWidth;
      const y = (value) => {
        const bounded = Math.min(maximum, Math.max(minimum, Number(value)));
        return pad.top + chartHeight - ((bounded - minimum) / spread) * chartHeight;
      };

      ctx.strokeStyle = '#d7dee7';
      ctx.lineWidth = 1;
      ctx.fillStyle = '#5e6b78';
      ctx.font = '12px system-ui';
      for (let index = 0; index <= 4; index += 1) {
        const gridY = pad.top + (index / 4) * chartHeight;
        const gridValue = maximum - ((index / 4) * spread);
        ctx.beginPath();
        ctx.moveTo(pad.left, gridY);
        ctx.lineTo(width - pad.right, gridY);
        ctx.stroke();
        ctx.fillText(formatter(Math.round(gridValue)), 8, gridY + 4);
      }

      series.forEach((line) => {
        ctx.strokeStyle = line.color;
        ctx.lineWidth = 3;
        ctx.lineCap = 'round';
        ctx.lineJoin = 'round';
        ctx.beginPath();
        let started = false;
        rows.forEach((row, index) => {
          const value = row[line.key];
          if (value === null || value === undefined) {
            started = false;
            return;
          }
          if (!started) ctx.moveTo(x(index), y(Number(value)));
          else ctx.lineTo(x(index), y(Number(value)));
          started = true;
        });
        ctx.stroke();
      });

      const labelIndexes = Array.from(new Set([0, Math.floor((rows.length - 1) / 2), rows.length - 1]));
      ctx.fillStyle = '#5e6b78';
      labelIndexes.forEach((index, labelIndex) => {
        const label = String(labelForRow(rows[index]) || '');
        ctx.textAlign = labelIndex === 0 ? 'left' : labelIndex === labelIndexes.length - 1 ? 'right' : 'center';
        ctx.fillText(label, x(index), height - 16);
      });
      ctx.textAlign = 'left';
    }

    function renderLegend(elementId, series) {
      document.getElementById(elementId).innerHTML = series.map((line) => `
        <span class="legend-item"><span class="legend-swatch" style="background:${line.color}"></span>${line.label}</span>
      `).join('');
    }

    function renderTables() {
      const anomalies = detail.anomalies || [];
      els.anomalies.innerHTML = anomalies.length ? anomalies.map((row) => `
        <tr><td>${shortTime(row.detected_at)}</td><td>${escapeHtml(row.anomaly_type)}</td><td>${integer(row.severity)}</td><td>${gold(row.observed_value)}</td><td>${escapeHtml(row.explanation)}</td></tr>
      `).join('') : '<tr><td colspan="5" class="empty">No recent anomalies for this item.</td></tr>';

      const outcomes = detail.player_outcomes || [];
      els.playerOutcomes.innerHTML = outcomes.length ? outcomes.map((row) => `
        <tr><td>${shortTime(row.observed_at)}</td><td>${escapeHtml(row.outcome)}</td><td>${integer(row.item_count)}</td><td>${gold(row.money)}</td><td>${escapeHtml(row.character || '-')}</td><td>${row.match_confidence === null || row.match_confidence === undefined ? '-' : `${integer(row.match_confidence)}%`}</td></tr>
      `).join('') : '<tr><td colspan="6" class="empty">No imported personal auction outcomes for this item.</td></tr>';

      const history = [...(detail.history || [])].reverse().slice(0, 100);
      els.snapshotHistory.innerHTML = history.length ? history.map((row) => `
        <tr><td>${escapeHtml(row.display_time)}</td><td>${gold(row.first_quartile_unit_price)}</td><td>${gold(row.median_unit_price)}</td><td>${gold(row.third_quartile_unit_price)}</td><td>${gold(row.min_unit_price)}</td><td>${integer(row.total_quantity)}</td><td>${integer(row.listing_count)}</td><td>${percentBps(row.sell_through_ratio_bps)}</td><td>${row.sell_through_confidence === null || row.sell_through_confidence === undefined ? '-' : `${integer(row.sell_through_confidence)}%`}</td></tr>
      `).join('') : '<tr><td colspan="9" class="empty">No snapshots have been recorded for this item yet.</td></tr>';
    }

    document.querySelectorAll('.range-button').forEach((button) => {
      button.addEventListener('click', () => {
        rangeHours = button.dataset.hours === 'all' ? 'all' : Number(button.dataset.hours);
        document.querySelectorAll('.range-button').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
        if (detail) renderCharts();
      });
    });
    document.querySelectorAll('.mode-button').forEach((button) => {
      button.addEventListener('click', () => {
        priceMode = button.dataset.mode;
        document.querySelectorAll('.mode-button').forEach((candidate) => candidate.classList.toggle('active', candidate === button));
        if (detail) renderCharts();
      });
    });
    els.refresh.addEventListener('click', loadItemDetail);
    els.timezone.addEventListener('change', loadItemDetail);
    loadItemDetail();
  </script>
</body>
</html>
"""
