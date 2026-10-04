import os, re, json, subprocess, sqlite3, time, urllib.request, urllib.error

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.environ.get("GROQ_API_KEY")
DB_PATH = os.path.expanduser("~/Jarvis/jarvis_memory.db")

SYSTEM = """You are JARVIS, a personal voice AI assistant. Address the user as "Boss".
Personality: intelligent, calm, professional, slightly witty, concise - this is SPOKEN aloud,
so keep replies SHORT (1-3 sentences max unless asked for detail). Never pretend to be sentient.
Reply in the same language the user speaks in (Hinglish is fine).

Clearly distinguish facts you know, your inference, actions you actually performed, and failures.
Use remember_fact to save important facts the user mentions. Use recall_memory to check what you
already know. Don't save passwords/secrets to memory."""

TOOLS = [
    {"type": "function", "function": {"name": "browse", "description": "Fetch a public web page and return readable text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "remember_fact", "description": "Save an important fact to long-term memory.",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"]}}},
    {"type": "function", "function": {"name": "recall_memory", "description": "Search long-term memory for relevant facts.",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "forget_fact", "description": "Delete a fact from memory by key.",
     "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "notify", "description": "Show an Android notification.",
     "parameters": {"type": "object", "properties": {"title": {"type": "string"}, "message": {"type": "string"}}, "required": ["title", "message"]}}},
]


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS memory (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    return conn


def speak(text):
    clean = re.sub(r"[*_`#]", "", text)[:500]
    try:
        subprocess.run(["termux-tts-speak", clean], timeout=30)
    except Exception as e:
        print("TTS error:", e)


def listen():
    try:
        r = subprocess.run(["termux-speech-to-text"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip()
    except Exception as e:
        print("STT error:", e)
        return ""


def run_tool(name, args):
    try:
        if name == "browse":
            if not re.match(r"^https?://", args["url"]):
                return "Error: only http/https URLs allowed"
            req = urllib.request.Request(args["url"], headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
            html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
            return text[:8000]
        if name == "remember_fact":
            conn = db()
            conn.execute("INSERT OR REPLACE INTO memory (key, value, updated_at) VALUES (?,?,?)",
                         (args["key"], args["value"], time.strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            conn.close()
            return "Remembered: " + args["key"]
        if name == "recall_memory":
            conn = db()
            rows = conn.execute("SELECT key, value FROM memory WHERE key LIKE ? OR value LIKE ?",
                                 (f"%{args['query']}%", f"%{args['query']}%")).fetchall()
            conn.close()
            return json.dumps([{"key": k, "value": v} for k, v in rows]) if rows else "No matching memory found."
        if name == "forget_fact":
            conn = db()
            conn.execute("DELETE FROM memory WHERE key = ?", (args["key"],))
            conn.commit()
            conn.close()
            return "Forgot: " + args["key"]
        if name == "notify":
            subprocess.run(["termux-notification", "--title", args["title"], "--content", args["message"]], timeout=10)
            return "Notification shown"
        return "Unknown tool: " + name
    except Exception as e:
        return "Error: " + str(e)


def call_api(messages):
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS,
                        "tool_choice": "auto", "max_tokens": 500}).encode()
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions", data=body,
        headers={"content-type": "application/json", "authorization": "Bearer " + API_KEY,
                 "user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def main():
    if not API_KEY:
        print("GROQ_API_KEY set nahi hai.")
        return
    messages = [{"role": "system", "content": SYSTEM}]
    print("=== JARVIS Voice Mode ===")
    print("Har baar Enter dabao to bolna shuru karo. 'exit' type karke band karo.\n")
    speak("Jarvis online. Good to go, Boss.")

    while True:
        cmd = input("\n[Enter = speak now | type 'exit' to quit]: ").strip().lower()
        if cmd == "exit":
            speak("Goodbye Boss.")
            break

        print("Listening...")
        text = listen()
        if not text:
            print("(kuch sunai nahi diya, dobara try karo)")
            continue
        print("You said:", text)

        if text.lower() in ("exit", "stop", "band karo", "quit"):
            speak("Goodbye Boss.")
            break

        messages.append({"role": "user", "content": text})
        try:
            for _ in range(4):
                resp = call_api(messages)
                msg = resp["choices"][0]["message"]
                messages.append(msg)
                if msg.get("content"):
                    print("Jarvis:", msg["content"])
                    speak(msg["content"])
                tool_calls = msg.get("tool_calls")
                if not tool_calls:
                    break
                for tc in tool_calls:
                    fn = tc["function"]
                    args = json.loads(fn["arguments"] or "{}")
                    result = run_tool(fn["name"], args)
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
        except Exception as e:
            print("Error:", e)
            speak("Sorry Boss, kuch gadbad ho gayi.")


main()
