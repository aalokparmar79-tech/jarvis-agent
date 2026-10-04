import os, re, json, time, sqlite3, subprocess, urllib.request, urllib.error
from flask import Flask, request, jsonify, render_template

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.environ.get("GROQ_API_KEY")
DB_PATH = os.environ.get("JARVIS_DB", "jarvis_memory.db")

SYSTEM = """You are JARVIS, a personal AI assistant. Address the user as "Boss".
Personality: intelligent, calm, professional, helpful, slightly witty, concise. Never pretend to be
conscious or sentient. Reply in the same language the user writes in (Hinglish is fine).

Always clearly distinguish, in your own wording when relevant:
- Known facts (from memory or tool results)
- Your inference/guess (say "I think" / "mujhe lagta hai")
- Actions you actually performed (confirm what tool ran and its real result)
- Actions that failed (say clearly it failed, don't pretend success)

You have long-term memory. Use the remember_fact tool to save important facts the user tells you
(name, preferences, ongoing projects, deadlines). Use recall_memory to check what you already know
before answering personal questions. Don't save sensitive secrets (passwords, keys) to memory.

Knowledge base (Bot Development Guide):
- Bot = automated program doing tasks without a human. Types: chatbots, automation bots, trading
  bots, AI bots, voice bots, game bots. Learn Python first. Playwright recommended for web automation.

Rules:
- Only help with legitimate automation (own accounts, public data, official APIs).
- Refuse to bypass anti-bot protections or break terms of service.
- Use tools when the user wants something done; explain briefly what you're doing."""

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "browse", "description": "Fetch a public web page and return readable text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "remember_fact", "description": "Save an important fact about the user/project to long-term memory.",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "recall_memory", "description": "Search long-term memory for relevant facts.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "forget_fact", "description": "Delete a fact from long-term memory by key.",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS memory
                     (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, tool TEXT, args TEXT, result TEXT)""")
    return conn


def log_action(tool, args, result):
    try:
        conn = db()
        conn.execute("INSERT INTO audit_log (ts, tool, args, result) VALUES (?,?,?,?)",
                      (time.strftime("%Y-%m-%d %H:%M:%S"), tool, json.dumps(args)[:500], str(result)[:500]))
        conn.commit()
        conn.close()
    except Exception:
        pass


def run_tool(name, args):
    try:
        if name == "read_file":
            with open(args["path"], encoding="utf-8") as f:
                result = f.read()[:20000]
        elif name == "write_file":
            os.makedirs(os.path.dirname(args["path"]) or ".", exist_ok=True)
            with open(args["path"], "w", encoding="utf-8") as f:
                f.write(args["content"])
            result = "File written: " + args["path"]
        elif name == "browse":
            if not re.match(r"^https?://", args["url"]):
                result = "Error: only http/https URLs allowed"
            else:
                req = urllib.request.Request(args["url"], headers={"User-Agent": "Mozilla/5.0"})
                html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
                html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
                text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
                result = text[:15000]
        elif name == "remember_fact":
            conn = db()
            conn.execute("INSERT OR REPLACE INTO memory (key, value, updated_at) VALUES (?,?,?)",
                         (args["key"], args["value"], time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()
            result = "Remembered: " + args["key"]
        elif name == "recall_memory":
            conn = db()
            rows = conn.execute("SELECT key, value FROM memory WHERE key LIKE ? OR value LIKE ?",
                                 (f"%{args['query']}%", f"%{args['query']}%")).fetchall()
            conn.close()
            result = json.dumps([{"key": k, "value": v} for k, v in rows]) if rows else "No matching memory found."
        elif name == "forget_fact":
            conn = db()
            conn.execute("DELETE FROM memory WHERE key = ?", (args["key"],))
            conn.commit()
            conn.close()
            result = "Forgot: " + args["key"]
        else:
            result = "Unknown tool: " + name
    except Exception as e:
        result = "Error: " + str(e)
    log_action(name, args, result)
    return result


def call_api(messages):
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS,
                        "tool_choice": "auto", "max_tokens": 2000}).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"content-type": "application/json", "authorization": "Bearer " + API_KEY,
                 "user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


app = Flask(__name__)
sessions = {}
last_request_time = {}
RATE_LIMIT_SECONDS = 3


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    ip = request.remote_addr
    now = time.time()
    if ip in last_request_time and now - last_request_time[ip] < RATE_LIMIT_SECONDS:
        return jsonify({"reply": "Thoda slow, Boss. Ek message bhej ke thoda ruko."}), 429
    last_request_time[ip] = now

    data = request.json or {}
    user_msg = str(data.get("message", ""))[:4000]
    sid = str(data.get("session", "default"))[:100]
    if not user_msg.strip():
        return jsonify({"reply": "Kuch likho Boss."})

    if sid not in sessions:
        sessions[sid] = [{"role": "system", "content": SYSTEM}]
    messages = sessions[sid]
    messages.append({"role": "user", "content": user_msg})
    replies = []
    try:
        for _ in range(6):
            resp = call_api(messages)
            msg = resp["choices"][0]["message"]
            messages.append(msg)
            if msg.get("content"):
                replies.append(msg["content"])
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break
            for tc in tool_calls:
                fn = tc["function"]
                args = json.loads(fn["arguments"] or "{}")
                result = run_tool(fn["name"], args)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
    except Exception as e:
        return jsonify({"reply": "Error: " + str(e)})
    return jsonify({"reply": "\n\n".join(replies) or "(no reply)"})


@app.route("/memory")
def view_memory():
    conn = db()
    rows = conn.execute("SELECT key, value, updated_at FROM memory ORDER BY updated_at DESC").fetchall()
    conn.close()
    return jsonify([{"key": k, "value": v, "updated": u} for k, v, u in rows])


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
