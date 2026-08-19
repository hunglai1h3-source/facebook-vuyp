# FB POST PRO — Render + Chrome Extension

## Kiến trúc

- **Render Web Service**: Flask UI/API, tài khoản FB POST PRO, chiến dịch, lịch sử, pairing.
- **Render PostgreSQL**: tài khoản người dùng.
- **Persistent Disk**: bài viết, danh sách Group, ảnh, job và device state.
- **FB POST PRO Connector (Chrome Extension)**: chạy trên Chrome của khách, dùng chính Facebook đang đăng nhập trên `facebook.com`.
- Server **không nhận mật khẩu Facebook và không nhận giá trị cookie Facebook**. Extension chỉ gửi boolean `facebook_logged_in`.

## Trải nghiệm khách

1. Khách đăng nhập Facebook bình thường trên Chrome.
2. Khách cài `FB POST PRO Connector`.
3. Vào **Cài đặt** trên FB POST PRO → **Tạo mã liên kết**.
4. Mở extension → nhập URL Render + mã liên kết.
5. Khi trạng thái xanh, khách tạo bài và bấm **CHẠY CHIẾN DỊCH**.
6. Extension mở các tab Facebook ở nền, thực hiện bài đăng và cập nhật tiến độ lên website.

> Chrome của khách phải đang chạy trong lúc chiến dịch hoạt động. Nếu Facebook yêu cầu đăng nhập/checkpoint, extension sẽ đưa tab Facebook ra trước để khách tự xử lý. Không có cơ chế bypass OTP/CAPTCHA/checkpoint.

## Chạy local

```powershell
py -m pip install -r requirements.txt
py app.py
```

Mở `http://127.0.0.1:5000`.

### Cài extension local

1. Mở `chrome://extensions`.
2. Bật **Developer mode**.
3. Bấm **Load unpacked**.
4. Chọn thư mục `extension`.
5. Đăng nhập `facebook.com` trên Chrome.
6. Vào FB POST PRO → Cài đặt → Tạo mã liên kết → nhập mã vào extension.

## Deploy Render

Repo đã có `render.yaml`.

- Web service chạy `gunicorn app:app --workers 1 --threads 8 --timeout 120`.
- Region: Singapore.
- Persistent disk: `/var/data/fbpostpro`.
- PostgreSQL được tạo cùng Blueprint.
- `ADMIN_PASSWORD` cần tự nhập trên Render khi Blueprint yêu cầu.

Có thể deploy bằng **New → Blueprint** và chọn repository chứa file này.

## Chrome Web Store

Thư mục `extension/` là source extension Manifest V3. Trước khi publish chính thức:

- Nếu dùng domain riêng thay vì `*.onrender.com`, thêm domain đó vào `host_permissions` và `content_scripts.matches` trong `extension/manifest.json`.
- Thay icon/branding nếu cần.
- Kiểm tra chính sách Chrome Web Store và chính sách Facebook/Meta trước khi phân phối thương mại.

## Lưu ý an toàn

- Không nhập mật khẩu Facebook vào FB POST PRO.
- Không gửi cookie Facebook lên server.
- Không bypass checkpoint, OTP, CAPTCHA hoặc biện pháp bảo mật của Facebook.
- Chỉ tự động hóa các tài khoản/Group mà người dùng có quyền hợp lệ và tuân thủ quy định nền tảng.
