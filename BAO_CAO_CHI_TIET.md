# Báo cáo kỹ thuật chi tiết: Auto Quiz Solver (auto-quiz-tool)

Tài liệu này mô tả đầy đủ để AI khác có thể đọc hiểu, tái tạo, sửa lỗi, mở rộng phần mềm.

## 1. Tổng quan

* **Tên:** Auto Quiz Solver
* **Ngôn ngữ:** Python 3, GUI `tkinter`
* **Mục đích:** Tự động giải trắc nghiệm trên phần mềm desktop theo vòng lặp: chụp màn hình vùng câu hỏi -> gửi Gemini Vision để OCR + chọn đáp án -> nếu confidence thấp thì fallback search DuckDuckGo -> tự click tọa độ đáp án A/B/C/D/E/F + nút NEXT -> chờ delay -> lặp.
* **Không dùng OCR local:** Không cần Tesseract. AI đọc trực tiếp từ ảnh nên hỗ trợ tiếng Việt.
* **File chính:** `main.py:1-466` chứa toàn bộ logic. Không chia module.

## 2. Cấu trúc workspace

```
auto-quiz-tool/
  main.py           # toàn bộ app, 466 dòng
  config.json       # cấu hình persist, tự tạo/cập nhật khi chạy
  requirements.txt  # 4 lib
  README.md         # hướng dẫn sử dụng 39 dòng
  test_shot.jpg     # tạo runtime khi bấm Chụp test, không có sẵn
  __pycache__/      # cache Python
```

## 3. Phụ thuộc `requirements.txt:1-4`

* `mss>=9.0.0`: chụp màn hình tốc độ cao.
* `Pillow>=10.0.0`: xử lý ảnh, resize, lưu JPEG.
* `pyautogui>=0.9.54`: `click(x,y)`, `position()`.
* `requests>=2.31.0`: gọi Gemini REST + DuckDuckGo.
* Thư viện chuẩn dùng thêm: `base64, io, json, os, re, threading, time, tkinter, unicodedata`.

Cài + chạy:

```powershell
pip install -r requirements.txt
python main.py
```

## 4. Cấu hình `config.json:1-37` và `main.py:16-32`

Schema `DEFAULT_CONFIG` tại `main.py:16-24`:

```json
{
  "api_key": "",
  "model": "gemini-3.5-flash-lite",
  "capture_region": null,
  "click_points": {"A": null, "B": null, "C": null, "D": null, "E": null, "F": null, "NEXT": null},
  "num_answers": 0,
  "delay_between_questions": 3.0,
  "auto_click_next": true
}
```

Giải thích field:

* `api_key`: Gemini API key, lấy tại `aistudio.google.com`. Lưu plaintext trong config.json.
* `model`: model chính. Giá trị hiện tại trong repo là `gemini-3.5-flash-lite`.
* `capture_region`: `null` = full màn hình chính `sct.monitors[1]`. Ngược lại `{"left","top","width","height"}` tính theo pixel màn hình.
* `click_points`: tọa độ `[x,y]` cho từng đáp án và nút chuyển câu. `null` = chưa hiệu chuẩn.
* `num_answers`: `0` = auto theo số `options` AI trả về. `2-6` = cắt `options[:need]` để ép số đáp án.
* `delay_between_questions`: giây nghỉ giữa 2 câu.
* `auto_click_next`: có click `NEXT` sau khi click đáp án không.

Ví dụ config thực tế đã hiệu chuẩn (đã ẩn api_key):

```json
{
  "model": "gemini-3.5-flash-lite",
  "capture_region": {"left": 0, "top": 118, "width": 1919, "height": 922},
  "click_points": {"A": [251,763], "B": [764,767], "C": [1224,763], "D": [1663,749], "E": null, "F": null, "NEXT": [1745,794]},
  "num_answers": 0,
  "delay_between_questions": 2.0,
  "auto_click_next": true
}
```

Hằng số liên quan:

* `MODEL_FALLBACKS` tại `main.py:27-32`: `["gemini-3.5-flash-lite","gemini-3.1-flash-lite","gemini-3-flash-preview","gemini-3.5-flash"]`. Dùng để tự đổi model khi 404/503.
* `INDEX_TO_KEY = ["A","B","C","D","E","F"]` tại `main.py:177`, `MAX_OPTIONS=6` tại `main.py:178`.
* `CONFIG_FILE` tại `main.py:14`: `config.json` cùng thư mục `main.py`.

Hàm persist:

* `load_config()` tại `main.py:34-48`: merge config file với default, đặc biệt merge sâu `click_points` để config cũ thiếu E/F vẫn chạy.
* `save_config(cfg)` tại `main.py:50-52`: `json.dump` UTF-8 indent 2.
* `App.persist()` tại `main.py:306-313`: đọc từ widget -> ghi vào `self.cfg` -> `save_config`.

