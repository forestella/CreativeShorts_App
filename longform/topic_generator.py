"""
STEP 1.5 — Topic Generator
채널 바이럴리티 분석 결과를 바탕으로 고성과 주제 후보를 생성한다.
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

TOPIC_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "topics": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "rank":             types.Schema(type=types.Type.INTEGER),
                    "title_ko":         types.Schema(type=types.Type.STRING,
                                            description="한국어 영상 제목 후보 (클릭 유도형)"),
                    "topic_summary":    types.Schema(type=types.Type.STRING,
                                            description="영상이 다룰 핵심 내용 1~2문장"),
                    "emotional_hook":   types.Schema(type=types.Type.STRING,
                                            description="이 주제가 건드리는 감정 트리거"),
                    "why_viral":        types.Schema(type=types.Type.STRING,
                                            description="채널 공식 기준 왜 잘 터질지 이유"),
                    "target_audience":  types.Schema(type=types.Type.STRING,
                                            description="이 영상이 가장 강하게 공명할 시청자 유형"),
                }
            )
        )
    },
    required=["topics"],
)


def generate_topics(style: dict, count: int = 10, extra_context: str = "") -> list[dict]:
    """
    채널 스타일 JSON으로 고성과 주제 후보를 생성한다.

    Args:
        style:         analyze_channel() 반환 dict (virality_formula 포함)
        count:         생성할 주제 수 (기본 10)
        extra_context: 추가 방향 힌트 (예: "연애/관계 주제 위주로")

    Returns:
        [{rank, title_ko, topic_summary, emotional_hook, why_viral, target_audience}, ...]
    """
    virality = style.get("virality_formula") or {}

    virality_summary = f"""
감정 트리거: {', '.join(virality.get('emotional_triggers', []))}
시청자 고통: {', '.join(virality.get('audience_pain_points', []))}
Hook 공식: {', '.join(virality.get('hook_formulas', []))}
제목 패턴: {', '.join(virality.get('title_patterns', []))}
독보적 포지셔닝: {virality.get('unique_positioning', '')}
콘텐츠 공백: {', '.join(virality.get('content_gaps', []))}
"""

    analyzed = style.get("top_videos_analyzed") or []
    analyzed_summary = "\n".join(
        f"- [{v.get('title','')}] 바이럴 이유: {v.get('why_viral','')} / 인사이트: {v.get('key_insight','')}"
        for v in analyzed
    )

    system_instruction = f"""
당신은 유튜브 채널의 콘텐츠 전략가입니다.
아래 채널의 바이럴리티 공식을 철저히 학습하고, 이 공식에 가장 잘 맞는 새로운 주제를 발굴하세요.

[채널 정보]
채널명: {style.get('channel_name', '')}
어조: {style.get('tone', '')}
비주얼 스타일: {style.get('visual_style_prompt', '')}

[바이럴리티 공식]
{virality_summary}

[분석된 인기 영상 인사이트]
{analyzed_summary if analyzed_summary else '(없음)'}

[주제 생성 규칙]
1. 채널의 Hook 공식과 제목 패턴을 반드시 활용
2. 시청자 고통점을 정확히 건드리는 주제
3. 기존 영상과 겹치지 않되 같은 감정 코드 활용
4. 콘텐츠 공백 영역 우선 탐색
5. 제목은 실제로 클릭하고 싶게 — 추상적인 제목 금지
"""

    user_prompt = f"{count}개의 고성과 주제 후보를 생성하세요."
    if extra_context:
        user_prompt += f"\n추가 방향: {extra_context}"

    print(f"   💡 주제 {count}개 생성 중...")

    response = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.9,
            response_mime_type="application/json",
            response_schema=TOPIC_SCHEMA,
        ),
    )

    data = json.loads(response.text)
    return data.get("topics", [])


def print_topics(topics: list[dict]) -> None:
    """주제 목록을 터미널에 보기 좋게 출력."""
    print("\n" + "=" * 64)
    print("  📋 주제 후보 목록")
    print("=" * 64)
    for t in topics:
        print(f"\n  [{t['rank']:2d}] {t['title_ko']}")
        print(f"       내용: {t['topic_summary']}")
        print(f"       감정: {t['emotional_hook']}")
        print(f"       이유: {t['why_viral']}")
        print(f"       타겟: {t['target_audience']}")
    print("\n" + "=" * 64)
