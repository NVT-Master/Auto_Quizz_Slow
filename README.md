# Auto Quiz Solver

Tool Python: chụp màn hình câu hỏi trên phần mềm desktop → gửi Gemini Vision để đọc + chọn đáp án → fallback tìm DuckDuckGo/Google → tự click A/B/C/D + nút Tiếp theo, lặp lại.

## 1. Cài đặt (Windows)

```powershell
cd auto-quiz-tool
pip install -r requirements.txt
python main.py
```

Không cần cài Tesseract — AI đọc trực tiếp từ ảnh nên hỗ trợ tốt tiếng Việt.

## 2. Lấy Gemini API key miễn phí

1. Vào https://aistudio.google.com → Sign in → Get API Key → Create API key
2. Copy key, dán vào ô API key trong tool

## 3. Quy trình dùng (5 phút setup 1 lần)

1. Mở phần mềm thi + mở tool (`python main.py`)
2. Bấm `1. Chọn vùng chụp` → kéo chuột quanh vùng câu hỏi + đáp án → Enter. Nếu bỏ qua (Esc) thì chụp full màn hình.
3. Bấm `2. Chụp test` → mở file `test_shot.jpg` kiểm tra đã bao trọn câu hỏi chưa.
4. Hiệu chuẩn click: bấm `Set A` → trong 3s di chuột tới đáp án A → tool tự lưu tọa độ. Làm tương tự B, C, D, NEXT (nút Tiếp theo / Câu tiếp).
5. Tick `Tự click Tiếp theo`, chỉnh Delay 3s.
6. Bấm `▶ BẮT ĐẦU AUTO`. Bấm `■ DỪNG` khi xong.

## 4. Cách nó chọn đáp án

- Chính: gửi ảnh lên `gemini-2.0-flash` với prompt ép trả JSON `{question, options, answer_index}`.
- Phụ: nếu AI confidence < 0.5 thì tool search DuckDuckGo theo nội dung câu hỏi, chấm điểm từng option theo từ khóa, chọn điểm cao nhất.
- Log trong tool hiện rõ: câu hỏi, AI chọn gì, fallback chọn gì, đã click ở đâu.

## 5. Lưu ý

- Một số phần mềm thi có chống gian lận / chặn chụp / khóa chuột — tool không bypass được, dùng có trách nhiệm, chỉ dùng nơi được phép (ôn luyện, demo).
- Nếu Gemini báo lỗi 400/404 model: đổi `model` trong `config.json` thành `gemini-1.5-flash` rồi chạy lại.
- Muốn chính xác hơn: chỉnh vùng chụp càng gọn càng tốt, chỉ bao câu hỏi + 4 đáp án.
