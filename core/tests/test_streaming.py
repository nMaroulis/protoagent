"""Live delivery through real ProtoLink provider parsing and ProtoAgent's bridge."""

import asyncio
import json
from unittest.mock import patch

import httpx
from protolink import RunEvent
from protolink.llms.server.ollama_client import OllamaLLM
from runtime_support import NativeRuntimeCase

from protoagent_core.help_agent import _answer_help_question
from protoagent_core.runtime import _run_agent_deck
from protoagent_core.runtime_storage import output_redaction
from protoagent_core.streaming import LiveOutput, _final_content_prefix


class StreamingTests(NativeRuntimeCase):
    async def exercise_stream(self, *, native=True, cancel=False, enabled=True, guide=False):
        release, delivered, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        calls = []
        first, rest = ("Hel", "lo") if native else ('{"type":"final","content":"Hel', 'lo"}')

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                try:
                    delivered.set()
                    yield (json.dumps({"message": {"content": first}}) + "\n").encode()
                    await release.wait()
                    yield (json.dumps({"message": {"content": rest}}) + "\n").encode()
                    yield b'{"done":true}\n'
                finally:
                    closed.set()

            async def aclose(self):
                closed.set()

        def respond(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, stream=Stream())

        client = httpx.AsyncClient
        transport = httpx.MockTransport(respond)
        with (
            patch(
                "protoagent_core.agents.common.secrets.token_urlsafe",
                return_value="test-runtime-credential",
            ),
            patch.object(OllamaLLM, "validate_connection", return_value=True),
            patch(
                "httpx.AsyncClient",
                side_effect=lambda **kwargs: client(transport=transport, **kwargs),
            ),
            patch.dict(
                "os.environ",
                {
                    "PROTOAGENT_AGENT_TRANSPORT": "runtime",
                    "PROTOAGENT_STREAM": "1" if enabled else "0",
                },
            ),
            patch("protoagent_core.agents.coder.create_selected_llm", return_value=None),
            patch("protoagent_core.agents.explorer.create_selected_llm", return_value=None),
        ):
            llm = OllamaLLM(
                base_url="http://provider.invalid", model="test", supports_tool_calling=native
            )
            with (
                patch("protoagent_core.agents.architect.create_selected_llm", return_value=llm),
                patch("protoagent_core.help_agent.create_llm_from_config", return_value=llm),
                patch(
                    "protoagent_core.help_agent.visible_config",
                    return_value={
                        "active_provider": "ollama",
                        "providers": {"ollama": {"model": "test"}},
                        "config_path": str(self.config_dir / "config.json"),
                    },
                ),
            ):
                running = asyncio.create_task(
                    _answer_help_question("How does /config work?", str(self.bridge.progress_path))
                    if guide
                    else _run_agent_deck(
                        "Say hello",
                        "ollama",
                        "test",
                        str(self.root),
                        None,
                        self.bridge,
                        {"resolved": "small", "label": "Small", "configured": "small"},
                    )
                )
                try:
                    await asyncio.wait_for(delivered.wait(), 2)

                    async def wait_preview():
                        while True:
                            if self.bridge.progress_path.exists():
                                records = [
                                    json.loads(line)
                                    for line in self.bridge.progress_path.read_text().splitlines()
                                ]
                                outputs = [
                                    item["live_output"] for item in records if "live_output" in item
                                ]
                                if outputs:
                                    return outputs
                            await asyncio.sleep(0.01)

                    if enabled:
                        previews = await asyncio.wait_for(wait_preview(), 2)
                        self.assertEqual(previews[0]["text"], "Hel")
                        self.assertEqual(previews[0]["channel"], "answer")
                        self.assertEqual(previews[0]["agent"], "guide" if guide else "architect")
                    else:
                        await asyncio.sleep(0.05)
                        if self.bridge.progress_path.exists():
                            self.assertNotIn('"live_output"', self.bridge.progress_path.read_text())
                    self.assertFalse(
                        running.done(), "Preview must arrive before generation completes"
                    )
                    self.assertFalse(release.is_set())
                    if cancel:
                        self.bridge.cancel_path.write_text(
                            json.dumps({"reason": "cancel during generation"})
                        )
                    else:
                        release.set()
                    result = await asyncio.wait_for(running, 3)
                finally:
                    release.set()
                    if not running.done():
                        running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
        self.assertEqual(result["status"], "canceled" if cancel else "completed", result["answer"])
        self.assertEqual(len(calls), 1, "Interrupted generation must not be resubmitted")
        self.assertTrue(calls[0]["stream"])
        self.assertTrue(closed.is_set())
        if not cancel:
            self.assertEqual(result["answer"], "Hello")
            self.assertTrue(
                any(
                    event["payload"].get("llm_event_type") == "llm_final"
                    for event in result["run_events"]
                )
            )
            if enabled:
                outputs = [
                    json.loads(line)["live_output"]
                    for line in self.bridge.progress_path.read_text().splitlines()
                    if '"live_output"' in line
                ]
                self.assertEqual(outputs[-1]["text"], "Hello")
                self.assertTrue(outputs[-1]["replace"])
                self.assertEqual(outputs[0]["id"], outputs[-1]["id"])

    async def test_native_model_text_arrives_before_generation_finishes(self):
        await self.exercise_stream()

    async def test_json_action_fragments_arrive_before_final_answer(self):
        await self.exercise_stream(native=False)

    async def test_guide_json_answer_streams_before_completion_without_a_project(self):
        await self.exercise_stream(guide=True, native=False)

    async def test_guide_native_answer_streams_before_completion(self):
        await self.exercise_stream(guide=True)

    async def test_guide_cancellation_closes_provider_without_replay(self):
        await self.exercise_stream(guide=True, native=False, cancel=True)

    async def test_guide_stream_zero_still_returns_final_result(self):
        await self.exercise_stream(guide=True, enabled=False)

    async def test_cancel_while_provider_is_waiting_closes_stream(self):
        await self.exercise_stream(cancel=True)

    async def test_cancel_while_json_answer_is_streaming_closes_stream(self):
        await self.exercise_stream(native=False, cancel=True)

    async def test_stream_zero_suppresses_preview_but_preserves_execution(self):
        await self.exercise_stream(enabled=False)

    def test_known_secret_split_across_chunks_is_not_exposed_in_preview(self):
        output = LiveOutput(self.bridge, output_redaction("abcab"))
        for text in ("value: ab", "c", "ab done"):
            output.emit(
                RunEvent(
                    "llm.stream",
                    agent_name="architect",
                    payload={"llm_event_type": "llm_chunk", "content": text},
                )
            )
        records = [json.loads(line) for line in self.bridge.progress_path.read_text().splitlines()]
        visible = "".join(item["live_output"]["text"] for item in records)
        self.assertEqual(visible, "value: [REDACTED] done")

    def test_json_answer_decodes_every_split_without_exposing_the_envelope(self):
        answer = 'Line one\n"quoted" \\ path\t café 🎉 and `code`'
        raw = json.dumps({"type": "final", "content": answer})
        output = LiveOutput(self.bridge, output_redaction(), json_agents={"architect"})
        for char in raw:
            output.emit(
                RunEvent(
                    "llm.stream",
                    agent_name="architect",
                    payload={
                        "llm_event_type": "llm_chunk",
                        "content": char,
                    },
                )
            )
        records = [json.loads(line) for line in self.bridge.progress_path.read_text().splitlines()]
        visible = "".join(item["live_output"]["text"] for item in records)
        self.assertEqual(visible, answer)
        self.assertTrue(all(item["live_output"]["channel"] == "answer" for item in records))

    def test_only_top_level_final_content_is_projected(self):
        for raw in (
            '{"type":"tool_call","args":{"type":"final","content":"hidden"}}',
            '{"type":"agent_call","content":"hidden"}',
            '{"content":"not yet identified"',
            '{"type":"final","content":{"nested":"hidden"}}',
        ):
            with self.subTest(raw=raw):
                self.assertEqual(_final_content_prefix(raw), "")
        self.assertEqual(_final_content_prefix('{"content":"Hello","type":"final"}'), "Hello")
        self.assertEqual(_final_content_prefix('```json\n{"type":"final","content":"Hel'), "Hel")

    def test_native_json_answer_is_preserved_and_workers_keep_their_own_channel(self):
        output = LiveOutput(self.bridge, output_redaction())
        answer = '{"type":"final","content":"genuine JSON answer"}'
        for agent in ("architect", "coder"):
            output.emit(
                RunEvent(
                    "llm.stream",
                    agent_name=agent,
                    payload={
                        "llm_event_type": "llm_chunk",
                        "content": answer,
                    },
                )
            )
        records = [
            json.loads(line)["live_output"]
            for line in self.bridge.progress_path.read_text().splitlines()
        ]
        self.assertEqual([item["text"] for item in records], [answer, answer])
        self.assertEqual([item["channel"] for item in records], ["answer", "generation"])

    def test_json_escaped_secret_split_between_chunks_stays_redacted(self):
        output = LiveOutput(self.bridge, output_redaction("abcab"), json_agents={"architect"})
        for chunk in ('{"type":"final","content":"value: ab', r"\u0063", 'ab done"}'):
            output.emit(
                RunEvent(
                    "llm.stream",
                    agent_name="architect",
                    payload={
                        "llm_event_type": "llm_chunk",
                        "content": chunk,
                    },
                )
            )
        records = [json.loads(line) for line in self.bridge.progress_path.read_text().splitlines()]
        self.assertEqual(
            "".join(item["live_output"]["text"] for item in records), "value: [REDACTED] done"
        )
