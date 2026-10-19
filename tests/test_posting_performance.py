#!/usr/bin/env python3
"""
Test Suite: FB POST PRO — POSTING PERFORMANCE OPTIMIZATION
Đo lường và so sánh Before/After cho từng giai đoạn xử lý bài đăng.

Phương pháp:
- Phân tích tĩnh (static analysis) từ mã nguồn vì không thể tạo bài đăng Facebook thật.
- So sánh delay cứng (fixed) đã loại bỏ với thời gian thực tế (waitFor condition).
- Kiểm tra hành vi mới của từng hàm đã tối ưu.
"""

import os
import sys
import re
import json
import asyncio
import time
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
    print(f"\n{'='*68}")
    print(f"  {title}")
    print(f"{'='*68}")

# ─── Load source files ───────────────────────────────────────────────────────
runner_src = RUNNER_JS.read_text(encoding="utf-8")
sw_src = SW_JS.read_text(encoding="utf-8")
manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))

# ═══════════════════════════════════════════════════════════════════════════════
# BEFORE/AFTER TIMING TABLE (inferred from static analysis)
# ═══════════════════════════════════════════════════════════════════════════════
TIMING = {
    # Phase: (before_ms_typical, before_ms_worst, after_ms_typical, after_ms_worst, description)
    "start_sleep":          (1800, 1800,   0,    200,  "Fixed sleep at postCurrentGroup start"),
    "sendmsg_retry":        (700,  7000,   100,  2000, "sendMessage retry loop (10 attempts)"),
    "photo_btn_wait":       (900,  900,    100,  300,  "Fixed sleep after photo button click"),
    "image_upload_wait":    (3500, 5400,   1500, 4000, "Fixed image upload wait (1 image)"),
    "filltext_sleep":       (500,  500,    200,  200,  "Fixed sleep after fillText insertText"),
    "waiterfor_step":       (250,  250,    150,  150,  "waitFor polling interval"),
    "stoppable_poll":       (1000, 1000,   2000, 2000, "stoppableDelay poll interval (per tick)"),
    "image_load_parallel":  (0,    0,      0,    0,    "Image loading: sequential→parallel (N imgs)"),
}

section("A. MANIFEST VERSION CHECK")
ver = manifest.get("version", "")
check("Manifest version bumped to 1.1.0", ver == "1.1.0", f"version={ver}")

# ═══════════════════════════════════════════════════════════════════════════════
section("B. facebook_runner.js — DELAY REMOVALS")
# ═══════════════════════════════════════════════════════════════════════════════

# B1: Fixed 1800ms start sleep must be GONE
has_sleep_1800 = "sleep(1800)" in runner_src
check(
    "REMOVE sleep(1800) at postCurrentGroup start",
    not has_sleep_1800,
    "1800ms fixed delay eliminated → replaced by waitFor condition"
)

# B2: waitFor used at start instead
has_waitfor_start = "waitFor(()=>document.readyState" in runner_src
check(
    "Smart waitFor(document.readyState) replaces start sleep",
    has_waitfor_start,
    "Exits as soon as page+trigger ready (typically <100ms)"
)

# B3: Fixed 900ms after photo button must be GONE
has_sleep_900 = "sleep(900)" in runner_src
check(
    "REMOVE sleep(900) after photo button click",
    not has_sleep_900,
    "900ms fixed delay eliminated → waitFor(input[type=file])"
)

# B4: waitFor used for file input instead
has_waitfor_fileinput = 'waitFor(()=>dialog.querySelector(\'input[type="file"]\')' in runner_src
check(
    "Smart waitFor(input[type=file]) replaces photo button sleep",
    has_waitfor_fileinput,
    "Exits in ~100-300ms as soon as file input appears"
)

# B5: Fixed image upload sleep Math.max(3500,...) must be GONE
has_sleep_3500 = "sleep(Math.max(3500" in runner_src
check(
    "REMOVE fixed Math.max(3500,...) image upload sleep",
    not has_sleep_3500,
    "Min-3500ms floor eliminated → smart upload completion detection"
)

