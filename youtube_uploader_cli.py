import sys
import os
import argparse
import logging
import json
import glob

# 프로젝트 루트를 sys.path에 추가
project_root = os.path.dirname(os.path.abspath(__file__))
if project_root not in sys.path:
    sys.path.append(project_root)

# src 폴더를 패스에 추가하여 import 가능하게 함
src_dir = os.path.join(project_root, "src")
if src_dir not in sys.path:
    sys.path.append(src_dir)

try:
    from utils.youtube_uploader import YouTubeUploader
except ImportError:
    # 만약 위 방식이 실패하면 직접 경로로 시도
    sys.path.append(os.path.join(project_root, "src", "utils"))
    from youtube_uploader import YouTubeUploader

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

def load_metadata_from_json(video_path):
    """
    영상 파일과 동일한 이름의 .json 파일이 있으면 메타데이터를 로드합니다.
    """
    json_path = os.path.splitext(video_path)[0] + ".json"
    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 메타데이터 JSON 로드 실패: {e}")
    return None

def get_available_tokens():
    """
    tokens/ 폴더 내의 가용한 채널 토큰 목록을 반환합니다.
    """
    tokens_dir = os.path.join(project_root, "tokens")
    if not os.path.exists(tokens_dir):
        os.makedirs(tokens_dir, exist_ok=True)
        return []
    
    return [f for f in os.listdir(tokens_dir) if f.startswith("youtube_token_") and f.endswith(".pickle")]

def main():
    parser = argparse.ArgumentParser(description="YouTube Video Uploader CLI")
    parser.add_argument("--video", help="Path to the video file (.mov or .mp4)")
    parser.add_argument("--title", help="Video title")
    parser.add_argument("--description", help="Video description")
    parser.add_argument("--tags", help="Comma separated tags")
    parser.add_argument("--privacy", default="public", choices=["public", "private", "unlisted"], help="Privacy status")
    parser.add_argument("--category", default="22", help="Category ID (22: People & Blogs)")
    parser.add_argument("--token", help="Specific token path to use")
    parser.add_argument("--client_secrets", default="client_secrets.json", help="Path to client_secrets.json")
    parser.add_argument("--scan", action="store_true", help="Scan for exported .mov files in project root")

    args = parser.parse_args()
    setup_logging()

    # --scan 옵션 처리: 프로젝트 루트에서 .mov 파일을 찾음
    if args.scan:
        mov_files = glob.glob(os.path.join(project_root, "*.mov"))
        if not mov_files:
            print("🔍 프로젝트 루트에서 .mov 파일을 찾을 수 없습니다.")
            sys.exit(0)
        
        print(f"🎬 발견된 영상 파일들: {mov_files}")
        # 가장 최근 파일 선택 (또는 사용자에게 물어보기 - CLI니까 첫 번째 것 우선 처리)
        args.video = sorted(mov_files, key=os.path.getmtime, reverse=True)[0]
        print(f"🚀 최신 파일 자동 선택: {os.path.basename(args.video)}")

    if not args.video:
        print("❌ 업로드할 영상 파일이 필요합니다. --video 또는 --scan 옵션을 사용하세요.")
        sys.exit(1)

    if not os.path.exists(args.video):
        print(f"❌ 파일을 찾을 수 없습니다: {args.video}")
        sys.exit(1)

    # 토큰 선택 로직
    tokens = get_available_tokens()
    token_path = args.token
    
    if not token_path:
        if not tokens:
            token_path = os.path.join(project_root, "tokens", "youtube_token_default.pickle")
            print(f"💡 가용한 토큰이 없어 기본 경로를 사용합니다: {token_path}")
        elif len(tokens) == 1:
            token_path = os.path.join(project_root, "tokens", tokens[0])
            print(f"💡 자동 선택된 채널: {tokens[0]}")
        else:
            print("\n📺 여러 채널 토큰이 발견되었습니다. 사용할 채널을 선택하세요:")
            for i, t in enumerate(tokens):
                print(f"  [{i+1}] {t}")
            
            try:
                choice = int(input(f"번호 선택 (1-{len(tokens)}): ")) - 1
                if 0 <= choice < len(tokens):
                    token_path = os.path.join(project_root, "tokens", tokens[choice])
                else:
                    print("❌ 잘못된 선택입니다. 첫 번째 토큰을 사용합니다.")
                    token_path = os.path.join(project_root, "tokens", tokens[0])
            except (ValueError, KeyboardInterrupt):
                token_path = os.path.join(project_root, "tokens", tokens[0])

    # 메타데이터 자동 로드 시도
    meta = load_metadata_from_json(args.video)
    
    title = args.title
    if not title and meta:
        title = meta.get('youtube_title') or meta.get('title')
    if not title:
        title = os.path.splitext(os.path.basename(args.video))[0]

    description = args.description
    if not description and meta:
        description = meta.get('description')
    if not description:
        description = title + " #shorts"

    tags = args.tags.split(",") if args.tags else []
    if not tags and meta:
        tags = meta.get('hashtags', [])
    if not tags:
        tags = ["shorts", "AI"]

    try:
        uploader = YouTubeUploader(
            client_secrets_file=args.client_secrets,
            token_path=token_path
        )
        
        print(f"📺 채널 인증 성공: {uploader.channel_title}")
        
        video_id = uploader.upload_video(
            file_path=args.video,
            title=title,
            description=description,
            tags=tags,
            privacy_status=args.privacy,
            category_id=args.category
        )
        
        print(f"\n✅ 드디어 업로드 완료!")
        print(f"🔗 채널: {uploader.channel_title}")
        print(f"🔗 URL: https://youtu.be/{video_id}")
        
    except Exception as e:
        print(f"❌ 업로드 중 오류 발생: {e}")
        # sys.exit(1)

if __name__ == "__main__":
    main()
