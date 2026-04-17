"""
STEP 4 — Motion Generator (Image → Short Loop Video)
Veo API로 정적 이미지에 4~5초 미세 움직임을 부여한다.

Notes:
  - 모델: veo-3.0-generate-preview (현재 가장 안정적인 공개 버전)
  - Image-to-Video 방식: 이미지 + 텍스트 프롬프트로 짧은 클립 생성
  - 할당량 제한이 엄격하므로 실패 시 폴백(fallback)으로 Ken Burns FFmpeg 처리
"""
import subprocess
import time
from pathlib import Path

import sys
APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
from config import GEMINI_API_KEY

from google import genai
from google.genai import types

client = genai.Client(api_key=GEMINI_API_KEY)

VEO_MODEL = "veo-3.0-generate-preview"
DEFAULT_MOTION = "slow cinematic zoom in, subtle ambient motion, dreamy atmosphere, 4 seconds loop"


def _ffmpeg_ken_burns(image_path: Path, output_path: Path, duration: int = 5) -> bool:
    """Veo API 실패 시 FFmpeg Ken Burns 폴백."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-vf", f"scale=8000:-1,zoompan=z='min(zoom+0.0015,1.5)':d={duration * 25}:s=1920x1080",
        "-t", str(duration),
        "-r", "25",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.returncode == 0


def generate_motion_clip(
    image_path: Path,
    output_path: Path,
    motion_prompt: str = DEFAULT_MOTION,
    duration_sec: int = 5,
) -> bool:
    """
    단일 이미지로부터 모션 클립을 생성한다.

    Returns:
        True: 성공 (Veo 또는 폴백 FFmpeg)
        False: 실패
    """
    output_path = Path(output_path)
    if output_path.exists():
        print(f"   ✓ 클립 캐시: {output_path.name}")
        return True

    print(f"   🎬 모션 생성: {image_path.name} ...")

    try:
        # 이미지를 Gemini File API로 업로드
        image_file = client.files.upload(file=str(image_path))
        while image_file.state.name == "PROCESSING":
            time.sleep(1)
            image_file = client.files.get(name=image_file.name)

        operation = client.models.generate_videos(
            model=VEO_MODEL,
            prompt=motion_prompt,
            image=types.Image(
                image_bytes=image_path.read_bytes(),
                mime_type="image/png",
            ),
            config=types.GenerateVideosConfig(
                aspect_ratio="16:9",
                duration_seconds=duration_sec,
                number_of_videos=1,
            ),
        )

        # 생성 완료 대기
        while not operation.done:
            time.sleep(5)
            operation = client.operations.get(operation)

        video_data = operation.response.generated_videos[0].video.video_bytes
        output_path.write_bytes(video_data)
        print(f"   ✓ Veo 클립 저장: {output_path.name}")
        return True

    except Exception as e:
        print(f"   ⚠ Veo API 실패 ({e}) → FFmpeg Ken Burns 폴백 적용")
        ok = _ffmpeg_ken_burns(image_path, output_path, duration=duration_sec)
        if ok:
            print(f"   ✓ 폴백 클립 저장: {output_path.name}")
        return ok


def generate_all_clips(
    script: dict,
    images_dir: Path,
    clips_dir: Path,
) -> list[Path]:
    """
    모든 씬 이미지에 대해 모션 클립을 생성한다.
    """
    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    scenes = script.get("scenes", [])
    results = []

    for scene in scenes:
        n = scene["order"]
        image_path = Path(images_dir) / f"scene_{n:03d}.png"
        clip_path = clips_dir / f"scene_{n:03d}.mp4"
        motion_hint = scene.get("motion_hint", DEFAULT_MOTION)
        duration = min(int(scene.get("duration_sec", 20)), 60)  # 씬당 최대 60초

        if not image_path.exists():
            print(f"   ✗ 이미지 없음, 건너뜀: {image_path.name}")
            results.append(None)
            continue

        # 한 씬이 20초 이상이면 동일 이미지를 루프로 반복 처리
        ok = generate_motion_clip(image_path, clip_path, motion_hint, duration_sec=min(duration, 8))
        results.append(clip_path if ok else None)

    return results
