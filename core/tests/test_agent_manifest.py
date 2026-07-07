from __future__ import annotations

import unittest

from protoagent_core.agents import agent_manifest


class AgentManifestTests(unittest.TestCase):
    def test_manifest_describes_kernel_contract_and_worker_state(self) -> None:
        manifest = agent_manifest({"resolved": "medium", "label": "Medium reasoning model"})

        architecture = manifest["architecture"]
        self.assertEqual(architecture["kernel"], "ProtoLink runtime kernel")
        self.assertEqual(architecture["controller"], "architect")
        self.assertIn("explorer", architecture["stateless"])
        self.assertIn("RunContract", architecture["contract"])
        self.assertIn("ProtoLink API-key auth", architecture["flow"])

        agents = {agent["name"]: agent for agent in manifest["agents"]}
        self.assertEqual(agents["architect"]["state"], "stateful")
        self.assertEqual(agents["architect"]["memory"], "protoagent-architect")
        self.assertEqual(agents["explorer"]["state"], "stateless")
        self.assertEqual(agents["explorer"]["memory"], "task-local")
        self.assertEqual(agents["coder"]["state"], "stateless")
        self.assertEqual(agents["coder"]["memory"], "task-local")


if __name__ == "__main__":
    unittest.main()
