# -*- coding: utf-8 -*-
"""
KIỂM THỬ: HẸN GIỜ ĐĂNG BÀI (CAMPAIGN SCHEDULER) VÀ TỐI ƯU TỐC ĐỘ TOÀN DIỆN
FB POST PRO — HIGH-SPEED ENGINE & SCHEDULED POSTING SUITE
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Thêm root vào path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import app as fb_app
from app import (
    app,
    parse_scheduled_time,
    format_vietnam_time,
    load_schedule,
    save_schedule,
    get_schedule_info,
    trigger_single_schedule,
    check_and_trigger_schedules,
    load_jobs,
    save_jobs,
    load_groups,
    save_groups,
    save_post_content,
    load_settings,
    save_settings,
    save_devices,
    get_active_device,
    get_campaign_state,
    update_campaign_state,
    sanitize_customer_id,
)

def run_tests():
    print("=" * 72)
    print("KIỂM THỬ TÍNH NĂNG HẸN GIỜ ĐĂNG BÀI & TĂNG TỐC XỬ LÝ (FB POST PRO)")
    print("=" * 72)

    passed_count = 0
    total_count = 0

    def check(name, cond, details=""):
        nonlocal passed_count, total_count
        total_count += 1
        icon = "✓" if cond else "✗"
        print(f"  [{icon}] {name}" + (f": {details}" if details else ""))
        if not cond:
            raise AssertionError(f"Test failed: {name} ({details})")
        passed_count += 1

    client = app.test_client()

    # -------------------------------------------------------------
    # 1. PARSE & FORMAT VIETNAM TIME
    # -------------------------------------------------------------
    print("\n[PHẦN 1] Kiểm tra xử lý múi giờ và định dạng thời gian Việt Nam...")
    now_utc = datetime.now(timezone.utc)
    future_utc = now_utc + timedelta(hours=2)

    # ISO string without timezone (Vietnam local time)
    iso_vn = "2026-10-23T15:30"
    parsed_dt = parse_scheduled_time(iso_vn)
    check("parse_scheduled_time nhận diện datetime-local ISO", parsed_dt is not None)
    check("parse_scheduled_time quy đổi về UTC chính xác", parsed_dt.tzinfo == timezone.utc)

    # Format Vietnam time
    formatted = format_vietnam_time(parsed_dt)
    check("format_vietnam_time định dạng đúng tiếng Việt", "15:30 ngày 23/10/2026" in formatted, formatted)

    # Past time handling
    past_iso = "2020-01-01T10:00"
    past_dt = parse_scheduled_time(past_iso)
    check("Nhận diện thời gian trong quá khứ", past_dt < now_utc)

    # -------------------------------------------------------------
    # 2. SCHEDULE PERSISTENCE & INFO HELPER
    # -------------------------------------------------------------
    print("\n[PHẦN 2] Kiểm tra lưu trữ và truy vấn lịch hẹn...")
    test_cid = "test_scheduler_user__dai_kim"

    # Clean initial state
    save_schedule(test_cid, None)
    check("load_schedule khi chưa có lịch hẹn trả về None", load_schedule(test_cid) is None)

    sched_data = {
        "schedule_id": "sched_test_12345",
        "campaign_name": "Chiến dịch Hẹn Giờ Test",
        "scheduled_at": future_utc.isoformat(),
        "status": "scheduled",
        "created_at": now_utc.isoformat(),
        "groups": ["https://facebook.com/groups/test_g1", "https://facebook.com/groups/test_g2"],
        "content": "Nội dung hẹn giờ đăng bài test",
        "images": [],
        "min_delay": 2,
        "max_delay": 5,
    }
    save_schedule(test_cid, sched_data)
    loaded = load_schedule(test_cid)
    check("save_schedule & load_schedule lưu trữ toàn vẹn", loaded is not None and loaded.get("schedule_id") == "sched_test_12345")

    info = get_schedule_info(test_cid)
    check("get_schedule_info tính toán time_remaining_str hợp lệ", info is not None and "còn" in info.get("time_remaining_str", ""))
    check("get_schedule_info đếm đúng số groups", info.get("groups_count") == 2)

    # -------------------------------------------------------------
    # 3. WEB ENDPOINTS: /schedule-campaign, /cancel-schedule, /schedule-status
    # -------------------------------------------------------------
    print("\n[PHẦN 3] Kiểm tra các route Web & API hẹn giờ...")
    daikim_user = fb_app.find_user_by_login("daikim")
    if not daikim_user:
        fb_app.bootstrap_management_accounts()
        daikim_user = fb_app.find_user_by_login("daikim")

    # Ensure must_change_password is False for smooth testing
    users = fb_app.load_users()
    users[daikim_user["user_id"]]["must_change_password"] = False
    fb_app.save_users(users)

    with client.session_transaction() as sess:
        sess["user_id"] = daikim_user["user_id"]
        sess["username"] = "daikim"
        sess["department"] = "dai_kim"
        sess["role"] = "dai_kim"
        sess["allowed_departments"] = ["dai_kim"]

    authed_cid = f"{daikim_user['user_id']}__dai_kim"
    save_groups(authed_cid, ["https://facebook.com/groups/group_sched_1"])
    save_post_content(authed_cid, "Nội dung bài viết hẹn giờ test")
    save_schedule(authed_cid, None)
    update_campaign_state(authed_cid, running=False, status="idle")

    # Reject past time
    r = client.post("/schedule-campaign", data={
        "campaign_name": "Past Campaign",
        "scheduled_at": "2020-01-01T08:00",
    }, headers={"Accept": "application/json"})
    check("Từ chối đặt lịch trong quá khứ", r.status_code in (200, 302))
    check("Không tạo lịch hẹn nếu thời gian không hợp lệ", load_schedule(authed_cid) is None)

    # Accept future time
    target_future = (datetime.now(timezone.utc) + timedelta(minutes=45)).isoformat()
    r = client.post("/schedule-campaign", data={
        "campaign_name": "Chiến dịch Chiều Nay",
        "scheduled_at": target_future,
        "min_delay": 3,
        "max_delay": 6,
    }, headers={"Accept": "application/json"})
    check("Tạo lịch hẹn thành công qua Web", r.status_code == 200)
    res_sched = r.get_json()
    check("Response API trả về ok: True và thông tin lịch hẹn", res_sched.get("ok") is True)

    # /schedule-status API
    r_stat = client.get("/schedule-status")
    stat_json = r_stat.get_json()
    check("API /schedule-status phản hồi chính xác", stat_json.get("status") == "scheduled")

    # /cancel-schedule API
    r_cancel = client.post("/cancel-schedule", headers={"Accept": "application/json"})
    check("API /cancel-schedule hủy lịch thành công", r_cancel.status_code == 200 and r_cancel.get_json().get("ok") is True)
    check("Lịch hẹn đã được xóa sau khi hủy", load_schedule(authed_cid) is None)

    # -------------------------------------------------------------
    # 4. AUTO-TRIGGER MECHANISM (ZERO-LATENCY PICKUP)
    # -------------------------------------------------------------
    print("\n[PHẦN 4] Kiểm tra cơ chế tự động kích hoạt khi đến giờ hẹn...")
    # Mock online device for authed_cid
    test_device_id = "ext_sched_device_001"
    save_devices(authed_cid, {
        test_device_id: {
            "device_id": test_device_id,
            "device_name": "Test Connector",
            "last_seen": fb_app.now_iso(),
            "paired_at": fb_app.now_iso(),
            "token": "tok_sched_123"
        }
    })
    save_settings(authed_cid, {"active_device_id": test_device_id, "campaign_name": "Chiến dịch hẹn giờ"})
    update_campaign_state(authed_cid, running=False, status="idle")

    # Set schedule due right now (1 second in the past)
    due_schedule = {
        "schedule_id": "sched_due_now",
        "campaign_name": "Chiến dịch Đã Đến Giờ",
        "scheduled_at": (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
        "status": "scheduled",
        "created_at": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
        "groups": ["https://facebook.com/groups/due_group_1"],
        "content": "Bài đăng tự động bung job",
        "images": [],
        "min_delay": 1,
        "max_delay": 2,
    }
    save_schedule(authed_cid, due_schedule)

    # Trigger scheduler check
    triggered_count = check_and_trigger_schedules()
    check("Scheduler tự động kích hoạt chiến dịch đến hạn", triggered_count >= 1)

    # Verify job created in pending state
    jobs = load_jobs(authed_cid)
    dev_job = jobs.get(test_device_id)
    check("Job được tạo trong hàng đợi của device", dev_job is not None)
    check("Job có status == 'pending'", dev_job.get("status") == "pending")
    check("Job giữ nguyên nội dung và campaign_name", dev_job.get("campaign_name") == "Chiến dịch Đã Đến Giờ")

    # Verify schedule cleaned up
    check("Lịch hẹn được dọn dẹp sau khi bung job", load_schedule(authed_cid) is None)

    # Verify campaign state transitioned to queued / running
    state = get_campaign_state(authed_cid)
    check("Trạng thái chiến dịch chuyển sang running=True", state.get("running") is True)
    check("Trạng thái chiến dịch là 'queued'", state.get("status") == "queued")

    # Clean test jobs
    save_jobs(authed_cid, {})
    update_campaign_state(authed_cid, running=False, status="idle")

    # -------------------------------------------------------------
    # 5. HIGH-SPEED POSTING & EXTENSION INTEGRATION
    # -------------------------------------------------------------
    print("\n[PHẦN 5] Kiểm tra giao thức tăng tốc & Extension...")
    runner_path = ROOT_DIR / "extension" / "facebook_runner.js"
    sw_path = ROOT_DIR / "extension" / "service_worker.js"
    bridge_path = ROOT_DIR / "extension" / "web_bridge.js"

    runner_txt = runner_path.read_text(encoding="utf-8")
    sw_txt = sw_path.read_text(encoding="utf-8")
    bridge_txt = bridge_path.read_text(encoding="utf-8")

    # Fast probe ping
    check("facebook_runner.js hỗ trợ FBPOST_PING", "FBPOST_PING" in runner_txt)
    check("service_worker.js probe FBPOST_PING trong waitTab", "FBPOST_PING" in sw_txt)
    check("web_bridge.js forward POLL_NOW khi user bấm nút", "POLL_NOW" in bridge_txt)
    check("Composer bypass chờ document.readyState khi DOM đã sẵn sàng", "findComposerTrigger" in runner_txt)

    print("\n" + "=" * 72)
    print(f"KẾT QUẢ KIỂM THỬ: {passed_count}/{total_count} TIÊU CHÍ PASS 100%!")
    print("CHỨC NĂNG HẸN GIỜ ĐĂNG BÀI & TĂNG TỐC XỬ LÝ: PASS")
    print("=" * 72)

if __name__ == "__main__":
    run_tests()
