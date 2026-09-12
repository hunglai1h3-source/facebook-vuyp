/**
 * FB POST PRO - Campaign Creation Wizard (Phase 12 + Simplified Background Flow)
 * Supports:
 * 1. Quick Mode (3 Steps) - Default UX
 * 2. Advanced Mode (6 Steps) - Switchable for power users
 * 3. Preflight Validation (/api/campaign/preflight)
 * 4. Auto 50/50 Group Balancing & Manual Allocation Accordion
 * 5. LocalStorage Preferences Persistence
 * 6. Background Execution decoupled from active tab
 */
document.addEventListener('DOMContentLoaded', () => {
  const PREF_KEY = 'fbpostpro_campaign_pref';

  // =========================================================
  // 1. MODE SWITCHER (QUICK 3-STEP vs ADVANCED 6-STEP)
  // =========================================================
  const btnModeQuick = document.getElementById('btnModeQuick');
  const btnModeAdvanced = document.getElementById('btnModeAdvanced');
  const quickWizardContainer = document.getElementById('quickWizardContainer');
  const advancedWizardContainer = document.getElementById('advancedWizardContainer');

  let activeMode = 'quick'; // 'quick' | 'advanced'

  function switchMode(mode) {
    activeMode = mode;
    if (mode === 'quick') {
      btnModeQuick?.classList.add('active');
      btnModeAdvanced?.classList.remove('active');
      if (quickWizardContainer) quickWizardContainer.style.display = 'block';
      if (advancedWizardContainer) advancedWizardContainer.style.display = 'none';

      // Sync data from Advanced to Quick
      syncDataToQuick();
      setFormInputsDisabled(advancedWizardContainer, true);
      setFormInputsDisabled(quickWizardContainer, false);
    } else {
      btnModeAdvanced?.classList.add('active');
      btnModeQuick?.classList.remove('active');
      if (quickWizardContainer) quickWizardContainer.style.display = 'none';
      if (advancedWizardContainer) advancedWizardContainer.style.display = 'block';

      // Sync data from Quick to Advanced
      syncDataToAdvanced();
      setFormInputsDisabled(quickWizardContainer, true);
      setFormInputsDisabled(advancedWizardContainer, false);
    }
  }

  function setFormInputsDisabled(container, disabled) {
    if (!container) return;
    const inputs = container.querySelectorAll('input:not([type="hidden"]), textarea, select');
    inputs.forEach(el => {
      // Don't disable checkboxes that we use for state tracking
      if (el.type === 'checkbox' || el.type === 'radio') {
        return;
      }
      el.disabled = disabled;
    });
  }

  btnModeQuick?.addEventListener('click', () => switchMode('quick'));
  btnModeAdvanced?.addEventListener('click', () => switchMode('advanced'));

  // =========================================================
  // 2. QUICK MODE WIZARD (3 STEPS)
  // =========================================================
  let quickCurrentStep = 1;
  const quickStepNodes = document.querySelectorAll('[data-quick-step-target]');
  const quickPanels = document.querySelectorAll('.quick-panel');

  // Quick Step 1 Elements
  const quickCampaignName = document.getElementById('quickCampaignName');
  const quickPostContent = document.getElementById('quickPostContent');
  const quickSelectTemplate = document.getElementById('quickSelectTemplate');
  const btnQuickLoadTemplate = document.getElementById('btnQuickLoadTemplate');
  const btnQuickSaveTemplate = document.getElementById('btnQuickSaveTemplate');
  const btnQuickSaveTemplateConfirm = document.getElementById('btnQuickSaveTemplateConfirm');

  // Quick Step 2 Elements
  const quickAccountCheckboxes = document.querySelectorAll('.quick-account-checkbox');
  const quickGroupCheckboxes = document.querySelectorAll('.quick-group-checkbox');
  const quickAccountCountBadge = document.getElementById('quickAccountCountBadge');
  const quickGroupCountBadge = document.getElementById('quickGroupCountBadge');
  const quickGroupSearch = document.getElementById('quickGroupSearch');
  const quickGroupFilter = document.getElementById('quickGroupFilter');
  const btnQuickSelectAllGroups = document.getElementById('btnQuickSelectAllGroups');
  const btnQuickDeselectAllGroups = document.getElementById('btnQuickDeselectAllGroups');
  const quickAutoBalanceChips = document.getElementById('quickAutoBalanceChips');
  const btnToggleQuickManualAlloc = document.getElementById('btnToggleQuickManualAlloc');
  const quickManualAllocContent = document.getElementById('quickManualAllocContent');
  const quickManualAllocBadge = document.getElementById('quickManualAllocBadge');
  const quickManualAllocList = document.getElementById('quickManualAllocList');
  const btnQuickEvenDistribute = document.getElementById('btnQuickEvenDistribute');

  // Quick Step 3 Elements
  const quickPreflightCard = document.getElementById('quickPreflightCard');
  const quickPreflightStatusDot = document.getElementById('quickPreflightStatusDot');
  const quickPreflightChecksGrid = document.getElementById('quickPreflightChecksGrid');
  const btnRecheckPreflight = document.getElementById('btnRecheckPreflight');
  const quickPreviewCampName = document.getElementById('quickPreviewCampName');
  const quickPreviewAccounts = document.getElementById('quickPreviewAccounts');
  const quickPreviewGroupsCount = document.getElementById('quickPreviewGroupsCount');
  const quickPreviewMode = document.getElementById('quickPreviewMode');
  const quickPreviewContent = document.getElementById('quickPreviewContent');
  const btnQuickScheduleRunNow = document.getElementById('btnQuickScheduleRunNow');
  const btnQuickScheduleLater = document.getElementById('btnQuickScheduleLater');
  const quickScheduleDateField = document.getElementById('quickScheduleDateField');
  const quickScheduledLocal = document.getElementById('quickScheduledLocal');
  const btnToggleQuickAdvancedOptions = document.getElementById('btnToggleQuickAdvancedOptions');
  const quickAdvancedOptionsContent = document.getElementById('quickAdvancedOptionsContent');
  const quickMinDelay = document.getElementById('quick_min_delay');
  const quickMaxDelay = document.getElementById('quick_max_delay');
  const btnQuickSaveDraft = document.getElementById('btnQuickSaveDraft');
  const btnQuickStartCampaign = document.getElementById('btnQuickStartCampaign');

  // Hidden Form Inputs
  const campaignActionHidden = document.getElementById('campaignActionHidden');
  const scheduledUtcHidden = document.getElementById('scheduledUtcHidden');

  // Manual snapshot overrides map: groupUrl -> accountId (null = auto balanced)
  let customSnapshotMap = null;

  function goToQuickStep(step) {
    if (step < 1 || step > 3) return;

    // Validation before advancing
    if (step > quickCurrentStep) {
      if (quickCurrentStep === 1) {
        const name = (quickCampaignName?.value || '').trim();
        const content = (quickPostContent?.value || '').trim();
        if (!name) {
          window.showToast?.('Vui lòng nhập tên chiến dịch.', 'warning');
          quickCampaignName?.focus();
          return;
        }
        if (!content) {
          window.showToast?.('Vui lòng nhập nội dung bài đăng.', 'warning');
          quickPostContent?.focus();
          return;
        }
      } else if (quickCurrentStep === 2) {
        const selAccounts = getQuickSelectedAccounts();
        const selGroups = getQuickSelectedGroups();
        if (selAccounts.length === 0) {
          window.showToast?.('Vui lòng chọn ít nhất 1 tài khoản Facebook.', 'warning');
          return;
        }
        if (selGroups.length === 0) {
          window.showToast?.('Vui lòng chọn ít nhất 1 nhóm Facebook.', 'warning');
          return;
        }
      }
    }

    quickCurrentStep = step;

    // Update stepper UI
    quickStepNodes.forEach(node => {
      const s = parseInt(node.getAttribute('data-quick-step-target'), 10);
      node.classList.remove('active', 'completed');
      if (s === quickCurrentStep) node.classList.add('active');
      else if (s < quickCurrentStep) node.classList.add('completed');
    });

    // Update panels
    quickPanels.forEach(panel => {
      const s = parseInt(panel.getAttribute('data-quick-step'), 10);
      if (s === quickCurrentStep) panel.classList.add('active');
      else panel.classList.remove('active');
    });

    if (quickCurrentStep === 2) {
      updateQuickStep2();
    } else if (quickCurrentStep === 3) {
      updateQuickStep3();
      triggerPreflightCheck();
    }

    window.scrollTo({ top: document.querySelector('.composer-form')?.offsetTop || 0, behavior: 'smooth' });
  }

  quickStepNodes.forEach(node => {
    node.addEventListener('click', () => {
      const s = parseInt(node.getAttribute('data-quick-step-target'), 10);
      goToQuickStep(s);
    });
  });

  document.querySelectorAll('[data-quick-next]').forEach(btn => {
    btn.addEventListener('click', () => goToQuickStep(quickCurrentStep + 1));
  });
  document.querySelectorAll('[data-quick-prev]').forEach(btn => {
    btn.addEventListener('click', () => goToQuickStep(quickCurrentStep - 1));
  });

  // Quick Step 1: Template handling
  btnQuickLoadTemplate?.addEventListener('click', () => {
    const opt = quickSelectTemplate?.options[quickSelectTemplate.selectedIndex];
    if (!opt || !opt.value) {
      window.showToast?.('Vui lòng chọn một mẫu từ danh sách.', 'warning');
      return;
    }
    try {
      const payload = JSON.parse(opt.getAttribute('data-payload') || '{}');
      if (payload.content !== undefined && quickPostContent) quickPostContent.value = payload.content;
      if (payload.min_delay && quickMinDelay) quickMinDelay.value = payload.min_delay;
      if (payload.max_delay && quickMaxDelay) quickMaxDelay.value = payload.max_delay;
      window.showToast?.(`Đã nạp mẫu "${opt.text}" thành công!`, 'success');
    } catch(e) {
      window.showToast?.('Lỗi nạp dữ liệu template.', 'error');
    }
  });

  async function handleQuickSaveTemplate() {
    const name = (quickCampaignName?.value || '').trim();
    const content = (quickPostContent?.value || '').trim();
    if (!name) {
      window.showToast?.('Vui lòng nhập tên chiến dịch để làm tên mẫu.', 'warning');
      goToQuickStep(1);
      quickCampaignName?.focus();
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
            min_delay: parseInt(quickMinDelay?.value || 1, 10),
            max_delay: parseInt(quickMaxDelay?.value || 3, 10),
          }
        })
      });
      const data = await res.json();
      if (data.success) {
        window.showToast?.('Đã lưu chiến dịch thành mẫu thành công!', 'success');
        if (quickSelectTemplate) {
          const opt = document.createElement('option');
          opt.value = data.template_id;
          opt.text = name;
          opt.setAttribute('data-payload', JSON.stringify({
            content, min_delay: quickMinDelay?.value, max_delay: quickMaxDelay?.value
          }));
          quickSelectTemplate.appendChild(opt);
          quickSelectTemplate.value = data.template_id;
        }
      } else {
        window.showToast?.(data.error || 'Không thể lưu mẫu', 'error');
      }
    } catch(e) {
      window.showToast?.('Lỗi kết nối khi lưu mẫu.', 'error');
    }
  }

  btnQuickSaveTemplate?.addEventListener('click', handleQuickSaveTemplate);
  btnQuickSaveTemplateConfirm?.addEventListener('click', handleQuickSaveTemplate);

  // Quick Step 2: Accounts & Groups & Auto-Balance
  function getQuickSelectedAccounts() {
    return Array.from(quickAccountCheckboxes)
      .filter(cb => cb.checked)
      .map(cb => ({
        account_id: cb.value,
        name: cb.getAttribute('data-name') || cb.value,
        online: cb.getAttribute('data-online') === 'true',
        status: cb.getAttribute('data-status') || 'READY'
      }));
  }

  function getQuickSelectedGroups() {
    return Array.from(quickGroupCheckboxes)
      .filter(cb => cb.checked)
      .map(cb => ({
        url: cb.value,
        assigned_account: cb.getAttribute('data-account') || ''
      }));
  }

  function updateQuickStep2() {
    const selAccounts = getQuickSelectedAccounts();
    const selGroups = getQuickSelectedGroups();

    if (quickAccountCountBadge) {
      quickAccountCountBadge.textContent = `${selAccounts.length} tài khoản`;
    }
    if (quickGroupCountBadge) {
      quickGroupCountBadge.textContent = `${selGroups.length} / ${quickGroupCheckboxes.length} nhóm`;
    }

    quickAccountCheckboxes.forEach(cb => {
      const card = cb.closest('.account-select-card');
      if (card) {
        if (cb.checked) card.classList.add('selected');
        else card.classList.remove('selected');
      }
    });

    renderAutoBalance(selAccounts, selGroups);
    saveCampaignPrefs();
  }

  function renderAutoBalance(accounts, groups) {
    if (!quickAutoBalanceChips) return;

    if (accounts.length === 0 || groups.length === 0) {
      quickAutoBalanceChips.innerHTML = '<span style="font-size:12px; color:var(--text3)">Chưa chọn đủ tài khoản hoặc nhóm.</span>';
      return;
    }

    const counts = {};
    accounts.forEach(a => counts[a.account_id] = 0);

    if (customSnapshotMap) {
      groups.forEach(g => {
        const aid = customSnapshotMap[g.url] || accounts[0].account_id;
        counts[aid] = (counts[aid] || 0) + 1;
      });
    } else {
      groups.forEach((g, idx) => {
        const acc = accounts[idx % accounts.length];
        counts[acc.account_id] = (counts[acc.account_id] || 0) + 1;
      });
    }

    quickAutoBalanceChips.innerHTML = accounts.map(a => {
      const cnt = counts[a.account_id] || 0;
      const pct = Math.round((cnt / (groups.length || 1)) * 100);
      return `
        <div class="auto-balance-chip">
          <span>${a.name}:</span>
          <strong>${cnt} nhóm (${pct}%)</strong>
        </div>
      `;
    }).join('');

    renderManualAllocationTable(accounts, groups);
  }

  function renderManualAllocationTable(accounts, groups) {
    if (!quickManualAllocList) return;
    if (accounts.length === 0 || groups.length === 0) {
      quickManualAllocList.innerHTML = '<div style="padding:12px; font-size:12px; color:var(--text3)">Chọn ít nhất 1 tài khoản và 1 nhóm để phân bổ.</div>';
      return;
    }

    quickManualAllocList.innerHTML = groups.map((g, idx) => {
      const assignedAid = (customSnapshotMap && customSnapshotMap[g.url]) ||
        accounts[idx % accounts.length].account_id;
      return `
        <div class="group-row" style="padding:8px 12px; display:flex; align-items:center; justify-content:space-between; gap:10px; border-bottom:1px solid var(--border)">
          <span style="font-family:var(--font-mono); font-size:11px; color:var(--muted); width:28px">#${idx + 1}</span>
          <span style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; color:var(--text)">${g.url}</span>
          <select class="quick-alloc-select" data-group-url="${g.url}" style="height:30px; font-size:12px; border-radius:var(--r-sm); background:var(--surface2); color:var(--text); border:1px solid var(--border2)">
            ${accounts.map(a => `<option value="${a.account_id}" ${a.account_id === assignedAid ? 'selected' : ''}>${a.name}</option>`).join('')}
          </select>
        </div>
      `;
    }).join('');

    quickManualAllocList.querySelectorAll('.quick-alloc-select').forEach(sel => {
      sel.addEventListener('change', () => {
        if (!customSnapshotMap) customSnapshotMap = {};
        const url = sel.getAttribute('data-group-url');
        customSnapshotMap[url] = sel.value;
        if (quickManualAllocBadge) {
          quickManualAllocBadge.textContent = 'Đã chỉnh sửa thủ công';
          quickManualAllocBadge.className = 'badge amber';
        }
        renderAutoBalance(getQuickSelectedAccounts(), getQuickSelectedGroups());
      });
    });
  }

  btnQuickEvenDistribute?.addEventListener('click', () => {
    customSnapshotMap = null;
    if (quickManualAllocBadge) {
      quickManualAllocBadge.textContent = 'Mặc định: Tự động chia đều';
      quickManualAllocBadge.className = 'badge muted';
    }
    renderAutoBalance(getQuickSelectedAccounts(), getQuickSelectedGroups());
    window.showToast?.('Đã chia đều 50/50 lại cho các tài khoản!', 'success');
  });

  btnToggleQuickManualAlloc?.addEventListener('click', () => {
    if (quickManualAllocContent) {
      const isClosed = quickManualAllocContent.style.display === 'none';
      quickManualAllocContent.style.display = isClosed ? 'block' : 'none';
      if (isClosed) {
        renderManualAllocationTable(getQuickSelectedAccounts(), getQuickSelectedGroups());
      }
    }
  });

  quickAccountCheckboxes.forEach(cb => cb.addEventListener('change', updateQuickStep2));
  quickGroupCheckboxes.forEach(cb => cb.addEventListener('change', updateQuickStep2));

  // Quick Groups Filter & Search
  function filterQuickGroups() {
    const q = (quickGroupSearch?.value || '').toLowerCase().trim();
    const mode = quickGroupFilter?.value || 'all';

    document.querySelectorAll('#quickGroupList .group-select-item').forEach(item => {
      const url = (item.getAttribute('data-url') || '').toLowerCase();
      const hasAccount = item.getAttribute('data-has-account') === 'true';

      let matchText = !q || url.includes(q);
      let matchFilter = true;
      if (mode === 'assigned') matchFilter = hasAccount;
      else if (mode === 'unassigned') matchFilter = !hasAccount;

      item.style.display = (matchText && matchFilter) ? 'flex' : 'none';
    });
  }

  quickGroupSearch?.addEventListener('input', filterQuickGroups);
  quickGroupFilter?.addEventListener('change', filterQuickGroups);

  btnQuickSelectAllGroups?.addEventListener('click', () => {
    document.querySelectorAll('#quickGroupList .group-select-item').forEach(item => {
      if (item.style.display !== 'none') {
        const cb = item.querySelector('.quick-group-checkbox');
        if (cb) cb.checked = true;
      }
    });
    updateQuickStep2();
  });

  btnQuickDeselectAllGroups?.addEventListener('click', () => {
    quickGroupCheckboxes.forEach(cb => cb.checked = false);
    updateQuickStep2();
  });

  // Quick Step 3: Review, Preflight & Run
  function updateQuickStep3() {
    const name = (quickCampaignName?.value || '').trim();
    const content = (quickPostContent?.value || '').trim();
    const selAccounts = getQuickSelectedAccounts();
    const selGroups = getQuickSelectedGroups();
    const action = campaignActionHidden?.value || 'run';

    if (quickPreviewCampName) quickPreviewCampName.textContent = name || '(Chưa đặt tên)';
    if (quickPreviewAccounts) quickPreviewAccounts.textContent = selAccounts.map(a => a.name).join(', ') || 'Chưa chọn tài khoản';
    if (quickPreviewGroupsCount) quickPreviewGroupsCount.textContent = `${selGroups.length} Groups Facebook`;
    if (quickPreviewContent) quickPreviewContent.textContent = content || '(Chưa nhập nội dung bài đăng)';

    if (quickPreviewMode) {
      if (action === 'run') quickPreviewMode.textContent = '🚀 Chạy nền ngay';
      else if (action === 'schedule') quickPreviewMode.textContent = `⏰ Hẹn lịch: ${quickScheduledLocal?.value || 'Chưa chọn'}`;
      else quickPreviewMode.textContent = '💾 Lưu bản nháp (Draft)';
    }
  }

  // Preflight validation caller
  async function triggerPreflightCheck() {
    if (!quickPreflightChecksGrid) return;
    const selAccounts = getQuickSelectedAccounts();
    const selGroups = getQuickSelectedGroups();
    const scheduledUtc = scheduledUtcHidden?.value || null;

    quickPreflightChecksGrid.innerHTML = `
      <div style="grid-column:1/-1; padding:8px 0; color:var(--text2); font-size:12px; display:flex; align-items:center; gap:8px">
        <span class="pulse pulsing" style="background:var(--blue)"></span> Đang kiểm tra hệ thống (Worker, Tài khoản, Session, Nhóm)...
      </div>
    `;

    try {
      const res = await fetch('/api/campaign/preflight', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          account_ids: selAccounts.map(a => a.account_id),
          group_urls: selGroups.map(g => g.url),
          scheduled_at: scheduledUtc
        })
      });
      const data = await res.json();
      renderPreflightResults(data);
    } catch (e) {
      quickPreflightChecksGrid.innerHTML = `
        <div style="grid-column:1/-1; color:var(--red); font-size:12px">
          Không thể kết nối đến máy chủ để kiểm tra. Vui lòng thử lại.
        </div>
      `;
    }
  }

  function renderPreflightResults(data) {
    if (!quickPreflightCard || !quickPreflightChecksGrid) return;

    const status = data.status || 'READY';
    quickPreflightCard.className = `preflight-card status-${status.toLowerCase()}`;

    if (quickPreflightStatusDot) {
      if (status === 'READY') quickPreflightStatusDot.style.background = 'var(--green)';
      else if (status === 'WARNING') quickPreflightStatusDot.style.background = 'var(--amber)';
      else quickPreflightStatusDot.style.background = 'var(--red)';
    }

    const checks = data.checks || [];
    quickPreflightChecksGrid.innerHTML = checks.map(c => {
      const st = c.status || 'PASS';
      const icon = st === 'PASS' ? '✓' : (st === 'WARN' ? '⚠' : '✗');
      const cls = st === 'PASS' ? 'check-pass' : (st === 'WARN' ? 'check-warn' : 'check-fail');
      return `
        <div class="preflight-check-item ${cls}">
          <span class="check-icon" style="font-weight:700">${icon}</span>
          <div style="min-width:0; flex:1">
            <strong style="display:block; font-size:12px">${c.label}</strong>
            <span style="display:block; font-size:11px; color:var(--text2); line-height:1.4">${c.message}</span>
          </div>
        </div>
      `;
    }).join('');

    if (btnQuickStartCampaign) {
      btnQuickStartCampaign.disabled = !data.can_run;
    }
  }

  btnRecheckPreflight?.addEventListener('click', triggerPreflightCheck);

  // Scheduling options toggle
  btnQuickScheduleRunNow?.addEventListener('click', () => {
    btnQuickScheduleRunNow.classList.add('active');
    btnQuickScheduleLater?.classList.remove('active');
    const radio = btnQuickScheduleRunNow.querySelector('input');
    if (radio) radio.checked = true;
    if (campaignActionHidden) campaignActionHidden.value = 'run';
    if (quickScheduleDateField) quickScheduleDateField.style.display = 'none';
    updateQuickStep3();
  });

  btnQuickScheduleLater?.addEventListener('click', () => {
    btnQuickScheduleLater.classList.add('active');
    btnQuickScheduleRunNow?.classList.remove('active');
    const radio = btnQuickScheduleLater.querySelector('input');
    if (radio) radio.checked = true;
    if (campaignActionHidden) campaignActionHidden.value = 'schedule';
    if (quickScheduleDateField) quickScheduleDateField.style.display = 'block';
    updateQuickStep3();
  });

  quickScheduledLocal?.addEventListener('change', () => {
    if (quickScheduledLocal.value && scheduledUtcHidden) {
      const dt = new Date(quickScheduledLocal.value);
      scheduledUtcHidden.value = dt.toISOString().slice(0, 19);
    }
    updateQuickStep3();
    triggerPreflightCheck();
  });

  // Collapsible Advanced Options
  btnToggleQuickAdvancedOptions?.addEventListener('click', () => {
    if (quickAdvancedOptionsContent) {
      const isClosed = quickAdvancedOptionsContent.style.display === 'none';
      quickAdvancedOptionsContent.style.display = isClosed ? 'block' : 'none';
    }
  });

  // Save draft
  btnQuickSaveDraft?.addEventListener('click', () => {
    if (campaignActionHidden) campaignActionHidden.value = 'draft';
    executeQuickCampaign(true);
  });

  // Run Campaign button
  btnQuickStartCampaign?.addEventListener('click', () => {
    executeQuickCampaign(false);
  });

  async function executeQuickCampaign(isDraft) {
    const name = (quickCampaignName?.value || '').trim();
    const content = (quickPostContent?.value || '').trim();
    const minDelay = parseInt(quickMinDelay?.value || 1, 10);
    const maxDelay = parseInt(quickMaxDelay?.value || 3, 10);
    const selAccounts = getQuickSelectedAccounts();
    const selGroups = getQuickSelectedGroups();
    const action = isDraft ? 'draft' : (campaignActionHidden?.value || 'run');
    const scheduledUtc = scheduledUtcHidden?.value || '';

    if (!name) {
      window.showToast?.('Vui lòng nhập tên chiến dịch.', 'warning');
      goToQuickStep(1);
      quickCampaignName?.focus();
      return;
    }
    if (!content) {
      window.showToast?.('Vui lòng nhập nội dung bài đăng.', 'warning');
      goToQuickStep(1);
      quickPostContent?.focus();
      return;
    }
    if (selAccounts.length === 0) {
      window.showToast?.('Vui lòng chọn ít nhất 1 tài khoản Facebook.', 'warning');
      goToQuickStep(2);
      return;
    }
    if (selGroups.length === 0) {
      window.showToast?.('Vui lòng chọn ít nhất 1 nhóm Facebook.', 'warning');
      goToQuickStep(2);
      return;
    }

    saveCampaignPrefs();

    const quickImagesInput = document.getElementById('quick_images');
    const hasFiles = quickImagesInput && quickImagesInput.files && quickImagesInput.files.length > 0;

    if (btnQuickStartCampaign) {
      btnQuickStartCampaign.disabled = true;
      btnQuickStartCampaign.innerHTML = '<span class="pulse pulsing"></span> Đang khởi chạy...';
    }

    try {
      let res;
      if (hasFiles) {
        const formData = new FormData();
        formData.append('campaign_name', name);
        formData.append('content', content);
        formData.append('min_delay', minDelay);
        formData.append('max_delay', maxDelay);
        formData.append('campaign_action', action);
        formData.append('scheduled_at', scheduledUtc);
        selAccounts.forEach(a => formData.append('selected_accounts', a.account_id));
        selGroups.forEach(g => formData.append('selected_groups', g.url));
        if (customSnapshotMap) {
          formData.append('account_group_snapshot', JSON.stringify(customSnapshotMap));
        }
        for (let i = 0; i < quickImagesInput.files.length; i++) {
          formData.append('images', quickImagesInput.files[i]);
        }

        res = await fetch('/run-campaign', {
          method: 'POST',
          headers: { 'X-Requested-With': 'XMLHttpRequest' },
          body: formData
        });
      } else {
        res = await fetch('/run-campaign', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
          },
          body: JSON.stringify({
            campaign_name: name,
            content: content,
            min_delay: minDelay,
            max_delay: maxDelay,
            campaign_action: action,
            scheduled_at: scheduledUtc,
            selected_accounts: selAccounts.map(a => a.account_id),
            selected_groups: selGroups.map(g => g.url),
            account_group_snapshot: customSnapshotMap || null
          })
        });
      }

      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        window.showToast?.(
          '🚀 Chiến dịch đã được khởi chạy trong nền! Bạn có thể yên tâm chuyển tab hoặc đóng trình duyệt.',
          'success'
        );
        setTimeout(() => {
          window.location.href = data.redirect_url || '/compose';
        }, 1200);
      } else {
        const err = await res.text();
        window.showToast?.(`Lỗi khởi chạy chiến dịch: ${err}`, 'error');
        if (btnQuickStartCampaign) {
          btnQuickStartCampaign.disabled = false;
          btnQuickStartCampaign.innerHTML = '<svg width="17" height="17" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg> BẮT ĐẦU NGAY';
        }
      }
    } catch (e) {
      window.showToast?.('Lỗi kết nối khi khởi chạy chiến dịch.', 'error');
      if (btnQuickStartCampaign) {
        btnQuickStartCampaign.disabled = false;
        btnQuickStartCampaign.innerHTML = '<svg width="17" height="17" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg> BẮT ĐẦU NGAY';
      }
    }
  }

  // LocalStorage Preferences Persistence
  function saveCampaignPrefs() {
    try {
      const selAccounts = getQuickSelectedAccounts().map(a => a.account_id);
      const selGroups = getQuickSelectedGroups().map(g => g.url);
      const minDelay = quickMinDelay?.value || 1;
      const maxDelay = quickMaxDelay?.value || 3;
      const payload = {
        accounts: selAccounts,
        groups: selGroups,
        min_delay: minDelay,
        max_delay: maxDelay
      };
      localStorage.setItem(PREF_KEY, JSON.stringify(payload));
    } catch(e) {}
  }

  function loadCampaignPrefs() {
    try {
      const raw = localStorage.getItem(PREF_KEY);
      if (!raw) return;
      const data = JSON.parse(raw);
      if (data.accounts && Array.isArray(data.accounts) && data.accounts.length > 0) {
        quickAccountCheckboxes.forEach(cb => {
          cb.checked = data.accounts.includes(cb.value);
        });
      }
      if (data.groups && Array.isArray(data.groups) && data.groups.length > 0) {
        quickGroupCheckboxes.forEach(cb => {
          cb.checked = data.groups.includes(cb.value);
        });
      }
      if (data.min_delay && quickMinDelay) quickMinDelay.value = data.min_delay;
      if (data.max_delay && quickMaxDelay) quickMaxDelay.value = data.max_delay;
    } catch(e) {}
  }

  // =========================================================
  // 3. DATA SYNCHRONIZATION BETWEEN QUICK & ADVANCED
  // =========================================================
  const advCampaignName = document.getElementById('campaign_name');
  const advPostContent = document.getElementById('postContent');
  const advMinDelay = document.getElementById('min_delay');
  const advMaxDelay = document.getElementById('max_delay');
  const advAccountCheckboxes = document.querySelectorAll('.account-select-checkbox');
  const advGroupCheckboxes = document.querySelectorAll('.group-select-checkbox');

  function syncDataToAdvanced() {
    if (advCampaignName && quickCampaignName) advCampaignName.value = quickCampaignName.value;
    if (advPostContent && quickPostContent) advPostContent.value = quickPostContent.value;
    if (advMinDelay && quickMinDelay) advMinDelay.value = quickMinDelay.value;
    if (advMaxDelay && quickMaxDelay) advMaxDelay.value = quickMaxDelay.value;

    const selAccounts = getQuickSelectedAccounts().map(a => a.account_id);
    advAccountCheckboxes.forEach(cb => {
      cb.checked = selAccounts.includes(cb.value);
      const card = cb.closest('.account-select-card');
      if (card) {
        if (cb.checked) card.classList.add('selected');
        else card.classList.remove('selected');
      }
    });

    const selGroups = getQuickSelectedGroups().map(g => g.url);
    advGroupCheckboxes.forEach(cb => {
      cb.checked = selGroups.includes(cb.value);
    });
  }

  function syncDataToQuick() {
    if (quickCampaignName && advCampaignName) quickCampaignName.value = advCampaignName.value;
    if (quickPostContent && advPostContent) quickPostContent.value = advPostContent.value;
    if (quickMinDelay && advMinDelay) quickMinDelay.value = advMinDelay.value;
    if (quickMaxDelay && advMaxDelay) quickMaxDelay.value = advMaxDelay.value;

    const selAdvAccounts = Array.from(advAccountCheckboxes).filter(cb => cb.checked).map(cb => cb.value);
    if (selAdvAccounts.length > 0) {
      quickAccountCheckboxes.forEach(cb => {
        cb.checked = selAdvAccounts.includes(cb.value);
      });
    }

    const selAdvGroups = Array.from(advGroupCheckboxes).filter(cb => cb.checked).map(cb => cb.value);
    if (selAdvGroups.length > 0) {
      quickGroupCheckboxes.forEach(cb => {
        cb.checked = selAdvGroups.includes(cb.value);
      });
    }

    updateQuickStep2();
  }

  // =========================================================
  // 4. ADVANCED 6-STEP WIZARD CONTROLLER (POWER USERS / TESTS)
  // =========================================================
  let advCurrentStep = 1;
  const advTotalSteps = 6;
  const advStepNodes = document.querySelectorAll('#advancedWizardContainer .wizard-step-node');
  const advPanels = document.querySelectorAll('#advancedWizardContainer .wizard-panel');

  const advSelectedAccountsCount = document.getElementById('selectedAccountsCount');
  const advSelectedGroupsCount = document.getElementById('selectedGroupsCount');
  const advBtnEvenDistribute = document.getElementById('btnWizardEvenDistribute');
  const advAssignmentReviewList = document.getElementById('assignmentReviewList');
  const advAssignmentSummaryBadges = document.getElementById('assignmentSummaryBadges');
  const advScheduleOptionCards = document.querySelectorAll('#advancedWizardContainer .schedule-option-card');
  const advScheduledLocalInput = document.getElementById('scheduledLocal');
  const advPreviewCampName = document.getElementById('previewCampName');
  const advPreviewAccounts = document.getElementById('previewAccounts');
  const advPreviewGroupsCount = document.getElementById('previewGroupsCount');
  const advPreviewDelay = document.getElementById('previewDelay');
  const advPreviewMode = document.getElementById('previewMode');
  const advPreviewContent = document.getElementById('previewContent');
  const advValidationGuardBox = document.getElementById('validationGuardBox');
  const advBtnStartCampaign = document.getElementById('btnStartCampaign');

  function goToAdvStep(step) {
    if (step < 1 || step > advTotalSteps) return;
    advCurrentStep = step;

    advStepNodes.forEach(node => {
      const s = parseInt(node.getAttribute('data-step-target'), 10);
      node.classList.remove('active', 'completed');
      if (s === advCurrentStep) node.classList.add('active');
      else if (s < advCurrentStep) node.classList.add('completed');
    });

    advPanels.forEach(panel => {
      const s = parseInt(panel.getAttribute('data-step'), 10);
      if (s === advCurrentStep) panel.classList.add('active');
      else panel.classList.remove('active');
    });

    if (advCurrentStep === 4) {
      renderAdvAssignmentReview();
    } else if (advCurrentStep === 6) {
      renderAdvPreviewAndValidate();
    }

    window.scrollTo({ top: document.querySelector('.composer-form')?.offsetTop || 0, behavior: 'smooth' });
  }

  advStepNodes.forEach(node => {
    node.addEventListener('click', () => {
      const s = parseInt(node.getAttribute('data-step-target'), 10);
      goToAdvStep(s);
    });
  });

  document.querySelectorAll('#advancedWizardContainer [data-wizard-next]').forEach(btn => {
    btn.addEventListener('click', () => goToAdvStep(advCurrentStep + 1));
  });
  document.querySelectorAll('#advancedWizardContainer [data-wizard-prev]').forEach(btn => {
    btn.addEventListener('click', () => goToAdvStep(advCurrentStep - 1));
  });

  function updateAdvAccountCount() {
    const selected = Array.from(advAccountCheckboxes).filter(cb => cb.checked);
    if (advSelectedAccountsCount) {
      advSelectedAccountsCount.textContent = `${selected.length} tài khoản đã chọn`;
    }
  }
  advAccountCheckboxes.forEach(cb => cb.addEventListener('change', updateAdvAccountCount));

  function updateAdvGroupCount() {
    const total = advGroupCheckboxes.length;
    const selected = Array.from(advGroupCheckboxes).filter(cb => cb.checked).length;
    if (advSelectedGroupsCount) {
      advSelectedGroupsCount.textContent = `${selected} / ${total} nhóm đã chọn`;
    }
  }
  advGroupCheckboxes.forEach(cb => cb.addEventListener('change', updateAdvGroupCount));

  function renderAdvAssignmentReview() {
    const accounts = Array.from(advAccountCheckboxes).filter(cb => cb.checked).map(cb => ({
      account_id: cb.value,
      name: cb.getAttribute('data-name') || cb.value
    }));
    const groups = Array.from(advGroupCheckboxes).filter(cb => cb.checked).map(cb => ({
      url: cb.value,
      assigned_account: cb.getAttribute('data-account') || ''
    }));

    if (!advAssignmentReviewList) return;
    if (!accounts.length) {
      advAssignmentReviewList.innerHTML = '<div class="empty-compact text-red" style="padding:16px">Chưa chọn tài khoản Facebook nào.</div>';
      return;
    }
    if (!groups.length) {
      advAssignmentReviewList.innerHTML = '<div class="empty-compact text-red" style="padding:16px">Chưa chọn nhóm nào.</div>';
      return;
    }

    advAssignmentReviewList.innerHTML = groups.map((g, idx) => {
      const matched = accounts.find(a => a.account_id === g.assigned_account) || accounts[idx % accounts.length];
      return `
        <div class="group-row" style="padding:10px 14px; display:flex; align-items:center; justify-content:space-between; gap:12px; border-bottom:1px solid var(--border)">
          <span style="font-family:var(--font-mono); font-size:11px; color:var(--muted); width:32px">#${idx+1}</span>
          <a href="${g.url}" target="_blank" class="group-url-link" style="flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px">${g.url}</a>
          <select class="wizard-assignment-select" data-group-url="${g.url}" style="height:32px; font-size:12px; border-radius:var(--r-sm); background:var(--surface2); color:var(--text); border:1px solid var(--border2)">
            ${accounts.map(a => `<option value="${a.account_id}" ${a.account_id === matched.account_id ? 'selected' : ''}>${a.name}</option>`).join('')}
          </select>
        </div>
      `;
    }).join('');

    updateAdvAssignmentBadges();
    advAssignmentReviewList.querySelectorAll('.wizard-assignment-select').forEach(sel => {
      sel.addEventListener('change', updateAdvAssignmentBadges);
    });
  }

  function updateAdvAssignmentBadges() {
    const accounts = Array.from(advAccountCheckboxes).filter(cb => cb.checked).map(cb => ({
      account_id: cb.value,
      name: cb.getAttribute('data-name') || cb.value
    }));
    const selects = advAssignmentReviewList?.querySelectorAll('.wizard-assignment-select') || [];
    const counts = {};
    accounts.forEach(a => counts[a.account_id] = 0);
    selects.forEach(sel => { counts[sel.value] = (counts[sel.value] || 0) + 1; });

    if (advAssignmentSummaryBadges) {
      advAssignmentSummaryBadges.innerHTML = accounts.map(a => `
        <span class="badge blue" style="font-size:12px">
          ${a.name}: <b>${counts[a.account_id] || 0}</b> nhóm
        </span>
      `).join('');
    }
  }

  advBtnEvenDistribute?.addEventListener('click', () => {
    const accounts = Array.from(advAccountCheckboxes).filter(cb => cb.checked).map(cb => ({
      account_id: cb.value
    }));
    const selects = advAssignmentReviewList?.querySelectorAll('.wizard-assignment-select') || [];
    if (!accounts.length || !selects.length) return;
    selects.forEach((sel, idx) => {
      sel.value = accounts[idx % accounts.length].account_id;
    });
    updateAdvAssignmentBadges();
    window.showToast?.('Đã chia đều danh sách nhóm cho các tài khoản!', 'success');
  });

  advScheduleOptionCards.forEach(card => {
    card.addEventListener('click', () => {
      advScheduleOptionCards.forEach(c => c.classList.remove('selected'));
      card.classList.add('selected');
      const action = card.getAttribute('data-action');
      if (campaignActionHidden) campaignActionHidden.value = action;
      const field = document.getElementById('wizardScheduleDateField');
      if (field) field.style.display = (action === 'schedule') ? 'block' : 'none';
    });
  });

  advScheduledLocalInput?.addEventListener('change', () => {
    if (advScheduledLocalInput.value && scheduledUtcHidden) {
      const dt = new Date(advScheduledLocalInput.value);
      scheduledUtcHidden.value = dt.toISOString().slice(0, 19);
    }
  });

  function renderAdvPreviewAndValidate() {
    const name = (advCampaignName?.value || '').trim();
    const content = (advPostContent?.value || '').trim();
    const minDelay = advMinDelay?.value || 1;
    const maxDelay = advMaxDelay?.value || 3;
    const accounts = Array.from(advAccountCheckboxes).filter(cb => cb.checked);
    const groups = Array.from(advGroupCheckboxes).filter(cb => cb.checked);

    if (advPreviewCampName) advPreviewCampName.textContent = name || '(Chưa đặt tên)';
    if (advPreviewAccounts) advPreviewAccounts.textContent = accounts.map(cb => cb.getAttribute('data-name') || cb.value).join(', ') || 'Chưa chọn tài khoản';
    if (advPreviewGroupsCount) advPreviewGroupsCount.textContent = `${groups.length} Groups Facebook`;
    if (advPreviewDelay) advPreviewDelay.textContent = `${minDelay} – ${maxDelay} phút`;
    if (advPreviewContent) advPreviewContent.textContent = content || '(Chưa nhập nội dung bài đăng)';

    const checks = [
      { key: 'name', valid: Boolean(name), step: 1, failMsg: 'Chưa có tên chiến dịch.' },
      { key: 'content', valid: Boolean(content), step: 1, failMsg: 'Nội dung bài đăng đang để trống.' },
      { key: 'accounts', valid: accounts.length > 0, step: 2, failMsg: 'Chưa chọn tài khoản Facebook nào.' },
      { key: 'groups', valid: groups.length > 0, step: 3, failMsg: 'Chưa chọn nhóm mục tiêu nào.' },
    ];
    const allValid = checks.every(c => c.valid);

    if (advValidationGuardBox) {
      if (allValid) {
        advValidationGuardBox.className = 'validation-guard-box valid';
        advValidationGuardBox.innerHTML = `
          <strong style="display:flex; align-items:center; gap:8px; font-size:var(--text-sm)">
            <svg width="18" height="18" fill="currentColor" viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-2 15l-5-5 1.41-1.41L10 14.17l7.59-7.59L19 8l-9 9z"/></svg>
            Tất cả điều kiện hợp lệ! Chiến dịch đã sẵn sàng khởi chạy.
          </strong>
        `;
      } else {
        advValidationGuardBox.className = 'validation-guard-box invalid';
        const failed = checks.filter(c => !c.valid);
        advValidationGuardBox.innerHTML = `
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

    if (advBtnStartCampaign) {
      advBtnStartCampaign.disabled = !allValid;
    }
  }

  window.wizardGoToStep = goToAdvStep;

  // =========================================================
  // 5. INITIALIZATION ON PAGE LOAD
  // =========================================================
  loadCampaignPrefs();
  switchMode('quick');
  updateQuickStep2();
  updateAdvAccountCount();
  updateAdvGroupCount();
});

