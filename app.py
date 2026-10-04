import os, re, json, subprocess, urllib.request, urllib.error
from flask import Flask, request, jsonify, render_template

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.environ.get("GROQ_API_KEY")

SYSTEM = """You are Jarvis, an autonomous AI agent running on the user's own device via Termux.
Reply in the same language the user writes in (Hinglish is fine).

You are AGENTIC: when the user gives you a goal (e.g. "build me a bot that...", "fix this error",
"research X and summarize"), you break it into steps yourself and execute them one by one using
your tools, without asking the user to do each step manually. Keep going, calling tools as needed,
until the task is actually done or you hit a real blocker you cannot solve - then explain clearly
what you did and what's left.

For simple questions, just answer directly without over-using tools.

Available tools:
- read_file / write_file: work with files on this device
- run_shell: run terminal commands (install packages, run scripts, test code)
- browse: fetch and read a public web page
- make_call / send_sms / notify: control this phone (calls, texts, notifications)

Rules:
- Only automate things the user owns or has permission for (their own accounts, public data,
  official APIs). Refuse to bypass anti-bot protections or break a site's terms of service -
  explain the concept and suggest the official API instead.
- Before running a potentially destructive shell command (delete, overwrite important files,
  reinstall system packages), briefly state what it will do.
- Be concise. Summarize progress after multi-step work instead of showing raw logs."""

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command (install packages, run scripts, tests).",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "browse", "description": "Fetch a public web page and return readable text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "make_call", "description": "Dial a phone number on this device.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}}},
    {"type": "function", "function": {"name": "send_sms", "description": "Send an SMS to a phone number.",
     "parameters": {"type": "object", "properties": {"number": {"type": "string"}, "message": {"type": "string"}}, "required": ["number", "message"]}}},
    {"type": "function", "function": {"name": "notify", "description": "Show an Android notification.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "message": {"type": "string"}}, "required": ["title", "message"]}}},
]


def run_tool(name, args):
    try:
        if name == "read_file":
            with open(args["path"], encoding="utf-8") as f:
                return f.read()[:20000]
        if name == "write_file":
            os.makedirs(os.path.dirname(args["path"]) or ".", exist_ok=True)
            with open(args["path"], "w", encoding="utf-8") as f:
                f.write(args["content"])
            return "File written: " + args["path"]
        if name == "run_shell":
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=180)
            out = (r.stdout + r.stderr).strip()
            return out[-6000:] if out else "(command ran, no output)"
        if name == "browse":
            req = urllib.request.Request(args["url"], headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
            return text[:15000]
        if name == "make_call":
            subprocess.run(["termux-telephony-call", args["number"]], timeout=10)
            return "Calling " + args["number"]
        if name == "send_sms":
            subprocess.run(["termux-sms-send", "-n", args["number"], args["message"]], timeout=15)
            return "SMS sent to " + args["number"]
        if name == "notify":
            subprocess.run(["termux-notification", "--title", args["title"], "--content", args["message"]], timeout=10)
            return "Notification shown"
        return "Unknown tool: " + name
    except Exception as e:
        return "Error: " + str(e)


def call_api(messages):
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS,
                        "tool_choice": "auto", "max_tokens": 3000}).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"content-type": "application/json", "authorization": "Bearer " + API_KEY,
                 "user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


app = Flask(__name__)
sessions = {}
MAX_AGENT_STEPS = 15  # how many tool-calls in a row the agent can do for one task


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.json
    user_msg = data.get("message", "")
    sid = data.get("session", "default")
    if sid not in sessions:
        sessions[sid] = [{"role": "system", "content": SYSTEM}]
    messages = sessions[sid]
    messages.append({"role": "user", "content": user_msg})

    steps_log = []
    try:
        for step in range(MAX_AGENT_STEPS):
            resp = call_api(messages)
            msg = resp["choices"][0]["message"]
            messages.append(msg)
            tool_calls = msg.get("tool_calls")

            if not tool_calls:
                final_text = msg.get("content") or "(done)"
                return jsonify({"reply": final_text, "steps": steps_log})

            for tc in tool_calls:
                fn = tc["function"]
                args = json.loads(fn["arguments"] or "{}")
                result = run_tool(fn["name"], args)
                steps_log.append(fn["name"] + "(" + json.dumps(args)[:80] + ")")
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})

        return jsonify({"reply": "Task bahut lamba ho gaya, jitna ho saka kiya. Thoda aur specific bolo to main aage continue karunga.", "steps": steps_log})
    except Exception as e:
        return jsonify({"reply": "Error: " + str(e), "steps": steps_log})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
