"""
STEP 1 — Channel Analyzer
채널 페이지 URL 또는 영상 URL을 받아 Gemini 2.5 Pro 멀티모달로 분석하고
스타일 가이드 JSON을 추출·캐싱한다.

채널 URL 입력 시 yt-dlp로 인기순 상위 영상을 자동 선택한다.
단일 영상이 아닌 상위 5개를 동시에 분석해 바이럴리티 공식을 추출한다.
"""
import json
import re
from pathlib import Path

import yt_dlp
from google import genai
from google.genai import types

APP_ROOT = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(APP_ROOT))

from config import GEMINI_API_KEY

CACHE_DIR = APP_ROOT / "cache" / "still_life"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

client = genai.Client(api_key=GEMINI_API_KEY)

STYLE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "channel_id":      types.Schema(type=types.Type.STRING),
        "channel_name":    types.Schema(type=types.Type.STRING),
        "tone":            types.Schema(type=types.Type.STRING),
        "tone_keywords":   types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        "script_structure": types.Schema(
            type=types.Type.OBJECT,
            properties={
                "hook_pattern":      types.Schema(type=types.Type.STRING),
                "body_pattern":      types.Schema(type=types.Type.STRING),
                "conclusion_pattern": types.Schema(type=types.Type.STRING),
            }
        ),
        "visual_style_prompt": types.Schema(type=types.Type.STRING),
        "scene_types":     types.Schema(type=types.Type.ARRAY, items=types.Schema(type=types.Type.STRING)),
        "avg_scene_duration_sec": types.Schema(type=types.Type.NUMBER),
        "narration_pace":  types.Schema(type=types.Type.STRING),
        "sample_script_excerpt": types.Schema(type=types.Type.STRING),

        # ── 바이럴리티 분석 (신규) ───────────────────────────────────────────
        "virality_formula": types.Schema(
            type=types.Type.OBJECT,
            properties={
                "emotional_triggers":  types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="시청자가 느끼는 핵심 감정 (공감/불안/희망/수치심 등)"
                ),
                "audience_pain_points": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="채널이 해결하는 시청자의 핵심 고통/문제"
                ),
                "hook_formulas": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="인기 영상에서 반복되는 Hook 공식 (예: '당신이 X를 하는 진짜 이유')"
                ),
                "title_patterns": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="제목에서 반복되는 패턴 (의문형/부정형/숫자형 등)"
                ),
                "unique_positioning": types.Schema(
                    type=types.Type.STRING,
                    description="이 채널만의 독보적 포지셔닝 — 왜 이 채널을 보는가"
                ),
                "content_gaps": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="채널이 아직 다루지 않은 잠재적 고성과 주제 영역"
                ),
                "retention_techniques": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="시청 지속률을 높이는 편집/내레이션 기법"
                ),
            }
        ),

        "top_videos_analyzed": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "video_id":    types.Schema(type=types.Type.STRING),
                    "title":       types.Schema(type=types.Type.STRING),
                    "why_viral":   types.Schema(type=types.Type.STRING),
                    "key_insight": types.Schema(type=types.Type.STRING),
                }
            ),
            description="분석한 각 영상의 바이럴 이유와 핵심 인사이트"
        ),
    },
    required=[
        "channel_id", "tone", "tone_keywords",
        "script_structure", "visual_style_prompt",
        "scene_types", "narration_pace", "virality_formula",
    ],
)

