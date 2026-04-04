import os
import sys
import json
import uuid
import threading
import subprocess
import traceback
from datetime import datetime

# 앱 루트 경로 설정 (독립형 구조)
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_ROOT)

# PyQt6 임포트 (기존 프로젝트 기준)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QTextEdit, QComboBox, QCheckBox,
                             QScrollArea, QDialog, QFormLayout, QDialogButtonBox, QGroupBox,
                             QListWidget, QMessageBox)
from PyQt6.QtCore import Qt, QObject, pyqtSignal, QThread
from PyQt6.QtGui import QFont, QTextCursor

from core_logic import (
    ensure_downloaded,
    parse_vtt,
    analyze_script_first,
    generate_single_tts,
    generate_metadata,
    clips_to_subtitles,
    sfx,
    reset_cost_tracker,
    get_cost_summary,
    TTS_CACHE_DIR,
    BGM_DIR
)
from video_engine import VideoProcessor

# ─── 채널 데이터 관리 ─────────────────────────────────────────────────────────
CHANNELS_FILE = os.path.join(APP_ROOT, "cache", "channels.json")

def _load_channels_data():
    if not os.path.exists(CHANNELS_FILE):
        return {"active_id": None, "channels": []}
    try:
        with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"active_id": None, "channels": []}

def _save_channels_data(data):
    os.makedirs(os.path.dirname(CHANNELS_FILE), exist_ok=True)
    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# Worker for Analysis Process
class AnalysisWorker(QObject):
    finished = pyqtSignal(dict, str, str) # data, vid, video_path
    error = pyqtSignal(str)

    def __init__(self, url, voice, model_name, ignore_cache, multimodal_mode=False):
        super().__init__()
        self.url = url
        self.voice = voice
        self.model_name = model_name
        self.ignore_cache = ignore_cache
        self.multimodal_mode = multimodal_mode

    def run(self):
        try:
            print(f"======================================")
            print(f"🚀 [1단계] 영상 준비 및 스크립트 분석 시작")
            print(f"======================================")

            print("\n[1/3] 영상 준비 (다운로드 또는 로컬 확인)...")
            vid, video_path, vtt_path = ensure_downloaded(self.url)

            print(f"\n[2/3] 자막 파싱...")
            transcript = parse_vtt(vtt_path)
            print(f"   {len(transcript)}개 구간 확보 완료.")

            if not transcript:
                print("   ⚠ 자막이 없어 로컬/추론 대체 분석을 실시할 수 있습니다.")
                if not os.path.exists(vtt_path):
                    print("   ❌ 사용할 수 있는 영어(.en.vtt) 자막이 없습니다. 자동 분석이 어려울 수 있습니다.")

            print(f"\n[3/3] 🤖 AI 스크립트 작성 (Model: {self.model_name}, Mode: {'Multimodal' if self.multimodal_mode else 'Transcript-only'})...")
            data = analyze_script_first(vid, self.url, transcript, model_name=self.model_name, ignore_cache=self.ignore_cache, multimodal_mode=self.multimodal_mode)

            print("\n✅ 분석 완료! 아래 에디터에서 대본과 컷 시간을 수정한 후 [최종 쇼츠 생성]을 눌러주세요.")
            self.finished.emit(data, vid, video_path)

        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))