Lưu ý lệch docs: `README.md:31` ghi model `gemini-2.0-flash`, nhưng code thực tế dùng dòng `gemini-3.x`. AI khác nên tin code.

## 5. Luồng hoạt động tổng thể

1. User mở app thi + chạy `python main.py` -> `App.__init__` tại `main.py:239-293`.
2. Setup 1 lần: `do_region()` -> `do_test_shot()` -> `do_calibrate()` cho A/B/C/D/E/F/NEXT -> set `num_answers`, `delay`, `auto_click_next`.
3. Bấm `BẮT ĐẦU AUTO` -> `App.start()` tại `main.py:350-369` validate key + điểm click -> `threading.Thread(target=self.loop, daemon=True)`.
4. `App.loop()` tại `main.py:445-461`: `while self.running: solve_once() + sleep(delay)`, sleep chia nhỏ `0.5s` để nút DỪNG phản hồi nhanh.
5. `App.solve_once()` tại `main.py:376-443`: chụp -> base64 -> thử lần lượt các model Gemini -> parse JSON -> log -> fallback nếu yếu -> validate `idx` -> `click_point()` -> click NEXT nếu bật.

## 6. Chi tiết từng khối trong `main.py`

### 6.1. Screenshot `main.py:54-76`

* `take_screenshot(region=None)` tại `main.py:55-67`: dùng `mss.mss()`, lấy `monitors[1]`. Nếu `region` có thì build dict `left/top/width/height`. `sct.grab()` trả BGRA -> convert `Image.frombytes("RGB", size, bgra, "raw", "BGRX")`.
* `img_to_base64(img, max_width=1600)` tại `main.py:69-76`: nếu rộng hơn 1600 thì resize giữ tỉ lệ bằng `Image.LANCZOS`, lưu JPEG quality 85, `base64.b64encode`.

### 6.2. Gọi Gemini Vision `main.py:79-110`

* `GEMINI_PROMPT` tại `main.py:79-85`: ép model chỉ trả 1 JSON duy nhất, không markdown:
  `{"question": "...", "options": [...], "answer_index": 0, "confidence": 0.9, "explanation": "..."}`. Quy tắc: đếm kỹ 2-6 đáp án theo thứ tự trên-xuống/trái-phải, `answer_index` 0=A...5=F, không đọc được thì `answer_index:-1`.
* `ask_gemini(img_b64, api_key, model)` tại `main.py:87-110`:
  * URL: `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}`
  * Payload: `contents[0].parts = [{text: GEMINI_PROMPT}, {inline_data: {mime_type: image/jpeg, data: img_b64}}]`, `generationConfig={temperature:0.1, maxOutputTokens:1000}`, timeout 90s.
  * Parse: `data["candidates"][0]["content"]["parts"][0]["text"]`, dùng regex `\{.*\}` DOTALL để bóc JSON kể cả khi model bọc ```json, rồi `json.loads`.

### 6.3. Fallback web-search `main.py:112-170`

Kích hoạt trong `solve_once()` tại `main.py:415-423` khi `idx<0 hoặc conf<0.5` và có `q, opts`.

* `web_search_snippets(query, max_chars=6000)` tại `main.py:113-143`:
  1. GET `https://api.duckduckgo.com/?q=&format=json&no_html=1`, gom `AbstractText, Answer, RelatedTopics[:5].Text`.
  2. POST `https://html.duckduckgo.com/html/` với `data={q:query}`, strip tag HTML, cắt `max_chars`. Lowercase toàn bộ trước khi trả.
  3. Lỗi mạng thì bỏ qua, trả chuỗi rỗng/cụt.
* `norm_vi(s)` tại `main.py:145-150`: lower + NFD strip dấu + `đ->d` để so khớp không dấu.
* `fallback_pick(question, options)` tại `main.py:152-170`: với mỗi option, tách tối đa 10 keyword `\w+` dài >=2 ký tự, `score = sum(blob.count(k))`, cộng 5 nếu 30 ký tự đầu xuất hiện nguyên văn. Chọn index điểm cao nhất. Trả `(best, "scores=[...]")`. Nếu thiếu dữ liệu hoặc search <20 ký tự thì trả `-1`.

### 6.4. Click `main.py:172-178`

* `click_point(pt)` tại `main.py:173-175`: `pyautogui.click(pt[0], pt[1])`. `pt` là list `[x,y]` từ `click_points`.

### 6.5. Chọn vùng chụp GUI `main.py:181-235`

