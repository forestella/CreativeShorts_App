"""
STEP 2 — Script Drafter
채널 스타일 JSON + 주제를 받아 씬 단위 대본을 생성한다.
각 씬은 narration, image_prompt, sfx_keyword를 포함한다.
"""
import json
from pathlib import Path

from google import genai
from google.genai import types

import sys
APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
from config import GEMINI_API_KEY

client = genai.Client(api_key=GEMINI_API_KEY)

SCENE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "order":          types.Schema(type=types.Type.INTEGER),
        "section":        types.Schema(type=types.Type.STRING),   # hook/body/conclusion
        "narration_kr":   types.Schema(type=types.Type.STRING),   # TTS용 한국어 (한글 숫자)
        "subtitle_kr":    types.Schema(type=types.Type.STRING),   # 자막용 한국어 (아라비아 숫자)
        "image_prompt_en": types.Schema(type=types.Type.STRING),  # 이미지 생성 AI 프롬프트 (영어)
        "motion_hint":    types.Schema(type=types.Type.STRING),   # Veo 모션 지시 (영어, 1문장)
        "duration_sec":   types.Schema(type=types.Type.NUMBER),   # 예상 재생 시간
        "sfx":            types.Schema(type=types.Type.STRING),   # SFX 키워드
    },
    required=["order", "section", "narration_kr", "subtitle_kr", "image_prompt_en", "duration_sec"],
)

SCRIPT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "title":          types.Schema(type=types.Type.STRING),
        "topic":          types.Schema(type=types.Type.STRING),
        "total_duration_sec": types.Schema(type=types.Type.NUMBER),
        "scenes":         types.Schema(type=types.Type.ARRAY, items=SCENE_SCHEMA),
    },
    required=["title", "topic", "total_duration_sec", "scenes"],
)


def draft_script(topic: str, style: dict, target_duration: int = 600) -> dict:
    """
    주제와 채널 스타일로 롱폼 대본을 생성한다.

    Args:
        topic: 영상 주제 (한국어)
        style: analyze_channel()이 반환한 스타일 가이드 dict
        target_duration: 목표 영상 길이 (초, 기본 10분)

    Returns:
        script.json 형태의 dict
    """
    style_summary = f"""
채널 어조: {style.get('tone', '지적이고 냉소적')}
어조 키워드: {', '.join(style.get('tone_keywords', []))}
Hook 패턴: {style.get('script_structure', {}).get('hook_pattern', '')}
Body 패턴: {style.get('script_structure', {}).get('body_pattern', '')}
결론 패턴: {style.get('script_structure', {}).get('conclusion_pattern', '')}
비주얼 스타일: {style.get('visual_style_prompt', 'minimalist flat illustration')}
내레이션 속도: {style.get('narration_pace', 'medium')}
씬 유형: {', '.join(style.get('scene_types', []))}
"""

    system_instruction = f"""
당신은 위 채널 스타일을 완벽히 복제하는 롱폼 유튜브 대본 작가입니다.

[채널 스타일 가이드]
{style_summary}

[대본 작성 규칙]
1. 총 영상 길이: {target_duration}초 ({target_duration // 60}분) 목표
2. 씬당 평균 15~30초. 총 씬 수: {target_duration // 20}개 내외
3. 각 씬의 image_prompt_en:
   - 채널 비주얼 스타일({style.get('visual_style_prompt', '')})을 접두사로 반드시 포함
   - 씬 내용을 상징하는 구체적 오브젝트 묘사 추가
   - 예: "minimalist flat illustration, muted tones — a small human figure standing before a giant AI brain"
4. motion_hint: 4초짜리 루프 영상 생성 지시. Ken Burns / 미세 흔들림 위주.
   예: "slow zoom in toward center, subtle particles floating"
5. narration_kr: TTS 발음을 위해 모든 숫자를 한글로 (예: "삼천 명")
6. subtitle_kr: 자막용, 아라비아 숫자 사용 가능 (예: "3,000명")
7. 채널 어조를 철저히 복제: {style.get('tone', '지적이고 냉소적')}
8. CTA(마지막 씬)는 반드시 구독/좋아요/알림 설정 요청만 포함할 것.
   웹사이트, 앱, 퀴즈, 링크, 외부 서비스 등 실존하지 않는 리소스는 절대 언급 금지.
"""

    print(f"   ✍️  대본 작성 중: '{topic}' ({target_duration}초 목표)...")

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=f"다음 주제로 롱폼 대본을 작성하세요: {topic}",
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.7,
            max_output_tokens=16384,
            response_mime_type="application/json",
            response_schema=SCRIPT_SCHEMA,
        ),
    )

    data = json.loads(response.text)
    print(f"   ✓ 씬 {len(data.get('scenes', []))}개 | 예상 {data.get('total_duration_sec', 0):.0f}초")
    return data
