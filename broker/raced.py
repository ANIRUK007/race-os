#!/usr/bin/env python3
"""raced -- the Race OS system broker.

An MCP server on a Unix socket. Agents discover capabilities with `tools/list`
and invoke them with `tools/call`; the broker sits between model output and the
system, applying a risk policy, path allowlist and audit log to every call.

Wire format: newline-delimited JSON-RPC 2.0 (MCP's stdio framing), carried over
$XDG_RUNTIME_DIR/race/agent.sock. Standard MCP clients that only speak stdio
attach through the `race-mcp` shim.

Stdlib only.
"""
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "raced", "version": "0.1.0"}

MAX_READ = 256 * 1024        # bytes returned by fs.read
MAX_OUTPUT = 64 * 1024       # bytes of stdout/stderr returned by proc.run
PROC_TIMEOUT = 30            # seconds


def runtime_dir():
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/race-{os.getuid()}")


def default_socket():
    return Path(os.environ.get("RACE_SOCK") or runtime_dir() / "race" / "agent.sock")


def default_audit():
    state = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(os.environ.get("RACE_AUDIT") or Path(state) / "race" / "audit.jsonl")


def default_roots():
    raw = os.environ.get("RACE_ROOTS") or str(Path.home())
    return [Path(r).resolve() for r in raw.split(":") if r]


# --------------------------------------------------------------------------- #
# Capability manifest. `risk` drives policy; the MCP annotations carry the same
# information to clients in standard form.

REASON = {"type": "string", "minLength": 1,
          "description": "Why the agent is taking this action. Shown to the user and logged."}

TOOLS = {
    "fs.list": {
        "risk": "read",
        "description": "List entries in a directory.",
        "inputSchema": {"type": "object", "required": ["path", "reason"],
                        "properties": {"path": {"type": "string"}, "reason": REASON}},
    },
    "fs.read": {
        "risk": "read",
        "description": f"Read a text file (first {MAX_READ} bytes).",
        "inputSchema": {"type": "object", "required": ["path", "reason"],
                        "properties": {"path": {"type": "string"}, "reason": REASON}},
    },
    "proc.run": {
        "risk": "destructive",
        "description": "Run a program. `argv` is an argument array; no shell is involved.",
        "inputSchema": {"type": "object", "required": ["argv", "reason"],
                        "properties": {
                            "argv": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                            "cwd": {"type": "string"},
                            "reason": REASON}},
    },
}

ANNOTATIONS = {
    "read":        {"readOnlyHint": True,  "destructiveHint": False},
    "mutate":      {"readOnlyHint": False, "destructiveHint": False},
    "destructive": {"readOnlyHint": False, "destructiveHint": True},
}


def manifest():
    return [{"name": name, "description": t["description"], "inputSchema": t["inputSchema"],
             "annotations": {**ANNOTATIONS[t["risk"]], "title": name}}
            for name, t in TOOLS.items()]


class ToolError(Exception):
    """A failure reported to the model as a tool result, not a protocol error."""


# --------------------------------------------------------------------------- #
# Providers

class Providers:
    def __init__(self, roots):
        self.roots = roots

    def resolve(self, raw, must_exist=True):
        """Resolve a path and refuse anything outside the allowed roots.

        realpath() follows symlinks before the containment check, so a symlink
        inside a root that points outside it is rejected.
        """
        p = Path(os.path.expanduser(raw))
        if not p.is_absolute():
            p = self.roots[0] / p
        real = Path(os.path.realpath(p))
        if not any(real == r or r in real.parents for r in self.roots):
            raise ToolError(f"path outside allowed roots: {raw}")
        if must_exist and not real.exists():
            raise ToolError(f"no such path: {raw}")
        return real

    def fs_list(self, args):
        d = self.resolve(args["path"])
        if not d.is_dir():
            raise ToolError(f"not a directory: {args['path']}")
        entries = []
        for e in sorted(d.iterdir(), key=lambda e: e.name):
            kind = "dir" if e.is_dir() else "symlink" if e.is_symlink() else "file"
            entries.append(f"{kind}\t{e.name}")
        return "\n".join(entries)

    def fs_read(self, args):
        f = self.resolve(args["path"])
        if not f.is_file():
            raise ToolError(f"not a file: {args['path']}")
        with open(f, "rb") as fh:
            data = fh.read(MAX_READ + 1)
        text = data[:MAX_READ].decode("utf-8", errors="replace")
        return text + ("\n[truncated]" if len(data) > MAX_READ else "")

    def proc_run(self, args):
        argv = args["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
            raise ToolError("argv must be a non-empty array of strings")
        cwd = self.resolve(args.get("cwd") or str(self.roots[0]))
        try:
            r = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=PROC_TIMEOUT,
                               stdin=subprocess.DEVNULL, shell=False)
        except FileNotFoundError:
            raise ToolError(f"program not found: {argv[0]}")
        except subprocess.TimeoutExpired:
            raise ToolError(f"timed out after {PROC_TIMEOUT}s")
        out = r.stdout[:MAX_OUTPUT].decode("utf-8", errors="replace")
        err = r.stderr[:MAX_OUTPUT].decode("utf-8", errors="replace")
        return f"exit {r.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"

    def call(self, name, args):
        return {"fs.list": self.fs_list, "fs.read": self.fs_read,
                "proc.run": self.proc_run}[name](args)


