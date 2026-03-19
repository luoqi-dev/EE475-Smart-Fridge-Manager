document.addEventListener('DOMContentLoaded', function () {
  const API_SUMMARY = '/api/inventory/summary';
  const API_INVENTORY = '/api/inventory';
  const API_ADD = '/api/manual/add';
  const API_REMOVE = '/api/manual/remove';
  const API_COLLISIONS_OPEN = '/api/collisions/open?include_items=1';
  const API_ENVIRONMENT = '/api/environment/latest';

  const table = document.getElementById('inventoryTable');
  const tbody = table?.querySelector('tbody');
  const refreshBtn = document.getElementById('refreshBtn');
  const statusText = document.getElementById('statusText');

  const itemInput = document.getElementById('itemInput');
  const manualYearInput = document.getElementById('manualYearInput');
  const manualMonthInput = document.getElementById('manualMonthInput');
  const manualDayInput = document.getElementById('manualDayInput');
  const manualHourInput = document.getElementById('manualHourInput');
  const addBtn = document.getElementById('addBtn');
  const removeBtn = document.getElementById('removeBtn');
  const actionStatus = document.getElementById('actionStatus');
  const tempValue = document.getElementById('tempValue');
  const humidityValue = document.getElementById('humidityValue');
  const envStatus = document.getElementById('envStatus');

  const modal = document.getElementById('collisionModal');
  const collisionCardList = document.getElementById('collisionCardList');
  const manualRemoveModal = document.getElementById('manualRemoveModal');
  const manualRemoveTitle = document.getElementById('manualRemoveTitle');
  const manualRemoveSummary = document.getElementById('manualRemoveSummary');
  const manualRemoveItems = document.getElementById('manualRemoveItems');
  const manualRemoveConfirmBtn = document.getElementById('manualRemoveConfirmBtn');
  const manualRemoveCancelBtn = document.getElementById('manualRemoveCancelBtn');
  const manualRemoveStatus = document.getElementById('manualRemoveStatus');
  const toastContainer = document.getElementById('toastContainer');

  if (
    !table || !tbody || !refreshBtn || !statusText ||
    !itemInput || !manualYearInput || !manualMonthInput || !manualDayInput ||
    !manualHourInput || !addBtn || !removeBtn || !actionStatus ||
    !tempValue || !humidityValue || !envStatus || !toastContainer ||
    !modal || !collisionCardList || !manualRemoveModal || !manualRemoveTitle ||
    !manualRemoveSummary || !manualRemoveItems || !manualRemoveConfirmBtn ||
    !manualRemoveCancelBtn || !manualRemoveStatus
  ) {
    console.error('Required elements not found.');
    return;
  }

  let refreshing = false;

  let openCases = [];
  let modalOpen = false;
  let manualRemoveSelection = [];
  let manualRemoveItemsState = [];

  // Expiration rules
  const EXPIRY_RULES_MS = {
    apple: 300 * 1000,
    banana: 600 * 1000,
    orange: 120 * 1000,
    lemon: 600 * 1000,
    carrot: 600 * 1000,
  };
  const expiredToastShown = new Set();

  const toastQueue = [];
  let toastActive = false;

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


  // Toast helper
  function showToast(message) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toastData = { message };

    if (toastActive) {
      toastQueue.push(toastData);
      return;
    }

    displayToast(toastData);
  }

  function displayToast(data) {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    toastActive = true;

    const toast = document.createElement('div');
    toast.className = 'toast';

    toast.innerHTML = `
      <div class="toast-title">Expired Item</div>
      <div class="toast-message">${data.message}</div>
      <button class="toast-close">&times;</button>
    `;

    const closeBtn = toast.querySelector('.toast-close');

    closeBtn.addEventListener('click', () => {
      toast.remove();
      toastActive = false;

      showNextToast();
    });

    container.appendChild(toast);
  }

  function showNextToast() {
    if (toastQueue.length === 0) return;

    const nextToast = toastQueue.shift();
    displayToast(nextToast);
  }

  // Expiration helpers
  function getExpiryMs(itemName) {
    return EXPIRY_RULES_MS[String(itemName || '').trim().toLowerCase()] || null;
  }

  function getExpiredInventoryItems(items) {
    const now = Date.now();
    const expired = [];

    for (const item of Array.isArray(items) ? items : []) {
      const expiryMs = getExpiryMs(item.item_name);
      if (!expiryMs || !item.event_time_utc) continue;

      const putInMs = new Date(item.event_time_utc).getTime();
      if (Number.isNaN(putInMs)) continue;

      if (now - putInMs > expiryMs) {
        expired.push(item);
      }
    }

    return expired;
  }

  function handleExpiredNotifications(items) {
    const expiredItems = getExpiredInventoryItems(items);

    for (const item of expiredItems) {
      const key = `${String(item.item_name || '').toLowerCase()}-${String(item.id || '')}-${String(item.event_time_utc || '')}`;
      if (expiredToastShown.has(key)) continue;

      expiredToastShown.add(key);
      showToast(`${titleCase(item.item_name)} has expired.`);
    }
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
      tr.innerHTML = `<td colspan="4" class="empty-state-cell">No items to show</td>`;
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


  // Environment helpers
  function renderEnvironment(data) {
    tempValue.textContent = `${data.temperature ?? '--'} °C`;
    humidityValue.textContent = `${data.humidity ?? '--'} %`;
  }

  async function refreshEnvironment() {
    try {
      const res = await fetch(API_ENVIRONMENT);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderEnvironment(data || {});
      setStatus(envStatus, 'Updated');
    } catch (e) {
      console.error(e);
      renderEnvironment({});
      setStatus(envStatus, 'Something went wrong', true);
    }
  }

  // Inventory refresh
  async function refresh() {
    if (refreshing) return;
    refreshing = true;

    setStatus(statusText, 'Working...');
    refreshBtn.disabled = true;

    try {
      const [summaryRes, inventoryRes] = await Promise.all([
        fetch(API_SUMMARY),
        fetch(API_INVENTORY),
      ]);

      if (!summaryRes.ok) throw new Error(`HTTP ${summaryRes.status}`);
      if (!inventoryRes.ok) throw new Error(`HTTP ${inventoryRes.status}`);

      const summaryData = await summaryRes.json();
      const inventoryData = await inventoryRes.json();

      renderSummary(summaryData);
      handleExpiredNotifications(inventoryData);
      setStatus(statusText, 'Updated');
    } catch (e) {
      console.error(e);
      renderSummary([]);
      setStatus(statusText, 'Something went wrong', true);
    } finally {
      refreshBtn.disabled = false;
      refreshing = false;
    }
  }

  // Manual action payload
  function getPayload() {
    const item_name = (itemInput.value || '').trim();
    return {
      item_name,
      year: (manualYearInput.value || '').trim(),
      month: (manualMonthInput.value || '').trim(),
      day: (manualDayInput.value || '').trim(),
      hour: (manualHourInput.value || '').trim(),
    };
  }

  // Shared POST helper
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

  // Manual add action
  async function handleAdd() {
    const payload = getPayload();
    if (!payload.item_name) {
      setStatus(actionStatus, 'Something went wrong', true);
      return;
    }
    if (!payload.year || !payload.month) {
      setStatus(actionStatus, 'Something went wrong', true);
      return;
    }

    addBtn.disabled = true;
    removeBtn.disabled = true;
    setStatus(actionStatus, 'Working...');

    try {
      await postJson(API_ADD, payload);
      setStatus(actionStatus, 'Updated');
      await refresh();
    } catch (e) {
      console.error(e);
      setStatus(actionStatus, 'Something went wrong', true);
    } finally {
      addBtn.disabled = false;
      removeBtn.disabled = false;
    }
  }

  // Manual remove modal
  function showManualRemoveModal(show) {
    manualRemoveModal.classList.toggle('hidden', !show);
    if (!show) {
      manualRemoveSelection = [];
      manualRemoveItemsState = [];
      manualRemoveItems.innerHTML = '';
      manualRemoveTitle.textContent = '';
      manualRemoveSummary.textContent = '';
      setStatus(manualRemoveStatus, 'Ready');
    }
  }

  // Manual remove list
  function renderManualRemoveItems() {
    manualRemoveItems.innerHTML = '';
    for (const item of manualRemoveItemsState) {
      const row = document.createElement('label');
      row.className = 'collision-item-row manual-remove-row';
      const checked = manualRemoveSelection.includes(String(item.id));
      row.innerHTML = `
        <input type="checkbox" class="manualRemoveCheck" value="${escapeHtml(item.id)}" ${checked ? 'checked' : ''} />
        <div class="collision-item-meta">
          <span class="collision-item-name">${escapeHtml(item.item_name)} #${escapeHtml(item.sequence)}</span>
          <span class="collision-item-time">${escapeHtml(formatCaseTime(item.event_time_utc))}</span>
        </div>
      `;
      manualRemoveItems.appendChild(row);
    }
  }

  async function submitManualRemove(removeItemIds) {
    return postJson(API_REMOVE, { remove_item_ids: removeItemIds.map(Number) });
  }

  // Manual remove action
  async function handleRemove() {
    const payload = getPayload();
    if (!payload.item_name) {
      setStatus(actionStatus, 'Something went wrong', true);
      return;
    }

    addBtn.disabled = true;
    removeBtn.disabled = true;
    setStatus(actionStatus, 'Working...');

    try {
      const result = await postJson('/api/manual/remove/candidates', { item_name: payload.item_name });
      const items = Array.isArray(result.items) ? result.items : [];

      if (items.length === 0) {
        window.alert(`No in-fridge ${payload.item_name} found.`);
        setStatus(actionStatus, 'Ready');
      } else if (items.length === 1) {
        await submitManualRemove([items[0].id]);
        setStatus(actionStatus, 'Updated');
        await refresh();
      } else {
        manualRemoveItemsState = items.map((item, index) => ({
          ...item,
          sequence: index + 1,
        }));
        manualRemoveSelection = [String(manualRemoveItemsState[0].id)];
        manualRemoveTitle.textContent = 'Select one or more items to remove from the fridge.';
        manualRemoveSummary.textContent = `${titleCase(payload.item_name)} items currently in the fridge`;
        renderManualRemoveItems();
        showManualRemoveModal(true);
        setStatus(actionStatus, 'Ready');
      }
    } catch (e) {
      console.error(e);
      setStatus(actionStatus, 'Something went wrong', true);
    } finally {
      addBtn.disabled = false;
      removeBtn.disabled = false;
    }
  }

  manualRemoveItems.addEventListener('change', (e) => {
    const target = e.target;
    if (!(target instanceof HTMLInputElement) || !target.classList.contains('manualRemoveCheck')) return;

    const itemId = String(target.value);
    if (target.checked) {
      if (!manualRemoveSelection.includes(itemId)) manualRemoveSelection.push(itemId);
    } else {
      manualRemoveSelection = manualRemoveSelection.filter((id) => id !== itemId);
    }
    setStatus(manualRemoveStatus, 'Ready');
  });

  manualRemoveConfirmBtn.addEventListener('click', async () => {
    if (manualRemoveSelection.length === 0) {
      setStatus(manualRemoveStatus, 'Something went wrong', true);
      return;
    }

    manualRemoveConfirmBtn.disabled = true;
    manualRemoveCancelBtn.disabled = true;
    setStatus(manualRemoveStatus, 'Working...');
    try {
      await submitManualRemove(manualRemoveSelection);
      showManualRemoveModal(false);
      setStatus(actionStatus, 'Updated');
      await refresh();
    } catch (e) {
      setStatus(manualRemoveStatus, 'Something went wrong', true);
    } finally {
      manualRemoveConfirmBtn.disabled = false;
      manualRemoveCancelBtn.disabled = false;
    }
  });

  manualRemoveCancelBtn.addEventListener('click', () => showManualRemoveModal(false));

  // Collision helpers
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

  // Collision submit
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

  // Collision rendering
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
      statusEl.textContent = 'Ready';

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
          <button type="button" class="collisionIgnoreBtn collision-btn-secondary">Use Default</button>
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
        setStatus(statusEl, 'Ready');
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
        setStatus(statusEl, 'Working...');
        try {
          await submitCollisionAction(c.case_id, selected);
          removeCaseCard(c.case_id);
          await refresh();
        } catch (e) {
          setStatus(statusEl, 'Something went wrong', true);
        } finally {
          confirmBtn.disabled = false;
          ignoreBtn.disabled = false;
        }
      });

      ignoreBtn.addEventListener('click', async () => {
        confirmBtn.disabled = true;
        ignoreBtn.disabled = true;
        setStatus(statusEl, 'Working...');
        try {
          await submitCollisionAction(c.case_id, view.defaultIds);
          removeCaseCard(c.case_id);
          await refresh();
        } catch (e) {
          setStatus(statusEl, 'Something went wrong', true);
        } finally {
          confirmBtn.disabled = false;
          ignoreBtn.disabled = false;
        }
      });

      collisionCardList.appendChild(card);
    }

    showModal(true);
  }

  // Collision polling
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

  // Initial load
  refresh();
  refreshEnvironment();
  pollCollisions();
  if (window.__smartFridgeRefreshTimer) window.clearInterval(window.__smartFridgeRefreshTimer);
  if (window.__smartFridgeCollisionTimer) window.clearInterval(window.__smartFridgeCollisionTimer);
  if (window.__smartFridgeEnvironmentTimer) window.clearInterval(window.__smartFridgeEnvironmentTimer);
  window.__smartFridgeRefreshTimer = window.setInterval(refresh, 5000);
  window.__smartFridgeCollisionTimer = window.setInterval(pollCollisions, 5000);
  window.__smartFridgeEnvironmentTimer = window.setInterval(refreshEnvironment, 5000);
});
