const SLOT_COUNT = 3;
const POLL_INTERVAL_MS = 5000;
const PAGE_TITLES = {
  overview: 'Dashboard Overview',
  monitoring: 'Monitoring Slot',
  assistant: 'AI Assistant',
  history: 'Riwayat Parkir'
};
const $ = (id) => document.getElementById(id);
let systemStatus = null;
let apiConnected = false;
let demoActive = false;
let demoSlots = [null, null, null];
let historyRequestId = 0;
let searchTimer = null;

function showToast(message, isError) {
  let toast = document.querySelector('.toast');
  if (!toast) {
    toast = document.createElement('div');
    toast.className = 'toast';
    toast.setAttribute('role', 'status');
    document.body.append(toast);
  }
  toast.textContent = message;
  toast.classList.toggle('error', Boolean(isError));
  window.clearTimeout(showToast.timeout);
  showToast.timeout = window.setTimeout(() => toast.remove(), 4200);
}

async function apiRequest(path, options) {
  const response = await fetch(path, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(body.error || `Permintaan gagal (${response.status}).`);
  }
  return body;
}

function currentPage() {
  const page = new URLSearchParams(window.location.search).get('page');
  return Object.prototype.hasOwnProperty.call(PAGE_TITLES, page) ? page : 'overview';
}

function navigateTo(page, replace) {
  if (!Object.prototype.hasOwnProperty.call(PAGE_TITLES, page)) return;
  const url = new URL(window.location.href);
  url.searchParams.set('page', page);
  window.history[replace ? 'replaceState' : 'pushState']({ page }, '', url);
  renderPage(page);
}

