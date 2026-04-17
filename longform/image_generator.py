"""
STEP 3 — Image Generator
씬별 image_prompt_en을 받아 Imagen 3로 이미지를 생성한다.

Notes:
  - Nano Banana 2는 공개 API 없음 → Google Imagen 3 사용
  - 스타일 일관성: 모든 씬에 style_prefix를 고정 적용
  - Vertex AI SDK 또는 google-genai SDK 사용 (환경에 따라 선택)
"""
import os
import time
from pathlib import Path

import sys
APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
from config import GEMINI_API_KEY

# Imagen 3 via google-genai (Gemini Developer API)
# 참고: Vertex AI 사용 시 google-cloud-aiplatform 필요
from google import genai
from google.genai import types

client = genai.Client(api_key=GEMINI_API_KEY)

IMAGEN_MODEL = "imagen-3.0-generate-002"


def generate_scene_images(
    script: dict,
    output_dir: Path,
    style_prefix: str = "",
) -> list[Path]:
    """
    script['scenes']의 image_prompt_en을 기반으로 씬별 이미지를 생성한다.

    Args:
        script: draft_script() 반환 dict
        output_dir: 이미지 저장 경로 (output/longform/{project_id}/images/)
        style_prefix: 모든 프롬프트에 공통 적용할 스타일 (비어있으면 script에서 자동 추출)

    Returns:
        생성된 이미지 파일 경로 리스트 (씬 순서 보장)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenes = script.get("scenes", [])
    results = []

    for scene in scenes:
        n = scene["order"]
        raw_prompt = scene.get("image_prompt_en", "")
        # style_prefix가 이미 프롬프트에 포함돼 있으면 중복 추가 방지
        if style_prefix and not raw_prompt.startswith(style_prefix[:20]):
            prompt = f"{style_prefix}, {raw_prompt}"
        else:
            prompt = raw_prompt

        out_path = output_dir / f"scene_{n:03d}.png"
        if out_path.exists():
            print(f"   ✓ 이미지 캐시: scene_{n:03d}.png")
            results.append(out_path)
            continue

        print(f"   🎨 이미지 생성 중: scene {n:03d} ...")
        try:
            response = client.models.generate_images(
                model=IMAGEN_MODEL,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio="16:9",   # 롱폼 가로형
                    safety_filter_level="block_some",
                ),
            )
            image_data = response.generated_images[0].image.image_bytes
            out_path.write_bytes(image_data)
            print(f"   ✓ 저장: {out_path.name}")
            results.append(out_path)
        except Exception as e:
            print(f"   ✗ scene {n:03d} 이미지 생성 실패: {e}")
            results.append(None)

        # API 레이트 리밋 방지
        time.sleep(1.0)

    return results
