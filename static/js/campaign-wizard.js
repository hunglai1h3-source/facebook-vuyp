/**
 * FB POST PRO - 6-Step Campaign Creation Wizard (Phase 12)
 */
document.addEventListener('DOMContentLoaded', () => {
  let currentStep = 1;
  const totalSteps = 6;

  // Elements
  const stepNodes = document.querySelectorAll('.wizard-step-node');
  const panels = document.querySelectorAll('.wizard-panel');

  // Step 1 Elements
  const inputCampaignName = document.getElementById('campaign_name');
  const inputPostContent = document.getElementById('postContent');
  const inputMinDelay = document.getElementById('min_delay');
  const inputMaxDelay = document.getElementById('max_delay');
  const selectTemplate = document.getElementById('selectTemplate');
  const btnLoadTemplate = document.getElementById('btnLoadTemplate');
  const btnSaveTemplate = document.getElementById('btnSaveTemplate');
  const btnSaveTemplateConfirm = document.getElementById('btnSaveTemplateConfirm');

  // Step 2 Elements (Accounts)
  const accountCheckboxes = document.querySelectorAll('.account-select-checkbox');
  const selectedAccountsCount = document.getElementById('selectedAccountsCount');

  // Step 3 Elements (Groups)
  const groupSearch = document.getElementById('wizardGroupSearch');
  const groupFilter = document.getElementById('wizardGroupFilter');
  const btnSelectAllGroups = document.getElementById('btnSelectAllGroups');
  const btnDeselectAllGroups = document.getElementById('btnDeselectAllGroups');
  const groupCheckboxes = document.querySelectorAll('.group-select-checkbox');
  const selectedGroupsCount = document.getElementById('selectedGroupsCount');

  // Step 4 Elements (Assignment Review)
  const btnEvenDistribute = document.getElementById('btnWizardEvenDistribute');
  const assignmentReviewList = document.getElementById('assignmentReviewList');
  const assignmentSummaryBadges = document.getElementById('assignmentSummaryBadges');

  // Step 5 Elements (Schedule)
  const scheduleOptionCards = document.querySelectorAll('.schedule-option-card');
  const scheduledLocalInput = document.getElementById('scheduledLocal');
  const scheduledUtcHidden = document.getElementById('scheduledUtcHidden');
  const campaignActionHidden = document.getElementById('campaignActionHidden');

  // Step 6 Elements (Preview & Confirm)
  const previewCampName = document.getElementById('previewCampName');
  const previewAccounts = document.getElementById('previewAccounts');
  const previewGroupsCount = document.getElementById('previewGroupsCount');
  const previewDelay = document.getElementById('previewDelay');
  const previewMode = document.getElementById('previewMode');
  const previewContent = document.getElementById('previewContent');
  const validationGuardBox = document.getElementById('validationGuardBox');
  const btnStartCampaign = document.getElementById('btnStartCampaign');

  // Step Navigation Function
  function goToStep(step) {
    if (step < 1 || step > totalSteps) return;
    currentStep = step;

    // Update step tracker nodes
    stepNodes.forEach(node => {
      const s = parseInt(node.getAttribute('data-step-target'), 10);
      node.classList.remove('active', 'completed');
      if (s === currentStep) node.classList.add('active');
      else if (s < currentStep) node.classList.add('completed');
    });

    // Update panels
    panels.forEach(panel => {
      const s = parseInt(panel.getAttribute('data-step'), 10);
      if (s === currentStep) panel.classList.add('active');
      else panel.classList.remove('active');
    });

    // Step-specific initializers
    if (currentStep === 4) {
      renderAssignmentReview();
    } else if (currentStep === 6) {
      renderPreviewAndValidate();
    }

    // Scroll to top of composer
    window.scrollTo({ top: document.querySelector('.composer-form')?.offsetTop || 0, behavior: 'smooth' });
  }

  // Click step nodes
  stepNodes.forEach(node => {
    node.addEventListener('click', () => {
      const s = parseInt(node.getAttribute('data-step-target'), 10);
      goToStep(s);
    });
  });

  // Next / Prev buttons in wizard panels
  document.querySelectorAll('[data-wizard-next]').forEach(btn => {
    btn.addEventListener('click', () => goToStep(currentStep + 1));
  });
  document.querySelectorAll('[data-wizard-prev]').forEach(btn => {
    btn.addEventListener('click', () => goToStep(currentStep - 1));
  });

  // ─── STEP 1: TEMPLATE HANDLING ───
  btnLoadTemplate?.addEventListener('click', () => {
    const opt = selectTemplate?.options[selectTemplate.selectedIndex];
    if (!opt || !opt.value) {
      window.showToast?.('Vui lòng chọn một mẫu từ danh sách.', 'warning');
      return;
    }
    try {
      const payload = JSON.parse(opt.getAttribute('data-payload') || '{}');
      if (payload.content !== undefined) inputPostContent.value = payload.content;
      if (payload.min_delay) inputMinDelay.value = payload.min_delay;
      if (payload.max_delay) inputMaxDelay.value = payload.max_delay;
      window.showToast?.(`Đã nạp mẫu "${opt.text}" thành công!`, 'success');
    } catch(e) {
      window.showToast?.('Lỗi nạp dữ liệu template.', 'error');
    }
  });

  async function handleSaveTemplate() {
    const name = (inputCampaignName?.value || '').trim();
    const content = (inputPostContent?.value || '').trim();
    if (!name) {
      window.showToast?.('Vui lòng nhập tên chiến dịch để làm tên mẫu.', 'warning');
      goToStep(1);
      inputCampaignName?.focus();
      return;
    }
    try {
      const res = await fetch('/api/campaign-templates', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          template_name: name,
          payload: {
            content: content,
            min_delay: parseInt(inputMinDelay?.value || 1, 10),
            max_delay: parseInt(inputMaxDelay?.value || 3, 10),
          }
        })
      });
      const data = await res.json();
      if (data.success) {
        window.showToast?.('Đã lưu chiến dịch thành mẫu thành công!', 'success');
        // Add to select dropdown
        if (selectTemplate) {
          const opt = document.createElement('option');
          opt.value = data.template_id;
          opt.text = name;
          opt.setAttribute('data-payload', JSON.stringify({
            content, min_delay: inputMinDelay?.value, max_delay: inputMaxDelay?.value
          }));
          selectTemplate.appendChild(opt);
          selectTemplate.value = data.template_id;
        }
      } else {
        window.showToast?.(data.error || 'Không thể lưu mẫu', 'error');
      }
    } catch(e) {
      window.showToast?.('Lỗi kết nối khi lưu mẫu.', 'error');
    }
  }

  btnSaveTemplate?.addEventListener('click', handleSaveTemplate);
  btnSaveTemplateConfirm?.addEventListener('click', handleSaveTemplate);

  // ─── STEP 2: ACCOUNT SELECTION ───
  function updateAccountCount() {
    const selected = Array.from(accountCheckboxes).filter(cb => cb.checked);
    if (selectedAccountsCount) {
      selectedAccountsCount.textContent = `${selected.length} tài khoản đã chọn`;
    }
    accountCheckboxes.forEach(cb => {
      const card = cb.closest('.account-select-card');
      if (card) {
        if (cb.checked) card.classList.add('selected');
        else card.classList.remove('selected');
      }
    });
  }
  accountCheckboxes.forEach(cb => cb.addEventListener('change', updateAccountCount));
  updateAccountCount();

  // ─── STEP 3: GROUP SELECTION & FILTER ───
  function updateGroupCount() {
    const total = groupCheckboxes.length;
    const selected = Array.from(groupCheckboxes).filter(cb => cb.checked).length;
    if (selectedGroupsCount) {
      selectedGroupsCount.textContent = `${selected} / ${total} nhóm đã chọn`;
    }
  }

  function filterGroupItems() {
    const q = (groupSearch?.value || '').toLowerCase().trim();
    const mode = groupFilter?.value || 'all';

    document.querySelectorAll('.group-select-item').forEach(item => {
      const url = (item.getAttribute('data-url') || '').toLowerCase();
      const hasAccount = item.getAttribute('data-has-account') === 'true';

      let matchText = !q || url.includes(q);
      let matchFilter = true;
      if (mode === 'assigned') matchFilter = hasAccount;
      else if (mode === 'unassigned') matchFilter = !hasAccount;

      if (matchText && matchFilter) item.style.display = 'flex';
      else item.style.display = 'none';
    });
  }

  groupSearch?.addEventListener('input', filterGroupItems);
  groupFilter?.addEventListener('change', filterGroupItems);

  btnSelectAllGroups?.addEventListener('click', () => {
    document.querySelectorAll('.group-select-item').forEach(item => {
      if (item.style.display !== 'none') {
        const cb = item.querySelector('.group-select-checkbox');
        if (cb) cb.checked = true;
      }
    });
    updateGroupCount();
  });

  btnDeselectAllGroups?.addEventListener('click', () => {
    groupCheckboxes.forEach(cb => cb.checked = false);
    updateGroupCount();
  });

  groupCheckboxes.forEach(cb => cb.addEventListener('change', updateGroupCount));
  updateGroupCount();

  // ─── STEP 4: ASSIGNMENT REVIEW & EVEN DISTRIBUTION ───
  function getSelectedAccounts() {
    return Array.from(accountCheckboxes)
      .filter(cb => cb.checked)
      .map(cb => ({
        account_id: cb.value,
        name: cb.getAttribute('data-name') || cb.value,
        online: cb.getAttribute('data-online') === 'true'
      }));
  }

  function getSelectedGroups() {
    return Array.from(groupCheckboxes)
      .filter(cb => cb.checked)
      .map(cb => ({
        url: cb.value,
        assigned_account: cb.getAttribute('data-account') || ''
      }));
  }

  function renderAssignmentReview() {
    const accounts = getSelectedAccounts();
    const groups = getSelectedGroups();

    if (!assignmentReviewList) return;

    if (!accounts.length) {
      assignmentReviewList.innerHTML = '<div class="empty-compact text-red" style="padding:16px">Chưa chọn tài khoản Facebook nào ở Bước 2. Hãy quay lại Bước 2 để chọn.</div>';
      return;
    }
    if (!groups.length) {
      assignmentReviewList.innerHTML = '<div class="empty-compact text-red" style="padding:16px">Chưa chọn nhóm nào ở Bước 3. Hãy quay lại Bước 3 để chọn.</div>';
      return;
    }

    // Render list
    assignmentReviewList.innerHTML = groups.map((g, idx) => {
      const matchedAccount = accounts.find(a => a.account_id === g.assigned_account) || accounts[idx % accounts.length];
      return `
        <div class="group-row" style="padding:10px 14px; display:flex; align-items:center; justify-content:space-between; gap:12px; border-bottom:1px solid var(--border)">
          <span style="font-family:var(--font-mono); font-size:11px; color:var(--muted); width:32px">#${idx+1}</span>
          <a href="${g.url}" target="_blank" class="group-url-link" style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px">${g.url}</a>
          <select class="wizard-assignment-select" data-group-url="${g.url}" style="height:32px; font-size:12px; border-radius:var(--r-sm); background:var(--surface2); color:var(--text); border:1px solid var(--border2)">
            ${accounts.map(a => `<option value="${a.account_id}" ${a.account_id === matchedAccount.account_id ? 'selected' : ''}>${a.name}</option>`).join('')}
          </select>
        </div>
      `;
    }).join('');

    updateAssignmentBadges();

    // Hook change event
    assignmentReviewList.querySelectorAll('.wizard-assignment-select').forEach(sel => {
      sel.addEventListener('change', updateAssignmentBadges);
    });
  }

  function updateAssignmentBadges() {
    const accounts = getSelectedAccounts();
    const selects = assignmentReviewList?.querySelectorAll('.wizard-assignment-select') || [];
    const counts = {};
    accounts.forEach(a => counts[a.account_id] = 0);
    selects.forEach(sel => {
      counts[sel.value] = (counts[sel.value] || 0) + 1;
    });

    if (assignmentSummaryBadges) {
      assignmentSummaryBadges.innerHTML = accounts.map(a => `
        <span class="badge blue" style="font-size:12px">
          ${a.name}: <b>${counts[a.account_id] || 0}</b> nhóm
        </span>
      `).join('');
    }
  }

  btnEvenDistribute?.addEventListener('click', () => {
    const accounts = getSelectedAccounts();
    const selects = assignmentReviewList?.querySelectorAll('.wizard-assignment-select') || [];
    if (!accounts.length || !selects.length) return;

    selects.forEach((sel, idx) => {
      const targetAcc = accounts[idx % accounts.length];
      sel.value = targetAcc.account_id;
    });
    updateAssignmentBadges();
    window.showToast?.('Đã chia đều danh sách nhóm cho các tài khoản!', 'success');
  });

  // ─── STEP 5: SCHEDULE HANDLING ───
  scheduleOptionCards.forEach(card => {
    card.addEventListener('click', () => {
      scheduleOptionCards.forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      const action = card.getAttribute('data-action');
      if (campaignActionHidden) campaignActionHidden.value = action;

      const scheduleField = document.getElementById('wizardScheduleDateField');
      if (scheduleField) {
        if (action === 'schedule') scheduleField.style.display = 'block';
        else scheduleField.style.display = 'none';
      }
    });
  });

  scheduledLocalInput?.addEventListener('change', () => {
    if (scheduledLocalInput.value && scheduledUtcHidden) {
      const dt = new Date(scheduledLocalInput.value);
      scheduledUtcHidden.value = dt.toISOString().slice(0, 19);
    }
  });

  // ─── STEP 6: PREVIEW & VALIDATION GUARD ───
  function renderPreviewAndValidate() {
    const name = (inputCampaignName?.value || '').trim();
    const content = (inputPostContent?.value || '').trim();
    const minDelay = inputMinDelay?.value || 1;
    const maxDelay = inputMaxDelay?.value || 3;
    const accounts = getSelectedAccounts();
    const groups = getSelectedGroups();
    const action = campaignActionHidden?.value || 'run';

    // Summary texts
    if (previewCampName) previewCampName.textContent = name || '(Chưa đặt tên)';
    if (previewAccounts) previewAccounts.textContent = accounts.map(a => a.name).join(', ') || 'Chưa chọn tài khoản';
    if (previewGroupsCount) previewGroupsCount.textContent = `${groups.length} Groups Facebook`;
    if (previewDelay) previewDelay.textContent = `${minDelay} – ${maxDelay} phút`;
    if (previewMode) {
      if (action === 'run') previewMode.textContent = 'Chạy ngay sau khi bắt đầu';
      else if (action === 'schedule') previewMode.textContent = `Hẹn lịch: ${scheduledLocalInput?.value || 'Chưa chọn giờ'}`;
      else previewMode.textContent = 'Lưu bản nháp (Draft)';
    }
    if (previewContent) previewContent.textContent = content || '(Chưa nhập nội dung bài đăng)';

    // Validation Guard Checklist
    const checks = [
      {
        key: 'name',
        label: 'Tên chiến dịch đã nhập',
        valid: Boolean(name),
        step: 1,
        failMsg: 'Chưa có tên chiến dịch.'
      },
      {
        key: 'content',
        label: 'Nội dung bài đăng đã nhập',
        valid: Boolean(content),
        step: 1,
        failMsg: 'Nội dung bài đăng đang để trống.'
      },
      {
        key: 'accounts',
        label: 'Đã chọn ít nhất 1 tài khoản Facebook',
        valid: accounts.length > 0,
        step: 2,
        failMsg: 'Chưa chọn tài khoản Facebook nào.'
      },
      {
        key: 'groups',
        label: 'Đã chọn ít nhất 1 nhóm Facebook mục tiêu',
        valid: groups.length > 0,
        step: 3,
        failMsg: 'Chưa chọn nhóm mục tiêu nào.'
      },
      {
        key: 'schedule_future',
        label: 'Thời gian hẹn lịch hợp lệ (ở tương lai)',
        valid: action !== 'schedule' || (scheduledLocalInput?.value && new Date(scheduledLocalInput.value) > new Date()),
        step: 5,
        failMsg: 'Thời gian hẹn chạy phải ở tương lai.'
      }
    ];

    const allValid = checks.every(c => c.valid);

    if (validationGuardBox) {
      if (allValid) {
        validationGuardBox.className = 'validation-guard-box valid';
        validationGuardBox.innerHTML = `
          <strong style="display:flex; align-items:center; gap:8px; font-size:var(--text-sm)">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
            Tất cả điều kiện hợp lệ! Chiến dịch đã sẵn sàng khởi chạy.
          </strong>
        `;
      } else {
        validationGuardBox.className = 'validation-guard-box invalid';
        const failed = checks.filter(c => !c.valid);
        validationGuardBox.innerHTML = `
          <strong style="display:flex; align-items:center; gap:8px; font-size:var(--text-sm); margin-bottom:8px">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm1 15h-2v-2h2v2zm0-4h-2V7h2v6z"/></svg>
            Cần bổ sung thông tin trước khi bắt đầu:
          </strong>
          <div style="display:flex; flex-direction:column; gap:6px">
            ${failed.map(f => `
              <div class="validation-check-item">
                <span>⚠️ ${f.failMsg}</span>
                <button type="button" class="btn secondary sm" onclick="window.wizardGoToStep(${f.step})" style="font-size:11px; padding:2px 8px; min-height:28px">
                  Đi đến Bước ${f.step}
                </button>
              </div>
            `).join('')}
          </div>
        `;
      }
    }

    if (btnStartCampaign) {
      btnStartCampaign.disabled = !allValid;
    }
  }

  window.wizardGoToStep = goToStep;
});