function renderPage(page) {
  document.querySelectorAll('.page-view').forEach((view) => {
    const active = view.dataset.page === page;
    view.hidden = !active;
    view.classList.toggle('active', active);
  });
  document.querySelectorAll('[data-page-link]').forEach((link) => {
    const active = link.dataset.pageLink === page;
    link.classList.toggle('active', active && link.classList.contains('nav-link'));
    if (link.classList.contains('nav-link')) {
      if (active) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
  });
  $('pageTitle').textContent = PAGE_TITLES[page];
  $('sidebar').classList.remove('open');
  $('menuToggle').setAttribute('aria-expanded', 'false');
  if (page === 'history') loadHistory();
}

function displaySlots() {
  if (demoActive) {
    return demoSlots.map((occupied, index) => ({
      slot: index + 1,
      status: occupied === null ? null : occupied ? 'occupied' : 'empty',
      distance_cm: null,
      updated_at: null
    }));
  }
  return systemStatus ? systemStatus.slots : Array.from({ length: SLOT_COUNT }, (_, index) => ({
    slot: index + 1,
    status: null,
    distance_cm: null,
    updated_at: null
  }));
}

function makeSlotCard(slot) {
  const occupied = slot.status === 'occupied';
  const empty = slot.status === 'empty';
  const stateClass = occupied ? 'occupied' : empty ? 'available' : 'unknown';
  const stateLabel = occupied ? 'TERISI' : empty ? 'KOSONG' : 'BELUM ADA DATA';
  const statusLabel = occupied ? 'OCCUPIED' : empty ? 'AVAILABLE' : 'NO DATA';
  const card = document.createElement('article');
  card.className = `parking-slot ${stateClass}`;
  card.setAttribute('aria-label', `Slot ${slot.slot}: ${stateLabel.toLowerCase()}`);

  const heading = document.createElement('div');
  heading.className = 'slot-number';
  const number = document.createElement('span');
  number.textContent = `SLOT ${String(slot.slot).padStart(2, '0')}`;
  const state = document.createElement('span');
  state.className = 'slot-state';
  state.textContent = stateLabel;
  heading.append(number, state);

  const car = document.createElement('div');
  car.className = 'car-illustration';
  car.setAttribute('aria-hidden', 'true');
  ['car-top', 'car-body', 'wheel left', 'wheel right'].forEach((className) => {
    const part = document.createElement('span');
    part.className = className;
    car.append(part);
  });

  const bottom = document.createElement('div');
  bottom.className = 'slot-bottom';
  const sensor = document.createElement('span');
  sensor.textContent = 'HC-SR04';
  const status = document.createElement('strong');
  status.textContent = statusLabel;
  bottom.append(sensor, status);

  const meta = document.createElement('div');
  meta.className = 'slot-meta';
  const distance = document.createElement('span');
  distance.className = 'slot-distance';
  distance.textContent = slot.distance_cm === null || slot.distance_cm === undefined
    ? 'Jarak —'
    : `Jarak ${slot.distance_cm} cm`;
  const updated = document.createElement('span');
  updated.textContent = `Update ${formatDateTime(slot.updated_at)}`;
  meta.append(distance, updated);
  card.append(heading, car, bottom, meta);
  return card;
}

function renderSlotCards() {
  const slots = displaySlots();
  ['overviewSlots', 'monitoringSlots'].forEach((id) => {
    const grid = $(id);
    grid.replaceChildren(...slots.map(makeSlotCard));
  });
}

function formatDateTime(value) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleString('id-ID', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderSummary() {
  const slots = displaySlots();
  const occupied = slots.filter((slot) => slot.status === 'occupied').length;
  const available = slots.filter((slot) => slot.status === 'empty').length;
  const unknown = SLOT_COUNT - occupied - available;
  const complete = unknown === 0;

  $('availableCount').textContent = complete ? String(available).padStart(2, '0') : '—';
  $('occupiedCount').textContent = String(occupied).padStart(2, '0');
  $('availableCaption').textContent = complete ? 'Dikonfirmasi sensor' : `${unknown} slot belum diketahui`;
  $('occupiedCaption').textContent = unknown ? `${occupied} terkonfirmasi · ${unknown} belum diketahui` : 'Dikonfirmasi sensor';
  $('capacityPercent').innerHTML = complete
    ? `${Math.round((occupied / SLOT_COUNT) * 100)}<span class="value-unit">%</span>`
    : '—<span class="value-unit">%</span>';
  $('capacityBar').style.width = complete ? `${(occupied / SLOT_COUNT) * 100}%` : '0%';

  if (!complete) {
    $('insightTitle').textContent = 'Menunggu data parkir';
    $('insightText').textContent = `${occupied} slot terkonfirmasi terisi, ${available} terkonfirmasi kosong, dan ${unknown} slot belum melaporkan status.`;
  } else if (occupied === SLOT_COUNT) {
    $('insightTitle').textContent = 'Parkiran sedang penuh';
    $('insightText').textContent = 'Ketiga slot terkonfirmasi terisi. Slot tersedia kembali setelah sensor melaporkan kendaraan keluar.';
  } else if (occupied === 0) {
    $('insightTitle').textContent = 'Semua slot terkonfirmasi kosong';
    $('insightText').textContent = 'Sensor melaporkan ketiga slot kosong dan siap digunakan.';
  } else {
    $('insightTitle').textContent = 'Masih tersedia tempat parkir';
    $('insightText').textContent = `${available} slot terkonfirmasi kosong dan ${occupied} slot terkonfirmasi terisi.`;
  }
  $('dataModeBadge').className = `demo-tag ${demoActive ? '' : apiConnected ? 'live' : 'waiting'}`;
  const badgeLabel = document.createElement('span');
  badgeLabel.textContent = '●';
  $('dataModeBadge').replaceChildren(
    badgeLabel,
    document.createTextNode(demoActive
      ? ' DEMO · DATA LOKAL'
      : apiConnected
        ? ' DATA BACKEND'
        : systemStatus
          ? ' API OFFLINE · DATA TERAKHIR'
          : ' MENUNGGU DATA SENSOR')
  );
  $('demoToggleBtn').disabled = apiConnected;
  $('demoToggleBtn').textContent = apiConnected ? 'DEMO tidak tersedia saat live' : demoActive ? 'Matikan DEMO' : 'Aktifkan DEMO';
  renderSlotCards();
  updateMonitoringDetails();
}

function updateConnectionStatus() {
  const chip = $('connectionChip');
  chip.classList.toggle('online', apiConnected);
  chip.classList.toggle('offline', !apiConnected);
  $('connectionText').textContent = apiConnected ? 'API TERHUBUNG' : 'API OFFLINE';
  const device = systemStatus && systemStatus.device;
  const online = apiConnected && device && device.status === 'online';
  $('sideDeviceStatus').textContent = online ? 'ESP32 online' : 'Menunggu data perangkat';
  $('sideDevicePulse').classList.toggle('online', Boolean(online));
  $('sideDevicePulse').classList.toggle('offline', Boolean(apiConnected && device && device.status === 'offline'));
  const espStatus = $('espStatus');
  espStatus.className = `device-status ${online ? 'online' : apiConnected ? 'offline' : 'neutral'}`;
  espStatus.innerHTML = `<i></i>${online ? 'Online' : apiConnected ? 'Offline' : 'Tidak diketahui'}`;
  $('deviceLastSeen').textContent = device && device.last_seen
    ? `Heartbeat ${formatDateTime(device.last_seen)}`
    : 'Belum ada heartbeat';
  $('ultrasonicStatus').textContent = systemStatus && systemStatus.summary.known_slots > 0
    ? `${systemStatus.summary.known_slots}/3 slot melapor`
    : 'Belum ada data';
  $('ldrReading').textContent = systemStatus && systemStatus.sensors.ldr_lux !== null
    ? `${systemStatus.sensors.ldr_lux} lux`
    : 'Intensitas cahaya belum tersedia';
  const sensors = systemStatus && systemStatus.sensors;
  $('irReading').textContent = sensors && (sensors.ir_entry !== null || sensors.ir_exit !== null)
    ? `Masuk: ${formatIrStatus(sensors.ir_entry)} · Keluar: ${formatIrStatus(sensors.ir_exit)}`
    : 'Data masuk/keluar belum tersedia';
  $('assistantDataStatus').textContent = apiConnected ? 'Terhubung ke data sistem' : 'Backend tidak terhubung';
  $('assistantContext').textContent = apiConnected && systemStatus
    ? `ESP32 ${online ? 'online' : 'offline'} · ${systemStatus.summary.known_slots}/3 status slot tersedia · Data DEMO tidak dikirim ke assistant`
    : 'Assistant memerlukan backend aktif; data DEMO lokal tidak digunakan untuk jawaban.';
  $('aiDisclaimer').textContent = apiConnected
    ? 'Jawaban memakai status dan riwayat database. Jika model AI belum dikonfigurasi, backend memberi jawaban faktual berbasis data.'
    : 'Tidak ada jawaban sensor yang dibuat tanpa koneksi backend. Kunci model AI disimpan di backend, bukan di browser.';
}

function formatSignal(value) {
  if (value === null || value === undefined) return 'Belum tersedia';
  return value ? 'Aktif' : 'Tidak aktif';
}

function formatIrStatus(value) {
  if (value === null || value === undefined) return 'Belum ada data';
  return value ? 'Deteksi aktif' : 'Standby';
}

function formatIrActivity(value) {
  if (value === null || value === undefined) return 'Belum ada telemetry IR.';
  return value ? 'Kendaraan terdeteksi pada pembacaan terakhir.' : 'Tidak ada deteksi pada pembacaan terakhir.';
}

function updateMonitoringDetails() {
  const sensors = systemStatus && systemStatus.sensors;
  $('monitoringUpdated').textContent = systemStatus && systemStatus.device.last_seen
    ? `Terakhir diterima ${formatDateTime(systemStatus.device.last_seen)}`
    : demoActive ? 'Pratinjau DEMO · bukan data sensor' : 'Menunggu data sensor';
  $('monitoringLdr').textContent = sensors && sensors.ldr_lux !== null ? `${sensors.ldr_lux} lux` : 'Belum tersedia';
  $('monitoringIrEntry').textContent = sensors ? formatIrStatus(sensors.ir_entry) : 'Belum tersedia';
  $('monitoringIrExit').textContent = sensors ? formatIrStatus(sensors.ir_exit) : 'Belum tersedia';
  $('monitoringIrEntryActivity').textContent = sensors ? formatIrActivity(sensors.ir_entry) : 'Belum ada telemetry IR.';
  $('monitoringIrExitActivity').textContent = sensors ? formatIrActivity(sensors.ir_exit) : 'Belum ada telemetry IR.';
  $('monitoringIrEntryUpdated').textContent = `Waktu pembaruan: ${formatDateTime(sensors && sensors.ir_entry_updated_at)}`;
  $('monitoringIrExitUpdated').textContent = `Waktu pembaruan: ${formatDateTime(sensors && sensors.ir_exit_updated_at)}`;
  const hasIrData = sensors && (sensors.ir_entry_updated_at || sensors.ir_exit_updated_at);
  $('irActivityStatus').textContent = hasIrData
    ? apiConnected ? 'Data IR diterima' : 'API offline · data terakhir'
    : 'Menunggu telemetry IR';
}

function renderStatus() {
  updateConnectionStatus();
  renderSummary();
}

async function refreshStatus() {
  try {
    systemStatus = await apiRequest('/api/status');
    apiConnected = true;
    if (demoActive) {
      demoActive = false;
      demoSlots = [null, null, null];
    }
  } catch (error) {
    apiConnected = false;
    if (!refreshStatus.hasReportedError) {
      $('assistantContext').textContent = `Backend belum dapat dihubungi: ${error.message}`;
      refreshStatus.hasReportedError = true;
    }
  }
  renderStatus();
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const minutes = Math.floor(seconds / 60);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const remainingMinutes = minutes % 60;
  if (days) return `${days} hari ${hours} jam`;
  if (hours) return `${hours} jam ${remainingMinutes} mnt`;
  return `${remainingMinutes} mnt`;
}

function createCell(text) {
  const cell = document.createElement('td');
  cell.textContent = text;
  return cell;
}

function renderHistory(records, total) {
  const body = $('historyBody');
  body.replaceChildren();
  $('historyCount').textContent = `${total} catatan`;
  if (!records.length) {
    const row = document.createElement('tr');
    row.className = 'empty-history';
    const cell = createCell('Belum ada riwayat yang sesuai dengan filter.');
    cell.colSpan = 6;
    row.append(cell);
    body.append(row);
    return;
  }
  records.forEach((record, index) => {
    const row = document.createElement('tr');
    row.append(createCell(String(index + 1)));
    row.append(createCell(`Slot ${record.slot}`));
    row.append(createCell(formatDateTime(record.entered_at)));
    row.append(createCell(formatDateTime(record.exited_at)));
    const statusCell = document.createElement('td');
    const status = document.createElement('span');
    status.className = `table-status ${record.exited_at ? 'available' : 'occupied'}`;
    status.textContent = record.exited_at ? 'Terisi → Kosong' : 'Kendaraan masuk · masih parkir';
    statusCell.append(status);
    row.append(statusCell, createCell(formatDuration(record.duration_seconds)));
    body.append(row);
  });
}

async function loadHistory() {
  const requestId = ++historyRequestId;
  const params = new URLSearchParams();
  const search = $('historySearch').value.trim();
  const slot = $('slotFilter').value;
  const date = $('dateFilter').value;
  if (search) params.set('q', search);
  if (slot) params.set('slot', slot);
  if (date) {
    params.set('from', date);
    params.set('to', date);
  }
  $('historyNote').textContent = apiConnected
    ? 'Memuat riwayat persisten dari database...'
    : 'Backend belum terhubung; riwayat tersimpan tersedia setelah API aktif.';
  try {
    const result = await apiRequest(`/api/history?${params.toString()}`);
    if (requestId !== historyRequestId) return;
    renderHistory(result.records, result.total);
    $('historyNote').textContent = `${result.total} catatan dari database · waktu disimpan backend · durasi tersedia setelah kendaraan keluar.`;
  } catch (error) {
    if (requestId !== historyRequestId) return;
    renderHistory([], 0);
    $('historyNote').textContent = `Riwayat tidak dapat dimuat: ${error.message}`;
  }
}

function addChatMessage(text, sender, detail) {
  const chatWindow = $('chatWindow');
  const row = document.createElement('div');
  row.className = `chat-message ${sender === 'user' ? 'user-message' : 'bot-message'}`;
  const avatar = document.createElement('div');
  avatar.className = 'chat-avatar';
  avatar.textContent = sender === 'user' ? 'K' : '✳';
  const content = document.createElement('div');
  content.className = 'message-content';
  const name = document.createElement('strong');
  name.textContent = sender === 'user' ? 'Kamu' : 'SmartPark Assistant';
  const paragraph = document.createElement('p');
  paragraph.textContent = text;
  const small = document.createElement('small');
  small.textContent = detail || (sender === 'user' ? 'Pesan pengguna' : 'Jawaban berdasarkan backend');
  content.append(name, paragraph, small);
  row.append(avatar, content);
  chatWindow.append(row);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return row;
}

async function sendQuestion(question) {
  const clean = question.trim();
  if (!clean) return;
  addChatMessage(clean, 'user');
  const submitButton = $('chatForm').querySelector('button');
  submitButton.disabled = true;
  const pending = addChatMessage('Memeriksa data parkir yang tersimpan...', 'assistant', 'Menghubungi backend');
  try {
    const result = await apiRequest('/api/assistant', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: clean })
    });
    pending.querySelector('p').textContent = result.answer;
    pending.querySelector('small').textContent = result.source === 'model'
      ? 'Model AI · konteks data database'
      : 'Jawaban faktual · data database';
  } catch (error) {
    pending.querySelector('p').textContent = `Tidak dapat menjawab karena backend/data tidak tersedia: ${error.message}`;
    pending.querySelector('small').textContent = 'Jawaban tidak dibuat dari simulasi';
  } finally {
    submitButton.disabled = false;
    $('chatInput').focus();
  }
}

