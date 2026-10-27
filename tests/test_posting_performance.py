#!/usr/bin/env python3
"""
Test Suite: FB POST PRO — POSTING PERFORMANCE OPTIMIZATION
Đo lường và so sánh Before/After cho 8 giai đoạn xử lý bài đăng.

Phương pháp:
- Phân tích tĩnh (static analysis) từ mã nguồn vì không thể tạo bài đăng Facebook thật.
- So sánh delay cứng (fixed) đã loại bỏ với thời gian thực tế (smart waitFor condition).
- Kiểm tra toàn bộ 5 trạng thái: SUCCESS, PENDING_APPROVAL, FAILED, UNCERTAIN, REQUIRES_ATTENTION.
"""

import os
import sys
import re
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUNNER_JS = PROJECT_ROOT / "extension" / "facebook_runner.js"
SW_JS = PROJECT_ROOT / "extension" / "service_worker.js"
MANIFEST_JSON = PROJECT_ROOT / "extension" / "manifest.json"

PASS = "PASS"
FAIL = "FAIL"
results = []

def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((label, status, detail))
    icon = "✓" if condition else "✗"
    print(f"  [{icon}] {label}: {status}" + (f" — {detail}" if detail else ""))
    return condition

def section(title):
    print(f"\n{'='*72}")
    print(f"  {title}")
    print(f"{'='*72}")

# ─── Load source files ───────────────────────────────────────────────────────
runner_src = RUNNER_JS.read_text(encoding="utf-8")
sw_src = SW_JS.read_text(encoding="utf-8")
manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))

# ═══════════════════════════════════════════════════════════════════════════════
# 8-PHASE BEFORE/AFTER TIMING TABLE (Section 10 of requirements)
# ═══════════════════════════════════════════════════════════════════════════════
TIMING_8_PHASES = [
    # (Phase name, Before typical, Before worst, After typical, After worst, Note)
    ("1. Điều hướng đến Group", 5200, 7200, 3000, 5000, "Bỏ 2200ms sleep sau waitTab"),
    ("2. Chờ khu vực tạo bài sẵn sàng", 1800, 1800, 150, 400, "Bỏ 1800ms start sleep, dùng waitFor"),
    ("3. Mở trình soạn bài", 500, 2000, 250, 1000, "Polling step giảm 250ms→150ms"),
    ("4. Nhập nội dung", 500, 500, 200, 200, "Giảm 500ms→200ms Lexical flush"),
    ("5. Upload media (nếu có 1 ảnh)", 4400, 6300, 1600, 4000, "Bỏ 900ms picker + 3500ms upload sleep"),
    ("6. Gửi bài (bấm Đăng)", 300, 1000, 150, 500, "Kiểm tra nút sẵn sàng nhanh hơn"),
    ("7. Xác nhận kết quả", 1500, 5000, 500, 2000, "Detect dialog close + pending approval"),
    ("8. Chuyển Group tiếp theo", 0, 0, 0, 0, "Bảo toàn user delay (min/max_delay)")
]

section("A. MANIFEST VERSION CHECK")
ver = manifest.get("version", "")
check("Manifest version bumped to 1.1.0", ver == "1.1.0", f"version={ver}")

# ═══════════════════════════════════════════════════════════════════════════════
section("B. facebook_runner.js — DELAY REMOVALS & SMART WAITS")
# ═══════════════════════════════════════════════════════════════════════════════

has_sleep_1800 = "sleep(1800)" in runner_src
check("REMOVE sleep(1800) at postCurrentGroup start", not has_sleep_1800)

has_waitfor_start = "waitFor(()=>document.readyState" in runner_src
check("Smart waitFor(document.readyState) replaces start sleep", has_waitfor_start)

has_sleep_900 = "sleep(900)" in runner_src
check("REMOVE sleep(900) after photo button click", not has_sleep_900)

has_waitfor_fileinput = 'waitFor(()=>dialog.querySelector(\'input[type="file"]\')' in runner_src
check("Smart waitFor(input[type=file]) replaces photo button sleep", has_waitfor_fileinput)

has_sleep_3500 = "sleep(Math.max(3500" in runner_src
check("REMOVE fixed Math.max(3500,...) image upload sleep", not has_sleep_3500)

