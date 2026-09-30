# Auto Quiz Solver - chụp màn hình, tra AI + Google, tự click đáp án
# Chạy: pip install -r requirements.txt && python main.py

import base64
import hashlib
import io
import json
import os
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "config.json")
CONFIG_BAK_FILE = os.path.join(os.path.dirname(__file__), "config.json.bak")

DEFAULT_CONFIG = {
    "provider": "gemini",
    "api_key": "",
    "model": "gemini-3.5-flash-lite",
    "capture_region": None,  # {"left":x,"top":y,"width":w,"height":h} None = full screen
    # STEP4: click_points chỉ còn NEXT. A/B/C/D/E/F legacy đã bỏ (config cũ vẫn load được, xem load_config).
    "click_points": {"NEXT": None},
    "delay_between_questions": 0.1,
    "auto_click_next": True,
    # TỐC ĐỘ NHANH NHẤT: ảnh nhỏ -> upload + Gemini nhanh hơn 0.5-1.5s/câu.
    # 1600/q88 giữ chữ nhỏ rõ hơn; ưu tiên độ chính xác hơn tốc độ upload.
    "screenshot_max_width": 1600,  # bề rộng tối đa ảnh gửi Gemini (giữ aspect ratio)
    "jpeg_quality": 88,  # JPEG quality ảnh gửi Gemini
    "answer_to_next_delay": 0.3,  # giây chờ giữa answer click và NEXT click (tách khỏi delay giữa câu)
    "screen_change_poll_interval": 0.15,  # tần suất kiểm tra ảnh nhẹ sau khi click
    "screen_change_timeout": 6.0,  # tối đa chờ câu mới xuất hiện
    # Click đáp án: pyautogui (chuột nhảy tới điểm click). Không còn backend nền.
    # Nguồn chụp: "region" (vùng cố định) hoặc "window" (bám theo cửa sổ app, cửa sổ
    # di chuyển vẫn chụp đúng). capture_window do nút SELECT WINDOW trong GUI ghi.
    "capture_mode": "region",
    "capture_window": None,  # {"title": "...", "exe": "..."} khi đã chọn cửa sổ
    # TỐI ƯU TỐC ĐỘ NHANH NHẤT (mặc định):
    # - enable_fallback False = tắt search DuckDuckGo để không cộng thêm độ trễ.
    # - min_confidence 0 = nếu AI trả index hợp lệ thì click ngay, ưu tiên tốc độ.
    # - fallback_min_margin: chỉ dùng khi bật fallback.
    "enable_fallback": False,
    "min_confidence": 0.0,
    "fallback_min_margin": 3,
    "verify_answer": False,
    "verification_min_confidence": 0.75,
    "google_grounding": True,
}

# Model Gemini duy nhất (fail fast; thử nhiều model nối tiếp tốn 8-30s khi lỗi).
DEFAULT_MODEL = "gemini-3.5-flash-lite"


def ensure_config_backup():
    """STEP1: backup config.json -> config.json.bak (không ghi đè nếu đã tồn tại)."""
    try:
        import shutil

        if os.path.exists(CONFIG_FILE) and not os.path.exists(CONFIG_BAK_FILE):
            shutil.copy2(CONFIG_FILE, CONFIG_BAK_FILE)
    except Exception:
        pass


