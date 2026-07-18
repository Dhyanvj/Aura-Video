import unittest
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

# add project root to python path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from app.services import task as tm
from app.models.schema import MaterialInfo, VideoParams
from app.utils import utils

resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resources")
RUN_INTEGRATION_TESTS = os.environ.get("MPT_RUN_INTEGRATION_TESTS", "").lower() in {
    "1",
    "true",
    "yes",
}

class TestTaskService(unittest.TestCase):
    def setUp(self):
        pass
    
    def tearDown(self):
        pass

    def test_generate_script_forwards_advanced_prompt_options(self):
        """
        任务生成入口和 WebUI/API 共用 VideoParams。这里验证自动生成文案时，
        高级提示词参数会继续传到 LLM 服务层，避免只在 /scripts 接口生效。
        """
        params = VideoParams(
            video_subject="咖啡",
            video_script="",
            video_language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

        with patch.object(tm.llm, "generate_script", return_value="生成的文案") as generate:
            result = tm.generate_script("task-id", params)

        self.assertEqual(result, "生成的文案")
        generate.assert_called_once_with(
            video_subject="咖啡",
            language="zh-CN",
            paragraph_number=2,
            video_script_prompt="语气轻松",
            custom_system_prompt="Only write short narration.",
        )

    def test_generate_terms_uses_script_order_mode_when_enabled(self):
        """
        默认模式不受影响；只有用户显式开启素材按文案顺序匹配时，任务层才
        要求 LLM 生成有序关键词，并适当增加关键词数量以覆盖更多脚本片段。
        """
        params = VideoParams(
            video_subject="城市通勤",
            video_script="",
            match_materials_to_script=True,
        )

        with patch.object(tm.llm, "generate_terms", return_value=["city", "train"]) as generate:
            result = tm.generate_terms("task-id", params, "先城市，再地铁")

        self.assertEqual(result, ["city", "train"])
        generate.assert_called_once_with(
            video_subject="城市通勤",
            video_script="先城市，再地铁",
            amount=8,
            match_script_order=True,
        )
    
    def test_generate_audio_uses_custom_file_inside_task_directory(self):
        task_id = "test-custom-audio-safe"
        task_dir = utils.task_dir(task_id)
        custom_audio_file = os.path.join(task_dir, "custom-audio.mp3")
        with open(custom_audio_file, "wb") as audio:
            audio.write(b"fake audio")

        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=custom_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.voice, "get_audio_duration", return_value=7),
                patch.object(tm.audio_analysis, "check_audible", return_value=(True, "")),
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(custom_audio_file))
        self.assertEqual(audio_duration, 7)
        self.assertIsNone(sub_maker)
        tts.assert_not_called()

    def test_generate_audio_accepts_server_side_custom_file(self):
        task_id = "test-custom-audio-server-side"
        task_dir = utils.task_dir(task_id)

        with tempfile.NamedTemporaryFile(suffix=".mp3") as server_audio:
            server_audio.write(b"fake audio")
            server_audio.flush()
            params = VideoParams(
                video_subject="custom audio",
                video_script="",
                custom_audio_file=server_audio.name,
                voice_name="test-voice",
            )

            try:
                with (
                    patch.object(tm.voice, "tts") as tts,
                    patch.object(tm.voice, "get_audio_duration", return_value=6),
                    patch.object(tm.audio_analysis, "check_audible", return_value=(True, "")),
                ):
                    audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                        task_id, params, "script"
                    )
            finally:
                shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_file, os.path.realpath(server_audio.name))
        self.assertEqual(audio_duration, 6)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()

    def test_generate_audio_rejects_missing_custom_file_without_tts(self):
        task_id = "test-custom-audio-missing"
        task_dir = utils.task_dir(task_id)
        missing_audio_file = os.path.join(task_dir, "missing.mp3")
        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=missing_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                audio_file, audio_duration, result_sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertIsNone(audio_file)
        self.assertIsNone(audio_duration)
        self.assertIsNone(result_sub_maker)
        tts.assert_not_called()
        update_task.assert_called_with(task_id, state=tm.const.TASK_STATE_FAILED)

    def test_generate_audio_fails_loudly_when_tts_audio_is_unusable(self):
        """
        Root-cause regression: a TTS provider can report success (a non-None
        SubMaker with valid word/sentence-boundary metadata) while the actual
        audio payload is missing, silent, or truncated. generate_audio() must
        validate the real file and hard-fail with an actionable reason rather
        than trust the provider's metadata and proceed.
        """
        task_id = "test-tts-unusable-audio"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(video_subject="x", video_script="", voice_name="test-voice")

        try:
            with (
                patch.object(tm.voice, "tts", return_value=object()) as tts,
                patch.object(
                    tm.audio_analysis,
                    "check_audible",
                    return_value=(False, "audio is effectively silent (mean volume -80.0 dB, threshold -50 dB)"),
                ),
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertIsNone(audio_file)
        self.assertIsNone(audio_duration)
        self.assertIsNone(sub_maker)
        tts.assert_called_once()
        _, kwargs = update_task.call_args
        self.assertEqual(kwargs["state"], tm.const.TASK_STATE_FAILED)
        self.assertIn("silent", kwargs["failure_reason"])

    def test_generate_audio_uses_real_measured_duration_not_submaker_metadata(self):
        """
        audio_duration must come from probing the actual file, not from the
        SubMaker's word-boundary metadata - the two can diverge exactly when
        the provider misbehaves, which is the scenario this whole fix exists
        to catch.
        """
        task_id = "test-tts-real-duration"
        task_dir = utils.task_dir(task_id)
        params = VideoParams(video_subject="x", video_script="", voice_name="test-voice")

        try:
            with (
                patch.object(tm.voice, "tts", return_value=object()),
                patch.object(tm.audio_analysis, "check_audible", return_value=(True, "")),
                patch.object(tm.audio_analysis, "probe_audio_duration", return_value=42.4),
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(audio_duration, 43)  # math.ceil(42.4)
        self.assertIsNotNone(sub_maker)

    def test_generate_audio_rejects_unusable_custom_audio_file(self):
        task_id = "test-custom-audio-unusable"
        task_dir = utils.task_dir(task_id)
        custom_audio_file = os.path.join(task_dir, "custom-audio.mp3")
        with open(custom_audio_file, "wb") as audio:
            audio.write(b"fake audio")

        params = VideoParams(
            video_subject="custom audio",
            video_script="",
            custom_audio_file=custom_audio_file,
            voice_name="test-voice",
        )

        try:
            with (
                patch.object(tm.voice, "tts") as tts,
                patch.object(tm.voice, "get_audio_duration", return_value=7),
                patch.object(
                    tm.audio_analysis,
                    "check_audible",
                    return_value=(False, "audio duration is 0.00s (expected at least 1s)"),
                ),
                patch.object(tm.sm.state, "update_task") as update_task,
            ):
                audio_file, audio_duration, sub_maker = tm.generate_audio(
                    task_id, params, "script"
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertIsNone(audio_file)
        self.assertIsNone(audio_duration)
        self.assertIsNone(sub_maker)
        tts.assert_not_called()
        _, kwargs = update_task.call_args
        self.assertEqual(kwargs["state"], tm.const.TASK_STATE_FAILED)
        self.assertIn("Custom audio file is unusable", kwargs["failure_reason"])

    def test_generate_subtitle_uses_whisper_for_custom_audio_without_sub_maker(self):
        """
        自定义音频不会经过 TTS，所以没有 sub_maker。
        Whisper 可以直接从音频文件转写，此时不能被 sub_maker 为空的保护逻辑提前跳过。
        """
        task_id = "test-custom-audio-whisper-subtitle"
        task_dir = utils.task_dir(task_id)
        audio_file = os.path.join(task_dir, "custom-audio.mp3")
        Path(audio_file).write_bytes(b"fake audio")
        params = VideoParams(
            video_subject="custom audio",
            video_script="Hello world.",
            subtitle_enabled=True,
        )

        def fake_whisper_create(audio_file, subtitle_file):
            Path(subtitle_file).write_text(
                "1\n00:00:00,000 --> 00:00:01,000\nHello world.\n\n",
                encoding="utf-8",
            )

        try:
            with (
                patch.object(
                    tm.config,
                    "app",
                    dict(tm.config.app, subtitle_provider="whisper"),
                ),
                patch.object(
                    tm.subtitle, "create", side_effect=fake_whisper_create
                ) as create,
                patch.object(tm.subtitle, "correct") as correct,
            ):
                subtitle_path = tm.generate_subtitle(
                    task_id=task_id,
                    params=params,
                    video_script="Hello world.",
                    sub_maker=None,
                    audio_file=audio_file,
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertTrue(subtitle_path.endswith("subtitle.srt"))
        create.assert_called_once_with(audio_file=audio_file, subtitle_file=subtitle_path)
        correct.assert_called_once_with(
            subtitle_file=subtitle_path, video_script="Hello world."
        )

    def test_generate_subtitle_skips_edge_provider_without_sub_maker(self):
        """
        Edge 字幕依赖 TTS 返回的 sub_maker 时间轴。
        自定义音频缺少该对象时应继续跳过，避免产生不可信的字幕时间轴。
        """
        task_id = "test-custom-audio-edge-no-submaker"
        task_dir = utils.task_dir(task_id)
        audio_file = os.path.join(task_dir, "custom-audio.mp3")
        Path(audio_file).write_bytes(b"fake audio")
        params = VideoParams(
            video_subject="custom audio",
            video_script="Hello world.",
            subtitle_enabled=True,
        )

        try:
            with (
                patch.object(
                    tm.config,
                    "app",
                    dict(tm.config.app, subtitle_provider="edge"),
                ),
                patch.object(tm.voice, "create_subtitle") as create_subtitle,
                patch.object(tm.subtitle, "create") as whisper_create,
            ):
                subtitle_path = tm.generate_subtitle(
                    task_id=task_id,
                    params=params,
                    video_script="Hello world.",
                    sub_maker=None,
                    audio_file=audio_file,
                )
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)

        self.assertEqual(subtitle_path, "")
        create_subtitle.assert_not_called()
        whisper_create.assert_not_called()

    def test_get_video_materials_remote_source_returns_paths_and_metadata_as_separate_values(self):
        # Regression: a real incident had the dict-shaped clip metadata
        # (search_term/provider/url/local_path) silently clobbered under the
        # same "materials" task-state key as the plain downloaded-path list,
        # because MemoryState.update_task() replaces rather than merges (see
        # get_video_materials' own docstring). storyboard.record_clips() then
        # crashed on 'str' object has no attribute 'get'. get_video_materials
        # must hand back the paths and the dict metadata as two distinct
        # values so nothing downstream can conflate them again.
        params = VideoParams(video_subject="t", video_source="pexels", match_materials_to_script=True)

        def fake_download_videos(**kwargs):
            kwargs["metadata_out"].append(
                {"search_term": "a", "provider": "pexels", "url": "https://x/a.mp4", "local_path": "/tmp/a.mp4"}
            )
            return ["/tmp/a.mp4"]

        with patch.object(tm.material, "download_videos", side_effect=fake_download_videos):
            paths, clip_metadata = tm.get_video_materials("task-x", params, ["a"], 10.0)

        self.assertEqual(paths, ["/tmp/a.mp4"])
        self.assertTrue(all(isinstance(c, dict) for c in clip_metadata))
        self.assertEqual(
            clip_metadata,
            [{"search_term": "a", "provider": "pexels", "url": "https://x/a.mp4", "local_path": "/tmp/a.mp4"}],
        )

    def test_get_video_materials_local_source_returns_paths_and_metadata_as_separate_values(self):
        material_a = MaterialInfo(provider="local", url="/tmp/1.png", duration=0)
        params = VideoParams(video_subject="t", video_source="local", video_materials=[material_a])

        with patch.object(tm.video, "preprocess_video", return_value=[material_a]):
            paths, clip_metadata = tm.get_video_materials("task-y", params, [], 10.0)

        self.assertEqual(paths, ["/tmp/1.png"])
        self.assertTrue(all(isinstance(c, dict) for c in clip_metadata))
        self.assertEqual(
            clip_metadata, [{"search_term": "", "provider": "local", "url": "/tmp/1.png", "local_path": "/tmp/1.png"}]
        )

    @unittest.skipUnless(
        RUN_INTEGRATION_TESTS,
        "MPT_RUN_INTEGRATION_TESTS not set",
    )
    def test_task_local_materials(self):
        task_id = "00000000-0000-0000-0000-000000000000"
        video_materials=[]
        for i in range(1, 4):
            video_materials.append(MaterialInfo(
                provider="local",
                url=os.path.join(resources_dir, f"{i}.png"),
                duration=0
            ))

        params = VideoParams(
            video_subject="金钱的作用",
            video_script="金钱不仅是交换媒介，更是社会资源的分配工具。它能满足基本生存需求，如食物和住房，也能提供教育、医疗等提升生活品质的机会。拥有足够的金钱意味着更多选择权，比如职业自由或创业可能。但金钱的作用也有边界，它无法直接购买幸福、健康或真诚的人际关系。过度追逐财富可能导致价值观扭曲，忽视精神层面的需求。理想的状态是理性看待金钱，将其作为实现目标的工具而非终极目的。",
            video_terms="money importance, wealth and society, financial freedom, money and happiness, role of money",
            video_aspect="9:16",
            video_concat_mode="random",
            video_transition_mode="None",
            video_clip_duration=3,
            video_count=1,
            video_source="local",
            video_materials=video_materials,
            video_language="",
            voice_name="zh-CN-XiaoxiaoNeural-Female",
            voice_volume=1.0,
            voice_rate=1.0,
            bgm_type="random",
            bgm_file="",
            bgm_volume=0.2,
            subtitle_enabled=True,
            subtitle_position="bottom",
            custom_position=70.0,
            font_name="MicrosoftYaHeiBold.ttc",
            text_fore_color="#FFFFFF",
            text_background_color=True,
            font_size=60,
            stroke_color="#000000",
            stroke_width=1.5,
            n_threads=2,
            paragraph_number=1
        )
        result = tm.start(task_id=task_id, params=params)
        print(result)
    

if __name__ == "__main__":
    unittest.main()
