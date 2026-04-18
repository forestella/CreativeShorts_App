"""
STEP 3 — Image Generator
씬별 image_prompt_en을 받아 Gemini 이미지 모델로 이미지를 생성한다.

캐릭터 일관성:
  1. generate_character_sheet()로 레퍼런스 이미지(character_ref.png) 1장 생성
  2. 이후 모든 씬 생성 시 레퍼런스 이미지를 멀티모달로 함께 전달
"""
import re
import time
from pathlib import Path

import sys
APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
from config import GEMINI_API_KEY

from google import genai
from google.genai import types
client = genai.Client(api_key=GEMINI_API_KEY)

IMAGE_MODEL = "gemini-2.5-flash-image"

_TEXT_INSTRUCTION_PATTERNS = re.compile(
    r"(bold[,\s]+glowing\s+kinetic\s+typography[^.]*\.|"
    r"kinetic\s+typography[^.]*\.|"
    r"text\s*[:\-][^.]*\.|"
    r"(with\s+)?(the\s+)?(words?|text|label|caption|title|subtitle)\s*['\"][^'\"]*['\"][^.]*\.|"
    r"(displaying|showing|reading|saying)\s*['\"][^'\"]*['\"][^.]*\.)",
    re.IGNORECASE,
)

def _strip_text_instructions(prompt: str) -> str:
    """프롬프트에서 텍스트/타이포그래피 삽입 지시어를 제거한다."""
    return _TEXT_INSTRUCTION_PATTERNS.sub("", prompt).strip()


_NO_TEXT_PREFIX = (
    "CRITICAL RULE: The image must contain ABSOLUTELY NO text, letters, words, numbers, "
    "Korean characters, Chinese characters, Japanese characters, captions, labels, signs, "
    "speech bubbles, or any written language of any kind. "
    "Pure visual illustration only. "
    "IMPORTANT: Horizontal landscape orientation, wide 16:9 composition. "
)
_NO_TEXT_SUFFIX = (
    " Again, strictly NO text or writing of any kind anywhere in the image."
)


def _extract_image_bytes(response) -> bytes:
    for part in response.candidates[0].content.parts:
        if part.inline_data and part.inline_data.mime_type.startswith("image/"):
            return part.inline_data.data
    raise RuntimeError("응답에 이미지 데이터 없음")


def generate_character_sheet(
    style: dict,
    output_dir: Path,
    force: bool = False,
) -> Path:
    """
    채널 비주얼 스타일 기반으로 주인공 캐릭터 레퍼런스 시트를 생성한다.
    이후 모든 씬 이미지 생성 시 이 이미지를 참조하여 캐릭터 일관성을 유지한다.

    Returns:
        character_ref.png 경로
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ref_path = output_dir / "character_ref.png"

    if ref_path.exists() and not force:
        print(f"   ✓ 캐릭터 레퍼런스 캐시: {ref_path.name}")
        return ref_path

    visual_style = style.get("visual_style_prompt", "cute chibi anime style illustration")
    channel_name = style.get("channel_name", "")

    prompt = (
        _NO_TEXT_PREFIX
        + f"{visual_style}. "
        "Character reference sheet: a single main character shown in 3 poses — "
        "front view, side view, and a neutral expression close-up. "
        "Simple background. Consistent design across all 3 poses. "
        f"Style matches {channel_name} channel aesthetic."
        + _NO_TEXT_SUFFIX
    )

    print("   🎨 캐릭터 레퍼런스 시트 생성 중...")
    response = client.models.generate_content(
        model=IMAGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE", "TEXT"],
            image_config=types.ImageConfig(aspect_ratio="16:9"),
        ),
    )
    image_data = _extract_image_bytes(response)
    ref_path.write_bytes(image_data)
    print(f"   ✓ 저장: {ref_path.name}")
    return ref_path


def generate_scene_images(
    script: dict,
    output_dir: Path,
    style_prefix: str = "",
    char_ref_path: Path | None = None,
) -> list[Path]:
    """
    script['scenes']의 image_prompt_en을 기반으로 씬별 이미지를 생성한다.
    char_ref_path가 있으면 매 씬 생성 시 레퍼런스 이미지를 함께 전달해 캐릭터 일관성 유지.

    Args:
        script:        draft_script() 반환 dict
        output_dir:    이미지 저장 경로
        style_prefix:  모든 프롬프트에 공통 적용할 스타일
        char_ref_path: character_ref.png 경로 (None이면 레퍼런스 없이 생성)

    Returns:
        생성된 이미지 파일 경로 리스트 (씬 순서 보장)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 레퍼런스 이미지 미리 로드
    ref_image_bytes: bytes | None = None
    if char_ref_path and Path(char_ref_path).exists():
        ref_image_bytes = Path(char_ref_path).read_bytes()
        print(f"   ✓ 캐릭터 레퍼런스 이미지 로드: {Path(char_ref_path).name}")

    scenes = script.get("scenes", [])
    results = []

    for scene in scenes:
        n = scene["order"]
        raw_prompt = _strip_text_instructions(scene.get("image_prompt_en", ""))

        if style_prefix and not raw_prompt.startswith(style_prefix[:20]):
            prompt = f"{style_prefix}, {raw_prompt}"
        else:
            prompt = raw_prompt

        prompt = _NO_TEXT_PREFIX + prompt + _NO_TEXT_SUFFIX

        out_path = output_dir / f"scene_{n:03d}.png"
        if out_path.exists():
            print(f"   ✓ 이미지 캐시: scene_{n:03d}.png")
            results.append(out_path)
            continue

        print(f"   🎨 이미지 생성 중: scene {n:03d} ...")
        try:
            if ref_image_bytes:
                # 레퍼런스 이미지와 함께 전달 → 캐릭터 일관성 유지
                contents = [
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="image/png",
                            data=ref_image_bytes,
                        )
                    ),
                    types.Part(
                        text=(
                            "Using the exact same character design from the reference image above, "
                            f"draw this scene: {prompt}"
                        )
                    ),
                ]
            else:
                contents = prompt

            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    image_config=types.ImageConfig(aspect_ratio="16:9"),
                ),
            )
            image_data = _extract_image_bytes(response)
            out_path.write_bytes(image_data)
            print(f"   ✓ 저장: {out_path.name}")
            results.append(out_path)
        except Exception as e:
            print(f"   ✗ scene {n:03d} 이미지 생성 실패: {e}")
            results.append(None)

        time.sleep(1.0)

    return results
