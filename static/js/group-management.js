(() => {
  const root = document.querySelector('[data-groups-ui]');
  if (!root) return;
  const rows = [...root.querySelectorAll('[data-group-row]')];
  const selectAll = root.querySelector('[data-select-all]');
  const bulkAccount = root.querySelector('[data-bulk-account]');

  const selectedRows = () => rows.filter(row => row.querySelector('.group-select')?.checked);
  const markSelection = row => row.classList.toggle('is-selected', !!row.querySelector('.group-select')?.checked);
  rows.forEach(row => row.querySelector('.group-select')?.addEventListener('change', () => markSelection(row)));
  selectAll?.addEventListener('change', () => {
    rows.forEach(row => {
      row.querySelector('.group-select').checked = selectAll.checked;
      markSelection(row);
    });
  });

  root.querySelector('[data-bulk-assign]')?.addEventListener('click', () => {
    const targets = selectedRows();
    if (!targets.length) return window.showToast?.('Hãy chọn ít nhất một Group.', 'warning');
    if (!bulkAccount?.value) return window.showToast?.('Hãy chọn Facebook account.', 'warning');
    targets.forEach(row => { row.querySelector('[data-account-assignment]').value = bulkAccount.value; });
    window.showToast?.(`Đã cập nhật bản nháp cho ${targets.length} Group. Bấm Lưu phân bổ để xác nhận.`, 'info');
  });

  root.querySelector('[data-even-distribute]')?.addEventListener('click', () => {
    const accountIds = [...(bulkAccount?.options || [])].map(option => option.value).filter(Boolean);
    if (accountIds.length < 2) return window.showToast?.('Cần ít nhất 2 Facebook account để chia đều.', 'warning');
    rows.forEach((row, index) => {
      row.querySelector('[data-account-assignment]').value = accountIds[index % accountIds.length];
    });
    window.showToast?.(`Đã tạo bản nháp chia đều ${rows.length} Group. Bạn có thể chỉnh từng dòng trước khi lưu.`, 'info');
  });

  root.querySelector('[data-save-assignments]')?.addEventListener('click', () => {
    const items = rows.map(row => ({
      group_url: row.dataset.groupUrl,
      account_id: row.querySelector('[data-account-assignment]').value
    }));
    const form = document.getElementById('assignmentForm');
    form.elements.assignments.value = JSON.stringify(items);
    form.submit();
  });

  root.querySelector('[data-bulk-delete]')?.addEventListener('click', () => {
    const targets = selectedRows();
    if (!targets.length) return window.showToast?.('Hãy chọn Group cần xóa.', 'warning');
    if (!window.confirm(`Xóa ${targets.length} Group đã chọn?`)) return;
    const form = document.getElementById('bulkDeleteForm');
    form.replaceChildren();
    targets.forEach(row => {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.name = 'group_urls';
      input.value = row.dataset.groupUrl;
      form.appendChild(input);
    });
    form.submit();
  });

  const file = document.getElementById('groupFile');
  file?.addEventListener('change', () => {
    const label = file.closest('.file-picker')?.querySelector('span');
    if (label) label.textContent = file.files?.[0]?.name || 'Chọn TXT/CSV';
  });
})();