function toggleDemo() {
  if (apiConnected) return;
  demoActive = !demoActive;
  if (demoActive && demoSlots.every((slot) => slot === null)) demoSlots = [false, false, false];
  $('demoToggleBtn').textContent = demoActive ? 'Matikan DEMO' : 'Aktifkan DEMO';
  $('randomBtn').disabled = !demoActive;
  $('resetBtn').disabled = !demoActive;
  renderStatus();
}

function initialize() {
  $('currentDate').textContent = new Date().toLocaleDateString('id-ID', {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric'
  });
  document.querySelectorAll('[data-page-link]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      navigateTo(link.dataset.pageLink);
    });
  });
  window.addEventListener('popstate', () => renderPage(currentPage()));
  $('menuToggle').addEventListener('click', () => {
    const open = $('sidebar').classList.toggle('open');
    $('menuToggle').setAttribute('aria-expanded', String(open));
  });
  $('chatForm').addEventListener('submit', (event) => {
    event.preventDefault();
    const input = $('chatInput');
    sendQuestion(input.value);
    input.value = '';
  });
  document.querySelectorAll('.suggestion-btn').forEach((button) => {
    button.addEventListener('click', () => sendQuestion(button.dataset.question));
  });
  $('demoToggleBtn').addEventListener('click', toggleDemo);
  $('randomBtn').addEventListener('click', () => {
    if (!demoActive) return;
    demoSlots = demoSlots.map(() => Math.random() > 0.5);
    renderStatus();
  });
  $('resetBtn').addEventListener('click', () => {
    if (!demoActive) return;
    demoSlots = [null, null, null];
    renderStatus();
  });
  ['historySearch', 'slotFilter', 'dateFilter'].forEach((id) => {
    $(id).addEventListener(id === 'historySearch' ? 'input' : 'change', () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(loadHistory, 200);
    });
  });
  $('clearFilters').addEventListener('click', () => {
    $('historySearch').value = '';
    $('slotFilter').value = '';
    $('dateFilter').value = '';
    loadHistory();
  });
  navigateTo(currentPage(), true);
  renderStatus();
  refreshStatus();
  window.setInterval(refreshStatus, POLL_INTERVAL_MS);
}

initialize();
