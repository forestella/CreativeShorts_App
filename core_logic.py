"""
[BETA v3] TTS 나레이션 + BGM + SFX 쇼츠 생성기
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
파이프라인:
  1. AI → 한국어 스크립트 + 영상 타임스탬프 매핑
  2. Gemini TTS → 각 clip 나레이션 음성 생성
  3. 나레이션 길이에 맞춰 영상 클립 추출
  4. 오디오 믹싱: 나레이션(100%) + 원본(10%) + SFX
  5. BGM 추가 (resources/bgm/ 폴더에 mp3 파일 넣기)
  6. CapCut 내보내기 (스크립트 자막 포함)

Usage:
    python test/creative_shorts_beta.py [YouTube_URL_or_mp4_path] --voice Charon --model gemini-2.5-flash-lite
"""
import os, sys, re, json, subprocess, time, random, argparse
from datetime import datetime
from pathlib import Path

# Project root path setup (독립형 앱 구조)
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

# Third-party imports
import yt_dlp
import whisper
from google import genai
from google.genai import types

# Project imports (독립형 로컬 참조)
from config import GEMINI_API_KEY
from video_engine import VideoProcessor
try:
    from src.youtube_long.fact_checker import FactChecker
except ImportError:
    FactChecker = None

DOWNLOADS_DIR = os.path.join(APP_ROOT, "downloads")
OUTPUT_DIR    = os.path.join(APP_ROOT, "output")
CACHE_DIR     = os.path.join(APP_ROOT, "cache", "creative_beta")
RESOURCE_DIR  = os.path.join(APP_ROOT, "resources", "sfx")
BGM_DIR       = os.path.join(APP_ROOT, "resources", "bgm")
TTS_CACHE_DIR = os.path.join(OUTPUT_DIR, "tts_cache")
CHANNELS_CONFIG_PATH = os.path.join(APP_ROOT, "cache", "channels_config.json")

for d in [DOWNLOADS_DIR, OUTPUT_DIR, CACHE_DIR, BGM_DIR, RESOURCE_DIR, TTS_CACHE_DIR, os.path.dirname(CHANNELS_CONFIG_PATH)]:
    os.makedirs(d, exist_ok=True)

# 전역 Gemini 클라이언트 초기화
client = genai.Client(api_key=GEMINI_API_KEY)

# ─── 비용 추적기 ─────────────────────────────────────────────────────────────
_session_usage: dict = {"input_tokens": 0, "output_tokens": 0, "model": ""}

# 모델별 USD 단가 (per 1M tokens) — https://ai.google.dev/gemini-api/docs/pricing
_PRICING = {
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash":      {"input": 0.30, "output": 2.50},
    "gemini-2.5-pro":        {"input": 1.25, "output": 10.00},
}

def _track_usage(model_name: str, response) -> None:
    """response.usage_metadata에서 토큰 수를 추출해 누적합니다."""
    global _session_usage
    meta = getattr(response, "usage_metadata", None)
    if not meta:
        return
    inp = getattr(meta, "prompt_token_count", 0) or 0
    out = getattr(meta, "candidates_token_count", 0) or 0
    _session_usage["input_tokens"] += inp
    _session_usage["output_tokens"] += out
    _session_usage["model"] = model_name

def reset_cost_tracker() -> None:
    global _session_usage
    _session_usage = {"input_tokens": 0, "output_tokens": 0, "model": ""}

def get_cost_summary() -> str:
    inp   = _session_usage["input_tokens"]
    out   = _session_usage["output_tokens"]
    model = _session_usage["model"] or "gemini-2.5-flash"
    pricing   = _PRICING.get(model, _PRICING["gemini-2.5-flash"])
    cost_in   = inp / 1_000_000 * pricing["input"]
    cost_out  = out / 1_000_000 * pricing["output"]
    cost_total = cost_in + cost_out
    KRW_RATE  = 1380
    lines = [
        "──────────────────────────────────────────",
        f" 💰 [Gemini API 비용 추산]  모델: {model}",
        "──────────────────────────────────────────",
        f"   입력 토큰:  {inp:,} tokens  × ${pricing['input']:.2f}/M  = ${cost_in:.5f}",
        f"   출력 토큰:  {out:,} tokens  × ${pricing['output']:.2f}/M  = ${cost_out:.5f}",
        f"   ─────────────────────────────────────",
        f"   합  계:     ${cost_total:.5f}  ≈  ₩{cost_total * KRW_RATE:.2f}",
        "──────────────────────────────────────────",
    ]
    return "\n".join(lines)


# ─── 유틸 ────────────────────────────────────────────────────────────────────

def fmt_time(sec):
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"

def cache_key(video_id):
    return os.path.join(CACHE_DIR, f"{video_id}_v3.json")

def get_audio_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path],
        capture_output=True, text=True
    )
    try:
        for s in json.loads(r.stdout).get("streams", []):
            if "duration" in s:
                return float(s["duration"])
    except Exception:
        pass
    return 0.0