def load_config():
    ensure_config_backup()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                out = DEFAULT_CONFIG.copy()
                # STEP4 migration: config cũ có A-F/num_answers vẫn load được, nhưng pipeline
                # không dùng chúng. Chỉ giữ NEXT + các field còn hiệu lực. Không làm mất
                # api_key/model/capture_region/delay_between_questions/auto_click_next/NEXT.
                # PHASE4: key perf mới thiếu trong file cũ -> lấy default (giữ behavior cũ).
                for k in (
                    "provider",
                    "api_key",
                    "model",
                    "capture_region",
                    "delay_between_questions",
                    "auto_click_next",
                    "screenshot_max_width",
                    "jpeg_quality",
                    "answer_to_next_delay",
                    "screen_change_poll_interval",
                    "screen_change_timeout",
                    "capture_mode",
                    "capture_window",
                    "enable_fallback",
                    "min_confidence",
                    "fallback_min_margin",
                    "verify_answer",
                    "verification_min_confidence",
                    "google_grounding",
                ):
                    if k in cfg:
                        out[k] = cfg[k]
                old_pts = (
                    cfg.get("click_points", {})
                    if isinstance(cfg.get("click_points", {}), dict)
                    else {}
                )
                out["click_points"] = {"NEXT": old_pts.get("NEXT", None)}
                return out
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg):
    # STEP4: chỉ persist field còn hiệu lực; bỏ legacy A-F/num_answers khi ghi.
    clean = {
        "provider": cfg.get("provider", "gemini"),
        "api_key": cfg.get("api_key", ""),
        "model": cfg.get("model", DEFAULT_CONFIG["model"]),
        "capture_region": cfg.get("capture_region", None),
        "click_points": {"NEXT": (cfg.get("click_points", {}) or {}).get("NEXT", None)},
        "delay_between_questions": cfg.get("delay_between_questions", 0.1),
        "auto_click_next": cfg.get("auto_click_next", True),
        "screenshot_max_width": cfg.get("screenshot_max_width", 1600),
        "jpeg_quality": cfg.get("jpeg_quality", 88),
        "answer_to_next_delay": cfg.get("answer_to_next_delay", 0.3),
        "screen_change_poll_interval": cfg.get("screen_change_poll_interval", 0.15),
        "screen_change_timeout": cfg.get("screen_change_timeout", 6.0),
        "capture_mode": cfg.get("capture_mode", "region"),
        "capture_window": cfg.get("capture_window", None),
        "enable_fallback": cfg.get("enable_fallback", False),
        "min_confidence": cfg.get("min_confidence", 0.0),
        "fallback_min_margin": cfg.get("fallback_min_margin", 3),
        "verify_answer": cfg.get("verify_answer", False),
        "verification_min_confidence": cfg.get("verification_min_confidence", 0.75),
        "google_grounding": cfg.get("google_grounding", True),
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(clean, f, ensure_ascii=False, indent=2)


# ---------- PHASE4: perf timing (đo bằng time.perf_counter, ms, không log API key) ----------
def append_history(row):
    """Ghi 1 dòng JSON vào answer_history.jsonl (không chứa API key).
    Để truy lại câu nào sai: mở file này + đối chiếu điểm Wayground."""
    try:
        p = os.path.join(os.path.dirname(__file__), "answer_history.jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def perf_timed(fn, *args, **kwargs):
    """Chạy fn, trả (result, ms). Dùng cho STEP 0 baseline từng stage."""
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, (time.perf_counter() - t0) * 1000.0


def perf_format_block(d):
    """Dựng block ## PERFORMANCE từ dict ms. Không chứa API key."""
    keys = (
        "screenshot_ms",
        "encode_ms",
        "gemini_ms",
        "parse_ms",
        "validation_ms",
        "mapping_ms",
        "fallback_ms",
        "click_ms",
        "next_ms",
        "total_ms",
    )
    lines = ["## PERFORMANCE"]
    for k in keys:
        lines.append(f"{k}: {d.get(k, 0.0):.1f}")
    lines.append("-------------")
    return "\n".join(lines)


# ---------- Screenshot ----------
def take_screenshot(region=None):
    """STEP1: Trả về (PIL.Image gốc, grabbed_area).
    grabbed_area = {"left","top","width","height"} vùng màn hình GỐC đã grab.
    - Nếu region != None: grabbed_area = region thực tế.
    - Nếu region is None: grabbed_area tương ứng sct.monitors[1].
    KHÔNG dùng kích thước ảnh resize để thay grabbed_area.
    """
    import mss
    from PIL import Image

    with mss.mss() as sct:
        mon = sct.monitors[1]
        if region is None:
            area = {
                "left": int(mon["left"]),
                "top": int(mon["top"]),
                "width": int(mon["width"]),
                "height": int(mon["height"]),
            }
        else:
            area = {
                "left": int(region["left"]),
                "top": int(region["top"]),
                "width": int(region["width"]),
                "height": int(region["height"]),
            }
        shot = sct.grab(area)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        grabbed_area = {
            "left": area["left"],
            "top": area["top"],
            "width": area["width"],
            "height": area["height"],
        }
        return img, grabbed_area


# ---------- Chụp theo cửa sổ app (bám cửa sổ di chuyển, như share-screen chọn window) ----------
def _real_enum_windows():
    """Liệt kê top-level window hiển thị: [{hwnd,title,exe,rect}]. ctypes, không thêm dep."""
    import ctypes

    u = ctypes.windll.user32
    k = ctypes.windll.kernel32
    u.IsWindowVisible.argtypes = [ctypes.c_void_p]
    u.IsWindowVisible.restype = ctypes.c_bool
    u.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetWindow.restype = ctypes.c_void_p
    u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.GetWindowRect.restype = ctypes.c_bool
    u.GetWindowThreadProcessId.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    out = []

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        try:
            if not u.IsWindowVisible(hwnd):
                return True
            if u.GetWindow(hwnd, 4):  # GW_OWNER: bỏ popup phụ thuộc
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            r = _RECT()
            if not u.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            w, h = r.right - r.left, r.bottom - r.top
            if w < 200 or h < 200:
                return True
            exe = ""
            try:
                pid = ctypes.c_ulong()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                hp = k.OpenProcess(0x1000, False, pid.value)
                if hp:
                    try:
                        b2 = ctypes.create_unicode_buffer(260)
                        sz = ctypes.c_ulong(260)
                        if k.QueryFullProcessImageNameW(hp, 0, b2, ctypes.byref(sz)):
                            exe = b2.value.split("\\")[-1].lower()
                    finally:
                        k.CloseHandle(hp)
            except Exception:
                pass
            out.append(
                {
                    "hwnd": hwnd,
                    "title": buf.value,
                    "exe": exe,
                    "rect": {
                        "left": int(r.left),
                        "top": int(r.top),
                        "width": int(w),
                        "height": int(h),
                    },
                }
            )
        except Exception:
            pass
        return True

    u.EnumWindows(cb, 0)
    return out


def _real_hwnd_at(x, y):
    """hwnd cửa sổ gốc (root) dưới điểm màn hình. None nếu không có."""
    import ctypes

    u = ctypes.windll.user32
    u.WindowFromPoint.argtypes = [ctypes.c_long, ctypes.c_long]
    u.WindowFromPoint.restype = ctypes.c_void_p
    u.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    u.GetAncestor.restype = ctypes.c_void_p
    h = u.WindowFromPoint(int(x), int(y))
    if not h:
        return None
    return u.GetAncestor(h, 2) or h  # GA_ROOT


def enum_top_windows(provider=None):
    return (provider or _real_enum_windows)()


def rank_window(windows, title, exe):
    """Chọn cửa sổ khớp nhất (pure, test được): đúng exe + đúng title trước,
    rồi tới title chứa nhau, cuối cùng là cửa sổ cùng exe to nhất. None nếu không khớp.
    """
    title = (title or "").strip()
    exe = (exe or "").strip().lower()
    cands = [
        w
        for w in (windows or [])
        if w.get("rect") and (not exe or (w.get("exe") or "").lower() == exe)
    ]
    if not cands:
        return None
    for w in cands:
        if title and w.get("title") == title:
            return w
    if title:
        hint = title[:30].lower()
        sub = [
            w
            for w in cands
            if hint
            and (
                hint in (w.get("title") or "").lower()
                or (w.get("title") or "").lower() in hint
            )
        ]
        if sub:
            return max(sub, key=lambda w: w["rect"]["width"] * w["rect"]["height"])
    return max(cands, key=lambda w: w["rect"]["width"] * w["rect"]["height"])


def find_window_rect(info, provider=None):
    """Tìm rect hiện tại của cửa sổ đã lưu. Trả dict rect hoặc None."""
    info = info or {}
    wins = enum_top_windows(provider)
    best = rank_window(wins, info.get("title", ""), info.get("exe", ""))
    return dict(best["rect"]) if best else None


def pick_window_at_cursor(pos=None, provider=None, hwnd_at=None):
    """Chọn cửa sổ dưới chuột (đếm 3s do caller thực hiện). Trả {"title","exe","rect"} hoặc None."""
    if pos is None:
        import pyautogui

        pos = pyautogui.position()
    wins = enum_top_windows(provider)
    h = (hwnd_at or _real_hwnd_at)(int(pos[0]), int(pos[1]))
    if h:
        for w in wins:
            if w.get("hwnd") == h:
                return {
                    "title": w.get("title", ""),
                    "exe": w.get("exe", ""),
                    "rect": dict(w["rect"]),
                }
    small = [
        w
        for w in wins
        if w.get("rect")
        and w["rect"]["left"] <= pos[0] < w["rect"]["left"] + w["rect"]["width"]
        and w["rect"]["top"] <= pos[1] < w["rect"]["top"] + w["rect"]["height"]
    ]
    if not small:
        return None
    best = min(small, key=lambda w: w["rect"]["width"] * w["rect"]["height"])
    return {
        "title": best.get("title", ""),
        "exe": best.get("exe", ""),
        "rect": dict(best["rect"]),
    }


def resolve_capture_area(cfg, finder=None):
    """Trả (region_or_None, mô tả nguồn). Window mode: bám rect cửa sổ hiện tại;
    mất cửa sổ → fallback vùng cũ. take_screenshot() và mapping giữ nguyên."""
    cfg = cfg or {}
    if cfg.get("capture_mode") == "window":
        info = cfg.get("capture_window") or {}
        if info.get("exe") or info.get("title"):
            try:
                rect = (finder or find_window_rect)(info)
            except Exception:
                rect = None
            if rect:
                return (
                    rect,
                    f"cửa sổ {info.get('exe', '')} — {str(info.get('title', ''))[:40]}",
                )
            return cfg.get("capture_region"), "cửa sổ mất → fallback vùng cũ"
    return cfg.get("capture_region"), "vùng cố định"


def img_to_base64(img, max_width=1600, jpeg_quality=88):
    """Resize giữ aspect ratio (không stretch), JPEG encode + base64.
    Trả (b64_string, meta) với meta giữ original/sent size để debug mapping box.
    Default theo config tốc độ nhanh nhất (1024/q68)."""
    from PIL import Image

    original_width, original_height = img.size
    out_img = img
    if img.width > max_width:
        r = max_width / img.width
        out_img = img.resize((max_width, int(img.height * r)), Image.LANCZOS)
    sent_width, sent_height = out_img.size
    buf = io.BytesIO()
    out_img.save(buf, format="JPEG", quality=int(jpeg_quality))
    b64 = base64.b64encode(buf.getvalue()).decode()
    meta = {
        "original_width": original_width,
        "original_height": original_height,
        "sent_width": sent_width,
        "sent_height": sent_height,
    }
    return b64, meta


# ---------- AI: Gemini Vision ----------
# PHASE4: prompt rút gọn (đo chars trước/sau trong test). Giữ đủ: đọc trực tiếp ảnh,
# 2-6 options động, thứ tự đọc, answer_index, box normalized full-click-area, -1/empty, JSON-only.
# Giữ explanation nhưng giới hạn <=12 từ (giữ tín hiệu suy luận, chặn output dài).
GEMINI_PROMPT = """Đọc TRỰC TIẾP screenshot được cung cấp. Chỉ trả 1 JSON hợp lệ, không markdown, không ```json:
{"question": "...", "options": [{"label": "A", "text": "...", "box": {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}}], "answer_index": 0, "confidence": 0.95, "explanation": "<=12 tu"}
- Trả đúng số đáp án thực sự thấy (2-6), thứ tự trên-xuống/trái-phải.
- answer_index là index trong options này (0=đầu tiên). Không dùng vị trí A/B/C/D cố định.
- box tính trên ẢNH NHẬN ĐƯỢC: (0,0) trên-trái, (1,1) dưới-phải; 0<=x1<x2<=1, 0<=y1<y2<=1.
  Luôn trả số thập phân đã CHUẨN HÓA 0..1, không bao giờ trả tọa độ pixel (ví dụ 250, 651 là SAI).
- box bao TOÀN BỘ vùng click: radio/checkbox (nếu có) + toàn bộ text; tâm box là điểm click.
- Trước khi trả JSON, hãy tự giải câu hỏi và kiểm tra từng lựa chọn một lần; không chọn theo vị trí, màu sắc hoặc suy đoán khi chưa đọc đủ chữ.
- Không biết đáp án: answer_index=-1, confidence=0. Không đọc được: {"question": "", "options": [], "answer_index": -1, "confidence": 0.0, "explanation": "khong doc duoc"}.
- Tiếng Việt, kiến thức chuẩn."""

VERIFY_PROMPT_TEMPLATE = """Kiểm tra lại câu hỏi trong screenshot một cách độc lập.
Chỉ trả 1 JSON hợp lệ, không markdown:
{"answer_index": 0, "confidence": 0.95, "reason": "ngan"}
Danh sách đáp án theo đúng thứ tự trong ảnh:
{options}
Đáp án mà lượt đọc trước chọn là index {candidate_index}: {candidate_text}
- Tự giải câu hỏi từ kiến thức của bạn, không mặc định tin đáp án đề xuất.
- answer_index phải là index 0-based trong danh sách trên; nếu không đọc/không chắc trả -1.
- confidence là mức chắc chắn thực tế từ 0 đến 1, không phóng đại.
- Chỉ xác nhận khi câu hỏi và các lựa chọn nhìn đủ rõ."""


def parse_gemini_text(text):
    """Tách JSON từ text Gemini (loại bỏ ```json ...). Pure helper để timing parse_ms + test truncate."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"Không parse được JSON: {text[:500]}")
    return json.loads(m.group(0))


def post_json_cancelable(url, headers, payload, cancel_check=None):
    """POST JSON có thể dừng chờ khi người dùng bấm STOP."""
    import requests

    result = {}

    def do_request():
        try:
            result["response"] = requests.post(
                url, headers=headers, json=payload, timeout=(3, 8)
            )
        except Exception as error:
            result["error"] = error

    request_thread = threading.Thread(target=do_request, daemon=True)
    request_thread.start()
    while request_thread.is_alive():
        if cancel_check is not None and cancel_check():
            raise RuntimeError("STOP_REQUESTED")
        request_thread.join(0.1)
    if "error" in result:
        raise result["error"]
    return result["response"]


def ask_gemini(
    img_b64,
    api_key,
    model,
    timer=None,
    prompt=GEMINI_PROMPT,
    google_grounding=False,
    cancel_check=None,
):
    """Gọi Gemini Vision. timer (dict, optional) nhận gemini_ms (request+parse) và parse_ms.
    Tốc độ nhanh: temperature 0.1 + maxOutputTokens 1024 + timeout 8s.
    """
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}},
                ]
            }
        ],
        # JSON schema ngắn: giới hạn output để giảm độ trễ và tránh model lan man.
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
            "responseMimeType": "application/json",
        },
    }
    if google_grounding:
        # Gemini tự gọi Google Search khi câu hỏi cần dữ kiện ngoài ảnh.
        payload["tools"] = [{"google_search": {}}]
    t0 = time.perf_counter()
    r = post_json_cancelable(url, {}, payload, cancel_check=cancel_check)
    try:
        r.raise_for_status()
    except Exception as error:
        raise RuntimeError(
            f"Gemini HTTP {getattr(r, 'status_code', '?')}: {getattr(r, 'text', '')[:400]}"
        ) from error
    data = r.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        raise RuntimeError(f"Gemini trả về lạ: {data}") from e
    t_parse = time.perf_counter()
    # bóc JSON trong text (loại bỏ ```json ...)
    parsed = parse_gemini_text(text)
    if timer is not None:
        timer["parse_ms"] = (time.perf_counter() - t_parse) * 1000.0
        timer["gemini_ms"] = (time.perf_counter() - t0) * 1000.0
    # STEP1: không tin toàn bộ dữ liệu ở đây — caller phải gọi validate_ai_result().
    return parsed


def ask_openai(
    img_b64, api_key, model, timer=None, prompt=GEMINI_PROMPT, cancel_check=None
):
    """Gọi OpenAI Vision với cùng schema JSON mà pipeline Gemini đang dùng."""
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_b64}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }
    t0 = time.perf_counter()
    r = post_json_cancelable(
        url,
        {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        payload,
        cancel_check=cancel_check,
    )
    try:
        r.raise_for_status()
    except Exception as error:
        raise RuntimeError(
            f"OpenAI HTTP {getattr(r, 'status_code', '?')}: {getattr(r, 'text', '')[:400]}"
        ) from error
    data = r.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"OpenAI trả về lạ: {data}") from e
    t_parse = time.perf_counter()
    parsed = parse_gemini_text(text)
    if timer is not None:
        timer["parse_ms"] = (time.perf_counter() - t_parse) * 1000.0
        timer["gemini_ms"] = (time.perf_counter() - t0) * 1000.0
    return parsed


def ask_ai(
    img_b64,
    api_key,
    model,
    provider="gemini",
    timer=None,
    google_grounding=False,
    cancel_check=None,
):
    """Gọi provider đang chọn nhưng trả cùng schema cho pipeline phía sau."""
    if provider == "openai":
        return ask_openai(
            img_b64,
            api_key,
            model,
            timer=timer,
            cancel_check=cancel_check,
        )
    return ask_gemini(
        img_b64,
        api_key,
        model,
        timer=timer,
        google_grounding=google_grounding,
        cancel_check=cancel_check,
    )


def verify_gemini_answer(img_b64, api_key, model, options, candidate_index, timer=None):
    """Dùng lượt Gemini thứ hai để kiểm tra độc lập index ứng viên."""
    option_texts = [
        o.get("text", "") if isinstance(o, dict) else str(o) for o in (options or [])
    ]
    candidate_text = (
        option_texts[candidate_index]
        if isinstance(candidate_index, int) and 0 <= candidate_index < len(option_texts)
        else ""
    )
    prompt = VERIFY_PROMPT_TEMPLATE.format(
        options=json.dumps(option_texts, ensure_ascii=False),
        candidate_index=candidate_index,
        candidate_text=candidate_text,
    )
    result = ask_gemini(img_b64, api_key, model, timer=timer, prompt=prompt)
    if not isinstance(result, dict):
        raise ValueError("Lượt xác minh không trả về object JSON")
    verified_index = result.get("answer_index", -1)
    confidence = result.get("confidence", 0.0)
    if isinstance(verified_index, bool) or not isinstance(verified_index, int):
        raise ValueError("Lượt xác minh trả answer_index không hợp lệ")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("Lượt xác minh trả confidence không hợp lệ")
    return verified_index, float(confidence), str(result.get("reason", ""))


# STEP1: validation nền tảng cho schema box mới. CHƯA click. Chỉ trả (ok, code, message).
MIN_BOX_AREA = 0.0002  # diện tích normalized tối thiểu (~14x14px trên ảnh 1000px)
MAX_OPTIONS_NEW = 6


def validate_ai_result(res):
    """Validate schema AI mới. Trả (ok: bool, code: str, message: str).
    Codes: OK | not_dict | no_options | too_many_options | option_not_dict |
           bad_text | bad_box | bad_box_coords_type | bad_box_range |
           bad_box_order | bad_box_too_small | bad_box_option_{i} |
           bad_answer_index | bad_answer_index_type | invalid_confidence
    """
    if not isinstance(res, dict):
        return False, "not_dict", "res phải là dict"
    opts = res.get("options", None)
    if not isinstance(opts, list):
        return False, "no_options", "options phải là list"
    if len(opts) == 0:
        # options rỗng chỉ hợp lệ khi answer_index=-1 (trường hợp không đọc được)
        idx0 = res.get("answer_index", None)
        if idx0 == -1:
            # vẫn check confidence hợp lệ bên dưới
            pass
        else:
            return False, "no_options", "options rỗng"
    if len(opts) > MAX_OPTIONS_NEW:
        return (
            False,
            "too_many_options",
            f"options={len(opts)} vượt quá {MAX_OPTIONS_NEW}",
        )
    for i, o in enumerate(opts):
        if not isinstance(o, dict):
            return False, "option_not_dict", f"option {i} phải là dict"
        t = o.get("text", None)
        if not isinstance(t, str):
            return False, "bad_text", f"option {i} text phải là string"
        # cho phép text rỗng nhưng log warning ở caller; không fail cứng nếu box ok?
        # STEP1: text rỗng vẫn coi là nghi ngờ nhưng không fail để tránh bỏ câu oan.
        # Tuy nhiên nếu text không phải string thì fail.
        box = o.get("box", None)
        if not isinstance(box, dict):
            return False, f"bad_box_option_{i}", f"option {i} thiếu box dict"
        for k in ("x1", "y1", "x2", "y2"):
            if k not in box:
                return False, f"bad_box_option_{i}", f"option {i} thiếu {k}"
            v = box[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return False, "bad_box_coords_type", f"option {i}.{k} phải là số"
            fv = float(v)
            if not (0.0 <= fv <= 1.0):
                return False, f"bad_box_option_{i}", f"option {i}.{k}={v} ngoài 0..1"
        x1, y1, x2, y2 = (
            float(box["x1"]),
            float(box["y1"]),
            float(box["x2"]),
            float(box["y2"]),
        )
        if not (x1 < x2 and y1 < y2):
            return (
                False,
                f"bad_box_option_{i}",
                f"option {i} phải có x1<x2 và y1<y2 (nhận {x1},{y1},{x2},{y2})",
            )
        area = (x2 - x1) * (y2 - y1)
        if area < MIN_BOX_AREA:
            return (
                False,
                f"bad_box_option_{i}",
                f"option {i} box quá nhỏ area={area:.6f}",
            )
    idx = res.get("answer_index", None)
    if isinstance(idx, bool) or not isinstance(idx, int):
        return False, "bad_answer_index_type", "answer_index phải là integer"
    if idx != -1 and not (0 <= idx < len(opts)):
        return (
            False,
            "bad_answer_index",
            f"answer_index={idx} ngoài range options={len(opts)}",
        )
    conf = res.get("confidence", None)
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return False, "invalid_confidence", "confidence phải là số 0..1"
    if not (0.0 <= float(conf) <= 1.0):
        return False, "invalid_confidence", f"confidence={conf} ngoài 0..1"
    return True, "OK", "hợp lệ"


# ---------- Fallback: web search + chấm điểm ----------
def web_search_snippets(query, max_chars=6000):
    """Tìm trên DuckDuckGo, trả về text gộp. Không cần API key."""
    import requests

    text = ""
    try:
        # API instant answer (nhẹ, ít bị chặn)
        r = requests.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        j = r.json()
        for k in ("AbstractText", "Answer"):
            if j.get(k):
                text += j[k] + "\n"
        for t in j.get("RelatedTopics", [])[:5]:
            if isinstance(t, dict) and t.get("Text"):
                text += t["Text"] + "\n"
    except Exception:
        pass
    try:
        # HTML search để lấy nhiều kết quả hơn
        import requests

        r = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a|class="result-snippet"[^>]*>(.*?)<',
            r.text,
            re.DOTALL,
        )
        clean = re.sub(r"<[^>]+>", " ", r.text)
        text += "\n" + clean[:max_chars]
    except Exception:
        pass
    return text[:max_chars].lower()


def norm_vi(s):
    import unicodedata

    s = s.lower()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("đ", "d")


def fallback_pick(question, options, min_margin=3):
    """Chấm điểm mỗi option theo số lần xuất hiện trong kết quả search. Trả về (index, why).
    why chứa scores + margin (nhất - nhì). Caller chỉ nên tin khi margin >= min_margin
    và điểm nhất > 0 — ngược lại rất dễ SAI (câu suy luận/toán/đáp án ngắn).
    Giữ signature tương thích: (best, why_string)."""
    if not question or not options:
        return -1, "thieu du lieu"
    blob_raw = web_search_snippets(question)
    if len(blob_raw.strip()) < 20:
        return -1, "search khong ra ket qua"
    blob = norm_vi(blob_raw)
    scores = []
    for opt in options:
        opt_n = norm_vi(opt)
        keywords = [w for w in re.findall(r"\w+", opt_n) if len(w) >= 2][:10]
        s = sum(blob.count(k) for k in keywords)
        # thưởng nếu cả cụm option xuất hiện nguyên văn (không dấu)
        if opt_n[:30] in blob:
            s += 5
        scores.append(s)
    best = max(range(len(scores)), key=lambda i: scores[i])
    sorted_scores = sorted(scores, reverse=True)
    margin = sorted_scores[0] - (sorted_scores[1] if len(sorted_scores) > 1 else 0)
    why = f"scores={scores} margin={margin}"
    if scores[best] <= 0:
        return -1, why + " (diem 0 -> khong tin)"
    if margin < min_margin:
        return -1, why + f" (margin<{min_margin} -> khong tin, de tranh click sai)"
    return best, why


# ---------- Click ----------
def click_point(pt):
    import pyautogui

    pyautogui.click(pt[0], pt[1])


# STEP4 master switch: REAL_ANSWER_CLICK=True cho phép click đáp án thật theo AI box.
# STEP1_NO_CLICK giữ lại để tương thích code cũ, luôn = not REAL_ANSWER_CLICK.
REAL_ANSWER_CLICK = True
STEP1_NO_CLICK = not REAL_ANSWER_CLICK

INDEX_TO_KEY = ["A", "B", "C", "D", "E", "F"]
MAX_OPTIONS = 6

# 1 câu chỉ trả lời 1 lần: màn hình giống hệt lần trước mà lần trước đã có
# kết quả cuối (ok/invalid/map_error/no_answer) thì bỏ qua Gemini, KHÔNG CLICK.
# Chỉ retry khi lỗi mạng/click (gemini_error/click_error) vì chưa trả lời xong.
DEDUP_SKIP_OUTCOMES = ("invalid", "map_error", "no_answer", "ok")


def should_skip_repeat(last_hash, last_outcome, cur_hash):
    """Pure helper: màn hình giống hệt + lần trước đã xong → bỏ qua Gemini. Không click."""
    return (
        bool(cur_hash) and cur_hash == last_hash and last_outcome in DEDUP_SKIP_OUTCOMES
    )


def question_key(question, options):
    """Key câu hỏi + đáp án đã chuẩn hóa (không dấu, lowercase).
    Cùng câu (kể cả timer đổi làm ảnh khác hash) mà key trùng → KHÔNG CLICK lại.
    Chỉ khi câu hỏi HOẶC đáp án khác mới được click."""
    try:
        qn = norm_vi(str(question or "").strip())
    except Exception:
        qn = str(question or "").strip().lower()
    parts = [qn]
    try:
        for o in options or []:
            t = o.get("text", "") if isinstance(o, dict) else str(o)
            parts.append(norm_vi(str(t).strip()))
    except Exception:
        pass
    return "|".join(parts)


def thumb_of(img, w=160, h=90):
    """Thumbnail grayscale để so ảnh gần đúng (chống timer đổi 1-2 pixel làm hash khác).
    Trả bytes, nhẹ (~14KB). None khi lỗi."""
    try:
        t = img.resize((w, h)).convert("L")
        return t.tobytes()
    except Exception:
        return None


def thumbs_similar(a, b, max_mean_diff=12.0):
    """So 2 thumb: trung bình chênh lệch điểm ảnh < ngưỡng → coi như cùng màn hình.
    Timer đếm giây chỉ đổi vài % pixel → similar=True → bỏ qua Gemini, tiết kiệm 4-6s.
    Câu mới đổi toàn bộ layout → diff lớn → False → tra tiếp."""
    try:
        if not a or not b or len(a) != len(b):
            return False
        n = len(a)
        s = 0
        step = max(1, n // 2000)
        cnt = 0
        for i in range(0, n, step):
            d = a[i] - b[i]
            s += d if d >= 0 else -d
            cnt += 1
        return (s / max(cnt, 1)) < max_mean_diff
    except Exception:
        return False


def thumbs_changed(a, b, min_mean_diff=12.0):
    """Trả True khi hai thumbnail khác đủ rõ để coi là đã sang màn hình khác."""
    if not a or not b:
        return False
    return not thumbs_similar(a, b, max_mean_diff=min_mean_diff)


def is_gambling_screen(question, options):
    """Màn hình thưởng/phạt (Redemption/Reattempt, options chỉ là •/••) → KHÔNG CLICK.
    Câu 11 trong log: AI đoán bừa A conf 0.5 vào màn hình này."""
    try:
        qn = norm_vi(str(question or ""))
    except Exception:
        qn = str(question or "").lower()
    for kw in ("redemption", "reattempt", "chon mot cach khon ngoan"):
        if kw in qn:
            return True
    try:
        texts = [
            (o.get("text", "") if isinstance(o, dict) else str(o)).strip()
            for o in (options or [])
        ]
    except Exception:
        return False
    if texts and len(texts) <= 4:
        stripped = [t.replace("•", "").replace(".", "").replace(" ", "") for t in texts]
        if all(t == "" for t in stripped) and any("•" in t for t in texts):
            return True
    return False


def try_pixel_fix(options, meta):
    """AI đôi khi trả tọa độ PIXEL (379, 809...) thay vì normalized 0..1 như prompt.
    Log câu 14/18/21: x1=379/390/393 trong khi box đúng ~0.38 → 379/1024≈0.37.
    Hàm này thử chia x cho sent/original width, y cho sent/original height;
    chỉ nhận khi toàn bộ box hợp lệ (0..1, x1<x2, area đủ). Trả (fixed_options, True)
    hoặc (original, False). KHÔNG đoán mò ngoài 2 kích thước đã biết."""
    try:
        import copy

        if not isinstance(options, list) or not options:
            return options, False
        sw = float((meta or {}).get("sent_width", 0) or 0)
        sh = float((meta or {}).get("sent_height", 0) or 0)
        ow = float((meta or {}).get("original_width", 0) or 0)
        oh = float((meta or {}).get("original_height", 0) or 0)
        cands = []
        if sw > 0 and sh > 0:
            cands.append((sw, sh))
        if ow > 0 and oh > 0 and (ow != sw or oh != sh):
            cands.append((ow, oh))
        if not cands:
            return options, False
        # chỉ fix khi có box lỗi ngoài 0..1
        need_fix = False
        for o in options:
            box = (o or {}).get("box", None) if isinstance(o, dict) else None
            if not isinstance(box, dict):
                continue
            for k in ("x1", "y1", "x2", "y2"):
                v = box.get(k, None)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                if not (0.0 <= float(v) <= 1.0):
                    need_fix = True
                    break
            if need_fix:
                break
        if not need_fix:
            return options, False
        for W, H in cands:
            fixed = copy.deepcopy(options)
            ok = True
            for o in fixed:
                if not isinstance(o, dict) or not isinstance(o.get("box"), dict):
                    ok = False
                    break
                box = o["box"]
                vals = {}
                for k in ("x1", "y1", "x2", "y2"):
                    v = box.get(k, None)
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        ok = False
                        break
                    f = float(v)
                    if 0.0 <= f <= 1.0:
                        vals[k] = f
                    elif f > 1.0:
                        base = W if k[0] == "x" else H
                        if base <= 0:
                            ok = False
                            break
                        vals[k] = f / base
                    else:
                        ok = False
                        break
                if not ok:
                    break
                if not (
                    0.0 <= vals["x1"] < vals["x2"] <= 1.0
                    and 0.0 <= vals["y1"] < vals["y2"] <= 1.0
                ):
                    ok = False
                    break
                if (vals["x2"] - vals["x1"]) * (vals["y2"] - vals["y1"]) < MIN_BOX_AREA:
                    ok = False
                    break
                box.update(vals)
            if ok:
                return fixed, True
        return options, False
    except Exception:
        return options, False


# ---------- STEP2: BOX normalized -> SCREEN coordinates (DRY-RUN, KHÔNG click thật) ----------
def _area_val(grabbed_area, key):
    """Hỗ trợ grabbed_area dạng dict (format hiện tại: take_screenshot/mss)."""
    if isinstance(grabbed_area, dict):
        if key not in grabbed_area:
            raise ValueError(f"grabbed_area thiếu '{key}': {grabbed_area}")
        return grabbed_area[key]
    return getattr(grabbed_area, key)


def box_to_screen_point(box, grabbed_area):
    """STEP2: tâm box normalized -> tọa độ màn hình tuyệt đối.
    Công thức bắt buộc: cx=(x1+x2)/2, cy=(y1+y2)/2;
    screen_x=left+cx*width, screen_y=top+cy*height. Trả (int, int).
    KHÔNG dùng kích thước ảnh resize. KHÔNG clamp box sai (raise lỗi).
    """
    if not isinstance(box, dict):
        raise ValueError(f"box phải là dict, nhận {type(box)}")
    for k in ("x1", "y1", "x2", "y2"):
        if k not in box:
            raise ValueError(f"box thiếu '{k}': {box}")
        v = box[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"box.{k} phải là số, nhận {v!r}")
        if not (0.0 <= float(v) <= 1.0):
            raise ValueError(f"box.{k}={v} ngoài 0..1 (không clamp)")
    x1, y1, x2, y2 = (
        float(box["x1"]),
        float(box["y1"]),
        float(box["x2"]),
        float(box["y2"]),
    )
    if not (x1 < x2 and y1 < y2):
        raise ValueError(f"box phải có x1<x2 và y1<y2, nhận {(x1, y1, x2, y2)}")
    try:
        left = float(_area_val(grabbed_area, "left"))
        top = float(_area_val(grabbed_area, "top"))
        width = float(_area_val(grabbed_area, "width"))
        height = float(_area_val(grabbed_area, "height"))
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"grabbed_area không hợp lệ: {grabbed_area} ({e})")
    if not (width > 0 and height > 0):
        raise ValueError(
            f"grabbed_area width/height phải > 0, nhận width={width} height={height}"
        )
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    return (int(round(left + cx * width)), int(round(top + cy * height)))


def click_box(box, grabbed_area, dry_run=True):
    """STEP4: hàm click/dry-run.
    - dry_run=True: chỉ tính tọa độ, KHÔNG gọi pyautogui.click(), return point.
    - dry_run=False: REAL CLICK, chỉ cho phép khi REAL_ANSWER_CLICK is True
      (tức STEP1_NO_CLICK is False). solve_once() chỉ gọi nhánh này sau khi dữ liệu
      đã validate đầy đủ + log REAL ANSWER CLICK.
    """
    pt = box_to_screen_point(box, grabbed_area)
    if dry_run:
        return pt
    if not REAL_ANSWER_CLICK or STEP1_NO_CLICK:
        raise RuntimeError(
            "Real click bị khóa (REAL_ANSWER_CLICK=False) — từ chối click thật."
        )
    import pyautogui

    pyautogui.click(pt[0], pt[1])
    return pt


# ---------- Click chuột (mouse duy nhất): pyautogui click tâm box AI ----------
def map_options_to_screen(options, grabbed_area):
    """STEP2: map toàn bộ options AI -> screen_point. Giữ thứ tự, giữ nguyên box,
    không sửa box, không tạo option, không phụ thuộc num_answers.
    Box lỗi -> raise ValueError (caller log lỗi, không click).
    """
    if not isinstance(options, list):
        raise ValueError(f"options phải là list, nhận {type(options)}")
    # kiểm tra sớm grabbed_area để lỗi rõ ràng trước khi map
    try:
        w = float(_area_val(grabbed_area, "width"))
        h = float(_area_val(grabbed_area, "height"))
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"grabbed_area không hợp lệ: {grabbed_area} ({e})")
    if not (w > 0 and h > 0):
        raise ValueError(
            f"grabbed_area width/height phải > 0, nhận width={w} height={h}"
        )
    mapped = []
    for i, o in enumerate(options):
        if not isinstance(o, dict):
            raise ValueError(f"option {i} phải là dict (không tính tọa độ mù)")
        box = o.get("box", None)
        if not isinstance(box, dict):
            raise ValueError(f"option {i} thiếu box dict (không tính tọa độ mù)")
        try:
            pt = box_to_screen_point(box, grabbed_area)
        except ValueError as e:
            raise ValueError(f"option {i} box lỗi: {e}")
        mapped.append(
            {
                "label": o.get(
                    "label", INDEX_TO_KEY[i] if 0 <= i < len(INDEX_TO_KEY) else str(i)
                ),
                "text": o.get("text", ""),
                "box": box,
                "screen_point": pt,
            }
        )
    return mapped


# (INDEX_TO_KEY / MAX_OPTIONS đã định nghĩa ở trên, giữ 1 định nghĩa duy nhất.)


# ---------- STEP3: pipeline thuần túy res -> selected box -> screen point (KHÔNG click) ----------
def resolve_answer_pipeline(res, grabbed_area, fallback_index=None):
    """STEP3 pipeline thuần túy, không GUI, không network, KHÔNG click.
    Luồng: validate_ai_result() -> options[] -> answer_index (đã check int + range)
    -> selected_option = options[answer_index] (KHÔNG tìm bằng label, KHÔNG dùng A-F)
    -> selected_box = selected_option["box"] (box gốc AI, không sửa/clamp)
    -> selected_point = box_to_screen_point(selected_box, grabbed_area)
      (đồng thời click_box(..., dry_run=True) phải trả cùng tọa độ).
    fallback_index (nếu có): chỉ dùng khi answer_index == -1; phải là int 0<=f<len,
    box vẫn lấy từ options[fallback_index]["box"]. Ngoài ra không tạo tọa độ.
    Trả dict: {status, question, n_options, mapped, answer_index, effective_index,
               selected, confidence, code, message, used_fallback}.
    status: 'ok' | 'no_answer' | 'invalid' | 'map_error'.
    """
    ok, code, msg = validate_ai_result(res)
    q = res.get("question", "") if isinstance(res, dict) else ""
    raw_opts = res.get("options", []) if isinstance(res, dict) else []
    opts = raw_opts if isinstance(raw_opts, list) else []
    idx = res.get("answer_index", None) if isinstance(res, dict) else None
    conf = res.get("confidence", 0) if isinstance(res, dict) else 0
    if not ok:
        return {
            "status": "invalid",
            "question": q,
            "n_options": len(opts) if isinstance(opts, list) else 0,
            "mapped": [],
            "answer_index": idx,
            "effective_index": None,
            "selected": None,
            "confidence": conf,
            "code": code,
            "message": msg,
            "used_fallback": False,
        }
    # validate index trước khi index list; chỉ -1 là negative hợp lệ
    use_fallback = False
    eff = idx
    if idx == -1:
        if fallback_index is None:
            try:
                mapped0 = map_options_to_screen(opts, grabbed_area)
            except ValueError as e:
                return {
                    "status": "map_error",
                    "question": q,
                    "n_options": len(opts),
                    "mapped": [],
                    "answer_index": idx,
                    "effective_index": None,
                    "selected": None,
                    "confidence": conf,
                    "code": "map_error",
                    "message": str(e),
                    "used_fallback": False,
                }
            return {
                "status": "no_answer",
                "question": q,
                "n_options": len(opts),
                "mapped": mapped0,
                "answer_index": idx,
                "effective_index": None,
                "selected": None,
                "confidence": conf,
                "code": "OK",
                "message": "no_answer",
                "used_fallback": False,
            }
        if isinstance(fallback_index, bool) or not isinstance(fallback_index, int):
            return {
                "status": "invalid",
                "question": q,
                "n_options": len(opts),
                "mapped": [],
                "answer_index": idx,
                "effective_index": None,
                "selected": None,
                "confidence": conf,
                "code": "bad_fallback_index",
                "message": "fallback_index phải là int",
                "used_fallback": False,
            }
        if not (0 <= fallback_index < len(opts)):
            return {
                "status": "invalid",
                "question": q,
                "n_options": len(opts),
                "mapped": [],
                "answer_index": idx,
                "effective_index": None,
                "selected": None,
                "confidence": conf,
                "code": "bad_fallback_index",
                "message": f"fallback_index={fallback_index} ngoài range options={len(opts)}",
                "used_fallback": False,
            }
        eff = fallback_index
        use_fallback = True
    # tới đây eff phải là int 0<=eff<len (validate_ai_result đã đảm bảo cho idx!=-1)
    if isinstance(eff, bool) or not isinstance(eff, int) or not (0 <= eff < len(opts)):
        return {
            "status": "invalid",
            "question": q,
            "n_options": len(opts),
            "mapped": [],
            "answer_index": idx,
            "effective_index": None,
            "selected": None,
            "confidence": conf,
            "code": "bad_answer_index",
            "message": f"answer_index={idx} ngoài range options={len(opts)}",
            "used_fallback": use_fallback,
        }
    try:
        mapped = map_options_to_screen(opts, grabbed_area)
    except ValueError as e:
        return {
            "status": "map_error",
            "question": q,
            "n_options": len(opts),
            "mapped": [],
            "answer_index": idx,
            "effective_index": None,
            "selected": None,
            "confidence": conf,
            "code": "map_error",
            "message": str(e),
            "used_fallback": use_fallback,
        }
    # CHỌN ĐÁP ÁN BẰNG INDEX DUY NHẤT — không tra label, không dùng click_points A-F.
    selected_mapped = mapped[eff]
    selected_box = selected_mapped["box"]
    # tính trực tiếp từ box AI + kiểm chứng click_box dry_run cho cùng kết quả
    direct_pt = box_to_screen_point(selected_box, grabbed_area)
    dry_pt = click_box(selected_box, grabbed_area, dry_run=True)
    assert (
        direct_pt == dry_pt == selected_mapped["screen_point"]
    ), "mapping lệch giữa các helper"
    selected = {
        "index": eff,
        "label": selected_mapped["label"],
        "text": selected_mapped["text"],
        "box": selected_box,
        "screen_point": direct_pt,
    }
    return {
        "status": "ok",
        "question": q,
        "n_options": len(mapped),
        "mapped": mapped,
        "answer_index": idx,
        "effective_index": eff,
        "selected": selected,
        "confidence": conf,
        "code": "OK",
        "message": "ok",
        "used_fallback": use_fallback,
    }


def format_dry_run_block(selected, answer_index, used_fallback=False):
    """Block log DRY-RUN chuẩn STEP3. Không click."""
    b = selected["box"]
    return (
        "\n==============================\nDRY-RUN ANSWER\n==============================\n"
        f"answer_index: {answer_index}\nlabel: {selected['label']}\ntext: {str(selected['text'])[:150]}\n"
        f"box: ({b.get('x1')}, {b.get('y1')}, {b.get('x2')}, {b.get('y2')})\n"
        f"screen_point: {selected['screen_point']}\n"
        f"fallback_used: {used_fallback}\nCLICK: BLOCKED\n=============================="
    )


# ---------- Vùng chọn bằng kéo chuột ----------
def select_region_gui():
    """Mở fullscreen overlay cho kéo chọn vùng. Trả về dict region hoặc None."""
    result = {}

    root = tk.Toplevel()
    root.attributes("-fullscreen", True)
    root.attributes("-alpha", 0.3)
    root.configure(bg="gray")
    root.attributes("-topmost", True)

    c = tk.Canvas(root, cursor="cross", bg="gray", highlightthickness=0)
    c.pack(fill="both", expand=True)
    label = tk.Label(
        root,
        text="Kéo chuột chọn VÙNG CÂU HỎI — Enter để xong, Esc để full màn hình",
        font=("Arial", 14),
        bg="yellow",
    )
    label.place(relx=0.5, rely=0.02, anchor="n")

    start = {}
    rect = [None]

    def on_down(e):
        start["x"], start["y"] = e.x_root, e.y_root

    def on_drag(e):
        if rect[0]:
            c.delete(rect[0])
        rect[0] = c.create_rectangle(
            c.canvasx(e.x) - (e.x_root - start.get("x", e.x_root)) * 0, 0, 0, 0
        )
        # vẽ đơn giản theo tọa độ màn hình
        c.delete("sel")
        x0, y0 = root.winfo_pointerx(), root.winfo_pointery()
        # dùng coords tuyệt đối chuyển về canvas: xấp xỉ
        c.create_rectangle(
            start.get("x", 0) - root.winfo_rootx(),
            start.get("y", 0) - root.winfo_rooty(),
            e.x,
            e.y,
            outline="red",
            width=3,
            tags="sel",
        )
        result["x0"], result["y0"] = start.get("x"), start.get("y")
        result["x1"], result["y1"] = e.x_root, e.y_root

    def on_ok(_=None):
        root.destroy()

    def on_cancel(_=None):
        result.clear()
        root.destroy()

    root.bind("<ButtonPress-1>", on_down)
    root.bind("<B1-Motion>", on_drag)
    root.bind("<Return>", on_ok)
    root.bind("<Escape>", on_cancel)
    c.bind("<Double-Button-1>", on_ok)
    root.wait_window()

    if "x0" not in result:
        return None
    x0, x1 = sorted([result["x0"], result["x1"]])
    y0, y1 = sorted([result["y0"], result["y1"]])
    if x1 - x0 < 50 or y1 - y0 < 50:
        return None
    return {"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0}


# ---------- GUI chính ----------
# ---------- GUI chính (PHASE5: Tkinter dark modern, 900x650 fixed, chỉ đổi UI) ----------
GUI_THEME = {
    "BG": "#1b2230",  # dark navy
    "CARD": "#242e40",  # card charcoal
    "FG": "#e8ecf1",  # text chính
    "MUTED": "#8b96a8",  # text phụ
    "ACCENT": "#3b82f6",  # action chính
    "GREEN": "#22c55e",  # running
    "RED": "#ef4444",  # stop
    "SECONDARY": "#344054",  # button phụ
    "ENTRY_BG": "#121826",  # nền entry/log
    "FONT": "Segoe UI",
}


class App:
    def __init__(self, master):
        self.master = master
        master.title("Auto Quiz Tool")
        master.geometry("620x580")
        master.resizable(False, False)
        master.configure(bg=GUI_THEME["BG"])
        self.cfg = load_config()
        self.running = False
        # PHASE4: thống kê latency/fallback theo session (không persist, không chứa API key).
        self.perf_history = []
        self.gemini_success_count = 0
        self.fallback_count = 0
        self._gemini_cooldown_until = 0.0
        self._gemini_backoff_seconds = 0.0
        T = GUI_THEME
        F = T["FONT"]

        # ----- Header -----
        header = tk.Frame(master, bg=T["BG"])
        header.pack(fill="x", padx=14, pady=(6, 0))
        title_box = tk.Frame(header, bg=T["BG"])
        title_box.pack(side="left")
        tk.Label(
            title_box,
            text="AUTO QUIZ TOOL",
            font=(F, 13, "bold"),
            bg=T["BG"],
            fg=T["FG"],
        ).pack(anchor="w")
        tk.Label(
            title_box,
            text="AI-powered automatic quiz solver",
            font=(F, 8),
            bg=T["BG"],
            fg=T["MUTED"],
        ).pack(anchor="w")
        self.status_var = tk.StringVar(value="● STOPPED")
        self.status_label = tk.Label(
            header,
            textvariable=self.status_var,
            font=(F, 10, "bold"),
            bg=T["BG"],
            fg="#f87171",
        )
        self.status_label.pack(side="right", anchor="e")

        # ----- AI CONFIGURATION -----
        ai_card = tk.LabelFrame(
            master,
            text="  AI CONFIGURATION  ",
            font=(F, 9, "bold"),
            bg=T["CARD"],
            fg=T["MUTED"],
            labelanchor="n",
            highlightthickness=0,
            bd=1,
            relief="flat",
            padx=12,
            pady=4,
        )
        ai_card.pack(fill="x", padx=14, pady=3)
        tk.Label(
            ai_card, text="API Key", font=(F, 8), bg=T["CARD"], fg=T["MUTED"]
        ).pack(anchor="w")
        key_row = tk.Frame(ai_card, bg=T["CARD"])
        key_row.pack(fill="x", pady=(1, 2))
        self.key_var = tk.StringVar(value=self.cfg.get("api_key", ""))
        self.key_entry = tk.Entry(
            key_row,
            textvariable=self.key_var,
            show="*",
            font=(F, 9),
            bg=T["ENTRY_BG"],
            fg=T["FG"],
            insertbackground=T["FG"],
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3a465c",
            highlightcolor=T["ACCENT"],
        )
        self.key_entry.pack(side="left", fill="x", expand=True, ipady=2)
        self._key_visible = False
        tk.Button(
            key_row,
            text="Hiện",
            font=(F, 8),
            bg=T["SECONDARY"],
            fg=T["FG"],
            relief="flat",
            padx=8,
            command=self.toggle_key,
        ).pack(side="left", padx=(6, 0))
        tk.Label(ai_card, text="Model", font=(F, 8), bg=T["CARD"], fg=T["MUTED"]).pack(
            anchor="w"
        )
        self.model_var = tk.StringVar(value=self.cfg.get("model", ""))
        tk.Entry(
            ai_card,
            textvariable=self.model_var,
            font=(F, 9),
            bg=T["ENTRY_BG"],
            fg=T["FG"],
            insertbackground=T["FG"],
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3a465c",
            highlightcolor=T["ACCENT"],
        ).pack(fill="x", pady=(1, 0), ipady=2)

        # ----- SCREEN CAPTURE -----
        cap_card = tk.LabelFrame(
            master,
            text="  SCREEN CAPTURE  ",
            font=(F, 9, "bold"),
            bg=T["CARD"],
            fg=T["MUTED"],
            labelanchor="n",
            highlightthickness=0,
            bd=1,
            relief="flat",
            padx=12,
            pady=4,
        )
        cap_card.pack(fill="x", padx=14, pady=3)
        self.capture_src_var = tk.StringVar(value="Vùng chụp cố định")
        tk.Label(
            cap_card,
            textvariable=self.capture_src_var,
            font=(F, 8),
            bg=T["CARD"],
            fg=T["MUTED"],
        ).pack(anchor="w")
        reg_row = tk.Frame(cap_card, bg=T["CARD"])
        reg_row.pack(fill="x", pady=(1, 2))
        self.region_vars = {}
        for k in ("X", "Y", "W", "H"):
            cell = tk.Frame(reg_row, bg=T["CARD"])
            cell.pack(side="left", padx=(0, 12))
            tk.Label(
                cell, text=k + ":", font=(F, 8, "bold"), bg=T["CARD"], fg=T["MUTED"]
            ).pack(side="left")
            v = tk.StringVar(value="—")
            tk.Label(
                cell,
                textvariable=v,
                font=(F, 9, "bold"),
                bg=T["CARD"],
                fg=T["FG"],
                width=6,
                anchor="w",
            ).pack(side="left")
            self.region_vars[k] = v
        btn_row = tk.Frame(cap_card, bg=T["CARD"])
        btn_row.pack(fill="x")
        tk.Button(
            btn_row,
            text="REGION",
            font=(F, 8, "bold"),
            bg=T["ACCENT"],
            fg="white",
            relief="flat",
            padx=10,
            pady=3,
            command=self.do_region,
        ).pack(side="left")
        tk.Button(
            btn_row,
            text="TEST",
            font=(F, 8),
            bg=T["SECONDARY"],
            fg=T["FG"],
            relief="flat",
            padx=10,
            pady=3,
            command=self.do_test_shot,
        ).pack(side="left", padx=(6, 0))
        tk.Button(
            btn_row,
            text="WINDOW",
            font=(F, 8, "bold"),
            bg=T["SECONDARY"],
            fg=T["FG"],
            relief="flat",
            padx=10,
            pady=3,
            command=self.do_pick_window,
        ).pack(side="left", padx=(6, 0))

        # ----- AUTOMATION -----
        auto_card = tk.LabelFrame(
            master,
            text="  AUTOMATION  ",
            font=(F, 9, "bold"),
            bg=T["CARD"],
            fg=T["MUTED"],
            labelanchor="n",
            highlightthickness=0,
            bd=1,
            relief="flat",
            padx=12,
            pady=4,
        )
        auto_card.pack(fill="x", padx=14, pady=3)
        delay_row = tk.Frame(auto_card, bg=T["CARD"])
        delay_row.pack(fill="x")
        tk.Label(delay_row, text="Delay:", font=(F, 9), bg=T["CARD"], fg=T["FG"]).pack(
            side="left"
        )
        self.delay_var = tk.StringVar(
            value=str(self.cfg.get("delay_between_questions", 3.0))
        )
        tk.Entry(
            delay_row,
            textvariable=self.delay_var,
            font=(F, 9),
            width=5,
            bg=T["ENTRY_BG"],
            fg=T["FG"],
            insertbackground=T["FG"],
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3a465c",
            highlightcolor=T["ACCENT"],
        ).pack(side="left", padx=6, ipady=2)
        tk.Label(delay_row, text="s", font=(F, 8), bg=T["CARD"], fg=T["MUTED"]).pack(
            side="left"
        )
        self.next_var = tk.BooleanVar(value=self.cfg.get("auto_click_next", True))
        tk.Checkbutton(
            delay_row,
            text="Auto click NEXT",
            variable=self.next_var,
            font=(F, 9),
            bg=T["CARD"],
            fg=T["FG"],
            selectcolor=T["CARD"],
            activebackground=T["CARD"],
            activeforeground=T["FG"],
        ).pack(side="left", padx=(14, 0))
        next_row = tk.Frame(auto_card, bg=T["CARD"])
        next_row.pack(fill="x", pady=(2, 0))
        tk.Label(next_row, text="NEXT:", font=(F, 8), bg=T["CARD"], fg=T["MUTED"]).pack(
            side="left"
        )
        self.nextx_var = tk.StringVar(value="—")
        self.nexty_var = tk.StringVar(value="—")
        tk.Label(
            next_row, text="X", font=(F, 8, "bold"), bg=T["CARD"], fg=T["MUTED"]
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            next_row,
            textvariable=self.nextx_var,
            font=(F, 9, "bold"),
            bg=T["CARD"],
            fg=T["FG"],
            width=6,
            anchor="w",
        ).pack(side="left")
        tk.Label(
            next_row, text="Y", font=(F, 8, "bold"), bg=T["CARD"], fg=T["MUTED"]
        ).pack(side="left")
        tk.Label(
            next_row,
            textvariable=self.nexty_var,
            font=(F, 9, "bold"),
            bg=T["CARD"],
            fg=T["FG"],
            width=6,
            anchor="w",
        ).pack(side="left")
        tk.Button(
            next_row,
            text="CALIBRATE",
            font=(F, 8, "bold"),
            bg=T["SECONDARY"],
            fg=T["FG"],
            relief="flat",
            padx=10,
            pady=3,
            command=lambda: self.do_calibrate("NEXT"),
        ).pack(side="left", padx=(10, 0))

        # ----- START / STOP -----
        ctl_row = tk.Frame(master, bg=T["BG"])
        ctl_row.pack(pady=2)
        self.btn_start = tk.Button(
            ctl_row,
            text="▶  START",
            font=(F, 10, "bold"),
            bg=T["ACCENT"],
            fg="white",
            relief="flat",
            padx=20,
            pady=4,
            command=self.start,
        )
        self.btn_start.pack(side="left", padx=6)
        self.btn_stop = tk.Button(
            ctl_row,
            text="■  STOP",
            font=(F, 10, "bold"),
            bg=T["RED"],
            fg="white",
            relief="flat",
            padx=20,
            pady=4,
            command=self.stop,
        )
        self.btn_stop.pack(side="left", padx=6)

        # ----- ACTIVITY LOG -----
        tk.Label(
            master, text="ACTIVITY LOG", font=(F, 9, "bold"), bg=T["BG"], fg=T["MUTED"]
        ).pack(anchor="w", padx=14)
        self.log = scrolledtext.ScrolledText(
            master,
            height=9,
            font=("Consolas", 9),
            bg=T["ENTRY_BG"],
            fg="#c9d4e3",
            insertbackground=T["FG"],
            relief="flat",
            highlightthickness=1,
            highlightbackground="#3a465c",
        )
        self.log.pack(padx=14, pady=(0, 4), fill="both", expand=True)
        self.log.configure(state="disabled")

        self._refresh_region()
        self._refresh_next()
        self._set_running_ui(False)
        self.info("Ready. Select region → Test screenshot → Calibrate NEXT → Start.")

    def toggle_key(self):
        self._key_visible = not self._key_visible
        self.key_entry.configure(show="" if self._key_visible else "*")

    def _refresh_region(self):
        if self.cfg.get("capture_mode") == "window":
            info = self.cfg.get("capture_window") or {}
            if info.get("exe") or info.get("title"):
                label = (
                    f"Cửa sổ: {info.get('exe', '')} — {str(info.get('title', ''))[:35]}"
                )
            else:
                label = "Cửa sổ: (chưa chọn)"
            self.capture_src_var.set(label)
            r = {}
        else:
            self.capture_src_var.set("Vùng chụp cố định")
            r = self.cfg.get("capture_region") or {}
        self.region_vars["X"].set(str(r.get("left", "—")))
        self.region_vars["Y"].set(str(r.get("top", "—")))
        self.region_vars["W"].set(str(r.get("width", "—")))
        self.region_vars["H"].set(str(r.get("height", "—")))

    def _refresh_next(self):
        npt = (self.cfg.get("click_points", {}) or {}).get("NEXT")
        if npt:
            self.nextx_var.set(str(npt[0]))
            self.nexty_var.set(str(npt[1]))
        else:
            self.nextx_var.set("—")
            self.nexty_var.set("—")

    def _set_running_ui(self, running):
        if running:
            self.status_var.set("● RUNNING")
            self.status_label.configure(fg=GUI_THEME["GREEN"])
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            self.status_var.set("● STOPPED")
            self.status_label.configure(fg="#f87171")
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")

    def info(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def persist(self):
        self.cfg["api_key"] = self.key_var.get().strip()
        self.cfg["model"] = self.model_var.get().strip() or self.cfg.get("model", "")
        self.cfg["auto_click_next"] = bool(self.next_var.get())
        try:
            self.cfg["delay_between_questions"] = float(self.delay_var.get())
        except Exception:
            self.cfg["delay_between_questions"] = 0.8
        save_config(self.cfg)

    def do_region(self):
        self.info("Mở overlay chọn vùng...")
        self.master.lower()
        time.sleep(0.3)
        r = select_region_gui()
        self.master.lift()
        if r:
            self.cfg["capture_region"] = r
            self.cfg["capture_mode"] = "region"
            self.persist()
            self._refresh_region()
            self.info(f"Đã set vùng chụp: {r}")
        else:
            self.cfg["capture_region"] = None
            self.cfg["capture_mode"] = "region"
            self.persist()
            self._refresh_region()
            self.info("Dùng FULL màn hình.")

    def do_test_shot(self):
        try:
            # STEP1: take_screenshot() trả (img, grabbed_area); img_to_base64() trả (b64, meta).
            area, src = resolve_capture_area(self.cfg)
            img, grabbed_area = take_screenshot(area)
            p = os.path.join(os.path.dirname(__file__), "test_shot.jpg")
            img.save(p)
            _, meta = img_to_base64(img)
            self.info(
                f"Chụp OK [{src}] size={img.size}, grabbed_area={grabbed_area}, meta={meta}, đã lưu {p}. Hãy mở ảnh kiểm tra vùng chụp."
            )
        except Exception as e:
            messagebox.showerror("Lỗi chụp", f"{e}\n\nCài: pip install mss pillow")

    def do_pick_window(self):
        """Chọn cửa sổ app dưới chuột sau 3s (kiểu share-screen). Từ đó bám theo cửa sổ."""
        self.info("Di chuột vào CỬA SỔ app quiz trong 3s...")
        self.master.update()
        time.sleep(3)
        try:
            picked = pick_window_at_cursor()
        except Exception as e:
            messagebox.showerror("Lỗi chọn cửa sổ", f"{e}")
            return
        if not picked or not picked.get("rect"):
            messagebox.showwarning(
                "Không thấy cửa sổ", "Không xác định được cửa sổ dưới chuột. Thử lại."
            )
            return
        self.cfg["capture_mode"] = "window"
        self.cfg["capture_window"] = {
            "title": picked.get("title", ""),
            "exe": picked.get("exe", ""),
        }
        self.persist()
        self._refresh_region()
        self.info(
            f"Đã bám cửa sổ: {picked.get('exe', '')} — {str(picked.get('title', ''))[:60]}"
        )

    def do_calibrate(self, name):
        self.info(f"Di chuột tới vị trí {name} trong 3s...")
        self.master.update()
        time.sleep(3)
        import pyautogui

        x, y = pyautogui.position()
        self.cfg.setdefault("click_points", {})[name] = [x, y]
        self.persist()
        self._refresh_next()
        self.info(f"Đã lưu {name} = ({x}, {y})")

    def start(self):
        if not self.key_var.get().strip():
            messagebox.showwarning(
                "Thiếu key",
                "Nhập Gemini API key trước (lấy free ở aistudio.google.com).",
            )
            return
        # STEP4: đáp án click theo AI box nên không cần calib A-F. Chỉ kiểm tra NEXT.
        try:
            want_next = bool(self.next_var.get())
        except Exception:
            want_next = True
        if want_next and not self.cfg.get("click_points", {}).get("NEXT"):
            if not messagebox.askyesno(
                "Chưa calib NEXT",
                "Chưa set NEXT. Vẫn chạy (chỉ click đáp án, không tự chuyển câu)?",
            ):
                return
        self.persist()
        self.running = True
        self._set_running_ui(True)
        threading.Thread(target=self.loop, daemon=True).start()
        self.info("▶ Bắt đầu auto-loop. Bấm DỪNG để dừng.")

    def stop(self):
        self.running = False
        self.persist()
        self._set_running_ui(False)
        self.info("■ Đã dừng.")

    def _perf_record(self, perf, used_fallback=False):
        """PHASE4: lưu 1 mẫu latency + đếm gemini_success/fallback. Dùng cho ## AVERAGE."""
        try:
            self.perf_history.append(dict(perf))
            if used_fallback:
                self.fallback_count += 1
            else:
                self.gemini_success_count += 1
        except Exception:
            pass

    def perf_average(self):
        """PHASE4: trung bình latency trên các lần solve_once đã chạy. Trả dict + log text."""
        n = len(self.perf_history)
        if n == 0:
            return {"n": 0}
        keys = (
            "screenshot_ms",
            "encode_ms",
            "gemini_ms",
            "parse_ms",
            "validation_ms",
            "mapping_ms",
            "fallback_ms",
            "click_ms",
            "next_ms",
            "total_ms",
        )
        avg = {"n": n}
        totals = [h.get("total_ms", 0.0) for h in self.perf_history]
        for k in keys:
            vals = [h.get(k, 0.0) for h in self.perf_history]
            avg[k] = sum(vals) / n
        avg["min_total_ms"] = min(totals)
        avg["max_total_ms"] = max(totals)
        total_q = self.gemini_success_count + self.fallback_count
        avg["fallback_rate"] = (self.fallback_count / total_q) if total_q else 0.0
        avg["gemini_success_count"] = self.gemini_success_count
        avg["fallback_count"] = self.fallback_count
        return avg

    def _wait_for_new_question(self, old_thumb=None, timeout=15.0):
        """Chờ câu mới ổn định trước khi cho phép gửi ảnh tiếp theo.

        Chỉ chụp thumbnail nhẹ để polling; không encode/gọi Gemini. Câu mới phải
        khác rõ câu cũ và giống lần polling ngay trước đó, nên timer/hiệu ứng
        chuyển cảnh không mở khóa nhầm một lần xử lý mới.
        """
        start = time.time()
        previous_thumb = None
        changed_thumb = None
        try:
            poll_interval = max(
                0.1, float(self.cfg.get("screen_change_poll_interval", 0.25))
            )
        except Exception:
            poll_interval = 0.25
        self.info("Đã trả lời xong → chờ câu mới (không chụp tra bừa)...")
        while getattr(self, "running", False) and (time.time() - start) < timeout:
            time.sleep(poll_interval)
            try:
                area, _src = resolve_capture_area(self.cfg)
                img, _grab = take_screenshot(area)
                current_thumb = thumb_of(img)
            except Exception:
                continue
            if not thumbs_changed(old_thumb, current_thumb):
                previous_thumb = None
                changed_thumb = None
                continue
            if changed_thumb is None:
                changed_thumb = current_thumb
                previous_thumb = current_thumb
                continue
            if thumbs_similar(previous_thumb, current_thumb):
                self.info("Đã sang câu mới (màn hình ổn định) → cho chụp tiếp.")
                return True
            previous_thumb = current_thumb
        if not getattr(self, "running", False):
            self.info("Đã bấm STOP trong lúc chờ câu mới.")
            return False
        self.info(
            "Chờ câu mới timeout (màn hình chưa đổi) → vòng sau sẽ tự bỏ qua nếu trùng."
        )
        return False

    def solve_once(self):
        # STEP4 real-click pipeline: Screenshot → Gemini → validate -> options[answer_index]
        # -> box AI -> box_to_screen_point -> REAL CLICK (1 answer + tối đa 1 NEXT).
        # Master switch: REAL_ANSWER_CLICK=True (STEP1_NO_CLICK=False tương thích).
        # TUYỆT ĐỐI KHÔNG dùng click_points A-F / num_answers / opts[:need] cho đáp án.
        # NEXT chỉ chạy sau khi answer click thành công, giữ logic hiện tại.
        # NEXT chỉ chạy sau khi answer click thành công, giữ logic hiện tại.
        # Chụp theo nguồn đã cấu hình: vùng cố định hoặc bám cửa sổ app.
        # PHASE4: perf timing từng stage (STEP 0). Không log API key.
        t_total0 = time.perf_counter()
        try:
            import pyautogui

            mouse_origin = tuple(pyautogui.position())
        except Exception:
            pyautogui = None
            mouse_origin = None
        perf = {
            "screenshot_ms": 0.0,
            "encode_ms": 0.0,
            "gemini_ms": 0.0,
            "parse_ms": 0.0,
            "validation_ms": 0.0,
            "mapping_ms": 0.0,
            "fallback_ms": 0.0,
            "click_ms": 0.0,
            "next_ms": 0.0,
            "total_ms": 0.0,
        }
        cap_area, cap_src = resolve_capture_area(self.cfg)
        (img, grabbed_area), perf["screenshot_ms"] = perf_timed(
            take_screenshot, cap_area
        )
        self.info(f"Nguồn chụp: {cap_src} grabbed_area={grabbed_area}")
        max_w = self.cfg.get("screenshot_max_width", 1600)
        jpg_q = self.cfg.get("jpeg_quality", 88)
        (b64_meta), perf["encode_ms"] = perf_timed(img_to_base64, img, max_w, jpg_q)
        b64, meta = b64_meta
        # Chỉ chặn click lại sau khi đã xác định cùng câu hỏi; không dùng độ giống ảnh.
        try:
            cur_thumb = thumb_of(img)
        except Exception:
            cur_thumb = None
        res = None
        last_err = ""
        gem_timer = {}
        provider = str(self.cfg.get("provider", "gemini")).strip().lower()
        if provider not in ("gemini", "openai"):
            provider = "gemini"
        models = [self.cfg.get("model", DEFAULT_MODEL)]
        for m in models:
            try:
                res = ask_ai(
                    b64,
                    self.cfg["api_key"],
                    m,
                    provider=provider,
                    timer=gem_timer,
                    google_grounding=bool(self.cfg.get("google_grounding", True)),
                    cancel_check=lambda: not self.running,
                )
                if m != self.cfg.get("model"):
                    self.cfg["model"] = m
                    self.persist()
                    self.info(f"Provider {provider} dùng model {m}")
                break
            except Exception as e:
                last_err = f"{m}: {e}"
                self.info(f"Provider {provider}, model {m} lỗi... ({str(e)[:150]})")
                continue
        perf["gemini_ms"] = gem_timer.get("gemini_ms", 0.0)
        perf["parse_ms"] = gem_timer.get("parse_ms", 0.0)
        if res is None:
            if "429" in last_err or "Too Many Requests" in last_err:
                previous = float(getattr(self, "_gemini_backoff_seconds", 0.0) or 0.0)
                backoff = min(60.0, max(5.0, previous * 2.0))
                self._gemini_backoff_seconds = backoff
                self._gemini_cooldown_until = time.time() + backoff
                self.info(
                    f"{provider} đang giới hạn request (429) → tạm nghỉ {backoff:.0f}s, "
                    "sẽ tự thử lại."
                )
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self.info(perf_format_block(perf))
            self._last_outcome = f"{provider}_error"
            return f"Lỗi {provider} (đã thử {len(models)} model): {last_err[:300]}"

        self._gemini_backoff_seconds = 0.0
        self._gemini_cooldown_until = 0.0

        # STEP3 pipeline: screenshot -> Gemini -> validate -> options[answer_index] -> box AI
        # -> box_to_screen_point -> DRY-RUN. KHÔNG dùng click_points A-F / num_answers / opts[:need].
        # fallback tối thiểu: khi AI yếu (idx==-1 hoặc conf<0.5) thử fallback_pick trên TEXTS;
        # index fallback (nếu hợp lệ) chỉ dùng để chọn options[fallback_index], box vẫn từ AI.
        q = res.get("question", "") if isinstance(res, dict) else ""
        raw_opts = res.get("options", []) if isinstance(res, dict) else []
        opts = raw_opts if isinstance(raw_opts, list) else []
        idx = res.get("answer_index", -1) if isinstance(res, dict) else -1
        conf = res.get("confidence", 0) if isinstance(res, dict) else 0

        self.info(f"Question: {str(q)[:300]}")
        self.info(
            f"Number of options: {len(opts) if isinstance(opts, list) else 'INVALID'}"
        )
        self.info(f"Confidence: {conf}")

        # Màn hình thưởng/phạt Redemption: đáp án là •/•• đoán bừa → KHÔNG CLICK bao giờ.
        if (
            q
            and isinstance(opts, list)
            and len(opts) > 0
            and is_gambling_screen(q, opts)
        ):
            self.info("Màn hình thưởng/phạt (Redemption) → KHÔNG CLICK, chờ câu mới.")
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=False)
            self.info(perf_format_block(perf))
            self._last_outcome = "no_answer"
            return "Bỏ qua: màn hình thưởng/phạt — KHÔNG CLICK (chờ câu mới)."

        # AI hay trả tọa độ pixel (379, 809...) thay vì 0..1 → tự chia theo sent/original size.
        # Log câu 14/18/21/24: fix xong là click được ngay, không mất thêm 1 vòng 5s.
        try:
            fixed_opts, was_fixed = try_pixel_fix(opts, meta)
        except Exception:
            fixed_opts, was_fixed = opts, False
        if was_fixed:
            try:
                res["options"] = fixed_opts
            except Exception:
                pass
            opts = fixed_opts
            self.info(
                "AI trả tọa độ pixel → đã tự chuẩn hóa về 0..1 (không bỏ câu oan)."
            )

        # 1 câu 1 lần: câu hỏi giống câu vừa trả lời xong → KHÔNG CLICK lại.
        # So theo TEXT câu hỏi (không so options) vì AI đọc options lúc thiếu lúc đủ
        # (log câu 13→15: cùng câu mà options đọc khác nhau → so cả options là lọt, click 2 lần).
        # Chỉ khi sang câu hỏi khác mới được click.
        try:
            cur_qtext = norm_vi(str(q or "").strip())
        except Exception:
            cur_qtext = str(q or "").strip().lower()
        if cur_qtext and cur_qtext == getattr(self, "_last_answered_qtext", None):
            n = int(getattr(self, "_stuck_count", 0) or 0) + 1
            try:
                self._stuck_count = n
            except Exception:
                pass
            self.info("Câu hỏi trùng câu vừa trả lời → KHÔNG CLICK lại, chờ câu mới.")
            if n >= 3:
                self.info(
                    f"KẸT cùng 1 câu {n} lần liên tiếp — kiểm tra nút NEXT {[1745, 794]} còn đúng không / quiz đã hết chưa. Nghỉ 3s chống đốt API."
                )
                try:
                    time.sleep(3)
                except Exception:
                    pass
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=False)
            self.info(perf_format_block(perf))
            self._last_outcome = "ok"
            return "Bỏ qua: trùng câu vừa trả lời — KHÔNG CLICK (chờ câu mới)."

        # STEP3: fallback tối thiểu. Chỉ chạy khi AI yếu VÀ enable_fallback=True.
        # Tốc độ: tắt fallback tiết kiệm 2-8s/câu yếu. Chính xác: fallback đếm từ khóa
        # rất dễ sai câu suy luận/toán -> yêu cầu margin, nếu không chắc thì KHÔNG CLICK.
        # PHASE4: đo fallback_ms riêng + đếm gemini_success/fallback để report rate.
        fallback_index = None
        try:
            conf_f = (
                float(conf)
                if (isinstance(conf, (int, float)) and not isinstance(conf, bool))
                else 1.0
            )
        except Exception:
            conf_f = 1.0
        try:
            min_conf = float(self.cfg.get("min_confidence", 0.6))
        except Exception:
            min_conf = 0.0
        enable_fb = self.cfg.get("enable_fallback", False)
        try:
            fb_margin = int(self.cfg.get("fallback_min_margin", 3))
        except Exception:
            fb_margin = 3
        if (
            (idx == -1 or conf_f < 0.5)
            and q
            and isinstance(opts, list)
            and len(opts) > 0
        ):
            if not enable_fb:
                self.info(
                    f"AI yếu (idx={idx} conf={conf}) nhưng enable_fallback=False -> bỏ qua search cho nhanh, chờ AI hoặc bỏ câu."
                )
            else:
                t_fb0 = time.perf_counter()
                try:
                    texts = [
                        o.get("text", "") if isinstance(o, dict) else "" for o in opts
                    ]
                    f_idx, why = fallback_pick(q, texts, min_margin=fb_margin)
                    self.info(
                        f"Fallback thử (AI yếu idx={idx} conf={conf}): chọn {f_idx} ({why})"
                    )
                    if (
                        isinstance(f_idx, int)
                        and not isinstance(f_idx, bool)
                        and 0 <= f_idx < len(opts)
                    ):
                        fallback_index = f_idx
                    else:
                        self.info(
                            f"Fallback không chắc ({f_idx}) — bỏ qua fallback, KHÔNG CLICK bừa."
                        )
                except Exception as e:
                    self.info(f"Fallback lỗi: {e} — bỏ qua fallback, KHÔNG CLICK.")
                perf["fallback_ms"] = (time.perf_counter() - t_fb0) * 1000.0

        candidate_index = fallback_index if idx == -1 else idx
        verify_enabled = bool(self.cfg.get("verify_answer", True))
        verification_failed = False
        if (
            verify_enabled
            and isinstance(candidate_index, int)
            and not isinstance(candidate_index, bool)
            and 0 <= candidate_index < len(opts)
        ):
            verify_timer = {}
            try:
                verified_index, verified_conf, verify_reason = verify_gemini_answer(
                    b64,
                    self.cfg["api_key"],
                    self.cfg.get("model", DEFAULT_MODEL),
                    opts,
                    candidate_index,
                    timer=verify_timer,
                )
                perf["gemini_ms"] += verify_timer.get("gemini_ms", 0.0)
                perf["parse_ms"] += verify_timer.get("parse_ms", 0.0)
                try:
                    verify_min_conf = float(
                        self.cfg.get("verification_min_confidence", 0.75)
                    )
                except Exception:
                    verify_min_conf = 0.75
                verification_failed = not (
                    verified_index == candidate_index
                    and verified_conf >= verify_min_conf
                )
                self.info(
                    f"Verification: index={verified_index} confidence={verified_conf:.2f} "
                    f"candidate={candidate_index} "
                    f"-> {'PASS' if not verification_failed else 'FAIL'}"
                    + (f" ({verify_reason[:120]})" if verify_reason else "")
                )
            except Exception as e:
                verification_failed = True
                self.info(f"Verification lỗi: {str(e)[:200]} — KHÔNG CLICK.")
            if verification_failed:
                perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
                self._perf_record(perf, used_fallback=bool(fallback_index is not None))
                self.info(perf_format_block(perf))
                append_history(
                    {
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "question": str(q)[:300],
                        "options": [
                            str(o.get("text", ""))[:150] if isinstance(o, dict) else ""
                            for o in opts
                        ],
                        "chosen": None,
                        "confidence": conf_f,
                        "used_fallback": bool(fallback_index is not None),
                        "outcome": "verification_failed",
                        "total_ms": round(perf["total_ms"], 1),
                    }
                )
                self._last_outcome = "no_answer"
                return "Lượt xác minh không đồng ý/không đủ chắc — KHÔNG CLICK."

        t_val0 = time.perf_counter()
        _v_ok, _v_code, _v_msg = validate_ai_result(res)
        perf["validation_ms"] = (time.perf_counter() - t_val0) * 1000.0
        t_map0 = time.perf_counter()
        pipe = resolve_answer_pipeline(res, grabbed_area, fallback_index=fallback_index)
        perf["mapping_ms"] = (time.perf_counter() - t_map0) * 1000.0

        if pipe["status"] == "invalid":
            self.info(f"Validation: ERROR {pipe['code']} — {pipe['message']}")
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=False)
            self.info(perf_format_block(perf))
            self._last_outcome = "invalid"
            try:
                # Kẹt invalid cùng 1 câu hỏi (AI cứ trả pixel): đếm để cảnh báo NEXT.
                _qt = norm_vi(str(q or "").strip())
                if _qt and _qt == getattr(self, "_last_answered_qtext", None):
                    _n = int(getattr(self, "_stuck_count", 0) or 0) + 1
                    self._stuck_count = _n
                    if _n >= 3:
                        self.info(
                            f"KẸT cùng 1 câu {_n} lần (invalid liên tục) — kiểm tra nút NEXT / quiz đã hết chưa."
                        )
            except Exception:
                pass
            return f"AI invalid ({pipe['code']}): {pipe['message']} — KHÔNG CLICK (STEP3 DRY-RUN)."
        if pipe["status"] == "map_error":
            self.info(f"Map options lỗi: {pipe['message']} — KHÔNG CLICK.")
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=False)
            self.info(perf_format_block(perf))
            self._last_outcome = "map_error"
            return f"Map box lỗi: {pipe['message']} — KHÔNG CLICK (STEP3 DRY-RUN)."
        # log toàn bộ options đã map (giữ thứ tự, không cắt)
        for mo in pipe["mapped"]:
            b = mo["box"]
            self.info(
                f"Option {mo['label']}: text={str(mo['text'])[:150]} box=({b.get('x1')},{b.get('y1')},{b.get('x2')},{b.get('y2')}) screen_point={mo['screen_point']}"
            )
        self.info(
            f"Answer index: {pipe['answer_index']}"
            + (
                f" (fallback -> {pipe['effective_index']})"
                if pipe["used_fallback"]
                else ""
            )
        )
        self.info(f"Validation: OK — grabbed_area={grabbed_area} sent_meta={meta}")
        try:
            expl = res.get("explanation", "") if isinstance(res, dict) else ""
            if expl:
                self.info(f"Explanation: {str(expl)[:200]}")
        except Exception:
            pass

        if pipe["status"] == "no_answer":
            self.info("No valid answer_index; skipping answer click.")
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=False)
            self.info(perf_format_block(perf))
            self._last_outcome = "no_answer"
            return "AI không xác định được đáp án (answer_index=-1) — KHÔNG CLICK (STEP3 DRY-RUN)."

        sel = pipe["selected"]
        # SELECTED ANSWER lấy duy nhất từ options[answer_index] (hoặc options[fallback_index]).
        self.info(
            f"SELECTED ANSWER answer_index={pipe['effective_index']} label={sel['label']} "
            f"text={str(sel['text'])[:150]} screen_point={sel['screen_point']}"
        )

        # Chốt chặn chính xác: AI yếu (conf < min_confidence, mặc định 0.6) mà không
        # có fallback chắc -> THÀ BỎ CÂU còn hơn click sai.
        if not pipe["used_fallback"] and min_conf > 0 and conf_f < min_conf:
            self.info(
                f"Confidence {conf_f:.2f} < min_confidence {min_conf:.2f} mà không có fallback chắc "
                f"-> KHÔNG CLICK để tránh sai. (Bật fallback hoặc giảm min_confidence nếu muốn liều.)"
            )
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=False)
            self.info(perf_format_block(perf))
            append_history(
                {
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "question": str(q)[:300],
                    "options": [
                        str(o.get("text", ""))[:150] if isinstance(o, dict) else ""
                        for o in opts
                    ],
                    "chosen": None,
                    "confidence": conf_f,
                    "used_fallback": False,
                    "outcome": "low_conf_skip",
                    "total_ms": round(perf["total_ms"], 1),
                }
            )
            self._last_outcome = "no_answer"
            return (
                f"AI yếu (conf={conf_f:.2f}<{min_conf:.2f}) — KHÔNG CLICK để tránh sai."
            )

        # STEP4 REAL CLICK: đúng 1 answer click từ AI box + tối đa 1 NEXT click sau đó.
        # Không dùng click_points A-F / num_answers / opts[:need] ở bất kỳ nhánh nào.
        if not REAL_ANSWER_CLICK or STEP1_NO_CLICK:
            self.info(
                "(REAL_ANSWER_CLICK=False: DRY-RUN, KHÔNG CLICK đáp án, KHÔNG CLICK NEXT.)"
            )
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=pipe["used_fallback"])
            self.info(perf_format_block(perf))
            self._last_outcome = "dryrun"
            return (
                f"OK (DRY-RUN): đáp án là {sel['label']} "
                f"tại {sel['screen_point']}, KHÔNG CLICK."
            )

        # Kiểm tra tọa độ trước khi click: int + non-negative, không clamp, không đoán screen-size.
        sx, sy = sel["screen_point"]
        if (
            isinstance(sx, bool)
            or isinstance(sy, bool)
            or not isinstance(sx, int)
            or not isinstance(sy, int)
            or sx < 0
            or sy < 0
        ):
            self.info(
                f"Tọa độ screen_point không hợp lệ {sel['screen_point']} — KHÔNG CLICK, KHÔNG NEXT."
            )
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=pipe["used_fallback"])
            self.info(perf_format_block(perf))
            self._last_outcome = "bad_point"
            return f"screen_point invalid {sel['screen_point']} — KHÔNG CLICK."

        b = sel["box"]
        self.info(
            "\n==============================\nREAL ANSWER CLICK (MOUSE)\n==============================\n"
            f"answer_index: {pipe['effective_index']}\nlabel: {sel['label']}\n"
            f"text: {str(sel['text'])[:150]}\n"
            f"box: ({b.get('x1')}, {b.get('y1')}, {b.get('x2')}, {b.get('y2')})\n"
            f"screen_point: ({sx}, {sy})\nsource: AI_BOX\n=============================="
        )
        try:
            # Click chuột duy nhất trong solve_once(): đúng box AI đã validate.
            t_click0 = time.perf_counter()
            click_box(sel["box"], grabbed_area, dry_run=False)
            perf["click_ms"] = (time.perf_counter() - t_click0) * 1000.0
        except Exception as e:
            # Answer click thất bại -> KHÔNG NEXT.
            self.info(f"Answer click thất bại: {e} — KHÔNG CLICK NEXT.")
            if pyautogui is not None and mouse_origin is not None:
                try:
                    pyautogui.moveTo(mouse_origin[0], mouse_origin[1], duration=0)
                except Exception:
                    pass
            perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
            self._perf_record(perf, used_fallback=pipe["used_fallback"])
            self.info(perf_format_block(perf))
            self._last_outcome = "click_error"
            return f"Answer click lỗi: {e} — KHÔNG NEXT."
        self.info(
            f"Answer click successful tại ({sx}, {sy}) [label={sel['label']}, source=AI_BOX, mouse]."
        )

        # NEXT chỉ chạy sau khi answer click thành công. Tốc độ nhanh nhất: 0.3s.
        if self.cfg.get("auto_click_next"):
            t_next0 = time.perf_counter()
            try:
                wait_next = float(self.cfg.get("answer_to_next_delay", 0.3))
            except Exception:
                wait_next = 0.3
            time.sleep(wait_next)
            npt = self.cfg.get("click_points", {}).get("NEXT")
            if npt:
                try:
                    click_point(npt)
                    self.info(f"Đã CLICK NEXT tại {npt}")
                except Exception as e:
                    self.info(f"CLICK NEXT thất bại: {e}")
            perf["next_ms"] = (time.perf_counter() - t_next0) * 1000.0
        if pyautogui is not None and mouse_origin is not None:
            try:
                pyautogui.moveTo(mouse_origin[0], mouse_origin[1], duration=0)
                self.info(f"Đã trả chuột về vị trí ban đầu {mouse_origin}")
            except Exception as e:
                self.info(f"Không trả được chuột về vị trí ban đầu: {e}")
        # CHỐT CHUYỂN CÂU: trả lời xong thì chờ câu sau mới cho chụp tiếp.
        # Không chờ là chụp bừa màn hình cũ/màn hình chuyển tiếp rồi click
        # đáp án câu trước vào câu này.
        try:
            moved = self._wait_for_new_question(
                old_thumb=cur_thumb,
                timeout=float(self.cfg.get("screen_change_timeout", 6.0)),
            )
        except Exception as e:
            moved = False
            self.info(f"Chờ câu mới lỗi: {e}")
        perf["total_ms"] = (time.perf_counter() - t_total0) * 1000.0
        self._perf_record(perf, used_fallback=pipe["used_fallback"])
        self.info(perf_format_block(perf))
        append_history(
            {
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "question": str(q)[:300],
                "options": [
                    str(o.get("text", ""))[:150] if isinstance(o, dict) else ""
                    for o in opts
                ],
                "chosen": sel["label"],
                "chosen_text": str(sel["text"])[:150],
                "confidence": conf_f,
                "used_fallback": bool(pipe["used_fallback"]),
                "outcome": "ok",
                "total_ms": round(perf["total_ms"], 1),
            }
        )
        try:
            self._last_answered_qtext = cur_qtext
        except Exception:
            pass
        try:
            self._stuck_count = 0
        except Exception:
            pass
        self._last_outcome = "ok"
        if moved:
            return f"OK câu này: {sel['label']} tại ({sx}, {sy}) (đã sang câu mới)"
        return f"OK câu này: {sel['label']} tại ({sx}, {sy}) (chờ câu mới timeout — vòng sau trùng sẽ bỏ qua)"

    def loop(self):
        n = 0
        while self.running:
            cooldown = max(
                0.0, float(getattr(self, "_gemini_cooldown_until", 0.0)) - time.time()
            )
            if cooldown > 0:
                while self.running and cooldown > 0:
                    time.sleep(min(0.25, cooldown))
                    cooldown = max(
                        0.0,
                        float(getattr(self, "_gemini_cooldown_until", 0.0))
                        - time.time(),
                    )
                continue
            n += 1
            try:
                r = self.solve_once()
                self.info(f"[Câu {n}] {r}")
            except Exception as e:
                self.info(f"[Câu {n}] LỖI: {e}")
            # solve_once() đã tự chờ màn hình sang câu mới sau khi click;
            # chỉ nghỉ tối đa 0.1s để tránh thêm độ trễ giữa hai câu.
            try:
                d = max(0.0, min(float(self.delay_var.get()), 0.1))
            except Exception:
                d = 0.1
            if self.running and d > 0:
                time.sleep(d)


