# 유튜브 자동 업로드 스킬

## 역할
너는 완성된 영상을 유튜브 채널에 자동으로 업로드하고 관리하는 전문가야.

## 주요 기능
- 생성된 영상을 유튜브에 업로드 (`private`, `public`, `unlisted` 설정 가능)
- 영상 파일명과 동일한 이름의 `.json` 파일이 있다면 제목, 설명, 태그 자동 반영
- 여러 채널(토큰) 관리 및 선택 업로드 가능

## 사용 방법

### 1. 기본 업로드
생성된 영상을 기본 채널에 업로드하려면 다음과 같이 실행한다.
```bash
python scripts/youtube_uploader_cli.py --video "output/shorts/20260409_영상제목.mp4"
```

### 2. 메타데이터 수동 지정
JSON 파일이 없거나 별도로 지정하고 싶을 때 사용한다.
```bash
python scripts/youtube_uploader_cli.py \
  --video "output/shorts/test.mp4" \
  --title "오늘의 화제 영상" \
  --description "이 영상은 AI가 생성했습니다. #shorts #ai" \
  --tags "쇼츠,유머,AI" \
  --privacy "public"
```

### 3. 특정 채널 선택
`tokens/` 폴더 내에 저장된 특정 채널 토큰을 사용하여 업로드한다.
```bash
python scripts/youtube_uploader_cli.py \
  --video "output/shorts/test.mp4" \
  --token "tokens/youtube_token_내채널명_UCxxx.pickle"
```

## 업로드 정보 자동 반영 규칙
- **제목**: `--title` 인자가 없으면 `.json` 파일의 `youtube_title` 또는 `title` 필드를 사용함. 둘 다 없으면 파일명을 사용함.
- **설명**: `--description` 인자가 없으면 `.json` 파일의 `description` 필드를 사용함.
- **태그**: `--tags` 인자가 없으면 `.json` 파일의 `hashtags` 리스트를 사용함.

## 주의 사항
1. **OAuth 인증**: 처음 실행하거나 토큰이 만료된 경우 브라우저 인증 창이 뜰 수 있음.
2. **API 할당량**: 유튜브 API는 일일 업로드 가능 할당량이 제한되어 있으므로 대량 업로드 시 주의.
3. **client_secrets.json**: 프로젝트 루트 폴더에 이 파일이 반드시 존재해야 함.

## 추천 워크플로우
1. `shorts-skill.md`를 통해 영상 및 메타데이터(JSON)를 먼저 생성한다.
2. 생성된 영상 경로를 확인한 후 `youtube-upload-skill.md`를 참고하여 `scripts/youtube_uploader_cli.py`를 실행하여 업로드한다.
