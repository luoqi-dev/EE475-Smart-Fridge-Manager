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
  const collisionCaseSelect = document.getElementById('collisionCaseSelect');
  const collisionSummary = document.getElementById('collisionSummary');
  const collisionItems = document.getElementById('collisionItems');
  const collisionConfirmBtn = document.getElementById('collisionConfirmBtn');
  const collisionCancelBtn = document.getElementById('collisionCancelBtn'); // optional
  const collisionStatus = document.getElementById('collisionStatus');

  if (
    !table || !tbody || !refreshBtn || !statusText ||
    !itemInput || !addBtn || !removeBtn || !actionStatus ||
    !modal || !collisionCaseSelect || !collisionSummary || !collisionItems ||
    !collisionConfirmBtn || !collisionStatus
  ) {
    console.error('Required elements not found.');
    return;
  }

  let refreshing = false;

  let openCases = [];
  let currentCase = null;
  let modalOpen = false;
  let activeCaseId = null;

  let maxSelectable = 1;
  let suppressCheckboxHandler = false;

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
      currentCase = null;
      activeCaseId = null;
      maxSelectable = 1;
      collisionItems.innerHTML = '';
      collisionSummary.textContent = '';
      setStatus(collisionStatus, 'Idle');
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
      setStatus(actionStatus, `Removed "${payload.item_name}" (track_id=${r.track_id})`);
      await refresh();
    } catch (e) {
      console.error(e);
      setStatus(actionStatus, `Error: ${e.message}`, true);
    } finally {
      addBtn.disabled = false;
      removeBtn.disabled = false;
    }
  }

  function renderCasePicker() {
    const prev = activeCaseId || collisionCaseSelect.value;

    collisionCaseSelect.innerHTML = '';
    for (const c of openCases) {
      const opt = document.createElement('option');
      opt.value = c.case_id;
      opt.textContent = c.item_name; // item_name only
      collisionCaseSelect.appendChild(opt);
    }

    if (prev && openCases.some(x => x.case_id === prev)) {
      collisionCaseSelect.value = prev;
      activeCaseId = prev;
    } else if (openCases[0]) {
      collisionCaseSelect.value = openCases[0].case_id;
      activeCaseId = openCases[0].case_id;
    }
  }

  function getSelectedIds() {
    const checks = collisionItems.querySelectorAll('.collisionCheck');
    const ids = [];
    for (const c of checks) {
      if (c.checked) ids.push(Number(c.value));
    }
    return ids;
  }

  function renderCollisionCase(c) {
    currentCase = c;
    activeCaseId = c.case_id;

    const sameNameCount = openCases.filter(x => x.item_name === c.item_name).length;
    maxSelectable = Math.max(1, sameNameCount);

    collisionSummary.textContent =
      `Which ${c.item_name} do you want to remove? (${maxSelectable} item${maxSelectable > 1 ? 's' : ''} need to be removed)`;

    collisionItems.innerHTML = '';
    const defaultIds = new Set((c.default_remove_ids || []).map(String));
    const items = Array.isArray(c.items) ? c.items : [];

    suppressCheckboxHandler = true;
    for (const it of items) {
      const checked = defaultIds.has(String(it.id));
      const time = formatUtcToLocal(it.event_time_utc);

      const row = document.createElement('label');
      row.style.display = 'flex';
      row.style.gap = '10px';
      row.style.padding = '6px 0';
      row.innerHTML = `
        <input type="checkbox" class="collisionCheck" value="${escapeHtml(it.id)}" ${checked ? 'checked' : ''} />
        <span>${escapeHtml(time)}</span>
      `;
      collisionItems.appendChild(row);
    }
    suppressCheckboxHandler = false;

    setStatus(collisionStatus, 'Idle');
  }

  collisionItems.addEventListener('change', (e) => {
    if (suppressCheckboxHandler) return;
    const t = e.target;
    if (!(t instanceof HTMLInputElement) || !t.classList.contains('collisionCheck')) return;

    const selected = getSelectedIds();
    if (selected.length > maxSelectable) {
      t.checked = false;
      setStatus(collisionStatus, `You can select at most ${maxSelectable} item${maxSelectable > 1 ? 's' : ''}.`, true);
    } else {
      setStatus(collisionStatus, 'Idle');
    }
  });

  collisionCaseSelect.addEventListener('change', () => {
    const id = collisionCaseSelect.value;
    activeCaseId = id;
    const c = openCases.find(x => x.case_id === id);
    if (c) renderCollisionCase(c);
  });

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
      renderCasePicker();

      if (modalOpen) {
        if (!activeCaseId || !openCases.some(x => x.case_id === activeCaseId)) {
          renderCollisionCase(openCases[0]);
        }
        return;
      }

      renderCollisionCase(openCases[0]);
      showModal(true);
    } catch (_) {}
  }

  async function confirmCollision() {
    if (!currentCase) return;

    const ids = getSelectedIds();
    if (ids.length === 0) {
      setStatus(collisionStatus, 'Please select an item.', true);
      return;
    }
    if (ids.length !== maxSelectable) {
      setStatus(
        collisionStatus,
        `Please select exactly ${maxSelectable} item${maxSelectable > 1 ? 's' : ''}.`,
        true
      );
      return;
    }

    collisionConfirmBtn.disabled = true;
    setStatus(collisionStatus, 'Submitting...');

    try {
      const res = await fetch(`/api/collisions/${encodeURIComponent(currentCase.case_id)}/actions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ remove_item_ids: ids }),
      });

      if (res.ok) {
        setStatus(collisionStatus, 'Submitted');
        showModal(false);
        await refresh();
      } else {
        setStatus(collisionStatus, `Error: HTTP ${res.status}`, true);
      }
    } catch (e) {
      setStatus(collisionStatus, `Error: ${e.message}`, true);
    } finally {
      collisionConfirmBtn.disabled = false;
    }
  }

  refreshBtn.addEventListener('click', refresh);
  addBtn.addEventListener('click', handleAdd);
  removeBtn.addEventListener('click', handleRemove);

  itemInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') handleAdd();
  });

  collisionConfirmBtn.addEventListener('click', confirmCollision);
  if (collisionCancelBtn) collisionCancelBtn.addEventListener('click', () => showModal(false));

  refresh();
  setInterval(refresh, 5000);
  setInterval(pollCollisions, 1000);
});
