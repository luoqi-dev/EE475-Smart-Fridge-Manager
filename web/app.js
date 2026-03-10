document.addEventListener('DOMContentLoaded', function () {
  const API_SUMMARY = '/api/inventory/summary';
  const API_ADD = '/api/manual/add';
  const API_REMOVE = '/api/manual/remove';
  const API_COLLISIONS_OPEN = '/api/collisions/open?include_items=1';

  const table = document.getElementById('inventoryTable');
  const tbody = table?.querySelector('tbody');
  const refreshBtn = document.getElementById('refreshBtn');
  const statusText = document.getElementById('statusText');

  const itemInput = document.getElementById('itemInput');
  const addBtn = document.getElementById('addBtn');
  const removeBtn = document.getElementById('removeBtn');
  const actionStatus = document.getElementById('actionStatus');

  const modal = document.getElementById('collisionModal');
  const collisionCardList = document.getElementById('collisionCardList');

  if (
    !table || !tbody || !refreshBtn || !statusText ||
    !itemInput || !addBtn || !removeBtn || !actionStatus ||
    !modal || !collisionCardList
  ) {
    console.error('Required elements not found.');
    return;
  }

  let refreshing = false;

  let openCases = [];
  let modalOpen = false;

  function setStatus(el, msg, isError = false) {
    el.textContent = msg;
    el.classList.toggle('error', !!isError);
  }

  function escapeHtml(s) {
    return String(s ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
  }

  function formatUtcToLocal(isoUtc) {
    if (!isoUtc) return '';
    const d = new Date(isoUtc);
    if (Number.isNaN(d.getTime())) return String(isoUtc);
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    const hh = String(d.getHours()).padStart(2, '0');
    const mi = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd} ${hh}:${mi}:${ss}`;
  }

  function showModal(show) {
    modal.classList.toggle('hidden', !show);
    modalOpen = show;
    if (!show) {
      collisionCardList.innerHTML = '';
    }
  }

  function renderSummary(rows) {
    tbody.innerHTML = '';
    if (!Array.isArray(rows) || rows.length === 0) {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td colspan="4" style="text-align:center;opacity:0.7;">No data</td>`;
      tbody.appendChild(tr);
      return;
    }

    for (const r of rows) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${escapeHtml(r.item_name)}</td>
        <td>${escapeHtml(r.quantity)}</td>
        <td>${escapeHtml(formatUtcToLocal(r.earliest_put_in_time))}</td>
        <td>${escapeHtml(formatUtcToLocal(r.latest_put_in_time))}</td>
      `;
      tbody.appendChild(tr);
    }
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;

    setStatus(statusText, 'Loading...');
    refreshBtn.disabled = true;

    try {
      const res = await fetch(API_SUMMARY);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderSummary(data);
      setStatus(statusText, `Updated: ${new Date().toLocaleString()}`);
    } catch (e) {
      console.error(e);
      renderSummary([]);
      setStatus(statusText, `Error: ${e.message}`, true);
    } finally {
      refreshBtn.disabled = false;
      refreshing = false;
    }
  }

  function getPayload() {
    const item_name = (itemInput.value || '').trim();
    return { item_name };
  }

  async function postJson(url, payload) {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const text = await res.text();
    let json = null;
    try { json = text ? JSON.parse(text) : null; } catch (_) {}

    if (!res.ok) {
      const msg = (json && json.error) ? json.error : (text || `HTTP ${res.status}`);
      throw new Error(msg);
    }
    return json;
  }

  async function handleAdd() {
    const payload = getPayload();
    if (!payload.item_name) {
      setStatus(actionStatus, 'Item is required.', true);
      return;
    }

    addBtn.disabled = true;
    removeBtn.disabled = true;
    setStatus(actionStatus, 'Adding...');

    try {
      const r = await postJson(API_ADD, payload);
      setStatus(actionStatus, `Added "${payload.item_name}" (track_id=${r.track_id})`);
      await refresh();
    } catch (e) {
      console.error(e);
      setStatus(actionStatus, `Error: ${e.message}`, true);
    } finally {
      addBtn.disabled = false;
      removeBtn.disabled = false;
    }
  }

  async function handleRemove() {
    const payload = getPayload();
    if (!payload.item_name) {
      setStatus(actionStatus, 'Item is required.', true);
      return;
    }

    addBtn.disabled = true;
    removeBtn.disabled = true;
    setStatus(actionStatus, 'Removing...');

    try {
      const r = await postJson(API_REMOVE, payload);
      setStatus(actionStatus, `Removed "${payload.item_name}" (id=${r.id})`);
      await refresh();
    } catch (e) {
      console.error(e);
      setStatus(actionStatus, `Error: ${e.message}`, true);
    } finally {
      addBtn.disabled = false;
      removeBtn.disabled = false;
    }
  }

  function formatCaseTime(isoUtc) {
    if (!isoUtc) return '';
    const d = new Date(isoUtc);
    if (Number.isNaN(d.getTime())) return String(isoUtc);
    return new Intl.DateTimeFormat(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
    }).format(d);
  }

  function titleCase(value) {
    const s = String(value || '').trim();
    if (!s) return 'Collision';
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  function getCaseViewModel(c) {
    const pendingIds = Array.isArray(c.pending_item_ids) ? c.pending_item_ids.map(String) : [];
    const defaultIds = Array.isArray(c.default_remove_ids) ? c.default_remove_ids.map(String) : [];
    const itemsById = new Map((Array.isArray(c.items) ? c.items : []).map((it) => [String(it.id), it]));

    const candidateItems = pendingIds
      .map((id) => itemsById.get(String(id)))
      .filter(Boolean)
      .sort((a, b) => {
        const timeCmp = String(a.event_time_utc).localeCompare(String(b.event_time_utc));
        if (timeCmp !== 0) return timeCmp;
        return Number(a.id) - Number(b.id);
      });

    const sequenceById = new Map(candidateItems.map((item, index) => [String(item.id), index + 1]));
    const defaultItems = defaultIds
      .map((id) => itemsById.get(String(id)))
      .filter(Boolean)
      .sort((a, b) => {
        const left = sequenceById.get(String(a.id)) || 0;
        const right = sequenceById.get(String(b.id)) || 0;
        return left - right;
      });

    return {
      pendingIds,
      defaultIds,
      candidateItems,
      defaultItems,
      sequenceById,
      requiredCount: defaultIds.length,
    };
  }

  function getCardSelectedIds(cardEl) {
    const checks = cardEl.querySelectorAll('.collisionCheck');
    const ids = [];
    for (const check of checks) {
      if (check.checked) ids.push(String(check.value));
    }
    return ids;
  }

  function getExistingCaseSelections() {
    const selectionByCaseId = new Map();
    const cards = collisionCardList.querySelectorAll('.collision-case-card');
    for (const card of cards) {
      const caseId = card.dataset.caseId;
      if (!caseId) continue;
      selectionByCaseId.set(caseId, getCardSelectedIds(card));
    }
    return selectionByCaseId;
  }

  async function submitCollisionAction(caseId, removeItemIds) {
    const res = await fetch(`/api/collisions/${encodeURIComponent(caseId)}/actions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ remove_item_ids: removeItemIds.map(Number) }),
    });

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    return res.json();
  }

  function removeCaseCard(caseId) {
    openCases = openCases.filter((c) => c.case_id !== caseId);
    renderCollisionCases();
  }

  function renderCollisionCases() {
    const existingSelections = getExistingCaseSelections();
    collisionCardList.innerHTML = '';

    if (!Array.isArray(openCases) || openCases.length === 0) {
      showModal(false);
      return;
    }

    for (const c of openCases) {
      const view = getCaseViewModel(c);
      const requiredCount = Math.max(0, view.requiredCount);

      const card = document.createElement('section');
      card.className = 'collision-case-card';
      card.dataset.caseId = c.case_id;

      const statusEl = document.createElement('span');
      statusEl.className = 'status';
      statusEl.textContent = 'Idle';

      const defaultMessage = document.createElement('p');
      defaultMessage.className = 'collision-message';
      defaultMessage.textContent =
        `By default, the system will remove these ${requiredCount} ${c.item_name} item${requiredCount === 1 ? '' : 's'}. If these are not the correct items, select the items you want to remove below.`;

      const defaultListHtml = view.defaultItems.length > 0
        ? view.defaultItems.map((item) => `
            <div class="collision-item-row">
              <div class="collision-item-meta">
                <span class="collision-item-name">${escapeHtml(c.item_name)} #${escapeHtml(view.sequenceById.get(String(item.id)) || '?')}</span>
                <span class="collision-item-time">${escapeHtml(formatCaseTime(item.event_time_utc))}</span>
              </div>
            </div>
          `).join('')
        : '<div class="collision-item-row"><div class="collision-item-meta"><span class="collision-item-time">No default items available.</span></div></div>';

      const selectedIds = existingSelections.get(c.case_id) || view.defaultIds;
      const candidateListHtml = view.candidateItems.map((item) => {
        const checked = selectedIds.includes(String(item.id));
        return `
          <label class="collision-item-row">
            <input type="checkbox" class="collisionCheck" value="${escapeHtml(item.id)}" ${checked ? 'checked' : ''} />
            <div class="collision-item-meta">
              <span class="collision-item-name">${escapeHtml(c.item_name)} #${escapeHtml(view.sequenceById.get(String(item.id)) || '?')}</span>
              <span class="collision-item-time">${escapeHtml(formatCaseTime(item.event_time_utc))}</span>
            </div>
          </label>
        `;
      }).join('');

      card.innerHTML = `
        <div class="collision-case-header">
          <h3 class="collision-case-title">${escapeHtml(titleCase(c.item_name))}</h3>
          <span class="hint">${escapeHtml(c.pending_type || 'OPEN')}</span>
        </div>

        <div class="collision-section">
          <h4 class="collision-section-title">Summary</h4>
          <p class="collision-message">${escapeHtml(c.summary || '')}</p>
        </div>

        <div class="collision-section collision-default-block">
          <h4 class="collision-section-title">Default Action</h4>
          ${defaultMessage.outerHTML}
          <div class="collision-item-list">${defaultListHtml}</div>
        </div>

        <div class="collision-section">
          <h4 class="collision-section-title">Select Actual Items To Remove</h4>
          <p class="collision-message">If the default action is incorrect, select the items you want to remove.</p>
          <div class="collision-item-list">${candidateListHtml}</div>
        </div>

        <div class="collision-actions">
          <button type="button" class="collisionConfirmBtn">Confirm</button>
          <button type="button" class="collisionIgnoreBtn collision-btn-secondary">Ignore</button>
        </div>
      `;

      const checks = card.querySelectorAll('.collisionCheck');
      const confirmBtn = card.querySelector('.collisionConfirmBtn');
      const ignoreBtn = card.querySelector('.collisionIgnoreBtn');
      card.querySelector('.collision-actions').appendChild(statusEl);

      function syncSelectionWarning(changedInput) {
        const selected = getCardSelectedIds(card);
        if (selected.length > requiredCount && changedInput) {
          changedInput.checked = false;
          setStatus(statusEl, `You can select at most ${requiredCount} item${requiredCount === 1 ? '' : 's'}.`, true);
          return false;
        }
        setStatus(statusEl, 'Idle');
        return true;
      }

      for (const check of checks) {
        check.addEventListener('change', () => {
          syncSelectionWarning(check);
        });
      }

      confirmBtn.addEventListener('click', async () => {
        const selected = getCardSelectedIds(card);
        if (selected.length !== requiredCount) {
          const msg = `Please select exactly ${requiredCount} item${requiredCount === 1 ? '' : 's'} or use Ignore.`;
          setStatus(statusEl, msg, true);
          window.alert(msg);
          return;
        }

        confirmBtn.disabled = true;
        ignoreBtn.disabled = true;
        setStatus(statusEl, 'Submitting...');
        try {
          await submitCollisionAction(c.case_id, selected);
          removeCaseCard(c.case_id);
          await refresh();
        } catch (e) {
          setStatus(statusEl, `Error: ${e.message}`, true);
        } finally {
          confirmBtn.disabled = false;
          ignoreBtn.disabled = false;
        }
      });

      ignoreBtn.addEventListener('click', async () => {
        confirmBtn.disabled = true;
        ignoreBtn.disabled = true;
        setStatus(statusEl, 'Submitting...');
        try {
          await submitCollisionAction(c.case_id, view.defaultIds);
          removeCaseCard(c.case_id);
          await refresh();
        } catch (e) {
          setStatus(statusEl, `Error: ${e.message}`, true);
        } finally {
          confirmBtn.disabled = false;
          ignoreBtn.disabled = false;
        }
      });

      collisionCardList.appendChild(card);
    }

    showModal(true);
  }

  async function pollCollisions() {
    try {
      const res = await fetch(API_COLLISIONS_OPEN);
      if (!res.ok) return;

      const cases = await res.json();

      if (!Array.isArray(cases) || cases.length === 0) {
        if (modalOpen) showModal(false);
        openCases = [];
        return;
      }

      openCases = cases;
      renderCollisionCases();
    } catch (_) {}
  }

  refreshBtn.addEventListener('click', refresh);
  addBtn.addEventListener('click', handleAdd);
  removeBtn.addEventListener('click', handleRemove);

  itemInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') handleAdd();
  });

  refresh();
  pollCollisions();
  if (window.__smartFridgeRefreshTimer) window.clearInterval(window.__smartFridgeRefreshTimer);
  if (window.__smartFridgeCollisionTimer) window.clearInterval(window.__smartFridgeCollisionTimer);
  window.__smartFridgeRefreshTimer = window.setInterval(refresh, 5000);
  window.__smartFridgeCollisionTimer = window.setInterval(pollCollisions, 5000);
});
