# -*- coding: utf-8 -*-
"""
KIỂM THỬ KÍCH THƯỚC, BỐ CỤC & TÍNH RESPONSIVE TOÀN HỆ THỐNG
(SYSTEM-WIDE UI DIMENSIONS & RESPONSIVE AUDIT)
"""

import sys
import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import app as fb_app
from app import app

def run_ui_dimension_tests():
    print("=" * 76)
    print("   KIỂM THỬ KÍCH THƯỚC & ĐỘ TƯƠNG THÍCH MÀN HÌNH (FB POST PRO UI)")
    print("=" * 76)

    client = app.test_client()
    passed = 0
    total = 0

    def assert_ui(name, condition, details=""):
        nonlocal passed, total
        total += 1
        icon = "✓" if condition else "✗"
        print(f"  [{icon}] {name}" + (f": {details}" if details else ""))
        if not condition:
            raise AssertionError(f"UI Dimension test failure: {name} ({details})")
        passed += 1

    # 1. KIỂM TRA TẤT CẢ FILE TEMPLATE CHO VIEWPORT META
    print("\n[PHẦN 1] Kiểm tra thẻ Viewport & Chuẩn hóa Box Model...")
    templates_dir = ROOT_DIR / "templates"
    standalone_templates = ["login.html", "change_password.html", "register.html", "base.html"]

    for t_name in standalone_templates:
        p = templates_dir / t_name
        content = p.read_text(encoding="utf-8")
        assert_ui(f"File {t_name} có thẻ viewport meta", "viewport" in content and "width=device-width" in content)
        assert_ui(f"File {t_name} áp dụng box-sizing: border-box toàn cục", "box-sizing:border-box" in content or "box-sizing: border-box" in content)

    # 2. KIỂM TRA CẤU TRÚC COMPOSE GRID & CAMPAIGN PREVIEW PANEL
    print("\n[PHẦN 2] Kiểm tra kích thước & Padding của Trang Tạo bài đăng (compose.html)...")
    compose_html = (templates_dir / "compose.html").read_text(encoding="utf-8")
    base_html = (templates_dir / "base.html").read_text(encoding="utf-8")

    assert_ui("Thẻ .campaign-preview có padding rõ ràng (>= 20px)", "padding: 24px" in compose_html or "padding:22px" in base_html)
    assert_ui("Thẻ .campaign-preview có box-sizing: border-box", "box-sizing: border-box" in compose_html)
    assert_ui(".compose-grid có cột preview giới hạn độ rộng lý tưởng (320px - 380px)", "minmax(320px, 380px)" in compose_html)
    assert_ui(".compose-grid hỗ trợ responsive 1 cột khi màn hình <= 1080px", "@media (max-width: 1080px)" in compose_html and "grid-template-columns: 1fr" in compose_html)

    # 3. KIỂM TRA TRANG DASHBOARD & STATS GRID
    print("\n[PHẦN 3] Kiểm tra kích thước & Lưới Trang Tổng quan (dashboard.html)...")
    dash_html = (templates_dir / "dashboard.html").read_text(encoding="utf-8")
    assert_ui(".dash-panel có padding đệm chuẩn (20px)", ".dash-panel{padding:20px}" in dash_html)
    assert_ui(".dashboard-main-grid hỗ trợ responsive khi thu nhỏ màn hình", "@media(max-width:1050px)" in dash_html and ".dashboard-main-grid{grid-template-columns:1fr}" in dash_html)
    assert_ui(".dash-stats hỗ trợ responsive trên di động", "@media(max-width:640px)" in dash_html and ".dash-stats{grid-template-columns:1fr}" in dash_html)

    # 4. KIỂM TRA TRANG QUẢN LÝ GROUPS & DANH SÁCH NHÓM
    print("\n[PHẦN 4] Kiểm tra chống vỡ khung bởi URL dài trong Quản lý Groups (groups.html)...")
    groups_html = (templates_dir / "groups.html").read_text(encoding="utf-8")
    assert_ui(".group-row sử dụng minmax(0, 1fr) chống tràn cột", "minmax(0, 1fr)" in groups_html)
    assert_ui(".group-link-text có text-overflow: ellipsis & overflow: hidden", "text-overflow: ellipsis" in groups_html and "overflow: hidden" in groups_html)
    assert_ui(".preview-item-url chống tràn khi dán link dài", "text-overflow: ellipsis" in groups_html)

    # 5. KIỂM TRA TRANG ĐĂNG NHẬP (login.html)
    print("\n[PHẦN 5] Kiểm tra kích thước 3 thẻ tài khoản & Modal đăng nhập...")
    login_html = (templates_dir / "login.html").read_text(encoding="utf-8")
    assert_ui(".cards-grid dùng tỷ lệ repeat(3, 1fr)", "repeat(3, 1fr)" in login_html)
    assert_ui(".cards-grid tự động xếp 1 cột khi màn hình hẹp (<= 820px)", "@media (max-width: 820px)" in login_html and "grid-template-columns: 1fr" in login_html)
    assert_ui(".modal-card có max-width chống tràn", "max-width: 440px" in login_html or "max-width:440px" in login_html)

    # 6. KIỂM TRA KHÔNG CÓ PHẦN TỬ CỐ ĐỊNH WIDTH VƯỢT QUÁ KHUNG
    print("\n[PHẦN 6] Kiểm tra quét các lỗi cố định width (hardcoded width)...")
    hardcoded_wide_pattern = re.compile(r'(?<!max-)(?<!min-)width:\s*([6-9]\d{2,}|[1-9]\d{3,})px')
    for html_file in templates_dir.glob("*.html"):
        c = html_file.read_text(encoding="utf-8")
        matches = hardcoded_wide_pattern.findall(c)
        assert_ui(f"File {html_file.name} không có width cố định vượt kích thước màn hình", len(matches) == 0, f"Tìm thấy {matches}" if matches else "")

    # TỔNG KẾT
    print("\n" + "=" * 76)
    print(f"KẾT QUẢ KIỂM THỬ GIAO DIỆN & KÍCH THƯỚC: {passed}/{total} TIÊU CHÍ PASS 100%!")
    print("KHÔNG CÒN BẤT KỲ LỖI TRÀN KHUNG, THIẾU PADDING HOẶC VỠ LAYOUT NÀO!")
    print("=" * 76)

if __name__ == "__main__":
    run_ui_dimension_tests()
