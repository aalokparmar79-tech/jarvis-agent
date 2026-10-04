import os, re, json, subprocess, urllib.request, urllib.error

MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
API_KEY = os.environ.get("GROQ_API_KEY")

SYSTEM = """You are Jarvis, a personal AI assistant and bot-development mentor.
Reply in the same language the user writes in (Hinglish is fine). Keep answers short and practical.

Knowledge base (Bot Development Guide by Podus):
- Bot = automated program doing tasks without a human. Types: chatbots (WhatsApp, Telegram,
  Instagram, web), automation bots (Selenium, Puppeteer, Playwright), trading bots (Python, APIs,
  signals, backtesting), AI bots (NLP, LLMs, vector DBs), voice bots (STT + TTS), game bots.
- Learn Python first. JavaScript is the second choice for web bots.
- Steps to make any bot: 1) define what you want, 2) pick the platform (browser, Telegram, OS),
  3) find the right tech (browser -> Playwright/Selenium, Telegram -> pyTelegramBotAPI/telebot,
  WhatsApp -> WhatsApp Business API), 4) if no API exists, automate the GUI.
- Web automation: login, forms, clicks, scraping, downloads, posting, repeated workflows.
- Playwright is the recommended tool (stable, auto-waits, cross-browser, codegen recorder).
  Selenium is older and great for testing.
- Start with chatbots and web automation; trading/game bots need system design and maths.

Rules:
- Help build legitimate automation: own accounts, public data, sites that allow it, official APIs.
- Do not help bypass anti-bot protections for ticket/sneaker scalping or to scrape platforms against
  their terms. Explain concepts generally and suggest official APIs instead.
- Use tools when the user wants something done. Explain what you are doing in one line."""

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "Read a text file.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "Create or overwrite a file. Asks user confirmation.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_shell", "description": "Run a shell command. Asks user confirmation.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {"name": "browse", "description": "Fetch a web page and return its text.",
     "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
]


def confirm(msg):
    return input("\n[Jarvis wants to] " + msg + "\nAllow? (y/n): ").strip().lower() == "y"


def run_tool(name, args):
    try:
        if name == "read_file":
            with open(args["path"], encoding="utf-8") as f:
                return f.read()[:20000]
        if name == "write_file":
            if not confirm("write file " + args["path"]):
                return "User denied."
            with open(args["path"], "w", encoding="utf-8") as f:
                f.write(args["content"])
            return "File written."
        if name == "run_shell":
            if not confirm("run: " + args["command"]):
                return "User denied."
            r = subprocess.run(args["command"], shell=True, capture_output=True, text=True, timeout=120)
            return (r.stdout + r.stderr)[-8000:] or "(no output)"
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
        headers={"content-type": "application/json", "authorization": "Bearer " + API_KEY,"user-agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(e.read().decode())


def main():
    if not API_KEY:
        print('Key set nahi hai. Pehle: export GROQ_API_KEY="your-key"')
        return
    messages = [{"role": "system", "content": SYSTEM}]
    print("Jarvis online (Groq). 'exit' likho band karne ke liye.\n")
    while True:
        user = input("You: ").strip()
        if user.lower() in ("exit", "quit"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        while True:
            try:
                resp = call_api(messages)
            except Exception as e:
                print("API error:", e)
                messages.pop()
                break
            msg = resp["choices"][0]["message"]
            messages.append(msg)
            if msg.get("content"):
                print("\nJarvis:", msg["content"], "\n")
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                break
            for tc in tool_calls:
                fn = tc["function"]
                args = json.loads(fn["arguments"] or "{}")
                result = run_tool(fn["name"], args)
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})


main()