* `select_region_gui()` tại `main.py:181-235`: mở `Toplevel` fullscreen alpha 0.3 topmost, canvas crosshair, label hướng dẫn Kéo chuột + Enter xong + Esc hủy + Double-click xong.
  * `ButtonPress-1` lưu `x_root,y_root`, `B1-Motion` vẽ rectangle đỏ tạm, lưu `x0,y0,x1,y1`.
  * `wait_window()` chặn đến khi đóng. Sort tọa độ, loại vùng <50x50px trả `None` = dùng full màn hình.

### 6.6. GUI chính `App` tại `main.py:238-462`

Layout `560x700` tại `main.py:241`:

* Ô API key `show="*"` tại `main.py:247-248`.
* Nút `1. Chọn vùng chụp` -> `do_region()` tại `main.py:316-329`, nút `2. Chụp test` -> `do_test_shot()` tại `main.py:331-338` lưu `test_shot.jpg` cùng thư mục.
* `OptionMenu` số đáp án `0,2,3,4,5,6` tại `main.py:259-261`, `get_num_answers()` tại `main.py:295-300`.
* Nút `Set A/B/C/D` tại `main.py:266-268` và `Set E/F/NEXT` tại `main.py:271-273` -> `do_calibrate(name)` tại `main.py:340-348`: sleep 3s rồi `pyautogui.position()` lưu.
* `Checkbutton auto_click_next` + Entry delay tại `main.py:277-281`.
* Nút `BẮT ĐẦU AUTO` tại `main.py:285-287` và `DỪNG` tại `main.py:288-289`.
* Log `ScrolledText` tại `main.py:291-292`, ghi qua `info(msg)` tại `main.py:302-304` kèm timestamp `HH:MM:SS`.

Logic `start()` tại `main.py:350-369`:

* Thiếu key -> warning.
* Nếu `num_answers>=2`: yêu cầu đủ `INDEX_TO_KEY[:need]`, thiếu thì `askyesno` vẫn chạy ở chế độ chỉ hiện đáp án.
* Nếu auto: yêu cầu ít nhất A,B, nếu không cũng hỏi tương tự.

Logic `solve_once()` dòng 376-443:

* Build list `models = [cfg.model] + fallbacks khác`.
* Thử từng model, lỗi thì log `Model {m} lỗi...` và tiếp. Thành công mà khác model cũ thì tự cập nhật `cfg.model` + persist + log `đã tự chuyển sang`.
* Hết model mà `res is None` -> trả lỗi.
* Cắt `opts[:need]` nếu ép số đáp án, rồi cắt `[:MAX_OPTIONS]`.
* Log hỏi, từng option, AI chọn + confidence + explanation.
* Fallback nếu yếu, log fallback.
* Nếu `idx` invalid -> `Không xác định được đáp án, bỏ qua`.
* Nếu có `pt` thì click + log `Đã CLICK`, nếu không -> `Cần tự click {key}`.
* Nếu `auto_click_next` thì sleep 0.8s rồi click NEXT nếu có.

## 7. Ví dụ IO cho AI khác

Input ảnh -> Gemini phải trả ví dụ:

```json
{"question": "Thủ đô của Việt Nam là?", "options": ["TP.HCM","Hà Nội","Đà Nẵng","Huế"], "answer_index": 1, "confidence": 0.95, "explanation": "Hà Nội là thủ đô"}
```

App map `1->B`, tra `click_points["B"]` e.g. `[764,767]` rồi click.

Quy trình dùng chuẩn từ `README.md:20-28`:
1. Mở phần mềm thi + `python main.py`
2. `1. Chọn vùng chụp` kéo quanh câu hỏi + đáp án -> Enter, Esc = full màn hình.
3. `2. Chụp test` mở `test_shot.jpg` kiểm tra.
4. `Set A` trong 3s di chuột tới A, tương tự B,C,D,NEXT.
5. Tick auto NEXT, delay 2-3s, BẮT ĐẦU AUTO.

## 8. Hạn chế và rủi ro đã ghi trong code/docs

* `README.md:37`: phần mềm thi có chống gian lận/chặn chụp/khóa chuột thì tool không bypass. Chỉ dùng nơi được phép.
* Vùng chụp càng gọn chỉ bao câu hỏi + đáp án thì chính xác hơn `README.md:39`.
* Model Google hay 404/503 nên phải có fallback list `main.py:26`.
* Vẽ rectangle trong `select_region_gui()` dùng xấp xỉ `winfo_rootx/y`, có thể lệch trên đa màn hình/scale DPI.
* Fallback search chỉ chấm tần suất từ khóa, dễ sai với câu suy luận/toán, chỉ là cứu cánh.
* API key lưu plaintext trong `config.json`, delay parse bằng `float()` có fallback 3.0s.
* Thread auto-loop là daemon, click dùng tọa độ tuyệt đối nên đổi độ phân giải/cửa sổ là phải calib lại.