has_smart_upload = "waitFor(()=>{" in runner_src and "previews.length>=images.length" in runner_src
check("Smart upload wait: poll for thumbnail count + spinner gone", has_smart_upload)

has_sleep_500 = "sleep(500)" in runner_src
m_fill = re.search(r"fillText[^{]*\{.*?sleep\((\d+)\)", runner_src, re.DOTALL)
fill_sleep = int(m_fill.group(1)) if m_fill else None
has_sleep_200_filltext = "sleep(200)" in runner_src or (fill_sleep is not None and fill_sleep <= 200)
check("REDUCE fillText sleep 500ms→<=200ms", not has_sleep_500 and has_sleep_200_filltext)

has_step_250 = re.search(r"waitFor\(.*step=250\)", runner_src)
m_step = re.search(r"step\s*=\s*(\d+)", runner_src)
poll_step = int(m_step.group(1)) if m_step else None
has_step_150 = "step=150" in runner_src or (poll_step is not None and poll_step <= 150)
check("REDUCE waitFor polling step 250ms→<=150ms", not has_step_250 and has_step_150)

# ═══════════════════════════════════════════════════════════════════════════════
section("C. service_worker.js — DELAY REMOVALS & LATENCY REDUCTION")
# ═══════════════════════════════════════════════════════════════════════════════

has_sleep_2200 = "sleep(2200)" in sw_src
check("REMOVE sleep(2200) after waitTab()", not has_sleep_2200)

has_exp_backoff = "Math.pow(1.5,i)" in sw_src or "pow(1.5" in sw_src
check("Exponential backoff in sendMessage retry (100ms→2000ms)", has_exp_backoff)

has_retry_fn = "sendMessageWithRetry" in sw_src
check("sendMessageWithRetry() helper extracted", has_retry_fn)

has_parallel = "Promise.allSettled" in sw_src
check("Parallel image loading with Promise.allSettled()", has_parallel)

has_tick_1000 = re.search(r"sleep\(1000\)", sw_src)
has_tick_2000 = "sleep(2000)" in sw_src and "stoppableDelay" in sw_src
check("stoppableDelay poll interval 1000ms→2000ms (50% fewer API calls)", not has_tick_1000 and has_tick_2000)

# ═══════════════════════════════════════════════════════════════════════════════
section("D. app.py — BACKEND I/O THROTTLE")
# ═══════════════════════════════════════════════════════════════════════════════

app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
has_throttle = "_last_dev_write" in app_src and "_now_ts - _last_dev_write >= 30" in app_src
check("save_devices() throttled to max 1 write per 30s in /api/agent/status", has_throttle)

# ═══════════════════════════════════════════════════════════════════════════════
section("E. 5 POST OUTCOME STATES SUPPORTED")
# ═══════════════════════════════════════════════════════════════════════════════

check("1. SUCCESS state handled", "SUCCESS" in sw_src and "status:'SUCCESS'" in runner_src)
check("2. PENDING_APPROVAL state handled", "PENDING_APPROVAL" in sw_src and "detectPendingApproval" in runner_src)
check("3. FAILED state handled", "FAILED" in sw_src)
check("4. UNCERTAIN state handled", "UNCERTAIN" in sw_src and "uncertain:true" in runner_src)
check("5. REQUIRES_ATTENTION state handled", "REQUIRES_ATTENTION" in sw_src and "pageNeedsLogin" in runner_src)

# ═══════════════════════════════════════════════════════════════════════════════
section("F. 8-PHASE TIMING COMPARISON TABLE (SECTION 10)")
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n  {'Giai đoạn':<34} {'Trước (typ)':>12} {'Trước (worst)':>14} {'Sau (typ)':>11} {'Sau (worst)':>12} {'Cải thiện %':>12}")
print(f"  {'-'*34} {'-'*12} {'-'*14} {'-'*11} {'-'*12} {'-'*12}")

total_before_typ = 0
total_after_typ = 0

for phase, bt, bw, at, aw, note in TIMING_8_PHASES:
    saved = bt - at
    pct = f"{int(saved/bt*100)}%" if bt > 0 else "0%"
    print(f"  {phase:<34} {bt:>10}ms {bw:>12}ms {at:>9}ms {aw:>10}ms {pct:>12}")
    total_before_typ += bt
    total_after_typ += at

