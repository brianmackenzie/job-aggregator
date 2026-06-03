// Health page — pulls /api/health and renders the recent scrape runs
// grouped by source. Status pill (ok / partial / error) makes failures
// scannable at a glance. the original author opens this whenever something looks off.

document.addEventListener('DOMContentLoaded', async  => {
  const summaryEl = document.getElementById('summary');
  const gridEl    = document.getElementById('runs-grid');

  try {
    const data = await Api.get('/health');
    summaryEl.innerHTML =
      `${data.registered_sources.length} sources registered · `
      + `${data.scrape_runs.length} recent runs shown`;

    // Group runs by source so a flaky source's pattern is visible at a glance.
    const bySource = {};
    for (const r of data.scrape_runs) {
      const k = r.source_name || 'unknown';
      (bySource[k] = bySource[k] || ).push(r);
    }
    // Keep server-provided ordering: it returns runs sorted desc by ts.
    const sources = Object.keys(bySource).sort;
    if (sources.length === 0) {
      gridEl.innerHTML = '<p class="muted">No scrape runs recorded yet. Trigger one from the CLI (RUNBOOK §8).</p>';
      return;
    }
    gridEl.innerHTML = sources.map(src => renderSourceCard(src, bySource[src])).join('');
  } catch (err) {
    summaryEl.textContent = 'Health endpoint failed: ' + err.message;
  }
});


function renderSourceCard(source, runs) {
  // Show the latest run's status as the headline, then up to 3 prior runs.
  const latest = runs[0];
  const cls = (latest.status || 'unknown');
  return `
    <div class="run-row">
      <span class="source">${escapeHtml(source)}</span>
      <span class="status ${escapeHtml(cls)}">${escapeHtml(cls)}</span>
      <div class="ts">${escapeHtml(latest.run_timestamp || '')}</div>
      <div class="muted">found ${latest.jobs_found ?? '–'} · new ${latest.jobs_new ?? '–'} · updated ${latest.jobs_updated ?? '–'}${
        latest.duration_ms ? ' · ' + Math.round(latest.duration_ms / 1000) + 's' : ''
      }</div>
      ${latest.error_message ? `<div class="muted" style="color:var(--danger);margin-top:0.25rem;">${escapeHtml(latest.error_message.slice(0, 240))}</div>` : ''}
    </div>
  `;
}
