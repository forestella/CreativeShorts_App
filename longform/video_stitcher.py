"""
STEP 6 — Video Stitcher
TTS 오디오 + 모션 클립 + BGM을 FFmpeg로 합성하여 최종 롱폼 영상을 출력한다.
"""
import json
import subprocess
from pathlib import Path


def _write_concat_list(paths: list[Path], list_file: Path) -> None:
    lines = [f"file '{p.resolve()}'\n" for p in paths if p and p.exists()]
    list_file.write_text("".join(lines), encoding="utf-8")


def stitch(
    script: dict,
    clips_dir: Path,
    tts_dir: Path,
    output_path: Path,
    bgm_path: Path | None = None,
    bgm_volume: float = 0.12,
) -> bool:
    """
    최종 영상 합성.

    Args:
        script:       draft_script() 반환 dict
        clips_dir:    모션 클립(.mp4) 폴더
        tts_dir:      TTS 오디오(.mp3) 폴더
        output_path:  출력 파일 경로
        bgm_path:     BGM 파일 (없으면 스킵)
        bgm_volume:   BGM 볼륨 비율 (0.0 ~ 1.0)

    Returns:
        True: 성공
    """
    clips_dir = Path(clips_dir)
    tts_dir = Path(tts_dir)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scenes = script.get("scenes", [])

    # ── 1. 비디오 클립 concat ─────────────────────────────────────────
    clip_paths = [clips_dir / f"scene_{s['order']:03d}.mp4" for s in scenes]
    valid_clips = [p for p in clip_paths if p.exists()]
    if not valid_clips:
        print("   ✗ 합성 가능한 클립 없음")
        return False

    concat_list = output_path.parent / "concat_clips.txt"
    _write_concat_list(valid_clips, concat_list)

    merged_video = output_path.parent / "_merged_video.mp4"
    subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-an", str(merged_video),
    ], check=True, capture_output=True)

    # ── 2. TTS 오디오 concat ─────────────────────────────────────────
    tts_paths = sorted(tts_dir.glob("scene_*.mp3"))
    if tts_paths:
        tts_list = output_path.parent / "concat_tts.txt"
        _write_concat_list(tts_paths, tts_list)
        merged_audio = output_path.parent / "_merged_tts.mp3"
        subprocess.run([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(tts_list),
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(merged_audio),
        ], check=True, capture_output=True)
    else:
        merged_audio = None

    # ── 3. 최종 믹싱 (비디오 + TTS + BGM) ───────────────────────────
    if merged_audio and bgm_path and Path(bgm_path).exists():
        filter_complex = (
            f"[1:a]volume=1.0[tts];"
            f"[2:a]volume={bgm_volume},aloop=loop=-1:size=2e+09[bgm];"
            f"[tts][bgm]amix=inputs=2:duration=first[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(merged_video),
            "-i", str(merged_audio),
            "-i", str(bgm_path),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
    elif merged_audio:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(merged_video),
            "-i", str(merged_audio),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(merged_video),
            "-c:v", "copy",
            str(output_path),
        ]

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        print(f"   ✗ FFmpeg 합성 오류:\n{result.stderr.decode()}")
        return False

    print(f"   ✅ 최종 영상 생성 완료: {output_path}")
    return True
