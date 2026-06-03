/* =====================================================================
   Job detail page — fetches one job by id, renders title/company/
   location/description + score breakdown + action buttons.

   R3 redesign updates:
     * QoL bar shown when qol_score is present.
     * Tag chips show industries / role_types / company_group.
     * "Back" link points to /index.html (the consolidated browse page).
     * Company-link target is /index.html?companies=<name>.
   ===================================================================== */

document.addEventListener('DOMContentLoaded', async  => {
  const titleEl    = document.getElementById('job-title');
  const subtitleEl = document.getElementById('job-subtitle');
  const metaEl     = document.getElementById('job-meta');
  const descEl     = document.getElementById('job-description');
  const noDescEl   = document.getElementById('no-desc');
  const scoreEl    = document.getElementById('job-score');
  const breakdown  = document.getElementById('score-breakdown');
  const actionRow  = document.getElementById('action-row');
  const externalEl = document.getElementById('external-link');
  const qolEl      = document.getElementById('job-qol');
  const qolValEl   = document.getElementById('job-qol-val');
  const tagsEl     = document.getElementById('job-tags');

  const jobId = qsGet('id', '');
  if (!jobId) {
    titleEl.textContent = 'Missing job ID';
    return;
  }

  // Pull the taxonomy in parallel with the job — needed for tag labels.
  let taxonomy = null;
  Api.get('/taxonomy').then(t => {
    taxonomy = t;
    if (current) renderTags(current);   // re-render tags once labels available
  }).catch( => { /* non-fatal */ });

  let current = null;       // current job row, refreshed after each action

  async function load {
    try {
      const data = await Api.get('/jobs/' + encodeURIComponent(jobId));
      current = data.job;
      render(current);
    } catch (err) {
      titleEl.textContent = 'Failed to load';
      subtitleEl.textContent = err.message;
    }
  }

  function render(j) {
    if (!j) {
      titleEl.textContent = 'Not found';
      return;
    }
    document.title = (j.title || 'Job') + ' · Jobs Aggregator';
    titleEl.textContent = j.title || '(no title)';

    // Company name -> tappable filter link to /index.html
    const company = (j.company || '').trim;
    if (company) {
      const href = '/index.html?companies=' + encodeURIComponent(company);
      subtitleEl.innerHTML =
        '<a class="company-link" href="' + escapeHtml(href) + '">' +
        escapeHtml(company) + '</a>';
    } else {
      subtitleEl.textContent = '';
    }

    // ---- Meta panel: location, posted, source, status, etc. ----
    const m = ;
    if (j.location) m.push(['Location', j.location]);
    if (j.work_mode && j.work_mode !== 'unclear') {
      m.push(['Work mode', j.work_mode.charAt(0).toUpperCase + j.work_mode.slice(1)]);
    }
    if (j.posted_at)    m.push(['Posted', formatPosted(j.posted_at)]);
    if (j.source)       m.push(['Source', j.source]);
    if (j.company_tier) m.push(['Tier',   j.company_tier]);
    if (j.track)        m.push(['Track',  j.track]);
    if (j.status)       m.push(['Status', j.status]);
    if (j.salary_min || j.salary_max) {
      const lo = j.salary_min ? '$' + Number(j.salary_min).toLocaleString : '?';
      const hi = j.salary_max ? '$' + Number(j.salary_max).toLocaleString : '?';
      m.push(['Salary', lo + ' – ' + hi]);
    }
    if (Number.isFinite(j.semantic_score)) m.push(['Semantic', j.semantic_score]);
    if (Number.isFinite(j.algo_score))     m.push(['Algo',     j.algo_score]);
    metaEl.innerHTML = m.map(([k, v]) =>
      `<dt>${escapeHtml(k)}</dt><dd>${escapeHtml(String(v))}</dd>`
    ).join('');

    // ---- QoL bar (R1 field) ----
    const qol = Number(j.qol_score);
    if (Number.isFinite(qol)) {
      qolEl.style.setProperty('--qol', qol + '%');
      qolValEl.textContent = qol;
      qolEl.hidden = false;
    } else {
      qolEl.hidden = true;
    }

    // ---- Tag chips ----
    renderTags(j);

    // ---- Score badge in header ---- (tappable -> modal)
    const sc = Number(j.score) || 0;
    scoreEl.className = 'score-badge ' + scoreClass(sc);
    scoreEl.textContent = sc;
    scoreEl.setAttribute('role', 'button');
    scoreEl.setAttribute('tabindex', '0');
    scoreEl.style.cursor = 'pointer';
    scoreEl.title = 'Tap for score breakdown';
    scoreEl.onclick =  => openBreakdownModalFor(j);

    // ---- Description ----
    if (j.description) {
      descEl.textContent = j.description;
      descEl.hidden = false;
      if (noDescEl) noDescEl.hidden = true;
    } else {
      descEl.hidden = true;
      if (noDescEl) noDescEl.hidden = false;
    }

    // ---- Score breakdown (inline) ----
    renderBreakdown(j);

    // ---- External link ----
    if (j.url) {
      externalEl.href = j.url;
      externalEl.hidden = false;
    } else {
      externalEl.hidden = true;
    }

    // ---- Action buttons ----
    renderActions(j);
  }

  // Render the tag-chip strip below the meta panel using R1 fields.
  function renderTags(j) {
    if (!tagsEl) return;
    const bits = ;
    const facetLabel = (facet, value) => {
      if (!taxonomy) return value;
      const opts = taxonomy[facet] || ;
      const hit = opts.find(o => o.value === value);
      return hit ? hit.label : value;
    };

    if (Array.isArray(j.industries)) {
      j.industries.forEach(v => bits.push(
        `<span class="tag tag--industry">${escapeHtml(facetLabel('industries', v))}</span>`));
    }
    if (j.company_group) {
      bits.push(`<span class="tag tag--group">${escapeHtml(facetLabel('company_groups', j.company_group))}</span>`);
    }
    if (Array.isArray(j.role_types)) {
      j.role_types.forEach(v => bits.push(
        `<span class="tag tag--role">${escapeHtml(facetLabel('role_types', v))}</span>`));
    }
    tagsEl.innerHTML = bits.join('');
    tagsEl.style.display = bits.length ? 'flex' : 'none';
  }

  function renderBreakdown(j) {
    const bd = j.score_breakdown;
    if (!bd || typeof bd !== 'object') {
      breakdown.hidden = true;
      return;
    }
    const rows = Object.entries(bd)
      .filter(([, v]) => v !== null && v !== undefined)
      .map(([k, v]) =>
        `<div class="bd-row">
           <span>${escapeHtml(k)}</span>
           <span class="muted">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</span>
         </div>`
      ).join('');
    if (!rows) { breakdown.hidden = true; return; }
    breakdown.innerHTML = '<h3>Score breakdown</h3>' + rows;
    breakdown.hidden = false;

    // QoL breakdown if present (R1) — appended below the score breakdown.
    if (j.qol_breakdown && typeof j.qol_breakdown === 'object') {
      const qrows = Object.entries(j.qol_breakdown)
        .filter(([, v]) => v !== null && v !== undefined)
        .map(([k, v]) =>
          `<div class="bd-row">
             <span>${escapeHtml(k)}</span>
             <span class="muted">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : String(v))}</span>
           </div>`
        ).join('');
      if (qrows) {
        breakdown.insertAdjacentHTML('beforeend',
          '<h3>Quality of life</h3>' + qrows);
      }
    }

    if (j.semantic_rationale) {
      breakdown.insertAdjacentHTML('beforeend',
        '<div class="bd-row"><em class="muted">' + escapeHtml(j.semantic_rationale) + '</em></div>');
    }
  }

  // Open the breakdown modal using the already-loaded `j` (no re-fetch).
  function openBreakdownModalFor(j) {
    const sc = Number(j.score) || 0;
    let html = `
      <p style="margin: 0 0 0.75rem;">
        <span class="score-badge ${scoreClass(sc)}" style="font-size:1.05rem; padding: 0.3rem 0.7rem;">${sc}</span>
        <span class="muted" style="margin-left: 0.5rem;">${escapeHtml(j.title || '')}</span>
      </p>
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
    openModal({ title: 'Score breakdown', bodyHtml: html });
  }

  function renderActions(j) {
    const status = j.status || 'active';
    actionRow.innerHTML = `
      <button class="action-save"   ${status === 'saved'   ? 'disabled' : ''}>${status === 'saved'   ? 'Saved'   : 'Save'   }</button>
      <button class="action-skip   danger"  ${status === 'archived' ? 'disabled' : ''}>${status === 'archived' ? 'Skipped' : 'Skip'  }</button>
      <button class="action-applied applied" ${status === 'applied' ? 'disabled' : ''}>${status === 'applied' ? 'Applied' : 'Applied'}</button>
    `;
    actionRow.querySelector('.action-save'   ).addEventListener('click',  => doAction('save'));
    actionRow.querySelector('.action-skip'   ).addEventListener('click',  => doAction('skip'));
    actionRow.querySelector('.action-applied').addEventListener('click',  => doAction('applied'));
  }

  async function doAction(action) {
    try {
      const r = await Api.post('/jobs/' + encodeURIComponent(jobId) + '/action', { action });
      current = r.job || current;
      toast('Marked as ' + action);
      render(current);
    } catch (err) {
      toast('Action failed: ' + err.message, { error: true });
    }
  }

  load;
});
