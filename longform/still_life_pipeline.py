"""
Project Still-Life AI — 롱폼 자동화 파이프라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

사용법:
  # 전체 실행 (채널 분석 + 대본 + 이미지 + 모션 + TTS + 합성)
  python longform/still_life_pipeline.py run \
    --topic "인간이 AI를 두려워하는 진짜 이유" \
    --ref-url "https://youtube.com/watch?v=EXAMPLE"

  # 단계별 실행
  python longform/still_life_pipeline.py scan   --url URL
  python longform/still_life_pipeline.py draft  --topic TOPIC --style-cache PATH
  python longform/still_life_pipeline.py images --script PATH
  python longform/still_life_pipeline.py motion --project PATH
  python longform/still_life_pipeline.py tts    --project PATH --voice Charon
  python longform/still_life_pipeline.py stitch --project PATH
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))

from longform.channel_analyzer import analyze_channel
from longform.script_drafter import draft_script
from longform.image_generator import generate_scene_images, generate_character_sheet
from longform.motion_generator import generate_all_clips
from longform.video_stitcher import stitch
from longform.topic_generator import generate_topics, print_topics

OUTPUT_ROOT = APP_ROOT / "output" / "longform"


def _project_dir(project_id: str) -> Path:
    d = OUTPUT_ROOT / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ─── 각 단계 함수 ─────────────────────────────────────────────────────────────

def cmd_scan(args):
    print("\n[STEP 1] 채널 스타일 분석...")
    style = analyze_channel(args.url, force_refresh=getattr(args, "force", False))
    print(f"   어조: {style.get('tone')}")
    print(f"   비주얼: {style.get('visual_style_prompt', '')[:80]}...")
    virality = style.get("virality_formula") or {}
    if virality:
        print(f"   감정 트리거: {', '.join(virality.get('emotional_triggers', []))}")
        print(f"   Hook 공식: {', '.join(virality.get('hook_formulas', []))}")
    return style


def cmd_topics(args):
    print("\n[STEP 1.5] 주제 후보 생성...")
    style_path = Path(args.style_cache)
    style = json.loads(style_path.read_text(encoding="utf-8"))
    topics = generate_topics(
        style=style,
        count=getattr(args, "count", 10),
        extra_context=getattr(args, "context", "") or "",
    )
    print_topics(topics)

    # JSON으로 저장 (선택)
    if getattr(args, "output", None):
        out = Path(args.output)
        out.write_text(json.dumps(topics, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ✓ 주제 목록 저장: {out}")

    return topics


def cmd_draft(args):
    print("\n[STEP 2] 대본 작성...")
    style_path = Path(args.style_cache)
    style = json.loads(style_path.read_text(encoding="utf-8"))
    script = draft_script(
        topic=args.topic,
        style=style,
        target_duration=getattr(args, "duration", 600),
    )

    project_id = f"still_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    proj = _project_dir(project_id)
    script_path = proj / "script.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   ✓ 저장: {script_path}")
    return script, str(proj)


def cmd_images(args):
    print("\n[STEP 3] 이미지 생성...")
    script_path = Path(args.script)
    script = json.loads(script_path.read_text(encoding="utf-8"))
    proj = script_path.parent
    images_dir = proj / "images"

    # 채널 스타일 로드 (캐릭터 레퍼런스 생성용)
    style = {}
    style_cache = getattr(args, "style_cache", None)
    if style_cache and Path(style_cache).exists():
        style = json.loads(Path(style_cache).read_text(encoding="utf-8"))

    # 캐릭터 레퍼런스 시트 생성 (1회, 이후 캐시 사용)
    force_char = getattr(args, "force_char", False)
    char_ref = generate_character_sheet(style, images_dir, force=force_char)

    # --scenes 옵션으로 특정 씬만 생성 가능
    scenes_filter = getattr(args, "scenes", None)
    if scenes_filter:
        filtered = [s for s in script.get("scenes", []) if s["order"] in scenes_filter]
        script = {**script, "scenes": filtered}
        print(f"   → 씬 {scenes_filter} 만 생성")

    paths = generate_scene_images(script, images_dir, style_prefix="", char_ref_path=char_ref)
    ok = sum(1 for p in paths if p)
    print(f"   ✓ {ok}/{len(paths)} 씬 이미지 생성 완료")
    return paths


def cmd_motion(args):
    print("\n[STEP 4] 모션 클립 생성...")
    proj = Path(args.project)
    script = json.loads((proj / "script.json").read_text(encoding="utf-8"))

    scenes_filter = getattr(args, "scenes", None)
    if scenes_filter:
        filtered = [s for s in script.get("scenes", []) if s["order"] in scenes_filter]
        script = {**script, "scenes": filtered}
        print(f"   → 씬 {scenes_filter} 만 생성")

    use_veo = getattr(args, "use_veo", False)
    clips = generate_all_clips(script, proj / "images", proj / "clips", use_veo=use_veo)
    ok = sum(1 for c in clips if c)
    print(f"   ✓ {ok}/{len(clips)} 클립 생성 완료")
    return clips


def _list_voices() -> list[dict]:
    voices_file = APP_ROOT / "resources" / "gemini_voices.json"
    if voices_file.exists():
        import json as _json
        return _json.loads(voices_file.read_text(encoding="utf-8"))
    return [
        {"name": "Charon",  "description_ko": "남성/신뢰감 있는 중저음"},
        {"name": "Kore",    "description_ko": "여성/차분하고 지적인 톤"},
        {"name": "Aoede",   "description_ko": "여성/밝고 부드러운 톤"},
        {"name": "Fenrir",  "description_ko": "남성/활기차고 경쾌한 톤"},
        {"name": "Puck",    "description_ko": "중성/느긋하고 편안한 톤"},
    ]


def _extract_tts_timing(mp3_path: Path, tts_scenes: list[dict]) -> dict:
    """
    Whisper STT로 full_narration.mp3를 전사해 씬별 실측 구간을 추출한다.
    core_logic.generate_single_tts와 동일한 글자 수 매핑 알고리즘 사용.
    반환: {scene_order: duration_sec}
    """
    import re as _re2
    import whisper as _whisper

    def _clean(t: str) -> str:
        return _re2.sub(r"[^가-힣a-zA-Z0-9]", "", t)

    print("   🧠 Whisper 전사 중 (base 모델)...")
    model = _whisper.load_model("base")
    result = model.transcribe(str(mp3_path), language="ko")
    ws_segs = result.get("segments", [])
    if not ws_segs:
        print("   ⚠ Whisper 세그먼트 없음")
        return {}

    timing: dict[int, float] = {}
    curr_idx = 0
    total_ws = len(ws_segs)
    prev_end = 0.0

    for i, scene in enumerate(tts_scenes):
        order = scene["order"]
        text = scene.get("narration_kr", scene.get("subtitle_kr", "")).strip()
        target_len = len(_clean(text))
        if not target_len:
            timing[order] = 0.0
            continue

        cur_len = 0
        merged = []
        while curr_idx < total_ws:
            seg_len = len(_clean(ws_segs[curr_idx]["text"]))
            # 마지막 씬이면 남은 세그먼트 전부
            if i == len(tts_scenes) - 1:
                merged.append(ws_segs[curr_idx])
                cur_len += seg_len
                curr_idx += 1
                continue
            if cur_len > 0 and abs(target_len - cur_len) < abs(target_len - (cur_len + seg_len)):
                break
            merged.append(ws_segs[curr_idx])
            cur_len += seg_len
            curr_idx += 1

        if merged:
            end_t = merged[-1]["end"]
            timing[order] = end_t - prev_end
            prev_end = end_t
        else:
            timing[order] = 0.0

    print(f"   ✓ Whisper 타이밍: {len(timing)}개 씬 | 총 {sum(timing.values()):.1f}초")
    return timing


def cmd_tts(args):
    print("\n[STEP 5] TTS 내레이션 생성 (단일 호출 — 목소리 일관성 유지)...")
    import subprocess as _sp2
    proj = Path(args.project)
    script = json.loads((proj / "script.json").read_text(encoding="utf-8"))
    tts_dir = proj / "tts"
    tts_dir.mkdir(exist_ok=True)

    voice = getattr(args, "voice", None)
    if not voice:
        voices = _list_voices()
        print("\n  사용 가능한 보이스:")
        for i, v in enumerate(voices):
            print(f"  [{i+1:2d}] {v['name']:<14} — {v.get('description_ko','')}")
        try:
            idx = int(input("\n  번호 선택 (기본 1): ").strip() or "1") - 1
            voice = voices[max(0, min(idx, len(voices)-1))]["name"]
        except (ValueError, EOFError):
            voice = voices[0]["name"]

    out_path = tts_dir / "full_narration.mp3"
    scenes = script.get("scenes", [])
    scene_orders = [s["order"] for s in scenes if s.get("narration_kr") or s.get("subtitle_kr")]

    if not out_path.exists():
        full_text = "\n\n".join(
            s.get("narration_kr", s.get("subtitle_kr", "")).strip()
            for s in scenes
            if s.get("narration_kr") or s.get("subtitle_kr")
        )
        if not full_text:
            print("   ✗ 나레이션 텍스트 없음")
            return
        print(f"   🎙 보이스: {voice} | 총 {len(scene_orders)}개 씬 단일 호출 중...")
        from video_engine import VideoProcessor
        ok = VideoProcessor()._generate_gemini_tts(full_text, str(out_path), voice_name=voice, playback_speed=1.0)
        if not ok:
            print("   ✗ TTS 생성 실패")
            return
        print("   ✓ 저장: full_narration.mp3")
    else:
        print("   ✓ TTS 캐시: full_narration.mp3")

    # Whisper로 씬별 타이밍 추출 → tts_timing.json
    timing_path = tts_dir / "tts_timing.json"
    tts_scenes = [s for s in scenes if s.get("narration_kr") or s.get("subtitle_kr")]
    if not timing_path.exists():
        print("   🔍 Whisper 씬 타이밍 추출 중...")
        timing = _extract_tts_timing(out_path, tts_scenes)
        if timing:
            timing_path.write_text(json.dumps(timing, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"   ✓ 타이밍 저장: {len(timing)}개 씬 | 총 {sum(timing.values()):.1f}초")
        else:
            print("   ⚠ 타이밍 추출 실패 (capcut 단계에서 문자 수 비율 사용)")
    else:
        print("   ✓ tts_timing.json 캐시")


def cmd_capcut(args):
    """모션 클립 + TTS + BGM을 CapCut 프로젝트로 내보내기."""
    import subprocess as _sp
    print("\n[STEP 6] CapCut 프로젝트 내보내기...")
    proj = Path(args.project)
    script = json.loads((proj / "script.json").read_text(encoding="utf-8"))
    clips_dir = proj / "clips"
    tts_dir = proj / "tts"

    # 클립 목록 + 실제 길이
    clip_durations = []
    for scene in script.get("scenes", []):
        n = scene["order"]
        clip = clips_dir / f"scene_{n:03d}.mp4"
        if not clip.exists():
            print(f"   ✗ 클립 없음: scene_{n:03d}.mp4 — 건너뜀")
            continue
        r = _sp.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(clip)],
            capture_output=True, text=True,
        )
        try:
            dur = float(r.stdout.strip())
        except ValueError:
            dur = float(scene.get("duration_sec", 8))
        clip_durations.append((clip, dur, scene))

    if not clip_durations:
        print("   ✗ 내보낼 클립이 없습니다.")
        return

    import re as _re

    def _narration_to_lines(text: str, max_len: int = 18) -> list[str]:
        """나레이션을 max_len자 이하 단위로 분리. 6자 미만 꼬리는 앞 줄에 합침."""
        def _chunk(s: str) -> list[str]:
            words, chunk, result = s.split(), "", []
            for w in words:
                candidate = (chunk + " " + w).strip() if chunk else w
                if len(candidate) > max_len and chunk:
                    result.append(chunk)
                    chunk = w
                else:
                    chunk = candidate
            if chunk:
                result.append(chunk)
            return result or [s[:max_len]]

        raw = []
        for part in _re.split(r'(?<=[.!?])\s+', text.strip()):
            part = part.strip()
            if part:
                raw.extend(_chunk(part))

        # 6자 미만 꼬리는 앞 줄에 합침
        lines: list[str] = []
        for line in raw:
            if lines and len(line) < 6:
                lines[-1] += " " + line
            else:
                lines.append(line)
        return lines or [text.strip()[:max_len]]

    # tts_timing.json 로드 — combined 빌드 전에 필요
    timing_path = tts_dir / "tts_timing.json"
    tts_timing: dict[int, float] = {}
    if timing_path.exists():
        raw = json.loads(timing_path.read_text(encoding="utf-8"))
        tts_timing = {int(k): v for k, v in raw.items()}
        print(f"   🎙 TTS 타이밍 로드: {len(tts_timing)}개 씬")
    else:
        print("   ⚠ tts_timing.json 없음 — 먼저 tts 단계를 실행하세요.")

    # TTS 길이로 Ken Burns 클립을 이미지에서 직접 재생성 → combined_tts.mp4
    images_dir = proj / "images"
    combined_path = proj / "combined_tts.mp4"
    if not combined_path.exists():
        print(f"   🔗 TTS 길이 맞춤 Ken Burns 클립 생성 중...")
        from longform.motion_generator import _ffmpeg_ken_burns
        looped_clips = []
        for i, (clip, clip_dur, scene) in enumerate(clip_durations):
            n = scene["order"]
            tts_dur = tts_timing.get(n, clip_dur)
            tts_clip = proj / f"tts_clip_{n:03d}.mp4"
            if not tts_clip.exists():
                img = images_dir / f"scene_{n:03d}.png"
                if img.exists():
                    _ffmpeg_ken_burns(img, tts_clip, duration=int(round(tts_dur)), variant_idx=i)
                else:
                    # 이미지 없으면 원본 클립을 루프
                    _sp.run([
                        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(clip),
                        "-t", str(tts_dur), "-c:v", "libx264", "-preset", "fast", "-an",
                        str(tts_clip),
                    ], capture_output=True)
            looped_clips.append(tts_clip)

        concat_list = proj / "concat_tts_list.txt"
        concat_list.write_text(
            "\n".join(f"file '{c.resolve()}'" for c in looped_clips),
            encoding="utf-8",
        )
        ret = _sp.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(concat_list), "-c", "copy", str(combined_path)],
            capture_output=True,
        )
        if ret.returncode != 0:
            print(f"   ✗ concat 실패: {ret.stderr.decode()}")
            return
        print(f"   ✓ concat 완료: {combined_path.name}")
    else:
        print(f"   ✓ concat 캐시: {combined_path.name}")

    # segments + subtitles: 모두 TTS 길이 기준 → 영상/오디오/자막 완전 동기화
    segments = []
    subtitles = []
    t = 0.0
    for clip, clip_dur, scene in clip_durations:
        n = scene["order"]
        seg_dur = tts_timing.get(n, clip_dur)
        segments.append({
            "start_time": t,
            "duration": seg_dur,
            "timeline_start": t,
        })
        narration = scene.get("narration_kr", "").strip()
        if narration:
            lines = _narration_to_lines(narration)
            line_dur = seg_dur / len(lines)
            for i, line in enumerate(lines):
                subtitles.append({
                    "text": line,
                    "start_us": int((t + i * line_dur) * 1_000_000),
                    "duration_us": int(line_dur * 1_000_000),
                })
        t += seg_dur

    # TTS / BGM
    tts_path = tts_dir / "full_narration.mp3"
    if not tts_path.exists():
        print("   ⚠ full_narration.mp3 없음 — 먼저 tts 단계를 실행하세요.")
        tts_path = None
    bgm_dir = APP_ROOT / "resources" / "bgm"
    bgm_file = next(bgm_dir.glob("*.mp3"), None) if bgm_dir.exists() else None
    if bgm_file:
        print(f"   🎵 BGM: {bgm_file.name}")

    from datetime import datetime as _dt
    project_name = f"still_{proj.name}_{_dt.now().strftime('%H%M%S')}"
    print(f"   📦 씬 {len(segments)}개 | 총 {t:.1f}초")

    from video_engine import VideoProcessor
    result = VideoProcessor().export_to_capcut(
        video_path=str(combined_path),
        segments=segments,
        project_name=project_name,
        title=None,          # 롱폼은 상단 제목 불필요
        tts_path=str(tts_path) if tts_path else None,
        bgm_path=str(bgm_file) if bgm_file else None,
        video_clips=None,
        subtitles=subtitles or None,
        zoom_factor=1.0,     # 롱폼은 확대 없이 원본 비율
    )
    if result:
        draft_info_path = Path(result) / "draft_info.json"
        if draft_info_path.exists():
            di = json.loads(draft_info_path.read_text(encoding="utf-8"))

            # 1) 16:9 캔버스
            di["canvas_config"] = {"background": None, "height": 1080, "ratio": "16:9", "width": 1920}

            # 2) sticker 트랙 제거 (위아래 검정 바 — 쇼츠 전용)
            di["tracks"] = [t for t in di["tracks"] if t.get("type") != "sticker"]

            # 3) 자막 트랙: 하단 위치 + 크기
            sub_mat_ids = set()
            for track in di["tracks"]:
                if track.get("type") == "text" and len(track.get("segments", [])) > 1:
                    for seg in track["segments"]:
                        sub_mat_ids.add(seg["material_id"])
                        seg["clip"]["scale"] = {"x": 0.5, "y": 0.5}
                        seg["clip"]["transform"]["y"] = -0.38

            draft_info_path.write_text(json.dumps(di, ensure_ascii=False, indent=2), encoding="utf-8")
            print("   ✓ 캔버스 16:9 적용 / 검정 바 제거 / 자막 하단 조정")
        print(f"   ✓ CapCut 프로젝트: {result}")
        import os as _os
        _os.system("open -a 'CapCut'")
    else:
        print("   ✗ CapCut 내보내기 실패")


def cmd_stitch(args):
    print("\n[STEP 6] 최종 합성...")
    proj = Path(args.project)
    script = json.loads((proj / "script.json").read_text(encoding="utf-8"))
    bgm = Path(APP_ROOT / "resources" / "bgm")
    bgm_file = next(bgm.glob("*.mp3"), None) if bgm.exists() else None

    ok = stitch(
        script=script,
        clips_dir=proj / "clips",
        tts_dir=proj / "tts",
        output_path=proj / "final.mp4",
        bgm_path=bgm_file,
    )
    return ok


def cmd_run(args):
    """채널 분석부터 최종 합성까지 전체 파이프라인 실행."""
    print("=" * 64)
    print("  Project Still-Life AI — 전체 파이프라인 시작")
    print(f"  주제: {args.topic}")
    print("=" * 64)

    # STEP 1: 채널 분석
    style = analyze_channel(args.ref_url)

    # STEP 2: 대본 작성
    target_dur = getattr(args, "duration", 600)
    script = draft_script(args.topic, style, target_duration=target_dur)

    project_id = f"still_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    proj = _project_dir(project_id)
    script_path = proj / "script.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n   📄 대본 저장: {script_path}")

    # 대본 컨펌 (선택)
    print("\n" + "!" * 64)
    print(f"  대본이 {script_path}에 저장되었습니다.")
    print("  수정 후 Enter를 누르면 계속 진행됩니다.")
    print("!" * 64)
    input("  >> Enter를 누르세요...")
    script = json.loads(script_path.read_text(encoding="utf-8"))

    # STEP 3: 이미지 생성
    generate_scene_images(script, proj / "images")

    # STEP 4: 모션 클립
    generate_all_clips(script, proj / "images", proj / "clips")

    # STEP 5: TTS
    from video_engine import VideoProcessor
    p = VideoProcessor()
    tts_dir = proj / "tts"
    tts_dir.mkdir(exist_ok=True)
    voice = getattr(args, "voice", "Charon")
    for scene in script.get("scenes", []):
        n = scene["order"]
        text = scene.get("narration_kr", "")
        out = tts_dir / f"scene_{n:03d}.mp3"
        if not out.exists() and text:
            p._generate_gemini_tts(text, str(out), voice_name=voice, playback_speed=1.0)

    # STEP 6: 합성
    bgm = Path(APP_ROOT / "resources" / "bgm")
    bgm_file = next(bgm.glob("*.mp3"), None) if bgm.exists() else None
    stitch(script, proj / "clips", tts_dir, proj / "final.mp4", bgm_path=bgm_file)

    print("\n" + "=" * 64)
    print(f"  ✅ 완료! 출력: {proj / 'final.mp4'}")
    print("=" * 64)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Project Still-Life AI 파이프라인")
    sub = parser.add_subparsers(dest="command")

    # scan
    p_scan = sub.add_parser("scan", help="채널 스타일 분석")
    p_scan.add_argument("--url", required=True)
    p_scan.add_argument("--force", action="store_true", help="캐시 무시 재분석")

    # topics
    p_topics = sub.add_parser("topics", help="바이럴 주제 후보 생성")
    p_topics.add_argument("--style-cache", required=True, dest="style_cache",
                          help="scan 단계에서 생성된 _style.json 경로")
    p_topics.add_argument("--count", type=int, default=10, help="생성할 주제 수 (기본 10)")
    p_topics.add_argument("--context", default="", help="방향 힌트 (예: '연애/관계 주제 위주로')")
    p_topics.add_argument("--output", default="", help="JSON 저장 경로 (선택)")

    # draft
    p_draft = sub.add_parser("draft", help="대본 작성")
    p_draft.add_argument("--topic", required=True)
    p_draft.add_argument("--style-cache", required=True, dest="style_cache")
    p_draft.add_argument("--duration", type=int, default=600)

    # images
    p_img = sub.add_parser("images", help="씬별 이미지 생성")
    p_img.add_argument("--script", required=True)
    p_img.add_argument("--scenes", type=int, nargs="+", metavar="N", help="생성할 씬 번호 (예: --scenes 1 2 3)")
    p_img.add_argument("--style-cache", dest="style_cache", default="", help="채널 스타일 JSON 경로 (캐릭터 레퍼런스용)")
    p_img.add_argument("--force-char", dest="force_char", action="store_true", help="캐릭터 레퍼런스 재생성")

    # motion
    p_mot = sub.add_parser("motion", help="이미지 → 모션 클립")
    p_mot.add_argument("--project", required=True)
    p_mot.add_argument("--scenes", type=int, nargs="+", metavar="N", help="생성할 씬 번호 (예: --scenes 1 2)")
    p_mot.add_argument("--use-veo", dest="use_veo", action="store_true",
                       help="Veo 3.1 Lite 사용 ($0.05/초, 기본은 무료 Ken Burns)")

    # tts
    p_tts = sub.add_parser("tts", help="TTS 내레이션 생성 (전체 1회 호출, 목소리 일관성)")
    p_tts.add_argument("--project", required=True)
    p_tts.add_argument("--voice", default="", help="보이스 이름 (생략 시 목록에서 선택)")

    # capcut
    p_capcut = sub.add_parser("capcut", help="클립 + TTS + BGM → CapCut 프로젝트 내보내기")
    p_capcut.add_argument("--project", required=True)

    # stitch
    p_stitch = sub.add_parser("stitch", help="최종 합성 (mp4 직접 합성)")
    p_stitch.add_argument("--project", required=True)

    # run (풀 파이프라인)
    p_run = sub.add_parser("run", help="전체 파이프라인 실행")
    p_run.add_argument("--topic", required=True)
    p_run.add_argument("--ref-url", required=True, dest="ref_url")
    p_run.add_argument("--voice", default="Charon")
    p_run.add_argument("--duration", type=int, default=600, help="목표 영상 길이(초)")

    args = parser.parse_args()

    dispatch = {
        "scan":   cmd_scan,
        "topics": cmd_topics,
        "draft":  cmd_draft,
        "images": cmd_images,
        "motion": cmd_motion,
        "tts":    cmd_tts,
        "capcut": cmd_capcut,
        "stitch": cmd_stitch,
        "run":    cmd_run,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
