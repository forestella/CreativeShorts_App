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
from longform.image_generator import generate_scene_images
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
    style_prefix = ""
    # style prefix는 첫 씬의 image_prompt_en에서 공통 부분을 자동 추출하거나 직접 지정
    paths = generate_scene_images(script, images_dir, style_prefix=style_prefix)
    ok = sum(1 for p in paths if p)
    print(f"   ✓ {ok}/{len(paths)} 씬 이미지 생성 완료")
    return paths


def cmd_motion(args):
    print("\n[STEP 4] 모션 클립 생성...")
    proj = Path(args.project)
    script = json.loads((proj / "script.json").read_text(encoding="utf-8"))
    clips = generate_all_clips(script, proj / "images", proj / "clips")
    ok = sum(1 for c in clips if c)
    print(f"   ✓ {ok}/{len(clips)} 클립 생성 완료")
    return clips


def cmd_tts(args):
    print("\n[STEP 5] TTS 내레이션 생성...")
    proj = Path(args.project)
    script = json.loads((proj / "script.json").read_text(encoding="utf-8"))
    tts_dir = proj / "tts"
    tts_dir.mkdir(exist_ok=True)

    # 기존 video_engine의 TTS 함수 재활용
    from video_engine import VideoProcessor
    p = VideoProcessor()

    for scene in script.get("scenes", []):
        n = scene["order"]
        text = scene.get("narration_kr", scene.get("subtitle_kr", ""))
        if not text:
            continue
        out = tts_dir / f"scene_{n:03d}.mp3"
        if out.exists():
            print(f"   ✓ TTS 캐시: scene_{n:03d}.mp3")
            continue
        voice = getattr(args, "voice", "Charon")
        ok = p._generate_gemini_tts(text, str(out), voice_name=voice, playback_speed=1.0)
        if ok:
            print(f"   ✓ TTS: scene_{n:03d}.mp3")
        else:
            print(f"   ✗ TTS 실패: scene {n}")


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

    # motion
    p_mot = sub.add_parser("motion", help="이미지 → 모션 클립")
    p_mot.add_argument("--project", required=True)

    # tts
    p_tts = sub.add_parser("tts", help="TTS 내레이션 생성")
    p_tts.add_argument("--project", required=True)
    p_tts.add_argument("--voice", default="Charon")

    # stitch
    p_stitch = sub.add_parser("stitch", help="최종 합성")
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
        "stitch": cmd_stitch,
        "run":    cmd_run,
    }

    if args.command not in dispatch:
        parser.print_help()
        sys.exit(1)

    dispatch[args.command](args)


if __name__ == "__main__":
    main()