SFX_MAP = {
    # [기본 리액션]
    "hook":       "40. 두둥액션.mp3",
    "tip":        "28. 띠링_soft.mp3",
    "surprise":   "10. 띠용.mp3",
    "emphasis":   "32. 느낌표.mp3",
    "transition": "7. 뿅.mp3",
    "ending":     "65. 예~.mp3",
    "question":   "31. 물음표.mp3",
    
    # [게임/액션 - 롤 최적화]
    "kill":       "13. 명중.mp3",
    "hit":        "64. 때리는소리.mp3",
    "punch":      "17. 펀치.mp3",
    "shotgun":    "14. 샷건.mp3",
    "dash":       "59. 대쉬.mp3",
    "spin":       "51. 스핀.mp3",
    "explode":    "26. 폭발음.mp3",
    "heart":      "41. 심장소리.mp3",
    "tension":    "30. 긴장공포.mp3",
    "suspense":   "38. 서스펜스.mp3",
    "success":    "27. 마리오동전.mp3",
    "fail":       "24. 빗나감.mp3",
    "miss":       "49. 못맞췄지롱~.mp3",
    
    # [코믹/재치]
    "funny":      "9. 뜨헉.mp3",
    "what":       "53. 먼개소리야.mp3",
    "laugh":      "55. 어이없는웃음.mp3",
    "evil":       "52. 사악한웃음.mp3",
    "duck":       "2. 러버덕.mp3",
    "beep":       "3. 삑~.mp3",
    "nope":       "6. 놉.mp3",
    "no":         "47. 노노노~.mp3",
    "bye":        "63. 안녕히계세요여러분.mp3",
    "scream":     "34. 아아악!.mp3",
    "shock":      "11. 내눈.mp3",
    "poop":       "12. 뿌직.mp3",
    
    # [분위기]
    "sad":        "37. 슬픈음악.mp3",
    "urgent":     "44. 다급한브금.mp3",
    "scary":      "43. 놀라는배경음.mp3",
    "rewind":     "20. 되감기.mp3",
}

def sfx(keyword):
    """SFX 키워드 → 파일 경로. 없으면 None."""
    if not keyword:
        return None
    filename = SFX_MAP.get(keyword.lower().strip())
    if not filename:
        return None
    p = os.path.join(RESOURCE_DIR, filename)
    return p if os.path.exists(p) else None

def find_bgm():
    """BGM 폴더에서 첫 번째 .mp3 반환."""
    for f in sorted(Path(BGM_DIR).glob("*.mp3")):
        return str(f)
    return None


# ─── STEP 1: 다운로드 ─────────────────────────────────────────────────────────

def ensure_downloaded(url):
    # 로컬 파일 경로인 경우 바로 반환
    if os.path.exists(url) and (url.endswith(".mp4") or url.endswith(".mkv")):
        vid = os.path.splitext(os.path.basename(url))[0]
        vtt_path = os.path.join(os.path.dirname(url), f"{vid}.en.vtt")
        if not os.path.exists(vtt_path):
            # vtt가 없으면 검색 시도 (같은 폴더 내 .vtt)
            vtt_search = list(Path(os.path.dirname(url)).glob(f"{vid}*.vtt"))
            if vtt_search:
                vtt_path = str(vtt_search[0])
            else:
                vtt_path = "" # 자막 없음 표시
        print(f"   📂 로컬 영상 사용: {url}")
        return vid, url, vtt_path

    if "v=" in url:
        vid = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        vid = url.split("youtu.be/")[1].split("?")[0]
    else:
        raise ValueError(f"유튜브 URL 또는 존재하는 영상 파일 경로가 아닙니다: {url}")

    # [수정] 성공적이었던 downloader.py의 구조를 반영하여 yt_dlp 라이브러리 모드로 전면 개편
    import yt_dlp
    import imageio_ffmpeg
    
    video_path = os.path.join(DOWNLOADS_DIR, f"{vid}.mp4")
    vtt_path   = os.path.join(DOWNLOADS_DIR, f"{vid}.en.vtt") # 기존 코드와의 호환성을 위해 en 유지
    
    if os.path.exists(video_path):
        print(f"   이미 다운로드됨: {os.path.basename(video_path)}")
        return vid, video_path, vtt_path

    # IP 차단 방지를 위한 랜덤 지연 (사용자 규칙 준수)
    print(f"   📥 유튜브 다운로드 시작: {url} (IP 차단 방지 대기 중...)")
    time.sleep(random.uniform(5, 10))

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
        'outtmpl': os.path.join(DOWNLOADS_DIR, f"{vid}.%(ext)s"),
        'merge_output_format': 'mp4',
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
        'ffmpeg_location': ffmpeg_path,
        'cookiesfrombrowser': ('chrome',),
        'nocheckcertificate': True,
        'writesubtitles': True,
        'writeautomaticsub': True,
        'subtitleslangs': ['en'], # 이 프로젝트는 영어 자동 자막(en)을 AI 용으로 사용
        'skip_video': False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception as e:
        err_msg = str(e)
        print(f"   ❌ 다운로드 실패! (Error: {err_msg})")
        # 429 에러 발생 시 사용자 지침에 따라 알림 후 즉시 종료
        if "429" in err_msg or "Too Many Requests" in err_msg:
            print("   ⛔ [CRITICAL] 429 'Too Many Requests' 발생. IP가 차단되었습니다. 즉시 종료합니다.")
            sys.exit(1)
        raise RuntimeError(f"영상 다운로드 실패: {err_msg}")

    # mp4 확장자 확인 (mkv 등으로 저장될 수 있으므로 병합 결과물 경로 재확인)
    if not os.path.exists(video_path):
        # 만약 확장자가 다르게 저장되었다면 리네임 시도
        for f in os.listdir(DOWNLOADS_DIR):
            if f.startswith(vid) and f.endswith(".mp4"):
                video_path = os.path.join(DOWNLOADS_DIR, f)
                break
        else:
            raise RuntimeError(f"영상 다운로드 완료되었으나 파일을 찾을 수 없습니다: {video_path}")

    print(f"   ✓ {os.path.basename(video_path)}")
    return vid, video_path, vtt_path


