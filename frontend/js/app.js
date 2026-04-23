/* =====================================================================
   Browse view — single-page filter + sort + paginate.
   Round R3.

   Pulls everything from two endpoints:
     GET /api/taxonomy            facet labels (industries / role_types
                                  / company_groups / work_modes /
                                  sort_options / statuses)
     GET /api/jobs/browse?...     filtered + sorted + paginated rows

   State model
   -----------
   The whole page state lives in `state` and is fully reflected into
   the URL query string so that:
     - reload restores the view
     - any link the original author shares opens at the same filters
     - back/forward buttons work without duplicate fetches

   Filters are AND across categories, OR within a category — the same
   semantics the server uses (db.query_jobs_for_browse).
   ===================================================================== */

(function  {

  /* ---------- Default state ---------- */
  // All multi-value filters are arrays so we can JSON-compare and
  // serialize cleanly into comma-separated query-string values.
  const defaultState =  => ({
    status:           'active',
    industries:       ,
    role_types:       ,
    company_groups:   ,
    work_modes:       ,
    // categorical engagement chip. Server filter only —
    // there is no algo weight on this any more.
    engagement_types: ,
    min_score:        0,
    min_qol:        0,
    min_salary:     0,
    sort_by:        'score',
    sort_dir:       'desc',
    q:              '',           // free-text — filtered client-side
    limit:          50,
    offset:         0,
  });

  let state         = readStateFromUrl;
  let taxonomy      = null;       // resolved on first load
  let lastResp      = null;       // last /browse response (for pagination)
  let loadedRows    = ;         // accumulated across "Load more" pages
  let inflight      = null;       // AbortController for current fetch

  /* multi-select state. Lives outside `state` because
     it does NOT belong in the URL — selection is ephemeral, page-local.
     - selectMode: true while the user has tapped the "Select" toggle.
     - selectedIds: Set of job_ids the user has checked. Survives across
       paginated "Load more" within the same select session. Cleared on
       a successful bulk action (the cleared rows would no longer match
       the active filter anyway, so re-render is the natural reset). */
  let selectMode = false;
  let selectedIds = new Set;

  /* DOM refs filled on DOMContentLoaded */
  let topbarSubtitle, searchInput, sortSelect, openFiltersBtn, closeFiltersBtn,
      applyFiltersBtn, resetFiltersBtn, filterCountEl, statusTabsEl,
      activeChipsEl, filterRailEl, railBackdropEl, jobCardsEl, emptyStateEl,
      loadMoreBtn, resultsCountEl, railCountEl,
      minScoreEl, minScoreOut, minQolEl, minQolOut, minSalaryEl, minSalaryOut,
      toggleSelectBtn, bulkToolbarEl, bulkCountEl, bulkSelectAllBtn,
      bulkClearBtn, bulkSaveBtn, bulkArchiveBtn;

  document.addEventListener('DOMContentLoaded', init);

  async function init {
    cacheDom;
    wireStaticEvents;
    hydrateSliders;
    searchInput.value = state.q;

    // Kick off taxonomy + first page in parallel; the cards render only
    // after BOTH resolve so we can label tag chips properly.
    try {
      const [tax, _firstPage] = await Promise.all([
        Api.get('/taxonomy'),
        loadPage({ replace: true }),
      ]);
      taxonomy = tax;
      renderFacets;
      renderStatusTabs;
      renderSortOptions;
      renderActiveChips;
      renderJobs;   // re-render now that taxonomy labels are available
    } catch (err) {
      // Taxonomy is the only must-have. If it failed but jobs loaded,
      // we'll show the cards with raw values instead of human labels.
      toast('Failed to load taxonomy: ' + err.message, { error: true });
    }
  }

  /* =====================================================================
     DOM cache + static wiring
     ===================================================================== */
  function cacheDom {
    topbarSubtitle   = document.getElementById('topbar-subtitle');
    searchInput      = document.getElementById('search-input');
    sortSelect       = document.getElementById('sort-select');
    openFiltersBtn   = document.getElementById('open-filters');
    filterCountEl    = document.getElementById('filter-count');
    closeFiltersBtn  = document.getElementById('close-filters');
    applyFiltersBtn  = document.getElementById('apply-filters');
    resetFiltersBtn  = document.getElementById('reset-filters');
    statusTabsEl     = document.getElementById('status-tabs');
    activeChipsEl    = document.getElementById('active-chips');
    filterRailEl     = document.getElementById('filter-rail');
    railBackdropEl   = document.getElementById('rail-backdrop');
    jobCardsEl       = document.getElementById('job-cards');
    emptyStateEl     = document.getElementById('empty-state');
    loadMoreBtn      = document.getElementById('load-more');
    resultsCountEl   = document.getElementById('results-count');
    railCountEl      = document.getElementById('rail-count');

    minScoreEl       = document.getElementById('min-score');
    minScoreOut      = document.getElementById('min-score-out');
    minQolEl         = document.getElementById('min-qol');
    minQolOut        = document.getElementById('min-qol-out');
    minSalaryEl      = document.getElementById('min-salary');
    minSalaryOut     = document.getElementById('min-salary-out');

    // select-mode + bulk-action toolbar refs.
    toggleSelectBtn  = document.getElementById('toggle-select');
    bulkToolbarEl    = document.getElementById('bulk-toolbar');
    bulkCountEl      = document.getElementById('bulk-count');
    bulkSelectAllBtn = document.getElementById('bulk-select-all');
    bulkClearBtn     = document.getElementById('bulk-clear');
    bulkSaveBtn      = document.getElementById('bulk-save');
    bulkArchiveBtn   = document.getElementById('bulk-archive');
  }

  function wireStaticEvents {
    // Search box: SERVER-SIDE substring filter.
    // Pre-this filtered only the loaded page in JS, which was
    // useless as soon as the matching row was past the first page —
    // searching "FanDuel" wouldn't find a FanDuel posting on page 5.
    // Now we send `q=` to /api/jobs/browse and refetch from offset 0.
    // Debounced 250ms so each keystroke isn't an API call.
    searchInput.addEventListener('input', debounce( => {
      state.q = searchInput.value.trim;
      reloadFromZero;
    }, 250));

    // Sort dropdown
    sortSelect.addEventListener('change',  => {
      state.sort_by = sortSelect.value;
      // direction is implicit per option (newest=desc, oldest=asc, etc.)
      state.sort_dir = (state.sort_by === 'oldest') ? 'asc' : 'desc';
      state.offset = 0;
      reloadFromZero;
    });

    // Sliders — all three rebind to fetch (debounced 250ms).
    minScoreEl.addEventListener('input', debounce( => {
      state.min_score = parseInt(minScoreEl.value, 10) || 0;
      minScoreOut.textContent = state.min_score;
      reloadFromZero;
    }, 250));
    minQolEl.addEventListener('input', debounce( => {
      state.min_qol = parseInt(minQolEl.value, 10) || 0;
      minQolOut.textContent = state.min_qol;
      reloadFromZero;
    }, 250));
    minSalaryEl.addEventListener('input', debounce( => {
      state.min_salary = parseInt(minSalaryEl.value, 10) || 0;
      minSalaryOut.textContent = formatSalary(state.min_salary);
      reloadFromZero;
    }, 250));

    // Drawer open/close on mobile
    openFiltersBtn.addEventListener('click', openDrawer);
    closeFiltersBtn.addEventListener('click', closeDrawer);
    applyFiltersBtn.addEventListener('click', closeDrawer);
    railBackdropEl.addEventListener('click', closeDrawer);
    resetFiltersBtn.addEventListener('click', resetAll);

    // Load more — appends another page to the loadedRows.
    loadMoreBtn.addEventListener('click',  => {
      state.offset += state.limit;
      loadPage({ replace: false });
    });

    // Browser back/forward — re-read state from URL.
    window.addEventListener('popstate',  => {
      state = readStateFromUrl;
      hydrateSliders;
      searchInput.value = state.q;
      if (sortSelect && taxonomy) sortSelect.value = state.sort_by;
      reloadFromZero;
    });

    // -----------------------------------------------------------------
    // select mode + bulk-action toolbar wiring.
    // -----------------------------------------------------------------

    // Toggle select mode. Entering shows the toolbar + checkboxes;
    // exiting hides them and clears the selection (so a stale checkbox
    // state can never linger across mode flips).
    toggleSelectBtn.addEventListener('click',  => {
      setSelectMode(!selectMode);
    });

    // "All" — check every currently-loaded card.
    bulkSelectAllBtn.addEventListener('click',  => {
      // Bug fix: if the user hits "All" without having toggled Select
      // first, the cards have no <input class="bulk-check"> DOM yet —
      // renderCard only emits checkbox markup while selectMode is true —
      // so the two querySelectorAll loops below would be no-ops and
      // the user would see nothing happen visually beyond the count
      // bumping up. Enter select mode first; the re-render inside
      // setSelectMode emits the checkboxes, then the add/check loop
      // below takes effect. setSelectMode clears selectedIds as a side
      // effect, so we populate the Set *after* the call.
      if (!selectMode) {
        setSelectMode(true);
      }
      loadedRows.forEach(j => { if (j.job_id) selectedIds.add(j.job_id); });
      // Re-render checkbox state without refetching.
      jobCardsEl.querySelectorAll('input.bulk-check').forEach(cb => {
        cb.checked = true;
      });
      // Bug fix: also flip the `.is-selected` class on every
      // rendered card LI. Without this, ticking every checkbox left the
      // cards visually unhighlighted — the selection was "armed" but the
      // user couldn't SEE which rows were armed, which made the
      // tap-to-deselect workflow feel broken (the checkbox toggled but
      // nothing else changed on screen). Symmetric fix in `Clear` below.
      jobCardsEl.querySelectorAll('.job-card').forEach(card => {
        card.classList.add('is-selected');
      });
      updateBulkToolbar;
    });

    // "Clear" — uncheck everything; toolbar count drops to 0.
    bulkClearBtn.addEventListener('click',  => {
      selectedIds.clear;
      jobCardsEl.querySelectorAll('input.bulk-check').forEach(cb => {
        cb.checked = false;
      });
      // Symmetric with the All-button fix: strip `.is-selected` from
      // every card so the accent border/background disappears along
      // with the checkboxes. (Without this, after an "All → Clear"
      // sequence the cards would still render highlighted until the
      // next full re-render.)
      jobCardsEl.querySelectorAll('.job-card.is-selected').forEach(card => {
        card.classList.remove('is-selected');
      });
      updateBulkToolbar;
    });

    // "Save selected" — bulk save → reload (saved rows leave the active feed).
    bulkSaveBtn.addEventListener('click',  => {
      runBulkAction('save');
    });

    // "Archive selected" — bulk archive → reload.
    bulkArchiveBtn.addEventListener('click',  => {
      runBulkAction('skip');
    });

    // Delegated checkbox handler. Captures any click on a card-level
    // checkbox; we use `change` so keyboard space-bar also fires.
    jobCardsEl.addEventListener('change', (ev) => {
      const cb = ev.target.closest('input.bulk-check');
      if (!cb) return;
      const id = cb.getAttribute('data-job-id');
      if (!id) return;
      if (cb.checked) selectedIds.add(id);
      else            selectedIds.delete(id);
      syncSelectedClass(id, cb.checked);
      updateBulkToolbar;
    });

    // follow-up: card-tap-to-select. the original author found the
    // original 18px checkbox too small to discover; in select mode we
    // make the entire card a tap target. The CSS suppresses the title
    // link's pointer-events so a tap on the title (a) won't navigate to
    // /job.html, and (b) bubbles up to the card LI here.
    //
    // We listen at the UL level (delegated) so newly-rendered cards
    // automatically pick up the behavior with no per-render rebind.
    jobCardsEl.addEventListener('click', (ev) => {
      if (!selectMode) return;
      // If the user clicked the checkbox or its label wrapper directly,
      // let the native change event handle it — don't double-toggle.
      if (ev.target.closest('.job-card__check')) return;
      // Score badge is interactive (opens breakdown modal); leave it.
      if (ev.target.closest('[data-score-badge]')) return;
      // Company-link inside the subtitle should still be inert in
      // select mode (it would refilter the page mid-selection); just
      // swallow it and treat the click as a card tap.
      const card = ev.target.closest('.job-card');
      if (!card) return;
      const id = card.getAttribute('data-job-id');
      if (!id) return;
      // Suppress any anchor-tag default navigation that may have come
      // from a sub-element we didn't explicitly mask.
      ev.preventDefault;
      // Toggle the Set + the visual state + the in-card checkbox.
      const nowOn = !selectedIds.has(id);
      if (nowOn) selectedIds.add(id);
      else       selectedIds.delete(id);
      syncSelectedClass(id, nowOn);
      const cb = card.querySelector('input.bulk-check');
      if (cb) cb.checked = nowOn;
      updateBulkToolbar;
    });
  }

  // Helper: flip the `.is-selected` class on the card LI matching `id`.
  // Cheap visual update without a full re-render, so the user gets
  // instant feedback when they tap.
  function syncSelectedClass(id, on) {
    const card = jobCardsEl.querySelector(
      `.job-card[data-job-id="${cssEscape(id)}"]`
    );
    if (card) card.classList.toggle('is-selected', !!on);
  }

  // CSS.escape isn't available in older Safari; fall back to a basic
  // attribute-safe escape that handles the characters job_ids actually
  // contain (`:`, `-`, alphanum). Good enough for our id format.
  function cssEscape(s) {
    if (window.CSS && typeof window.CSS.escape === 'function') {
      return window.CSS.escape(s);
    }
    return String(s).replace(/[^a-zA-Z0-9_-]/g, c => '\\' + c);
  }

  function hydrateSliders {
    minScoreEl.value  = state.min_score;
    minQolEl.value    = state.min_qol;
    minSalaryEl.value = state.min_salary;
    minScoreOut.textContent  = state.min_score;
    minQolOut.textContent    = state.min_qol;
    minSalaryOut.textContent = formatSalary(state.min_salary);
  }

  /* =====================================================================
     Drawer (mobile only — desktop CSS overrides position)
     ===================================================================== */
     function openDrawer() {
      // Bug fix: the CSS listens for `body.rail-open` on both the
      // filter-rail (`transform: translateX(0)`) and the backdrop
      // (`opacity: 1`). Previously we toggled `.open` on the rail element
      // and `.show` on the backdrop element directly — no CSS rule matched
      // those names, so on mobile the drawer never slid in (rail stayed at
      // translateX(-100%), backdrop stayed at opacity 0). Desktop was
      // unaffected because at ≥1024px the rail is `position: sticky` with
      // `transform: none !important;`, overriding any transform transition.
      document.body.classList.add('rail-open');
      railBackdropEl.hidden = false;
      // Force a reflow so the opacity transition runs from 0 → 1 in the
      // next frame, instead of snapping to 1 in the same frame as the
      // `hidden` attr removal.
      void railBackdropEl.offsetWidth;
    }
    function closeDrawer() {
      document.body.classList.remove('rail-open');
      // Defer re-hiding the backdrop from the a11y tree until after the
      // opacity fade completes (matches --dur-2 = 200ms, padded to 240ms).
      setTimeout(() => { railBackdropEl.hidden = true; }, 240);
    }

  /* =====================================================================
     Facet rendering — chips for industries / role_types / etc.
     ===================================================================== */
  function renderFacets {
    if (!taxonomy) return;
    renderChipFacet('filter-industries',       taxonomy.industries       || , state.industries);
    renderChipFacet('filter-role-types',       taxonomy.role_types       || , state.role_types);
    renderChipFacet('filter-company-groups',   taxonomy.company_groups   || , state.company_groups);
    renderChipFacet('filter-work-modes',       taxonomy.work_modes       || , state.work_modes);
    renderChipFacet('filter-engagement-types', taxonomy.engagement_types || , state.engagement_types);
  }

  function renderChipFacet(elId, options, selectedArr) {
    const ul = document.getElementById(elId);
    if (!ul) return;
    const sel = new Set(selectedArr);
    ul.innerHTML = options.map(o => {
      const on = sel.has(o.value) ? ' on' : '';
      return `<li class="${on.trim}" data-value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</li>`;
    }).join('');
  }

  // Delegated click handler on the filter rail — toggles a chip's
  // selection in the right state-array based on the parent UL's
  // data-facet attribute.
  document.addEventListener('click', (ev) => {
    const li = ev.target.closest('.chip-list li[data-value]');
    if (!li) return;
    const ul = li.closest('.chip-list');
    if (!ul) return;
    const facet = ul.getAttribute('data-facet');   // "industries" etc.
    if (!facet || !Array.isArray(state[facet])) return;
    const value = li.getAttribute('data-value');
    const idx = state[facet].indexOf(value);
    if (idx >= 0) state[facet].splice(idx, 1);
    else state[facet].push(value);
    li.classList.toggle('on');
    state.offset = 0;
    reloadFromZero;
  });

  /* =====================================================================
     Status tabs
     ===================================================================== */
  function renderStatusTabs {
    const opts = (taxonomy && taxonomy.statuses) || [
      { value: 'active', label: 'Active' },
    ];
    statusTabsEl.innerHTML = opts.map(o => {
      const cls = o.value === state.status ? 'on' : '';
      return `<button type="button" class="${cls}" data-status="${escapeHtml(o.value)}">${escapeHtml(o.label)}</button>`;
    }).join('');
    statusTabsEl.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click',  => {
        state.status = btn.getAttribute('data-status');
        state.offset = 0;
        statusTabsEl.querySelectorAll('button').forEach(b => b.classList.remove('on'));
        btn.classList.add('on');
        reloadFromZero;
      });
    });
  }

  /* =====================================================================
     Sort dropdown
     ===================================================================== */
  function renderSortOptions {
    // Fallback list used only when the /api/taxonomy call fails. Kept in
    // sync with src/lambdas/api_jobs.py::_taxonomy so the dropdown still
    // shows every sort option if the network hiccups during load.
    const opts = (taxonomy && taxonomy.sort_options) || [
      { value: 'score',        label: 'Best match' },
      { value: 'semantic',     label: 'Semantic (Haiku) only' },
      { value: 'qol',          label: 'Quality of life' },
      { value: 'comp',         label: 'Compensation' },
      { value: 'newest',       label: 'Newest' },
      { value: 'oldest',       label: 'Oldest' },
      // Group by company, then rank score within each company.
      { value: 'company_asc',  label: 'Company (A\u2013Z)' },
      { value: 'company_desc', label: 'Company (Z\u2013A)' },
    ];
    sortSelect.innerHTML = opts.map(o =>
      `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`
    ).join('');
    sortSelect.value = state.sort_by;
  }

  /* =====================================================================
     Active filter chips
     ===================================================================== */
  function renderActiveChips {
    const chips = ;
    const labelOf = (facet, value) => {
      if (!taxonomy) return value;
      const opts = taxonomy[facet] || ;
      const hit = opts.find(o => o.value === value);
      return hit ? hit.label : value;
    };

    state.industries.forEach(v => chips.push(makeChip(
      'industries', v, 'Industry: ' + labelOf('industries', v))));
    state.role_types.forEach(v => chips.push(makeChip(
      'role_types', v, 'Role: ' + labelOf('role_types', v))));
    state.company_groups.forEach(v => chips.push(makeChip(
      'company_groups', v, 'Group: ' + labelOf('company_groups', v))));
    state.work_modes.forEach(v => chips.push(makeChip(
      'work_modes', v, labelOf('work_modes', v))));
    state.engagement_types.forEach(v => chips.push(makeChip(
      'engagement_types', v, labelOf('engagement_types', v))));

    if (state.min_score > 0) chips.push(makeNumChip('min_score', 'Score ≥ ' + state.min_score));
    if (state.min_qol   > 0) chips.push(makeNumChip('min_qol',   'QoL ≥ '   + state.min_qol));
    if (state.min_salary > 0) chips.push(makeNumChip('min_salary', 'Salary ≥ ' + formatSalary(state.min_salary)));
    if (state.q)             chips.push(makeNumChip('q',          '"' + state.q + '"'));

    if (chips.length === 0) {
      activeChipsEl.hidden = true;
      activeChipsEl.innerHTML = '';
      filterCountEl.hidden = true;
      filterCountEl.textContent = '0';
      return;
    }
    activeChipsEl.hidden = false;
    activeChipsEl.innerHTML = chips.join('');
    filterCountEl.hidden = false;
    filterCountEl.textContent = chips.length;

    // Wire the × buttons.
    activeChipsEl.querySelectorAll('button[data-clear]').forEach(btn => {
      btn.addEventListener('click',  => {
        const facet = btn.getAttribute('data-facet');
        const value = btn.getAttribute('data-value');
        if (facet === 'q') {
          // q is server-side; removing the chip refetches from
          // offset 0 instead of just re-rendering loaded rows.
          state.q = '';
          searchInput.value = '';
          reloadFromZero;
          return;
        }
        if (facet === 'min_score') {
          state.min_score = 0; minScoreEl.value = 0; minScoreOut.textContent = 0;
        } else if (facet === 'min_qol') {
          state.min_qol = 0; minQolEl.value = 0; minQolOut.textContent = 0;
        } else if (facet === 'min_salary') {
          state.min_salary = 0; minSalaryEl.value = 0; minSalaryOut.textContent = formatSalary(0);
        } else if (Array.isArray(state[facet])) {
          state[facet] = state[facet].filter(x => x !== value);
          renderFacets;   // refresh chip-cloud "on" classes
        }
        state.offset = 0;
        reloadFromZero;
      });
    });
  }

  function makeChip(facet, value, label) {
    return `<span class="chip">${escapeHtml(label)} <button type="button" data-clear data-facet="${escapeHtml(facet)}" data-value="${escapeHtml(value)}" aria-label="Remove">×</button></span>`;
  }
  function makeNumChip(facet, label) {
    return `<span class="chip">${escapeHtml(label)} <button type="button" data-clear data-facet="${escapeHtml(facet)}" aria-label="Remove">×</button></span>`;
  }

  /* =====================================================================
     URL <-> state
     ===================================================================== */
  function writeStateToUrl {
    const sp = new URLSearchParams;
    if (state.status !== 'active') sp.set('status', state.status);
    if (state.industries.length)       sp.set('industries',       state.industries.join(','));
    if (state.role_types.length)       sp.set('role_types',       state.role_types.join(','));
    if (state.company_groups.length)   sp.set('company_groups',   state.company_groups.join(','));
    if (state.work_modes.length)       sp.set('work_modes',       state.work_modes.join(','));
    if (state.engagement_types.length) sp.set('engagement_types', state.engagement_types.join(','));
    if (state.min_score  > 0) sp.set('min_score',  state.min_score);
    if (state.min_qol    > 0) sp.set('min_qol',    state.min_qol);
    if (state.min_salary > 0) sp.set('min_salary', state.min_salary);
    if (state.q)              sp.set('q',          state.q);
    if (state.sort_by !== 'score') sp.set('sort_by', state.sort_by);
    if (state.sort_dir !== 'desc') sp.set('sort_dir', state.sort_dir);
    if (state.q) sp.set('q', state.q);
    const next = location.pathname + (sp.toString ? '?' + sp : '');
    history.replaceState(null, '', next);
  }

  function readStateFromUrl {
    const sp = new URLSearchParams(location.search);
    const arr = (k) => (sp.get(k) || '').split(',').map(s => s.trim).filter(Boolean);
    const num = (k, def, lo, hi) => {
      const v = parseInt(sp.get(k), 10);
      if (!Number.isFinite(v)) return def;
      if (lo !== undefined && v < lo) return lo;
      if (hi !== undefined && v > hi) return hi;
      return v;
    };
    // Special-case the company-link param coming from job.html: a bare
    // ?companies= comes from a "show all jobs from this company" tap.
    // Treat it as a free-text query so the user sees something useful
    // even though we don't have a dedicated company filter.
    const cs = sp.get('companies');
    return {
      status:           sp.get('status') || 'active',
      industries:       arr('industries'),
      role_types:       arr('role_types'),
      company_groups:   arr('company_groups'),
      work_modes:       arr('work_modes'),
      engagement_types: arr('engagement_types'),
      min_score:        num('min_score',  0, 0, 100),
      min_qol:        num('min_qol',    0, 0, 100),
      min_salary:     num('min_salary', 0, 0, 10_000_000),
      sort_by:        sp.get('sort_by')  || 'score',
      sort_dir:       sp.get('sort_dir') || 'desc',
      q:              sp.get('q') || cs || '',
      limit:          50,
      offset:         0,
    };
  }

  function resetAll {
    state = defaultState;
    searchInput.value = '';
    hydrateSliders;
    sortSelect.value = state.sort_by;
    renderFacets;
    renderStatusTabs;
    renderActiveChips;
    reloadFromZero;
  }

  /* =====================================================================
     Fetch helpers
     ===================================================================== */
  function reloadFromZero {
    state.offset = 0;
    loadPage({ replace: true });
  }

  async function loadPage({ replace }) {
    if (inflight) inflight.abort;
    inflight = new AbortController;

    const params = new URLSearchParams;
    params.set('status', state.status);
    // q is server-side now.
    if (state.q)                       params.set('q',                state.q);
    if (state.industries.length)       params.set('industries',       state.industries.join(','));
    if (state.role_types.length)       params.set('role_types',       state.role_types.join(','));
    if (state.company_groups.length)   params.set('company_groups',   state.company_groups.join(','));
    if (state.work_modes.length)       params.set('work_modes',       state.work_modes.join(','));
    if (state.engagement_types.length) params.set('engagement_types', state.engagement_types.join(','));
    if (state.min_score  > 0) params.set('min_score',  state.min_score);
    if (state.min_qol    > 0) params.set('min_qol',    state.min_qol);
    if (state.min_salary > 0) params.set('min_salary', state.min_salary);
    params.set('sort_by',  state.sort_by);
    params.set('sort_dir', state.sort_dir);
    params.set('limit',    state.limit);
    params.set('offset',   state.offset);

    writeStateToUrl;
    showLoading(replace);

    try {
      const data = await fetchJson('/api/jobs/browse?' + params.toString, inflight.signal);
      lastResp = data;
      if (replace) {
        loadedRows = data.jobs || ;
      } else {
        loadedRows = loadedRows.concat(data.jobs || );
      }
      renderJobs;
      renderActiveChips;
      updateCounts;
      return data;
    } catch (err) {
      if (err.name === 'AbortError') return;
      toast('Failed to load: ' + err.message, { error: true });
      resultsCountEl.textContent = 'Failed to load.';
    } finally {
      inflight = null;
    }
  }

  function showLoading(isInitial) {
    if (isInitial) {
      jobCardsEl.innerHTML = '';
      emptyStateEl.hidden = true;
      loadMoreBtn.hidden = true;
    }
    resultsCountEl.innerHTML = '<span class="spinner"></span> loading…';
  }

  function updateCounts {
    const total     = (lastResp && lastResp.total)     || 0;
    const rawTotal  = (lastResp && lastResp.raw_total) || total;
    const shown     = loadedRows.length;
    // dedup collapses the raw row count. Surface the gap so
    // the original author sees "1,234 roles · 89 dupes collapsed" instead of being
    // confused by a moving total.
    const dedupGap  = rawTotal - total;
    const dedupNote = (dedupGap > 0) ? ` · ${dedupGap} dupes collapsed` : '';
    if (state.q) {
      resultsCountEl.textContent =
        `${total} matching "${state.q}" · showing ${shown}${dedupNote}`;
    } else {
      resultsCountEl.textContent =
        `Showing ${shown} of ${total}${dedupNote}`;
    }
    railCountEl.textContent = `${total} match${total === 1 ? '' : 'es'}`;
    topbarSubtitle.textContent =
      total === 0 ? 'No matches in this view'
                  : `${total} ${state.status} ${total === 1 ? 'job' : 'jobs'}`;
    loadMoreBtn.hidden = !(lastResp && lastResp.has_more);
  }

  // Tiny helper that throws on non-2xx. Used instead of window.Api.get
  // because we need an AbortSignal for cancelling stale requests.
  async function fetchJson(path, signal) {
    const r = await fetch(path, { credentials: 'same-origin', signal });
    if (!r.ok) throw new Error('HTTP ' + r.status + ' on ' + path);
    return r.json;
  }

  /* =====================================================================
     Card rendering
     ===================================================================== */
  // applyLocalSearch removed; q= is server-side now.
  function renderJobs {
    if (loadedRows.length === 0) {
      jobCardsEl.innerHTML = '';
      emptyStateEl.hidden  = false;
      return;
    }
    emptyStateEl.hidden = true;
    jobCardsEl.innerHTML = loadedRows.map(renderCard).join('');
  }

  // Build the card markup. Reads enriched fields from R1: industries,
  // role_types, company_group, qol_score, plus salary / posted_at /
  // work_mode that already existed.
  function renderCard(j) {
    const score = Number(j.score) || 0;
    const qol   = Number(j.qol_score);
    const detail = '/job.html?id=' + encodeURIComponent(j.job_id);

    // Subtitle row: company + industry + work-mode chip + posted
    const company = (j.company || '').trim;
    const companyHref = company ? '/index.html?companies=' + encodeURIComponent(company) : null;
    const subParts = ;
    if (companyHref) {
      subParts.push(`<a class="company-link" href="${companyHref}">${escapeHtml(company)}</a>`);
    } else if (company) {
      subParts.push(`<span>${escapeHtml(company)}</span>`);
    }
    if (Array.isArray(j.industries) && j.industries.length) {
      subParts.push(`<span class="muted-meta">${escapeHtml(industryLabels(j.industries).join(', '))}</span>`);
    }
    if (j.work_mode && j.work_mode !== 'unclear') {
      const lbl = j.work_mode.charAt(0).toUpperCase + j.work_mode.slice(1);
      subParts.push(`<span class="work-mode-chip wm-${escapeHtml(j.work_mode)}">${escapeHtml(lbl)}</span>`);
    }
    // engagement-type chip — only shown when the detector
    // surfaced something other than the default full-time / unclear.
    // the original author wants the rare exec-track engagements (interim, advisor)
    // visible at a glance without burying the score.
    if (j.engagement_type && j.engagement_type !== 'fulltime'
                          && j.engagement_type !== 'unclear') {
      const elbl = facetLabel('engagement_types', j.engagement_type);
      subParts.push(`<span class="engagement-chip eng-${escapeHtml(j.engagement_type)}">${escapeHtml(elbl)}</span>`);
    }
    if (j.location) {
      subParts.push(`<span class="muted-meta">${escapeHtml(j.location)}</span>`);
    }
    if (j.posted_at) {
      subParts.push(`<span class="muted-meta">${escapeHtml(formatPosted(j.posted_at))}</span>`);
    }
    // dedup hint. dupe_count > 1 means this same role was
    // also pulled by N-1 other sources; we surface the source list so
    // the original author can see "also on apify_linkedin" without losing the data.
    const dc = Number(j.dupe_count) || 0;
    if (dc > 1 && Array.isArray(j.dupe_sources) && j.dupe_sources.length > 1) {
      const others = j.dupe_sources.filter(s => s && s !== j.source);
      const tip = others.length
        ? `Also pulled by: ${others.join(', ')}`
        : `Seen ${dc} times`;
      subParts.push(`<span class="muted-meta dupe-hint" title="${escapeHtml(tip)}">+${dc - 1} dupe${dc === 2 ? '' : 's'}</span>`);
    }

    // Salary, if present
    let salaryStr = '';
    const lo = Number(j.salary_min), hi = Number(j.salary_max);
    if (Number.isFinite(lo) && lo > 0 || Number.isFinite(hi) && hi > 0) {
      const fLo = Number.isFinite(lo) && lo > 0 ? '$' + (lo / 1000).toFixed(0) + 'k' : null;
      const fHi = Number.isFinite(hi) && hi > 0 ? '$' + (hi / 1000).toFixed(0) + 'k' : null;
      salaryStr = fLo && fHi ? `${fLo} – ${fHi}` : (fLo || fHi);
    }

    // Tags (role_types + company_group). Industries already shown above
    // so we keep them out of the tag row to avoid duplication.
    const tagBits = ;
    if (j.company_group) {
      tagBits.push(`<span class="tag tag--group">${escapeHtml(facetLabel('company_groups', j.company_group))}</span>`);
    }
    if (Array.isArray(j.role_types)) {
      j.role_types.slice(0, 4).forEach(rt => {
        tagBits.push(`<span class="tag tag--role">${escapeHtml(facetLabel('role_types', rt))}</span>`);
      });
    }

    // QoL bar (only if the field is present — older rows pre-R1 won't have it)
    let qolBar = '';
    if (Number.isFinite(qol)) {
      qolBar = `
        <div class="qol-bar" style="--qol:${qol}%">
          <span class="qol-bar__lbl">QoL</span>
          <span class="qol-bar__track"><span class="qol-bar__fill"></span></span>
          <span class="qol-bar__val">${qol}</span>
        </div>`;
    }

    // Top-right metric pills: salary on the left of the score badge.
    const metricsBits = ;
    if (salaryStr) {
      metricsBits.push(`<span class="metric-pill"><span class="metric-pill__lbl">Comp</span> ${escapeHtml(salaryStr)}</span>`);
    }
    if (score > 0) {
      metricsBits.push(`<span class="score-badge ${scoreClass(score)}" data-score-badge tabindex="0" role="button" aria-label="Score ${score}, tap for breakdown">${score}</span>`);
    }

    // bulk-select checkbox. Only rendered while select mode is
    // active so the card layout stays unchanged for the normal flow.
    // We use `data-job-id` (not `name=job_ids`) because the form is
    // never submitted — the JS reads selectedIds directly.
    let checkboxHtml = '';
    if (selectMode) {
      const checked = selectedIds.has(j.job_id) ? ' checked' : '';
      // aria-label uses the title so screen readers announce "Checkbox
      // for AI Deployment Manager" instead of an opaque "Checkbox 17".
      checkboxHtml = `<label class="job-card__check" aria-label="Select ${escapeHtml(j.title || j.job_id)}">
        <input type="checkbox" class="bulk-check" data-job-id="${escapeHtml(j.job_id)}"${checked}>
      </label>`;
    }

    // semantic snippet — one truncated line of LLM rationale
    // when present. The server already truncates to ~220 chars before
    // sending. If the rationale is missing (older row, or low-algo row
    // that the engine deliberately skipped), we render nothing rather
    // than a placeholder; the score badge already conveys the verdict.
    let semanticHtml = '';
    const rat = (j.semantic_rationale || '').trim;
    if (rat) {
      // dropped the inline "LLM NN" chip that used to sit
      // in front of the rationale. The right-side score badge already
      // shows the same number, so the chip was duplicate signal and
      // visually busy. The prose itself remains — that's the one piece
      // the badge can't show at a glance.
      semanticHtml = `<p class="semantic-snippet" title="${escapeHtml(rat)}">${escapeHtml(rat)}</p>`;
    }

    // `.is-selected` keeps the visual highlight in sync after a "Load
    // more" or status-tab switch — the Set survives across pagination
    // even though the DOM is re-rendered.
    const selClass = (selectMode && selectedIds.has(j.job_id))
      ? ' is-selected' : '';

    return `
      <li class="job-card${selClass}" data-job-id="${escapeHtml(j.job_id)}">
        ${checkboxHtml}
        <div class="job-card__row1">
          <a class="job-card__title" href="${detail}">${escapeHtml(j.title || '(untitled)')}</a>
          <div class="job-card__metrics">${metricsBits.join('')}</div>
        </div>
        <div class="job-card__sub">${subParts.join('<span class="sep">·</span>')}</div>
        ${qolBar}
        ${semanticHtml}
        ${tagBits.length ? `<div class="job-card__tags">${tagBits.join('')}</div>` : ''}
      </li>
    `;
  }

  // Look up taxonomy human label, falling back to raw value. Used for
  // both the in-card industry list and the tag chips.
  function facetLabel(facet, value) {
    if (!taxonomy) return value;
    const opts = taxonomy[facet] || ;
    const hit = opts.find(o => o.value === value);
    return hit ? hit.label : value;
  }
  function industryLabels(values) {
    return values.map(v => facetLabel('industries', v));
  }

  /* =====================================================================
     bulk select + bulk action helpers
     ===================================================================== */

  // Flip the page in/out of select mode. Entering reveals the toolbar
  // and the per-card checkboxes; exiting hides both and forgets the
  // selection (don't carry checkbox state across mode flips — it
  // confuses the user about what's "armed").
  function setSelectMode(on) {
    selectMode = !!on;
    selectedIds.clear;
    document.body.classList.toggle('select-mode', selectMode);
    bulkToolbarEl.hidden = !selectMode;
    toggleSelectBtn.setAttribute('aria-pressed', selectMode ? 'true' : 'false');
    const lbl = toggleSelectBtn.querySelector('.select-label');
    if (lbl) lbl.textContent = selectMode ? 'Done' : 'Select';
    // Re-render so cards pick up / drop the checkbox markup.
    renderJobs;
    updateBulkToolbar;
  }

  // Refresh the toolbar count text + the disabled state of the Save /
  // Archive buttons. Called after every checkbox change + every render.
  function updateBulkToolbar {
    const n = selectedIds.size;
    if (bulkCountEl) bulkCountEl.textContent =
      `${n} selected`;
    if (bulkSaveBtn)    bulkSaveBtn.disabled    = (n === 0);
    if (bulkArchiveBtn) bulkArchiveBtn.disabled = (n === 0);
  }

  // POST /api/jobs/bulk_action with the current selection. On success
  // we clear the selection, exit select mode, and reload from offset 0
  // the rows we just acted on no longer match the active filter
  // (e.g. archived rows are gone from the active tab) so the reload
  // is the natural "they've vanished" UX the original author asked for.
  async function runBulkAction(action) {
    const ids = Array.from(selectedIds);
    if (ids.length === 0) return;
    const verb = action === 'save' ? 'save' : 'archive';
    // Lightweight guardrail on big batches — accidental mass-archive
    // is hard to undo since the user has to dig through the archived
    // tab to restore each row individually.
    if (ids.length >= 25 &&
        !confirm(`${verb} ${ids.length} jobs? This will reload the list.`)) {
      return;
    }

    // Disable the buttons during the request so a double-tap can't
    // fire the same call twice.
    bulkSaveBtn.disabled = true;
    bulkArchiveBtn.disabled = true;

    try {
      const r = await fetch('/api/jobs/bulk_action', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ action, job_ids: ids }),
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json;
      const msg = data.ok
        ? `${data.updated} ${verb}d`
        : `${data.updated} ${verb}d, ${data.missing.length} missing, ${data.errors.length} errors`;
      toast(msg);

      // Selection done — drop it, leave select mode, and reload.
      selectedIds.clear;
      setSelectMode(false);
      reloadFromZero;
    } catch (err) {
      toast('Bulk ' + verb + ' failed: ' + err.message, { error: true });
      // Re-enable so the original author can retry without leaving select mode.
      updateBulkToolbar;
    }
  }

  /* =====================================================================
     Misc helpers
     ===================================================================== */
  function debounce(fn, ms) {
    let t = null;
    return function  {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout( => fn.apply(this, args), ms);
    };
  }
  function formatSalary(n) {
    if (!n || n <= 0) return '$0';
    if (n >= 1000) return '$' + (n / 1000).toFixed(0) + 'k';
    return '$' + n;
  }
});
