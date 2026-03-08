document.addEventListener('DOMContentLoaded', function () {
  const API_SUMMARY = '/api/inventory/summary';
  const API_ADD = '/api/manual/add';
  const API_REMOVE = '/api/manual/remove';

  const table = document.getElementById('inventoryTable');
  const tbody = table?.querySelector('tbody');
  const refreshBtn = document.getElementById('refreshBtn');
  const statusText = document.getElementById('statusText');

  const itemInput = document.getElementById('itemInput');
  const addBtn = document.getElementById('addBtn');
  const removeBtn = document.getElementById('removeBtn');
  const actionStatus = document.getElementById('actionStatus');

  if (!table || !tbody || !refreshBtn || !statusText || !itemInput || !addBtn || !removeBtn || !actionStatus) {
    console.error('Required elements not found.');
    return;
  }

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

  function render(rows) {
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

  let refreshing = false;

  async function refresh() {
    if (refreshing) return;
    refreshing = true;

    setStatus(statusText, 'Loading...');
    refreshBtn.disabled = true;

    try {
      const res = await fetch(API_SUMMARY);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      render(data);
      setStatus(statusText, `Updated: ${new Date().toLocaleString()}`);
    } catch (e) {
      console.error(e);
      render([]);
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

  refreshBtn.addEventListener('click', refresh);
  addBtn.addEventListener('click', handleAdd);
  removeBtn.addEventListener('click', handleRemove);

  // Enter 键快速添加
  itemInput.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') handleAdd();
  });

  // 首次刷新 + 每 5 秒自动刷新
  refresh();
  setInterval(refresh, 5000);
});