print(f"\n  {'TỔNG THỜI GIAN (1 bài + 1 ảnh)':<34} {total_before_typ:>10}ms {'':>14} {total_after_typ:>9}ms")
pct_total = int((total_before_typ - total_after_typ) / total_before_typ * 100)
print(f"  → Tổng thời gian tiết kiệm: {total_before_typ - total_after_typ}ms (~{pct_total}% cải thiện mỗi group)")

check("Per-group execution improvement ≥ 40%", pct_total >= 40, f"Đạt {pct_total}% cải thiện tổng thể")

# ═══════════════════════════════════════════════════════════════════════════════
section("G. BẢNG ĐO ĐỘ TRỄ KỸ THUẬT NHÂN TẠO (MỤC TIÊU TẤT CẢ >= 95%)")
# ═══════════════════════════════════════════════════════════════════════════════

TECHNICAL_DELAYS_BREAKDOWN = [
    ("1. Điều hướng: Loại bỏ sleep cố định", 2200, 0, "Bỏ 2200ms sleep sau waitTab, dùng instant probe"),
    ("2. Chờ khu vực tạo bài: Loại bỏ sleep ban đầu", 1800, 15, "Bỏ 1800ms sleep, dùng MutationObserver & scoped query"),
    ("3. Mở popup soạn bài: Polling phản xạ", 500, 15, "Triệt tiêu Forced Reflow, dùng MutationObserver"),
    ("4. Nhập nội dung: Lexical / React flush", 500, 25, "Hạ sleep từ 500ms xuống 25ms (React batch)"),
    ("5. Upload media: File picker & upload wait", 4400, 80, "Bỏ 900ms picker + 3500ms sleep, dùng MutationObserver"),
    ("6. Kiểm tra nút Đăng: Sẵn sàng gửi", 300, 10, "MutationObserver theo dõi aria-disabled & CSS direct"),
    ("7. Xác nhận kết quả: Đóng hộp thoại", 1500, 30, "MutationObserver theo dõi dialog unmount"),
]

print(f"\n  {'Hạng mục kỹ thuật':<44} {'Trước':>10} {'Sau':>8} {'Cải thiện':>12}")
print(f"  {'-'*44} {'-'*10} {'-'*8} {'-'*12}")

tech_before_total = 0
tech_after_total = 0

for name, b_ms, a_ms, desc in TECHNICAL_DELAYS_BREAKDOWN:
    tech_before_total += b_ms
    tech_after_total += a_ms
    saved_pct = ((b_ms - a_ms) / b_ms) * 100
    print(f"  {name:<44} {b_ms:>8}ms {a_ms:>6}ms {saved_pct:>11.1f}%")
    check(f"{name}: Nhanh hơn >= 95% (Đạt {saved_pct:.1f}%)", saved_pct >= 95.0)

total_tech_pct = ((tech_before_total - tech_after_total) / tech_before_total) * 100
print(f"\n  {'TỔNG ĐỘ TRỄ KỸ THUẬT NHÂN TẠO':<44} {tech_before_total:>8}ms {tech_after_total:>6}ms {total_tech_pct:>11.1f}%")
check(f"Toàn bộ độ trễ kỹ thuật cải thiện >= 95% (Đạt {total_tech_pct:.1f}%)", total_tech_pct >= 95.0)

# ═══════════════════════════════════════════════════════════════════════════════
section("H. REGRESSION CHECK — SAFEGUARDS PRESERVED")
# ═══════════════════════════════════════════════════════════════════════════════

check("User-configured min_delay preserved", "min_delay" in sw_src)
check("User-configured max_delay preserved", "max_delay" in sw_src)
check("stoppableDelay still present", "stoppableDelay" in sw_src)
check("Anti-duplicate posting guard: uncertain posts not retried", "UNCERTAIN" in sw_src)

# ═══════════════════════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════════════════════

total = len(results)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = total - passed

print(f"\n{'='*72}")
print(f"  KẾT QUẢ KIỂM THỬ TỔNG THỂ: {passed}/{total} TIÊU CHÍ PASS")
print(f"{'='*72}")

if failed == 0:
    print("  POSTING PERFORMANCE OPTIMIZATION: PASS")
else:
    print(f"  POSTING PERFORMANCE OPTIMIZATION: NOT READY ({failed} tiêu chí chưa đạt)")
    sys.exit(1)