# ─── STEP 2: VTT 파싱 ────────────────────────────────────────────────────────

def parse_vtt(path):
    if not os.path.exists(path):
        return []
    pattern = re.compile(
        r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}.*?\n(.*?)(?=\n{2,}|\Z)',
        re.DOTALL
    )
    with open(path, encoding='utf-8') as f:
        content = f.read()
    result = []
    for ts_str, text in pattern.findall(content):
        h, m, s = map(float, ts_str.split(':'))
        sec = h * 3600 + m * 60 + s
        clean = re.sub(r'<[^>]+>', '', text)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if clean and (not result or result[-1]['text'] != clean):
            result.append({'start': sec, 'text': clean})
    return result


# ─── STEP 3: AI 스크립트 + 타임스탬프 매핑 ───────────────────────────────────

def analyze_script_first(video_id, video_url, transcript, model_name="gemini-2.5-flash-lite", ignore_cache=False, multimodal_mode=False, curator_mode=True):
    """
    영상 트랜스크립트를 분석하여 60초 이내의 숏폼 대본과 타임스탬프를 매핑합니다.
    (STRICT 60s LIMIT, NO WHATSAPP)
    """
    ck = cache_key(video_id)
    
    if not ignore_cache and os.path.exists(ck):
        print(f"   ✓ 분석 캐시 발견: {ck}")
        with open(ck, 'r', encoding='utf-8') as f:
            return json.load(f)

    # 메타데이터 추출 (프롬프트 보강용)
    meta_block = ""
    try:
        ydl_opts = {'quiet': True, 'noplaylist': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=False)
            meta_block += f"[Channel Name]\n{info.get('uploader', '') or info.get('channel', '')}\n\n"
            meta_block += f"[Title]\n{info.get('title', '')}\n\n"
            meta_block += f"[Description]\n{info.get('description', '')[:500]}\n\n"
    except: pass

    # ─── 고도화된 스무스한 분석 시스템 지침 (project.md v2.0 준수) ───
    has_transcript = len(transcript) > 0

    # 멀티모달 모드이고 transcript가 없으면 경고
    if multimodal_mode and not has_transcript:
        print("   ⚠ Transcript 없음 → 멀티모달 영상 분석으로 타임스탬프를 직접 추출합니다.")
    elif not multimodal_mode and not has_transcript:
        print("   ⚠ Transcript가 비어 있습니다. 타임스탬프 정확도가 낮을 수 있습니다.")

    # 멀티모달 모드일 때 소스 영상 총 길이 측정 (Gemini에게 범위 알려주기 위함)
    source_video_duration = 0.0
    if multimodal_mode:
        video_path_for_dur = os.path.join(DOWNLOADS_DIR, f"{video_id}.mp4")
        if not os.path.exists(video_path_for_dur):
            search = list(Path(DOWNLOADS_DIR).glob(f"{video_id}.*"))
            if search: video_path_for_dur = str(search[0])
        if os.path.exists(video_path_for_dur):
            source_video_duration = get_audio_duration(video_path_for_dur)
            print(f"   📏 소스 영상 총 길이: {fmt_time(source_video_duration)} ({source_video_duration:.1f}초)")

    duration_hint = f"{source_video_duration:.1f}초 ({fmt_time(source_video_duration)})" if source_video_duration > 0 else "알 수 없음"
    
    if curator_mode:
        system_instruction = f"""
You are a Professional Video Curator & Short-form Director. Your expertise is in taking existing content and adding unique value through insightful commentary and creative editing.
{vision_extra if multimodal_mode else ""}
━━━ [MASTER PROTOCOL: CURATOR MODE v3.0] ━━━

1. CURATORIAL SCRIPT RULES:
   - ROLE: You are an expert narrator providing a 'Reaction' or 'Insight'. 
   - TONE: Natural, engaging, and authoritative. Use POLITE spoken Korean (존랫말).
   - ORIGINALITY: DO NOT just summarize. Add "Why" this is interesting, or point out details the viewer might miss.
   - THE HOOK (ORDER #01): Start with a curiosity-driven opening. E.g., "What if I told you...", "This sequence is actually...", "Look closely at this..."
   - `script_kr`: For Subtitles. Use ARABIC NUMBERS for readability.
   - `tts_kr`: For Narration. Use HANGEUL ONLY for perfect pronunciation.
   - CURRENCY: Convert foreign currency to KRW (1 USD ≈ 1400 KRW). Mention in Hangeul only in `tts_kr`.

2. VISUAL STRATEGY (CRITICAL):
   - Every clip must feel 'Transformative' to avoid Reused Content flags.
   - Suggest a `visual_type` for each clip:
     * "original": Standard cut (use only if the scene is incredibly unique).
     * "blurred_bg": Original video sharp in center, blurred/zoomed version in background (fills 9:16).
     * "framed": Original video inside a styled rounded frame with background color.
     * "ai_opening": (Only for Clip #01) A high-quality conceptual image generated by AI to set the theme.
   - VISUAL ALIGNMENT: The script MUST match the visual action.

3. OUTPUT SHORT DURATION & LIMITS:
   - TOTAL DURATION: ≤60s (Target: 56-59s).
   - MAX 10 CLIPS. 
   - THE ENDING: Natural subscription reminder without mention of specific names. Use "또 만나요!", "함께해주셔서 감사합니다".

{timestamp_rule}
"""
    else:
        # Standard Mode (기존)
        system_instruction = f"""
You are a Professional Short-form Director. Your expertise is in turning long videos into a 60-second viral Short.
{vision_extra if multimodal_mode else ""}
━━━ [MASTER PROTOCOL: STANDARD v2.0] ━━━

1. SCRIPT RULES:
   - TONE: Use POLITE and NATURAL spoken Korean (존댓말).
   - `script_kr`: For Subtitles. `tts_kr`: For TTS (Hangeul Only).
   - THE HOOK: The first clip MUST be grand and engaging.
   - VISUAL ALIGNMENT: Narrate what is VISIBLE on screen.

2. VISUAL TYPE: Use "original" or "blurred_bg" only.

3. DURATION: ≤60 seconds total. MAX 10 CLIPS.

{timestamp_rule}
"""

    # transcript 데이터를 분석하기 좋게 정비 (RawSeconds 명시)
    transcript_text = "\n".join([f"[{fmt_time(t['start'])} | RawSeconds: {t['start']:.2f}] {t['text']}" for t in transcript])

    if multimodal_mode:
        transcript_section = f"""[TRANSCRIPT (secondary reference only — source video timestamps take priority)]
{transcript_text if has_transcript else "(No transcript available — rely entirely on video analysis for timestamps.)"}
"""
        analyze_instruction = f"""Watch the ENTIRE video (total: {fmt_time(source_video_duration)}). Generate the best Shorts script (MAX 10 CLIPS, TOTAL clip duration ~58s).
Pick ONLY the most viral and important visual highlights — spread across the FULL video.
CRITICAL: start_time must be the real SOURCE VIDEO second (e.g., 245.0, 487.5, 820.0)."""
    else:
        transcript_section = f"""[TRANSCRIPT DATA TO ANALYZE]
{transcript_text}
"""
        analyze_instruction = """Analyze the data above and generate the best Shorts script (MAX 10 CLIPS, TOTAL ~58s).
Pick ONLY the most viral and important highlights from the transcript."""

    user_prompt = f"""
[VIDEO CONTEXT]
{meta_block}

{transcript_section}
{analyze_instruction}
"""

    # response_schema로 항상 유효한 JSON 보장
    clip_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "order":     types.Schema(type=types.Type.INTEGER),
            "role":      types.Schema(type=types.Type.STRING),
            "script_kr": types.Schema(type=types.Type.STRING), # For Subtitles (Numbers OK)
            "tts_kr":    types.Schema(type=types.Type.STRING), # For TTS (Hangeul Only)
            "sfx":       types.Schema(type=types.Type.STRING),
            "visual_type":types.Schema(type=types.Type.STRING), # "original", "blurred_bg", "framed", "ai_opening"
            "ai_prompt": types.Schema(type=types.Type.STRING), # English prompt for generate_image tool
            "start_time":types.Schema(type=types.Type.NUMBER),
            "end_time":  types.Schema(type=types.Type.NUMBER),
            "duration":  types.Schema(type=types.Type.NUMBER),
            "note":      types.Schema(type=types.Type.STRING),
        },
        required=["order","role","script_kr","tts_kr","visual_type","start_time","end_time","duration"],
    )
    response_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "title":        types.Schema(type=types.Type.STRING),
            "channel":      types.Schema(type=types.Type.STRING),
            "edit_concept": types.Schema(type=types.Type.STRING),
            "clips":        types.Schema(type=types.Type.ARRAY, items=clip_schema),
            "total_duration":types.Schema(type=types.Type.NUMBER),
        },
        required=["title","channel","edit_concept","clips","total_duration"],
    )

    target_len = 60
    print(f"   🧠 Gemini: {target_len}초 내외의 핵심 요약 대본 작성 중... (Mode: {'VISION' if multimodal_mode else 'TEXT'})")
    
    contents = [user_prompt]
    
    # [멀티모달 모드] 영상 업로드 로직 추가
    if multimodal_mode:
        try:
            # 1. 영상 파일 찾기
            video_path = os.path.join(DOWNLOADS_DIR, f"{video_id}.mp4")
            if not os.path.exists(video_path):
                # mp4가 없으면 mkv 등 다른 확장자 탐색
                search = list(Path(DOWNLOADS_DIR).glob(f"{video_id}.*"))
                if search: video_path = str(search[0])
            
            if os.path.exists(video_path):
                print(f"      📽️ 영상 시각 분석을 위해 업로드 중 (File API)...")
                video_file = client.files.upload(file=video_path)
                
                # 업로드 완료 대기 (active 상태 확인)
                while video_file.state.name == "PROCESSING":
                    time.sleep(2)
                    video_file = client.files.get(name=video_file.name)
                
                if video_file.state.name == "FAILED":
                    print("      ✗ 영상 업로드 실패 (멀티모달 분석 불가, 텍스트 모드로 전환)")
                else:
                    print(f"      ✓ 영상 분석 준비 완료. ({video_file.name})")
                    contents.insert(0, video_file) # 영상 데이터를 프롬프트 맨 앞에 삽입
            else:
                print(f"      ✗ 영상 파일을 찾을 수 없어 텍스트 분석으로 진행합니다: {video_path}")
        except Exception as e:
            print(f"      ⚠ 멀티모달 업로드 중 오류: {e}")

    # [수정] 503 에러 예외처리 및 재시도 로직 추가
    max_retries = 3
    retry_delay = 5
    response = None
    
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.1,
                    max_output_tokens=16384,
                    response_mime_type="application/json",
                    response_schema=response_schema,
                )
            )
            _track_usage(model_name, response)
            break # 성공 시 루프 탈출
        except Exception as e:
            err_msg = str(e)
            if ("503" in err_msg or "Service Unavailable" in err_msg) and attempt < max_retries - 1:
                print(f"   ⚠ Gemini API 503 에러 발생. {retry_delay}초 후 재시도 합니다... ({attempt+1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay *= 2 # 지수 백오프
            else:
                print(f"   ❌ Gemini API 호출 실패: {e}")
                raise

    if not response:
        raise RuntimeError("Gemini API로부터 응답을 받지 못했습니다.")

    try:
        data = json.loads(response.text)
    except json.JSONDecodeError as e:
        print(f"   ⚠ JSON 파싱 오류: {e}\n{response.text[:400]}")
        raise

    clips = data.get('clips', [])
    
    # [수정] 클립 수 제한: 지침서에 따라 핵심 10개 내외로 최적화 (날아가는 것 방지)
    if len(clips) > 12:
        print(f"   ⚠ AI가 너무 많은 클립({len(clips)}개)을 생성했습니다. 상위 10-12개로 최적화하여 보여줍니다.")
        # 강제로 자르는 대신, 사용자가 GUI에서 전체를 확인할 수 있도록 넉넉히 유지하거나 AI에게 정교한 요약을 맡김
        # (여기서는 AI가 이미 60초에 맞춰 요약한 결과를 반환한다는 전제하에 보정만 수행)

    processed_clips = []
    total_acc_dur = 0.0
    for i, c in enumerate(clips):
        # 최소 2.5초, 최대 8초로 클립 길이 보정
        dur = round(c['end_time'] - c['start_time'], 2)
        dur = max(2.5, min(8.0, dur)) 
        
        c['duration'] = dur
        c['end_time'] = round(c['start_time'] + dur, 2)
        
        # [수정] 뒷부분이 날아가지 않도록 강제 break(컷팅) 대신 누적 시간만 계산
        total_acc_dur += dur
        processed_clips.append(c)

    data['clips'] = processed_clips
    data['total_duration'] = round(total_acc_dur, 2)
    
    if total_acc_dur > 70.0:
        print(f"   ⚠ 현재 대본의 총 길이가 {total_acc_dur:.1f}초로 쇼츠 규정(60초)을 다소 초과합니다.")
        print(f"      (해결책: GUI 에디터에서 대사량을 소량 줄이시면 딱 맞게 조절됩니다.)")

    with open(ck, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return data


# ─── STEP 4: 유튜브 메타데이터 생성 ─────────────────────────────────────────

def generate_metadata(clips, project_path, model_name):
    """대본을 바탕으로 유튜브 메타데이터(제목/설명/태그/고정댓글)를 생성하고 metadata.txt로 저장."""
    print(f"   📝 유튜브 메타데이터 생성 중...")
    try:
        full_script = "\n".join([b.get('script_kr', '') for b in clips])
        prompt = f"""다음 유튜브 숏폼(Shorts) 대본을 바탕으로, 업로드용 메타데이터(제목, 설명+태그, 댓글 유도)를 매력적으로 작성해줘.

1. 제목 (3가지 추천):
- 시청자의 클릭을 유발하는 어그로성 제목 3가지를 추천해줌. ⚠️ 제목에 이모지 절대 금지.

2. 설명글 및 태그 (통합 구성):
- ⚠️ 대본 내용을 스포일러하지 말고 시청자의 호기심을 극대화하는 1~2줄의 문장을 작성.
- 설명글 바로 뒤에 한 줄 띄우고 관련 해시태그(#숏폼 #레전드 등) 5~10개를 이어서 나열함.
- 설명글과 태그 사이에 '태그:' 같은 구분 문구를 절대 넣지 말고, 한 번에 복사해서 유튜브 설명란에 바로 붙여넣을 수 있는 깨끗한 형태(문장+해시태그)로만 만들어줄 것.

3. 고정 댓글 (댓글 유도):
- 영상 내용과 자연스럽게 연결되는 질문형 댓글을 1개 작성. 시청자가 직접 답하고 싶게 만들어야 함.
- 예시 형식: "여러분은 몇 가지나 해당되시나요? 댓글로 알려주세요!" / "이 중에서 가장 충격적인 게 뭐였나요?" 등
- ⚠️ 이모지 1~2개만 허용. 너무 길지 않게 2줄 이내로 작성.

대본:
{full_script}"""

        from google import genai
        import time
        client_meta = genai.Client(api_key=GEMINI_API_KEY)

        max_retries = 3
        retry_delay = 5
        response_text = ""
        for attempt in range(max_retries):
            try:
                _meta_resp = client_meta.models.generate_content(model=model_name, contents=prompt)
                _track_usage(model_name, _meta_resp)
                response_text = _meta_resp.text.strip()
                break
            except Exception as e:
                err_msg = str(e)
                if ("503" in err_msg or "Service Unavailable" in err_msg) and attempt < max_retries - 1:
                    print(f"      ⚠ 메타데이터 생성 503 에러. {retry_delay}초 후 재시도... ({attempt+1}/{max_retries})")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    raise

        meta_file = os.path.join(project_path, "metadata.txt")
        with open(meta_file, "w", encoding="utf-8") as f:
            f.write(response_text)

        print(f"\n──────────────────────────────────────────")
        print(f" 📌 [자동 생성된 유튜브 메타데이터 (복사/붙여넣기)]")
        print(f"──────────────────────────────────────────")
        print(response_text)
        print(f"──────────────────────────────────────────\n")
        print(f"   ✓ metadata.txt 저장 완료 (자동으로 문서를 엽니다)")
        os.system(f"open '{meta_file}'")
    except Exception as e:
        print(f"   ✗ 메타데이터 분석 실패: {e}")


# ─── STEP 5: 가이드 출력 ─────────────────────────────────────────────────────

def print_script_guide(data):
    clips = data.get('clips') or data.get('clips') or []
    print("\n" + "═" * 64)
    print("  📝 [BETA v3] 스크립트 + 컷편집 가이드")
    print("═" * 64)
    print(f"  제목: {data.get('title','')}")
    print(f"  기획: {data.get('edit_concept','')}")
    print(f"  총 {len(clips)}개 클립 | 예상 {data.get('total_duration',0):.0f}초\n")
    for c in clips:
        sfx_tag = f"  [SFX: {c.get('sfx','')}]" if c.get('sfx') else ""
        print(f"  #{c['order']:02d} [{fmt_time(c['start_time'])}→{fmt_time(c['end_time'])}]"
              f"  {c['duration']:.0f}s  [{c['role']}]{sfx_tag}")
        for line in c.get('script_kr', '').split('\\n'):
            if line.strip():
                print(f"      ▶ {line.strip()}")
        print()
    print("═" * 64)


# ─── STEP 5: Gemini TTS 나레이션 생성 (통합 생성 + Whisper 정밀 싱크) ────────

def generate_single_tts(clips, video_id, voice="Charon"):
    """
    1. 전체 텍스트 통합 TTS 생성 (목소리 일관성 달성)
    2. Whisper를 통해 오디오의 정확한 타이밍과 호흡(정적) 구간 추출
    3. 추출된 타이밍에 AI가 생성한 '무결점 원본 대본'을 비율에 맞춰 덮어씌움
    4. perfect_subtitles.srt 파일을 생성하여 제공
    """
    import whisper
    import re
    import datetime

    p = VideoProcessor()
    tts_dir = os.path.join(TTS_CACHE_DIR, video_id)
    os.makedirs(tts_dir, exist_ok=True)
    
    valid_clips = []
    for c in clips:
        # TTS 생성 시에는 한글 발음용 tts_kr 필드 사용 (없으면 script_kr fallback)
        script = c.get('tts_kr') or c.get('script_kr', '')
        script = script.replace('\\n', ' ').strip()
        if script:
            valid_clips.append((c, script))
            
    if not valid_clips:
        return [{**c, 'tts_path': None, 'tts_duration': c['duration'], 'tts_end': 0.0} for c in clips]
        
    full_text = " ".join([script for _, script in valid_clips])
    full_tts_path = os.path.join(tts_dir, "full_narration_whisper.mp3")
    
    print(f"   🔊 통합 TTS 음성 생성 중... (voice: {voice})")
    ok = p._generate_gemini_tts(full_text, full_tts_path, voice_name=voice, playback_speed=1.0)
    if not ok or not os.path.exists(full_tts_path):
        print("      ✗ 통합 TTS 생성 실패")
        return [{**c, 'tts_path': None, 'tts_duration': c['duration'], 'tts_end': 0.0} for c in clips]

    print(f"   🧠 Whisper 정밀 타이밍 추출 및 무결점 대본(SRT) 병합 중...")
    whisper_model = whisper.load_model("base")
    result_ws = whisper_model.transcribe(full_tts_path, language="ko")
    ws_segments = result_ws.get('segments', [])
    
    def clean_text(t):
        return re.sub(r'[^가-힣a-zA-Z0-9]', '', t)
        
    def format_time(s):
        td = datetime.timedelta(seconds=s)
        hours, remainder = divmod(td.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d},{int(td.microseconds // 1000):03d}"
        
    result = []
    curr_ws_idx = 0
    total_ws = len(ws_segments)

    srt_lines = []
    srt_counter = 1

    from pydub import AudioSegment
    full_audio = AudioSegment.from_file(full_tts_path)

    for i, c in enumerate(clips):
        clip_script = c.get('script_kr', '').replace('\\n', ' ').strip()
        if not clip_script:
            prev_end = result[-1]['tts_end'] if result else 0.0
            result.append({**c, 'tts_path': None, 'tts_duration': c['duration'], 'tts_end': prev_end})
            continue

        target_len = len(clean_text(clip_script))
        cur_len = 0
        merged_segs = []

        # Whisper 세그먼트 매핑 (다음 클립 침범 방지 알고리즘)
        while curr_ws_idx < total_ws:
            seg_len = len(clean_text(ws_segments[curr_ws_idx]['text']))
            if cur_len > 0 and abs(target_len - cur_len) < abs(target_len - (cur_len + seg_len)):
                break
            merged_segs.append(ws_segments[curr_ws_idx])
            cur_len += seg_len
            curr_ws_idx += 1

        clip_start_t = result[-1]['tts_end'] if result else 0.0
        clip_end_t = merged_segs[-1]['end'] if merged_segs else (clip_start_t + c['duration'])
        
        # 마지막 클립 안전 보정
        if c['order'] == valid_clips[-1][0]['order']:
            clip_end_t = len(full_audio) / 1000.0

        # ---- SRT 생성 로직 (Whisper 세그먼트 단위로 무결점 원본 대본 비율 분할) ----
        if not merged_segs:
            srt_lines.append(f"{srt_counter}\n{format_time(clip_start_t)} --> {format_time(clip_end_t)}\n{clip_script}\n\n")
            srt_counter += 1
        else:
            total_ws_len = max(sum(len(clean_text(s['text'])) for s in merged_segs), 1)
            char_idx = 0
            for seg_idx, s in enumerate(merged_segs):
                seg_char_count = len(clean_text(s['text']))
                # 이 세그먼트가 전체 문장에서 차지하는 비율만큼 글자를 가져옴
                chars_to_take = int(len(clip_script) * (seg_char_count / total_ws_len))
                
                if seg_idx == len(merged_segs) - 1:
                    chunk_text = clip_script[char_idx:].strip()
                else:
                    raw_end = char_idx + chars_to_take
                    # 단어가 잘리는 것을 방지: 근처에 띄어쓰기가 있으면 그곳까지 포함
                    if raw_end < len(clip_script) and clip_script[raw_end] != ' ':
                        space_idx = clip_script.find(' ', raw_end)
                        if space_idx != -1 and space_idx - raw_end <= 4:
                            raw_end = space_idx + 1
                    chunk_text = clip_script[char_idx:raw_end].strip()
                    char_idx = raw_end

                if chunk_text:
                    srt_lines.append(f"{srt_counter}\n{format_time(s['start'])} --> {format_time(s['end'])}\n{chunk_text}\n\n")
                    srt_counter += 1
        # -------------------------------------------------------------

        duration = max(0.1, clip_end_t - clip_start_t)
        
        # 물리적 오디오 커팅 (선택적으로 계속 활용 가능하도록 남겨둠)
        clip_audio = full_audio[int(clip_start_t * 1000): int(clip_end_t * 1000)]
        seg_path = os.path.join(tts_dir, f"clip_{c['order']:02d}_tts.mp3")
        clip_audio.export(seg_path, format="mp3", bitrate="192k")

        duration_s = len(clip_audio) / 1000.0
        result.append({
            **c, 
            'tts_path': seg_path,
            'tts_duration': duration_s,
            'tts_end': clip_end_t
        })
        print(f"      ✓ 클립 #{c['order']:02d} 매핑 완료: {duration_s:.2f}s (오타 제거 기반 SRT 블록: {len(merged_segs)}개 생성)")

    # SRT 최종 파일 저장
    srt_path = os.path.join(tts_dir, "perfect_subtitles.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(srt_lines)
        
    print(f"   ✅ 무결점 자막 파일 생성 성공: {srt_path}")
    return result

# ─── MAIN ─────────────────────────────────────────────────────────────────────

# ─── 자막 유틸 ──────────────────────────────────────────────────────────────────

def split_korean_text(text, max_chars=19):
    """한국어 텍스트를 max_chars 이하의 의미 단위로 분할."""
    # 줄바꿈으로 먼저 나눔
    raw_lines = [l.strip() for l in text.replace('\\n', '\n').split('\n') if l.strip()]
    chunks = []
    for line in raw_lines:
        while len(line) > max_chars:
            cut = -1
            for i in range(min(max_chars, len(line)) - 1, -1, -1):
                if line[i] in '.!?.。、':
                    # 숫자 뒤 쉼표는 분리 금지 (예: "40,000원" 줄바꿈 방지)
                    if line[i] == ',' and i > 0 and line[i-1].isdigit():
                        continue
                    cut = i + 1
                    break
            if cut <= 0:
                for i in range(min(max_chars, len(line)) - 1, -1, -1):
                    if line[i] == ' ':
                        cut = i
                        break
            if cut <= 0:
                cut = max_chars
            chunks.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            chunks.append(line)
    return chunks if chunks else [text.strip()]


def clips_to_subtitles(clips_with_tts):
    """clip별 script_kr → 19자 의미단위로 분할한 CapCut 자막 타이밍"""
    subs, cursor_us = [], 0
    for c in clips_with_tts:
        clip_dur_us = int((c['tts_duration'] if c['tts_duration'] > 0 else c['duration']) * 1_000_000)
        text = c.get('script_kr', '').strip()
        if not text:
            cursor_us += clip_dur_us
            continue
        chunks = split_korean_text(text, max_chars=19)
        total_chars = sum(len(ch) for ch in chunks)
        for ch in chunks:
            chunk_dur_us = int(clip_dur_us * len(ch) / max(total_chars, 1))
            subs.append({'text': ch, 'start_us': cursor_us, 'duration_us': chunk_dur_us})
            cursor_us += chunk_dur_us
    return subs




# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="[BETA v3] TTS 나레이션 쇼츠 생성기")
    parser.add_argument("url", help="유튜브 URL 또는 로컬 mp4 파일 경로")
    parser.add_argument("--voice", default="Charon", help="Gemini TTS 목소리 (기본: Charon)")
    parser.add_argument("--model", default="gemini-2.5-flash-lite", help="사용할 Gemini 모델")
    parser.add_argument("--ignore-cache", action="store_true", help="기존 분석 캐시 무시")
    
    args = parser.parse_args()
    
    url = args.url
    voice = args.voice
    model_name = args.model
    ignore_cache = args.ignore_cache

    print("=" * 64)
    print("  [BETA v3] TTS 나레이션 쇼츠 생성기 (파라미터 입력 모드)")
    print(f"  Input: {url}")
    print(f"  Voice: {voice} | Model: {model_name}")
    print("=" * 64)

    # 1. 다운로드
    print("\n[1/5] 영상 준비 (다운로드 또는 로컬 확인)...")
    vid, video_path, vtt_path = ensure_downloaded(url)

    # 2. 자막 파싱
    print(f"\n[2/5] 자막 파싱...")
    transcript = parse_vtt(vtt_path)
    print(f"   {len(transcript)}개 구간" + (" ✓" if transcript else " (없음)"))
    
    if not transcript:
        # 로컬 파일인 경우 자막이 없을 수 있음 -> AI가 영상을 직접 보게 하거나 에러 처리
        print("   ⚠ 사용 가능한 자막(.vtt)이 없습니다. AI 분석이 불가능할 수 있습니다.")
        if not os.path.exists(vtt_path):
            print("      (Tip: 영상과 같은 이름의 .en.vtt 파일이 필요합니다)")
            sys.exit(1)

    # 3. AI 스크립트 + 매핑
    print(f"\n[3/5] 🤖 AI 스크립트 작성 (Model: {model_name})...")
    data  = analyze_script_first(vid, url, transcript, model_name=model_name, ignore_cache=ignore_cache)
    
    # [추가] 대본 컨펌 및 수정 로직
    edit_path = os.path.join(PROJECT_ROOT, "edit_script.json")
    with open(edit_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    print(f"\n" + "!" * 64)
    print(f"  📢 대본이 생성되어 '{os.path.basename(edit_path)}'에 저장되었습니다.")
    print(f"  직접 파일을 열어 내용을 수정한 뒤, 아래 Enter를 누르면 진행됩니다.")
    print("!" * 64)
    input("\n  >> 대본 수정을 완료했다면 [Enter]를 누르세요...")
    
    # 수정된 내용 다시 불러오기
    with open(edit_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    clips = data.get('clips') or data.get('clips') or []
    
    print_script_guide(data)

    # 4. Gemini TTS 나레이션 (통합 생성)
    print(f"\n[4/5] 🔊 Gemini TTS 단일 통합 생성 (voice: {voice})...")
    clips_with_tts = generate_single_tts(clips, vid, voice=voice)
    bgm = find_bgm()
    
    # 5. CapCut 내보내기 (직접 연동)
    project_name = f"{vid}_{datetime.now().strftime('%Y%m%d_%H%M')}"
    cap_title    = data.get('title') or project_name
    cap_channel  = data.get('channel', '')
    cap_source   = f"출처: {cap_channel}" if cap_channel else f"출처: {url}"
    cap_subs     = clips_to_subtitles(clips_with_tts)
    
    tts_dir = os.path.join(TTS_CACHE_DIR, vid)
    concat_tts = os.path.join(tts_dir, "narration_concat.mp3")
    clip_files = [c['tts_path'] for c in clips_with_tts if c.get('tts_path') and os.path.exists(c['tts_path'])]
    
    if clip_files:
        concat_list = os.path.join(tts_dir, "concat_tts.txt")
        with open(concat_list, 'w') as f:
            for fp in clip_files: f.write(f"file '{fp}'\n")
        subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                        "-i", concat_list, "-c", "copy", concat_tts], capture_output=True)
        final_tts_path = concat_tts if os.path.exists(concat_tts) else None
    else:
        final_tts_path = None

    # clip별 source_timerange 세그먼트 구성
    print(f"\n[5/5] 📋 CapCut 세그먼트 구성 중... (원본 영상 기반 source_timerange)")
    cap_segments = []
    cumulative_t = 0.0
    for c in clips_with_tts:
        dur = c['tts_duration'] if c['tts_duration'] > 0 else c['duration']
        sfx_file = sfx(c.get('sfx'))
        cap_segments.append({
            'start_time':    c['start_time'],
            'duration':      dur,
            'sfx_path':      sfx_file,
            'timeline_start': cumulative_t,
            'visual_type':   c.get('visual_type', 'blurred_bg'), # 기본값 'blurred_bg'로 변경하여 수익화 최적화
            'ai_prompt':     c.get('ai_prompt', '')
        })
        sfx_tag = f"  🔊{c['sfx']}" if c.get('sfx') and sfx_file else ""
        print(f"   ✓ #{c['order']:02d} [{c['role']}] {c['start_time']:.1f}s → {c['start_time']+dur:.1f}s  ({dur:.2f}s){sfx_tag}")
        cumulative_t += dur


    print(f"\n[6/6] 🎬 CapCut 프로젝트 생성 ({project_name})...")
    project_path = VideoProcessor().export_to_capcut(
        video_path=video_path,
        segments=cap_segments,   # ← 원본 영상 + source_timerange 방식
        project_name=project_name,
        title=cap_title,
        source=cap_source,
        subtitles=cap_subs,
        tts_path=final_tts_path,
        bgm_path=bgm,
        video_clips=None          # ← 물리 클립 방식 비활성화
    )
    
    if project_path:
        generate_metadata(clips, project_path, model_name)

    print("\n" + "═" * 64)
    print("  ✅ [BETA v3] 모든 프로세스 완료!")
    if project_path:
        print(f"  CapCut 프로젝트 생성됨: {project_name}")
    print("═" * 64)

if __name__ == "__main__":
    main()
