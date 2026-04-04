import os
import logging
import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import numpy as np
from moviepy.editor import VideoFileClip, ImageClip, CompositeVideoClip, concatenate_videoclips, AudioFileClip
import time
import librosa
import tempfile
from gtts import gTTS
import moviepy.video.fx.all as vfx
from proglog import ProgressBarLogger
import uuid
import copy
from datetime import datetime

# APP_ROOT 설정
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

class UILogger(ProgressBarLogger):
    def __init__(self, progress_callback):
        super().__init__()
        self.progress_callback = progress_callback
        self.last_perc = -1

    def callback(self, **changes):
        pass # 더 이상 메시지 콜백 안 씀

    def bars_callback(self, bar, attr, value, old_value=None):
        if attr == 'index' and bar == 't':
            total = self.bars[bar]['total']
            if total:
                perc = int(100 * value / total)
                if perc != self.last_perc:
                    self.last_perc = perc
                    if self.progress_callback:
                        self.progress_callback(perc)

if not hasattr(PIL.Image, 'ANTIALIAS'):
    PIL.Image.ANTIALIAS = PIL.Image.LANCZOS

logger = logging.getLogger(__name__)

class VideoProcessor:
    def create_text_clip_pil(self, text, fontsize, color, bg_color, size, duration, position, outline_color=None, shadow_color=None):
        try:
            width = size[0]
            if width is None: width = 1000
            font_path = "/Users/chris/Library/Containers/com.lemon.lvoverseas/Data/Library/Fonts/Jalnan2TTF.ttf"
            if not os.path.exists(font_path):
                font_path = os.path.join(APP_ROOT, "resources", "fonts", "Jalnan2TTF.ttf")
            if not os.path.exists(font_path):
                font_path = os.path.join(APP_ROOT, "Jalnan2TTF.ttf")
            if not os.path.exists(font_path):
                raise FileNotFoundError(f"필수 폰트 파일을 찾을 수 없습니다: {font_path}")
            
            font = PIL.ImageFont.truetype(font_path, int(fontsize))
            if fontsize == 80:
                fontsize = 70
                font = PIL.ImageFont.truetype(font_path, fontsize)

            try:
                left, top, right, bottom = font.getbbox(text)
                text_w = right - left
                text_h = bottom - top
            except:
                text_w, text_h = font.getsize(text)
                
            padding = 30
            img_w = width
            img_h = text_h + (padding * 2) + 40
            
            img = PIL.Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
            draw = PIL.ImageDraw.Draw(img)
            
            is_transparent_bg = 'rgba(0,0,0,0)' in bg_color or bg_color == 'transparent' or (isinstance(bg_color, tuple) and bg_color[3] == 0)
            if not is_transparent_bg:
                bg_rect = [(img_w - text_w)/2 - padding, 10, (img_w + text_w)/2 + padding, 10 + text_h + padding]
                draw.rectangle(bg_rect, fill=(0, 0, 0, 100))
            
            text_pos = ((img_w - text_w)/2, padding)
            text_rgb = (255, 255, 255)
            if color == 'yellow': text_rgb = (255, 255, 0)
            elif isinstance(color, tuple): text_rgb = color

            if shadow_color:
                s_offset = 5
                draw.text((text_pos[0]+s_offset, text_pos[1]+s_offset), text, font=font, fill=shadow_color)

            if outline_color:
                o_width = 6
                for ox in range(-o_width, o_width + 1):
                    for oy in range(-o_width, o_width + 1):
                        if ox == 0 and oy == 0: continue
                        if (ox*ox + oy*oy) <= o_width*o_width:
                            draw.text((text_pos[0]+ox, text_pos[1]+oy), text, font=font, fill=outline_color)
            
            draw.text(text_pos, text, font=font, fill=text_rgb)
            img_np = np.array(img)
            txt_clip = ImageClip(img_np).set_duration(duration).set_position(position)
            return txt_clip
        except Exception as e:
            logger.error(f"PIL 텍스트 생성 실패: {e}")
            return None

    def _change_audio_speed(self, audio_clip, speed=1.0):
        if speed == 1.0: return audio_clip
        try:
            import soundfile as sf
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                temp_path = tf.name
            audio_clip.write_audiofile(temp_path, logger=None)
            y, sr = librosa.load(temp_path, sr=None, mono=True)
            y_stretched = librosa.effects.time_stretch(y, rate=speed)
            y_normalized = librosa.util.normalize(y_stretched)
            sf.write(temp_path, y_normalized, sr)
            new_audio = AudioFileClip(temp_path)
            return new_audio
        except Exception as e:
            logger.error(f"피치 보정 실패: {e}")
            return audio_clip

    def _map_time_after_jumpcut(self, original_t, kept_segs):
        if not kept_segs: return original_t
        cumulative = 0.0
        for (s, e) in kept_segs:
            if original_t <= e:
                return cumulative + max(0.0, original_t - s)
            cumulative += (e - s)
        return cumulative

    def _remove_silence(self, clip, silence_threshold=0.04, min_silence_len=0.4):
        try:
            if clip.audio is None: return clip, []
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_audio:
                temp_audio_path = temp_audio.name
            clip.audio.write_audiofile(temp_audio_path, fps=16000, nbytes=2, buffersize=2000, logger=None)
            y, sr = librosa.load(temp_audio_path, sr=16000)
            y_harmonic, _ = librosa.effects.hpss(y, margin=1.0)
            rms = librosa.feature.rms(y=y_harmonic, frame_length=1024, hop_length=512)[0]
            times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=512)
            max_rms = np.max(rms)
            threshold = max_rms * silence_threshold
            is_speaking = rms > threshold
            chunk_dur = 512 / sr
            min_silence_frames = int(min_silence_len / chunk_dur)
            in_silence_start = -1
            for i in range(len(is_speaking)):
                if not is_speaking[i]:
                    if in_silence_start == -1: in_silence_start = i
                else:
                    if in_silence_start != -1:
                        if (i - in_silence_start) < min_silence_frames:
                            is_speaking[in_silence_start:i] = True
                        in_silence_start = -1
            if in_silence_start != -1 and (len(is_speaking) - in_silence_start) < min_silence_frames:
                is_speaking[in_silence_start:] = True
            segments = []
            is_active = False
            start_t = 0
            for i in range(len(is_speaking)):
                if is_speaking[i] and not is_active:
                    is_active = True
                    start_t = times[i]
                elif not is_speaking[i] and is_active:
                    is_active = False
                    end_t = times[i]
                    if (end_t - start_t) > 0.1: segments.append((start_t, end_t))
            if is_active:
                if (times[-1] - start_t) > 0.1: segments.append((start_t, times[-1]))
            os.remove(temp_audio_path)
            if not segments: return clip, []
            clips = []
            actual_kept = []
            pad_start, pad_end, last_end = 0.3, 0.2, 0
            for (start, end) in segments:
                s = max(last_end, start - pad_start)
                e = min(clip.duration, end + pad_end)
                if e - s > 0.5:
                    clips.append(clip.subclip(s, e))
                    actual_kept.append((s, e))
                last_end = e
            if not clips: return clip, []
            return concatenate_videoclips(clips), actual_kept
        except Exception as e:
            logger.error(f"음성 분석 공백 제거 실패: {e}")
            return clip, []
    def _create_transformative_clip(self, clip, visual_type="original", size=(1080, 1920), ai_image_path=None):
        """visual_type에 따라 영상의 레이아웃을 변형하여 반환합니다."""
        target_w, target_h = size
        duration = clip.duration
        
        # 기본 배경 (검정)
        bg = ImageClip(np.array(PIL.Image.new('RGB', (target_w, target_h), (0, 0, 0)))).set_duration(duration)
        
        if visual_type == "ai_opening" and ai_image_path and os.path.exists(ai_image_path):
            # AI 생성 이미지를 배경으로 사용
            opening_img = ImageClip(ai_image_path).set_duration(duration).resize(height=target_h)
            if opening_img.w > target_w:
                opening_img = opening_img.crop(x_center=opening_img.w/2, width=target_w)
            return CompositeVideoClip([opening_img], size=size)

        if visual_type == "blurred_bg":
            # 1. 배경용 블러 처리 (확대 후 블러)
            bg_blur = clip.resize(height=target_h).fx(vfx.colorx, 0.5).fx(vfx.gaussian_blur, 15)
            if bg_blur.w > target_w:
                bg_blur = bg_blur.crop(x_center=bg_blur.w/2, width=target_w)
            else:
                bg_blur = bg_blur.resize(width=target_w)
            
            # 2. 전경용 메인 영상 (비율 유지하며 중앙 배치)
            fg = clip.resize(width=target_w * 0.9) # 가로 90% 크기
            return CompositeVideoClip([bg_blur, fg.set_position("center")], size=size)

        elif visual_type == "framed":
            # 감각적인 그라데이션 대신 단색/블러 배경 + 프레임
            bg_framed = ImageClip(np.array(PIL.Image.new('RGB', (target_w, target_h), (20, 20, 25)))).set_duration(duration)
            fg = clip.resize(width=target_w * 0.85)
            # 프레임 느낌을 주기 위해 약간의 패딩과 테두리 효과 (여기서는 단순 배치)
            return CompositeVideoClip([bg_framed, fg.set_position(("center", "center"))], size=size)

        else:
            # 기본 (중앙 배치)
            fg = clip.resize(width=target_w)
            if fg.h > target_h:
                fg = fg.crop(y_center=fg.h/2, height=target_h)
            return CompositeVideoClip([bg, fg.set_position("center")], size=size)

    def create_title_clip_pil(self, text, duration, position, title_color_mode="기본 (위:노랑 / 아래:연두)"):
        try:
            font_path = "/Users/chris/Library/Containers/com.lemon.lvoverseas/Data/Library/Fonts/Jalnan2TTF.ttf"
            if not os.path.exists(font_path):
                font_path = os.path.join(APP_ROOT, "resources", "fonts", "JalnanGothicTTF.ttf")
            if not os.path.exists(font_path):
                font_path = os.path.join(APP_ROOT, "JalnanGothicTTF.ttf")
            if not os.path.exists(font_path):
                raise FileNotFoundError(f"필수 폰트 파일을 찾을 수 없습니다: {font_path}")
            if '\n' in text:
                parts = text.split('\n', 1)
                line1, line2 = parts[0].strip(), parts[1].strip()
            elif len(text.replace(" ", "")) <= 6:
                line1, line2 = text, ""
            else:
                words = text.split()
                if len(words) > 1:
                    total_len = len(text)
                    halfway = total_len // 2
                    best_i, min_diff, current_len = 1, 999, 0
                    for i, w in enumerate(words):
                        current_len += len(w) + (1 if i > 0 else 0)
                        diff = abs(current_len - halfway)
                        if diff < min_diff:
                            min_diff, best_i = diff, i + 1
                    line1_candidate, line2_candidate = " ".join(words[:best_i]), " ".join(words[best_i:])
                    if not line2_candidate or len(line1_candidate) < 3 or len(line2_candidate) < 3:
                        line1, line2 = text, ""
                    else:
                        line1, line2 = line1_candidate, line2_candidate
                else: line1, line2 = text, ""
            img_w, img_h = 1080, 450
            img = PIL.Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
            draw = PIL.ImageDraw.Draw(img)
            def draw_line(draw_text, y_offset, color):
                fontsize, font = 110, PIL.ImageFont.truetype(font_path, 110)
                while fontsize > 30:
                    try:
                        left, top, right, bottom = font.getbbox(draw_text)
                        text_w, text_h = right - left, bottom - top
                    except: text_w, text_h = font.getsize(draw_text)
                    if text_w <= 1000: break
                    fontsize -= 4
                    font = PIL.ImageFont.truetype(font_path, fontsize)
                x = (img_w - text_w) / 2
                draw.text((x + 6, y_offset + 6), draw_text, font=font, fill=(0,0,0,180))
                draw.text((x, y_offset), draw_text, font=font, fill=color)
                return text_h
            color_top, color_bottom = (255, 235, 59, 255), (150, 255, 100, 255)
            if title_color_mode == "시니어채널 (위:빨강 / 아래:노랑)":
                color_top, color_bottom = (255, 50, 50, 255), (255, 235, 59, 255)
            elif title_color_mode == "흰색 단일": color_top = color_bottom = (255, 255, 255, 255)
            elif title_color_mode == "파란색 강조": color_top, color_bottom = (50, 150, 255, 255), (255, 255, 255, 255)
            if line2:
                h1 = draw_line(line1, 40, color_top)
                draw_line(line2, 40 + h1 + 20, color_bottom)
            else: draw_line(line1, 175, color_top)
            img_np = np.array(img)
            return ImageClip(img_np).set_duration(duration).set_position(position)
        except Exception as e:
            logger.error(f"PIL 타이틀 생성 실패: {e}")
            return None

    def create_comment_overlay_pil(self, comment_text, duration, position):
        try:
            import textwrap
            font_path = os.path.join(APP_ROOT, "resources", "fonts", "Jalnan2TTF.ttf")
            if not os.path.exists(font_path): font_path = os.path.join(APP_ROOT, "Jalnan2TTF.ttf")
            fontsize, font = 50, PIL.ImageFont.truetype(font_path, 50)
            display_text = "💬 " + comment_text
            wrapped_text = "\n".join(textwrap.wrap(display_text, width=24))
            img_w, img_h = 950, 400
            img = PIL.Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
            draw = PIL.ImageDraw.Draw(img)
            try:
                left, top, right, bottom = draw.textbbox((0,0), wrapped_text, font=font)
                text_w, text_h = right - left, bottom - top
            except: text_w, text_h = font.getsize_multiline(wrapped_text)
            box_w, box_h = min(img_w, text_w + 80), min(img_h, text_h + 80)
            x_box, y_box = (img_w - box_w) / 2, (img_h - box_h) / 2
            draw.rounded_rectangle([x_box, y_box, x_box + box_w, y_box + box_h], radius=25, fill=(0, 0, 0, 210))
            draw.text((x_box + 40, y_box + 40), wrapped_text, font=font, fill=(255, 255, 255, 255))
            img_np = np.array(img)
            display_dur = min(4.0, duration) 
            return ImageClip(img_np).set_duration(display_dur).set_position(position).crossfadein(0.5).crossfadeout(0.5)
        except Exception as e:
            logger.error(f"PIL 댓글 오버레이 생성 실패: {e}")
            return None

    def make_shorts(self, video_path, segments, output_path=None, title_text=None, use_jumpcut=False, use_bypass_filter=False, playback_speed=1.05, use_narration=True, use_comment_overlay=False, tts_engine="gemini", zoom_factor=1.0, title_color_mode="기본 (위:노랑 / 아래:연두)", progress_callback=None):
        if not output_path:
            base, ext = os.path.splitext(video_path)
            output_path = f"{base}_shorts.mp4"
        try:
            full_clip = VideoFileClip(video_path)
            processed_segments, temp_tts_files, temp_tts_audios = [], [], []
            for i, seg in enumerate(segments):
                start, end = seg['start_time'], seg['start_time'] + seg['duration']
                sub = full_clip.subclip(start, min(end, full_clip.duration))
                if playback_speed != 1.0:
                    original_audio = sub.audio
                    video_only = sub.without_audio().fx(vfx.speedx, playback_speed)
                    if original_audio:
                        corrected_audio = self._change_audio_speed(original_audio, playback_speed)
                        sub = video_only.set_audio(corrected_audio)
                    else: sub = video_only
                jumpcut_kept_segs = []
                if use_jumpcut: sub, jumpcut_kept_segs = self._remove_silence(sub)
                mc_dur_total = 0.0
                mc_intro_text = seg.get('mc_intro')
                has_full_narration = use_narration and seg.get('narration') and seg.get('narration').strip()
                if has_full_narration:
                    sub = sub.fx(vfx.colorx, 0.4)
                    if sub.audio:
                        import moviepy.audio.fx.all as afx
                        sub = sub.set_audio(sub.audio.fx(afx.volumex, 0.05))
                elif mc_intro_text and mc_intro_text.strip():
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf: mc_path = tf.name
                        effect_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resource", "28. 띠링_soft.mp3")
                        success = self._generate_gemini_tts(mc_intro_text, mc_path, voice_name=seg.get('voice', 'Charon'), mood=seg.get('mood'), playback_speed=playback_speed, effect_path=effect_path if os.path.exists(effect_path) else None)
                        if success:
                            mc_audio = AudioFileClip(mc_path)
                            mc_dur = mc_audio.duration
                            fps = full_clip.fps if full_clip.fps else 30.0
                            intro_end = max(0, start - (1.0/fps))
                            pre_start = max(0, intro_end - mc_dur)
                            intro_part = full_clip.subclip(pre_start, intro_end).without_audio()
                            if mc_dur > intro_part.duration + 0.001:
                                freeze_len = mc_dur - intro_part.duration
                                if freeze_len > 0:
                                    freeze_clip = ImageClip(full_clip.get_frame(pre_start)).set_duration(freeze_len)
                                    intro_part = concatenate_videoclips([freeze_clip, intro_part])
                            intro_clip = intro_part.fx(vfx.colorx, 0.4).set_audio(mc_audio)
                            import moviepy.audio.fx.all as afx
                            if intro_clip.audio: intro_clip = intro_clip.set_audio(intro_clip.audio.fx(afx.audio_fadeout, 0.05))
                            if sub.audio: sub = sub.set_audio(sub.audio.fx(afx.audio_fadein, 0.1))
                            sub = concatenate_videoclips([intro_clip, sub])
                            mc_dur_total = intro_clip.duration
                            temp_tts_files.append(mc_path); temp_tts_audios.append(mc_audio)
                    except Exception as e: logger.error(f"MC 인트로 삽입 실패: {e}")
                if use_narration and seg.get('narration'):
                    narration_text = seg.get('narration')
                    if narration_text.strip():
                        try:
                            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf: tts_path = tf.name
                            success, audio_clips, subtitle_clips = False, [], []
                            current_cumulative_time = mc_dur_total + 0.5
                            if tts_engine == "gemini":
                                lines = [l.strip() for l in narration_text.split('\n') if l.strip()]
                                effect_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resource", "28. 띠링_soft.mp3")
                                for line_idx, line in enumerate(lines):
                                    if progress_callback: progress_callback(int(10 + (line_idx / max(1, len(lines))) * 80))
                                    current_voice, spoken_text = seg.get('voice', 'Charon'), line
                                    if line.startswith("[MC]:") or line.startswith("MC:"): spoken_text = line.split(":", 1)[1].strip()
                                    elif line.startswith("[패널]:") or line.startswith("패널:"): current_voice, spoken_text = seg.get('panel_voice', 'Kore'), line.split(":", 1)[1].strip()
                                    if not spoken_text: continue
                                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf_part: part_path = tf_part.name
                                    if self._generate_gemini_tts(spoken_text, part_path, voice_name=current_voice, mood=seg.get('mood'), playback_speed=playback_speed, effect_path=effect_path if line_idx == 0 else None):
                                        audio_clips.append(part_path); temp_tts_files.append(part_path)
                                        from pydub import AudioSegment
                                        part_dur = AudioSegment.from_file(part_path).duration_seconds
                                        import re, textwrap
                                        sentence_splits = re.split(r'(?<=[.!?])\s+', spoken_text)
                                        wrapped_lines = []
                                        for segment in sentence_splits:
                                            if segment.strip(): wrapped_lines.extend(textwrap.wrap(segment, width=12))
                                        sub_segment_start, total_chars = current_cumulative_time, len(spoken_text.replace(" ", ""))
                                        for w_line in wrapped_lines:
                                            line_dur = part_dur * (len(w_line.replace(" ", "")) / max(1, total_chars))
                                            s_clip = self.create_text_clip_pil(w_line, 70, 'white', 'rgba(0,0,0,0)', (1000, None), line_dur, ('center', 'center'), outline_color=(0, 0, 0, 255), shadow_color=(0, 0, 0, 200))
                                            if s_clip: s_clip = s_clip.set_start(sub_segment_start); subtitle_clips.append(s_clip)
                                            sub_segment_start += line_dur
                                        current_cumulative_time += (part_dur + 0.3)
                                if audio_clips:
                                    from pydub import AudioSegment
                                    combined = AudioSegment.empty(); silence = AudioSegment.silent(duration=300)
                                    for i, path in enumerate(audio_clips):
                                        if i > 0: combined += silence
                                        combined += AudioSegment.from_file(path)
                                    combined += AudioSegment.silent(duration=800); combined.export(tts_path, format="wav"); success = True
                            if not success:
                                gTTS(text=narration_text.replace("[MC]:", "").replace("[패널]:", ""), lang='ko').save(tts_path)
                            tts_audio = AudioFileClip(tts_path); tts_dur = tts_audio.duration
                            tts_audio_delayed = tts_audio.set_start(mc_dur_total + 0.5)
                            if sub.audio:
                                from moviepy.audio.AudioClip import CompositeAudioClip
                                sub = sub.set_audio(CompositeAudioClip([sub.audio, tts_audio_delayed]).volumex(1.5))
                            else: sub = sub.set_audio(tts_audio_delayed.volumex(1.5))
                            req_dur = mc_dur_total + 0.5 + tts_dur + 0.3
                            if req_dur > sub.duration:
                                freeze_clip = ImageClip(sub.get_frame(sub.duration - 0.01)).set_duration(req_dur - sub.duration)
                                sub = concatenate_videoclips([sub.without_audio(), freeze_clip]).set_audio(sub.audio.set_duration(req_dur))
                            elif req_dur < sub.duration:
                                sub = sub.subclip(0, min(sub.duration, req_dur + 0.2))
                                import moviepy.audio.fx.all as afx
                                if sub.audio: sub = sub.set_audio(sub.audio.fx(afx.audio_fadeout, 0.2))
                            temp_tts_files.append(tts_path); temp_tts_audios.append(tts_audio)
                        except Exception as e: logger.error(f"보이스오버 삽입 실패: {e}")
                # [수정] 전문적인 큐레이션 레이아웃 적용 (블러 배경 / 프레임 등)
                v_type = seg.get('visual_type', 'blurred_bg')
                ai_path = seg.get('ai_image_path')
                
                # 시각적 변형 레이아웃 생성
                fs_base = self._create_transformative_clip(sub, visual_type=v_type, ai_image_path=ai_path)
                final_dur = fs_base.duration
                segment_clips = [fs_base]
                if 'subtitle_clips' in locals() and subtitle_clips: segment_clips.extend(subtitle_clips)
                if use_bypass_filter: segment_clips.append(ImageClip(np.array(PIL.Image.new('RGBA', (1080, 1920), (255, 150, 0, 12)))).set_duration(final_dur).set_position(('center', 'center')))
                if title_text:
                    t_clip = self.create_title_clip_pil(title_text, final_dur, ('center', 100), title_color_mode=title_color_mode)
                    if t_clip: segment_clips.append(t_clip)
                s_text = seg.get('source_text', '')
                if s_text:
                    cap_clip = self.create_text_clip_pil(s_text, 50, 'white', 'rgba(0,0,0,0.5)', (1000, None), final_dur, ('center', 1510))
                    if cap_clip: segment_clips.append(cap_clip)
                best_c = seg.get('best_comment', '')
                if best_c and use_comment_overlay:
                    c_clip = self.create_comment_overlay_pil(best_c, final_dur, ('center', 1100))
                    if c_clip: segment_clips.append(c_clip)
                twist_c = seg.get('twist_comment', ''); twist_ts = seg.get('twist_timestamp', 0)
                if twist_c and twist_ts > 0:
                    t_ts_shorts = mc_dur_total + self._map_time_after_jumpcut((twist_ts - seg['start_time']) / playback_speed, jumpcut_kept_segs)
                    try:
                        c_path = seg.get('captured_comment_path')
                        if not c_path or not os.path.exists(c_path):
                            import hashlib
                            th = hashlib.md5(twist_c.encode()).hexdigest()
                            rp = os.path.join(os.getcwd(), "cache", "comment_captures", f"{th}.png")
                            if os.path.exists(rp): c_path = rp
                        if c_path and os.path.exists(c_path):
                            tw_clip = ImageClip(c_path).set_duration(3.0).set_position(('center', 1620)).resize(width=1060).crossfadein(0.3).crossfadeout(0.4)
                        else: tw_clip = self.create_comment_overlay_pil(twist_c, 3.0, ('center', 1620))
                        if tw_clip: tw_clip = tw_clip.set_start(t_ts_shorts); segment_clips.append(tw_clip)
                    except: pass
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf: tw_a_path = tf.name
                        if self._generate_gemini_tts(twist_c, tw_a_path, voice_name=seg.get('panel_voice', 'Kore'), mood=seg.get('mood', '깜짝 놀라며'), playback_speed=playback_speed):
                            tw_a = AudioFileClip(tw_a_path).set_start(t_ts_shorts); tw_dur = tw_a.duration
                            if sub.audio:
                                from moviepy.audio.fx.all import volumex
                                sub = sub.set_audio(sub.audio.fl(lambda gf, t: 0.3*gf(t) if t_ts_shorts<=t<=t_ts_shorts+tw_dur else gf(t)))
                                from moviepy.audio.AudioClip import CompositeAudioClip
                                sub = sub.set_audio(CompositeAudioClip([sub.audio, tw_a.volumex(1.2)]))
                            else: sub = sub.set_audio(tw_a)
                            temp_tts_files.append(tw_a_path); temp_tts_audios.append(tw_a)
                    except: pass
                safe_clips = []
                for c in segment_clips:
                    if c.start < final_dur:
                        if c.end > final_dur: c = c.set_duration(final_dur - c.start)
                        safe_clips.append(c)
                fs = CompositeVideoClip(safe_clips, size=(1080, 1920)).set_duration(final_dur)
                if fs.audio:
                    import moviepy.audio.fx.all as afx
                    fs = fs.set_audio(fs.audio.fx(afx.audio_fadeout, 0.5))
                processed_segments.append(fs)
            final_v = concatenate_videoclips(processed_segments, method="compose")
            if final_v.audio:
                import moviepy.audio.fx.all as afx
                final_v.audio.fps = 44100
                final_v.audio = afx.audio_normalize(final_v.audio)
                final_v.audio = afx.audio_fadeout(final_v.audio, 0.5)
            final_v.write_videofile(output_path, codec='libx264', audio_codec='aac', temp_audiofile='temp-audio.m4a', remove_temp=True, fps=30, threads=4, preset='ultrafast', logger=UILogger(progress_callback) if progress_callback else "bar")
            full_clip.close()
            for s in processed_segments: s.close()
            for c in temp_tts_audios: c.close()
            for f in temp_tts_files:
                if os.path.exists(f):
                    dd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "downloads", "debug_audio")
                    os.makedirs(dd, exist_ok=True)
                    try: shutil.copy(f, os.path.join(dd, os.path.basename(f)))
                    except: pass
            return output_path
        except Exception as e:
            logger.error(f"쇼츠 합성 실패: {e}")
            return None

    def export_to_capcut(self, video_path, segments, project_name="Autogen_Project", title=None, source=None, subtitles=None, tts_path=None, bgm_path=None, video_clips=None, subtitle_position="중단", channel_watermark=None):
        import uuid, json, shutil, copy
        from datetime import datetime
        # 1. 경로 설정 (독립형 앱 구조)
        capcut_link_path = os.path.join(APP_ROOT, "capcut_link")
        if not os.path.exists(capcut_link_path):
            # 링크가 없으면 상위 폴더(shorts_bot)의 링크 시도 또는 현재 폴더에 생성
            parent_link = os.path.join(os.path.dirname(APP_ROOT), "capcut_link")
            capcut_link_path = parent_link if os.path.exists(parent_link) else capcut_link_path
            
        template_path = os.path.join(APP_ROOT, "resources", "templates", "capcut")
        if not os.path.exists(template_path): return None
        new_draft_id = str(uuid.uuid4()).upper()
        if not project_name: project_name = f"ShortsBot_{datetime.now().strftime('%m%d_%H%M')}"
        target_dir = os.path.join(capcut_link_path, project_name); os.makedirs(target_dir, exist_ok=True)
        try:
            with open(os.path.join(template_path, "draft_info.json"), "r", encoding="utf-8") as f: info = json.load(f)
            with open(os.path.join(template_path, "draft_meta_info.json"), "r", encoding="utf-8") as f: meta = json.load(f)
            # 프로젝트 초기화 (기존 템플릿의 잔여 미디어 제거)
            info["materials"]["videos"] = []
            info["materials"]["photos"] = []
            meta["draft_materials"] = [{"type": 0, "value": []}, {"type": 255, "value": []}]
            
            info["id"], video_mat_id, local_mat_id = new_draft_id, str(uuid.uuid4()).upper(), str(uuid.uuid4()).lower()
            def to_us(s): return int(s * 1000000)
            video_track = info["tracks"][0]; video_track["id"] = str(uuid.uuid4()).upper()

            if video_clips:
                # ── 신규: 클립별 별도 material 방식 (싱크 완벽) ─────────────────
                info["materials"]["videos"] = []
                seg_template = info["tracks"][0]["segments"][0]
                new_segments, current_t = [], 0
                for i, (clip_path, clip_dur_s) in enumerate(video_clips):
                    c_id = str(uuid.uuid4()).upper()
                    dest_c = os.path.join(target_dir, os.path.basename(clip_path))
                    shutil.copy2(clip_path, dest_c)
                    info["materials"]["videos"].append({
                        "id": c_id,
                        "path": os.path.realpath(dest_c),
                        "local_material_id": str(uuid.uuid4()).lower(),
                        "material_name": os.path.basename(clip_path),
                        "type": "video",
                        "width": 0, "height": 0, "duration": to_us(clip_dur_s),
                        "extra_info": "", "import_time": 0, "import_time_ms": 0,
                        "media_type": 1, "category_id": "", "category_name": "",
                        "check_flag": 0, "create_time": 0, "metetype": "photo",
                        "roughcut_time_range": {"duration": to_us(clip_dur_s), "start": 0},
                        "sub_type": 0, "formula_id": "",
                        "stable": {"matrix_x": 0.0, "matrix_y": 0.0, "time_range": {"duration": 0, "start": 0}},
                        "team_id": "", "source_platform": 0, "audio_fade": None
                    })
                    ns = copy.deepcopy(seg_template)
                    ns["id"] = str(uuid.uuid4()).upper()
                    ns["material_id"] = c_id
                    ns["source_timerange"] = {"duration": to_us(clip_dur_s), "start": 0}
                    ns["target_timerange"] = {"duration": to_us(clip_dur_s), "start": current_t}
                    # 9:16 세로 영상 확대 (실제 CapCut JSON 구조 기반)
                    ns["clip"] = {
                        "alpha": 1.0,
                        "flip": {"horizontal": False, "vertical": False},
                        "rotation": 0.0,
                        "scale": {"x": 1.5, "y": 1.5},
                        "transform": {"x": 0.0, "y": 0.0}
                    }
                    ns["volume"] = 0.0  # 원본 오디오 완전 소거
                    ns["speed"] = 1.0
                    ns["reverse"] = False
                    ns["visible"] = True
                    # uniform_scale: setdefault 대신 강제 할당 (템플릿에 이미 파일이 있어도 1.5로 덮어쓰기)
                    ns["uniform_scale"] = {"on": True, "value": 1.5}
                    new_segments.append(ns)
                    current_t += to_us(clip_dur_s)
            else:
                # ── 원본 영상 + source_timerange 방식 ──────────────────────────────────
                import subprocess as _sp
                video_filename = os.path.basename(video_path)
                dest_video_path = os.path.join(target_dir, video_filename)
                
                # Mac OS 캡컷 샌드박스 이슈(미디어 연결 끊김)를 방지하기 위해 드래프트 폴더 내 필수 복사
                shutil.copy2(video_path, dest_video_path)
                dest_video_path = os.path.realpath(dest_video_path)

                # ★ ffprobe로 실제 영상 길이/해상도 감지 (0으로 두면 CapCut이 1초로 인식하는 치명적 버그)
                video_dur_us, video_w, video_h = 0, 0, 0
                try:
                    _probe = _sp.run(
                        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", dest_video_path],
                        capture_output=True, text=True
                    )
                    for _s in json.loads(_probe.stdout).get("streams", []):
                        if "duration" in _s and video_dur_us == 0:
                            video_dur_us = to_us(float(_s["duration"]))
                        if _s.get("width") and video_w == 0:
                            video_w, video_h = int(_s["width"]), int(_s.get("height", 0))
                except Exception as _e:
                    print(f"   ⚠ ffprobe 실패: {_e}")

                v_mat = {
                    "id": video_mat_id,
                    "local_material_id": local_mat_id,
                    "path": dest_video_path,
                    "material_name": video_filename,
                    "type": "video",
                    "width": int(video_w),
                    "height": int(video_h),
                    "duration": int(video_dur_us),
                    "category_id": "", "category_name": "local",
                    "check_flag": 62978047, "formula_id": "",
                    "source": 0, "source_platform": 0,
                    "stable": {"matrix_path": "", "stable_level": 0, "time_range": {"duration": 0, "start": 0}},
                    "crop": {"lower_left_x": 0.0, "lower_left_y": 1.0, "lower_right_x": 1.0, "lower_right_y": 1.0, "upper_left_x": 0.0, "upper_left_y": 0.0, "upper_right_x": 1.0, "upper_right_y": 0.0},
                    "crop_ratio": "original", "crop_scale": 1.0,
                    "video_algorithm": {"ai_background_configs": [], "aigc_generate_list": [], "algorithms": [], "gameplay_configs": [], "skip_algorithm_index": []}
                }
                info["materials"]["videos"] = [v_mat]
                seg_template = video_track["segments"][0] # ★ 캡컷이 미리 만들어둔 완벽한 템플릿 세그먼트
                new_segments, current_t = [], 0
                for seg in segments:
                    s_dur, s_start = to_us(seg['duration']), to_us(seg['start_time'])
                    # ★ 템플릿 deepcopy (모든 필수 필드/참조 유지)
                    ns = copy.deepcopy(seg_template)
                    ns["id"] = str(uuid.uuid4()).upper()
                    ns["material_id"] = video_mat_id
                    ns["source_timerange"] = {"duration": s_dur, "start": s_start}
                    ns["target_timerange"] = {"duration": s_dur, "start": current_t}
                    ns["render_timerange"] = {"duration": s_dur, "start": current_t}
                    
                    # 확대/축소 및 볼륨 설정
                    ns["clip"]["scale"] = {"x": 1.5, "y": 1.5} # 1.5배 확대
                    ns["uniform_scale"]["value"] = 1.0
                    ns["volume"] = 0.0 # 원본 영상 소리는 유튜브 쇼츠에서 나레이션 방해를 막기 위해 완전 음소거 처리
                    ns["enable_adjust"] = True
                    
                    new_segments.append(ns)
                    current_t += s_dur






            info["tracks"][0]["segments"] = new_segments; info["tracks"][0]["id"] = str(uuid.uuid4()).upper(); info["duration"] = current_t

            # --- Audio Tracks (TTS & BGM) ---
            audio_mats = []
            audio_tracks = []
            if "audios" not in info["materials"]: info["materials"]["audios"] = []
            
            def add_audio_track(a_path, vol=1.0, start_us=0, src_dur_us=None, fade_in_us=0, fade_out_us=0):
                """오디오 트랙 추가. src_dur_us=None이면 전체 타임라인 길이(BGM/TTS용), 아니면 지정 길이(SFX용)"""
                if not a_path or not os.path.exists(a_path): return
                a_id = str(uuid.uuid4()).upper()
                a_name = os.path.basename(a_path)
                # SFX 파일명 충돌 방지 (동일 파일이 여러 beat에서 쓰일 수 있음)
                dest_a_name = f"{a_id[:8]}_{a_name}"
                dest_a = os.path.join(target_dir, "media", dest_a_name)
                os.makedirs(os.path.dirname(dest_a), exist_ok=True)
                shutil.copy2(a_path, dest_a)

                tgt_dur_us = src_dur_us if src_dur_us is not None else current_t
                actual_src_dur = src_dur_us if src_dur_us is not None else current_t

                info["materials"]["audios"].append({
                    "id": a_id,
                    "path": str(dest_a),
                    "material_name": dest_a_name,
                    "type": "audio",
                    "local_material_id": ""
                })
                seg = {
                    "id": str(uuid.uuid4()).upper(),
                    "material_id": a_id,
                    "source_timerange": {"duration": actual_src_dur, "start": 0},
                    "target_timerange": {"duration": tgt_dur_us, "start": start_us},
                    "clip": {"alpha": 1.0, "scale": {"x": 1.0, "y": 1.0}, "transform": {"x": 0.0, "y": 0.0}},
                    "render_index": 0, "reverse": False, "speed": 1.0,
                    "visible": True, "volume": vol,
                    "audio_fade": {
                        "fade_in_duration": fade_in_us,
                        "fade_out_duration": fade_out_us
                    }
                }
                audio_tracks.append({
                    "id": str(uuid.uuid4()).upper(),
                    "type": "audio",
                    "attribute": 0, "flag": 0,
                    "segments": [seg]
                })

            add_audio_track(tts_path, vol=1.0)
            add_audio_track(bgm_path, vol=0.03) # BGM 볼륨 극단적 하향 (쇼츠 나레이션 강조)

            # ★ SFX 트랙: beat별 효과음을 타임라인 정확한 시점에 삽입
            import subprocess as _sp2
            for seg in segments:
                sfx_p = seg.get('sfx_path')
                tl_start = seg.get('timeline_start', 0.0)
                if sfx_p and os.path.exists(sfx_p):
                    # SFX 파일 실제 길이 감지
                    sfx_dur_us = 2_000_000  # 기본 2초
                    try:
                        _r = _sp2.run(
                            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", sfx_p],
                            capture_output=True, text=True
                        )
                        for _s in json.loads(_r.stdout).get("streams", []):
                            if "duration" in _s:
                                sfx_dur_us = to_us(float(_s["duration"]))
                                break
                    except: pass
                    # 볼륨 낮추고 페이드 0.5초 적용 (성욱님 요청: SFX도 튀지 않게 0.08 수준으로 하향)
                    add_audio_track(sfx_p, vol=0.08, start_us=to_us(tl_start), src_dur_us=sfx_dur_us, fade_in_us=500000, fade_out_us=500000)

            info["tracks"].extend(audio_tracks)

            # 텍스트 트랙 추가 (제목 / 출처)
            def _make_text_material(text_str, color, font_path, font_size, bold, has_shadow=True, global_alpha=1.0):
                tid = str(uuid.uuid4()).upper()
                # 그림자 설정 (사용자 요청: _2224 프로젝트 참고)
                shadow_obj = {
                    "thickness_projection_distance": 0, "thickness_projection_angle": -45,
                    "diffuse": 0.0833, "distance": 0,
                    "content": {"solid": {"color": [0, 0, 0]}, "render_type": "solid"},
                    "thickness_projection_enable": False, "angle": 0
                }
                
                content_dict = {
                    "styles": [{
                        "fill": {"content": {"solid": {"color": [
                            int(color[1:3], 16) / 255, int(color[3:5], 16) / 255, int(color[5:7], 16) / 255
                        ]}, "render_type": "solid"}},
                        "range": [0, len(text_str)],
                        "size": font_size,
                        "bold": bold,
                        "font": {"path": font_path, "id": ""},
                        "shadows": [shadow_obj] if has_shadow else []
                    }],
                    "text": text_str
                }
                content = json.dumps(content_dict, ensure_ascii=False)
                
                return {"id": tid, "type": "text", "content": content,
                    "font_path": font_path, "font_size": float(font_size),
                    "text_color": color, "alignment": 1, "bold_width": 0.008 if bold else 0.0,
                    "global_alpha": global_alpha,
                    "has_shadow": has_shadow, "initial_scale": 1.0,
                    "is_rich_text": True, "layer_weight": 1, "line_feed": 1,
                    "line_max_width": 0.82, "line_spacing": 0.02,
                    "shadow_alpha": 0.9, "shadow_angle": -45.0, "shadow_color": "#000000",
                    "shadow_distance": 5.0, "shadow_point": {"x": 0.636, "y": -0.636},
                    "shadow_smoothing": 0.45, "text_alpha": 1.0,
                    "text_size": 30, "typesetting": 0, "underline": False,
                    "words": {"end_time": [], "start_time": [], "text": []}}

            def _make_text_track(mat_id, duration, transform_y, scale_xy):
                seg = {"id": str(uuid.uuid4()).upper(), "material_id": mat_id,
                    "target_timerange": {"duration": duration, "start": 0},
                    "clip": {"alpha": 1.0, "scale": {"x": scale_xy, "y": scale_xy},
                        "transform": {"x": 0.0, "y": transform_y}},
                    "render_index": 0, "reverse": False, "speed": 1.0,
                    "visible": True, "volume": 1.0}
                return {"id": str(uuid.uuid4()).upper(), "type": "text",
                    "attribute": 0, "flag": 0, "segments": [seg]}

            # [폰트 전면 수정] 성욱님 시스템의 잘난체 경로를 절대 고정
            JALNAN = "/Users/chris/Library/Containers/com.lemon.lvoverseas/Data/Library/Fonts/Jalnan2TTF.ttf"
            if not os.path.exists(JALNAN):
                # 시스템에 없으면 앱 내부 폰트 사용
                JALNAN = os.path.abspath(os.path.join(APP_ROOT, "resources", "fonts", "Jalnan2TTF.ttf"))
            
            CAPCUT_EN = "/Applications/CapCut.app/Contents/Resources/Font/SystemFont/en.ttf"
            
            text_mats = []
            text_tracks = []
            if title:
                # 제목 자동 줄바꿈: 가장 가운데에 위치한 공백(띄어쓰기)을 기준으로 정확히 2줄로 분리 (가독성 극대화)
                display_title = title
                words = title.split()
                if len(words) >= 2:
                    mid_idx = len(words) // 2
                    # 앞줄과 뒷줄의 길이 밸런스를 최대한 맞춤
                    display_title = " ".join(words[:mid_idx]) + "\n" + " ".join(words[mid_idx:])

                # 폰트: JALNAN, 그림자 포함. 
                # (스크린샷처럼 쇼츠 화면에 큼직하고 빵빵하게 찰 수 있도록 사이즈 14에 스케일 1.45 적용)
                tm = _make_text_material(display_title, "#f1f503", JALNAN, 14, True, has_shadow=True)
                text_mats.append(tm)
                text_tracks.append(_make_text_track(tm["id"], current_t, 0.6667, 1.45))
                
            # 채널명 워터마크 (반투명, 영상 중앙) — CapCut UI "불투명도 20" 기준
            if channel_watermark:
                _WM_ALPHA = 0.2 ** 0.75  # ≈ 0.299, CapCut UI 불투명도 20% 적용
                wm = _make_text_material(channel_watermark, "#FFFFFF", JALNAN, 10, False, has_shadow=False, global_alpha=_WM_ALPHA)
                text_mats.append(wm)
                text_tracks.append(_make_text_track(wm["id"], current_t, 0.0, 0.7))

            if source:
                sm = _make_text_material(source, "#FFFFFF", JALNAN, 10, False, has_shadow=True)
                text_mats.append(sm)
                # Y = -1067px / 1920 = -0.5557... (캡컷 프로젝트 실측값)
                text_tracks.append(_make_text_track(sm["id"], current_t, -1067/1920, 0.70))

            if subtitles:
                sub_y = -761/1920 if subtitle_position == "하단" else -0.075
                sub_segs = []
                for sub in subtitles:
                    # 자막도 잘난체 + 그림자 적용, 폰트 크기 12로 상향 (사용자 요청)
                    sm = _make_text_material(sub['text'], "#FFFFFF", JALNAN, 12, False, has_shadow=True)
                    text_mats.append(sm)
                    seg = {"id": str(uuid.uuid4()).upper(), "material_id": sm["id"],
                        "target_timerange": {"duration": sub['duration_us'], "start": sub['start_us']},
                        "clip": {"alpha": 1.0, "scale": {"x": 1.0, "y": 1.0},
                            "transform": {"x": 0.0, "y": sub_y}},
                        "render_index": 0, "reverse": False, "speed": 1.0,
                        "visible": True, "volume": 1.0}
                    sub_segs.append(seg)
                sub_track = {"id": str(uuid.uuid4()).upper(), "type": "text",
                    "attribute": 0, "flag": 0, "segments": sub_segs}
                text_tracks.append(sub_track)

            if text_mats:
                info["materials"]["texts"] = text_mats
                info["tracks"] = info["tracks"] + text_tracks

            # 타임스탬프 업데이트 (유닉스 마이크로초 단위)
            now_us = int(time.time() * 1000000)
            
            # draft_info.json 업데이트 (루트 필드)
            info["create_time"] = now_us
            info["update_time"] = now_us
            
            # draft_meta_info.json 업데이트 (주요 필드 반영)
            meta["draft_id"] = new_draft_id
            meta["draft_name"] = project_name
            meta["tm_duration"] = current_t
            
            # Mac 버전 캡컷에서 사용하는 실제 필드명 (tm_ 접두사)
            meta["tm_draft_create"] = now_us
            meta["tm_draft_modified"] = now_us
            # 호환성을 위한 추가 필드명
            meta["draft_create_time"] = now_us
            meta["draft_updated_time"] = now_us
            meta["draft_access_time"] = now_us

            if not video_clips and dest_video_path and video_filename:
                m_info = {
                    "id": local_mat_id,
                    "file_Path": dest_video_path,
                    "extra_info": video_filename,
                    "import_time": int(now_us / 1000000), # 초 단위
                    "import_time_ms": int(now_us / 1000), # 밀리초 단위
                    "create_time": int(now_us / 1000000), # 초 단위
                    "type": 0,
                    "metetype": "photo"
                }
                meta["draft_materials"][0]["value"] = [m_info]

            with open(os.path.join(target_dir, "draft_info.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=2)
            with open(os.path.join(target_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            return target_dir
        except Exception as e:
            import traceback
            print(f"\n   ❌ CapCut export 실패: {e}")
            traceback.print_exc()
            logger.error(f"CapCut export 실패: {e}")
            return None

    def _load_voices(self):
        voice_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'gemini_voices.json')
        try:
            if os.path.exists(voice_file):
                with open(voice_file, 'r', encoding='utf-8') as f: return json.load(f)
        except: pass
        return []

    def get_voice_info(self, voice_name=None):
        voices = self._load_voices()
        if voice_name:
            for v in voices:
                if v['name'].lower() == voice_name.lower(): return v
            return None
        return voices

    def _generate_gemini_tts(self, text: str, output_path: str, voice_name: str = "Charon", mood: str = None, playback_speed: float = 1.0, effect_path: str = None) -> bool:
        from google import genai; from google.genai import types; from config import GEMINI_API_KEY
        try: from pydub import AudioSegment, effects; import io, re
        except: return False
        try:
            cue_match = re.match(r'^\s*[\(\[（【]([^)\]）】]+)[\)\]）】]\s*(.*)', text, re.DOTALL)
            if cue_match: text = cue_match.group(2).strip()
            safe_text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            full_content = f'<speak><prosody rate="{f"{int(round(playback_speed*100))}%"}">{safe_text}</prosody></speak>' if playback_speed != 1.0 else safe_text
            # print(f"   [DEBUG] TTS Voice: {voice_name}")
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(model="gemini-2.5-flash-preview-tts", contents=full_content, config=types.GenerateContentConfig(response_modalities=["AUDIO"], speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)))))
            audio_data = b""
            if hasattr(response, "candidates") and response.candidates:
                for part in response.candidates[0].content.parts:
                    if part.inline_data: audio_data += part.inline_data.data
            if not audio_data: return False
            audio = AudioSegment.from_raw(io.BytesIO(audio_data), sample_width=2, frame_rate=24000, channels=1).high_pass_filter(100).set_channels(2)
            if effect_path and os.path.exists(effect_path):
                try: audio = audio.overlay((AudioSegment.from_file(effect_path).set_frame_rate(audio.frame_rate).set_channels(2)) - 8, position=0)
                except: pass
            audio = effects.normalize(audio, headroom=0.1).fade_in(50).fade_out(60).set_frame_rate(44100)
            ext = output_path.split('.')[-1].lower()
            if ext == "wav": audio.export(output_path, format="wav")
            else: audio.export(output_path, format="mp3", bitrate="192k")
            return True
        except: return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    p = VideoProcessor()
