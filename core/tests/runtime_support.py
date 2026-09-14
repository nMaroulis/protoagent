"""Provider-free integration fixtures using installed ProtoLink public primitives."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from protolink import ApprovalBroker, ApprovalDecision, RunContext, RunHandle, Task
from protolink.storage import SQLiteRunStore

from protoagent_core.agents.coder import create_coder_agent
from protoagent_core.agents.verifier import create_verifier_agent
from protoagent_core.checkpoints import checkpoint_store, private_file
from protoagent_core.runtime_bridge import RuntimeBridge
from protoagent_core.runtime_policy import AttemptState, RunAuthorization
from protoagent_core.runtime_storage import output_redaction


class NativeRuntimeCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = (Path(self.temp.name) / "workspace").resolve()
        self.root.mkdir()
        self.config_dir = Path(self.temp.name) / "state"
        self.config_patch = patch("protoagent_core.config.CONFIG_DIR", self.config_dir)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)
        config_path_patch = patch(
            "protoagent_core.config.CONFIG_PATH", self.config_dir / "config.json"
        )
        config_path_patch.start()
        self.addCleanup(config_path_patch.stop)
        self.context = RunContext(workspace_uri=self.root.as_uri(), trace_id="test-owned-trace")
        self.authorization = RunAuthorization(self.context)
        self.checkpoints = checkpoint_store(str(self.root))
        self.attempt = AttemptState(str(self.root), self.checkpoints, self.authorization)
        self.attempt.begin()
        self.broker = ApprovalBroker(timeout_seconds=3)
        self.redaction = output_redaction()
        self.store = SQLiteRunStore(
            private_file(self.config_dir / "runs" / "test.sqlite"), redaction_policy=self.redaction
        )
        common = dict(
            workspace=str(self.root),
            transport=None,
            approval_handler=self.broker,
            authorization=self.authorization,
            attempt=self.attempt,
        )
        self.coder = create_coder_agent(**common, checkpoints=self.checkpoints, tool_only=True)
        self.verifier = create_verifier_agent(**common)
        self.coder.run_store = self.verifier.run_store = self.store
        self.bridge = RuntimeBridge(str(Path(self.temp.name) / "progress.jsonl"))
        self.bridge.bind(self.broker, self.authorization, self.redaction)
        self.handles = []

    async def asyncTearDown(self):
        for handle in self.handles:
            await handle.cancel("Test cleanup")
            await handle.result()
        self.bridge.cleanup()
        self.temp.cleanup()

    def start_tool(self, agent, name, args, *, context=None):
        task = Task.create_tool_call(tool_name=name, args=args)
        (context or self.context.child(agent_name=agent.card.name)).attach_to_task(task)
        handle = RunHandle.start(agent, task, redaction_policy=self.redaction)
        self.handles.append(handle)
        return handle

    async def pending(self, count=1):
        async with asyncio.timeout(3):
            while len(records := self.broker.pending(self.authorization.scope)) < count:
                await asyncio.sleep(0.005)
        return records

    async def tool(self, agent, name, args, approved=True):
        handle = self.start_tool(agent, name, args)
        (record,) = await self.pending()
        self.broker.resolve(
            ApprovalDecision(approved, record.request.request_id),
            scope=self.authorization.scope,
            fingerprint=record.fingerprint,
        )
        return await asyncio.wait_for(handle.result(), 3)
