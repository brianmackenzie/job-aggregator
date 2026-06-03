// Settings page — read/write per-user prefs.
//
// Two managed lists: hidden_companies (string) and saved_searches
// ({name, query}). The third managed value is display_options, a
// {hide_below_score: number} dict.
//
// Every save writes the entire list back via PUT /api/prefs to keep
// the server-side concurrency model trivial — the table is single-user.

document.addEventListener('DOMContentLoaded', async  => {
  // Hidden companies
  const hcInput = document.getElementById('hc-input');
  const hcAdd   = document.getElementById('hc-add');
  const hcList  = document.getElementById('hc-list');

  // Saved searches
  const ssName  = document.getElementById('ss-name');
  const ssQuery = document.getElementById('ss-query');
  const ssAdd   = document.getElementById('ss-add');
  const ssList  = document.getElementById('ss-list');

  // Display options
  const minScore = document.getElementById('opt-min-score');
  const optSave  = document.getElementById('opt-save');

  let prefs = { hidden_companies: , saved_searches: , display_options: {} };

  try {
    const r = await Api.get('/prefs');
    prefs = Object.assign(prefs, r.prefs || {});
  } catch (err) {
    toast('Failed to load prefs: ' + err.message, { error: true });
  }

  renderHidden;
  renderSaved;
  renderOptions;

  hcAdd.addEventListener('click', async  => {
    const name = (hcInput.value || '').trim.toLowerCase;
    if (!name) return;
    if ((prefs.hidden_companies || ).includes(name)) {
      toast('Already hidden');
      return;
    }
    prefs.hidden_companies = (prefs.hidden_companies || ).concat([name]);
    await save('hidden_companies', prefs.hidden_companies);
    hcInput.value = '';
    renderHidden;
  });

  ssAdd.addEventListener('click', async  => {
    const name  = (ssName.value  || '').trim;
    const query = (ssQuery.value || '').trim;
    if (!name || !query) return;
    prefs.saved_searches = (prefs.saved_searches || ).concat([{ name, query }]);
    await save('saved_searches', prefs.saved_searches);
    ssName.value = ssQuery.value = '';
    renderSaved;
  });

  optSave.addEventListener('click', async  => {
    const v = parseInt(minScore.value, 10);
    prefs.display_options = Object.assign({}, prefs.display_options || {},
      { hide_below_score: Number.isFinite(v) ? v : 0 });
    await save('display_options', prefs.display_options);
    toast('Saved');
  });

  // ---- helpers ----
  async function save(key, value) {
    try {
      await Api.put('/prefs', { config_key: key, value });
    } catch (err) {
      toast('Save failed: ' + err.message, { error: true });
    }
  }

  function renderHidden {
    const xs = prefs.hidden_companies || ;
    if (xs.length === 0) {
      hcList.innerHTML = '<li class="muted">No companies hidden.</li>';
      return;
    }
    hcList.innerHTML = xs.map((name, i) =>
      `<li>${escapeHtml(name)}<button data-i="${i}" aria-label="Remove">×</button></li>`
    ).join('');
    hcList.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', async  => {
        const i = Number(btn.dataset.i);
        prefs.hidden_companies.splice(i, 1);
        await save('hidden_companies', prefs.hidden_companies);
        renderHidden;
      });
    });
  }

  function renderSaved {
    const xs = prefs.saved_searches || ;
    if (xs.length === 0) {
      ssList.innerHTML = '<li class="muted">No saved searches.</li>';
      return;
    }
    ssList.innerHTML = xs.map((s, i) =>
      `<li><strong>${escapeHtml(s.name)}</strong> <span class="muted">— ${escapeHtml(s.query)}</span><button data-i="${i}" aria-label="Remove">×</button></li>`
    ).join('');
    ssList.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', async  => {
        const i = Number(btn.dataset.i);
        prefs.saved_searches.splice(i, 1);
        await save('saved_searches', prefs.saved_searches);
        renderSaved;
      });
    });
  }

  function renderOptions {
    const v = (prefs.display_options || {}).hide_below_score;
    minScore.value = Number.isFinite(v) ? v : 0;
  }
});