SYSTEM_PROMPT = """
당신은 유튜브 채널의 성장 전략가이자 콘텐츠 심리학자입니다.
주어진 여러 영상을 보고 해당 채널이 수백만 조회수를 달성하는 '바이럴리티 공식'을 역엔지니어링하세요.

분석 목표:
1. 어조(Tone of Voice): 냉소적/지적/친근/권위적 등을 구체적 형용사로 표현
2. 대본 구조: Hook → Body → CTA 각각의 패턴을 한 문장으로 요약
3. 비주얼 스타일: 이미지 생성 AI에게 전달할 수 있는 영어 스타일 프롬프트
   (예: "minimalist flat illustration, muted earth tones, geometric shapes, no text")
4. 씬 유형: 영상에서 반복되는 씬 카테고리 목록
5. 내레이션 속도: slow/medium/fast 중 하나

바이럴리티 분석 (핵심):
6. 감정 트리거: 시청자가 느끼는 핵심 감정 (공감/불안/희망/수치심/안도 등) — 구체적으로
7. 시청자 고통: 채널이 건드리는 시청자의 핵심 고통점 (예: "내가 왜 이러는지 모르겠다")
8. Hook 공식: 인기 영상 제목/도입부에서 반복되는 패턴
   (예: "당신이 [흔한 행동]을 하는 진짜 이유", "왜 우리는 [역설적 행동]을 하는가")
9. 제목 패턴: 의문형/부정형/숫자형/고발형 등 어떤 제목이 클릭을 유도하는지
10. 독보적 포지셔닝: 이 채널이 경쟁 채널과 다른 점, 시청자가 이 채널만 보는 이유
11. 콘텐츠 공백: 채널 스타일로 제작 가능하지만 아직 다루지 않은 고성과 주제 영역
12. 시청 지속 기법: 내레이션/편집에서 반복되는 retention hook 기법

각 영상별로:
- 이 영상이 바이럴된 핵심 이유 (감정적/심리적 관점에서)
- 다른 영상에서 반복 사용 가능한 핵심 인사이트
"""


def _is_channel_url(url: str) -> bool:
    return bool(re.search(r"youtube\.com/(@[\w-]+|channel/|c/|user/)", url))


def _is_age_restricted(entry: dict) -> bool:
    """
    yt-dlp flat 항목이 19금(성인 인증 필요)인지 판단.
    flat 모드에서는 age_limit이 없을 수 있으므로 제목 키워드도 보조 확인.
    """
    age_limit = entry.get("age_limit") or 0
    if age_limit >= 18:
        return True
    # flat 모드에서 age_limit이 없을 때 제목으로 보조 필터
    title = (entry.get("title") or "").lower()
    adult_keywords = ["adult", "18+", "mature", "explicit", "nsfw", "성인", "19금", "야동", "에로"]
    return any(kw in title for kw in adult_keywords)


def resolve_channel_to_video_urls(channel_url: str, top_n: int = 5) -> list[dict]:
    """
    채널 URL → 인기 영상 상위 N개 URL + 메타데이터 반환.
    /videos?sort=p 탭에서 flat-playlist로 목록 조회.
    19금(age_limit >= 18) 영상은 자동 제외한다.
    """
    base = channel_url.rstrip("/").split("?")[0]
    if base.endswith("/videos"):
        base = base[:-7]
    videos_url = f"{base}/videos?sort=p"
    print(f"   🔎 채널 인기 영상 목록 조회 중... ({videos_url})")

    ydl_opts = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "playlist_end": 40,   # 19금 제외 후 top_n을 채우기 위해 넉넉히 조회
        "skip_download": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(videos_url, download=False)

    entries = info.get("entries") or []
    flat = []
    for e in entries:
        if e.get("entries"):
            flat.extend(e["entries"])
        else:
            flat.append(e)

    # 실제 영상 ID만 필터 (UC...는 채널 ID이므로 제외)
    videos = [
        e for e in flat
        if e.get("id") and not e["id"].startswith("UC") and len(e["id"]) == 11
    ]

    # 19금 영상 제외
    safe_videos = [v for v in videos if not _is_age_restricted(v)]
    skipped = len(videos) - len(safe_videos)
    if skipped:
        print(f"   ⚠️  19금 영상 {skipped}개 제외됨")

    if not safe_videos:
        raise RuntimeError(f"채널에서 분석 가능한 영상을 찾을 수 없습니다 (19금 제외 후): {videos_url}")

    selected = safe_videos[:top_n]
    for v in selected:
        view_count = v.get("view_count") or 0
        print(f"   ✓ [{view_count:,}회] {v.get('title', v['id'])}")

    return [
        {
            "id": v["id"],
            "title": v.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={v['id']}",
            "view_count": v.get("view_count") or 0,
        }
        for v in selected
    ]