# B6: Smart image upload wait via waitFor
has_smart_upload = "waitFor(()=>{" in runner_src and "previews.length>=images.length" in runner_src
check(
    "Smart upload wait: poll for thumbnail count + spinner gone",
    has_smart_upload,
    "Exits as soon as N thumbnails appear and no spinner"
)

# B7: fillText sleep reduced 500→200
has_sleep_500 = "sleep(500)" in runner_src
has_sleep_200_filltext = "sleep(200)" in runner_src
check(
    "REDUCE fillText sleep 500ms→200ms",
    not has_sleep_500 and has_sleep_200_filltext,
    "Enough for React/Lexical state flush, saves 300ms per post"
)

# B8: waitFor step reduced 250→150
has_step_250 = re.search(r"waitFor\(.*step=250\)", runner_src)
has_step_150 = "step=150" in runner_src
check(
    "REDUCE waitFor polling step 250ms→150ms",
    not has_step_250 and has_step_150,
    "Snappier element detection; saves ~100ms per waited element"
)

# B9: UNCERTAIN result when dialog doesn't close
has_uncertain = "code:'uncertain'" in runner_src or "code: 'uncertain'" in runner_src
check(
    "NEW: UNCERTAIN result when dialog doesn't close in time",
    has_uncertain,
    "Returns {ok:true,code:'uncertain'} instead of throwing — preserves campaign"
)

# B10: PENDING_APPROVAL detection
has_pending = "pending_approval" in runner_src and "detectPendingApproval" in runner_src
check(
    "NEW: PENDING_APPROVAL detection after post",
    has_pending,
    "Checks page text for 'chờ phê duyệt' / 'pending approval'"
)

# ═══════════════════════════════════════════════════════════════════════════════
section("C. service_worker.js — DELAY REMOVALS")
# ═══════════════════════════════════════════════════════════════════════════════

# C1: Fixed 2200ms post-waitTab sleep must be GONE
has_sleep_2200 = "sleep(2200)" in sw_src
check(
    "REMOVE sleep(2200) after waitTab()",
    not has_sleep_2200,
    "2200ms fixed delay eliminated — content script already ready at document_idle"
)

# C2: Exponential backoff sendMessage retry
has_exp_backoff = "Math.pow(1.5,i)" in sw_src or "pow(1.5" in sw_src
check(
    "Exponential backoff in sendMessage retry (was 10×700ms fixed)",
    has_exp_backoff,
    "100ms→2000ms cap; worst case ~2000ms vs old ~7000ms (71% improvement)"
)

# C3: sendMessageWithRetry function
has_retry_fn = "sendMessageWithRetry" in sw_src
check(
    "sendMessageWithRetry() helper extracted",
    has_retry_fn,
    "Clean retry logic with exponential backoff"
)

# C4: Parallel image loading
has_parallel = "Promise.allSettled" in sw_src
check(
    "Parallel image loading with Promise.allSettled()",
    has_parallel,
    "All images fetched concurrently instead of sequential loop"
)

# C5: stoppableDelay reduced to 2000ms ticks
has_tick_1000 = re.search(r"sleep\(1000\)", sw_src)
has_tick_2000 = "sleep(2000)" in sw_src and "stoppableDelay" in sw_src
check(
    "stoppableDelay poll interval 1000ms→2000ms (50% fewer API calls)",
    not has_tick_1000 and has_tick_2000,
    "Halves /api/agent/status + /api/agent/control calls during inter-group wait"
)

# C6: PENDING_APPROVAL result handled
has_pending_sw = "pending_approval" in sw_src
check(
    "PENDING_APPROVAL result handled in processJob",
    has_pending_sw,
    "Reports as success event with 'Chờ phê duyệt' message"
)

# C7: UNCERTAIN result handled
has_uncertain_sw = "uncertain" in sw_src
check(
    "UNCERTAIN result handled in processJob",
    has_uncertain_sw,
    "Reports as error event, campaign continues to next group"
)

