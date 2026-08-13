# Jarvis

Jarvis là trợ lý AI chạy cục bộ trên Linux, nhận lệnh từ Terminal, giao diện GTK và Discord. Jarvis có thể quản lý file/cửa sổ, nhắc việc, điều khiển YouTube, mở Zalo, tóm tắt các hội thoại Zalo mang nhãn **Công việc**, và dùng mô hình Ollama làm AI cục bộ.

> **Phạm vi hỗ trợ:** hướng dẫn này dành cho Ubuntu/Linux chạy X11 hoặc XWayland và `systemd --user`.

## 1. Kiến trúc vận hành

- `jarvis.py`: tiến trình nền sở hữu Discord, IPC, lịch nhắc và TTS worker.
- `jarvis_gui.py`: giao diện GTK kết nối đến tiến trình nền qua Unix socket.
- `jarvis_core/`: lưu trữ hội thoại, bộ nhớ, bảo mật, AI cục bộ và quản lý cửa sổ.
- `tts/VieNeu-TTS`: submodule TTS tiếng Việt, có môi trường Python riêng.
- Chrome YouTube/Zalo: profile riêng tại `~/.config/jarvis-chrome`.
- Chrome DevTools: chỉ mở trên loopback `127.0.0.1:9223`.

Jarvis giữ khóa một tiến trình. Không chạy đồng thời `jarvis.py` trong Terminal và `jarvis.service`.

## 2. Yêu cầu hệ thống

### Bắt buộc

- Ubuntu/Linux có `systemd --user`.
- Python 3 có module `venv` và `pip`.
- Node.js cùng `npx` để chạy `chrome-devtools-mcp`.
- Google Chrome.
- Các công cụ desktop: `wmctrl`, `xprop`, `lsof`.
- Git có hỗ trợ submodule.

Ví dụ trên Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nodejs npm \
  wmctrl x11-utils lsof git
```

Cài Google Chrome bằng gói chính thức của Google, sau đó kiểm tra:

```bash
google-chrome --version
npx --version
wmctrl --version
xprop -version
lsof -v
```

### Tùy chọn

- GUI GTK 4:

  ```bash
  sudo apt install -y python3-gi gir1.2-gtk-4.0 zenity
  ```

- Ollama cho hội thoại/AI cục bộ. Mặc định Jarvis gọi:

  ```text
  http://127.0.0.1:11434
  ```

  với model:

  ```text
  qwen3:4b
  ```

- VieNeu-TTS cho giọng nói tiếng Việt. Đây là submodule có dependency và môi trường riêng; xem tài liệu trong `tts/VieNeu-TTS` trước khi cài vì yêu cầu CPU/GPU phụ thuộc máy.

## 3. Tải và cài nhánh YouTube/Zalo

Trang dự án:

<https://github.com/jocelyngiyyuuu/jarvis-assistant>

### Cách dễ nhất: dùng Git

Cách này tải đúng nhánh có tính năng YouTube/Zalo và lấy luôn TTS submodule.

1. Mở Terminal.
2. Sao chép toàn bộ khối lệnh dưới đây, dán vào Terminal rồi nhấn Enter. Khối
   lệnh sẽ dừng ngay nếu thư mục đích đã tồn tại hoặc một bước cài đặt thất bại;
   nó không đóng cửa sổ Terminal hiện tại.

   ```bash
   (
     set -e
     TARGET="$HOME/Projects/jarvis"

     if [ -e "$TARGET" ]; then
       printf 'DỪNG: %s đã tồn tại. Không có file nào trong đó bị thay đổi.\n' "$TARGET"
       exit 1
     fi

     sudo apt update
     sudo apt install -y git python3 python3-venv python3-pip nodejs npm \
       wmctrl x11-utils lsof

     mkdir -p "$HOME/Projects"
     git clone --recurse-submodules \
       --branch agent/jarvis-v1.0.21-tts --single-branch \
       https://github.com/jocelyngiyyuuu/jarvis-assistant.git \
       "$TARGET"

     cd "$TARGET"
     bash scripts/repair-jarvis-env.sh
   )
   ```

3. Khi Terminal trở lại dấu nhắc mà không báo lỗi, chạy:

   ```bash
   cd "$HOME/Projects/jarvis"
   .venv/bin/python -c 'import discord, dotenv, mcp; print("Cài đặt Python: OK")'
   .venv/bin/python -m unittest discover -s tests -q
   ```

Kết quả đúng có dòng `Cài đặt Python: OK`, một dòng `Ran ... tests in ...` và
kết thúc bằng `OK`:

```text
Cài đặt Python: OK
Ran ... tests in ...
OK
```

Sau đó chuyển đến mục [Cấu hình `.env`](#5-cấu-hình-env).

Nếu khối lệnh báo `DỪNG`, không xóa thư mục cũ. Kiểm tra nó bằng:

```bash
cd "$HOME/Projects/jarvis"
git status --short
```

Nếu đây là một bản Jarvis cũ, hãy backup rồi làm theo mục
[Backup, cập nhật và rollback](#14-backup-cập-nhật-và-rollback). Nếu không biết
thư mục đó là gì, dừng tại đây thay vì xóa hoặc ghi đè.

### Nếu đã clone nhưng thiếu TTS

Chạy trong thư mục Jarvis:

```bash
cd "$HOME/Projects/jarvis"
git submodule update --init --recursive
```

### Tải ZIP để xem mã nguồn

Bản ZIP chỉ phù hợp để đọc hoặc tham khảo mã nguồn. Không dùng bản ZIP cho các
bước cài đặt và vận hành phía dưới vì:

- ZIP không tải VieNeu-TTS submodule;
- ZIP không có lịch sử Git để cập nhật an toàn;
- các lệnh còn lại trong README dùng đường dẫn `~/Projects/jarvis` được tạo bởi
  cách Git ở trên.

Cách tải:

1. Mở trang <https://github.com/jocelyngiyyuuu/jarvis-assistant>.
2. Bấm danh sách nhánh đang hiện tên `main`.
3. Chọn nhánh `agent/jarvis-v1.0.21-tts`.
4. Bấm **Code**, sau đó bấm **Download ZIP**.
5. Giải nén vào một thư mục mới để xem mã nguồn.
6. Không kéo hoặc chép nội dung ZIP vào một bản Jarvis đã cài.

Muốn cài, chạy service, dùng YouTube/Zalo hoặc cập nhật về sau, hãy quay lại
[Cách dễ nhất: dùng Git](#cách-dễ-nhất-dùng-git).

Không chạy `git reset --hard` hoặc `git clean` khi chưa kiểm tra thay đổi cục bộ.

## 4. Tạo môi trường Python

Cách nhanh nhất:

```bash
cd ~/Projects/jarvis
bash scripts/repair-jarvis-env.sh
```

Hoặc cài thủ công:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

Kiểm tra import:

```bash
.venv/bin/python -c 'import discord, dotenv, mcp; print("Dependencies OK")'
```

## 5. Cấu hình `.env`

Tạo file `.env` trong thư mục dự án:

```dotenv
# Bắt buộc nếu dùng Discord
DISCORD_TOKEN=thay_bang_bot_token
DISCORD_USER_ID=thay_bang_user_id_duoc_phep