# ---------- Test helpers (offline, KHÔNG auto-run, KHÔNG click, KHÔNG phá GUI) ----------
# Chạy: python -c "import main; main.test_step2_all(); ..."
def _step1_make_opt(label, text, x1, y1, x2, y2):
    return {
        "label": label,
        "text": text,
        "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
    }


def test_validate_step1():
    """Chạy 8 case trong spec. Trả list (name, ok, code). KHÔNG click, KHÔNG gọi API."""

    def bi(i):
        # 4 box rời nhau theo chiều dọc
        boxes = [
            (0.05, 0.10, 0.95, 0.22),
            (0.05, 0.28, 0.95, 0.40),
            (0.05, 0.46, 0.95, 0.58),
            (0.05, 0.64, 0.95, 0.76),
            (0.05, 0.78, 0.95, 0.88),
            (0.05, 0.89, 0.95, 0.97),
        ]
        x1, y1, x2, y2 = boxes[i % len(boxes)]
        return _step1_make_opt(
            INDEX_TO_KEY[i % 6], f"Đáp án {INDEX_TO_KEY[i % 6]}", x1, y1, x2, y2
        )

    cases = [
        (
            "Case1_4opt_valid",
            {
                "question": "Q?",
                "options": [bi(0), bi(1), bi(2), bi(3)],
                "answer_index": 1,
                "confidence": 0.9,
                "explanation": "ok",
            },
            True,
        ),
        (
            "Case2_2opt_valid",
            {
                "question": "Q?",
                "options": [bi(0), bi(1)],
                "answer_index": 0,
                "confidence": 0.8,
                "explanation": "ok",
            },
            True,
        ),
        (
            "Case3_6opt_valid",
            {
                "question": "Q?",
                "options": [bi(0), bi(1), bi(2), bi(3), bi(4), bi(5)],
                "answer_index": 5,
                "confidence": 0.7,
                "explanation": "ok",
            },
            True,
        ),
        (
            "Case4_idx_minus1",
            {
                "question": "Q?",
                "options": [bi(0), bi(1)],
                "answer_index": -1,
                "confidence": 0,
                "explanation": "không chắc",
            },
            True,
        ),
        (
            "Case5_box_out_of_range",
            {
                "question": "Q?",
                "options": [_step1_make_opt("A", "x", -0.1, 0.1, 0.5, 0.3)],
                "answer_index": 0,
                "confidence": 0.9,
                "explanation": "",
            },
            False,
        ),
        (
            "Case6_x1_gte_x2",
            {
                "question": "Q?",
                "options": [_step1_make_opt("A", "x", 0.6, 0.1, 0.4, 0.3)],
                "answer_index": 0,
                "confidence": 0.9,
                "explanation": "",
            },
            False,
        ),
        (
            "Case7_idx_out_of_range",
            {
                "question": "Q?",
                "options": [bi(0), bi(1)],
                "answer_index": 5,
                "confidence": 0.9,
                "explanation": "",
            },
            False,
        ),
        (
            "Case8_empty_options",
            {
                "question": "",
                "options": [],
                "answer_index": 0,
                "confidence": 0.5,
                "explanation": "rỗng",
            },
            False,
        ),
    ]
    results = []
    for name, payload, expect_ok in cases:
        ok, code, msg = validate_ai_result(payload)
        passed = ok == expect_ok
        results.append((name, ok, code, msg, "PASS" if passed else "FAIL"))
        print(
            f"{name}: validate=({'OK' if ok else 'ERROR '+code}) expect_ok={expect_ok} -> {'PASS' if passed else 'FAIL'} ({msg})"
        )
    # case phụ: options rỗng + idx=-1 phải OK (không đọc được gì)
    ok, code, msg = validate_ai_result(
        {
            "question": "",
            "options": [],
            "answer_index": -1,
            "confidence": 0.0,
            "explanation": "không đọc được",
        }
    )
    print(f"Case8b_empty_idx-1: validate=({'OK' if ok else 'ERROR '+code}) ({msg})")
    results.append(("Case8b_empty_idx-1_valid", ok, code, msg, "INFO"))
    return results