# --------------------------------------------------------------------------- #
# Audit

class Audit:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, **rec):
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **rec}
        line = json.dumps(rec, ensure_ascii=False)
        with self.lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")


# --------------------------------------------------------------------------- #
# Session: one client connection

class Session:
    def __init__(self, broker, rfile, wfile):
        self.broker = broker
        self.rfile, self.wfile = rfile, wfile
        self.id = str(uuid.uuid4())
        self.client_caps = {}
        self.approved_mutate = set()   # mutate tools confirmed once this session

    def send(self, msg):
        self.wfile.write((json.dumps(msg) + "\n").encode())
        self.wfile.flush()

    def read(self):
        line = self.rfile.readline()
        if not line:
            return None
        return json.loads(line)

    # -- permission ---------------------------------------------------------

    def confirm(self, name, args, reason):
        """Ask the client to approve via MCP elicitation. Fail closed."""
        if "elicitation" not in self.client_caps:
            return False, "client cannot prompt the user (no elicitation capability)"
        req_id = f"elicit-{uuid.uuid4()}"
        shown = {k: v for k, v in args.items() if k != "reason"}
        self.send({"jsonrpc": "2.0", "id": req_id, "method": "elicitation/create", "params": {
            "message": f"Allow {name}?\nReason: {reason}\nArguments: {json.dumps(shown)}",
            "requestedSchema": {"type": "object", "required": ["approve"],
                                "properties": {"approve": {"type": "boolean",
                                                           "title": "Approve this action"}}},
        }})
        # ponytail: assumes the client answers the elicitation before sending
        # anything else on this connection. Add a pending-request table if a
        # client ever pipelines.
        msg = self.read()
        if not msg or msg.get("id") != req_id:
            return False, "no answer to permission request"
        res = msg.get("result") or {}
        ok = res.get("action") == "accept" and (res.get("content") or {}).get("approve") is True
        return ok, "approved" if ok else "declined by user"

    def authorize(self, name, risk, args, reason):
        if risk == "read":
            return True, "auto"
        if risk == "mutate" and name in self.approved_mutate:
            return True, "approved earlier this session"
        ok, why = self.confirm(name, args, reason)
        if ok and risk == "mutate":
            self.approved_mutate.add(name)
        return ok, why

    # -- methods ------------------------------------------------------------

    def tools_call(self, params):
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOLS.get(name)
        if not tool:
            raise KeyError(f"unknown tool: {name}")
        missing = [k for k in tool["inputSchema"]["required"]
                   if k not in args or args[k] in ("", None)]
        if missing:
            raise ValueError(f"missing required argument(s): {', '.join(missing)}")
        reason, risk = args["reason"], tool["risk"]

        ok, decision = self.authorize(name, risk, args, reason)
        audit = dict(session=self.id, tool=name, risk=risk, reason=reason,
                     args={k: v for k, v in args.items() if k != "reason"}, decision=decision)
        if not ok:
            self.broker.audit.write(**audit, outcome="denied")
            return {"isError": True, "content": [{"type": "text", "text": f"denied: {decision}"}]}
        try:
            text = self.broker.providers.call(name, args)
        except ToolError as e:
            self.broker.audit.write(**audit, outcome="error", error=str(e))
            return {"isError": True, "content": [{"type": "text", "text": str(e)}]}
        self.broker.audit.write(**audit, outcome="ok")
        return {"content": [{"type": "text", "text": text}]}

    def dispatch(self, msg):
        method, params = msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            self.client_caps = params.get("capabilities") or {}
            return {"protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": manifest()}
        if method == "tools/call":
            return self.tools_call(params)
        raise LookupError(method)

    def run(self):
        while True:
            try:
                msg = self.read()
            except json.JSONDecodeError:
                self.send({"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "parse error"}})
                continue
            if msg is None:
                return
            if "id" not in msg:          # notification, e.g. notifications/initialized
                continue
            try:
                result = self.dispatch(msg)
                self.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            except LookupError as e:
                self.send({"jsonrpc": "2.0", "id": msg["id"],
                           "error": {"code": -32601, "message": f"method not found: {e}"}})
            except (KeyError, ValueError) as e:
                self.send({"jsonrpc": "2.0", "id": msg["id"],
                           "error": {"code": -32602, "message": str(e).strip("'")}})


# --------------------------------------------------------------------------- #
# Server

class Broker(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, sock_path, roots, audit_path):
        self.providers = Providers(roots)
        self.audit = Audit(audit_path)
        sock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if sock_path.exists():
            sock_path.unlink()
        super().__init__(str(sock_path), Handler)
        os.chmod(sock_path, 0o600)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        Session(self.server, self.rfile, self.wfile).run()


def main():
    sock, roots, audit = default_socket(), default_roots(), default_audit()
    server = Broker(sock, roots, audit)
    print(f"raced listening on {sock}; roots={[str(r) for r in roots]}; audit={audit}",
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        sock.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
