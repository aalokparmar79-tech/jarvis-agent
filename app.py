import os, re, json, subprocess, urllib.request, urllib.error
from flask import Flask, request, jsonify, render_template, session, redirect, url_for

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.environ.get("GROQ_API_KEY")
JARVIS_PASSWORD = os.environ.get("JARVIS_PASSWORD", "changeme123")

SYSTEM = """You are Jarvis, a personal AI assistant and bot-development mentor.
Reply in the same language the user writes in (Hinglish is fine). Keep answers short and practical.

Knowledge base (Bot Development Guide):
- Bot = automated program doing tasks without a human. Types: chatbots, automation bots
  (Selenium, Puppeteer, Playwright), trading bots, AI bots, voice bots, game bots.
- Learn Python first. Playwright is the recommended tool for web automation.
- Steps: 1) define goal, 2) pick platform, 3) find right tech, 4) automate GUI if no API exists.

Rules:
- Help build legitimate automation only. No bypassing anti-bot protections against terms of service.
- Use tools when the user wants something done. Explain what you are doing in one line."""

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "browse", "description": "Fetch a public web page and return readable text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
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
        if name == "browse":
            req = urllib.request.Request(args["url"], headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
            return text[:15000]
        return "Unknown tool: " + name
    except Exception as e:
        return "Error: " + str(e)


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
app.secret_key = os.environ.get("FLASK_SECRET", "change-this-secret-key-too")
sessions = {}


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        if request.form.get("password") == JARVIS_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("home"))
        return render_template("login.html", error="Wrong password")
    if not session.get("logged_in"):
        return render_template("login.html", error=None)
    return render_template("index.html")


@app.route("/logout")
def logout():
    session.pop("logged_in", None)
    return redirect(url_for("home"))


@app.route("/chat", methods=["POST"])
def chat():
    if not session.get("logged_in"):
        return jsonify({"reply": "Please login first."}), 401
    data = request.json
    user_msg = data.get("message", "")
    sid = data.get("session", "default")
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


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
