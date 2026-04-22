// Cross-page helpers shared by every page.
//
// Renders the bottom nav, exposes a toast helper, provides small text
// utilities, and ships a tappable score-badge → breakdown-modal flow.
// Loaded BEFORE every page-specific script.

(function  {
  // ----- Escape user-controlled text before injecting into HTML -----
  // Always run user/source-supplied strings (titles, companies, messages)
  // through this before string-templating into innerHTML.
  window.escapeHtml = function (s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  };

  // ----- Score -> badge class. Mirrors api_stats._band on the server. -----
  // R3: added s-purple at the top end so Tier-S rows visually pop above
  // the regular T1 green. Threshold is intentionally aspirational (90+)
  // so it stays rare on the cards.
  window.scoreClass = function (s) {
    if (!Number.isFinite(s)) return 's-gray';
    if (s >= 90) return 's-purple';
    if (s >= 78) return 's-green';
    if (s >= 65) return 's-yellow';
    if (s >= 50) return 's-orange';
    return 's-gray';
  };

  // ----- Friendly date formatter. Input: ISO string. Output: "". -----
  window.formatPosted = function (iso) {
    if (!iso) return '';
    // Truncate to YYYY-MM-DD before parsing — Workday scraper writes
    // a synthetic timestamp at "now" which would otherwise show today's
    // hh:mm and look misleading.
    const d = new Date(iso.length > 10 ? iso : iso + 'T00:00:00Z');
    if (isNaN(d.getTime)) return '';
    const now = new Date;
    const days = Math.round((now - d) / 86400000);
    if (days <= 0)  return 'today';
    if (days === 1) return '1d ago';
    if (days < 14)  return days + 'd ago';
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  };

  // ----- Bottom nav. Highlights the current page based on pathname. -----
  // R3: index.html is now the consolidated browse view, so the "All" tab
  // was dropped. The browse page itself hides the bottom nav via CSS
  // (.browse-page nav.bottom-nav { display: none }) — the topbar is
  // primary nav up there. The bottom nav is just for job/health/settings.
  const NAV = [
    { href: '/index.html',    label: 'Browse'   },
    { href: '/health.html',   label: 'Health'   },
    { href: '/settings.html', label: 'Settings' },
  ];
  window.renderBottomNav = function  {
    const path = location.pathname;
    // "/" should map to "/index.html" so the browse tab is highlighted.
    const here = path === '/' ? '/index.html' : path;
    const html = NAV.map(item => {
      const cls = item.href === here ? 'active' : '';
      return `<a href="${item.href}" class="${cls}">${item.label}</a>`;
    }).join('');
    const nav = document.createElement('nav');
    nav.className = 'bottom-nav';
    nav.innerHTML = html;
    document.body.appendChild(nav);
  };

  // ----- Toast: bottom-anchored ephemeral message. -----
  // Auto-hides after 2.5s. Calling toast while one is visible replaces
  // it (no queue) — sequential actions overwrite the prior message.
  let _toastTimer = null;
  window.toast = function (msg, opts) {
    opts = opts || {};
    let el = document.querySelector('.toast');
    if (!el) {
      el = document.createElement('div');
      el.className = 'toast';
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.classList.toggle('error', !!opts.error);
    // Force a reflow before adding .show so the transition fires.
    void el.offsetWidth;
    el.classList.add('show');
    clearTimeout(_toastTimer);
    _toastTimer = setTimeout( => {
      el.classList.remove('show');
    }, opts.durationMs || 2500);
  };

  // ----- Small URL query helpers (read + update without page reload) -----
  window.qsGet = function (key, fallback) {
    const v = new URLSearchParams(location.search).get(key);
    return v == null ? fallback : v;
  };
  window.qsSet = function (params) {
    // params is a plain object of {key: value}; null/undefined values delete.
    const sp = new URLSearchParams(location.search);
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === undefined || v === '') sp.delete(k);
      else sp.set(k, v);
    }
    const next = location.pathname + (sp.toString ? '?' + sp : '');
    history.replaceState(null, '', next);
  };

  // ----- Modal: bottom-sheet on mobile, centered card on desktop. -----
  // Usage: openModal({ title: '...', bodyHtml: '...' });
  // The backdrop click + close button hide it.
  window.openModal = function (opts) {
    closeModal;   // make sure no prior is left open
    const back = document.createElement('div');
    back.className = 'modal-backdrop';
    back.id = '_modal_';
    back.innerHTML = `
      <div class="modal" role="dialog" aria-modal="true">
        <header>
          <h3>${escapeHtml(opts.title || '')}</h3>
          <button class="primary" data-close>Close</button>
        </header>
        <div class="body">${opts.bodyHtml || ''}</div>
      </div>
    `;
    document.body.appendChild(back);
    // wire close interactions
    back.addEventListener('click', (ev) => {
      if (ev.target === back || ev.target.matches('[data-close]')) closeModal;
    });
    document.addEventListener('keydown', _escClose, { once: true });
    // animate in
    void back.offsetWidth;
    back.classList.add('show');
  };
  window.closeModal = function  {
    const back = document.getElementById('_modal_');
    if (!back) return;
    back.classList.remove('show');
    setTimeout( => back.remove, 200);
  };
  function _escClose(ev) {
    if (ev.key === 'Escape') closeModal;
  }

  // ----- Render a single job row (shared between Today + All pages) -----
  // job: a Jobs-table row dict from /api/jobs (ScoreIndex projection).
  //
  // Section 8.3 hooks:
  //   * Score badge tap → opens score-breakdown modal (uses j.score_breakdown
  //     when present; otherwise fetches the full record).
  //   * Company name tap → navigates to /all.html?companies=<name> so the original author
  //     can see every other listing for that company in one tap.
  // Work-mode chip: tiny pill next to company that shows remote/hybrid/onsite.
  // the original author explicit instruction was to SHOW work mode rather
  // than penalize non-remote roles, so the UI needs a visible label.
  // Values from Haiku's semantic layer: "remote" | "hybrid" | "onsite" | "unclear".
  function workModeChip(mode) {
    if (!mode || mode === 'unclear') return '';
    const lbl = mode.charAt(0).toUpperCase + mode.slice(1);
    return `<span class="work-mode-chip wm-${escapeHtml(mode)}">${escapeHtml(lbl)}</span>`;
  }

  window.renderJobRow = function (j) {
    const score = Number.isFinite(j.score) ? j.score : 0;
    const posted = formatPosted(j.posted_at);
    const detailHref = '/job.html?id=' + encodeURIComponent(j.job_id);
    const company = (j.company || '').trim;
    const companyHref = company
      ? '/index.html?companies=' + encodeURIComponent(company)
      : null;
    const wm = workModeChip(j.work_mode);
    return `
      <li class="job-row" data-job-id="${escapeHtml(j.job_id)}">
        <a class="job-title" href="${detailHref}">
          ${escapeHtml(j.title)}
        </a>
        <div class="job-meta">
          ${companyHref
            ? `<a class="company-link" href="${companyHref}">${escapeHtml(company)}</a>`
            : `<span>${escapeHtml(company)}</span>`}
          ${wm ? `<span class="sep"> · </span>${wm}` : ''}
          ${j.location ? `<span class="sep"> · </span><span class="muted">${escapeHtml(j.location)}</span>` : ''}
          ${posted ? `<span class="job-posted muted">${escapeHtml(posted)}</span>` : ''}
        </div>
        ${score > 0 ? `<span class="score-badge ${scoreClass(score)}" data-score-badge tabindex="0" role="button" aria-label="Score ${score}, tap for breakdown">${score}</span>` : ''}
      </li>
    `;
  };

  // ----- Score-badge tap: open breakdown modal -----
  // Delegated handler so it works for rows rendered AFTER common.js loaded.
  document.addEventListener('click', async (ev) => {
    const badge = ev.target.closest('[data-score-badge]');
    if (!badge) return;
    ev.preventDefault;
    ev.stopPropagation;
    const row = badge.closest('.job-row');
    if (!row) return;
    const jobId = row.getAttribute('data-job-id');
    if (!jobId) return;
    await openBreakdownModal(jobId);
  });

  async function openBreakdownModal(jobId) {
    openModal({
      title: 'Score breakdown',
      bodyHtml: '<p class="muted"><span class="spinner"></span> loading…</p>',
    });
    try {
      const data = await Api.get('/jobs/' + encodeURIComponent(jobId));
      const j = data.job || {};
      const sc = Number(j.score) || 0;
      let html = `
        <p style="margin: 0 0 0.5rem;">
          <span class="score-badge ${scoreClass(sc)}" style="font-size:1.05rem; padding: 0.3rem 0.7rem;">${sc}</span>
          <span class="muted" style="margin-left: 0.5rem;">${escapeHtml(j.title || '')}</span>
        </p>
        <p class="muted" style="margin: 0 0 0.75rem;">${escapeHtml(j.company || '')}${j.location ? ' · ' + escapeHtml(j.location) : ''}</p>
      `;
      const bd = j.score_breakdown;
      if (bd && typeof bd === 'object') {
        const rows = Object.entries(bd)
          .filter(([, v]) => v !== null && v !== undefined && v !== '')
          .map(([k, v]) => `
            <div class="bd-row">
              <span>${escapeHtml(k)}</span>
              <span class="muted">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</span>
            </div>`).join('');
        html += `<div class="score-breakdown">${rows || '<p class="muted">(no breakdown captured)</p>'}</div>`;
      } else {
        html += '<p class="muted">No score breakdown captured for this row.</p>';
      }
      if (j.semantic_rationale) {
        html += `<p style="margin-top: 0.75rem;"><em class="muted">${escapeHtml(j.semantic_rationale)}</em></p>`;
      }
      html += `<p class="hint" style="margin-top: 1rem;"><a href="/job.html?id=${encodeURIComponent(jobId)}">Open full job →</a></p>`;
      const body = document.querySelector('#_modal_ .body');
      if (body) body.innerHTML = html;
    } catch (err) {
      const body = document.querySelector('#_modal_ .body');
      if (body) body.innerHTML = '<p class="muted">Failed to load: ' + escapeHtml(err.message) + '</p>';
    }
  }

  // Render the bottom nav as soon as the DOM is ready on any page that
  // includes common.js. Pages don't need to do anything to opt in.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', renderBottomNav);
  } else {
    renderBottomNav;
  }
});