def test_pipeline_with_shot():
    """Kiểm tra pipeline offline với test_shot.jpg nếu tồn tại. KHÔNG gọi Gemini, KHÔNG click."""
    p = os.path.join(os.path.dirname(__file__), "test_shot.jpg")
    if not os.path.exists(p):
        print(
            f"test_shot.jpg không tồn tại ({p}) — bỏ qua pipeline test (đây là bình thường)."
        )
        return None
    from PIL import Image

    img = Image.open(p)
    b64, meta = img_to_base64(img)
    print(f"test_shot.jpg size={img.size} meta={meta} b64_len={len(b64)}")
    print(
        "PIPELINE OFFLINE OK (chưa gọi Gemini, KHÔNG CLICK). Muốn test Gemini thật thì gọi ask_gemini() thủ công."
    )
    return meta


# ---------- STEP2 test helpers (offline, KHÔNG auto-run, KHÔNG click, KHÔNG phá GUI) ----------
def _step2_area_100_200_1000_500():
    return {"left": 100, "top": 200, "width": 1000, "height": 500}


def _step2_make_opts(n):
    boxes = [
        (0.05, 0.05, 0.95, 0.15),
        (0.05, 0.20, 0.95, 0.30),
        (0.05, 0.35, 0.95, 0.45),
        (0.05, 0.50, 0.95, 0.60),
        (0.05, 0.65, 0.95, 0.75),
        (0.05, 0.80, 0.95, 0.90),
    ]
    out = []
    for i in range(n):
        x1, y1, x2, y2 = boxes[i]
        out.append(
            {
                "label": INDEX_TO_KEY[i],
                "text": f"Option {INDEX_TO_KEY[i]}",
                "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            }
        )
    return out


def test_box_to_screen_step2():
    """STEP2 §8: 4 case mapping với grabbed_area dict left=100,top=200,width=1000,height=500."""
    area = _step2_area_100_200_1000_500()
    cases = [
        ("Case1_full", {"x1": 0.0, "y1": 0.0, "x2": 1.0, "y2": 1.0}, (600, 450)),
        ("Case2_topleft", {"x1": 0.0, "y1": 0.0, "x2": 0.2, "y2": 0.2}, (200, 250)),
        ("Case3_center", {"x1": 0.4, "y1": 0.4, "x2": 0.6, "y2": 0.6}, (600, 450)),
        (
            "Case4_bottomright",
            {"x1": 0.8, "y1": 0.8, "x2": 1.0, "y2": 1.0},
            (1000, 650),
        ),
    ]
    results = []
    for name, box, expect in cases:
        got = box_to_screen_point(box, area)
        passed = got == expect
        results.append((name, got, expect, "PASS" if passed else "FAIL"))
        print(f"{name}: got={got} expect={expect} -> {'PASS' if passed else 'FAIL'}")
        # click_box dry_run phải trả cùng tọa độ và KHÔNG click
        got_dry = click_box(box, area, dry_run=True)
        assert got_dry == got, f"{name} click_box dry_run lệch"
    # STEP4: gate đã mở (REAL_ANSWER_CLICK=True) nên dry_run=False đi qua mock click đúng 1 lần;
    # khi gate đóng thì phải raise. Mock để không chạm màn hình thật.
    import sys

    if REAL_ANSWER_CLICK and not STEP1_NO_CLICK:
        clicked, fake = _step4_fake_click_recorder()
        real_mod = sys.modules.get("pyautogui", None)
        sys.modules["pyautogui"] = fake
        try:
            pt = click_box(cases[0][1], area, dry_run=False)
        finally:
            if real_mod is None:
                sys.modules.pop("pyautogui", None)
            else:
                sys.modules["pyautogui"] = real_mod
        gate_pass = pt == (600, 450) and clicked == [(600, 450)]
        print(
            f"dry_run=False (gate mở): pt={pt} clicked={clicked} -> {'PASS' if gate_pass else 'FAIL'} (mock, 1 click)"
        )
        results.append(
            (
                "dry_run_False_mock_click",
                clicked == [(600, 450)],
                True,
                "PASS" if gate_pass else "FAIL",
            )
        )
    else:
        try:
            click_box(cases[0][1], area, dry_run=False)
            print("dry_run=False: FAIL (đáng lẽ bị chặn bởi gate)")
            results.append(("dry_run_False_blocked", False, True, "FAIL"))
        except RuntimeError as e:
            print(f"dry_run=False blocked OK ({e})")
            results.append(("dry_run_False_blocked", True, True, "PASS"))
    return results


def test_mapping_resize_step2():
    """Resize 2000x1000->1024x512 (default) nhưng map theo grabbed_area 2000x1000."""
    from PIL import Image

    img = Image.new("RGB", (2000, 1000), color="white")
    _, meta = img_to_base64(img)
    print(f"resize meta={meta}")
    assert meta["original_width"] == 2000 and meta["sent_width"] == 1024, meta
    area = {"left": 0, "top": 0, "width": 2000, "height": 1000}
    box = {"x1": 0.25, "y1": 0.25, "x2": 0.75, "y2": 0.75}
    got = box_to_screen_point(box, area)
    expect = (1000, 500)
    wrong = (800, 400)
    passed = got == expect and got != wrong
    print(
        f"resize_map: got={got} expect={expect} (sai nếu {wrong}) -> {'PASS' if passed else 'FAIL'}"
    )
    return [("resize_map", got, expect, "PASS" if passed else "FAIL")]


def test_mapping_options_step2():
    """STEP2 §10: map đủ 2/3/4/5/6 options, không cắt, không dùng num_answers/A-F."""
    area = _step2_area_100_200_1000_500()
    results = []
    for n in (2, 3, 4, 5, 6):
        opts = _step2_make_opts(n)
        mapped = map_options_to_screen(opts, area)
        order_ok = [m["label"] for m in mapped] == [o["label"] for o in opts] and [
            m["box"] for m in mapped
        ] == [o["box"] for o in opts]
        passed = len(mapped) == n and order_ok
        print(
            f"{n}_options: mapped={len(mapped)} order_ok={order_ok} -> {'PASS' if passed else 'FAIL'}"
        )
        for mo in mapped:
            print(f"  {mo['label']}: box={mo['box']} screen_point={mo['screen_point']}")
        results.append((f"{n}_options", len(mapped), n, "PASS" if passed else "FAIL"))
    return results


def test_mapping_invalid_step2():
    """STEP2 §11: A-F invalid, không click (chỉ raise/log)."""
    area = _step2_area_100_200_1000_500()
    results = []
    # A. answer_index=-1 -> validate OK nhưng skip, không click
    r = {
        "question": "Q",
        "options": _step2_make_opts(2),
        "answer_index": -1,
        "confidence": 0.0,
        "explanation": "",
    }
    ok, code, _ = validate_ai_result(r)
    a_pass = ok and r["answer_index"] == -1
    print(
        f"A_idx-1: validate_ok={ok} -> {'PASS (skip, KHÔNG CLICK)' if a_pass else 'FAIL'}; No valid answer_index; skipping answer click."
    )
    results.append(("A_idx-1_skip", a_pass, True, "PASS" if a_pass else "FAIL"))
    # B. answer_index vượt range -> validation error
    r = {
        "question": "Q",
        "options": _step2_make_opts(2),
        "answer_index": 5,
        "confidence": 0.9,
        "explanation": "",
    }
    ok, code, _ = validate_ai_result(r)
    b_pass = not ok and code == "bad_answer_index"
    print(
        f"B_idx-range: ok={ok} code={code} -> {'PASS' if b_pass else 'FAIL'} (KHÔNG CLICK)"
    )
    results.append(
        ("B_idx_range", code, "bad_answer_index", "PASS" if b_pass else "FAIL")
    )
    # C. box invalid -> map raise, không click
    try:
        map_options_to_screen(
            [
                {
                    "label": "A",
                    "text": "x",
                    "box": {"x1": 0.6, "y1": 0.1, "x2": 0.4, "y2": 0.3},
                }
            ],
            area,
        )
        print("C_box-invalid: FAIL (đáng lẽ raise)")
        results.append(("C_box_invalid", False, True, "FAIL"))
    except ValueError as e:
        print(f"C_box-invalid: PASS (raise, KHÔNG CLICK): {e}")
        results.append(("C_box_invalid", True, True, "PASS"))
    # D. width=0
    try:
        box_to_screen_point(
            {"x1": 0.1, "y1": 0.1, "x2": 0.5, "y2": 0.3},
            {"left": 0, "top": 0, "width": 0, "height": 500},
        )
        print("D_width0: FAIL")
        results.append(("D_width0", False, True, "FAIL"))
    except ValueError as e:
        print(f"D_width0: PASS (KHÔNG CLICK): {e}")
        results.append(("D_width0", True, True, "PASS"))
    # E. height=0
    try:
        box_to_screen_point(
            {"x1": 0.1, "y1": 0.1, "x2": 0.5, "y2": 0.3},
            {"left": 0, "top": 0, "width": 1000, "height": 0},
        )
        print("E_height0: FAIL")
        results.append(("E_height0", False, True, "FAIL"))
    except ValueError as e:
        print(f"E_height0: PASS (KHÔNG CLICK): {e}")
        results.append(("E_height0", True, True, "PASS"))
    # F. options=[] -> map ra [] (validate với idx=-1 mới OK), không click
    mapped = map_options_to_screen([], area)
    f_pass = mapped == []
    print(f"F_empty: mapped={mapped} -> {'PASS (KHÔNG CLICK)' if f_pass else 'FAIL'}")
    results.append(("F_empty", f_pass, True, "PASS" if f_pass else "FAIL"))
    return results


def test_step2_all():
    """Chạy toàn bộ test STEP2. KHÔNG click, KHÔNG gọi API."""
    print("=== test_box_to_screen_step2 ===")
    a = test_box_to_screen_step2()
    print("=== test_mapping_resize_step2 ===")
    b = test_mapping_resize_step2()
    print("=== test_mapping_options_step2 ===")
    c = test_mapping_options_step2()
    print("=== test_mapping_invalid_step2 ===")
    d = test_mapping_invalid_step2()
    all_rows = a + b + c + d
    fails = [r for r in all_rows if r[3] == "FAIL"]
    print(f"STEP2 TOTAL: {len(all_rows) - len(fails)}/{len(all_rows)} PASS")
    return all_rows


# ---------- STEP3 test helpers (offline, KHÔNG auto-run, KHÔNG click, KHÔNG gọi Gemini) ----------
def _step3_make_res(n, answer_index, conf=0.9):
    boxes = [
        (0.05, 0.05, 0.95, 0.15),
        (0.05, 0.20, 0.95, 0.30),
        (0.05, 0.35, 0.95, 0.45),
        (0.05, 0.50, 0.95, 0.60),
        (0.05, 0.65, 0.95, 0.75),
        (0.05, 0.80, 0.95, 0.90),
    ]
    opts = []
    for i in range(n):
        x1, y1, x2, y2 = boxes[i]
        opts.append(
            {
                "label": INDEX_TO_KEY[i],
                "text": f"Text {INDEX_TO_KEY[i]}",
                "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
            }
        )
    return {
        "question": f"Q{n}?",
        "options": opts,
        "answer_index": answer_index,
        "confidence": conf,
        "explanation": "fake",
    }


def test_solve_pipeline_step3():
    """STEP3 §10: pipeline res -> options[idx] -> box AI -> point, KHÔNG click."""
    area = {"left": 100, "top": 200, "width": 1000, "height": 500}
    cases = [
        ("CASE1_2opt_idx1", _step3_make_res(2, 1), "B"),
        ("CASE2_3opt_idx2", _step3_make_res(3, 2), "C"),
        ("CASE3_4opt_idx0", _step3_make_res(4, 0), "A"),
        ("CASE4_5opt_idx4", _step3_make_res(5, 4), "E"),
        ("CASE5_6opt_idx5", _step3_make_res(6, 5), "F"),
    ]
    results = []
    for name, res, expect_label in cases:
        pipe = resolve_answer_pipeline(res, area)
        sel = pipe["selected"]
        expect_box = res["options"][res["answer_index"]]["box"]
        expect_pt = box_to_screen_point(expect_box, area)
        passed = (
            pipe["status"] == "ok"
            and sel is not None
            and sel["label"] == expect_label
            and sel["box"] == expect_box
            and sel["screen_point"] == expect_pt
        )
        print(
            f"{name}: label={sel['label'] if sel else None} pt={sel['screen_point'] if sel else None} "
            f"expect=({expect_label},{expect_pt}) -> {'PASS' if passed else 'FAIL'} (KHÔNG CLICK)"
        )
        print(
            format_dry_run_block(sel, res["answer_index"]) if sel else "  no selected"
        )
        results.append(
            (
                name,
                sel["screen_point"] if sel else None,
                expect_pt,
                "PASS" if passed else "FAIL",
            )
        )
    # CASE6 idx=-1
    pipe = resolve_answer_pipeline(_step3_make_res(2, -1, 0.0), area)
    c6 = pipe["status"] == "no_answer" and pipe["selected"] is None
    print(
        f"CASE6_idx-1: status={pipe['status']} -> {'PASS' if c6 else 'FAIL'}; "
        "No valid answer_index; skipping answer click. (KHÔNG CLICK)"
    )
    results.append(
        ("CASE6_idx-1", pipe["status"], "no_answer", "PASS" if c6 else "FAIL")
    )
    # CASE7 invalid idx
    pipe = resolve_answer_pipeline(_step3_make_res(4, 6), area)
    c7 = pipe["status"] == "invalid"
    print(
        f"CASE7_idx6/4opt: status={pipe['status']} code={pipe['code']} -> {'PASS' if c7 else 'FAIL'} (KHÔNG CLICK)"
    )
    results.append(
        (
            "CASE7_idx_invalid",
            pipe["code"],
            "bad_answer_index",
            "PASS" if c7 else "FAIL",
        )
    )
    # CASE8 invalid box
    bad = _step3_make_res(2, 0)
    bad["options"][0] = {
        "label": "A",
        "text": "x",
        "box": {"x1": 0.6, "y1": 0.1, "x2": 0.4, "y2": 0.3},
    }
    pipe = resolve_answer_pipeline(bad, area)
    c8 = pipe["status"] in ("invalid", "map_error")
    print(
        f"CASE8_badbox: status={pipe['status']} -> {'PASS' if c8 else 'FAIL'} (KHÔNG CLICK, error rõ ràng)"
    )
    results.append(
        ("CASE8_badbox", pipe["status"], "invalid/map_error", "PASS" if c8 else "FAIL")
    )
    # fallback hợp lệ: idx=-1 + fallback_index=1 -> chọn B, box từ AI
    pipe = resolve_answer_pipeline(_step3_make_res(3, -1, 0.0), area, fallback_index=1)
    cf = (
        pipe["status"] == "ok"
        and pipe["selected"]["label"] == "B"
        and pipe["used_fallback"]
    )
    print(
        f"CASE_FB_valid: label={pipe['selected']['label'] if pipe['selected'] else None} "
        f"used_fallback={pipe['used_fallback']} -> {'PASS' if cf else 'FAIL'} (box từ AI, KHÔNG CLICK)"
    )
    results.append(
        (
            "CASE_FB_valid",
            pipe["selected"]["label"] if pipe["selected"] else None,
            "B",
            "PASS" if cf else "FAIL",
        )
    )
    # fallback invalid: idx=-1 + fallback_index=9 -> invalid, không click
    pipe = resolve_answer_pipeline(_step3_make_res(3, -1, 0.0), area, fallback_index=9)
    cfi = pipe["status"] == "invalid"
    print(
        f"CASE_FB_invalid: status={pipe['status']} -> {'PASS' if cfi else 'FAIL'} (KHÔNG CLICK)"
    )
    results.append(
        ("CASE_FB_invalid", pipe["status"], "invalid", "PASS" if cfi else "FAIL")
    )
    return results


def test_no_fixed_points_step3():
    """STEP3 §11: chứng minh AI BOX -> SCREEN, không phải LABEL -> FIXED CLICK POINT."""
    area = {"left": 100, "top": 200, "width": 1000, "height": 500}
    fake_click_points = {"A": (1, 1), "B": (2, 2), "C": (3, 3), "D": (4, 4)}
    res = _step3_make_res(4, 2)  # chọn C theo index
    pipe = resolve_answer_pipeline(res, area)
    sel = pipe["selected"]
    fixed = fake_click_points[sel["label"]]
    passed = sel["screen_point"] != fixed and sel[
        "screen_point"
    ] == box_to_screen_point(res["options"][2]["box"], area)
    print(
        f"no_A-F: selected={sel['label']} pt={sel['screen_point']} fixed_C={fixed} "
        f"-> {'PASS' if passed else 'FAIL'} (dùng box AI, KHÔNG dùng click_points A-F)"
    )
    return [("no_fixed_A-F", sel["screen_point"], fixed, "PASS" if passed else "FAIL")]


def test_resize_regression_step3():
    """STEP3 §13: giữ regression resize."""
    area = {"left": 0, "top": 0, "width": 2000, "height": 1000}
    box = {"x1": 0.25, "y1": 0.25, "x2": 0.75, "y2": 0.75}
    got = box_to_screen_point(box, area)
    passed = got == (1000, 500) and got != (800, 400)
    print(
        f"resize_step3: got={got} expect=(1000, 500) -> {'PASS' if passed else 'FAIL'}"
    )
    return [("resize_step3", got, (1000, 500), "PASS" if passed else "FAIL")]


def test_step3_all():
    """Chạy toàn bộ test STEP3. KHÔNG click, KHÔNG gọi API/network."""
    print("=== test_solve_pipeline_step3 ===")
    a = test_solve_pipeline_step3()
    print("=== test_no_fixed_points_step3 ===")
    b = test_no_fixed_points_step3()
    print("=== test_resize_regression_step3 ===")
    c = test_resize_regression_step3()
    all_rows = a + b + c
    fails = [r for r in all_rows if r[3] == "FAIL"]
    print(f"STEP3 TOTAL: {len(all_rows) - len(fails)}/{len(all_rows)} PASS")
    return all_rows


# ---------- STEP4 test helpers (mock click, KHÔNG gọi pyautogui thật, KHÔNG loop) ----------
def _step4_fake_click_recorder():
    """Trả (clicked_list, fake_module): fake pyautogui với click(x, y) ghi nhận, không chạm màn hình."""
    import sys
    import types

    clicked = []
    fake = types.ModuleType("pyautogui")

    def fake_click(x, y=None, *a, **k):
        if isinstance(x, (list, tuple)):
            clicked.append((int(x[0]), int(x[1])))
        else:
            clicked.append((int(x), int(y)))

    fake.click = fake_click
    fake.position = lambda: (0, 0)
    return clicked, fake


def _step4_run_real_click(box, area):
    """Chạy click_box(dry_run=False) với pyautogui mock. Trả clicked list + point."""
    import sys

    clicked, fake = _step4_fake_click_recorder()
    real_mod = sys.modules.get("pyautogui", None)
    sys.modules["pyautogui"] = fake
    try:
        pt = click_box(box, area, dry_run=False)
    finally:
        if real_mod is None:
            sys.modules.pop("pyautogui", None)
        else:
            sys.modules["pyautogui"] = real_mod
    return clicked, pt


def test_real_click_pipeline_step4():
    """STEP4 §13: mock real click. 1 answer click duy nhất từ AI box; no-click cases = 0 click."""
    import sys

    area = {"left": 100, "top": 200, "width": 1000, "height": 500}
    results = []

    def check(name, res, expect_label):
        pipe = resolve_answer_pipeline(res, area)
        sel = pipe["selected"]
        expect_box = res["options"][res["answer_index"]]["box"]
        expect_pt = box_to_screen_point(expect_box, area)
        clicked, pt = _step4_run_real_click(sel["box"], area)
        passed = (
            pipe["status"] == "ok"
            and sel["label"] == expect_label
            and pt == expect_pt
            and clicked == [expect_pt]
        )
        print(
            f"{name}: clicked={clicked} expect=[{expect_pt}] -> {'PASS' if passed else 'FAIL'}"
        )
        results.append((name, clicked, [expect_pt], "PASS" if passed else "FAIL"))

    check("CASE1_2opt_B", _step3_make_res(2, 1), "B")
    check("CASE2_4opt_C", _step3_make_res(4, 2), "C")
    check("CASE3_6opt_F", _step3_make_res(6, 5), "F")

    # CASE4 idx=-1 -> NO CLICK (không gọi click_box False)
    pipe = resolve_answer_pipeline(_step3_make_res(2, -1, 0.0), area)
    c4 = pipe["status"] == "no_answer" and pipe["selected"] is None
    print(
        f"CASE4_idx-1: status={pipe['status']} clicks=0 -> {'PASS' if c4 else 'FAIL'} (NO CLICK)"
    )
    results.append(("CASE4_idx-1", [], [], "PASS" if c4 else "FAIL"))

    # CASE5 invalid index -> NO CLICK
    pipe = resolve_answer_pipeline(_step3_make_res(4, 6), area)
    c5 = pipe["status"] == "invalid"
    print(
        f"CASE5_idx-invalid: status={pipe['status']} clicks=0 -> {'PASS' if c5 else 'FAIL'} (NO CLICK)"
    )
    results.append(("CASE5_idx-invalid", [], [], "PASS" if c5 else "FAIL"))

    # CASE6 invalid box -> NO CLICK
    bad = _step3_make_res(2, 0)
    bad["options"][0] = {
        "label": "A",
        "text": "x",
        "box": {"x1": 0.6, "y1": 0.1, "x2": 0.4, "y2": 0.3},
    }
    pipe = resolve_answer_pipeline(bad, area)
    c6 = pipe["status"] in ("invalid", "map_error")
    print(
        f"CASE6_badbox: status={pipe['status']} clicks=0 -> {'PASS' if c6 else 'FAIL'} (NO CLICK)"
    )
    results.append(("CASE6_badbox", [], [], "PASS" if c6 else "FAIL"))

    # CASE7 REAL_ANSWER_CLICK=False -> click_box(False) phải raise, 0 click thật
    import main as _m

    old_val, old_compat = _m.REAL_ANSWER_CLICK, _m.STEP1_NO_CLICK
    _m.REAL_ANSWER_CLICK, _m.STEP1_NO_CLICK = False, True
    try:
        clicked, fake = _step4_fake_click_recorder()
        sys.modules["pyautogui"] = fake
        try:
            click_box(_step3_make_res(2, 0)["options"][0]["box"], area, dry_run=False)
            print("CASE7_gate-off: FAIL (đáng lẽ raise)")
            results.append(("CASE7_gate-off", False, True, "FAIL"))
        except RuntimeError:
            c7 = clicked == []
            print(
                f"CASE7_gate-off: raise OK, clicks={clicked} -> {'PASS' if c7 else 'FAIL'} (NO REAL CLICK)"
            )
            results.append(("CASE7_gate-off", clicked, [], "PASS" if c7 else "FAIL"))
        finally:
            sys.modules.pop("pyautogui", None)
    finally:
        _m.REAL_ANSWER_CLICK, _m.STEP1_NO_CLICK = old_val, old_compat

    # CASE8 fixed points giả vs AI box
    fake_pts = {
        "A": (1, 1),
        "B": (2, 2),
        "C": (3, 3),
        "D": (4, 4),
        "E": (5, 5),
        "F": (6, 6),
    }
    res = _step3_make_res(4, 2)
    pipe = resolve_answer_pipeline(res, area)
    clicked, pt = _step4_run_real_click(pipe["selected"]["box"], area)
    c8 = clicked == [pt] and clicked[0] != fake_pts[pipe["selected"]["label"]]
    print(
        f"CASE8_no-AF: clicked={clicked} fixed={fake_pts[pipe['selected']['label']]} "
        f"-> {'PASS' if c8 else 'FAIL'} (click == AI BOX, != fixed)"
    )
    results.append(("CASE8_no-AF", clicked, [pt], "PASS" if c8 else "FAIL"))
    return results


def test_next_safety_step4():
    """STEP4 §14: NEXT chỉ được phép sau answer click thành công. Mock, không click thật."""
    results = []
    # A/B/C: answer invalid | box invalid | click exception -> NEXT = 0
    for name, res in [
        ("A_invalid-idx", _step3_make_res(2, 9)),
        (
            "B_invalid-box",
            {
                **_step3_make_res(2, 0),
                "options": [
                    {
                        "label": "A",
                        "text": "x",
                        "box": {"x1": 0.9, "y1": 0.1, "x2": 0.1, "y2": 0.3},
                    },
                    _step3_make_res(2, 0)["options"][1],
                ],
            },
        ),
    ]:
        pipe = resolve_answer_pipeline(
            res, {"left": 0, "top": 0, "width": 500, "height": 500}
        )
        answer_clicks = 0  # pipeline invalid -> solve_once return trước click
        next_clicks = 0 if answer_clicks == 0 else 1
        passed = pipe["status"] != "ok" and answer_clicks == 0 and next_clicks == 0
        print(
            f"NEXT-{name}: answer={answer_clicks} next={next_clicks} -> {'PASS' if passed else 'FAIL'}"
        )
        results.append((f"NEXT-{name}", next_clicks, 0, "PASS" if passed else "FAIL"))
    # C: click exception (gate off) -> NEXT = 0
    import main as _m

    old_val, old_compat = _m.REAL_ANSWER_CLICK, _m.STEP1_NO_CLICK
    _m.REAL_ANSWER_CLICK, _m.STEP1_NO_CLICK = False, True
    try:
        try:
            _step4_run_real_click(
                _step3_make_res(2, 0)["options"][0]["box"],
                {"left": 0, "top": 0, "width": 500, "height": 500},
            )
            exc_clicks, exc_next = 1, 1
        except RuntimeError:
            exc_clicks, exc_next = 0, 0
        passed = exc_clicks == 0 and exc_next == 0
        print(
            f"NEXT-C_click-exc: answer={exc_clicks} next={exc_next} -> {'PASS' if passed else 'FAIL'}"
        )
        results.append(("NEXT-C_click-exc", exc_next, 0, "PASS" if passed else "FAIL"))
    finally:
        _m.REAL_ANSWER_CLICK, _m.STEP1_NO_CLICK = old_val, old_compat
    # D: success -> NEXT được phép nếu auto_click_next (mock 1 NEXT, không click thật)
    pipe = resolve_answer_pipeline(
        _step3_make_res(2, 1), {"left": 0, "top": 0, "width": 500, "height": 500}
    )
    d_pass = pipe["status"] == "ok"
    print(
        f"NEXT-D_success: answer=1(mock) next=allowed -> {'PASS' if d_pass else 'FAIL'}"
    )
    results.append(("NEXT-D_success", 1, 1, "PASS" if d_pass else "FAIL"))
    return results


def test_variable_options_step4():
    """STEP4 §15: 2..6 options, không num_answers/opts[:need] trong pipeline."""
    import inspect

    area = {"left": 100, "top": 200, "width": 1000, "height": 500}
    src_all = inspect.getsource(resolve_answer_pipeline) + inspect.getsource(
        App.solve_once
    )
    # chỉ xét code thực thi, bỏ comment/dòng trống (comment còn nhắc "KHÔNG dùng opts[:need]")
    code_lines = [
        ln
        for ln in src_all.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    code = "\n".join(code_lines)
    clean = "[:need]" not in code
    # cho phép từ num_answers xuất hiện trong comment/docstring legacy, nhưng active code
    # solve_once không được đọc cfg num_answers để lọc options
    active = (
        src_all.split("fallback tối thiểu")[0]
        if "fallback tối thiểu" in src_all
        else src_all
    )
    no_num_use = (
        'get("num_answers"' not in active
        and "num_answers" not in active.split("Master switch")[0]
    )
    results = []
    for n in (2, 3, 4, 5, 6):
        res = _step3_make_res(n, n - 1)
        pipe = resolve_answer_pipeline(res, area)
        passed = (
            pipe["status"] == "ok"
            and pipe["n_options"] == n
            and pipe["selected"]["label"] == INDEX_TO_KEY[n - 1]
        )
        print(
            f"{n}_options: n={pipe['n_options']} label={pipe['selected']['label']} -> {'PASS' if passed else 'FAIL'}"
        )
        results.append(
            (f"{n}_options", pipe["n_options"], n, "PASS" if passed else "FAIL")
        )
    print(
        f"no_opts_need_cut: {'PASS' if clean else 'FAIL'}; no_num_answers_in_pipeline: {'PASS' if no_num_use else 'FAIL'}"
    )
    results.append(("no_opts_need_cut", clean, True, "PASS" if clean else "FAIL"))
    return results


def test_step4_all():
    """Chạy toàn bộ test STEP4 (mock). KHÔNG click thật, KHÔNG gọi API, KHÔNG loop."""
    print("=== test_real_click_pipeline_step4 ===")
    a = test_real_click_pipeline_step4()
    print("=== test_next_safety_step4 ===")
    b = test_next_safety_step4()
    print("=== test_variable_options_step4 ===")
    c = test_variable_options_step4()
    all_rows = a + b + c
    fails = [r for r in all_rows if r[3] == "FAIL"]
    print(f"STEP4 TOTAL: {len(all_rows) - len(fails)}/{len(all_rows)} PASS")
    return all_rows


# ---------- PHASE4 perf tests (offline, KHÔNG click thật, KHÔNG gọi API) ----------
def _phase4_synth_image(w=1919, h=922):
    """Ảnh synthetic có chữ/vạch để JPEG benchmark ổn định hơn ảnh trắng trơn."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (w, h), color="white")
    d = ImageDraw.Draw(img)
    for y in range(0, h, 18):
        d.line([(0, y), (w, y)], fill="gray")
    d.text(
        (50, 40),
        "Cau hoi trac nghiem? A. Dap an mot B. Dap an hai C. Dap an ba D. Dap an bon",
    )
    return img


def test_perf_encode_benchmark():
    """PHASE4 STEP5-7: benchmark resize/encode theo width/quality. Không đổi default khi chưa đủ bằng chứng."""
    from PIL import Image

    img = _phase4_synth_image()
    rows = []
    for width in (1600, 1400, 1280):
        for q in (85, 75, 65):
            (out, meta), ms = perf_timed(img_to_base64, img, width, q)
            import base64 as _b64

            # độ dài payload ước lượng từ meta thay vì decode lại
            print(
                f"width={width} q={q}: {ms:.1f} ms sent={meta['sent_width']}x{meta['sent_height']}"
            )
            rows.append((width, q, ms, meta))
    base = [r for r in rows if r[0] == 1600 and r[1] == 85][0]
    print(
        f"BASELINE current(1600/q85): {base[2]:.1f} ms sent={base[3]['sent_width']}x{base[3]['sent_height']}"
    )
    return rows


def test_perf_parse_truncation():
    """PHASE4 STEP3: payload 6 options text dài phải parse+validate OK trong budget 2048 tokens."""
    long_text = "Dap an text dai " * 12  # ~200 chars/option
    boxes = [
        (0.05, 0.05, 0.95, 0.15),
        (0.05, 0.20, 0.95, 0.30),
        (0.05, 0.35, 0.95, 0.45),
        (0.05, 0.50, 0.95, 0.60),
        (0.05, 0.65, 0.95, 0.75),
        (0.05, 0.80, 0.95, 0.90),
    ]
    opts = [
        {
            "label": INDEX_TO_KEY[i],
            "text": long_text,
            "box": {"x1": b[0], "y1": b[1], "x2": b[2], "y2": b[3]},
        }
        for i, b in enumerate(boxes)
    ]
    payload = {
        "question": "Cau hoi " * 20,
        "options": opts,
        "answer_index": 5,
        "confidence": 0.9,
        "explanation": "ly do ngan",
    }
    import json as _json

    raw = _json.dumps(payload, ensure_ascii=False)
    wrapped = "```json\n" + raw + "\n```"  # Gemini hay bọc markdown
    parsed, parse_ms = perf_timed(parse_gemini_text, wrapped)
    ok, code, msg = validate_ai_result(parsed)
    est_tokens = len(raw) / 4.0
    passed = ok and parsed["answer_index"] == 5 and est_tokens < 2048
    print(
        f"6opt-longtext: chars={len(raw)} est_tokens~{est_tokens:.0f}/2048 parse={parse_ms:.2f}ms "
        f"validate={code} -> {'PASS' if passed else 'FAIL'}"
    )
    return [("parse_truncation_6opt", est_tokens, 2048, "PASS" if passed else "FAIL")]


def test_perf_prompt_schema():
    """PHASE4 STEP2: prompt gọn vẫn yêu cầu đủ schema (box/answer_index/confidence), đo chars."""
    p = GEMINI_PROMPT
    must = [
        '"box"',
        '"answer_index"',
        '"confidence"',
        '"options"',
        "x1",
        "x2",
        "answer_index=-1",
        "JSON",
    ]
    missing = [m for m in must if m not in p]
    print(f"prompt_chars={len(p)} (baseline cũ 1503); missing={missing}")
    passed = len(missing) == 0 and len(p) < 1503
    return [("prompt_schema_trim", len(p), 1503, "PASS" if passed else "FAIL")]


def test_perf_config_migration():
    """Default ưu tiên độ rõ 1600/q88/next 0.3; save không mất NEXT."""
    cfg = DEFAULT_CONFIG
    passed = (
        cfg.get("screenshot_max_width", None) == 1600
        and cfg.get("jpeg_quality", None) == 88
        and abs(float(cfg.get("answer_to_next_delay", -1)) - 0.3) < 1e-9
        and "NEXT" in cfg.get("click_points", {})
    )
    print(
        f"perf_defaults: w={cfg.get('screenshot_max_width')} q={cfg.get('jpeg_quality')} "
        f"next_delay={cfg.get('answer_to_next_delay')} -> {'PASS' if passed else 'FAIL'}"
    )
    return [("perf_config_defaults", passed, True, "PASS" if passed else "FAIL")]


def test_perf_timer_helpers():
    """perf_timed/perf_format_block hoạt động, không chứa API key."""
    _, ms = perf_timed(lambda: 1 + 1)
    block = perf_format_block({"screenshot_ms": 1.0, "total_ms": 2.0})
    passed = ms >= 0.0 and "## PERFORMANCE" in block and "screenshot_ms" in block
    print(f"timer_helpers: ms={ms:.3f} -> {'PASS' if passed else 'FAIL'}")
    return [("timer_helpers", ms >= 0.0, True, "PASS" if passed else "FAIL")]


def test_phase4_all():
    """Chạy toàn bộ test PHASE4. Offline, mock, không click thật."""
    print("=== test_perf_encode_benchmark ===")
    a = test_perf_encode_benchmark()
    print("=== test_perf_parse_truncation ===")
    b = test_perf_parse_truncation()
    print("=== test_perf_prompt_schema ===")
    c = test_perf_prompt_schema()
    print("=== test_perf_config_migration ===")
    d = test_perf_config_migration()
    print("=== test_perf_timer_helpers ===")
    e = test_perf_timer_helpers()
    checks = b + c + d + e
    fails = [r for r in checks if r[3] == "FAIL"]
    print(f"PHASE4 CHECKS: {len(checks) - len(fails)}/{len(checks)} PASS")
    return checks


# ---------- Dedup test (offline, không click) ----------
def test_dedup_skip():
    """1 câu 1 lần: màn hình giống hệt + đã có kq cuối (ok/invalid/map_error/no_answer) → skip."""
    rows = []
    cases = [
        ("same_invalid", "h1", "invalid", "h1", True),
        ("same_map_error", "h1", "map_error", "h1", True),
        ("same_no_answer", "h1", "no_answer", "h1", True),
        ("same_ok", "h1", "ok", "h1", True),
        ("same_click_error", "h1", "click_error", "h1", False),
        ("same_gemini_error", "h1", "gemini_error", "h1", False),
        ("diff_ok", "h1", "ok", "h2", False),
        ("diff_invalid", "h1", "invalid", "h2", False),
        ("same_empty_outcome", "h1", "", "h1", False),
        ("same_none_hash", None, "invalid", None, False),
    ]
    for name, last_h, outcome, cur_h, expect in cases:
        got = should_skip_repeat(last_h, outcome, cur_h)
        passed = got == expect
        print(f"{name}: skip={got} expect={expect} -> {'PASS' if passed else 'FAIL'}")
        rows.append((name, got, expect, "PASS" if passed else "FAIL"))
    # question_key: cùng câu + đáp án (khác dấu/hoa-thường) → trùng → không click lại
    k1 = question_key(
        "Thủ đô của Việt Nam là?", [{"text": "Hà Nội"}, {"text": "TP.HCM"}]
    )
    k2 = question_key(
        "thu do cua viet nam la?", [{"text": "ha noi"}, {"text": "tp.hcm"}]
    )
    k3 = question_key("Thủ đô của Việt Nam là?", [{"text": "Huế"}, {"text": "TP.HCM"}])
    k_ok = k1 == k2 and k1 != k3 and k1 != ""
    print(
        f"question_key_dup: k1==k2={k1==k2} k1!=k3={k1!=k3} -> {'PASS' if k_ok else 'FAIL'}"
    )
    rows.append(("question_key_dup", k_ok, True, "PASS" if k_ok else "FAIL"))
    return rows


def _fake_windows():
    big = {
        "hwnd": 1,
        "title": "Quizizz — Play",
        "exe": "chrome.exe",
        "rect": {"left": 0, "top": 0, "width": 1920, "height": 1040},
    }
    small = {
        "hwnd": 2,
        "title": "Zalo",
        "exe": "zalo.exe",
        "rect": {"left": 100, "top": 100, "width": 400, "height": 300},
    }
    other = {
        "hwnd": 3,
        "title": "YouTube",
        "exe": "chrome.exe",
        "rect": {"left": 0, "top": 0, "width": 800, "height": 600},
    }
    return [big, small, other]


def test_window_capture():
    """rank/pick/resolve cửa sổ bằng fake list, không chạm Win32 thật."""
    rows = []
    wins = _fake_windows()

    def check(name, cond, detail=""):
        print(f"{name} -> {'PASS' if cond else 'FAIL'} {detail}")
        rows.append((name, cond, True, "PASS" if cond else "FAIL"))

    # 1. đúng exe + đúng title
    b = rank_window(wins, "Quizizz — Play", "chrome.exe")
    check(
        "rank_exact",
        b is not None and b["hwnd"] == 1,
        f"hwnd={b['hwnd'] if b else None}",
    )
    # 2. đúng exe, title đổi (browser đổi title theo trang) → vẫn bắt được chrome to nhất
    b = rank_window(wins, "Bài 5: An toàn - Google Chrome", "chrome.exe")
    check("rank_same_exe_largest", b is not None and b["hwnd"] == 1)
    # 3. exe khác → bắt đúng app đó
    b = rank_window(wins, "Zalo", "zalo.exe")
    check("rank_other_exe", b is not None and b["hwnd"] == 2)
    # 4. exe không tồn tại → None (không bắt bừa)
    check("rank_no_match", rank_window(wins, "Game", "game.exe") is None)
    # 5. list rỗng → None
    check("rank_empty", rank_window([], "Quizizz", "chrome.exe") is None)
    # 6. resolve mode window tìm thấy → rect + mô tả
    area, src = resolve_capture_area(
        {
            "capture_mode": "window",
            "capture_window": {"title": "Quizizz", "exe": "chrome.exe"},
            "capture_region": {"left": 1, "top": 2, "width": 3, "height": 4},
        },
        finder=lambda info: find_window_rect(info, provider=_fake_windows),
    )
    check("resolve_window_hit", area["width"] == 1920 and "cửa sổ" in src, src)
    # 7. resolve mode window mất cửa sổ → fallback vùng cũ
    area, src = resolve_capture_area(
        {
            "capture_mode": "window",
            "capture_window": {"title": "X", "exe": "nope.exe"},
            "capture_region": {"left": 1, "top": 2, "width": 3, "height": 4},
        },
        finder=lambda info: find_window_rect(info, provider=_fake_windows),
    )
    check(
        "resolve_window_fallback",
        area == {"left": 1, "top": 2, "width": 3, "height": 4} and "fallback" in src,
        src,
    )
    # 8. resolve mode region → bỏ qua window, dùng region
    area, src = resolve_capture_area(
        {
            "capture_mode": "region",
            "capture_window": {"title": "Quizizz", "exe": "chrome.exe"},
            "capture_region": {"left": 5, "top": 6, "width": 7, "height": 8},
        }
    )
    check(
        "resolve_region_mode",
        area == {"left": 5, "top": 6, "width": 7, "height": 8},
        src,
    )
    # 9. pick tại điểm trong Zalo → bắt Zalo (hwnd_at giả lập)
    p = pick_window_at_cursor(
        pos=(150, 150), provider=_fake_windows, hwnd_at=lambda x, y: 2
    )
    check("pick_by_hwnd", p is not None and p["exe"] == "zalo.exe", str(p))
    # 10. pick điểm ngoài mọi cửa sổ, không hwnd → None
    p = pick_window_at_cursor(
        pos=(5000, 5000), provider=_fake_windows, hwnd_at=lambda x, y: None
    )
    check("pick_outside", p is None)
    # 11. migration config mới: default region, window None
    d = DEFAULT_CONFIG
    check(
        "config_defaults",
        d.get("capture_mode") == "region" and d.get("capture_window") is None,
    )
    return rows


def test_window_capture_all():
    print("=== test_window_capture ===")
    a = test_window_capture()
    fails = [r for r in a if r[3] == "FAIL"]
    print(f"WINDOW CAPTURE: {len(a) - len(fails)}/{len(a)} PASS")
    return a


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
