import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import Mock, patch

import jarvis
from jarvis_core.discord_voice import (
    DiscordVoiceCommandSink,
    match_discord_voice_command,
    match_whisper_chat_command,
    match_whisper_voice_command,
    starts_with_jarvis_wake_phrase,
)


class DiscordVoiceChannelCommandTests(unittest.IsolatedAsyncioTestCase):
    def make_message(self, *, voice_channel=None, voice_client=None, guild=True):
        author = SimpleNamespace(
            voice=(
                SimpleNamespace(channel=voice_channel)
                if voice_channel is not None else None
            )
        )
        return SimpleNamespace(
            author=author,
            guild=(
                SimpleNamespace(voice_client=voice_client) if guild else None
            ),
            reply=AsyncMock(),
        )

    async def test_join_connects_to_owner_current_voice_channel(self):
        channel = SimpleNamespace(id=10, name="Phòng Jarvis", connect=AsyncMock())
        message = self.make_message(voice_channel=channel)

        handled = await jarvis.handle_discord_voice_channel_command(
            message, "!join"
        )

        self.assertTrue(handled)
        channel.connect.assert_awaited_once_with()
        self.assertIn("đã vào", message.reply.await_args.args[0])

    async def test_join_requires_owner_to_be_in_voice(self):
        message = self.make_message()

        handled = await jarvis.handle_discord_voice_channel_command(
            message, "!join"
        )

        self.assertTrue(handled)
        self.assertIn("vào một kênh thoại", message.reply.await_args.args[0])

    async def test_join_uses_configured_channel_when_owner_voice_state_missing(self):
        channel = SimpleNamespace(id=20, name="Jarvis Voice", connect=AsyncMock())
        message = self.make_message()
        message.guild.get_channel = unittest.mock.Mock(return_value=channel)

        handled = await jarvis.handle_discord_voice_channel_command(
            message, "!join", configured_voice_channel_id=20
        )

        self.assertTrue(handled)
        message.guild.get_channel.assert_called_once_with(20)
        channel.connect.assert_awaited_once_with()

    async def test_join_moves_existing_voice_client(self):
        channel = SimpleNamespace(id=20, name="Phòng mới")
        voice_client = SimpleNamespace(
            channel=SimpleNamespace(id=10),
            is_connected=lambda: True,
            move_to=AsyncMock(),
        )
        message = self.make_message(
            voice_channel=channel, voice_client=voice_client
        )

        handled = await jarvis.handle_discord_voice_channel_command(
            message, "!join"
        )

        self.assertTrue(handled)
        voice_client.move_to.assert_awaited_once_with(channel)

    async def test_leave_disconnects_voice_client(self):
        voice_client = SimpleNamespace(
            is_connected=lambda: True,
            disconnect=AsyncMock(),
        )
        message = self.make_message(voice_client=voice_client)

        handled = await jarvis.handle_discord_voice_channel_command(
            message, "!leave"
        )

        self.assertTrue(handled)
        voice_client.disconnect.assert_awaited_once_with(force=True)

    async def test_unrelated_command_is_not_consumed(self):
        message = self.make_message()
        self.assertFalse(
            await jarvis.handle_discord_voice_channel_command(
                message, "mở youtube"
            )
        )
        message.reply.assert_not_awaited()

    async def test_spoken_all_on_controls_ac_fan_and_pc(self):
        channel = SimpleNamespace(send=AsyncMock())
        async def immediate(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(
            jarvis, "send_esp32_device_commands"
        ) as send_esp, patch.object(
            jarvis, "send_reliable_wake_on_lan", return_value=15
        ) as wake, patch.object(
            jarvis.asyncio, "to_thread", side_effect=immediate
        ), patch.object(
            jarvis, "speak_on_ubuntu_and_discord", new=AsyncMock()
        ) as discord_tts:
            self.assertTrue(
                await jarvis.handle_discord_spoken_device_command(
                    "bật thiết bị", channel
                )
            )
        send_esp.assert_called_once_with(["CMD AC_B", "CMD DEVICE_ON"])
        wake.assert_called_once_with()
        discord_tts.assert_awaited_once_with(
            "Jarvis đã bật tất cả thiết bị.", channel
        )

    async def test_spoken_fan_on_uses_speed_three_and_swing_command(self):
        channel = SimpleNamespace(send=AsyncMock())
        async def immediate(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(
            jarvis, "send_esp32_device_commands"
        ) as send_esp, patch.object(
            jarvis.asyncio, "to_thread", side_effect=immediate
        ), patch.object(
            jarvis, "speak_on_ubuntu_and_discord", new=AsyncMock()
        ) as discord_tts:
            self.assertTrue(
                await jarvis.handle_discord_spoken_device_command(
                    "bật quạt", channel
                )
            )
        send_esp.assert_called_once_with(["CMD DEVICE_ON"])
        discord_tts.assert_awaited_once_with(
            "Jarvis đã bật quạt ở mức ba và bật lắc.", channel
        )

    async def test_shared_router_handles_fan_on_before_local_ai(self):
        async def immediate(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(
            jarvis, "send_esp32_device_commands"
        ) as send_esp, patch.object(
            jarvis.asyncio, "to_thread", side_effect=immediate
        ), patch.object(
            jarvis.CORE.local_ai, "decide"
        ) as local_ai:
            self.assertTrue(
                await jarvis.route_command("bật quạt", allow_local_ai=False)
            )

        send_esp.assert_called_once_with(["CMD DEVICE_ON"])
        local_ai.assert_not_called()
        self.assertEqual(
            jarvis.last_command_response,
            "✅ Jarvis đã bật quạt ở mức ba và bật lắc.",
        )

    async def test_general_spoken_command_uses_shared_chat_router(self):
        channel = SimpleNamespace(id=22, send=AsyncMock())

        async def routed(command, *, source):
            self.assertEqual(command, "mở youtube cá nhân")
            self.assertEqual(source, "discord")
            jarvis.set_command_response(
                "✅ Đã mở YouTube.", spoken_message="Đã mở YouTube."
            )

        with patch.object(
            jarvis, "route_command", side_effect=routed
        ) as route, patch.object(
            jarvis, "speak_on_ubuntu_and_discord", new=AsyncMock()
        ) as discord_tts, patch.object(
            jarvis, "classify_remote_command", return_value="normal"
        ):
            self.assertTrue(
                await jarvis.handle_discord_spoken_command(
                    "mở youtube cá nhân", channel, owner_id=42
                )
            )

        route.assert_awaited_once()
        channel.send.assert_awaited_once_with("✅ Đã mở YouTube.")
        discord_tts.assert_awaited_once_with("Đã mở YouTube.", channel)

    async def test_sensitive_spoken_command_uses_shared_router(self):
        channel = SimpleNamespace(id=22, send=AsyncMock())

        async def routed(command, *, source):
            self.assertEqual(command, "tóm tắt gmail")
            self.assertEqual(source, "discord")
            jarvis.set_command_response("Không có thư mới.")

        with patch.object(
            jarvis, "route_command", side_effect=routed
        ) as route, patch.object(
            jarvis, "speak_on_ubuntu_and_discord", new=AsyncMock()
        ), patch.object(
            jarvis.CORE.conversation, "add"
        ) as conversation:
            self.assertTrue(
                await jarvis.handle_discord_spoken_command(
                    "tóm tắt gmail", channel, owner_id=42
                )
            )

        route.assert_awaited_once()
        channel.send.assert_awaited_once_with("Không có thư mới.")
        conversation.assert_called_once_with(
            "discord_voice", "user", "tóm tắt gmail"
        )

    async def test_dangerous_spoken_command_posts_owner_confirmation(self):
        channel = SimpleNamespace(id=22, send=AsyncMock())

        with patch.object(
            jarvis, "route_command", new=AsyncMock()
        ) as route, patch.object(
            jarvis, "speak_on_ubuntu_and_discord", new=AsyncMock()
        ) as discord_tts, patch.object(
            jarvis.CORE.security, "settings",
            return_value={"discord_enabled": True, "dangerous_enabled": True},
        ):
            self.assertTrue(
                await jarvis.handle_discord_spoken_command(
                    "tắt pc", channel, owner_id=42
                )
            )

        route.assert_not_awaited()
        sent = channel.send.await_args
        self.assertIn("cần xác nhận", sent.args[0])
        self.assertIsInstance(
            sent.kwargs["view"], jarvis.DiscordDangerConfirmationView
        )
        discord_tts.assert_awaited_once()

    async def test_forbidden_spoken_command_is_rejected(self):
        channel = SimpleNamespace(id=22, send=AsyncMock())
        with patch.object(
            jarvis, "route_command", new=AsyncMock()
        ) as route, patch.object(
            jarvis, "speak_on_ubuntu_and_discord", new=AsyncMock()
        ):
            self.assertFalse(
                await jarvis.handle_discord_spoken_command(
                    "đọc .env", channel, owner_id=42
                )
            )
        route.assert_not_awaited()
        self.assertIn("từ chối", channel.send.await_args.args[0])

    async def test_chat_voice_message_routes_transcribed_command(self):
        attachment = SimpleNamespace(
            is_voice_message=lambda: True,
            duration=2.0,
            size=1200,
            read=AsyncMock(return_value=b"encoded-opus"),
        )
        message = SimpleNamespace(
            attachments=[attachment],
            author=SimpleNamespace(id=42),
            channel=SimpleNamespace(id=22),
            reply=AsyncMock(),
        )
        result = {
            "text": "Mở YouTube cá nhân.",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.02,
        }
        async def immediate(func, *args, **kwargs):
            return func(*args, **kwargs)

        with patch.object(
            jarvis, "decode_voice_message_audio", return_value=b"pcm"
        ), patch.object(
            jarvis, "recognize_whisper_pcm", return_value=result
        ), patch.object(
            jarvis.asyncio, "to_thread", side_effect=immediate
        ), patch.object(
            jarvis, "handle_discord_spoken_command", new=AsyncMock(return_value=True)
        ) as handle, patch.object(
            jarvis.CORE.security, "rate_limit", return_value=True
        ):
            self.assertTrue(
                await jarvis.handle_discord_chat_voice_message(message)
            )

        message.reply.assert_awaited_once_with(
            "🗣️ Jarvis nghe: **mở youtube cá nhân**",
            mention_author=False,
        )
        handle.assert_awaited_once_with(
            "mở youtube cá nhân", message.channel, owner_id=42
        )

    async def test_chat_voice_message_rejects_long_recording(self):
        attachment = SimpleNamespace(
            is_voice_message=lambda: True,
            duration=16.0,
            size=1200,
        )
        message = SimpleNamespace(
            attachments=[attachment], reply=AsyncMock()
        )
        self.assertTrue(
            await jarvis.handle_discord_chat_voice_message(message)
        )
        self.assertIn("tối đa 15 giây", message.reply.await_args.args[0])

    async def test_plain_thank_you_gets_friendly_reply(self):
        self.assertTrue(
            await jarvis.route_command("thank you", allow_local_ai=False)
        )
        self.assertEqual(
            jarvis.last_command_response, "Chúc bạn một ngày tốt lành."
        )


class DiscordVoiceRecognitionSafetyTests(unittest.TestCase):
    @staticmethod
    def result(text, confidence=0.92):
        words = text.split()
        return {
            "text": text,
            "result": [
                {
                    "word": word,
                    "conf": confidence,
                    "start": index * 0.25,
                    "end": (index + 1) * 0.25,
                }
                for index, word in enumerate(words)
            ],
        }

    def test_complete_supported_phrase_is_accepted(self):
        self.assertEqual(
            match_discord_voice_command(self.result("gia vít bật máy lạnh")),
            "bật máy lạnh",
        )

    def test_pc_letter_variant_is_canonicalized(self):
        self.assertEqual(
            match_discord_voice_command(self.result("cha vít tắt p c")),
            "tắt pc",
        )

    def test_natural_fan_variant_is_canonicalized(self):
        self.assertEqual(
            match_discord_voice_command(self.result("da vít bật máy quạt")),
            "bật quạt",
        )

    def test_general_chat_command_after_wake_word_is_accepted(self):
        self.assertEqual(
            match_discord_voice_command(
                self.result("gia vít mở youtube cá nhân")
            ),
            "mở youtube cá nhân",
        )

    def test_alternate_wake_pronunciation_is_accepted(self):
        self.assertEqual(
            match_discord_voice_command(
                self.result("cha viết tăng âm lượng")
            ),
            "tăng âm lượng",
        )

    def test_duplicate_device_verb_is_repaired_without_changing_action(self):
        self.assertEqual(
            match_discord_voice_command(
                self.result("gia vít tắt tắt quạt")
            ),
            "tắt quạt",
        )

    def test_acoustically_distinct_stop_fan_alias(self):
        self.assertEqual(
            match_discord_voice_command(
                self.result("gia vít dừng quạt")
            ),
            "tắt quạt",
        )

    def test_background_speech_is_not_treated_as_wake_attempt(self):
        self.assertFalse(starts_with_jarvis_wake_phrase("tiền mặt"))
        self.assertTrue(starts_with_jarvis_wake_phrase("cha vít tắt quạt"))

    def test_whisper_command_requires_literal_wake_word(self):
        result = {
            "text": "Jarvis, tắt quạt.",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.05,
        }
        self.assertEqual(match_whisper_voice_command(result), "tắt quạt")
        result["text"] = "tắt quạt"
        self.assertIsNone(match_whisper_voice_command(result))

    def test_chat_voice_message_does_not_require_wake_word(self):
        result = {
            "text": "Tình trạng hệ thống.",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.02,
        }
        self.assertEqual(
            match_whisper_chat_command(result), "tình trạng hệ thống"
        )

    def test_chat_voice_message_rejects_uncertain_transcript(self):
        result = {
            "text": "Mở YouTube.",
            "average_log_probability": -1.10,
            "no_speech_probability": 0.02,
        }
        self.assertIsNone(match_whisper_chat_command(result))

    def test_observed_tat_quat_as_tat_bai_is_canonicalized(self):
        result = {
            "text": "Jarvis. Tắt bài.",
            "average_log_probability": -0.55,
            "no_speech_probability": 0.01,
        }
        self.assertEqual(match_whisper_voice_command(result), "tắt quạt")

    def test_weather_pronunciation_substitutions_are_canonicalized(self):
        for heard in (
            "Jarvis. Thời thiết hôm nay thế nào?",
            "Jarvis. Thời tiếc hôm nay thế nào?",
            "Jarvis. Thời tiếp hôm nay thế nào?",
            "Jarvis. Tạm tích một tuần nữa thế nào?",
            "Jarvis. Thời thích một tuần nữa thế nào?",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.45,
                    "no_speech_probability": 0.01,
                }
                expected = (
                    "thời tiết một tuần nữa như thế nào"
                    if "tuần nữa" in heard
                    else "thời tiết hôm nay thế nào"
                )
                self.assertEqual(match_whisper_voice_command(result), expected)

    def test_weather_period_first_and_repeated_words_are_canonicalized(self):
        for heard in (
            "Jarvis. Hôm nay thời tiết thế nào?",
            "Jarvis. Thời tiết hôm nay hôm nay hôm nay hôm nay.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.52,
                    "no_speech_probability": 0.01,
                }
                self.assertEqual(
                    match_whisper_voice_command(result),
                    "thời tiết hôm nay thế nào",
                )

    def test_observed_giam_ve_wake_substitution_is_accepted(self):
        result = {
            "text": "Giảm vệ. Thời tiết hôm nay thế nào.",
            "average_log_probability": -0.72,
            "no_speech_probability": 0.01,
        }
        self.assertEqual(
            match_whisper_voice_command(result),
            "thời tiết hôm nay thế nào",
        )

    def test_weather_as_clipped_wake_only_recovers_exact_device_command(self):
        base = {
            "average_log_probability": -0.60,
            "no_speech_probability": 0.01,
        }
        self.assertEqual(
            match_whisper_voice_command({
                **base, "text": "Thời tiết. Bật máy lạnh."
            }),
            "bật máy lạnh",
        )
        self.assertIsNone(match_whisper_voice_command({
            **base, "text": "Thời tiết hôm nay."
        }))

    def test_observed_bat_thich_bai_is_canonicalized(self):
        result = {
            "text": "Jarvis. Bật thích bài.",
            "average_log_probability": -0.70,
            "no_speech_probability": 0.01,
        }
        self.assertEqual(match_whisper_voice_command(result), "bật thiết bị")

    def test_repeated_wake_hallucination_is_rejected(self):
        result = {
            "text": "Jarvis ợ giúp. Jarvis ợ giúp.",
            "average_log_probability": -0.52,
            "no_speech_probability": 0.00,
        }
        self.assertIsNone(match_whisper_voice_command(result))

    def test_uncertain_whisper_result_is_rejected(self):
        result = {
            "text": "Jarvis bật quạt",
            "average_log_probability": -1.20,
            "no_speech_probability": 0.05,
        }
        self.assertIsNone(match_whisper_voice_command(result))

    def test_observed_youtube_substitution_is_canonicalized(self):
        result = {
            "text": "Jarvis. My YouTube.",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.05,
        }
        self.assertEqual(match_whisper_voice_command(result), "mở youtube")

    def test_thank_you_too_closes_youtube_but_thank_you_stays_conversation(self):
        close_result = {
            "text": "Jarvis. Thank you too.",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.05,
        }
        thanks_result = {
            "text": "Jarvis. Thank you.",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.05,
        }
        self.assertEqual(
            match_whisper_voice_command(close_result), "tắt youtube"
        )
        self.assertEqual(
            match_whisper_voice_command(thanks_result), "thank you"
        )

    def test_github_pronunciation_substitutions_are_canonicalized(self):
        for heard in (
            "Jarvis. Mở Git Hub.",
            "Jarvis. Mở gít hắp.",
            "Jarvis. Mở gít hấp.",
            "Jarvis. Log it up.",
            "Jarvis. Loggit hub.",
            "Jarvis. Barh GitHub.",
            "Jarvis. Bargit Hub.",
            "Jarvis. Bargith Hub.",
            "Jarvis. Mà gít hấp.",
            "Jarvis. Mà gít hắp.",
            "Jarvis. Má gít ạp.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.55,
                    "no_speech_probability": 0.01,
                }
                self.assertEqual(
                    match_whisper_voice_command(result), "mở github"
                )

    def test_repeated_observed_wake_keeps_first_exact_github_command(self):
        result = {
            "text": (
                "Cháu vết. Mà gít hấp. "
                "Chá vết, chá vất. Má gít ạp."
            ),
            "average_log_probability": -0.92,
            "no_speech_probability": 0.03,
        }
        self.assertEqual(match_whisper_voice_command(result), "mở github")

    def test_observed_tag_github_substitution_closes_github(self):
        for heard in (
            "Jarvis. Tag GitHub.",
            "Jarvis. Tag Gighub.",
            "Jarvis. Thank you GitHub.",
            "Jarvis. Tắt Git Hub.",
            "Jarvis. Tắt gít hấp.",
            "Jarvis. Tắt gít hắp.",
            "Jarvis. Đóng gít hấp.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.42,
                    "no_speech_probability": 0.02,
                }
                self.assertEqual(
                    match_whisper_voice_command(result), "tắt github"
                )

    def test_observed_device_substitution_is_canonicalized(self):
        result = {
            "text": "Jarvis bật thấy bay",
            "average_log_probability": -0.20,
            "no_speech_probability": 0.05,
        }
        self.assertEqual(match_whisper_voice_command(result), "bật thiết bị")

    def test_observed_zalo_english_substitution_is_canonicalized(self):
        result = {
            "text": "Jarvis. Mở Yellow.",
            "average_log_probability": -0.27,
            "no_speech_probability": 0.01,
        }
        self.assertEqual(match_whisper_voice_command(result), "mở zalo")

    def test_observed_zalo_vietnamese_phonetic_substitution_is_canonicalized(self):
        result = {
            "text": "Jarvis. Mở dì lâu.",
            "average_log_probability": -0.76,
            "no_speech_probability": 0.02,
        }
        self.assertEqual(match_whisper_voice_command(result), "mở zalo")

    def test_latest_zalo_de_lo_substitution_is_canonicalized(self):
        for heard, expected in (
            ("Jarvis. Bật dè lô.", "mở zalo"),
            ("Jarvis. Mở dè lô.", "mở zalo"),
            ("Jarvis. Tắt dè lô.", "tắt zalo"),
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.68,
                    "no_speech_probability": 0.09,
                }
                self.assertEqual(match_whisper_voice_command(result), expected)

    def test_latest_zalo_ghi_de_lo_substitutions_are_canonicalized(self):
        for heard, expected in (
            ("Jarvis. Tắt ghi lô.", "tắt zalo"),
            ("Jarvis. Tắt dê lô.", "tắt zalo"),
            ("Jarvis. Mở ghi lô.", "mở zalo"),
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.50,
                    "no_speech_probability": 0.05,
                }
                self.assertEqual(match_whisper_voice_command(result), expected)

    def test_observed_device_vowel_substitution_is_canonicalized(self):
        result = {
            "text": "Jarvis. Bật thích bị.",
            "average_log_probability": -0.25,
            "no_speech_probability": 0.17,
        }
        self.assertEqual(match_whisper_voice_command(result), "bật thiết bị")

    def test_latest_device_phonetic_substitutions_are_canonicalized(self):
        for heard in (
            "Jarvis. Bật thích bậy.",
            "Jarvis. Bật. Tích bay.",
            "Jarvis. Tắt thích bây.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.30,
                    "no_speech_probability": 0.01,
                }
                expected_action = "tắt" if "Tắt" in heard else "bật"
                self.assertEqual(
                    match_whisper_voice_command(result),
                    f"{expected_action} thiết bị",
                )

    def test_newest_device_substitutions_are_canonicalized(self):
        for heard in (
            "Jarvis. Bật thích quay.",
            "Jarvis. Bạc tích bay.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.57,
                    "no_speech_probability": 0.03,
                }
                self.assertEqual(
                    match_whisper_voice_command(result), "bật thiết bị"
                )

    def test_tat_action_phonetic_substitution_is_repaired_for_all_commands(self):
        for heard, expected in (
            ("Jarvis. Tạch YouTube.", "tắt youtube"),
            ("Jarvis. Tạc Zalo.", "tắt zalo"),
            ("Jarvis. Tắc thích quay.", "tắt thiết bị"),
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.42,
                    "no_speech_probability": 0.03,
                }
                self.assertEqual(match_whisper_voice_command(result), expected)

    def test_latest_device_microphone_substitutions_are_canonicalized(self):
        for heard in (
            "Jarvis. Bật thích mậy.",
            "Jarvis. Mở thích máy.",
            "Jarvis. Bật thích bệnh.",
            "Jarvis. Mặc thức bệnh.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.44,
                    "no_speech_probability": 0.03,
                }
                self.assertEqual(
                    match_whisper_voice_command(result), "bật thiết bị"
                )

    def test_latest_tat_device_substitutions_are_canonicalized(self):
        for heard in (
            "Jarvis. Tạch thích bày.",
            "Jarvis. Tạch tạch bay.",
        ):
            with self.subTest(heard=heard):
                result = {
                    "text": heard,
                    "average_log_probability": -0.53,
                    "no_speech_probability": 0.03,
                }
                self.assertEqual(
                    match_whisper_voice_command(result), "tắt thiết bị"
                )

    def test_partial_or_embedded_phrase_is_rejected(self):
        self.assertIsNone(match_discord_voice_command(self.result("bật")))
        self.assertIsNone(
            match_discord_voice_command(self.result("bật quạt"))
        )
        self.assertIsNone(
            match_discord_voice_command(
                self.result("hãy bật quạt giúp tôi")
            )
        )

    def test_low_confidence_phrase_is_rejected(self):
        self.assertIsNone(
            match_discord_voice_command(
                self.result("gia vít tắt thiết bị", confidence=0.50)
            )
        )

    def test_late_speaking_start_keeps_wake_word_audio(self):
        sink = object.__new__(DiscordVoiceCommandSink)
        sink.owner_id = 42
        sink.closed = False
        sink.buffer = bytearray(b"jarvis-audio")
        sink.buffer_lock = __import__("threading").Lock()
        sink.collecting = True
        sink.stop_timer = None
        self.addCleanup(setattr, sink, "closed", True)
        member = SimpleNamespace(id=42)

        sink.on_voice_member_speaking_start(member)

        self.assertEqual(sink.buffer, b"jarvis-audio")


if __name__ == "__main__":
    unittest.main()