# 하위 호환성 유지
def resolve_channel_to_video_url(channel_url: str) -> str:
    videos = resolve_channel_to_video_urls(channel_url, top_n=1)
    return videos[0]["url"]


def get_style_cache_path(channel_id: str) -> Path:
    return CACHE_DIR / f"{channel_id}_style.json"


def _extract_video_id(url: str) -> str:
    """YouTube URL에서 video ID 추출."""
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    return url.split("/")[-1].split("?")[0]


def _get_channel_id_from_url(url: str) -> str:
    """채널 URL에서 채널 식별자 추출 (캐시 키로 사용)."""
    # @handle 형식
    m = re.search(r"@([\w-]+)", url)
    if m:
        return m.group(1)
    # channel/UC... 형식
    m = re.search(r"channel/([\w-]+)", url)
    if m:
        return m.group(1)
    return _extract_video_id(url)


def analyze_channel(url: str, force_refresh: bool = False, top_n: int = 5) -> dict:
    """
    채널 페이지 URL 또는 영상 직접 URL을 받아 스타일 + 바이럴리티 분석 JSON을 반환.
    채널 URL이면 yt-dlp로 인기순 상위 top_n개 영상을 자동 선택한다.
    YouTube URL을 Gemini에 직접 전달 — 영상 다운로드 없음.
    캐시가 있으면 재분석하지 않는다.
    """
    if _is_channel_url(url):
        channel_key = _get_channel_id_from_url(url)
        cache_path = get_style_cache_path(channel_key)

        if not force_refresh and cache_path.exists():
            print(f"   ✓ 채널 스타일 캐시 발견: {cache_path.name}")
            return json.loads(cache_path.read_text(encoding="utf-8"))

        video_metas = resolve_channel_to_video_urls(url, top_n=top_n)
    else:
        # 단일 영상 URL
        vid = _extract_video_id(url)
        cache_path = get_style_cache_path(vid)

        if not force_refresh and cache_path.exists():
            print(f"   ✓ 채널 스타일 캐시 발견: {cache_path.name}")
            return json.loads(cache_path.read_text(encoding="utf-8"))

        video_metas = [{"id": vid, "title": "", "url": url, "view_count": 0}]
        channel_key = vid

    print(f"   🔍 Gemini 2.5 Pro로 {len(video_metas)}개 영상 동시 분석 중... (다운로드 없음)")

    # 여러 영상을 하나의 멀티턴 contents에 담아 전달
    contents = []
    for i, vm in enumerate(video_metas, 1):
        contents.append(
            types.Content(
                role="user",
                parts=[
                    types.Part(text=f"[영상 {i}/{len(video_metas)}] 제목: {vm['title']} | 조회수: {vm['view_count']:,}회"),
                    types.Part(
                        file_data=types.FileData(
                            mime_type="video/mp4",
                            file_uri=vm["url"],
                        )
                    ),
                ]
            )
        )

    contents.append(
        types.Content(
            role="user",
            parts=[types.Part(text=(
                f"위 {len(video_metas)}개 영상을 모두 분석하여 채널의 스타일 가이드와 바이럴리티 공식을 JSON으로 반환하세요. "
                "channel_id는 채널 핸들 또는 첫 번째 영상 ID를 사용하세요."
            ))]
        )
    )

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=STYLE_SCHEMA,
        ),
    )

    data = json.loads(response.text)
    data["channel_id"] = data.get("channel_id") or channel_key
    data["_analyzed_video_ids"] = [vm["id"] for vm in video_metas]

    cache_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   ✓ 스타일 + 바이럴리티 캐시 저장: {cache_path}")
    return data
