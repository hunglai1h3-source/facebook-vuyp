(() => {
  const root = document.querySelector('[data-campaign-monitor]');
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  const message = root.querySelector('[data-campaign-message]');
  const worker = root.querySelector('[data-worker-status]');
  const progressCount = root.querySelector('[data-progress-count]');
  const progressBar = root.querySelector('[data-progress-bar]');
  const successCount = root.querySelector('[data-success-count]');
  const errorCount = root.querySelector('[data-error-count]');
  const badge = root.querySelector('.section-header .badge');
  const launchForm = root.querySelector('[data-campaign-launch]');
  const scheduledLocal = launchForm?.querySelector('[data-scheduled-local]');
  const scheduledUtc = launchForm?.querySelector('[data-scheduled-utc]');
  let stopped = false;

  const labels = {
    waiting: 'SẴN SÀNG', draft: 'BẢN NHÁP', scheduled: 'ĐÃ HẸN LỊCH',
    queued: 'ĐANG CHỜ', running: 'ĐANG CHẠY', paused: 'TẠM DỪNG',
    completed: 'HOÀN THÀNH', partial_failed: 'HOÀN THÀNH MỘT PHẦN',
    failed: 'CÓ LỖI', cancelled: 'ĐÃ HỦY', stopped: 'ĐÃ DỪNG',
    worker_offline: 'WORKER OFFLINE'
  };

  function render(data) {
    const total = Math.max(0, Number(data.total) || 0);
    const processed = Math.max(0, Number(data.processed) || 0);
    const percent = total ? Math.min(100, Math.round(processed * 100 / total)) : 0;
    const state = data.display_status || data.status || 'waiting';
    if (message) message.textContent = data.message || 'Chưa có tác vụ nào đang chạy.';
    if (worker) {
      if (Array.isArray(data.engine_workers) && data.engine_workers.length) {
        const onlineCount = data.engine_workers.filter((item) => item.online).length;
        worker.textContent = `${onlineCount}/${data.engine_workers.length} worker online`;
      } else {
        worker.textContent = data.agent_online
          ? `Worker online${data.agent_device?.worker_state ? ` • ${data.agent_device.worker_state}` : ''}`
          : 'Worker offline • job sẽ không tự chạy lại';
      }
    }
    if (progressCount) progressCount.textContent = `${processed}/${total}`;
    if (progressBar) progressBar.style.width = `${percent}%`;
    if (successCount) successCount.textContent = Number(data.success) || 0;
    if (errorCount) errorCount.textContent = Number(data.errors) || 0;
    if (badge) {
      badge.textContent = labels[state] || String(state).toUpperCase();
      badge.className = `badge ${['failed', 'worker_offline'].includes(state) ? 'red' : data.running ? 'blue' : 'muted'}`;
    }
  }

  async function refresh() {
    if (stopped || document.visibilityState === 'hidden') return;
    try {
      const response = await fetch(statusUrl, {headers: {'Accept': 'application/json'}});
      if (response.status === 401) {
        stopped = true;
        return;
      }
      if (!response.ok) throw new Error(`Campaign status ${response.status}`);
      render(await response.json());
    } catch (error) {
      if (worker) worker.textContent = 'Không thể cập nhật trạng thái server';
      console.warn('campaign status', error);
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refresh();
  });
  launchForm?.addEventListener('submit', (event) => {
    const action = event.submitter?.value || 'run';
    scheduledLocal?.setCustomValidity('');
    if (action === 'schedule' && !scheduledLocal?.value) {
      event.preventDefault();
      scheduledLocal?.setCustomValidity('Vui lòng chọn ngày và giờ hẹn chạy.');
      scheduledLocal?.reportValidity();
      return;
    }
    if (scheduledUtc) {
      scheduledUtc.value = scheduledLocal?.value
        ? new Date(scheduledLocal.value).toISOString()
        : '';
    }
  });
  refresh();
  window.setInterval(refresh, 3000);
})();
