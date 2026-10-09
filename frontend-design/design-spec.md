# Jarvis Desktop — design specification

## Bối cảnh và mục tiêu

Jarvis là trợ lý AI local chạy trên Ubuntu, hiện được điều khiển qua Terminal và Discord. Mục tiêu của đợt thiết kế này là chuyển các khả năng quan trọng thành một ứng dụng desktop native có thể mở ngay trên GNOME, giúp người dùng không phải ghi nhớ cú pháp lệnh và không phải đọc các danh sách chữ dài. Đây không phải website, không phải trang marketing và cũng không phải bảng điều khiển từ xa. Sản phẩm là một cửa sổ làm việc cá nhân, ưu tiên thao tác bằng chuột và bàn phím, phản hồi rõ ràng, an toàn với những hành động ảnh hưởng đến file hoặc nguồn điện.

## Người dùng và tình huống sử dụng

Người dùng chính là chủ máy Ubuntu, thường xuyên mở ứng dụng, hỏi AI local, kiểm tra RAM/ổ đĩa, tìm file, quản lý bộ nhớ của Jarvis và đặt lịch tác vụ. Cửa sổ có thể được dùng toàn màn hình hoặc đặt cạnh Terminal, vì vậy phải hoạt động tốt từ 1024×600 trở lên. Khoảng cách quan sát là khoảng một mét trên laptop/desktop. Nội dung chính cần đọc nhanh, nút thao tác phải rõ và các hành động nguy hiểm không được đặt cạnh thao tác thường xuyên.

## Kiến trúc nội dung bắt buộc

Ứng dụng có năm khu vực cấp cao ngang hàng. Chat là trang mở mặc định, hiển thị hội thoại, trạng thái model local, ô nhập lệnh và gợi ý theo ngữ cảnh. Bộ nhớ cho phép xem, tìm, thêm, sửa và quên các mục đã lưu; mỗi mục cần hiện loại, thời gian và mức quan trọng. File & ổ đĩa hiển thị dung lượng, thư mục lớn, file gần đây, file đang dùng và các ứng viên có thể dọn; mọi đề xuất xóa chỉ là báo cáo cho đến khi người dùng xác nhận. Hệ thống trình bày CPU, RAM, nhiệt độ nếu có, uptime, trạng thái TTS/Ollama/Discord và các cảnh báo. Lịch tác vụ hiển thị lịch sleep sâu, tắt màn hình hoặc tác vụ đã hẹn, với khả năng hủy và xem thời gian còn lại.

## Nguyên tắc tương tác

Điều hướng cấp cao chỉ có một tầng. Mỗi trang có một chủ đề rõ, tránh nhồi tất cả thông tin lên dashboard. Phản hồi dùng bốn trạng thái thống nhất: đang chạy, thành công, cảnh báo và lỗi. Tác vụ kéo dài phải có tiến trình hoặc thông báo đang thực hiện, đồng thời có khả năng hủy khi kỹ thuật cho phép. Hành động xóa file, quên bộ nhớ hay suspend phải tách khỏi hành động thông thường và giải thích ảnh hưởng. Kết quả tìm kiếm dài cần phân trang hoặc cuộn, không cắt mất nội dung.

## Ngôn ngữ và nội dung

Giao diện dùng tiếng Việt ngắn gọn. Không dùng dữ liệu trang trí hoặc con số giả để tạo cảm giác “dashboard”. Bản xem trước chỉ dùng thông tin đã biết từ dự án: model qwen3:4b, các dịch vụ Local AI/TTS/Discord, năm nhóm chức năng và các lệnh phổ biến. Những số đo hệ thống trong mockup được ghi rõ là bản xem trước, khi triển khai phải lấy dữ liệu thật từ `JarvisCore`.

## Hình thức và ràng buộc kỹ thuật

Ba bản xem trước có cùng khung 1280×800 để so sánh. Nội dung không cần ảnh vì đây là công cụ dữ liệu và hội thoại; hình ảnh trang trí không làm tăng thông tin. Bản triển khai dự kiến dùng GTK trên GNOME/Wayland, giữ được hành vi native, phím tắt và khả năng thích ứng. Font sản xuất ưu tiên font hệ thống Ubuntu/GNOME để hiển thị tiếng Việt ổn định. Màu sắc phải đạt tương phản đọc được;正文 tối thiểu 14 px, chú thích tối thiểu 12 px. Ba hướng phải khác nhau về cấu trúc: một hướng dashboard bento tối, một hướng GNOME sáng với sidebar, và một hướng command-center cô đọng thiên bàn phím.

## Visual motif và năm câu hỏi form

Vai trò của màn hình là “trạm điều khiển đang sống”, không phải trang báo cáo. Điểm nhìn là laptop 1 m. Nhiệt độ thị giác cần bình tĩnh, tin cậy và có chút cá tính kỹ thuật. Sức chứa ưu tiên một tác vụ chính và ba đến bốn tín hiệu phụ trong một màn hình. Visual motif xuất phát từ chính Jarvis: một luồng hội thoại ở trung tâm kết nối năm năng lực của máy, với trạng thái dịch vụ luôn hiện diện nhưng không lấn át nội dung.
