# Project Still-Life AI — 롱폼 자동화 스킬

## 역할
Gemini 2.5 Pro의 멀티모달 채널 분석 → Imagen 이미지 생성 → Veo 모션 레이어링 → TTS 내레이션
파이프라인을 통해 '정지 이미지 + 미세 움직임(Cinemagraph)' 방식의 롱폼 영상을 자동 생성한다.

---

## 사전 요구사항

```bash
pip install google-genai pillow yt-dlp imageio-ffmpeg pydub
```

`.env` 필수 키:
```
GEMINI_API_KEY=...
# 이미지 생성용 (아래 중 택1)
GOOGLE_CLOUD_PROJECT=...   # Imagen 3 사용 시
# STABILITY_API_KEY=...    # Stability AI 사용 시 (대안)
```

---

## 파이프라인 6단계

### STEP 1 — Channel Scan (채널 스타일 해체)
```bash
python longform/still_life_pipeline.py scan \
  --url "https://youtube.com/watch?v=VIDEO_ID" \
  --cache-dir cache/still_life
```
- Gemini 2.5 Pro 멀티모달로 영상 직접 분석 (YouTube Data API 미사용)
- 출력: `cache/still_life/{channel_id}_style.json`
  - `tone`: 어조 분석 (냉소적/지적/친근 등)
  - `visual_style`: 이미지 스타일 프롬프트 베이스
  - `script_structure`: 도입-전개-결론 패턴
  - `scene_types`: 씬 유형 목록 (해설/비교/예시 등)

### STEP 2 — Creative Draft (대본 작성)
```bash
python longform/still_life_pipeline.py draft \
  --topic "인간이 AI를 두려워하는 진짜 이유" \
  --style-cache cache/still_life/{channel_id}_style.json \
  --length 1500
```
- 채널 스타일 JSON을 시스템 프롬프트에 주입
- 씬 단위 구조로 대본 생성 (각 씬: 텍스트 + 이미지 프롬프트 + 키워드)
- 출력: `output/longform/{project_id}/script.json`

### STEP 3 — Batch Image Gen (씬별 이미지 생성)
```bash
python longform/still_life_pipeline.py images \
  --script output/longform/{project_id}/script.json
```
- **기본 모델**: `imagen-3.0-generate-002` (Google Vertex AI)
- **대안**: Stability AI SDXL API
- 씬별 동일 스타일 유지를 위해 `style_prompt`를 모든 씬에 접두사로 고정
- 출력: `output/longform/{project_id}/images/scene_{N:03d}.png`

### STEP 4 — Motion Layering (이미지 → 루프 영상)
```bash
python longform/still_life_pipeline.py motion \
  --project output/longform/{project_id}
```
- 모델: `veo-3.0-generate-preview` (현재 Google AI Studio API)
- 씬당 3~5초 Ken Burns / 미세 흔들림 효과
- 모션 프롬프트: `"slow zoom in, subtle ambient motion, cinematic, 4 seconds"`
- 출력: `output/longform/{project_id}/clips/scene_{N:03d}.mp4`

### STEP 5 — TTS 내레이션
```bash
python longform/still_life_pipeline.py tts \
  --project output/longform/{project_id} \
  --voice Charon
```
- 모델: `gemini-2.5-flash-preview-tts`
- 씬별 스크립트를 개별 클립으로 생성 후 연결
- 기존 `video_engine.py`의 `_generate_gemini_tts()` 재활용

### STEP 6 — Final Stitch (최종 합성)
```bash
python longform/still_life_pipeline.py stitch \
  --project output/longform/{project_id} \
  --bgm resources/bgm/ambient.mp3
```
- FFmpeg로 영상 클립 + TTS 오디오 + BGM 믹싱
- 자막(.srt) 소프트 서브 삽입
- 출력: `output/longform/{project_id}/final.mp4`

---

## 단일 명령어 전체 실행 (풀 파이프라인)
```bash
python longform/still_life_pipeline.py run \
  --topic "인간이 AI를 두려워하는 진짜 이유" \
  --ref-url "https://youtube.com/watch?v=TSOL_VIDEO" \
  --voice Charon \
  --voice-speed 1.0
```

---

## 디렉토리 구조
```
CreativeShorts_App/
├── longform/
│   ├── still_life_pipeline.py   ← 메인 파이프라인 (CLI)
│   ├── channel_analyzer.py      ← STEP 1: 채널 스타일 분석
│   ├── script_drafter.py        ← STEP 2: 대본 작성
│   ├── image_generator.py       ← STEP 3: Imagen / Stability AI
│   ├── motion_generator.py      ← STEP 4: Veo 모션
│   └── video_stitcher.py        ← STEP 6: FFmpeg 합성
├── cache/
│   └── still_life/              ← 채널 스타일 캐시 (재분석 방지)
└── output/
    └── longform/
        └── {project_id}/
            ├── script.json
            ├── images/
            ├── clips/
            └── final.mp4
```

---

## 모델 선택 가이드 (비용 최적화)

| 단계 | 모델 | 비용 수준 | 비고 |
|------|------|-----------|------|
| 채널 분석 | `gemini-2.5-pro` | 높음 (1회) | 캐싱으로 반복 호출 차단 |
| 대본 작성 | `gemini-2.5-pro` | 중간 | 1500자 기준 ~₩50 |
| 이미지 생성 | `imagen-3.0-generate-002` | 씬당 비용 | 씬 수 최소화 권장 |
| 모션 | `veo-3.0-generate-preview` | 가장 높음 | 4초 클립 기준 |
| TTS | `gemini-2.5-flash-preview-tts` | 낮음 | Flash 사용으로 절감 |

---

## ⚠️ 주의 사항

1. **Nano Banana 2**: 공개 API 없음. PRD의 스타일 고정 목표는 Imagen 3의 `style prompt prefix` 고정 방식으로 대체.
2. **Veo API 할당량**: 현재 제한적 — 테스트 시 씬 수를 5개 이하로 제한.
3. **채널 캐시 필수**: `cache/still_life/{id}_style.json` 이 없으면 STEP 1부터 실행.
4. **YouTube 직접 다운로드**: 기존 `ensure_downloaded()` 함수 재활용 (IP 차단 방지 딜레이 적용됨).