# Worker for Generation Process
class GenerationWorker(QObject):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, data, vid, video_path, url, voice, model_name, bgm_path=None,
                 subtitle_position="중단", use_sfx=True, channel_watermark=None):
        super().__init__()
        self.data = data
        self.vid = vid
        self.video_path = video_path
        self.url = url
        self.voice = voice
        self.model_name = model_name
        self.bgm_path = bgm_path
        self.subtitle_position = subtitle_position
        self.use_sfx = use_sfx
        self.channel_watermark = channel_watermark

    def run(self):
        try:
            print(f"\n======================================")
            print(f"🎥 [2단계] TTS 및 CapCut 프로젝트 생성 시작")
            print(f"======================================")

            clips = self.data.get('clips', [])

            print(f"\n[1/3] 🔊 (버그수정) 클립별 개별 TTS 직접 생성 중 (보이스: {self.voice})...")
            clips_with_tts = generate_single_tts(clips, self.vid, voice=self.voice)
            bgm = self.bgm_path

            print(f"\n[2/3] 📋 CapCut 세그먼트 구성 중...")
            project_name = f"{self.vid}_{datetime.now().strftime('%Y%m%d_%H%M')}"
            cap_title    = self.data.get('title') or project_name
            cap_channel  = self.data.get('channel', '')
            cap_source   = f"출처: {cap_channel}" if cap_channel else f"출처: {self.url}"
            cap_subs     = clips_to_subtitles(clips_with_tts)

            # ────────────────────────────────────────────────────────────
            # 🔊 오디오 병합 (ffmpeg concat 대신 Pydub을 사용해 싱크 정확성 확보)
            # ────────────────────────────────────────────────────────────
            tts_dir = os.path.join(TTS_CACHE_DIR, self.vid)
            concat_tts = os.path.join(tts_dir, "narration_concat.mp3")

            try:
                from pydub import AudioSegment
                final_audio = AudioSegment.silent(duration=0)
                exact_sync_success = True

                for c in clips_with_tts:
                    dur_ms = int((c['tts_duration'] if c['tts_duration'] > 0 else c['duration']) * 1000)
                    clip_path = c.get('tts_path')

                    if clip_path and os.path.exists(clip_path):
                        seg = AudioSegment.from_file(clip_path)
                        if len(seg) > dur_ms + 100:
                            seg = seg[:dur_ms]
                        final_audio += seg
                    else:
                        final_audio += AudioSegment.silent(duration=dur_ms)

                try:
                    from pydub.effects import normalize, compress_dynamic_range
                    final_audio = normalize(final_audio)
                    final_audio = compress_dynamic_range(final_audio, threshold=-15.0, ratio=4.0, attack=5.0, release=50.0)
                except Exception as e:
                    print(f"      [오디오 마스터링 패스] {e}")

                final_audio.export(concat_tts, format="mp3", bitrate="192k")
                final_tts_path = concat_tts
            except Exception as e:
                print(f"   [싱크 경고] Pydub 처리 실패, FFMPEG fallback 사용 ({e})")
                clip_files = [c['tts_path'] for c in clips_with_tts if c.get('tts_path') and os.path.exists(c['tts_path'])]
                if clip_files:
                    concat_list = os.path.join(tts_dir, "concat_tts.txt")
                    with open(concat_list, 'w') as f:
                        for fp in clip_files: f.write(f"file '{fp}'\n")
                    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                                    "-i", concat_list, "-acodec", "libmp3lame", concat_tts], capture_output=True)
                    final_tts_path = concat_tts if os.path.exists(concat_tts) else None
                else:
                    final_tts_path = None

            # ────────────────────────────────────────────────────────────

            cap_segments = []
            cumulative_t = 0.0
            for c in clips_with_tts:
                dur = c['tts_duration'] if c['tts_duration'] > 0 else c['duration']
                sfx_file = sfx(c.get('sfx')) if self.use_sfx else None
                cap_segments.append({
                    'start_time':    c['start_time'],
                    'duration':      dur,
                    'sfx_path':      sfx_file,
                    'timeline_start': cumulative_t,
                })
                sfx_tag = f"  🔊{c['sfx']}" if c.get('sfx') and sfx_file else ""
                print(f"   ✓ #{c['order']:02d} [{c['role']}] {c['start_time']:.1f}s → {c['start_time']+dur:.1f}s  ({dur:.2f}s){sfx_tag}")
                cumulative_t += dur

            print(f"\n[3/3] 🎬 CapCut 프로젝트 내보내기 ({project_name})...")
            project_path = VideoProcessor().export_to_capcut(
                video_path=self.video_path,
                segments=cap_segments,
                project_name=project_name,
                title=cap_title,
                source=cap_source,
                subtitles=cap_subs,
                tts_path=final_tts_path,
                bgm_path=bgm,
                video_clips=None,
                subtitle_position=self.subtitle_position,
                channel_watermark=self.channel_watermark
            )

            if project_path:
                generate_metadata(clips, project_path, self.model_name)

            print("\n🎉 모든 프로세스 완료!")
            if project_path:
                print(f"👉 CapCut 프로젝트 생성됨: {project_path}")
                print("🚀 CapCut 앱을 자동으로 실행합니다...")
                os.system("open -a 'CapCut'")

            self.finished.emit(project_path)

        except Exception as e:
            traceback.print_exc()
            self.error.emit(str(e))