# Khuyến nghị: giới hạn Jarvis vào đúng một channel
DISCORD_CHANNEL_ID=thay_bang_channel_id

# Tùy chọn: AI cục bộ
OLLAMA_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3:4b
```

Siết quyền file:

```bash
chmod 600 .env
```

Lưu ý bảo mật:

- Không commit `.env`.
- Không gửi token vào Discord, issue hoặc log.
- Nếu token từng bị lộ, reset token trong Discord Developer Portal.
- Bật **Message Content Intent** cho bot Discord.
- `DISCORD_USER_ID` là allowlist người điều khiển.
- `DISCORD_CHANNEL_ID` giới hạn bot vào đúng channel; nếu bỏ trống, Jarvis có thể nhận lệnh của chủ ở channel khác mà bot nhìn thấy.

## 6. Chạy thử trong Terminal

Dừng service trước để tránh khóa một tiến trình:

```bash
systemctl --user stop jarvis.service 2>/dev/null || true
cd ~/Projects/jarvis
.venv/bin/python jarvis.py
```

Gõ `help` để xem lệnh. Nhấn `Ctrl+C` để dừng.

Nếu TTS chưa được cài, Jarvis có thể báo không tìm thấy Python TTS nhưng các tính năng không phụ thuộc giọng nói vẫn có thể chạy.

## 7. Cài dịch vụ `systemd --user`

Tạo unit:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/jarvis.service <<'EOF'
[Unit]
Description=Jarvis AI Assistant
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/Projects/jarvis
ExecStart=%h/Projects/jarvis/.venv/bin/python %h/Projects/jarvis/jarvis.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
```

Nếu repository nằm ở đường dẫn khác, sửa `WorkingDirectory` và `ExecStart` trước khi khởi động.

