import os
import pickle
import logging
import subprocess
import webbrowser
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

# [참고] YouTube Data API v3 Scopes
# https://www.googleapis.com/auth/youtube.upload : 영상 업로드 권한
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.readonly',
    'https://www.googleapis.com/auth/youtube.force-ssl'  # 댓글 작성에 필요
]

class YouTubeUploader:
    def __init__(self, client_secrets_file='client_secrets.json', token_path='youtube_token.pickle'):
        """
        YouTube Data API OAuth2 인증을 처리하고 업로더를 초기화합니다.
        
        Args:
            client_secrets_file (str): Google Cloud Console에서 다운로드한 클라이언트 시크릿 JSON 파일 경로
            token_path (str): 인증된 토큰을 저장할 경로 (여러 채널 관리 시 각기 다른 경로 사용)
        """
        self.client_secrets_file = client_secrets_file
        self.token_path = token_path
        self.youtube, self.channel_title, self.channel_id = self._authenticate()

    def _has_required_scopes(self, creds):
        """토큰이 필요한 모든 스코프를 포함하는지 확인합니다."""
        if not creds or not hasattr(creds, 'scopes') or not creds.scopes:
            return False
        required = set(SCOPES)
        granted = set(creds.scopes)
        missing = required - granted
        if missing:
            logger.warning(f"⚠️ 토큰에 누락된 스코프: {missing}")
            return False
        return True

    def _authenticate(self):
        creds = None
        # 기존 토큰 정보 로드
        if os.path.exists(self.token_path):
            with open(self.token_path, 'rb') as token:
                creds = pickle.load(token)

        # 스코프가 부족한 경우 토큰 삭제 후 재인증
        if creds and not self._has_required_scopes(creds):
            logger.info(f"🔄 스코프 부족으로 토큰 재발급 필요 ({self.token_path}) - 기존 토큰 삭제 후 재인증...")
            os.remove(self.token_path)
            creds = None

        # 토큰이 없거나 만료된 경우 재인증
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logger.info(f"🔑 YouTube 토큰 만료됨 ({self.token_path}) - 갱신 중...")
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.error(f"토큰 갱신 실패: {e}. 재인증이 필요합니다.")
                    creds = self._get_new_creds()
            else:
                creds = self._get_new_creds()
            
            # 다음을 위해 토큰 저장
            os.makedirs(os.path.dirname(os.path.abspath(self.token_path)), exist_ok=True)
            with open(self.token_path, 'wb') as token:
                pickle.dump(creds, token)
        
        service = build('youtube', 'v3', credentials=creds)
        
        # 실제 채널 정보 가져오기 (사용자 확인 및 고유 식별용)
        try:
            channel_resp = service.channels().list(part='snippet,id', mine=True).execute()
            if not channel_resp.get('items'):
                raise Exception("인증된 계정에 유튜브 채널이 없습니다.")
            channel_info = channel_resp['items'][0]
            channel_title = channel_info['snippet']['title']
            channel_id = channel_info['id']
        except Exception as e:
            logger.error(f"채널 정보 획득 실패: {e}")
            channel_title = "알 수 없는 채널"
            channel_id = "unknown"
            
        return service, channel_title, channel_id

    def _get_new_creds(self):
        if not os.path.exists(self.client_secrets_file):
            raise FileNotFoundError(f"YouTube OAuth client_secrets.json 파일을 찾을 수 없습니다. (경로: {self.client_secrets_file})")

        logger.info("🔐 YouTube OAuth 브라우저 인증 시작...")
        flow = InstalledAppFlow.from_client_secrets_file(self.client_secrets_file, SCOPES)

        # run_local_server가 생성한 state를 그대로 유지하면서 브라우저만 강제로 열기 위해
        # webbrowser.open을 subprocess.Popen(['open', url])으로 임시 교체
        original_open = webbrowser.open
        def _force_open(url, **_):
            logger.info("🌐 브라우저를 강제로 엽니다...")
            subprocess.Popen(['open', url])
            return True
        webbrowser.open = _force_open

        try:
            creds = flow.run_local_server(port=0, open_browser=True,
                                          authorization_prompt_message='')
        finally:
            webbrowser.open = original_open

        return creds

    def upload_video(self, file_path, title, description, tags=None, privacy_status='private', category_id='22'):
        """
        영상을 유튜브에 업로드합니다.
        
        Args:
            file_path (str): 업로드할 영상 파일 경로
            title (str): 영상 제목
            description (str): 영상 설명
            tags (list): 태그 리스트
            privacy_status (str): 공개 범위 (public, private, unlisted)
            category_id (str): 카테고리 ID (22: People & Blogs)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"업로드할 파일을 찾을 수 없습니다: {file_path}")

        logger.info(f"📤 유튜브 업로드 시작: {title} ({privacy_status})")
        
        # .mov 파일인 경우 mimetype 조정 가능성 확인 (영상 업로드 API는 대부분의 영상 포맷 지원)
        mimetype = 'video/mp4'
        if file_path.lower().endswith('.mov'):
            mimetype = 'video/quicktime'

        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags or [],
                'categoryId': category_id
            },
            'status': {
                'privacyStatus': privacy_status,
                'selfDeclaredMadeForKids': False
            }
        }

        # 미디어 파일 설정 (청크 업로드)
        media = MediaFileUpload(
            file_path, 
            mimetype=mimetype, 
            resumable=True
        )

        request = self.youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )

        response = None
        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.info(f"🚀 {title} 업로드 진행 중: {progress}%")
            except Exception as e:
                logger.error(f"업로드 중 청크 에러 발생: {e}")
                raise e
        
        video_id = response.get('id')
        logger.info(f"✅ 유튜브 업로드 완료! Video ID: {video_id} (채널: {self.channel_title})")
        return video_id

    def get_playlists(self):
        """채널의 재생목록 목록을 반환합니다."""
        playlists = []
        req = self.youtube.playlists().list(part='snippet', mine=True, maxResults=50)
        while req:
            resp = req.execute()
            for item in resp.get('items', []):
                playlists.append({'id': item['id'], 'title': item['snippet']['title']})
            req = self.youtube.playlists().list_next(req, resp)
        return playlists

    def add_to_playlist(self, video_id, playlist_id):
        """영상을 재생목록에 추가합니다."""
        self.youtube.playlistItems().insert(
            part='snippet',
            body={'snippet': {
                'playlistId': playlist_id,
                'resourceId': {'kind': 'youtube#video', 'videoId': video_id}
            }}
        ).execute()
        logger.info(f"📋 재생목록에 추가 완료: {playlist_id}")

    def post_comment(self, video_id, comment_text):
        """
        영상에 고정 댓글을 작성합니다. (youtube.force-ssl scope 필요)

        Args:
            video_id (str): 댓글 달 영상 ID
            comment_text (str): 댓글 내용
        Returns:
            comment_id (str): 생성된 댓글 ID
        """
        body = {
            'snippet': {
                'videoId': video_id,
                'topLevelComment': {
                    'snippet': {
                        'textOriginal': comment_text
                    }
                }
            }
        }
        response = self.youtube.commentThreads().insert(
            part='snippet',
            body=body
        ).execute()
        comment_id = response['id']
        logger.info(f"💬 댓글 작성 완료: {comment_id}")
        return comment_id