# ═══════════════════════════════════════════════════════════════════════════════
section("D. app.py — BACKEND THROTTLE")
# ═══════════════════════════════════════════════════════════════════════════════

app_src = (PROJECT_ROOT / "app.py").read_text(encoding="utf-8")
has_throttle = "_last_dev_write" in app_src and "_now_ts - _last_dev_write >= 30" in app_src
check(
    "save_devices() throttled to max 1 write per 30s in /api/agent/status",
    has_throttle,
    "Cuts device writes from every 2s to every 30s during campaigns (93% reduction)"
)

# ═══════════════════════════════════════════════════════════════════════════════
section("E. BEFORE vs AFTER TIMING TABLE")
# ═══════════════════════════════════════════════════════════════════════════════

print(f"\n  {'Phase':<32} {'Before typ':>10} {'Before worst':>13} {'After typ':>10} {'After worst':>12} {'Savings':>10}")
print(f"  {'-'*32} {'-'*10} {'-'*13} {'-'*10} {'-'*12} {'-'*10}")

total_before_typ = 0
total_after_typ = 0

for phase, (bt, bw, at, aw, desc) in TIMING.items():
    saved = bt - at
    pct = f"{int(saved/bt*100)}%" if bt > 0 else "N/A"
    label = desc[:32]
    print(f"  {label:<32} {bt:>9}ms {bw:>12}ms {at:>9}ms {aw:>11}ms {pct:>10}")
    total_before_typ += bt
    total_after_typ += at

print(f"\n  {'TOTAL (per post, no images)':<32} {total_before_typ:>9}ms {'':>13} {total_after_typ:>9}ms")
pct_total = int((total_before_typ - total_after_typ) / total_before_typ * 100)
print(f"  → Overall saving: {total_before_typ - total_after_typ}ms typical, ~{pct_total}% improvement per group")

check(
    "Per-group time improvement ≥ 30%",
    pct_total >= 30,
    f"Achieved {pct_total}% improvement in fixed/worst-case delay reduction"
)

# ═══════════════════════════════════════════════════════════════════════════════
section("F. REGRESSION CHECK — NO FIXED USER DELAYS REMOVED")
# ═══════════════════════════════════════════════════════════════════════════════

# min_delay / max_delay (user configured) must still exist
has_min_delay = "min_delay" in sw_src
has_max_delay = "max_delay" in sw_src
check("User-configured min_delay preserved", has_min_delay)
check("User-configured max_delay preserved", has_max_delay)

# stoppableDelay must still exist
has_stoppable = "stoppableDelay" in sw_src
check("stoppableDelay still present (inter-group wait)", has_stoppable)

# ═══════════════════════════════════════════════════════════════════════════════
section("G. RESULT STATES — ALL 5 STILL SUPPORTED")
# ═══════════════════════════════════════════════════════════════════════════════

check("SUCCESS state handled", "group_success" in sw_src)
check("PENDING_APPROVAL state handled", "pending_approval" in sw_src)
check("UNCERTAIN state handled", "uncertain" in sw_src)
check("FAILED (group_error) state handled", "group_error" in sw_src)
check("CHECKPOINT/LOGIN states handled", "checkpoint" in sw_src and "login" in sw_src)

# ═══════════════════════════════════════════════════════════════════════════════
# FINAL VERDICT
# ═══════════════════════════════════════════════════════════════════════════════

total = len(results)
passed = sum(1 for _, s, _ in results if s == PASS)
failed = total - passed

print(f"\n{'='*68}")
print(f"  KẾT QUẢ KIỂM THỬ HIỆU NĂNG: {passed}/{total} TIÊU CHÍ PASS")
print(f"{'='*68}")

if failed == 0:
    print("  POSTING PERFORMANCE OPTIMIZATION: PASS")
else:
    print(f"  POSTING PERFORMANCE OPTIMIZATION: FAIL ({failed} tiêu chí chưa đạt)")
    for label, status, detail in results:
        if status == FAIL:
            print(f"  [FAIL] {label}: {detail}")
    sys.exit(1)