Nạp và bật service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now jarvis.service
systemctl --user is-active jarvis.service
```

Theo dõi log:

```bash
journalctl --user -u jarvis.service -f
```

Restart sau khi cập nhật mã:

```bash
systemctl --user restart jarvis.service
systemctl --user is-active jarvis.service
journalctl --user -u jarvis.service -n 120 --no-pager
```

## 8. Cài và mở GUI

Cài launcher desktop:

```bash
cd ~/Projects/jarvis
bash scripts/install-jarvis-desktop.sh
```

Sau đó mở **Jarvis** từ menu ứng dụng. Launcher sẽ dùng `jarvis.service` làm tiến trình nền và kết nối GUI qua Unix socket.

Nếu `.venv` bị thiếu hoặc hỏng:

```bash
bash scripts/repair-jarvis-env.sh
```

## 9. Chrome riêng cho YouTube và Zalo

Jarvis tự tạo profile:

```text
~/.config/jarvis-chrome
```

với quyền `0700`, và khởi động Chrome bằng:

```text
--remote-debugging-address=127.0.0.1
--remote-debugging-port=9223
```

MCP kết nối trực tiếp đến:

```text
http://127.0.0.1:9223
```

Thiết kế này tránh phải bấm **Allow** mỗi lần MCP kết nối và không mở DevTools ra LAN.

DevTools loopback không có cơ chế xác thực riêng: một tiến trình khác chạy dưới
cùng tài khoản Linux vẫn có thể thử truy cập endpoint khi Chrome Jarvis đang mở.
Không chạy phần mềm không tin cậy dưới cùng tài khoản và luôn khóa phiên desktop
khi rời máy. Quyền `0700` bảo vệ thư mục profile khỏi tài khoản khác, nhưng không
thay thế việc kiểm soát các tiến trình của chính tài khoản hiện tại.

### Đăng nhập lần đầu

1. Gửi `mở zalo` hoặc `mở youtube`.
2. Đăng nhập trong đúng cửa sổ Chrome Jarvis vừa mở.
3. Không xóa `~/.config/jarvis-chrome` nếu muốn giữ phiên đăng nhập.

Không tự động click hộp quyền, nhập mật khẩu hoặc vượt qua cơ chế bảo mật của Chrome bằng automation.

### Kiểm tra bảo mật profile

Trước tiên gửi `mở youtube` hoặc `mở zalo` để Chrome Jarvis khởi động, sau đó chạy:

```bash
stat -c '%a %n' ~/.config/jarvis-chrome
lsof -nP -iTCP:9223 -sTCP:LISTEN
curl -fsS http://127.0.0.1:9223/json/version
```

Kỳ vọng:

- quyền profile là `700`;
- listener chỉ ở `127.0.0.1:9223`;
- metadata trả về Chrome/Chromium và WebSocket cùng port `9223`.

## 10. Lệnh sử dụng thường gặp

### YouTube

```text
youtube
mở youtube
tìm youtube <từ khóa>
tìm video <từ khóa>
mở video 3
tạm dừng youtube
phát tiếp youtube
youtube đang phát gì?
đóng youtube
```

Các lệnh YouTube không ghi profile sẽ dùng Chrome Jarvis ngay, không hỏi profile Học/Cá nhân.

`youtube đang phát gì?` chỉ đọc metadata và trạng thái video; lệnh này không click, đổi focus, điều hướng, gọi `play()` hoặc `pause()`.

### Zalo

```text
mở zalo
tóm tắt zalo công việc
tóm tắt zalo ngày 13/08/2026
hỏi zalo nhóm <tên nhóm> hôm nay về <câu hỏi>
đóng zalo
```

Tóm tắt Zalo chỉ đọc các hội thoại trong phạm vi được yêu cầu. Jarvis trả nội
dung cho kênh yêu cầu nhưng stdout/systemd journal chỉ ghi trạng thái hoàn thành,
không ghi nội dung chat hay bản tóm tắt. Dù vậy, không chia sẻ journal hoặc dữ
liệu runtime nếu chưa kiểm tra nội dung và quyền truy cập.

### Tin nhắn Zalo hẹn giờ

Ví dụ:

```text
sau 10 phút gửi tin nhắn cho Minh với nội dung kiểm tra đơn
nhắn zalo cho Minh lúc 19:30 ngày 14/08/2026: kiểm tra đơn
```

Jarvis lưu lịch nhưng vẫn yêu cầu xác nhận trong Discord trước khi gửi thật. Không dùng người nhận thật khi kiểm thử nếu chưa có sự đồng ý rõ ràng.

## 11. Ollama

Cài và chạy Ollama theo tài liệu chính thức, sau đó tải model mặc định:

```bash
ollama pull qwen3:4b
ollama serve
```

Kiểm tra:

```bash
curl -fsS http://127.0.0.1:11434/api/tags
```

Có thể đổi endpoint/model trong `.env` bằng `OLLAMA_URL` và `OLLAMA_MODEL`.

## 12. Kiểm thử trước khi vận hành hoặc cập nhật

Dùng đúng Python trong `.venv`:

```bash
cd ~/Projects/jarvis
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q jarvis.py jarvis_gui.py jarvis_core tests
bash -n scripts/*.sh
git diff --check
git diff --cached --check
```

Sau đó restart và kiểm tra service:

```bash
systemctl --user restart jarvis.service
systemctl --user is-active jarvis.service
journalctl --user -u jarvis.service -n 120 --no-pager
```

## 13. Xử lý lỗi thường gặp

### `Một phiên Jarvis khác đang chạy`

Jarvis đã được service hoặc một Terminal khác giữ lock:

```bash
systemctl --user status jarvis.service --no-pager
```

Chọn một trong hai cách chạy, không chạy cả hai.

### Discord không kết nối

```bash
journalctl --user -u jarvis.service -n 120 --no-pager
```

Kiểm tra:

- `DISCORD_TOKEN` tồn tại và chưa bị reset;
- `DISCORD_USER_ID` là số hợp lệ;
- `DISCORD_CHANNEL_ID` đúng channel;
- bot có quyền xem/gửi tin và đã bật Message Content Intent.

### Jarvis vẫn hỏi profile khi mở YouTube

Đảm bảo service đang chạy mã mới nhất:

```bash
systemctl --user restart jarvis.service
journalctl --user -u jarvis.service -n 40 --no-pager
```

### Không mở hoặc không focus đúng Chrome Jarvis

```bash
command -v google-chrome wmctrl xprop lsof
lsof -nP -iTCP:9223 -sTCP:LISTEN
wmctrl -lx | grep -i chrome
```

Jarvis chỉ báo mở thành công khi xác minh được đúng PID Chrome sở hữu DevTools và đúng cửa sổ YouTube/Zalo. Nếu không xác định chắc chắn, Jarvis sẽ báo thất bại thay vì focus nhầm Chrome cá nhân.

### Port `9223` bị chiếm

```bash
lsof -nP -iTCP:9223 -sTCP:LISTEN
```

Không kill tiến trình khi chưa xác định chủ sở hữu. Đóng Chrome automation cũ hoặc restart Jarvis/Chrome Jarvis sau khi đã bảo toàn phiên cần thiết.

### Tóm tắt Zalo không có nội dung

- Đảm bảo đã đăng nhập trong profile Chrome Jarvis.
- Kiểm tra Zalo có nhãn **Công việc**.
- Chạy `mở zalo`, chờ giao diện tải, rồi thử lại.
- Không xóa profile nếu chưa backup phiên đăng nhập.

### MCP không kết nối

Kiểm tra Node/npm, Chrome và endpoint:

```bash
npx --version
google-chrome --version
curl -fsS http://127.0.0.1:9223/json/version
```

Không đổi remote debugging sang `0.0.0.0` để “sửa nhanh”; việc đó làm lộ quyền điều khiển Chrome ra mạng.

### TTS không hoạt động

Kiểm tra:

```bash
test -x tts/VieNeu-TTS/.venv/bin/python && echo 'TTS Python OK'
test -f tts/tts_engine.py && echo 'TTS engine OK'
journalctl --user -u jarvis.service -n 120 --no-pager
```

TTS worker giữ model trong RAM để phản hồi nhanh; mức RAM cao sau khi model đã load không tự động đồng nghĩa với memory leak.

## 14. Backup, cập nhật và rollback

Trước khi cập nhật:

```bash
git status --short
git -C tts/VieNeu-TTS status --short
```

Backup file đang sửa bằng cách copy trạng thái trên đĩa, không lấy từ `HEAD` nếu đang có thay đổi chưa commit.

Cập nhật an toàn:

```bash
git fetch origin
git pull --ff-only
.venv/bin/python -m unittest discover -s tests -v
systemctl --user restart jarvis.service
```

Rollback một file từ backup:

```bash
cp backups/<ten-backup>/jarvis.py jarvis.py
systemctl --user restart jarvis.service
systemctl --user is-active jarvis.service
```

Không commit các dữ liệu sau:

- `.env`;
- database và dữ liệu runtime;
- log;
- backup;
- profile Chrome;
- token/credential;
- thay đổi cục bộ trong submodule TTS nếu không thuộc phạm vi commit.

## 15. Nguyên tắc an toàn

- Chỉ bind Chrome DevTools vào loopback.
- Không tự động click dialog quyền hoặc nhập secret.
- Discord phải kiểm tra cả user allowlist và channel khi channel được cấu hình.
- Không gửi tin Zalo thật trong test nếu chưa có xác nhận.
- Không công bố nội dung Zalo hoặc exception MCP nội bộ.
- Không báo thao tác thành công trước khi runtime xác minh xong.
