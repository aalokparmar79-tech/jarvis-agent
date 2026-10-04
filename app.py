import os, re, json, time, sqlite3, urllib.request, urllib.error
from flask import Flask, request, jsonify, render_template

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.environ.get("GROQ_API_KEY")
DB_PATH = os.environ.get("JARVIS_DB", "jarvis_memory.db")

SYSTEM = """You are JARVIS, a personal AI assistant. Address the user as "Boss".
Personality: intelligent, calm, professional, helpful, slightly witty, concise. Never pretend to be
conscious or sentient. Reply in the same language the user writes in (Hinglish is fine).

Always clearly distinguish: known facts, your inference/guess, actions you actually performed
(confirm what tool ran and the real result), and actions that failed (say clearly, don't pretend).

You have long-term memory (remember_fact/recall_memory/forget_fact) and a task/reminder system
(add_task/list_tasks/complete_task/delete_task, add_reminder/list_reminders). Use them proactively
when the user mentions things to remember, do, or be reminded about.

Before doing a destructive action (delete_task, forget_fact, overwrite an existing file), briefly
state what you're about to do in your reply. Don't silently destroy data.

Knowledge base (Bot Development Guide):
- Bot = automated program doing tasks without a human. Chatbots, automation bots (Selenium,
  Playwright), trading bots, AI bots, voice bots, game bots. Learn Python first.
- Steps for any bot: 1) define goal, 2) pick platform, 3) find right tech, 4) automate GUI if no API.
- Playwright recommended for web automation.

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
    {"type": "function", "function": {"name": "remember_fact", "description": "Save an important fact to long-term memory.",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "recall_memory", "description": "Search long-term memory for relevant facts.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "forget_fact", "description": "Delete a fact from memory by key.",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "add_task", "description": "Add a task/todo item.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "project": {"type": "string"}}, "required": ["title"]}}},
    {"type": "function", "function": {"name": "list_tasks", "description": "List all pending tasks, optionally filtered by project.",
     "parameters": {"type": "object", "properties": {"project": {"type": "string"}}}}},
    {"type": "function", "function": {"name": "complete_task", "description": "Mark a task as done by its id.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "delete_task", "description": "Delete a task by its id.",
     "parameters": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {"name": "add_reminder", "description": "Add a reminder with a due time description.",
     "parameters": {"type": "object", "properties": {"text": {"type": "string"}, "due": {"type": "string"}}, "required": ["text", "due"]}}},
    {"type": "function", "function": {"name": "list_reminders", "description": "List all reminders.",
     "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "set_timer", "description": "Start a live countdown timer in the browser that alerts the user with sound after N seconds. Use this for short timers like 'remind me in 5 seconds/minutes', not for long-term reminders.",
     "parameters": {"type": "object", "properties": {"seconds": {"type": "integer"}, "label": {"type": "string"}}, "required": ["seconds", "label"]}}},
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_log
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, tool TEXT, args TEXT, result TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, project TEXT, done INTEGER DEFAULT 0, created_at TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS reminders
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, due TEXT, created_at TEXT)""")
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
            conn.commit(); conn.close()
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
            conn.commit(); conn.close()
            result = "Forgot: " + args["key"]
        elif name == "add_task":
            conn = db()
            conn.execute("INSERT INTO tasks (title, project, done, created_at) VALUES (?,?,0,?)",
                        (args["title"], args.get("project", "general"), time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); conn.close()
            result = "Task added: " + args["title"]
        elif name == "list_tasks":
            conn = db()
            if args.get("project"):
                rows = conn.execute("SELECT id, title, project, done FROM tasks WHERE project=? AND done=0", (args["project"],)).fetchall()
            else:
                rows = conn.execute("SELECT id, title, project, done FROM tasks WHERE done=0").fetchall()
            conn.close()
            result = json.dumps([{"id": r[0], "title": r[1], "project": r[2]} for r in rows]) if rows else "No pending tasks."
        elif name == "complete_task":
            conn = db()
            conn.execute("UPDATE tasks SET done=1 WHERE id=?", (args["task_id"],))
            conn.commit(); conn.close()
            result = "Task marked done: #" + str(args["task_id"])
        elif name == "delete_task":
            conn = db()
            conn.execute("DELETE FROM tasks WHERE id=?", (args["task_id"],))
            conn.commit(); conn.close()
            result = "Task deleted: #" + str(args["task_id"])
        elif name == "add_reminder":
            conn = db()
            conn.execute("INSERT INTO reminders (text, due, created_at) VALUES (?,?,?)",
                        (args["text"], args["due"], time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit(); conn.close()
            result = "Reminder set: " + args["text"] + " @ " + args["due"]
        elif name == "list_reminders":
            conn = db()
            rows = conn.execute("SELECT id, text, due FROM reminders").fetchall()
            conn.close()
            result = json.dumps([{"id": r[0], "text": r[1], "due": r[2]} for r in rows]) if rows else "No reminders set."
        elif name == "set_timer":
            result = "Timer started: " + args["label"] + " for " + str(args["seconds"]) + " seconds"
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
        return jsonify({"reply": "Thoda slow, Boss."}), 429
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
    timer_info = None
    try:
        for _ in range(8):
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
                if fn["name"] == "set_timer":
                    timer_info = {"seconds": args["seconds"], "label": args["label"]}
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
    except Exception as e:
        return jsonify({"reply": "Error: " + str(e)})
    out = {"reply": "\n\n".join(replies) or "(no reply)"}
    if timer_info:
        out["timer"] = timer_info
    return jsonify(out)


@app.route("/dashboard")
def dashboard():
    conn = db()
    tasks = conn.execute("SELECT id, title, project FROM tasks WHERE done=0").fetchall()
    reminders = conn.execute("SELECT id, text, due FROM reminders").fetchall()
    memory = conn.execute("SELECT key, value FROM memory ORDER BY updated_at DESC LIMIT 20").fetchall()
    logs = conn.execute("SELECT ts, tool, result FROM audit_log ORDER BY id DESC LIMIT 20").fetchall()
    conn.close()
    return render_template("dashboard.html", tasks=tasks, reminders=reminders, memory=memory, logs=logs)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
