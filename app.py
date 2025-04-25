import os
import re
import json
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
from llmproxy import generate
from duckduckgo_search import DDGS

app = Flask(__name__)

# --- SESSION MANAGEMENT ---
SESSION_FILE = "session_store.json"

def load_sessions():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}


def save_sessions(sessions):
    with open(SESSION_FILE, "w") as f:
        json.dump(sessions, f, indent=4)

session_dict = load_sessions()

# --- DUMMY TEST USER ---

def _init_test_user():
    session_dict.setdefault("test_user", {
        "session_id": "test_user-session",
        "onboarding_stage": "done",
        "condition": "Crohn's disease",
        "news_pref": None,
        "news_sources": ["bbc.com", "nytimes.com"]
    })
    save_sessions(session_dict)

_init_test_user()

# --- TOOL FUNCTIONS ---
def websearch(query):
    with DDGS() as ddgs:
        results = ddgs.text(query, max_results=5)
    return [r.get("href") for r in results]


def youtube_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:youtube.com", max_results=5)
    return [r.get("href") for r in results if "youtube.com/watch" in r.get("href", "")]


def tiktok_search(query):
    with DDGS() as ddgs:
        results = ddgs.text(f"{query} site:tiktok.com", max_results=5)
    return [r.get("href") for r in results if "tiktok.com" in r.get("href", "")]


def instagram_search(query):
    tag = query.replace(" ", "")
    with DDGS() as ddgs:
        results = ddgs.text(f"#{tag} site:instagram.com", max_results=5)
    return [r.get("href") for r in results if "instagram.com" in r.get("href", "")]  

# --- WEEKLY UPDATE GENERATION ---
TOOL_MAP = {
    "YouTube": "youtube_search",
    "TikTok": "tiktok_search",
    "Instagram Reel": "instagram_search",
    "Research News": "websearch"
}

def agent_weekly_update(user_info, health_info):
    pref = user_info.get("news_pref")
    tool = TOOL_MAP.get(pref, "websearch")
    condition = health_info.get("condition", "health")

    system_prompt = f"""
You are an AI agent generating weekly updates for a user with {condition}.
Use only the {tool} tool to generate exactly five unique calls.
Each call should search for \"{condition}\" plus a relevant phrase.
Respond with one tool call per line, e.g.:
{tool}(\"{condition} management tips\")
"""
    resp = generate(
        model="4o-mini",
        system=system_prompt,
        query="Generate five unique tool calls, one per line.",
        temperature=0.8,
        lastk=30,
        session_id="HEALTH_UPDATE_AGENT",
        rag_usage=False
    )
    return resp.get("response", "")


def weekly_update_internal(user):
    sess = session_dict.get(user)
    if not sess:
        return {"text": "User not found."}

    pref = sess.get("news_pref")
    tool = TOOL_MAP.get(pref, "websearch")
    user_info = {"news_pref": pref}
    health_info = {"condition": sess.get("condition")}

    # Step 1: generate initial calls
    raw = agent_weekly_update(user_info, health_info)
    pattern = re.compile(rf'({tool}\("[^"]+"\))')
    calls = [m.group(1) for m in pattern.finditer(raw)]

    # Step 2: ensure exactly 5 unique calls
    seen = set(calls)
    extra_prompt = f"""
You are an AI agent generating weekly updates for a user with {sess.get('condition')}.
Use only the {tool} tool to generate exactly one additional unique call.
Do not repeat these: {', '.join(seen)}
Respond with one tool call, e.g.:
{tool}(\"{sess.get('condition')} meal ideas\")
"""
    while len(calls) < 5:
        extra = generate(
            model="4o-mini",
            system=extra_prompt,
            query="Generate one additional unique tool call.",
            temperature=0.8,
            lastk=30,
            session_id="HEALTH_UPDATE_AGENT",
            rag_usage=False
        ).get("response", "")
        match = pattern.search(extra)
        if match:
            call = match.group(1)
            if call not in seen:
                seen.add(call)
                calls.append(call)
                continue
        break

    # Step 3: execute calls without eval()
    results = []
    for call in calls[:5]:
        m = re.match(r'([a-z_]+)\("([^"]+)"\)', call)
        if m:
            func_name, query_str = m.groups()
            func = globals().get(func_name)
            if func:
                try:
                    links = func(query_str)
                    top = links[0] if links else "No results found"
                except Exception:
                    top = "Error fetching results"
            else:
                top = "Unknown tool"
        else:
            top = "Invalid call"
        results.append({"query": call, "link": top})

    # Step 4: format
    lines = [f"• {r['query']}: {r['link']}" for r in results]
    text = "Here is your weekly health content digest with 5 unique searches:\n" + "\n".join(lines)
    return {"text": text, "results": results}

# --- ONBOARDING FUNCTIONS ---

def first_interaction(message, user):
    # ... existing onboarding logic ...
    return {"text": "..."}

# --- MAIN ROUTE ---
@app.route('/', methods=['POST'])
def main():
    global session_dict
    data = request.get_json()
    message = data.get("text", "").strip()
    user = data.get("user_name", "Unknown")

    session_dict = load_sessions()

    # Initialize new users
    if user not in session_dict:
        session_dict[user] = {
            "session_id": f"{user}-session",
            "onboarding_stage": "condition",
            "condition": "",
            "age": 0,
            "weight": 0,
            "medications": [],
            "emergency_contact": "",
            "news_pref": "",
            "news_sources": ["bbc.com", "nytimes.com"]
        }
        save_sessions(session_dict)

    # 1) User asks for weekly update → show buttons
    if message.lower() == "weekly update":
        buttons = [
            {"type":"button","text":"🎥 YouTube","msg":"YouTube","msg_in_chat_window":True},
            {"type":"button","text":"📸 Instagram Reel","msg":"Instagram Reel","msg_in_chat_window":True},
            {"type":"button","text":"🎵 TikTok","msg":"TikTok","msg_in_chat_window":True},
            {"type":"button","text":"🧪 Research News","msg":"Research News","msg_in_chat_window":True}
        ]
        return jsonify({
            "text": "Choose your weekly-update content type:",
            "attachments": [{"collapsed": False, "color": "#e3e3e3", "actions": buttons}]
        })

    # 2) User picks channel → generate update
    if message in TOOL_MAP:
        session_dict[user]["news_pref"] = message
        session_dict[user]["onboarding_stage"] = "done"
        save_sessions(session_dict)
        return jsonify(weekly_update_internal(user))

    # 3) Onboarding or default
    if session_dict[user].get("onboarding_stage") != "done":
        response = first_interaction(message, user)
    else:
        response = {"text": "You're onboarded! Type 'weekly update' to choose content and get your digest."}

    save_sessions(session_dict)
    return jsonify(response)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)








