const $ = id => document.getElementById(id);
const ui = {
  server: $('server'),
  code: $('code'),
  connect: $('connect'),
  check: $('check'),
  facebook: $('facebook'),
  disconnect: $('disconnect'),
  status: $('status'),
  message: $('message')
};

function normalizeServer(v) {
  let s = String(v || '').trim();
  if (!s) return '';
  if (!/^https?:\/\//i.test(s)) {
    if (s.startsWith('localhost') || s.startsWith('127.0.0.1') || /^192\.168\./.test(s) || /^10\./.test(s)) {
      s = 'http://' + s;
    } else {
      s = 'https://' + s;
    }
  }
  try {
    const u = new URL(s);
    return u.origin;
  } catch (e) {
    return s.replace(/\/+$/, '');
  }
}

async function config() {
  return await chrome.storage.local.get(['serverOrigin', 'deviceId', 'token', 'customerId', 'repairRequired', 'lastError']);
}

function msg(t, isError = false) {
  if (!ui.message) return;
  ui.message.textContent = t || '';
  if (isError) {
    ui.message.style.color = 'var(--red, #fb7185)';
    ui.message.style.borderLeftColor = 'var(--red, #fb7185)';
  } else {
    ui.message.style.color = '';
    ui.message.style.borderLeftColor = '';
  }
}

async function refresh() {
  const c = await config();
  if (c.serverOrigin && !ui.server.value) {
    ui.server.value = c.serverOrigin;
  }
  if (!ui.server.value) {
    try {
      const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
      if (tabs && tabs[0] && tabs[0].url && /^https?:\/\//i.test(tabs[0].url)) {
        const origin = new URL(tabs[0].url).origin;
        if (!origin.includes('facebook.com')) {
          ui.server.value = origin;
        }
      }
    } catch (e) {}
  }

  if (!c.deviceId || !c.token || c.repairRequired) {
    ui.status.className = 'status offline';
    const subText = c.repairRequired
      ? (c.lastError || 'Phiên đã hết hạn. Hãy tạo mã mới trên website rồi liên kết lại.')
      : 'Tạo mã trên website rồi nhập vào đây';
    ui.status.innerHTML = `<span></span><div><strong>${c.repairRequired ? 'Cần kết nối lại' : 'Chưa liên kết'}</strong><small>${subText}</small></div>`;
    return;
  }

  try {
    const r = await chrome.runtime.sendMessage({ type: 'GET_STATUS' });
    if (r?.ok) {
      if (r.isOnline) {
        ui.status.className = 'status online';
        ui.status.innerHTML = `<span></span><div><strong>Connector Online • Sẵn sàng</strong><small>Facebook: ${r.facebookLoggedIn ? 'Đã đăng nhập ✓' : 'Chưa đăng nhập ⚠️'} • ${r.deviceName || 'Chrome'}</small></div>`;
      } else {
        ui.status.className = 'status offline';
        ui.status.innerHTML = `<span></span><div><strong>Đã liên kết (Ngoại tuyến)</strong><small>Bấm KIỂM TRA NGAY để kích hoạt kết nối</small></div>`;
      }
    } else {
      throw new Error(r?.error || 'Không kiểm tra được');
    }
  } catch (e) {
    ui.status.className = 'status offline';
    ui.status.innerHTML = '<span></span><div><strong>Đã liên kết nhưng chưa kiểm tra được</strong><small>Mở website hoặc bấm KIỂM TRA NGAY</small></div>';
  }
}

ui.connect.onclick = async () => {
  const server = normalizeServer(ui.server.value);
  const code = ui.code.value.trim().toUpperCase();
  if (!server) {
    msg('Vui lòng nhập URL website.', true);
    return;
  }
  if (!code) {
    msg('Vui lòng nhập mã liên kết 8 ký tự.', true);
    return;
  }
  ui.connect.disabled = true;
  msg('Đang kết nối...');
  try {
    const res = await chrome.runtime.sendMessage({
      type: 'PAIR_FROM_POPUP',
      server: server,
      code: code
    });
    if (!res || !res.ok) {
      throw new Error(res?.error || 'Liên kết thất bại. Vui lòng kiểm tra mã liên kết.');
    }
    ui.code.value = '';
    msg('✓ Đã liên kết thành công!');
    await refresh();
  } catch (e) {
    msg(e.message || String(e), true);
  } finally {
    ui.connect.disabled = false;
  }
};

ui.check.onclick = async () => {
  msg('Đang kiểm tra kết nối...');
  ui.check.disabled = true;
  try {
    const r = await chrome.runtime.sendMessage({ type: 'FORCE_POLL' });
    if (r?.ok) {
      msg('✓ Kết nối hoạt động tốt. Facebook: ' + (r.facebookLoggedIn ? 'Đã đăng nhập' : 'Chưa đăng nhập'));
    } else {
      msg(r?.error || 'Không kết nối được tới máy chủ.', true);
    }
  } catch (e) {
    msg(String(e?.message || e), true);
  } finally {
    ui.check.disabled = false;
    await refresh();
  }
};

ui.facebook.onclick = () => chrome.tabs.create({ url: 'https://www.facebook.com/', active: true });

ui.disconnect.onclick = async () => {
  if (!confirm('Bạn có chắc muốn ngắt liên kết trên trình duyệt này?')) return;
  await chrome.storage.local.remove(['deviceId', 'token', 'customerId', 'repairRequired', 'lastError']);
  msg('Đã ngắt liên kết trên Chrome này.');
  await refresh();
};

refresh();