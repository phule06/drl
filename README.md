# Theo dõi hoạt động IUH bằng GitHub Actions

Workflow mở trang Đoàn TN IUH, dùng Gemini đọc CAPTCHA, đăng nhập, kiểm tra nút **Đăng ký** còn hoạt động hay không, rồi gửi thông tin hoạt động kèm nội dung popup chi tiết qua Telegram. Chương trình chỉ theo dõi và thông báo; không tự đăng ký.

## Chuẩn bị trên GitHub

1. Tạo một **private repository** và tải các tệp trong thư mục này lên nhánh mặc định. Đừng tải `.venv`, `.env` hoặc tệp trạng thái lên GitHub. GitHub Actions phải được bật cho repository.
2. Vào **Settings → Secrets and variables → Actions → Repository secrets**, thêm đủ năm secrets:

   | Secret | Giá trị |
   |---|---|
   | `IUH_MSSV` | Mã số sinh viên IUH |
   | `IUH_PASSWORD` | Mật khẩu IUH |
   | `GEMINI_API_KEY` | API key từ [Google AI Studio](https://aistudio.google.com/apikey) |
   | `TELEGRAM_BOT_TOKEN` | Token bot từ [BotFather](https://t.me/BotFather) |
   | `TELEGRAM_CHAT_ID` | Chat ID cá nhân hoặc nhóm nhận tin |

   `GITHUB_TOKEN` được GitHub cấp tự động; không cần tự tạo secret. Không ghi mật khẩu, token hay API key vào code, issue hoặc chat.

3. Mở bot trên Telegram và gửi `/start` trước. Để lấy Chat ID, gọi `getUpdates` của Telegram từ trình duyệt hoặc PowerShell, rồi lấy `result[].message.chat.id`. Với chat nhóm, thêm bot vào nhóm và gửi một tin trong nhóm trước khi gọi `getUpdates`. Nếu bot cũ đã có webhook và `getUpdates` không trả dữ liệu, lấy ID từ cấu hình cũ hoặc xóa webhook cũ.

## Chạy thử lần đầu trên GitHub

Vào **Actions → IUH activity monitor → Run workflow**. Lượt chạy thủ công sẽ gửi Telegram một tin tổng kết, rồi gửi riêng từng hoạt động được nhận diện là mới và đang cho đăng ký. Mở log của lượt chạy để xem số hoạt động và lỗi nếu có. Lượt chạy thành công sẽ lưu danh sách mã băm hoạt động trong artifact `iuh-monitor-state`, giúp các lượt sau không gửi lặp.

Lịch tự động có sẵn nhưng **chưa bật**. Sau khi xác nhận lượt chạy thử nhận diện đúng, vào **Settings → Secrets and variables → Actions → Variables**, tạo `ENABLE_SCHEDULE` với giá trị `true`. Workflow sẽ kiểm tra mỗi 30 phút **liên tục 24/7**. Tắt lịch bằng cách đổi giá trị thành `false` hoặc xóa variable. GitHub có thể chạy lịch trễ hoặc bỏ một lượt khi tải cao.

Nếu muốn chạy thử lại ngay, bấm **Run workflow** lần nữa. Tin tổng kết vẫn được gửi, còn hoạt động đã thấy sẽ không được thông báo lặp. Artifact được giữ 90 ngày và được làm mới sau mỗi lượt chạy thành công. Nếu không có lượt thành công trong 90 ngày, lịch sử có thể mất và các hoạt động còn mở sẽ được gửi lại.

## Cách nhận diện và nội dung thông báo

Chương trình đọc bảng `#tab1 table.tblDoanvien` trên trang IUH và kiểm tra trực tiếp cột **Đăng ký** (cột cuối):
- **Điều kiện thông báo:** Có nút hoặc link **Đăng ký** đang kích hoạt (không bị vô hiệu hóa / disabled, không phải trạng thái "Đã đăng ký", "Hủy", hay text "Đã đủ số lượng").
- **Nội dung gửi về Telegram:** Gồm Tên hoạt động, Điểm tích lũy, toàn bộ nội dung trong popup chi tiết modal (Thời gian, Địa điểm, Đơn vị tổ chức, Người phụ trách, Mục điểm...) và link website IUH.
- **Chống trùng lặp:** Mỗi hoạt động được băm (hash SHA-256) từ Tên, Học kỳ và Chi tiết để lưu vào lịch sử, tránh gửi lặp qua các lượt chạy.

Giao diện IUH và khả năng đọc CAPTCHA chưa được kiểm chứng trên GitHub runner; lượt chạy đầu tiên của bạn sẽ xác nhận điều đó. Nếu log báo `Check failed`, kiểm tra secrets, giới hạn Gemini API, khả năng truy cập IUH từ GitHub và các selector trong `monitor.py`. Code chỉ in loại lỗi, không in URL có token hoặc nội dung CAPTCHA. Nếu trang IUH đổi cấu trúc, cần sửa phần `extract_activities` và `parse_activity`.

## Tệp chính

- `.github/workflows/iuh-monitor.yml`: nút chạy thủ công, lịch và cấu hình runner.
- `monitor.py`: trình duyệt, Gemini, lọc hoạt động, Telegram và lịch sử thông báo.
- `requirements.txt`: thư viện Python.

Tài liệu nền: [GitHub Actions schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets), [Playwright trên GitHub Actions](https://playwright.dev/python/docs/ci), [Gemini image understanding](https://ai.google.dev/gemini-api/docs/generate-content/image-understanding), [Telegram sendMessage](https://core.telegram.org/bots/api#sendmessage).