class StreamRedirector(QObject):
    text_written = pyqtSignal(str)

    def write(self, text):
        self.text_written.emit(str(text))

    def flush(self):
        pass


# ─── 채널 관리 다이얼로그 ──────────────────────────────────────────────────────
class ChannelManagerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("채널 관리")
        self.setMinimumWidth(500)
        self.setMinimumHeight(380)
        self.setModal(True)
        self._data = _load_channels_data()
        self._init_ui()
        self._refresh_list()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 채널 목록
        layout.addWidget(QLabel("<b>채널 목록</b>"))
        self.channel_list = QListWidget()
        self.channel_list.currentRowChanged.connect(self._on_select)
        layout.addWidget(self.channel_list)

        # 편집 폼
        edit_group = QGroupBox("채널 정보 편집")
        form = QFormLayout(edit_group)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("예: 진리의 울림")
        self.watermark_edit = QLineEdit()
        self.watermark_edit.setPlaceholderText("예: @진리의 울림")
        form.addRow("채널명 (목록 표시용):", self.name_edit)
        form.addRow("워터마크 (영상 중앙 반투명):", self.watermark_edit)
        layout.addWidget(edit_group)

        # 버튼 행
        btn_layout = QHBoxLayout()
        self.btn_add    = QPushButton("+ 채널 추가")
        self.btn_save_ch = QPushButton("저장")
        self.btn_del    = QPushButton("삭제")
        self.btn_add.clicked.connect(self._add_channel)
        self.btn_save_ch.clicked.connect(self._save_channel)
        self.btn_del.clicked.connect(self._delete_channel)
        btn_layout.addWidget(self.btn_add)
        btn_layout.addWidget(self.btn_save_ch)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_del)
        layout.addLayout(btn_layout)

        # 닫기
        close_btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close_btns.rejected.connect(self.reject)
        layout.addWidget(close_btns)

    def _refresh_list(self):
        self.channel_list.clear()
        for ch in self._data.get("channels", []):
            self.channel_list.addItem(ch.get("name", "(이름없음)"))

    def _on_select(self, row):
        channels = self._data.get("channels", [])
        if 0 <= row < len(channels):
            ch = channels[row]
            self.name_edit.setText(ch.get("name", ""))
            self.watermark_edit.setText(ch.get("watermark", ""))

    def _add_channel(self):
        ch = {
            "id": str(uuid.uuid4()),
            "name": "새 채널",
            "watermark": "@새채널",
            "voice": None,
            "model": None,
            "scale": "1.5",
            "bgm_name": "없음 (BGM 없이)",
            "bgm": None,
            "subtitle_position": "중단",
            "use_sfx": True
        }
        self._data.setdefault("channels", []).append(ch)
        _save_channels_data(self._data)
        self._refresh_list()
        self.channel_list.setCurrentRow(len(self._data["channels"]) - 1)

    def _save_channel(self):
        row = self.channel_list.currentRow()
        channels = self._data.get("channels", [])
        if 0 <= row < len(channels):
            channels[row]["name"] = self.name_edit.text().strip() or "채널"
            channels[row]["watermark"] = self.watermark_edit.text().strip()
            _save_channels_data(self._data)
            self._refresh_list()
            self.channel_list.setCurrentRow(row)

    def _delete_channel(self):
        row = self.channel_list.currentRow()
        channels = self._data.get("channels", [])
        if 0 <= row < len(channels):
            name = channels[row].get("name", "채널")
            reply = QMessageBox.question(self, "삭제 확인",
                f"'{name}'을(를) 삭제할까요?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                channels.pop(row)
                _save_channels_data(self._data)
                self._refresh_list()
                self.name_edit.clear()
                self.watermark_edit.clear()


# ─── 설정 팝업 다이얼로그 ────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.setMinimumWidth(480)
        self.setModal(True)

        layout = QVBoxLayout(self)

        # ── 생성 옵션 그룹 ──
        gen_group = QGroupBox("생성 옵션")
        gen_form = QFormLayout(gen_group)

        self.voice_combo = QComboBox()
        # gemini_voices.json 로드 시도 (앱 내장 또는 상위 폴더)
        try:
            voices_file = os.path.join(APP_ROOT, "resources", "gemini_voices.json")
            if not os.path.exists(voices_file):
                # 백업 경로 (기존 프로젝트 구조 내에 있는 경우)
                voices_file = os.path.join(os.path.dirname(APP_ROOT), "src", "data", "gemini_voices.json")
            
            if os.path.exists(voices_file):
                import json
                with open(voices_file, "r", encoding="utf-8") as f:
                    voices_data = json.load(f)
                    for v in voices_data:
                        voice_label = f"{v['name'].capitalize()} ({v['description_ko']})"
                        self.voice_combo.addItem(voice_label, v['name'])
            else:
                default_voices = [
                    ("Kore", "여성/차분하고 지적인 톤"),
                    ("Charon", "남성/신뢰감 있는 중저음"),
                    ("Aoede", "여성/밝고 부드러운 톤"),
                    ("Fenrir", "남성/활기차고 경쾌한 톤"),
                    ("Puck", "중성/느긋하고 편안한 톤")
                ]
                for name, desc in default_voices:
                    self.voice_combo.addItem(f"{name} ({desc})", name.lower())
        except Exception as e:
            print(f"⚠ 보이스 데이터 로드 실패: {e}")
            self.voice_combo.addItem("Charon (기본 남성)", "charon")

        gen_form.addRow("나레이션 보이스:", self.voice_combo)

        self.model_combo = QComboBox()
        self.model_combo.addItems(["gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-2.5-flash"])
        gen_form.addRow("Gemini 모델:", self.model_combo)

        self.scale_combo = QComboBox()
        self.scale_combo.addItems(["1.0", "1.25", "1.5", "1.75", "2.0"])
        self.scale_combo.setCurrentText("1.5")
        gen_form.addRow("확대 배율:", self.scale_combo)

        layout.addWidget(gen_group)

        # ── 영상 옵션 그룹 ──
        vid_group = QGroupBox("영상 옵션")
        vid_form = QFormLayout(vid_group)

        self.subtitle_pos_combo = QComboBox()
        self.subtitle_pos_combo.addItems(["중단", "하단"])
        vid_form.addRow("자막 위치:", self.subtitle_pos_combo)

        self.bgm_combo = QComboBox()
        self.bgm_combo.setMinimumWidth(240)
        self._refresh_bgm_combo()
        vid_form.addRow("BGM:", self.bgm_combo)

        self.use_sfx_cb = QCheckBox("효과음 사용")
        self.use_sfx_cb.setChecked(True)
        self.use_sfx_cb.setToolTip("설교, 강의 등 조용한 콘텐츠에서는 효과음을 끄세요")
        vid_form.addRow("효과음:", self.use_sfx_cb)

        layout.addWidget(vid_group)

        # ── 분석 옵션 그룹 ──
        ana_group = QGroupBox("분석 옵션")
        ana_form = QFormLayout(ana_group)

        self.ignore_cache_cb = QCheckBox("캐시 무시 (재분석 강제)")
        ana_form.addRow("", self.ignore_cache_cb)

        self.multimodal_cb = QCheckBox("멀티모달 모드 (영상 직접 분석)")
        self.multimodal_cb.setToolTip("자막뿐 아니라 영상 화면의 시각적 요소를 직접 AI가 분석합니다 (마술, 스포츠 등)")
        ana_form.addRow("", self.multimodal_cb)

        layout.addWidget(ana_group)

        # ── 확인 버튼 ──
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _refresh_bgm_combo(self):
        self.bgm_combo.clear()
        self.bgm_combo.addItem("없음 (BGM 없이)", None)
        from pathlib import Path
        for f in sorted(p for ext in ("*.mp3", "*.mp4") for p in Path(BGM_DIR).glob(ext)):
            self.bgm_combo.addItem(f.stem, str(f))

    def get_selected_bgm(self):
        return self.bgm_combo.currentData()


# ─── 메인 윈도우 ──────────────────────────────────────────────────────────────
class PyQtCreativeShortsGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("[BETA v3] 오디오/SFX 스크립트 기반 쇼츠 생성기 (PyQt6)")
        self.resize(1100, 900)

        self.current_vid = None
        self.current_video_path = None
        self.current_url = None
        self.url_history = []
        self._active_channel_id = None  # 채널 전환 시 이전 채널 설정 저장용

        # 설정 다이얼로그 (단일 인스턴스 재사용)
        self.settings_dialog = SettingsDialog(self)

        self.init_ui()

        from PyQt6.QtCore import QTimer
        QTimer.singleShot(200, self.safe_startup)

    def safe_startup(self):
        try:
            self.load_settings()
            self._refresh_channel_combo()
            self.setup_logging()
            print("🚀 시스템 준비 완료.")
        except Exception as e:
            print(f"⚠ 초기 기동 중 오류: {e}")

    # 설정 위임 프로퍼티 (하위 호환)
    @property
    def voice_combo(self): return self.settings_dialog.voice_combo
    @property
    def model_combo(self): return self.settings_dialog.model_combo
    @property
    def scale_combo(self): return self.settings_dialog.scale_combo
    @property
    def subtitle_pos_combo(self): return self.settings_dialog.subtitle_pos_combo
    @property
    def bgm_combo(self): return self.settings_dialog.bgm_combo
    @property
    def use_sfx_cb(self): return self.settings_dialog.use_sfx_cb
    @property
    def ignore_cache_cb(self): return self.settings_dialog.ignore_cache_cb
    @property
    def multimodal_cb(self): return self.settings_dialog.multimodal_cb

    def get_selected_bgm(self):
        return self.settings_dialog.get_selected_bgm()

    # ── 채널 관련 메서드 ──────────────────────────────────────────────────────

    def _refresh_channel_combo(self):
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.addItem("(채널 없음)", None)
        data = _load_channels_data()
        for ch in data.get("channels", []):
            self.channel_combo.addItem(ch["name"], ch["id"])
        # 마지막으로 선택한 채널 복원
        active_id = data.get("active_id")
        if active_id:
            for i in range(self.channel_combo.count()):
                if self.channel_combo.itemData(i) == active_id:
                    self.channel_combo.setCurrentIndex(i)
                    self._active_channel_id = active_id
                    break
        self.channel_combo.blockSignals(False)
        # 활성 채널 설정 적용 (신호 없이 호출했으므로 수동으로)
        ch = self.get_active_channel()
        if ch:
            self._apply_channel_settings(ch)

    def get_active_channel(self):
        ch_id = self.channel_combo.currentData()
        if not ch_id:
            return None
        data = _load_channels_data()
        for ch in data.get("channels", []):
            if ch["id"] == ch_id:
                return ch
        return None

    def _apply_channel_settings(self, ch):
        """채널 설정을 설정 다이얼로그 위젯에 적용"""
        if ch.get("voice"):
            for i in range(self.voice_combo.count()):
                if self.voice_combo.itemData(i) == ch["voice"]:
                    self.voice_combo.setCurrentIndex(i)
                    break
        if ch.get("model"):
            idx = self.model_combo.findText(ch["model"])
            if idx >= 0: self.model_combo.setCurrentIndex(idx)
        if ch.get("scale"):
            idx = self.scale_combo.findText(ch["scale"])
            if idx >= 0: self.scale_combo.setCurrentIndex(idx)
        if ch.get("subtitle_position"):
            idx = self.subtitle_pos_combo.findText(ch["subtitle_position"])
            if idx >= 0: self.subtitle_pos_combo.setCurrentIndex(idx)
        if "use_sfx" in ch:
            self.use_sfx_cb.setChecked(ch["use_sfx"])
        if ch.get("bgm_name"):
            idx = self.bgm_combo.findText(ch["bgm_name"])
            if idx >= 0: self.bgm_combo.setCurrentIndex(idx)

    def _collect_channel_settings(self):
        """현재 설정을 활성 채널 프로필에 저장"""
        ch_id = self._active_channel_id
        if not ch_id:
            return
        data = _load_channels_data()
        for ch in data.get("channels", []):
            if ch["id"] == ch_id:
                ch["voice"] = self.voice_combo.currentData()
                ch["model"] = self.model_combo.currentText()
                ch["scale"] = self.scale_combo.currentText()
                ch["subtitle_position"] = self.subtitle_pos_combo.currentText()
                ch["use_sfx"] = self.use_sfx_cb.isChecked()
                ch["bgm_name"] = self.bgm_combo.currentText()
                ch["bgm"] = self.bgm_combo.currentData()
                break
        _save_channels_data(data)

    def _on_channel_changed(self, _index):
        # 이전 채널 설정 먼저 저장
        self._collect_channel_settings()
        # 새 채널 설정 적용
        ch = self.get_active_channel()
        if ch:
            self._apply_channel_settings(ch)
        # 활성 채널 ID 업데이트
        new_id = self.channel_combo.currentData()
        self._active_channel_id = new_id
        data = _load_channels_data()
        data["active_id"] = new_id
        _save_channels_data(data)

    def open_channel_manager(self):
        dlg = ChannelManagerDialog(self)
        dlg.exec()
        self._refresh_channel_combo()

    # ── 설정 저장/로드 ────────────────────────────────────────────────────────

    def save_settings(self):
        settings_file = os.path.join(APP_ROOT, "cache", "gui_settings.json")
        settings = {
            "url": self.get_current_url(),
            "voice": self.voice_combo.currentText(),
            "model": self.model_combo.currentText(),
            "scale": self.scale_combo.currentText(),
            "ignore_cache": self.ignore_cache_cb.isChecked(),
            "multimodal_mode": self.multimodal_cb.isChecked(),
            "subtitle_position": self.subtitle_pos_combo.currentText(),
            "bgm": self.bgm_combo.currentText(),
            "use_sfx": self.use_sfx_cb.isChecked(),
            "url_history": self.url_history
        }
        try:
            os.makedirs(os.path.dirname(settings_file), exist_ok=True)
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ 설정 저장 중 오류: {e}")

    def load_settings(self):
        settings_file = os.path.join(APP_ROOT, "cache", "gui_settings.json")
        if not os.path.exists(settings_file):
            self.url_history = []
            return
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                settings = json.load(f)

            self.url_history = settings.get("url_history", [])
            self.update_url_combo()

            last_url = settings.get("url", "")
            if last_url:
                self.url_entry.setCurrentText(last_url)

            voice = settings.get("voice")
            if voice:
                idx = self.voice_combo.findText(voice)
                if idx >= 0: self.voice_combo.setCurrentIndex(idx)

            model = settings.get("model")
            if model:
                idx = self.model_combo.findText(model)
                if idx >= 0: self.model_combo.setCurrentIndex(idx)

            scale = settings.get("scale", "1.5")
            idx = self.scale_combo.findText(scale)
            if idx >= 0: self.scale_combo.setCurrentIndex(idx)

            self.ignore_cache_cb.setChecked(settings.get("ignore_cache", False))
            self.multimodal_cb.setChecked(settings.get("multimodal_mode", False))

            sub_pos = settings.get("subtitle_position", "중단")
            idx = self.subtitle_pos_combo.findText(sub_pos)
            if idx >= 0: self.subtitle_pos_combo.setCurrentIndex(idx)

            bgm_name = settings.get("bgm")
            if bgm_name:
                idx = self.bgm_combo.findText(bgm_name)
                if idx >= 0: self.bgm_combo.setCurrentIndex(idx)

            self.use_sfx_cb.setChecked(settings.get("use_sfx", True))
        except Exception as e:
            print(f"⚠ 설정 불러오기 중 오류: {e}")

    def open_settings(self):
        self.settings_dialog.exec()
        self._collect_channel_settings()  # 현재 채널에 설정 저장
        self.save_settings()

    def get_current_url(self):
        text = self.url_entry.currentText().strip()
        if " | " in text:
            return text.split(" | ")[0].strip()
        return text

    def update_url_combo(self):
        self.url_entry.clear()
        for item in self.url_history:
            if isinstance(item, dict):
                disp = f"{item['url']} | {item.get('title', '제목없음')}"
                self.url_entry.addItem(disp, item['url'])
            else:
                self.url_entry.addItem(item, item)

    def add_to_history(self, url, title):
        new_item = {"url": url, "title": title}
        self.url_history = [h for h in self.url_history if (h['url'] if isinstance(h, dict) else h) != url]
        self.url_history.insert(0, new_item)
        self.url_history = self.url_history[:20]
        self.update_url_combo()
        self.url_entry.setCurrentText(f"{url} | {title}")
        self.save_settings()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── 채널 선택 ──
        ch_layout = QHBoxLayout()
        ch_layout.addWidget(QLabel("채널:"))
        self.channel_combo = QComboBox()
        self.channel_combo.setMinimumWidth(200)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        ch_layout.addWidget(self.channel_combo)
        self.btn_channel_mgr = QPushButton("채널 관리")
        self.btn_channel_mgr.setFixedWidth(90)
        self.btn_channel_mgr.clicked.connect(self.open_channel_manager)
        ch_layout.addWidget(self.btn_channel_mgr)
        ch_layout.addStretch()
        main_layout.addLayout(ch_layout)

        # ── URL 입력 + 설정 버튼 ──
        url_layout = QHBoxLayout()
        url_layout.addWidget(QLabel("영상 URL (또는 MP4 경로):"))
        self.url_entry = QComboBox()
        self.url_entry.setEditable(True)
        self.url_entry.setPlaceholderText("https://www.youtube.com/watch?v=...")
        url_layout.addWidget(self.url_entry, 1)

        self.btn_settings = QPushButton("⚙ 설정")
        self.btn_settings.setFixedWidth(80)
        self.btn_settings.setToolTip("보이스, 모델, BGM, 효과음 등 세부 설정")
        self.btn_settings.clicked.connect(self.open_settings)
        url_layout.addWidget(self.btn_settings)
        main_layout.addLayout(url_layout)

        # ── 분석 버튼 ──
        self.btn_analyze = QPushButton("1. 영상 다운로드 및 AI 대본 초안 생성")
        self.btn_analyze.setStyleSheet("background-color: #2196F3; color: white; font-weight: bold; font-size: 14px; padding: 10px;")
        self.btn_analyze.clicked.connect(self.start_analysis)
        main_layout.addWidget(self.btn_analyze)

        # ── JSON 에디터 ──
        main_layout.addWidget(QLabel("<b>📝 대본 직접 편집 (JSON 구조)</b>", font=QFont("Arial", 12)))
        self.json_editor = QTextEdit()
        self.json_editor.setFont(QFont("Menlo", 12))
        self.json_editor.setPlainText("여기에 대본 초안이 생성됩니다. 원하는 대로 수정한 뒤 아래 생성 버튼을 누르세요.")
        main_layout.addWidget(self.json_editor, 2)

        # ── 생성 버튼 ──
        self.btn_generate = QPushButton("2. 최종 쇼츠 (CapCut) 생성하기")
        self.btn_generate.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; font-size: 16px; padding: 15px;")
        self.btn_generate.clicked.connect(self.start_generation)
        main_layout.addWidget(self.btn_generate)

        # ── 로그 ──
        main_layout.addWidget(QLabel("<b>터미널 로그</b>", font=QFont("Arial", 11)))
        self.log_box = QTextEdit()
        self.log_box.setFont(QFont("Menlo", 11))
        self.log_box.setReadOnly(True)
        self.log_box.setStyleSheet("background-color: #1e1e1e; color: #a4ce7d;")
        main_layout.addWidget(self.log_box, 1)

    def setup_logging(self):
        self.sys_out_redirector = StreamRedirector()
        self.sys_out_redirector.text_written.connect(self.append_log)
        sys.stdout = self.sys_out_redirector
        sys.stderr = self.sys_out_redirector

    def append_log(self, text):
        cursor = self.log_box.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.log_box.setTextCursor(cursor)
        self.log_box.ensureCursorVisible()

    def start_analysis(self):
        url = self.get_current_url()
        if not url:
            print("❌ URL을 입력해주세요.")
            return

        self.save_settings()

        self.btn_analyze.setEnabled(False)
        self.btn_generate.setEnabled(False)
        self.json_editor.clear()
        reset_cost_tracker()

        self.thread = QThread()
        self.worker = AnalysisWorker(
            url=url,
            voice=self.voice_combo.currentData(),
            model_name=self.model_combo.currentText(),
            ignore_cache=self.ignore_cache_cb.isChecked(),
            multimodal_mode=self.multimodal_cb.isChecked()
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.on_analysis_finished)
        self.worker.error.connect(self.on_worker_error)
        self.thread.start()

    def on_analysis_finished(self, data, vid, video_path):
        self.current_vid = vid
        self.current_video_path = video_path
        self.current_url = self.get_current_url()

        formatted_json = json.dumps(data, ensure_ascii=False, indent=2)
        self.json_editor.setPlainText(formatted_json)

        title = data.get('title', '제목없음')
        self.add_to_history(self.current_url, title)

        self.thread.quit()
        self.thread.wait()

        self.btn_analyze.setEnabled(True)
        self.btn_generate.setEnabled(True)

    def start_generation(self):
        if not self.current_vid or not self.current_video_path:
            print("❌ 먼저 '분석 시작'을 완료해주세요.")
            return

        self.save_settings()

        editor_content = self.json_editor.toPlainText().strip()
        try:
            data = json.loads(editor_content)
        except json.JSONDecodeError as e:
            print(f"❌ JSON 파싱 에러! 형식이 올바르지 않습니다.\n{e}")
            return

        # 활성 채널에서 워터마크 가져오기 (출처는 소스 영상 메타데이터에서 자동 추출)
        ch = self.get_active_channel()
        channel_watermark = ch.get("watermark", "") if ch else ""

        self.btn_analyze.setEnabled(False)
        self.btn_generate.setEnabled(False)

        self.gen_thread = QThread()
        self.gen_worker = GenerationWorker(
            data=data,
            vid=self.current_vid,
            video_path=self.current_video_path,
            url=self.current_url,
            voice=self.voice_combo.currentData(),
            model_name=self.model_combo.currentText(),
            bgm_path=self.get_selected_bgm(),
            subtitle_position=self.subtitle_pos_combo.currentText(),
            use_sfx=self.use_sfx_cb.isChecked(),
            channel_watermark=channel_watermark
        )
        self.gen_worker.moveToThread(self.gen_thread)
        self.gen_thread.started.connect(self.gen_worker.run)
        self.gen_worker.finished.connect(self.on_generation_finished)
        self.gen_worker.error.connect(self.on_worker_error)
        self.gen_thread.start()

    def on_generation_finished(self, project_path):
        self.gen_thread.quit()
        self.gen_thread.wait()
        print(get_cost_summary())
        self.btn_analyze.setEnabled(True)
        self.btn_generate.setEnabled(True)

    def on_worker_error(self, err_msg):
        print(f"\n❌ 에러 발생: {err_msg}")
        if hasattr(self, 'thread') and self.thread.isRunning():
            self.thread.quit()
            self.thread.wait()
        if hasattr(self, 'gen_thread') and self.gen_thread.isRunning():
            self.gen_thread.quit()
            self.gen_thread.wait()

        self.btn_analyze.setEnabled(True)
        self.btn_generate.setEnabled(True)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = PyQtCreativeShortsGUI()
    window.show()
    sys.exit(app.exec())
