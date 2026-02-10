let inventory = JSON.parse(localStorage.getItem('inventory') || '[]');

const tbody = document.querySelector('#inventoryTable tbody');
const addBtn = document.getElementById('addBtn');
const removeBtn = document.getElementById('removeBtn');

function saveInventory() {
  localStorage.setItem('inventory', JSON.stringify(inventory));
}

function formatDuration(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, '0');
  const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, '0');
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${hours}:${minutes}:${seconds}`;
}

function renderInventory() {
  tbody.innerHTML = '';
  const now = Date.now();

  inventory.forEach(item => {
    const tr = document.createElement('tr');
    const duration = formatDuration(now - item.inTime);

    tr.innerHTML = `
      <td>${item.id}</td>
      <td>${item.name}</td>
      <td>${duration}</td>
    `;
    tbody.appendChild(tr);
  });
}

function addItem() {
  const id = 'ITEM-' + Math.floor(Math.random() * 100000);
  const names = ['Apple', 'Banana', 'Milk', 'Meat', 'Canned Food'];

  inventory.push({
    id,
    name: names[Math.floor(Math.random() * names.length)],
    inTime: Date.now()
  });

  saveInventory();
  renderInventory();
}

function removeItem() {
  if (inventory.length === 0) {
    alert('No items in inventory');
    return;
  }
  inventory.pop();
  saveInventory();
  renderInventory();
}

addBtn.addEventListener('click', addItem);
removeBtn.addEventListener('click', removeItem);

renderInventory();
setInterval(renderInventory, 1000);
