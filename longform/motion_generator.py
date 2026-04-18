"""
STEP 4 — Motion Generator (Image → Short Loop Video)
FFmpeg Ken Burns 효과로 정적 이미지에 미세 움직임을 부여한다.
Veo API는 비용이 높아 기본 비활성화 — --use-veo 플래그로 활성화 가능.
"""
import subprocess
from pathlib import Path

DEFAULT_MOTION = "slow cinematic zoom in, subtle ambient motion"

# Ken Burns 변형 목록: (방향, 앵커)
_KB_VARIANTS = [
    ("in",  "center"),   # 중앙 줌인
    ("out", "center"),   # 중앙 줌아웃
    ("in",  "tl"),       # 좌상단 줌인
    ("in",  "br"),       # 우하단 줌인
]


def _ffmpeg_ken_burns(
    image_path: Path,
    output_path: Path,
    duration: int = 5,
    variant_idx: int = 0,
) -> bool:
    """PIL로 프레임별 float 정밀 crop → FFmpeg 파이프로 인코딩. zoompan 떨림 없음."""
    from PIL import Image

    fps = 25
    out_w, out_h = 1920, 1080
    direction, anchor = _KB_VARIANTS[variant_idx % len(_KB_VARIANTS)]

    img = Image.open(image_path).convert("RGB")
    # 줌 여유분 확보: 2배 업스케일
    sw, sh = img.width * 2, img.height * 2
    img = img.resize((sw, sh), Image.LANCZOS)

    zoom_start, zoom_end = (1.0, 1.5) if direction == "in" else (1.5, 1.0)
    n_frames = duration * fps

    cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{out_w}x{out_h}", "-pix_fmt", "rgb24", "-r", str(fps),
        "-i", "-",
        "-pix_fmt", "yuv420p", "-vcodec", "libx264", "-preset", "fast",
        str(output_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    try:
        for i in range(n_frames):
            t = i / max(n_frames - 1, 1)
            zoom = zoom_start + (zoom_end - zoom_start) * t
            cw, ch = sw / zoom, sh / zoom

            if anchor == "center":
                cx, cy = (sw - cw) / 2, (sh - ch) / 2
            elif anchor == "tl":
                cx, cy = 0.0, 0.0
            else:  # br
                cx, cy = sw - cw, sh - ch

            frame = img.crop((cx, cy, cx + cw, cy + ch)).resize((out_w, out_h), Image.LANCZOS)
            proc.stdin.write(frame.tobytes())
    finally:
        proc.stdin.close()

    proc.wait()
    return proc.returncode == 0


def generate_motion_clip(
    image_path: Path,
    output_path: Path,
    motion_prompt: str = DEFAULT_MOTION,
    duration_sec: int = 5,
    use_veo: bool = False,
    variant_idx: int = 0,
) -> bool:
    """
    단일 이미지로부터 모션 클립을 생성한다.
    use_veo=False(기본)이면 FFmpeg Ken Burns만 사용.

    Returns:
        True: 성공 / False: 실패
    """
    output_path = Path(output_path)
    if output_path.exists():
        print(f"   ✓ 클립 캐시: {output_path.name}")
        return True

    print(f"   🎬 Ken Burns 클립 생성: {image_path.name} ...")

    if use_veo:
        try:
            import time
            import sys
            from pathlib import Path as _P
            APP_ROOT = _P(__file__).resolve().parent.parent
            sys.path.insert(0, str(APP_ROOT))
            from config import GEMINI_API_KEY
            from google import genai
            from google.genai import types as gtypes

            _client = genai.Client(api_key=GEMINI_API_KEY)
            VEO_MODEL = "veo-3.1-lite-generate-preview"

            operation = _client.models.generate_videos(
                model=VEO_MODEL,
                prompt=motion_prompt,
                image=gtypes.Image(
                    image_bytes=image_path.read_bytes(),
                    mime_type="image/png",
                ),
                config=gtypes.GenerateVideosConfig(
                    aspect_ratio="16:9",
                    duration_seconds=min(duration_sec, 8),
                    number_of_videos=1,
                ),
            )
            while not operation.done:
                time.sleep(5)
                operation = _client.operations.get(operation)

            video_data = operation.response.generated_videos[0].video.video_bytes
            output_path.write_bytes(video_data)
            print(f"   ✓ Veo 클립 저장: {output_path.name}")
            return True
        except Exception as e:
            print(f"   ⚠ Veo 실패 ({e}) → Ken Burns 폴백")

    ok = _ffmpeg_ken_burns(image_path, output_path, duration=duration_sec, variant_idx=variant_idx)
    if ok:
        print(f"   ✓ 저장: {output_path.name}")
    return ok


def generate_all_clips(
    script: dict,
    images_dir: Path,
    clips_dir: Path,
    use_veo: bool = False,
) -> list[Path]:
    """
    모든 씬 이미지에 대해 모션 클립을 생성한다.
    use_veo=False(기본): FFmpeg Ken Burns만 사용 (무료)
    use_veo=True: Veo 3.1 Lite 사용 (유료, $0.05/초)
    """
    clips_dir = Path(clips_dir)
    clips_dir.mkdir(parents=True, exist_ok=True)
    scenes = script.get("scenes", [])
    results = []

    for i, scene in enumerate(scenes):
        n = scene["order"]
        image_path = Path(images_dir) / f"scene_{n:03d}.png"
        clip_path = clips_dir / f"scene_{n:03d}.mp4"
        motion_hint = scene.get("motion_hint", DEFAULT_MOTION)
        duration = min(int(scene.get("duration_sec", 20)), 60)

        if not image_path.exists():
            print(f"   ✗ 이미지 없음, 건너뜀: {image_path.name}")
            results.append(None)
            continue

        ok = generate_motion_clip(
            image_path, clip_path, motion_hint,
            duration_sec=min(duration, 8),
            use_veo=use_veo,
            variant_idx=i,
        )
        results.append(clip_path if ok else None)

    return results
