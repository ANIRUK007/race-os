"""Checks the broker's trust boundary: allowlist, required reason, fail-closed
confirmation for destructive tools, and the audit log.  python3 -m unittest"""
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import raced  # noqa: E402


class Client:
    def __init__(self, path, elicitation, answer=None):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(path)
        self.f = self.s.makefile("rwb")
        self.answer = answer      # "accept" / "decline" when the broker asks
        self.asked = []
        self.n = 0
        caps = {"elicitation": {}} if elicitation else {}
        self.call("initialize", {"protocolVersion": raced.PROTOCOL_VERSION,
                                 "capabilities": caps, "clientInfo": {"name": "test"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, msg):
        self.f.write((json.dumps(msg) + "\n").encode())
        self.f.flush()

    def call(self, method, params=None):
        self.n += 1
        self._send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        while True:
            msg = json.loads(self.f.readline())
            if msg.get("method") == "elicitation/create":
                self.asked.append(msg["params"]["message"])
                content = {"approve": self.answer == "accept"}
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "result": {"action": self.answer, "content": content}})
                continue
            return msg

    def tool(self, name, **args):
        return self.call("tools/call", {"name": name, "arguments": args})


def text(resp):
    return resp["result"]["content"][0]["text"]


class BrokerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.root = t / "root"
        self.root.mkdir()
        (self.root / "notes.md").write_text("socket path is agent.sock\n")
        outside = t / "secret.txt"
        outside.write_text("do not read")
        (self.root / "escape").symlink_to(outside)
        self.sock = t / "agent.sock"
        self.audit = t / "audit.jsonl"
        self.server = raced.Broker(self.sock, [self.root.resolve()], self.audit)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def client(self, elicitation, answer=None):
        c = Client(str(self.sock), elicitation, answer)
        self.addCleanup(c.s.close)
        self.addCleanup(c.f.close)
        return c

    def audit_lines(self):
        return [json.loads(l) for l in self.audit.read_text().splitlines()]

    def test_manifest_is_standard_mcp_with_required_reason(self):
        tools = {t["name"]: t for t in self.client(True).call("tools/list")["result"]["tools"]}
        self.assertEqual(set(tools), {"fs.list", "fs.read", "proc.run"})
        for t in tools.values():
            self.assertIn("reason", t["inputSchema"]["required"])
        self.assertTrue(tools["fs.read"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["proc.run"]["annotations"]["destructiveHint"])

    def test_read_is_auto_allowed_and_audited(self):
        c = self.client(elicitation=False)
        r = c.tool("fs.read", path=str(self.root / "notes.md"), reason="user asked about the socket")
        self.assertIn("agent.sock", text(r))
        self.assertEqual(c.asked, [])
        rec = self.audit_lines()[-1]
        self.assertEqual((rec["tool"], rec["outcome"], rec["reason"]),
                         ("fs.read", "ok", "user asked about the socket"))

    def test_paths_outside_roots_are_rejected(self):
        c = self.client(elicitation=False)
        for p in ["/etc/passwd", str(self.root / ".." / "secret.txt"), str(self.root / "escape")]:
            r = c.tool("fs.read", path=p, reason="probe")
            self.assertTrue(r["result"]["isError"], p)
            self.assertIn("outside allowed roots", text(r))

    def test_missing_reason_is_a_protocol_error(self):
        r = self.client(True).tool("fs.read", path="notes.md")
        self.assertEqual(r["error"]["code"], -32602)
        self.assertIn("reason", r["error"]["message"])

    def test_destructive_fails_closed_without_elicitation(self):
        marker = self.root / "ran"
        r = self.client(elicitation=False).tool(
            "proc.run", argv=["touch", str(marker)], reason="test")
        self.assertTrue(r["result"]["isError"])
        self.assertFalse(marker.exists())
        self.assertEqual(self.audit_lines()[-1]["outcome"], "denied")

    def test_destructive_declined_does_not_run(self):
        marker = self.root / "ran"
        c = self.client(elicitation=True, answer="decline")
        r = c.tool("proc.run", argv=["touch", str(marker)], reason="make a marker")
        self.assertTrue(r["result"]["isError"])
        self.assertFalse(marker.exists())
        self.assertIn("make a marker", c.asked[0])   # reason is shown to the user

    def test_destructive_approved_runs_without_a_shell(self):
        c = self.client(elicitation=True, answer="accept")
        r = c.tool("proc.run", argv=["echo", "a; touch pwned"], reason="echo test")
        self.assertIn("a; touch pwned", text(r))
        self.assertFalse((self.root / "pwned").exists())
        self.assertEqual(self.audit_lines()[-1]["outcome"], "ok")


if __name__ == "__main__":
    unittest.main()
